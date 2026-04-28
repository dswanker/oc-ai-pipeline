"""
POST /ingest — manual ingest entry point.

Used for:
  - CLI-driven bulk ingest of an existing folder of form/protocol pairs
  - Backfilling specific rows when monday's webhook didn't fire
  - Testing the ingest pipeline outside of monday

Most production traffic should come through /webhook/monday instead.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, HttpUrl

from app.deps import get_ingest_queue
from workers.queue import IngestJob, IngestJobKind, IngestQueue

router = APIRouter()


class ManualIngestRequest(BaseModel):
    """
    Manual ingest. Provide either:
      - a monday item ID (worker reads files from monday columns), OR
      - direct URLs to form_design and (optionally) protocol files.

    If both are provided, monday wins.
    """

    monday_item_id: int | None = None
    form_design_url: HttpUrl | None = None
    protocol_url: HttpUrl | None = None


@router.post("", status_code=202)
async def manual_ingest(
    payload: ManualIngestRequest,
    queue: IngestQueue = Depends(get_ingest_queue),
) -> dict[str, str]:
    job = IngestJob(
        kind=IngestJobKind.START,
        monday_item_id=payload.monday_item_id,
        form_design_url=str(payload.form_design_url) if payload.form_design_url else None,
        protocol_url=str(payload.protocol_url) if payload.protocol_url else None,
    )
    await queue.enqueue(job)
    return {"status": "queued", "job_id": job.job_id}
