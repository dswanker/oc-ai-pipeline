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

- CRITICAL — DO NOT COLLAPSE THE SCHEDULE OF EVENTS / SCHEDULE OF ASSESSMENTS TABLE.
  Most protocols include a structured table (often called "Schedule of Events",
  "Schedule of Assessments", or "Visit Schedule") that lists every visit, call,
  and assessment timepoint as a separate row. Your study_events output MUST contain
  one entry for every distinct row in that table. Never merge similar-looking rows
  into a single event.

  Concrete example: if the table lists "24 Hour Call", "48 Hour Call", "72 Hour
  Call", "96 Hour Call", "120 Hour Call", and "144 Hour Call" as six separate rows,
  you MUST emit six study_events (e.g. SE_CA24H, SE_CA48H, SE_CA72H, SE_CA96H,
  SE_CA120H, SE_CA144H). Do NOT consolidate them into a single
  "SE_PHONE_CALLS" or "SE_FOLLOWUP_CALLS" entry. Each timepoint needs its own
  OID, name, and category — even when the assessments performed at each are
  identical.

  Sanity check before returning: count the visit/call/assessment rows in the
  Schedule of Events table. Your study_events array length should match that
  count (plus any unscheduled events like AE/Concomitant Med logs).

- Do not invent visits that are not in the protocol. The Schedule of Events
  table is the authoritative source — only emit what is listed there.

- WORKED EXAMPLE — study this pattern carefully.

  Suppose the Schedule of Events table in the protocol contains 7 columns:

    | Assessment        | Screening | Day 1 | 24h Call | 48h Call | 72h Call | Day 7 | Day 30 |
    |-------------------|-----------|-------|----------|----------|----------|-------|--------|
    | Informed Consent  |    X      |       |          |          |          |       |        |
    | Adverse Events    |    X      |   X   |    X     |    X     |    X     |   X   |   X    |
    | Vital Signs       |    X      |   X   |          |          |          |   X   |   X    |

  CORRECT study_events output — exactly 7 entries, one per column header:

    {"oid": "SE_SCREEN",  "name": "Screening",  "category": "scheduled"}
    {"oid": "SE_D1",      "name": "Day 1",      "category": "scheduled"}
    {"oid": "SE_CA24H",   "name": "24h Call",   "category": "scheduled"}
    {"oid": "SE_CA48H",   "name": "48h Call",   "category": "scheduled"}
    {"oid": "SE_CA72H",   "name": "72h Call",   "category": "scheduled"}
    {"oid": "SE_D7",      "name": "Day 7",      "category": "scheduled"}
    {"oid": "SE_D30",     "name": "Day 30",     "category": "scheduled"}

  INCORRECT — the following are failure modes you must AVOID. Each of these
  is wrong because it collapses two or more separate Schedule columns into
  a single event entry, making downstream form-to-event mapping impossible:

    WRONG: {"oid": "SE_CALLS", "name": "Phone Calls"}
      ↑ collapses 3 separate columns (24h, 48h, 72h) into one event.

    WRONG: {"oid": "SE_D2_D7", "name": "Post-treatment Days 2-7"}
      ↑ uses a date range to merge multiple columns. Date ranges in event
      names or OIDs are NEVER correct — every column in the Schedule is
      its own discrete event with its own OID.

    WRONG: {"oid": "SE_FOLLOWUP", "name": "Follow-up Visits"}
      ↑ merges Day 7 and Day 30 into a single generic "follow-up" event.
      Even when assessments performed are similar across visits, each
      visit is still a separate event.

  Apply the same one-event-per-column expansion regardless of how many
  rows the Schedule has. A 30-column Schedule produces 30 study_events
  entries, not 5 grouped ones.

- forms: extract ALL CRFs. Use F_ prefix for form_id, bare code for form_oid_short.
  visits_assigned must list EVERY SE_ OID where this form is administered. If
  the Schedule of Events table shows a form checkmarked at multiple visits or
  calls, list all of them. For example, an AE form filled at every visit and
  every call must list every SE_ OID for those events — not just one
  representative SE_OID.

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
    model: str = "claude-opus-4-7",
    max_tokens: int = 16000,
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
