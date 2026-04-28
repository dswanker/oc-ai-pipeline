"""
Shared dependency providers for FastAPI route handlers.

Use these via ``Depends(...)`` in routes — keeps construction logic out
of route code and makes testing easier (override these in tests to
inject mocks).

Singleton lifetime via ``lru_cache(maxsize=1)``: every consumer in the
process gets the same instance. That matters for the embedder (we
don't want to load the 1.3 GB model twice) and for the vector store
(we want one connection pool, one schema).

Coordination of dim: the vector store needs to know the embedding
dimension at construction time. That dim comes from the embedder's
loaded model. ``get_vector_store`` triggers an embedder load and
asks for its dim before constructing the store. First call is slow
(model download/load); subsequent calls are instant.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import Request

from core.ctgov_client import CTGovClient
from core.embed import Embedder
from core.fingerprint import FingerprintExtractor
from core.monday_client import MondayClient
from core.vector_store import VectorStore
from workers.queue import IngestQueue


def get_ingest_queue(request: Request) -> IngestQueue:
    """Returns the singleton ingest queue attached to app.state."""
    return request.app.state.ingest_queue


@lru_cache(maxsize=1)
def get_monday_client() -> MondayClient:
    return MondayClient()


@lru_cache(maxsize=1)
def get_ctgov_client() -> CTGovClient:
    """Currently unused — CT.gov support deferred. Kept for forward
    compat so the route handlers don't have to be reshaped when we
    do enable it."""
    return CTGovClient()


@lru_cache(maxsize=1)
def get_fingerprint_extractor() -> FingerprintExtractor:
    return FingerprintExtractor()


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    """Singleton vector store, with vec_dim aligned to the embedder.

    Triggers an embedder load on first call so we can ask for its
    dim. After that, every consumer (retrieve route, ingest worker)
    gets the same store with the same dim, so embeddings written and
    embeddings queried are guaranteed-comparable.
    """
    embedder = get_embedder()
    return VectorStore(vec_dim=embedder.dim)
