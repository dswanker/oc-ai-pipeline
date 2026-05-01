"""
POST /webhook/monday — entry point from monday boards.

Handles webhooks from two boards:

  1. Study Build Trainer — Corpus (board 18410424473)
     Columns we watch:
       - Trigger         (color_mm2tw612) — human kicks off ingest
       - Human Decision  (color_mm2th07z) — human responds to a question

  2. Convention Rulebook (board 18411236453)
     Columns we watch:
       - Submit Trigger  (color_mm2y41kb) — human submits appendix for review

Per the concurrency lessons in TODO/TODO-concurrency-queueing.md, this
handler MUST return immediately (HTTP 202) and dispatch real work to a
background task. Long-running work in a sync webhook handler will time
out monday's webhook delivery (~30s).
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.deps import get_ingest_queue
from workers.queue import IngestJob, IngestJobKind, IngestQueue

logger = structlog.get_logger(__name__)
router = APIRouter()


class MondayWebhookEvent(BaseModel):
    """
    Subset of the monday webhook payload we care about.

    The full payload is documented at:
        https://developer.monday.com/api-reference/docs/webhooks
    """

    type: str  # "create_pulse", "update_column_value", etc.
    pulseId: int | None = None  # item ID
    boardId: int | None = None
    columnId: str | None = None
    value: dict | None = None
    previousValue: dict | None = None


class MondayWebhookPayload(BaseModel):
    event: MondayWebhookEvent | None = None
    challenge: str | None = None  # monday's webhook handshake


# ── Column ID constants ───────────────────────────────────────────────────────

# Corpus board columns
CORPUS_TRIGGER_COL       = "color_mm2tw612"
CORPUS_HUMAN_DECISION_COL = "color_mm2th07z"

# Convention Rulebook board columns
CONVENTION_TRIGGER_COL   = "color_mm2y41kb"
CONVENTION_SUBMIT_LABEL  = "Submit for Review"

# Convention Rulebook board ID
CONVENTION_BOARD_ID      = 18411236453


@router.post("/monday", status_code=202)
async def monday_webhook(
    request: Request,
    payload: MondayWebhookPayload,
    background: BackgroundTasks,
    queue: IngestQueue = Depends(get_ingest_queue),
    x_monday_signature: str | None = Header(default=None),
) -> dict[str, str]:
    """
    Monday webhook receiver.

    Returns 202 immediately. Real work happens via the queue + worker.

    monday's initial webhook handshake POSTs a `challenge` field that
    must be echoed back; we handle that first.
    """
    # 1. Handshake
    if payload.challenge:
        return {"challenge": payload.challenge}

    if not payload.event:
        raise HTTPException(400, "Missing event payload")

    # TODO: verify x_monday_signature against MONDAY_WEBHOOK_SECRET.
    # See https://developer.monday.com/api-reference/docs/webhooks#verifying-webhook-requests

    event = payload.event
    logger.info(
        "monday.webhook.received",
        event_type=event.type,
        item_id=event.pulseId,
        column_id=event.columnId,
    )

    # ── Convention Rulebook board ─────────────────────────────────────────────
    # Check this first so it doesn't fall through to the corpus logic.
    if event.columnId == CONVENTION_TRIGGER_COL:
        # Only fire when the label changes to "Submit for Review"
        new_label = ""
        if isinstance(event.value, dict):
            new_label = (event.value.get("label") or {}).get("text", "")

        if new_label == CONVENTION_SUBMIT_LABEL and event.pulseId is not None:
            logger.info(
                "convention.webhook.received",
                item_id=event.pulseId,
                label=new_label,
            )
            from workers.convention_worker import process_convention_job
            asyncio.create_task(process_convention_job(int(event.pulseId)))
            return {"status": "queued", "board": "convention_rulebook"}

        # Label changed to something else (e.g. "Awaiting Human") — ignore
        logger.info("convention.webhook.ignored", label=new_label)
        return {"status": "ignored"}

    # ── Corpus board ──────────────────────────────────────────────────────────
    if event.columnId == CORPUS_TRIGGER_COL:
        # Verify it actually changed to "Send to Trainer" (label id 0).
        # TODO: parse event.value to confirm new label is "Send to Trainer".
        kind = IngestJobKind.START
    elif event.columnId == CORPUS_HUMAN_DECISION_COL:
        kind = IngestJobKind.HUMAN_RESPONSE
    else:
        # Not a column we care about — accept but ignore.
        logger.info("monday.webhook.ignored", column_id=event.columnId)
        return {"status": "ignored"}

    if event.pulseId is None:
        raise HTTPException(400, "Missing pulseId on monday event")

    # 3. Enqueue. The worker will pick this up and do the actual work.
    job = IngestJob(
        kind=kind,
        monday_item_id=event.pulseId,
        monday_board_id=event.boardId or 0,
    )
    await queue.enqueue(job)

    return {"status": "queued", "job_id": job.job_id}
