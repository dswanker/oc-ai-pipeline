"""
Study fingerprint extractor.

Given a ``ParsedForm`` (the output of any FormParser), use Claude to
extract a structured ``StudyFingerprint`` — sponsor, intervention(s),
indication, phase, study type, therapeutic area.

The fingerprint is consumed by:

* CT.gov client / matcher — to build the search query and to score the
  resulting candidates against the form.
* Vector store — as filterable metadata on indexed pairs (so retrieval
  can narrow by therapeutic area, sponsor, etc.).

Why Claude and not regex / heuristics? Form designs vary enormously
across sponsors, CROs, and therapeutic areas. Claude handles missing
fields, weird abbreviations, and implicit metadata (oncology AE form
full of "PSA" and "EBRT" → indication = prostate cancer) without us
writing fragile rules per disease.

Module structure:

* ``StudyFingerprint`` — the dataclass returned by extraction.
* ``serialize_parsed_form`` — pure function that flattens a ParsedForm
  to a compact JSON-friendly dict for the prompt.
* ``build_extraction_prompt`` — pure function that assembles the prompt
  string, including any human-supplied overrides.
* ``parse_fingerprint_response`` — pure function that turns Claude's
  JSON response into a StudyFingerprint, defensively handling
  malformed input.
* ``FingerprintExtractor`` — async class that calls the Anthropic API
  and orchestrates the above. Imports of ``anthropic`` / ``app.config``
  are deferred to first use so the module is importable in environments
  where those deps aren't installed (notably: unit tests).

Human-in-the-loop overrides:

If the curator filled in fields like Sponsor/Client on the monday
corpus row, the worker passes them as ``overrides=`` to ``extract()``.
We do two things with them: (1) tell Claude in the prompt that they're
ground truth and to copy them through; (2) also force them post-hoc
in case Claude ignored the prompt instruction. The post-hoc step is a
defense-in-depth measure — Claude is mostly cooperative here, but
not always.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.form_parser.base import ParsedForm

if TYPE_CHECKING:  # pragma: no cover
    from anthropic import AsyncAnthropic

# Use structlog if available, fall back to a stdlib-logging shim that
# accepts the same kwargs-as-context API. Lets the module import in
# environments where structlog isn't installed (CI without dev extras;
# the test sandbox during development).
try:
    import structlog

    logger = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    import logging

    _stdlogger = logging.getLogger(__name__)

    class _StdlibShimLogger:
        """Tiny adapter so call sites can use structlog's kwargs style."""

        @staticmethod
        def _fmt(event: str, kw: dict[str, Any]) -> str:
            if not kw:
                return event
            tail = " ".join(f"{k}={v!r}" for k, v in kw.items())
            return f"{event} {tail}"

        def info(self, event: str, **kw: Any) -> None:
            _stdlogger.info(self._fmt(event, kw))

        def warning(self, event: str, **kw: Any) -> None:
            _stdlogger.warning(self._fmt(event, kw))

        def error(self, event: str, **kw: Any) -> None:
            _stdlogger.error(self._fmt(event, kw))

        def debug(self, event: str, **kw: Any) -> None:
            _stdlogger.debug(self._fmt(event, kw))

    logger = _StdlibShimLogger()


# Override keys that are allowed to flow through from monday into the
# fingerprint. Anything else is silently dropped so we can't be tricked
# by stray columns.
_ALLOWED_OVERRIDE_KEYS = frozenset({
    "sponsor",
    "intervention",
    "indication",
    "phase",
    "study_type",
    "therapeutic_area",
})


@dataclass
class StudyFingerprint:
    """
    Structured study identity inferred from a form design.

    Any field may be ``None`` if it couldn't be determined.
    ``extraction_confidence`` is Claude's self-reported confidence in
    the extraction (0.0–1.0). ``notes`` carries free-text caveats.
    """

    sponsor: str | None = None
    intervention: list[str] | None = None  # drug/device name(s)
    indication: str | None = None  # disease / condition
    phase: str | None = None  # "1" | "2" | "3" | "4" | "1/2" | "2/3"
    study_type: str | None = None  # "interventional" | "observational"
    therapeutic_area: str | None = None  # e.g. "oncology"
    extraction_confidence: float = 0.0
    notes: str | None = None


# ─── Pure helper functions (testable without the SDK) ─────────────


