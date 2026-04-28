"""
GET /jobs/{id}     — status of an ingest job (started, parsing, etc.)
GET /corpus/stats  — corpus-level statistics
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.deps import get_ingest_queue, get_vector_store
from core.vector_store import VectorStore
from workers.queue import IngestQueue

router = APIRouter()


class JobStatusResponse(BaseModel):
    job_id: str
    state: str  # queued | running | awaiting_human | done | failed
    monday_item_id: int | None = None
    error: str | None = None


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: str,
    queue: IngestQueue = Depends(get_ingest_queue),
) -> JobStatusResponse:
    status = queue.get_status(job_id)
    if status is None:
        raise HTTPException(404, f"No job with id {job_id}")
    return JobStatusResponse(**status)


class CorpusStatsResponse(BaseModel):
    total_pairs: int
    pairs_with_protocol: int
    pairs_without_protocol: int  # form-only entries (no CT.gov or human protocol)
    distinct_sponsors: int
    indexed_at_latest: str | None = None


@router.get("/corpus/stats", response_model=CorpusStatsResponse)
async def corpus_stats(
    store: VectorStore = Depends(get_vector_store),
) -> CorpusStatsResponse:
    stats = await store.stats()
    return CorpusStatsResponse(**stats)
