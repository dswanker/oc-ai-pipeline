"""
Embedding model wrapper.

Wraps ``sentence-transformers`` (BAAI/bge-large-en-v1.5 by default)
behind a small async-friendly API. The actual sentence-transformers
calls are synchronous and CPU/GPU bound, so we run them in a thread
pool (``asyncio.to_thread``) to keep the FastAPI event loop free.

Why local embeddings instead of an API:
  * No third-party data exposure — protocol JSONs may contain sensitive
    sponsor / drug / patient-population details.
  * Works on-prem when we eventually offer that.
  * No per-call cost.
  * Tradeoff: ~1.3 GB model download on first use, ~500MB-1GB RAM at
    runtime.

If we ever swap to an API embedder, the only changes needed are inside
this module — the rest of the codebase only sees ``embed(text)`` →
``list[float]``.

What we embed:

The trainer embeds protocol-analysis JSON, NOT raw protocol text. The
JSON is dense, canonical, and already contains the signal that matters
(sponsor, intervention, indication, phase, CRF list, etc). Raw protocol
PDFs are mostly boilerplate plus signal, and the JSON is what the
pipeline produces from a protocol anyway. Embedding the JSON makes
ingest-time and query-time consistent.

The ``format_protocol_analysis_for_embedding`` helper takes a parsed
JSON dict and turns it into a stable, canonical text representation
that the embedder can consume. It's deterministic — same JSON in always
produces the same text out — which is essential because the same
embedding vector should result whether we're indexing OR retrieving.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

# Logger — same pattern as core/fingerprint.py and
# core/protocol_analysis_client.py.
try:
    import structlog

    logger = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    import logging

    _stdlogger = logging.getLogger(__name__)

    class _StdlibShimLogger:
        @staticmethod
        def _fmt(event: str, kw: dict[str, Any]) -> str:
            if not kw:
                return event
            tail = " ".join(f"{k}={v!r}" for k, v in kw.items())
            return f"{event} {tail}"

        def info(self, event: str, **kw: Any) -> None:
            _stdlogger.info(self._fmt(event, kw))

        def warning(self, event: str, **kw: Any) -> None:
            _stdlogger.warning(self._fmt(event, kw))

        def error(self, event: str, **kw: Any) -> None:
            _stdlogger.error(self._fmt(event, kw))

        def debug(self, event: str, **kw: Any) -> None:
            _stdlogger.debug(self._fmt(event, kw))

    logger = _StdlibShimLogger()


# ─── Pure helper: protocol-analysis JSON → embedding-friendly text ──


# Field ordering matters: same JSON produces same text. Keys we
# explicitly know about come first in this canonical order; unknown
# keys come last in alphabetical order.
_KNOWN_KEY_ORDER: tuple[str, ...] = (
    "study_name",
    "sponsor",
    "indication",
    "therapeutic_area",
    "phase",
    "study_type",
    "intervention",
    "interventions",
    "comparator",
    "primary_endpoint",
    "primary_endpoints",
    "secondary_endpoints",
    "patient_population",
    "inclusion_criteria",
    "exclusion_criteria",
    "study_design",
    "enrollment",
    "duration",
    "forms",
    "crfs",
    "schedule_of_assessments",
    "visits",
)


def format_protocol_analysis_for_embedding(
    analysis: dict[str, Any] | str,
) -> str:
    """
    Turn a protocol-analysis JSON dict into a canonical text string.

    Determinism is the key property — given the same input, this
    function returns the same output, byte for byte. That property
    is what makes ingest-time embeddings comparable to query-time
    embeddings of the same content.

    Args:
        analysis: Either a parsed dict, or a JSON string that we
            parse first. Strings that aren't valid JSON are returned
            wrapped with a header so the caller never gets surprised
            by an exception here — bad input still produces an
            embeddable text, just with low signal.

    Returns:
        A plain-text representation suitable for embedding.
    """
    if isinstance(analysis, str):
        try:
            data = json.loads(analysis)
        except (json.JSONDecodeError, ValueError):
            # Degraded path: treat the whole string as text.
            return f"protocol_analysis (unstructured)\n\n{analysis}"
    elif isinstance(analysis, dict):
        data = analysis
    else:
        return f"protocol_analysis (unsupported type: {type(analysis).__name__})"

    lines: list[str] = ["protocol_analysis"]

    # Render known keys in a stable order
    rendered: set[str] = set()
    for key in _KNOWN_KEY_ORDER:
        if key in data:
            value = data[key]
            lines.append(f"{key}: {_render_value(value)}")
            rendered.add(key)

    # Render any remaining keys alphabetically
    extra_keys = sorted(k for k in data.keys() if k not in rendered)
    for key in extra_keys:
        value = data[key]
        lines.append(f"{key}: {_render_value(value)}")

    return "\n".join(lines)


def _render_value(value: Any) -> str:
    """Stringify a JSON value in a stable, embedding-friendly way."""
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Trim & collapse whitespace to keep canonical form predictable.
        return " ".join(value.split())
    if isinstance(value, list):
        return _render_list(value)
    if isinstance(value, dict):
        return _render_dict(value)
    return str(value)


def _render_list(items: list[Any]) -> str:
    if not items:
        return "(empty)"
    rendered = [_render_value(it) for it in items]
    # Keep simple lists on one line, more complex ones broken up.
    if all(isinstance(it, (str, int, float, bool)) for it in items) and \
       sum(len(r) for r in rendered) < 200:
        return ", ".join(rendered)
    return "; ".join(rendered)


def _render_dict(d: dict[str, Any]) -> str:
    if not d:
        return "(empty)"
    parts = []
    for k in sorted(d.keys()):
        parts.append(f"{k}={_render_value(d[k])}")
    return "{ " + ", ".join(parts) + " }"


# ─── Embedder class ────────────────────────────────────────────────


class Embedder:
    """
    Wraps a sentence-transformers model with a small async API.

    Construction is cheap — the actual model is lazy-loaded on first
    use so that importing this module doesn't trigger the big
    download. Only the first ``embed()`` or ``embed_batch()`` call
    pays the load cost.

    For tests, you can construct with ``model_name=`` pointing at any
    available model, or inject a stub via ``encode_fn=`` to bypass
    sentence-transformers entirely.
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        *,
        encode_fn: Any = None,  # injectable for tests; should be Callable[[list[str]], list[list[float]]]
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._encode_fn = encode_fn
        self._model: Any = None
        self._dim: int | None = None

    # ── Lazy load + properties ────────────────────────────────────

    def _resolve_settings(self) -> tuple[str, str]:
        if self._model_name and self._device:
            return self._model_name, self._device
        from app.config import settings

        return (
            self._model_name or settings.embed_model_name,
            self._device or settings.embed_device,
        )

    def _ensure_loaded(self) -> None:
        if self._encode_fn is not None or self._model is not None:
            return

        from sentence_transformers import SentenceTransformer  # heavy import

        model_name, device = self._resolve_settings()
        logger.info("embedder.loading", model=model_name, device=device)
        self._model = SentenceTransformer(model_name, device=device)
        self._dim = self._model.get_sentence_embedding_dimension()
        logger.info("embedder.loaded", model=model_name, dim=self._dim)

    @property
    def dim(self) -> int:
        """The embedding dimension. Triggers a load if not already done."""
        if self._dim is not None:
            return self._dim
        if self._encode_fn is not None:
            # Probe the stub with a single known input to discover dim.
            sample = self._encode_fn(["dim_probe"])
            self._dim = len(sample[0])
            return self._dim
        self._ensure_loaded()
        assert self._dim is not None
        return self._dim

    # ── Public API ────────────────────────────────────────────────

    async def embed(self, text: str) -> list[float]:
        """Embed a single string → list of floats of length ``dim``."""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of strings.

        More efficient than calling ``embed`` in a loop because
        sentence-transformers internally batches the GPU/CPU work.
        """
        if not texts:
            return []

        # Stub path — used by tests
        if self._encode_fn is not None:
            return self._encode_fn(texts)

        # Real path — load model on first call, run encoding in
        # a worker thread so we don't block the event loop.
        self._ensure_loaded()

        def _encode_sync() -> list[list[float]]:
            # normalize_embeddings=True gives us unit-length vectors,
            # which means cosine similarity and dot product become
            # equivalent — and sqlite-vec's MATCH operator uses dot
            # product, so this is what we want.
            arr = self._model.encode(  # type: ignore[union-attr]
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return arr.tolist()

        return await asyncio.to_thread(_encode_sync)

    async def embed_protocol_analysis(
        self,
        analysis: dict[str, Any] | str,
    ) -> list[float]:
        """
        Convenience: format protocol-analysis JSON, then embed.

        This is the canonical way to produce an embedding for the
        corpus index OR for a query. Both ingest and retrieve should
        go through this same function so embeddings are comparable.
        """
        text = format_protocol_analysis_for_embedding(analysis)
        return await self.embed(text)