def serialize_parsed_form(
    parsed: ParsedForm,
    *,
    max_items_per_form: int = 5,
    max_forms: int = 30,
) -> dict[str, Any]:
    """
    Flatten a ParsedForm to a compact dict suitable for embedding in
    the extraction prompt.

    Trade-offs:
      * Trim sample item labels per form to ``max_items_per_form``;
        the full list of items rarely adds signal beyond the first
        few labels.
      * Cap total forms at ``max_forms`` to keep token usage bounded
        on very large studies. This is a soft limit — most clinical
        trials have under 30 forms.
      * Pass through OpenClinica vendor-extension details verbatim
        when present. They're high-signal (Phase, ProtocolType,
        OfficialTitle) and only ~100 tokens.
    """
    forms_summary: list[dict[str, Any]] = []
    for f in parsed.forms[:max_forms]:
        items = [it for g in f.groups for it in g.items]
        sample_labels = [
            it.label for it in items[:max_items_per_form] if it.label
        ]
        domains = sorted({it.domain for it in items if it.domain})
        forms_summary.append({
            "oid": f.oid,
            "name": f.name,
            "domain": (
                domains[0] if len(domains) == 1
                else (domains or None)
            ),
            "item_count": len(items),
            "sample_items": sample_labels,
        })

    out: dict[str, Any] = {
        "study_name": parsed.study_name,
        "study_oid": parsed.study_oid,
        "extracted_sponsor": parsed.sponsor,  # may be None
        "form_count": len(parsed.forms),
        "forms": forms_summary,
    }

    # Surface OpenClinica vendor extension data if it survived parsing.
    raw = parsed.raw_metadata or {}
    oc_details = raw.get("openclinica_details") if isinstance(raw, dict) else None
    if oc_details:
        out["openclinica_details"] = oc_details

    if len(parsed.forms) > max_forms:
        out["_truncation_note"] = (
            f"Truncated to first {max_forms} of {len(parsed.forms)} forms"
        )

    return out


def build_extraction_prompt(
    parsed: ParsedForm,
    overrides: dict[str, Any] | None = None,
) -> str:
    """
    Build the prompt sent to Claude.

    The prompt has three pieces:
      1. Role + task statement.
      2. (optional) Human-supplied overrides as ground truth.
      3. The parsed form payload + the required JSON output schema.
    """
    cleaned_overrides = _filter_overrides(overrides)
    serialized = serialize_parsed_form(parsed)

    parts: list[str] = []

    parts.append(
        "You are reading the structure of a clinical trial form design "
        "(a CRF set). Your job is to infer the study's identity from "
        "the form structure: sponsor, drug/device intervention(s), "
        "disease indication, phase, study type, and therapeutic area."
    )
    parts.append("")

    if cleaned_overrides:
        parts.append(
            "The fields below have been GROUND-TRUTH-supplied by a "
            "human curator. Copy each of them through to your output "
            "exactly as given — do NOT infer or override them, even if "
            "the form structure suggests otherwise."
        )
        parts.append("<human_supplied>")
        parts.append(json.dumps(cleaned_overrides, indent=2))
        parts.append("</human_supplied>")
        parts.append("")

    parts.append("Below is the parsed form structure:")
    parts.append("<parsed_form>")
    parts.append(json.dumps(serialized, indent=2))
    parts.append("</parsed_form>")
    parts.append("")

    parts.append(
        "Return a single JSON object with these exact keys (use null "
        "for unknown):"
    )
    parts.append("")
    parts.append("{")
    parts.append('  "sponsor": string | null,')
    parts.append('  "intervention": [string, ...] | null,')
    parts.append('  "indication": string | null,')
    parts.append('  "phase": "1" | "2" | "3" | "4" | "1/2" | "2/3" | null,')
    parts.append('  "study_type": "interventional" | "observational" | null,')
    parts.append('  "therapeutic_area": string | null,')
    parts.append('  "extraction_confidence": number between 0.0 and 1.0,')
    parts.append('  "notes": string | null')
    parts.append("}")
    parts.append("")
    parts.append(
        "Output ONLY the JSON object. No explanation, no markdown "
        "fences, no preamble."
    )

    return "\n".join(parts)


def parse_fingerprint_response(
    text: str,
    overrides: dict[str, Any] | None = None,
) -> StudyFingerprint:
    """
    Turn Claude's response text into a StudyFingerprint.

    Robust against:
      * Markdown ``json`` fences around the JSON body.
      * Minor type slop (e.g. integer where string expected).
      * Missing keys (filled with ``None``).
      * Outright malformed JSON (returns empty fingerprint with a
        diagnostic ``notes`` field rather than crashing — the worker
        can surface this on monday for the curator to investigate).

    Then applies any provided overrides as a post-hoc safeguard.
    """
    cleaned_overrides = _filter_overrides(overrides)
    cleaned_text = _strip_json_fences(text)

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        logger.warning("fingerprint.parse_failed", error=str(exc))
        fp = StudyFingerprint(notes=f"JSON parse failed: {exc}")
        if cleaned_overrides:
            _apply_overrides(fp, cleaned_overrides)
        return fp

    if not isinstance(data, dict):
        fp = StudyFingerprint(
            notes=f"Response was not a JSON object: {type(data).__name__}"
        )
        if cleaned_overrides:
            _apply_overrides(fp, cleaned_overrides)
        return fp

    fp = StudyFingerprint(
        sponsor=_str_or_none(data.get("sponsor")),
        intervention=_list_of_strings_or_none(data.get("intervention")),
        indication=_str_or_none(data.get("indication")),
        phase=_str_or_none(data.get("phase")),
        study_type=_str_or_none(data.get("study_type")),
        therapeutic_area=_str_or_none(data.get("therapeutic_area")),
        extraction_confidence=_float_or_zero(data.get("extraction_confidence")),
        notes=_str_or_none(data.get("notes")),
    )

    if cleaned_overrides:
        _apply_overrides(fp, cleaned_overrides)

    return fp


