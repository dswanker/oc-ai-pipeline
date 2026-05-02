"""
Generates per-form XLSForm specs (survey rows + choices) from the
protocol analysis JSON using CDASH domain knowledge.

This is the second phase of protocol analysis, feeding build_all_xlsforms
so the predicted EDC ZIP contains real XLSForm files for Items/Choices/Logic
accuracy scoring.

Kept as a separate module from protocol_analysis_client.py because:
- It's called only when generating a predicted build (not for embedding)
- It can be cached separately from the study-structure analysis
- Prompts/tokens are very different from the study-structure call
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

try:
    import structlog
    logger = structlog.get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


FORM_SPEC_SYSTEM = """
You are an expert OpenClinica 4 EDC builder with deep knowledge of CDASH data standards.
You generate XLSForm survey and choices content for clinical trial CRF forms.
Return ONLY valid JSON — no preamble, no explanation, no markdown fences.
""".strip()


def _form_spec_prompt(form: dict[str, Any]) -> str:
    return f"""
Generate XLSForm survey rows and choices for this clinical trial CRF form:

Form ID: {form.get('form_oid_short', '')}
Form Title: {form.get('form_title', '')}
CDASH Domain: {form.get('cdash_domain', 'N/A')}
Repeating: {form.get('repeating', 'No')}
ePRO: {form.get('epro', 'No')}

Return a JSON object with this exact structure:
{{
  "form_id": "{form.get('form_oid_short', '')}",
  "form_title": "{form.get('form_title', '')}",
  "settings": {{
    "form_title": "{form.get('form_title', '')}",
    "form_id": "{form.get('form_oid_short', '')}",
    "version": "1",
    "style": "theme-grid",
    "namespaces": "oc=\\"http://openclinica.org/xforms\\" , OpenClinica=\\"http://openclinica.com/odm\\""
  }},
  "survey": [
    {{
      "type": "<XLSForm type: text | integer | decimal | date | select_one LIST_NAME | select_multiple LIST_NAME | begin group | end group | note | calculate>",
      "name": "<CDASH variable name, e.g. AESTDAT, AETERM, VSPERF>",
      "label": "<Human readable question label>",
      "required": "<yes | no | >",
      "relevant": "<XPath expression or empty string>",
      "constraint": "<XPath expression or empty string>",
      "calculation": "<XPath expression or empty string>",
      "oc:itemgroupOID": "<IG_DOMAIN_DOMAIN pattern, e.g. IG_AE_AE>"
    }}
  ],
  "choices": [
    {{
      "list_name": "<UPPERCASE list name, e.g. YES_NO_C>",
      "name": "<coded value, e.g. Y>",
      "label": "<display label, e.g. Yes>"
    }}
  ]
}}

Rules:
- Use standard CDASH variable names for the domain (e.g. AE domain: AETERM, AESTDAT, AEENDAT, AESER, AEOUT, etc.)
- Always start with a TPTCALC (text, hidden timepoint calculator) and TPT (text, timepoint label) row
- Wrap all data fields in a begin group / end group using IG_DOMAIN_DOMAIN naming
- Performed question (VSPERF, LBPERF, etc.) should come first for assessments
- Date fields use type: date
- Coded fields use type: select_one LISTNAME — include the choices
- Free text fields use type: text
- Numeric fields use type: integer or decimal
- Include relevant logic for conditional fields (e.g. show result only if PERF=Yes)
- choices: include all standard coded values for the domain
- Keep survey rows to the most clinically essential fields for this domain
""".strip()


async def generate_form_specs(
    analysis_dict: dict[str, Any],
    *,
    client=None,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 8000,
    max_retries: int = 2,
) -> dict[str, Any]:
    """
    Generate per-form XLSForm specs for all forms in analysis_dict.

    Returns a dict keyed by form_oid_short, each value being a form
    dict with 'survey', 'choices', 'settings' keys suitable for
    build_all_xlsforms.

    Uses Sonnet (faster/cheaper than Opus) since this is structured
    generation from domain knowledge, not complex reasoning.
    """
    if client is None:
        from anthropic import AsyncAnthropic
        if api_key is None:
            from app.config import settings
            api_key = settings.anthropic_api_key
        client = AsyncAnthropic(api_key=api_key)

    forms = analysis_dict.get("forms", [])
    result: dict[str, Any] = {}

    for form in forms:
        form_id = form.get("form_oid_short", "")
        if not form_id:
            continue

        prompt = _form_spec_prompt(form)
        logger.info("form_specs.generating", form_id=form_id)

        for attempt in range(max_retries):
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=FORM_SPEC_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(
                    getattr(block, "text", "") or ""
                    for block in response.content
                ).strip()

                # Strip markdown fences if present
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()

                spec = json.loads(text)
                result[form_id] = spec
                logger.info("form_specs.done", form_id=form_id,
                            survey_rows=len(spec.get("survey", [])),
                            choices=len(spec.get("choices", [])))
                break

            except Exception as exc:
                from anthropic import RateLimitError
                if isinstance(exc, RateLimitError) and attempt < max_retries - 1:
                    logger.warning("form_specs.rate_limited", form_id=form_id,
                                   attempt=attempt + 1)
                    await asyncio.sleep(60)
                    continue
                logger.warning("form_specs.failed", form_id=form_id,
                               error=str(exc))
                break

    return result


def merge_form_specs_into_analysis(
    analysis_dict: dict[str, Any],
    form_specs: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge per-form survey/choices/settings into the analysis dict's
    forms list so build_all_xlsforms can consume it directly.
    """
    merged = dict(analysis_dict)
    merged_forms = []

    for form in analysis_dict.get("forms", []):
        form_id = form.get("form_oid_short", "")
        spec = form_specs.get(form_id)
        if spec:
            merged_form = dict(form)
            merged_form["survey"]   = spec.get("survey", [])
            merged_form["choices"]  = spec.get("choices", [])
            merged_form["settings"] = spec.get("settings", {})
            # build_all_xlsforms uses form_id key (short code)
            merged_form["form_id"]  = form_id
            merged_forms.append(merged_form)
        else:
            merged_forms.append(form)

    merged["forms"] = merged_forms
    return merged
