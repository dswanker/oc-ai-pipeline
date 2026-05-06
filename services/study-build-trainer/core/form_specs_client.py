"""
Generates per-form XLSForm specs (survey + choices) from the protocol
analysis JSON using CDASH domain knowledge.

Optimizations vs v1:
- Concurrent calls via asyncio.gather + semaphore (20 sequential → parallel)
- max_tokens 8000 → 3000 (right-sized for typical form output)
- Compact prompt (removed redundant schema examples)
- Single shared client across all concurrent calls
- Batch-level rate-limit handling rather than per-form retry loops
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

try:
    import structlog
    logger = structlog.get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


FORM_SPEC_SYSTEM = (
    "You are an OpenClinica 4 EDC builder with deep knowledge of the "
    "Clinical Data Acquisition Standards Harmonization Implementation "
    "Guide (CDASHIG), published by CDISC. All variable names, domain "
    "conventions, and value lists you produce MUST follow the LATEST "
    "published CDASHIG version you have knowledge of — never legacy "
    "names from earlier versions. CDASHIG evolves over time (v1.0, v1.1, "
    "v2.0, v2.1, v2.2, and so on); use the most recent version "
    "available in your training, not whichever variant happens to come "
    "to mind first. "
    "Return ONLY valid JSON, no preamble, no markdown fences."
)

# Compact prompt — no verbose schema example, no repeated field values
_PROMPT_TEMPLATE = """\
Generate XLSForm content for this CRF using the LATEST CDASHIG version's conventions.

Form: {form_id} | {form_title} | CDASH: {cdash} | Repeating: {repeating} | ePRO: {epro}

PROTOCOL DATA POINTS — these are the specific items the protocol asks to capture
on this form. Your survey output MUST cover all of these. Use them as the
ground-truth list of fields the customer expects:
{protocol_data_points_block}

Return JSON with keys: form_id, form_title, settings, survey, choices.

