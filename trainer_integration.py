"""
trainer_integration.py — bridge from oc-ai-pipeline to study-build-trainer.

Phase 1 integration: before generating the EDC structure JSON, fetch
similar past protocol→form pairs from the trainer service and inject
them as a few-shot prose block into the Claude prompt's `extra_text`.

Three functions:

  * run_protocol_analysis_quick(protocol_pdf) -> dict
      Pre-step: call Claude with a short prompt to extract a small
      protocol-analysis-shaped dict (sponsor, indication, phase, etc).
      ~5-15 sec, ~$0.05. Used as the query-time signal for retrieval —
      same canonical shape as what the trainer indexes.

  * retrieve_examples(analysis, k, reserve_same_sponsor) -> list[match]
      POST /retrieve to the trainer service. Returns a list of match
      dicts (or [] on any error — failure is non-fatal).

  * format_examples_block(matches, sponsor_hint=None) -> str
      Format the matches as a few-shot prose block ready to feed
      `extra_text`. Implements the "reserve slot 1 for same-sponsor"
      behaviour: if `sponsor_hint` is supplied AND there's at least
      one same-sponsor match in the list, that match is moved to slot 1.

The trainer URL is read from the env var TRAINER_URL. Default for
local dev: http://localhost:8001. Production sets this to the
internal Railway hostname (e.g. http://trainer.railway.internal:8001).

Failure mode (graceful):
  Any error from the trainer (timeout, network failure, 5xx, malformed
  JSON) is logged and the function returns []. The pipeline continues
  with the EDC structure prompt unchanged — no examples, no error.
  The trainer is a value-add, not a critical dependency.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, TYPE_CHECKING

# httpx is already a transitive dep of anthropic — same client lib the
# pipeline already uses for monday calls.
# Import lazily so this module is importable even where httpx is not
# present (e.g. the trainer's sandbox during integration testing).
if TYPE_CHECKING:  # pragma: no cover
    import httpx  # noqa: F401


# ─── Module-level config ─────────────────────────────────────────────────


def _trainer_url() -> str:
    """Read trainer URL from env. Defaults to localhost for local dev."""
    return os.environ.get("TRAINER_URL", "http://localhost:8001").strip().rstrip("/")


# Timeouts. Tuned for "must not delay the pipeline if the trainer is sad".
# The retrieve call should normally take 100-300 ms; we give it 10s before
# we give up and proceed without examples.
_RETRIEVE_TIMEOUT_S = 10.0
_ANALYSIS_TIMEOUT_S = 60.0


# ─── Pre-step: cheap protocol analysis ───────────────────────────────────


# A short, low-output-token analysis prompt. We're not producing a full
# Study Spec — just enough structured signal to drive retrieval. The
# trainer indexes the analysis JSONs the pipeline produces during full
# runs, so the keys here should overlap with whatever shape the trainer
# was trained on. Keep it tight: sponsor / indication / phase / TA /
# intervention / study_type.
QUICK_ANALYSIS_PROMPT = """\
Read the attached clinical trial protocol PDF and extract a small
structured summary. Return ONLY a JSON object with these keys (omit
keys that you cannot determine from the protocol):

{
  "sponsor": "<full sponsor company name as written>",
  "indication": "<brief disease or condition>",
  "phase": "<1, 2, 3, 4, or label like 1/2 — string>",
  "therapeutic_area": "<oncology, cardiology, neurology, etc.>",
  "study_type": "<interventional or observational>",
  "intervention": ["<drug/device/procedure>", ...]
}

