"""
POST /webhook/monday — entry point from the monday corpus board.

monday fires this webhook when configured columns change on the
"Study Build Trainer — Corpus" board (board ID in settings). We
expect notifications for two columns:

  - Trigger              (color_mm2tw612) — human kicks off ingest
  - Human Decision       (color_mm2th07z) — human responds to a question

Per the concurrency lessons in TODO/TODO-concurrency-queueing.md, this
handler MUST return immediately (HTTP 202) and dispatch real work to a
background task. Long-running work in a sync webhook handler will time
out monday's webhook delivery (~30s).
"""
from __future__ import annotations

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

    # 2. Determine intent. Two columns trigger trainer action:
    #    - Trigger column            → start ingest
    #    - Human Decision column     → resume paused ingest
    #
    # Column IDs come from the board (see top of file).
    TRIGGER_COL = "color_mm2tw612"
    HUMAN_DECISION_COL = "color_mm2th07z"

    if event.columnId == TRIGGER_COL:
        # Verify it actually changed to "Send to Trainer" (label id 0).
        # TODO: parse event.value to confirm new label is "Send to Trainer".
        kind = IngestJobKind.START
    elif event.columnId == HUMAN_DECISION_COL:
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
