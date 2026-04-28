"""
POST /retrieve — runtime retrieval, called by oc-ai-pipeline.

This is the read path. The pipeline calls this when it starts processing
a new protocol, and gets back the top-k similar past protocol-form pairs
to inject into its EDC structure prompt as few-shot examples.

This endpoint should be FAST (low hundreds of milliseconds). Heavy work
(embedding, indexing) happens elsewhere; this just queries the vector
store and returns metadata + paths to cached examples.

About the input shape: callers should send a parsed protocol-analysis
JSON dict in ``analysis``. That's the canonical form. The legacy
``protocol_text`` and ``fingerprint`` fields are still accepted but
discouraged — they go through the same canonical formatter so the
embedding is comparable, but unstructured text loses signal.
"""
from __future__ import annotations

from dataclasses import asdict

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_embedder, get_vector_store
from core.embed import Embedder, format_protocol_analysis_for_embedding
from core.vector_store import VectorStore

logger = structlog.get_logger(__name__)
router = APIRouter()


class RetrieveRequest(BaseModel):
    """
    Retrieval input.

    Provide ONE of:
      - analysis: parsed protocol-analysis JSON dict. **Preferred.**
        Same shape that the pipeline's protocol-analysis skill emits.
      - protocol_text: free-text fallback. Less signal; still works.
      - fingerprint: shorthand for the StudyFingerprint dict shape.

    `k` is the number of similar past pairs to return.
    `filters` lets the pipeline narrow the search by metadata
    (e.g. only return matches in the same therapeutic area).
    """

    analysis: dict | None = None
    protocol_text: str | None = None
    fingerprint: dict | None = None
    k: int = Field(default=10, ge=1, le=50)
    filters: dict | None = None  # e.g. {"therapeutic_area": "oncology"}


class RetrieveMatch(BaseModel):
    """One match in the retrieve response. Pydantic version of
    core.vector_store.RetrievedPair, for FastAPI serialization."""

    pair_hash: str
    similarity: float
    sponsor: str | None = None
    indication: str | None = None
    phase: str | None = None
    therapeutic_area: str | None = None
    nct_id: str | None = None
    has_protocol: bool = False
    monday_item_id: int | None = None
    form_design_path: str | None = None
    protocol_path: str | None = None
    indexed_at: str | None = None


class RetrieveResponse(BaseModel):
    matches: list[RetrieveMatch]
    query_embedding_dim: int
    embedding_ms: float
    search_ms: float


@router.post("", response_model=RetrieveResponse)
async def retrieve(
    payload: RetrieveRequest,
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
) -> RetrieveResponse:
    if not (payload.analysis or payload.protocol_text or payload.fingerprint):
        raise HTTPException(
            400,
            "Must provide one of: analysis, protocol_text, fingerprint",
        )

    # All paths converge on the same canonical formatter so query
    # embeddings are comparable to ingest-time embeddings.
    if payload.analysis is not None:
        text_to_embed = format_protocol_analysis_for_embedding(payload.analysis)
    elif payload.fingerprint is not None:
        text_to_embed = format_protocol_analysis_for_embedding(payload.fingerprint)
    else:
        # Free-text fallback — still goes through the formatter so the
        # leading "protocol_analysis (unstructured)" header is consistent.
        text_to_embed = format_protocol_analysis_for_embedding(
            payload.protocol_text or ""
        )

    import time

    t0 = time.perf_counter()
    query_vec = await embedder.embed(text_to_embed)
    t1 = time.perf_counter()

    matches = await store.query(
        query_vec=query_vec,
        k=payload.k,
        filters=payload.filters,
    )
    t2 = time.perf_counter()

    logger.info(
        "retrieve.success",
        k=payload.k,
        match_count=len(matches),
        embedding_ms=(t1 - t0) * 1000,
        search_ms=(t2 - t1) * 1000,
    )

    # Convert dataclass results → pydantic models for FastAPI
    return RetrieveResponse(
        matches=[RetrieveMatch(**asdict(m)) for m in matches],
        query_embedding_dim=len(query_vec),
        embedding_ms=(t1 - t0) * 1000,
        search_ms=(t2 - t1) * 1000,
    )
