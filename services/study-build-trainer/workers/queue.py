"""
In-process FIFO queue for ingest jobs.

Phase 1 deliberate simplicity: a single asyncio.Queue and a single
worker coroutine. Pulls one job at a time, runs it to completion,
then takes the next.

This pattern matches Option 2 in TODO/TODO-concurrency-queueing.md.
Same constraints apply:
  - Queue lives in memory; lost if container restarts (fine for v1).
  - Single-container only (we're on one Railway service).
  - HTTP 202 from webhook handlers, real work happens here.

When we need horizontal scale, this gets replaced by Redis or a real
job queue. The interface (.enqueue, .get_status) stays the same.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)


class IngestJobKind(StrEnum):
    START = "start"  # initial trigger from monday or manual
    HUMAN_RESPONSE = "human_response"  # human set Human Decision column


class IngestJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    DONE = "done"
    FAILED = "failed"


@dataclass
class IngestJob:
    kind: IngestJobKind
    monday_item_id: int | None = None
    monday_board_id: int = 0
    form_design_url: str | None = None
    protocol_url: str | None = None
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: IngestJobState = IngestJobState.QUEUED
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class IngestQueue:
    """Async FIFO queue + single-worker coroutine."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[IngestJob] = asyncio.Queue()
        self._jobs: dict[str, IngestJob] = {}
        self._worker_task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        """Spawn the worker coroutine. Idempotent."""
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._run(), name="ingest-worker")
        logger.info("queue.started")

    async def stop(self) -> None:
        """Stop the worker. Drains nothing — in-flight job will complete or fail."""
        self._stopping = True
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("queue.stopped")

    async def enqueue(self, job: IngestJob) -> None:
        self._jobs[job.job_id] = job
        await self._queue.put(job)
        logger.info("queue.enqueued", job_id=job.job_id, kind=job.kind.value)

    def get_status(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        return {
            "job_id": job.job_id,
            "state": job.state.value,
            "monday_item_id": job.monday_item_id,
            "error": job.error,
        }

    async def _run(self) -> None:
        """Worker loop. Pulls one job at a time, runs it, repeats."""
        # Late import to avoid circular dependencies (worker → core → ...)
        from workers.ingest_worker import process_job

        while not self._stopping:
            job = await self._queue.get()
            job.state = IngestJobState.RUNNING
            try:
                await process_job(job)
                # process_job mutates job.state to DONE or AWAITING_HUMAN
                if job.state == IngestJobState.RUNNING:
                    job.state = IngestJobState.DONE
            except Exception as exc:  # noqa: BLE001 — log everything, don't crash worker
                logger.exception("queue.job_failed", job_id=job.job_id)
                job.state = IngestJobState.FAILED
                job.error = str(exc)
            finally:
                self._queue.task_done()
