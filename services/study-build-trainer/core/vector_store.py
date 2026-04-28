"""
Vector store wrapper, backed by SQLite + ``sqlite-vec``.

Why this stack:

* Zero infrastructure. One file on disk per environment.
* Easily handles ~1-10 K vectors. We expect 100-200 in the bolus load,
  growing slowly. Overkill to run a dedicated vector DB.
* Schema is regular SQL, which means metadata filtering is just
  ``WHERE`` clauses — easier than learning a vector DB's filter syntax.
* Easy to back up (just copy the file) and easy to inspect (regular
  ``sqlite3`` CLI).

Schema:

    pairs(
        id                INTEGER PRIMARY KEY,
        pair_hash         TEXT UNIQUE,           -- stable identifier for an indexed pair
        monday_item_id    INTEGER,               -- traceability back to the corpus board
        sponsor           TEXT,
        indication        TEXT,
        phase             TEXT,
        therapeutic_area  TEXT,
        nct_id            TEXT,                  -- reserved for future CT.gov work
        has_protocol      INTEGER,               -- 0/1 boolean
        indexed_at        TEXT,                  -- ISO8601
        form_design_path  TEXT,
        protocol_path     TEXT,
        fingerprint_json  TEXT                   -- full StudyFingerprint as JSON, for debugging
    )

    vec_pairs(rowid INTEGER PRIMARY KEY, embedding FLOAT[N])
        -- managed by sqlite-vec; rowid maps 1:1 to pairs.id

When the embedding model is swapped (different vector dim), the
``vec_pairs`` virtual table needs to be rebuilt. ``rebuild_index``
handles this — it re-embeds every pair using the cached form/protocol
files.

Concurrency:

For Phase 1 the trainer service is a single FastAPI process behind a
single Railway container, so we use one persistent sqlite connection
serialized by an asyncio lock. If we ever scale horizontally, the
right move is to switch to pgvector or Qdrant rather than try to make
sqlite multi-writer.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

# Logger — same shim pattern.
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


# Allowlist of metadata columns that callers can filter on. We only
# accept these in WHERE clauses to prevent SQL injection via filter
# keys (values are always parameterized).
_FILTERABLE_COLUMNS: frozenset[str] = frozenset({
    "sponsor",
    "indication",
    "phase",
    "therapeutic_area",
    "nct_id",
    "has_protocol",
    "monday_item_id",
})


# ─── Public types ──────────────────────────────────────────────────


@dataclass
class RetrievedPair:
    """One result from a retrieval query."""

    pair_hash: str
    similarity: float  # 0.0–1.0; higher is more similar
    sponsor: str | None = None
    indication: str | None = None
    phase: str | None = None
    therapeutic_area: str | None = None
    nct_id: str | None = None
    has_protocol: bool = False
    monday_item_id: int | None = None
    form_design_path: str | None = None
    """Local path to the cached form-design file. Pipeline reads from
    here when constructing few-shot examples."""
    protocol_path: str | None = None
    indexed_at: str | None = None


@dataclass
class IndexInput:
    """Payload for adding (or upserting) one pair."""

    pair_hash: str
    embedding: list[float]
    monday_item_id: int | None
    sponsor: str | None
    indication: str | None
    phase: str | None
    therapeutic_area: str | None
    nct_id: str | None
    has_protocol: bool
    form_design_path: str
    protocol_path: str | None
    fingerprint_json: str


# ─── The store ─────────────────────────────────────────────────────


class VectorStore:
    """
    SQLite + sqlite-vec backed corpus index.

    Lifecycle:
      * Construction is cheap — defers all DB work to first use.
      * ``_ensure_open`` opens the connection, loads the sqlite-vec
        extension, and runs schema migrations (idempotent).
      * ``close`` releases resources (used by tests).

    ``vec_dim`` is required when you first build the schema. It comes
    from the embedder's ``dim`` property. Once chosen, every embedding
    you store must have that dimension. To change dim, rebuild the
    index from the cached files.
    """

    def __init__(
        self,
        db_path: str | None = None,
        vec_dim: int | None = None,
    ) -> None:
        self._db_path_override = db_path
        self._vec_dim = vec_dim
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ────────────────────────────────────────────────

    def _resolve_db_path(self) -> str:
        if self._db_path_override:
            return self._db_path_override
        from app.config import settings

        return settings.vector_db_path

    def _ensure_open(self) -> None:
        if self._conn is not None:
            return

        # Lazy import — sqlite-vec isn't installed in environments
        # that only run the unit tests for the metadata layer.
        import sqlite_vec

        db_path = self._resolve_db_path()
        # Make parent dir if needed (for relative ./corpus/embeddings.db)
        from pathlib import Path

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False is safe here because all access goes
        # through self._lock (an asyncio.Lock), which serializes
        # operations. Without this flag, sqlite3 would refuse to let
        # asyncio.to_thread run our SQL in a worker thread (different
        # thread than the one that opened the connection).
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        self._conn = conn
        self._run_migrations()
        logger.info("vector_store.opened", path=db_path, dim=self._vec_dim)

    def _run_migrations(self) -> None:
        """Idempotent schema setup."""
        assert self._conn is not None
        cur = self._conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS pairs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                pair_hash         TEXT UNIQUE NOT NULL,
                monday_item_id    INTEGER,
                sponsor           TEXT,
                indication        TEXT,
                phase             TEXT,
                therapeutic_area  TEXT,
                nct_id            TEXT,
                has_protocol      INTEGER NOT NULL DEFAULT 0,
                indexed_at        TEXT,
                form_design_path  TEXT,
                protocol_path     TEXT,
                fingerprint_json  TEXT
            )
        """)

        # Helpful indexes for the metadata filters callers will use.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pairs_sponsor ON pairs(sponsor)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pairs_phase ON pairs(phase)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pairs_ta ON pairs(therapeutic_area)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pairs_indication ON pairs(indication)")

        # Vector virtual table — only create if vec_dim has been declared.
        # Tests that don't use vector ops can skip this entirely.
        if self._vec_dim is not None:
            cur.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_pairs USING vec0(
                    embedding FLOAT[{self._vec_dim}]
                )
            """)

        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Add / upsert ──────────────────────────────────────────────

    async def add(self, item: IndexInput) -> None:
        """Insert or replace a pair (keyed by ``pair_hash``)."""
        if self._vec_dim is None:
            raise RuntimeError(
                "VectorStore was constructed without vec_dim — call "
                "configure(dim=...) or pass vec_dim to the constructor "
                "before calling add()."
            )
        if len(item.embedding) != self._vec_dim:
            raise ValueError(
                f"Embedding has {len(item.embedding)} dims; store "
                f"expects {self._vec_dim}."
            )

        async with self._lock:
            await asyncio.to_thread(self._add_sync, item)

    def _add_sync(self, item: IndexInput) -> None:
        self._ensure_open()
        assert self._conn is not None

        indexed_at = datetime.now(UTC).isoformat()

        # First the metadata row, in pairs. INSERT OR REPLACE
        # keyed on pair_hash so re-ingesting is idempotent.
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO pairs (
                pair_hash, monday_item_id, sponsor, indication, phase,
                therapeutic_area, nct_id, has_protocol, indexed_at,
                form_design_path, protocol_path, fingerprint_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pair_hash) DO UPDATE SET
                monday_item_id = excluded.monday_item_id,
                sponsor = excluded.sponsor,
                indication = excluded.indication,
                phase = excluded.phase,
                therapeutic_area = excluded.therapeutic_area,
                nct_id = excluded.nct_id,
                has_protocol = excluded.has_protocol,
                indexed_at = excluded.indexed_at,
                form_design_path = excluded.form_design_path,
                protocol_path = excluded.protocol_path,
                fingerprint_json = excluded.fingerprint_json
            """,
            (
                item.pair_hash,
                item.monday_item_id,
                item.sponsor,
                item.indication,
                item.phase,
                item.therapeutic_area,
                item.nct_id,
                1 if item.has_protocol else 0,
                indexed_at,
                item.form_design_path,
                item.protocol_path,
                item.fingerprint_json,
            ),
        )

        # Look up the rowid for this pair, then upsert the embedding.
        # vec0 virtual tables don't support ON CONFLICT, so we delete
        # then insert.
        row = cur.execute(
            "SELECT id FROM pairs WHERE pair_hash = ?", (item.pair_hash,)
        ).fetchone()
        rowid = row["id"]

        cur.execute("DELETE FROM vec_pairs WHERE rowid = ?", (rowid,))
        cur.execute(
            "INSERT INTO vec_pairs (rowid, embedding) VALUES (?, ?)",
            (rowid, _vec_to_blob(item.embedding)),
        )

        self._conn.commit()
        logger.info(
            "vector_store.added",
            pair_hash=item.pair_hash,
            rowid=rowid,
            sponsor=item.sponsor,
        )

    # ── Query ─────────────────────────────────────────────────────

    async def query(
        self,
        query_vec: list[float],
        k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedPair]:
        """
        Top-k similarity search with optional metadata filters.

        Filters are simple equality predicates on indexed columns.
        Example::

            await store.query(vec, k=5,
                              filters={"therapeutic_area": "oncology",
                                       "phase": "2"})

        Unknown keys in ``filters`` raise ValueError to surface typos
        loudly rather than silently match nothing.
        """
        if self._vec_dim is None:
            raise RuntimeError("VectorStore not configured with vec_dim")
        if len(query_vec) != self._vec_dim:
            raise ValueError(
                f"Query vec has {len(query_vec)} dims; store expects "
                f"{self._vec_dim}."
            )

        # Validate filter keys before touching the DB
        if filters:
            unknown = set(filters.keys()) - _FILTERABLE_COLUMNS
            if unknown:
                raise ValueError(
                    f"Unknown filter columns: {sorted(unknown)}. "
                    f"Allowed: {sorted(_FILTERABLE_COLUMNS)}"
                )

        async with self._lock:
            return await asyncio.to_thread(self._query_sync, query_vec, k, filters)

    def _query_sync(
        self,
        query_vec: list[float],
        k: int,
        filters: dict[str, Any] | None,
    ) -> list[RetrievedPair]:
        self._ensure_open()
        assert self._conn is not None

        # Build the SQL. sqlite-vec's vec0 virtual table requires the
        # KNN match to specify its `k` directly in the MATCH clause:
        #
        #     SELECT rowid, distance FROM vec_pairs
        #     WHERE embedding MATCH ? AND k = ?
        #
        # Additional filtering on metadata happens AFTER the KNN
        # selection. We wrap the vec query in a CTE and JOIN to pairs
        # in the outer query, then apply the metadata filters there.
        #
        # If filters are present, we over-fetch from vec0 (k * 5, capped
        # at 200) to give the post-KNN filter room to find the requested
        # number of matches. Caveat: if the corpus is dominated by
        # entries that don't match the filter, results may be
        # filter-starved. For Phase 1's expected corpus size (~100-200)
        # this is safe; revisit if we hit it.
        vec_k = k * 5 if filters else k
        if vec_k > 200:
            vec_k = 200

        sql_parts = [
            "WITH knn AS (",
            "  SELECT rowid, distance",
            "  FROM vec_pairs",
            "  WHERE embedding MATCH ? AND k = ?",
            ")",
            "SELECT p.*, knn.distance AS distance",
            "FROM knn",
            "JOIN pairs p ON p.id = knn.rowid",
        ]
        params: list[Any] = [_vec_to_blob(query_vec), vec_k]

        if filters:
            sql_parts.append("WHERE 1=1")
            for col, val in filters.items():
                # Already validated against allowlist above
                if col == "has_protocol":
                    sql_parts.append(f"AND p.{col} = ?")
                    params.append(1 if val else 0)
                else:
                    sql_parts.append(f"AND p.{col} = ?")
                    params.append(val)

        sql_parts.append("ORDER BY knn.distance ASC")
        sql_parts.append("LIMIT ?")
        params.append(k)

        sql = "\n".join(sql_parts)
        cur = self._conn.cursor()
        rows = cur.execute(sql, params).fetchall()

        results: list[RetrievedPair] = []
        for row in rows:
            # vec0 distance is L2 by default — for normalized vectors
            # similarity = 1 - (L2_distance² / 2). We expose that as
            # ``similarity`` so 1.0 means identical, 0.0 means
            # orthogonal, ~−1 means opposite.
            distance_l2 = float(row["distance"])
            similarity = max(-1.0, min(1.0, 1.0 - (distance_l2 * distance_l2) / 2.0))

            results.append(RetrievedPair(
                pair_hash=row["pair_hash"],
                similarity=similarity,
                sponsor=row["sponsor"],
                indication=row["indication"],
                phase=row["phase"],
                therapeutic_area=row["therapeutic_area"],
                nct_id=row["nct_id"],
                has_protocol=bool(row["has_protocol"]),
                monday_item_id=row["monday_item_id"],
                form_design_path=row["form_design_path"],
                protocol_path=row["protocol_path"],
                indexed_at=row["indexed_at"],
            ))

        logger.info("vector_store.query", k=k, returned=len(results),
                    filters=filters or {})
        return results

    # ── Stats ─────────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Corpus-level statistics."""
        async with self._lock:
            return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> dict[str, Any]:
        self._ensure_open()
        assert self._conn is not None
        cur = self._conn.cursor()

        total = cur.execute("SELECT COUNT(*) AS c FROM pairs").fetchone()["c"]
        with_proto = cur.execute(
            "SELECT COUNT(*) AS c FROM pairs WHERE has_protocol = 1"
        ).fetchone()["c"]
        without_proto = total - with_proto
        sponsors = cur.execute(
            "SELECT COUNT(DISTINCT sponsor) AS c FROM pairs WHERE sponsor IS NOT NULL"
        ).fetchone()["c"]
        latest = cur.execute(
            "SELECT MAX(indexed_at) AS m FROM pairs"
        ).fetchone()["m"]

        return {
            "total_pairs": total,
            "pairs_with_protocol": with_proto,
            "pairs_without_protocol": without_proto,
            "distinct_sponsors": sponsors,
            "indexed_at_latest": latest,
        }

    # ── Maintenance ───────────────────────────────────────────────

    async def rebuild_index(self, embedder: Any) -> None:
        """
        Rebuild ``vec_pairs`` from scratch by re-embedding every pair.

        Use cases:
          * Switched embedding models (different dim or different
            vector space).
          * Vector table corrupted; metadata is fine.
          * Tuning the format used by
            ``format_protocol_analysis_for_embedding``.

        Reads ``fingerprint_json`` for each pair (we stored the full
        analysis there at ingest), formats it the same way, embeds,
        and reinserts. Metadata in ``pairs`` is untouched.
        """
        # Lazy import to avoid circular dependency
        from core.embed import format_protocol_analysis_for_embedding

        async with self._lock:
            self._ensure_open()
            assert self._conn is not None

            cur = self._conn.cursor()
            rows = cur.execute(
                "SELECT id, fingerprint_json FROM pairs"
            ).fetchall()

            logger.info("vector_store.rebuild.start", count=len(rows))

            # Wipe and recreate the vec table so dim can change cleanly
            cur.execute("DROP TABLE IF EXISTS vec_pairs")
            self._conn.commit()
            self._vec_dim = embedder.dim
            self._run_migrations()

            for row in rows:
                rowid = row["id"]
                try:
                    analysis = json.loads(row["fingerprint_json"]) \
                        if row["fingerprint_json"] else {}
                except (json.JSONDecodeError, ValueError):
                    analysis = {}
                text = format_protocol_analysis_for_embedding(analysis)
                vec = await embedder.embed(text)

                cur.execute(
                    "INSERT INTO vec_pairs (rowid, embedding) VALUES (?, ?)",
                    (rowid, _vec_to_blob(vec)),
                )

            self._conn.commit()
            logger.info("vector_store.rebuild.done", count=len(rows),
                        dim=self._vec_dim)


# ─── helpers ───────────────────────────────────────────────────────


def _vec_to_blob(vec: list[float]) -> bytes:
    """Serialize a Python list[float] to the f32-LE bytes sqlite-vec wants."""
    import struct

    return struct.pack(f"{len(vec)}f", *vec)


# Re-export for convenience
__all__ = ["VectorStore", "RetrievedPair", "IndexInput"]
