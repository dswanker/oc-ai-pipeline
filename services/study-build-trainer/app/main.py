"""
FastAPI app entry point for the OC4 Study Build Trainer.

Runs on port 8001 by default (see TRAINER_PORT in .env). Mounts route
modules from app/routes and starts the in-process ingest worker on
startup.

Run locally:
    uvicorn app.main:app --port 8001 --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI

from app.config import settings
from app.routes import health, ingest, jobs, retrieve, webhook
from workers.queue import IngestQueue

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Start the ingest worker on app startup, stop it on shutdown.

    The queue is attached to app.state so route handlers can enqueue
    jobs via `request.app.state.ingest_queue.enqueue(...)`.
    """
    logger.info("trainer.startup", port=settings.trainer_port)

    queue = IngestQueue()
    await queue.start()
    app.state.ingest_queue = queue

    # TODO: warm up the embedding model here (sentence-transformers
    # downloads the model on first use; doing it at startup avoids a
    # cold-start on the first /retrieve call).

    # TODO: open the vector store connection and stash on app.state.

    try:
        yield
    finally:
        logger.info("trainer.shutdown")
        await queue.stop()


def create_app() -> FastAPI:
    logging.basicConfig(level=settings.trainer_log_level.upper())

    app = FastAPI(
        title="OC4 Study Build Trainer",
        version="0.1.0",
        description=(
            "RAG retrieval microservice for the oc-ai-pipeline. "
            "Indexes historical protocol-form pairs and serves them as "
            "few-shot examples at pipeline runtime."
        ),
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(webhook.router, prefix="/webhook", tags=["webhook"])
    app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
    app.include_router(retrieve.router, prefix="/retrieve", tags=["retrieve"])
    app.include_router(jobs.router, tags=["jobs"])

    return app


app = create_app()