# ─── Internal helpers ─────────────────────────────────────────────


def _filter_overrides(
    overrides: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Drop unknown keys and falsy values; return None if nothing useful left."""
    if not overrides:
        return None
    out = {
        k: v
        for k, v in overrides.items()
        if k in _ALLOWED_OVERRIDE_KEYS and v not in (None, "", [], {})
    }
    return out or None


def _strip_json_fences(text: str) -> str:
    """Strip ``\u0060\u0060\u0060json ... \u0060\u0060\u0060`` or ``\u0060\u0060\u0060 ... \u0060\u0060\u0060`` wrapping if present."""
    s = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", s, re.DOTALL)
    if m:
        return m.group(1).strip()
    return s


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    # Coerce non-string scalars (e.g. phase = 2 as int) to string
    return str(v)


def _list_of_strings_or_none(v: Any) -> list[str] | None:
    if v is None:
        return None
    if isinstance(v, list):
        out = [str(x).strip() for x in v if x not in (None, "")]
        out = [s for s in out if s]
        return out or None
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else None
    return None


def _float_or_zero(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _apply_overrides(
    fp: StudyFingerprint,
    overrides: dict[str, Any],
) -> StudyFingerprint:
    """Force override values onto the fingerprint, mutating in place."""
    if "sponsor" in overrides:
        fp.sponsor = str(overrides["sponsor"])
    if "intervention" in overrides:
        v = overrides["intervention"]
        fp.intervention = v if isinstance(v, list) else [str(v)]
    if "indication" in overrides:
        fp.indication = str(overrides["indication"])
    if "phase" in overrides:
        fp.phase = str(overrides["phase"])
    if "study_type" in overrides:
        fp.study_type = str(overrides["study_type"])
    if "therapeutic_area" in overrides:
        fp.therapeutic_area = str(overrides["therapeutic_area"])
    return fp


# ─── Class wrapper around the API call ────────────────────────────


class FingerprintExtractor:
    """
    Calls Claude to extract a StudyFingerprint from a ParsedForm.

    Construction:

      * No args → reads ``ANTHROPIC_API_KEY`` and ``ANTHROPIC_MODEL``
        from ``app.config.settings``. This is the production path used
        by ``app.deps.get_fingerprint_extractor``.
      * ``api_key=`` / ``model=`` overrides → for one-off scripts.
      * ``client=`` (an AsyncAnthropic instance, real or stub) →
        for unit tests. Bypasses settings entirely.
    """

    def __init__(
        self,
        client: "AsyncAnthropic | None" = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 1500,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    def _ensure_client(self) -> "AsyncAnthropic":
        if self._client is None:
            # Deferred imports — only when actually used. Keeps the
            # module import cost low and lets tests skip these.
            from anthropic import AsyncAnthropic

            from app.config import settings

            api_key = self._api_key or settings.anthropic_api_key
            self._client = AsyncAnthropic(api_key=api_key)
        return self._client

    def _resolve_model(self) -> str:
        if self._model:
            return self._model
        from app.config import settings

        return settings.anthropic_model

    async def extract(
        self,
        parsed: ParsedForm,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> StudyFingerprint:
        """
        Extract a StudyFingerprint from a ParsedForm.

        Args:
            parsed: The parsed form, from any FormParser.
            overrides: Optional human-supplied ground-truth values
                (e.g. ``{"sponsor": "Candel Therapeutics, Inc."}``)
                that should bypass inference and be copied through.
                Unknown keys are silently dropped.
        """
        prompt = build_extraction_prompt(parsed, overrides)
        client = self._ensure_client()
        model = self._resolve_model()

        logger.info("fingerprint.extract.begin", study_oid=parsed.study_oid)

        response = await client.messages.create(
            model=model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        # The Anthropic SDK returns ``response.content`` as a list of
        # content blocks. For non-streaming text-only completions, this
        # is one block of type "text". We concatenate defensively in
        # case the SDK ever splits across blocks.
        text = ""
        for block in response.content:
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str):
                text += block_text

        fingerprint = parse_fingerprint_response(text, overrides)
        logger.info(
            "fingerprint.extract.done",
            study_oid=parsed.study_oid,
            sponsor=fingerprint.sponsor,
            intervention=fingerprint.intervention,
            confidence=fingerprint.extraction_confidence,
        )
        return fingerprint