settings: {{form_title, form_id, version:"1", style:"theme-grid", \
namespaces:"oc=\\"http://openclinica.org/xforms\\" , OpenClinica=\\"http://openclinica.com/odm\\""}}

survey rows: [{{"type","name","label","required","relevant","constraint","calculation","oc:itemgroupOID"}}]
choices rows: [{{"list_name","name","label"}}]

Rules (Patch 14 makes these protocol-grounded rather than CDASHIG-default):
- Generate one survey row for EACH protocol data point listed above. Do not
  skip data points the protocol asks for, even if they're unusual or non-CDASH.
- For each data point, choose the variable name following LATEST CDASHIG
  conventions when the data point maps to a standard CDASH variable. For
  data points that don't have a CDASH equivalent (study-specific items),
  invent a name following CDASH naming patterns (uppercase, domain-prefixed,
  underscore-free e.g. AESPID style). Preserve the protocol's label verbatim
  in the label field — labels are how the customer will recognize the field.
- Choices for select_one/select_multiple data points: use the protocol's
  listed values when provided. Map to LATEST CDISC controlled terminology
  values (MILD/MODERATE/SEVERE etc.) when the protocol's values clearly
  match a CDISC codelist; preserve as-given otherwise.
- After covering all protocol data points, add CDASHIG "Highly Recommended"
  variables for the domain ONLY if they're clearly required by the protocol
  (e.g. AESER for serious AE flag if the protocol mentions SAE reporting).
  Don't pad with CDASH defaults that the protocol doesn't ask for.
- Use ONLY LATEST CDASHIG variable names for standard items (AETERM, AESTDAT,
  AEENDAT, VSPERF, VSDAT, VSORRES, LBPERF, LBDAT, LBORRES, MHTERM, MHSTDAT).
  Avoid deprecated older-version names (e.g. AESTDTC was superseded by AESTDAT).

XLSForm structural rules:
- Start with TPTCALC (calculate) + TPT (text, timepoint label)
- Wrap data fields in begin group/end group, OID pattern IG_{cdash}_{cdash}
- Perf question first (VSPERF/LBPERF), then date, then results
- select_one fields: UPPERCASE list names
- relevant logic on conditional fields
"""


def _format_data_points(form: dict[str, Any]) -> str:
    """Render the form's protocol_data_points list as a compact bullet block.

    Patch 14: protocol_analysis now extracts a per-form data-point list from
    the protocol PDF. We render it here as input to form_specs so the model
    grounds its survey rows in the protocol's actual data collection
    requirements rather than CDASH defaults.

    Returns "(none extracted from protocol — fall back to CDASHIG defaults)"
    when the form lacks data points, which preserves the pre-Patch-14
    behavior for back-compat with old analysis JSON.
    """
    points = form.get("protocol_data_points") or []
    if not points:
        return "  (none extracted from protocol — fall back to CDASHIG defaults)"

    lines = []
    for p in points:
        if not isinstance(p, dict):
            continue
        label = (p.get("label") or "").strip()
        ptype = (p.get("type") or "text").strip()
        values = p.get("values") or []
        line = f"  - {label} (type: {ptype})"
        if values and isinstance(values, list):
            sample = ", ".join(str(v) for v in values[:8])
            if len(values) > 8:
                sample += f", … +{len(values) - 8} more"
            line += f"  [values: {sample}]"
        lines.append(line)
    return "\n".join(lines) if lines else "  (data points present but malformed)"


def _prompt(form: dict[str, Any]) -> str:
    return _PROMPT_TEMPLATE.format(
        form_id=form.get("form_oid_short", ""),
        form_title=form.get("form_title", ""),
        cdash=form.get("cdash_domain", "N/A"),
        repeating=form.get("repeating", "No"),
        epro=form.get("epro", "No"),
        protocol_data_points_block=_format_data_points(form),
    )


def _parse_spec(text: str) -> dict[str, Any] | None:
    """Tolerant JSON extractor for Sonnet form_spec responses.

    Patch 6 made this multi-strategy because Sonnet occasionally adds
    preamble ('Here's the JSON for AE:'), trailing prose, or wraps the
    JSON in markdown fences anywhere in the response. The original parser
    only handled fences at the very start of the string and failed silently
    on every other shape, which dropped ~50% of form_specs on CRS-135.

    Strategies, tried in order:
      1. Markdown fence anywhere in the text  (```json ... ``` or ``` ... ```)
      2. Whole-string as-is                   (clean responses, the happy path)
      3. First balanced {...} block           (preamble/postamble cases)

    Returns the parsed dict, or None if nothing parses.
    """
    if not text:
        return None
    t = text.strip()

    # Strategy 1: markdown fence anywhere
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", t, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 2: try as-is (fast path for clean responses)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # Strategy 3: locate first balanced {...} block, scanning while
    # respecting string boundaries and escapes
    start = t.find("{")
    if start >= 0:
        depth, in_string, escape = 0, False, False
        for i in range(start, len(t)):
            c = t[i]
            if escape:
                escape = False
                continue
            if in_string:
                if c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break  # malformed even balanced — give up
    return None


async def _generate_one(
    form: dict[str, Any],
    client,
    model: str,
    max_tokens: int,
    semaphore: asyncio.Semaphore,
    *,
    max_retries: int = 4,
    initial_wait_seconds: int = 5,
) -> tuple[str, dict[str, Any] | None]:
    """Generate spec for one form. Returns (form_id, spec_or_None).

    Patch 12: retry on transient API errors (429 rate limit, 529 overloaded,
    5xx server errors). The Anthropic SDK does its own 2 retries with
    sub-second backoffs, but Anthropic's overload errors typically clear
    in 30-60s — sub-second retries don't give the queue time to recover.
    Result on CRS-135: ~5 of 30 forms fail per run (different forms each
    run because failures are random), losing ~80-100 items.

    Outer retry loop with exponential backoff (5s, 10s, 20s, 40s = 75s
    worst-case wait) sits on top of the SDK's own retries. Total worst
    case per form: 5 attempts × ~3 SDK retries each = 15 API calls,
    bounded ~135s wallclock. Realistic case: 1-2 outer retries needed.

    Holds the semaphore through retries — this is intentional. When all
    workers hit overload, holding slots throttles concurrent load on
    Anthropic until the queue recovers, which is exactly the behavior
    we want.
    """
    form_id = form.get("form_oid_short", "")
    if not form_id:
        return form_id, None

    async with semaphore:
        logger.info("form_specs.generating", form_id=form_id)
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0.0,  # Patch 13: deterministic output. The
                                      # default temperature (~1.0) made Sonnet
                                      # 4.6 produce wildly different choice
                                      # lists per call (e.g. ODI form
                                      # generated 67 choices in Run 14, 8 in
                                      # Run 15, 24 in Run 16 — same prompt).
                                      # temperature=0 pins the output for a
                                      # given prompt, giving us stable
                                      # baselines for measuring future
                                      # patches.
                    system=FORM_SPEC_SYSTEM,
                    messages=[{"role": "user", "content": _prompt(form)}],
                )
                text = "".join(
                    getattr(b, "text", "") or "" for b in response.content
                )
                spec = _parse_spec(text)
                if spec:
                    logger.info(
                        "form_specs.done",
                        form_id=form_id,
                        survey_rows=len(spec.get("survey", [])),
                        choices=len(spec.get("choices", [])),
                        attempts=attempt + 1,
                    )
                else:
                    # Patch 6: log a preview of the response on parse failure so
                    # we can see what shape Sonnet actually returned. Without
                    # this, every failure was opaque.
                    preview = (text or "")[:300].replace("\n", "\\n")
                    logger.warning(
                        "form_specs.parse_failed",
                        form_id=form_id,
                        text_len=len(text or ""),
                        text_preview=preview,
                    )
                return form_id, spec
            except Exception as exc:
                # Determine if this is a transient error worth retrying.
                # We classify retryable based on Anthropic SDK exception class
                # to avoid retrying on non-transient bugs (auth errors, code
                # errors, etc.). Lazy-import to keep this module importable
                # in test environments without the anthropic SDK.
                try:
                    from anthropic import (
                        APIConnectionError, APITimeoutError, RateLimitError,
                        InternalServerError, APIStatusError,
                    )
                    is_transient_class = isinstance(exc, (
                        APIConnectionError, APITimeoutError,
                        RateLimitError, InternalServerError,
                    ))
                    is_retryable_status = (
                        isinstance(exc, APIStatusError)
                        and getattr(exc, "status_code", None)
                        in (429, 503, 529)
                    )
                    is_retryable = is_transient_class or is_retryable_status
                except ImportError:
                    # Fallback: retry on status codes we recognize even
                    # without typed exceptions.
                    status = getattr(exc, "status_code", None)
                    is_retryable = status in (429, 503, 529) or (
                        status is not None and 500 <= status < 600
                    )

                status = getattr(exc, "status_code", None)
                if is_retryable and attempt < max_retries:
                    wait = initial_wait_seconds * (2 ** attempt)
                    logger.warning(
                        "form_specs.retrying",
                        form_id=form_id,
                        attempt=attempt + 1,
                        max_attempts=max_retries + 1,
                        status=status,
                        error_class=type(exc).__name__,
                        wait_seconds=wait,
                    )
                    last_exc = exc
                    await asyncio.sleep(wait)
                    continue
                # Non-retryable, or out of retries — log and give up
                logger.warning(
                    "form_specs.failed",
                    form_id=form_id,
                    error=str(exc),
                    error_class=type(exc).__name__,
                    status=status,
                    attempts=attempt + 1,
                )
                return form_id, None
        # Defensive: shouldn't reach here because the loop always returns,
        # but keep this for safety against future refactors.
        if last_exc is not None:
            logger.warning(
                "form_specs.failed",
                form_id=form_id,
                error=str(last_exc),
                reason="exhausted_retries",
            )
        return form_id, None


async def generate_form_specs(
    analysis_dict: dict[str, Any],
    *,
    client=None,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 8000,
    concurrency: int = 6,
) -> dict[str, Any]:
    """
    Generate per-form XLSForm specs for all forms concurrently.

    concurrency=6 keeps us well within Sonnet rate limits while running
    ~3-4x faster than sequential. Tune up/down based on your API tier.
    """
    if client is None:
        from anthropic import AsyncAnthropic
        if api_key is None:
            from app.config import settings
            api_key = settings.anthropic_api_key
        client = AsyncAnthropic(api_key=api_key)

    forms = [f for f in analysis_dict.get("forms", []) if f.get("form_oid_short")]
    semaphore = asyncio.Semaphore(concurrency)

    tasks = [
        _generate_one(form, client, model, max_tokens, semaphore)
        for form in forms
    ]
    pairs = await asyncio.gather(*tasks, return_exceptions=False)

    return {fid: spec for fid, spec in pairs if spec is not None}


def merge_form_specs_into_analysis(
    analysis_dict: dict[str, Any],
    form_specs: dict[str, Any],
) -> dict[str, Any]:
    """Merge survey/choices/settings into analysis_dict forms list."""
    merged_forms = []
    for form in analysis_dict.get("forms", []):
        form_id = form.get("form_oid_short", "")
        spec = form_specs.get(form_id)
        if spec:
            merged_form = {**form,
                           "form_id":  form_id,
                           "survey":   spec.get("survey", []),
                           "choices":  spec.get("choices", []),
                           "settings": spec.get("settings", {})}
            merged_forms.append(merged_form)
        else:
            merged_forms.append(form)
    return {**analysis_dict, "forms": merged_forms}
