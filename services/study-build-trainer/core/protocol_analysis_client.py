"""
Trainer's protocol analysis client.

Uses a self-contained prompt (no tool use) to extract study structure
from a protocol PDF. Returns a structured JSON dict suitable for:
  - Embedding / vector search
  - Accuracy scoring against the human-approved ODM XML + XLSForms
  - Study fingerprinting

Intentionally simpler than the full pipeline protocol-analysis skill,
which requires tool use to read reference files. The trainer only needs
study structure, not XLSForm-level survey rows.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

try:
    import structlog
    logger = structlog.get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


ANALYSIS_PROMPT = """
You are an expert clinical trial data manager analyzing a clinical trial protocol PDF.

Extract the following information and return it as a single JSON object.
Return ONLY the JSON — no preamble, no explanation, no markdown fences.

Required JSON structure:
{
  "study_meta": {
    "protocol_number": "<protocol number, e.g. PrTK05>",
    "study_name": "<short study name>",
    "sponsor": "<sponsor organization>",
    "phase": "<1, 2, 3, or 4>",
    "indication": "<disease / therapeutic area>",
    "therapeutic_area": "<e.g. Oncology>",
    "study_type": "<interventional or observational>",
    "intervention": ["<drug 1>", "<drug 2>"]
  },
  "study_events": [
    {
      "oid": "<StudyEvent OID, e.g. SE_BASELINE>",
      "name": "<human label, e.g. Eligibility / Baseline>",
      "category": "<scheduled | unscheduled | common>"
    }
  ],
  "forms": [
    {
      "form_id": "<CDASH-style form OID, e.g. F_AE>",
      "form_oid_short": "<short form code used in XLSForm, e.g. AE>",
      "form_title": "<human readable title, e.g. Adverse Events>",
      "cdash_domain": "<CDASH domain, e.g. AE, DM, VS, LB — or N/A>",
      "visits_assigned": ["<SE_OID_1>", "<SE_OID_2>"],
      "complexity": "<Simple | Average | Complex>",
      "repeating": "<Yes | No>",
      "epro": "<Yes | No>",
      "arm": "<BOTH | TREATMENT | CONTROL>"
    }
  ],
  "form_event_matrix": [
    {
      "form_oid_short": "<e.g. AE>",
      "event_oid": "<e.g. SE_AE>"
    }
  ]
}

Rules:
- study_events: extract ALL study events / visits from the Schedule of Assessments.
  Use SE_ prefix for OIDs (e.g. SE_BASELINE, SE_C1, SE_AE, SE_EOS).
  Match OID naming conventions: uppercase, underscores, no spaces.
- forms: extract ALL CRFs. Use F_ prefix for form_id, bare code for form_oid_short.
  visits_assigned should list the SE_ OIDs where this form appears.
- form_event_matrix: one entry per form-event assignment (same data as visits_assigned,
  but flattened for easy lookup).
- If a value is unknown, use null — never omit required fields.
- Do not include XLSForm survey rows or choice lists — just structure.
""".strip()


async def run_protocol_analysis(
    pdf_bytes: bytes,
    *,
    client=None,
    api_key: str | None = None,
    model: str = "claude-opus-4-6",
    max_tokens: int = 8000,
    max_retries: int = 3,
    initial_wait_seconds: int = 60,
) -> str:
    """
    Analyze a protocol PDF and return structured JSON as a string.
    No tool use — fully self-contained single-turn call.
    """
    if client is None:
        from anthropic import AsyncAnthropic
        if api_key is None:
            from app.config import settings
            api_key = settings.anthropic_api_key
        client = AsyncAnthropic(api_key=api_key)

    content = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf_bytes).decode(),
            },
        },
        {"type": "text", "text": ANALYSIS_PROMPT},
    ]

    last_exc = None
    for attempt in range(max_retries):
        try:
            logger.info(
                "protocol_analysis.attempt",
                attempt=attempt + 1,
                content_blocks=len(content),
                model=model,
            )
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}],
            )
            text = "".join(
                getattr(block, "text", "") or ""
                for block in response.content
            )
            logger.info("protocol_analysis.success", response_length=len(text))
            return text

        except Exception as exc:
            from anthropic import RateLimitError, APIError
            if isinstance(exc, RateLimitError):
                last_exc = exc
                if attempt < max_retries - 1:
                    wait = initial_wait_seconds * (attempt + 1)
                    logger.warning(
                        "protocol_analysis.rate_limited",
                        attempt=attempt + 1,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError("protocol_analysis: exited retry loop without result")