Do not include narrative text, markdown fences, or explanations.
The first character of your response must be `{` and the last `}`.
"""


async def run_protocol_analysis_quick(
    protocol_pdf: bytes,
    *,
    call_claude_fn: Any = None,
    extract_json_fn: Any = None,
) -> dict[str, Any]:
    """
    Fast, lightweight protocol analysis used as the trainer query.

    Args:
        protocol_pdf: Raw PDF bytes.
        call_claude_fn: Injectable for tests. Defaults to the pipeline's
            real `claude_client.call_claude`.
        extract_json_fn: Injectable for tests. Defaults to the pipeline's
            real `claude_client.extract_json`.

    Returns:
        Parsed dict, or {} on any failure.
    """
    # Late import — keeps trainer_integration.py importable in
    # environments where claude_client isn't on the path (notably tests).
    if call_claude_fn is None or extract_json_fn is None:
        try:
            from claude_client import call_claude as _real_call
            from claude_client import extract_json as _real_extract
        except ImportError:  # pragma: no cover
            return {}
        call_claude_fn = call_claude_fn or _real_call
        extract_json_fn = extract_json_fn or _real_extract

    if not protocol_pdf:
        return {}

    try:
        text = await asyncio.wait_for(
            call_claude_fn(
                QUICK_ANALYSIS_PROMPT,
                pdf_bytes=protocol_pdf,
                cache_prompt=False,
                max_tokens=2000,  # plenty for the small JSON shape
            ),
            timeout=_ANALYSIS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        print("[trainer] quick analysis timed out — proceeding without examples", flush=True)
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"[trainer] quick analysis failed: {type(exc).__name__}: {exc}", flush=True)
        return {}

    try:
        result = extract_json_fn(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[trainer] quick analysis JSON extract failed: {exc}", flush=True)
        return {}

    if isinstance(result, dict):
        return result
    return {}


# ─── HTTP call: POST /retrieve ───────────────────────────────────────────


async def retrieve_examples(
    analysis: dict[str, Any],
    *,
    k: int = 3,
    reserve_same_sponsor: bool = True,
    http_client: "httpx.AsyncClient | None" = None,
) -> list[dict[str, Any]]:
    """
    Fetch top-k similar pairs from the trainer service.

    Args:
        analysis: Protocol-analysis-shaped dict to use as the query.
            Must be JSON-serialisable. Empty dict → returns [].
        k: Number of examples to request. Default 3.
        reserve_same_sponsor: If True and `analysis['sponsor']` is set,
            we'll attempt to reserve slot 1 for a same-sponsor match.
            Implemented at format-time, not query-time — we still ask
            the trainer for the top-k by similarity. Reordering happens
            in `format_examples_block`.
        http_client: Injectable for tests. If None, a fresh one is
            created and closed inside this function.

    Returns:
        A list of match dicts. Empty list on any error (graceful
        failure — the pipeline continues without examples).
    """
    if not analysis:
        return []

    url = f"{_trainer_url()}/retrieve"
    payload = {"analysis": analysis, "k": int(k)}

    # Lazy httpx import so the module imports cleanly in environments
    # without httpx (notably some unit-test sandboxes). Real callers
    # always have httpx via anthropic's transitive deps.
    if http_client is None:
        try:
            import httpx as _httpx
        except ImportError:
            print("[trainer] httpx not installed — proceeding without examples",
                  flush=True)
            return []
        connect_error_cls: type[BaseException] = _httpx.ConnectError
        timeout_error_cls: type[BaseException] = _httpx.TimeoutException
        client = _httpx.AsyncClient(timeout=_RETRIEVE_TIMEOUT_S)
        owned_client = True
    else:
        # Caller provided a client — try to use httpx error classes if
        # available, fall back to base classes for stub-only test runs.
        try:
            import httpx as _httpx
            connect_error_cls = _httpx.ConnectError
            timeout_error_cls = _httpx.TimeoutException
        except ImportError:
            connect_error_cls = ConnectionError  # type: ignore[assignment]
            timeout_error_cls = TimeoutError  # type: ignore[assignment]
        client = http_client
        owned_client = False

    try:
        response = await client.post(url, json=payload)
    except connect_error_cls:
        # Trainer not reachable — log & continue without examples.
        print(f"[trainer] not reachable at {url} — proceeding without examples",
              flush=True)
        if owned_client:
            await client.aclose()
        return []
    except timeout_error_cls:
        print(f"[trainer] /retrieve timed out at {url} — proceeding without examples",
              flush=True)
        if owned_client:
            await client.aclose()
        return []
    except Exception as exc:  # noqa: BLE001
        print(f"[trainer] /retrieve unexpected error: {type(exc).__name__}: {exc}",
              flush=True)
        if owned_client:
            await client.aclose()
        return []
    finally:
        if owned_client and not client.is_closed:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    if response.status_code != 200:
        print(f"[trainer] /retrieve HTTP {response.status_code}: "
              f"{response.text[:200]}",
              flush=True)
        return []

    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        print("[trainer] /retrieve returned non-JSON body — proceeding without examples",
              flush=True)
        return []

    matches = body.get("matches") if isinstance(body, dict) else None
    if not isinstance(matches, list):
        return []

    # Sanity: discard any obviously bogus items
    out: list[dict[str, Any]] = []
    for m in matches:
        if isinstance(m, dict) and m.get("pair_hash"):
            out.append(m)
    print(f"[trainer] retrieved {len(out)} examples from {url}", flush=True)
    return out


# ─── Format matches as a prose block for `extra_text` ────────────────────


def format_examples_block(
    matches: list[dict[str, Any]],
    *,
    sponsor_hint: str | None = None,
    reserve_same_sponsor: bool = True,
) -> str:
    """
    Format match dicts into a few-shot prose block for the prompt.

    Args:
        matches: List of match dicts as returned by `retrieve_examples`.
        sponsor_hint: The current study's sponsor (if known). When
            provided AND `reserve_same_sponsor=True`, the first
            same-sponsor match in the list is moved to slot 1.
        reserve_same_sponsor: Whether to apply the same-sponsor
            reordering. Default True.

    Returns:
        A formatted string ready to drop into `extra_text`. Returns
        empty string if `matches` is empty.
    """
    if not matches:
        return ""

    ordered = list(matches)

    # Same-sponsor reservation: find the FIRST same-sponsor match and
    # move it to position 0. Other matches keep their relative order.
    if reserve_same_sponsor and sponsor_hint:
        target = _normalize_sponsor(sponsor_hint)
        for i, m in enumerate(ordered):
            if _normalize_sponsor(m.get("sponsor")) == target:
                if i != 0:
                    ordered.insert(0, ordered.pop(i))
                break  # only move ONE — the first match found

    lines: list[str] = [
        "SIMILAR PAST STUDY BUILDS (top examples from corpus, for "
        "reference only — do NOT copy item OIDs verbatim, use them "
        "only to inform structural choices about CDASH domains, form "
        "groupings, and label conventions):",
        "",
    ]

    for idx, m in enumerate(ordered, start=1):
        sponsor = m.get("sponsor") or "(sponsor unknown)"
        indication = m.get("indication") or "(indication unknown)"
        phase = m.get("phase") or "(phase unknown)"
        ta = m.get("therapeutic_area") or "(therapeutic area unknown)"
        sim = m.get("similarity")
        sim_str = f"{sim:.2f}" if isinstance(sim, (int, float)) else "?"

        same_sponsor_tag = ""
        if (sponsor_hint
                and _normalize_sponsor(sponsor) == _normalize_sponsor(sponsor_hint)):
            same_sponsor_tag = " (SAME SPONSOR)"

        form_path = m.get("form_design_path") or "(no form-design path available)"
        protocol_path = m.get("protocol_path") or ""

        lines.append(f"EXAMPLE {idx} — similarity {sim_str}{same_sponsor_tag}")
        lines.append(f"  Sponsor: {sponsor}")
        lines.append(f"  Indication: {indication}")
        lines.append(f"  Phase: {phase}")
        lines.append(f"  Therapeutic area: {ta}")
        lines.append(f"  Form-design file: {form_path}")
        if protocol_path:
            lines.append(f"  Protocol PDF: {protocol_path}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ─── Helpers ─────────────────────────────────────────────────────────────


def _normalize_sponsor(s: str | None) -> str:
    """
    Normalise sponsor names for equality checks.

    Strips whitespace, lowercases, and removes common corporate
    suffixes. "Candel Therapeutics, Inc." and "candel therapeutics"
    should compare equal.
    """
    if not s:
        return ""
    text = s.strip().lower()
    # Strip trailing punctuation
    text = text.rstrip(",. ")
    # Strip common corporate suffixes (most → least specific)
    for suffix in (
        " incorporated", " corporation", " plc", " ltd", " limited",
        " gmbh", " llc", " inc.", " inc", " corp.", " corp", " co.",
        " co", " s.a.", " s.a", " ag", " sa",
    ):
        if text.endswith(suffix):
            text = text[: -len(suffix)].rstrip(",. ")
    return text


# ─── HTTP call: POST /pending-row ────────────────────────────────────────


_PENDING_ROW_TIMEOUT_S = 30.0


async def create_pending_row(
    protocol_pdf: bytes,
    *,
    name: str,
    protocol_filename: str,
    sponsor_client: str | None = None,
    source_pipeline_item: str | None = None,
    sponsor: str | None = None,
    protocol_number: str | None = None,
    protocol_pdf_sha256: str | None = None,
    study_spec_json: bytes | None = None,
    edc_build_zip: bytes | None = None,
    http_client: "httpx.AsyncClient | None" = None,
) -> int | None:
    """
    Create a pending row on the trainer's monday corpus board.

    Sends the protocol PDF + metadata to the trainer's POST /pending-row
    endpoint. The trainer creates a row in "Awaiting Build Completion"
    status, attaches the protocol, and returns the new monday item_id.

    A human will later visit that row, upload the final form definitions
    (ODM XML or XLSForm zip), and flip the trigger to ingest the pair
    into the corpus.

    Args:
        protocol_pdf: Raw PDF bytes.
        name: Row title (typically the protocol number, e.g. ABT-CIP-10601).
        protocol_filename: Original filename for the PDF (preserved on
            upload so the trainer's parser can dispatch on extension).
        sponsor_client: Deprecated alias for ``sponsor``; kept for backward
            compatibility. If both are supplied, ``sponsor`` wins.
        source_pipeline_item: Optional oc-ai-pipeline item ID for
            traceability — links the trainer row back to the pipeline run.
        sponsor: Sponsor name. Used (with ``protocol_number``) by the
            trainer for (sponsor, protocol_number) dedup, and to seed the
            Sponsor/Client column on the trainer board. Preferred over
            ``sponsor_client``.
        protocol_number: Protocol number. Used (with ``sponsor``) by the
            trainer for dedup so re-runs of the pipeline against the same
            protocol don't create duplicate corpus rows.
        protocol_pdf_sha256: Hex SHA-256 of ``protocol_pdf``. Stored by the
            trainer for warning-only PDF-drift detection on dedup hits.
            Not used as a dedup key.
        study_spec_json: Pipeline-produced Study Spec JSON bytes. When
            present, the trainer skips its own protocol-analysis step and
            uses these pipeline outputs as the predicted side.
        edc_build_zip: Pipeline-produced EDC Build ZIP bytes. When
            present, the trainer skips its own predicted-build generation
            and uses this ZIP as the predicted EDC ZIP for accuracy
            scoring.
        http_client: Injectable for tests. If None, a fresh one is
            created and closed inside this function.

    Returns:
        The new trainer-board item_id, or None on any error. Failure is
        graceful — caller should not treat None as fatal.
    """
    if not protocol_pdf:
        print("[trainer] create_pending_row: empty protocol_pdf — skipping", flush=True)
        return None
    if not name:
        print("[trainer] create_pending_row: empty name — skipping", flush=True)
        return None

    url = f"{_trainer_url()}/pending-row"

    # Lazy httpx import — same pattern as retrieve_examples.
    if http_client is None:
        try:
            import httpx as _httpx
        except ImportError:
            print("[trainer] httpx not installed — cannot create pending row",
                  flush=True)
            return None
        connect_error_cls: type[BaseException] = _httpx.ConnectError
        timeout_error_cls: type[BaseException] = _httpx.TimeoutException
        client = _httpx.AsyncClient(timeout=_PENDING_ROW_TIMEOUT_S)
        owned_client = True
    else:
        try:
            import httpx as _httpx
            connect_error_cls = _httpx.ConnectError
            timeout_error_cls = _httpx.TimeoutException
        except ImportError:
            connect_error_cls = ConnectionError  # type: ignore[assignment]
            timeout_error_cls = TimeoutError  # type: ignore[assignment]
        client = http_client
        owned_client = False

    # Multipart form data: protocol_pdf as a file, others as form fields.
    files = {
        "protocol_pdf": (protocol_filename, protocol_pdf, "application/pdf"),
    }
    if study_spec_json:
        files["study_spec_json"] = (
            f"{name}_study_spec.json", study_spec_json, "application/json",
        )
    if edc_build_zip:
        files["edc_build_zip"] = (
            f"{name}_edc_build.zip", edc_build_zip, "application/zip",
        )

    data: dict[str, str] = {"name": name}
    effective_sponsor = sponsor or sponsor_client
    if effective_sponsor:
        data["sponsor_client"] = effective_sponsor
    if protocol_number:
        data["protocol_number"] = protocol_number
    if protocol_pdf_sha256:
        data["protocol_pdf_sha256"] = protocol_pdf_sha256
    if source_pipeline_item:
        data["source_pipeline_item"] = str(source_pipeline_item)

    try:
        response = await client.post(url, files=files, data=data)
    except connect_error_cls:
        print(f"[trainer] not reachable at {url} — pending row not created",
              flush=True)
        if owned_client:
            await client.aclose()
        return None
    except timeout_error_cls:
        print(f"[trainer] /pending-row timed out at {url}", flush=True)
        if owned_client:
            await client.aclose()
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[trainer] /pending-row unexpected error: {type(exc).__name__}: {exc}",
              flush=True)
        if owned_client:
            await client.aclose()
        return None
    finally:
        if owned_client and not client.is_closed:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    if response.status_code not in (200, 201):
        print(f"[trainer] /pending-row HTTP {response.status_code}: "
              f"{response.text[:200]}",
              flush=True)
        return None

    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        print("[trainer] /pending-row returned non-JSON body", flush=True)
        return None

    item_id = body.get("item_id") if isinstance(body, dict) else None
    if not isinstance(item_id, int):
        print(f"[trainer] /pending-row response missing item_id: {body!r}",
              flush=True)
        return None

    print(f"[trainer] pending row created: item_id={item_id} name={name!r}",
          flush=True)
    return item_id



__all__ = [
    "run_protocol_analysis_quick",
    "retrieve_examples",
    "format_examples_block",
    "create_pending_row",
    "QUICK_ANALYSIS_PROMPT",
]
