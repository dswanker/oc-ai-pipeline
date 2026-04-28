"""
Tests for ``core.vector_store``.

These tests exercise both the metadata layer (validation, filter
allowlists, error paths) AND the vector layer (add → query
end-to-end). The vector tests REQUIRE ``sqlite-vec`` to be installed.
If it's not, those tests are skipped with a message.

Run as a script::

    python tests/test_vector_store.py

On the Mac (after ``pip install sqlite-vec``), all tests run. In
sandboxes without sqlite-vec, only the metadata-layer tests run.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

# Standalone-script support
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.vector_store import (
    IndexInput,
    RetrievedPair,
    VectorStore,
    _vec_to_blob,
)

# Skip vector-layer tests when sqlite-vec isn't installed.
try:
    import sqlite_vec  # noqa: F401
    SQLITE_VEC_AVAILABLE = True
except ImportError:
    SQLITE_VEC_AVAILABLE = False


# ─── _vec_to_blob — pure function, always testable ─────────────────


def test_vec_to_blob_returns_bytes() -> None:
    blob = _vec_to_blob([1.0, 2.0, 3.0])
    assert isinstance(blob, bytes)


def test_vec_to_blob_correct_length_for_f32() -> None:
    """4 bytes per float, per sqlite-vec's f32 expectation."""
    blob = _vec_to_blob([1.0, 2.0, 3.0, 4.0])
    assert len(blob) == 16  # 4 floats × 4 bytes


def test_vec_to_blob_roundtrips_via_struct() -> None:
    """Confirm we serialize as little-endian f32, and that the bytes
    can be re-read as floats."""
    import struct

    original = [1.5, -2.25, 3.125, 0.0]
    blob = _vec_to_blob(original)
    recovered = list(struct.unpack(f"{len(original)}f", blob))
    # f32 is lossy but for these clean fractions it should be exact
    assert recovered == original


# ─── RetrievedPair / IndexInput — sanity ───────────────────────────


def test_retrieved_pair_builds_with_minimum_fields() -> None:
    p = RetrievedPair(pair_hash="x", similarity=0.9)
    assert p.pair_hash == "x"
    assert p.similarity == 0.9
    assert p.has_protocol is False  # default
    assert p.sponsor is None  # default


def test_index_input_dataclass_holds_full_payload() -> None:
    item = IndexInput(
        pair_hash="abc123",
        embedding=[0.1, 0.2, 0.3, 0.4],
        monday_item_id=99,
        sponsor="Acme",
        indication="cancer",
        phase="2",
        therapeutic_area="oncology",
        nct_id=None,
        has_protocol=True,
        form_design_path="/cache/abc.xml",
        protocol_path="/cache/abc.pdf",
        fingerprint_json='{"sponsor":"Acme"}',
    )
    assert item.pair_hash == "abc123"
    assert item.has_protocol is True


# ─── Validation — runs without sqlite-vec ──────────────────────────


def test_query_rejects_unknown_filter_keys() -> None:
    """Unknown filter keys should raise loudly to surface typos."""
    store = VectorStore(db_path=":memory:", vec_dim=4)
    try:
        asyncio.run(store.query(
            [0.1, 0.2, 0.3, 0.4],
            k=5,
            filters={"sponsor": "Acme", "totally_made_up_column": "x"},
        ))
    except ValueError as exc:
        msg = str(exc)
        assert "totally_made_up_column" in msg
        assert "Unknown filter columns" in msg
    else:
        raise AssertionError("Expected ValueError on unknown filter key")


def test_query_rejects_dim_mismatch() -> None:
    """Query vec must have the same dim the store was configured for."""
    store = VectorStore(db_path=":memory:", vec_dim=4)
    try:
        asyncio.run(store.query([0.1, 0.2, 0.3], k=5))  # wrong dim
    except ValueError as exc:
        assert "3 dims" in str(exc)
        assert "expects 4" in str(exc)
    else:
        raise AssertionError("Expected ValueError on dim mismatch")


def test_query_rejects_when_dim_unset() -> None:
    """Vector ops should fail loudly if vec_dim wasn't configured."""
    store = VectorStore(db_path=":memory:", vec_dim=None)
    try:
        asyncio.run(store.query([0.1, 0.2], k=5))
    except RuntimeError as exc:
        assert "vec_dim" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when vec_dim is None")


def test_add_rejects_dim_mismatch() -> None:
    store = VectorStore(db_path=":memory:", vec_dim=4)
    bad_input = IndexInput(
        pair_hash="x",
        embedding=[0.1, 0.2, 0.3],  # wrong dim
        monday_item_id=None, sponsor=None, indication=None,
        phase=None, therapeutic_area=None, nct_id=None,
        has_protocol=False, form_design_path="/f", protocol_path=None,
        fingerprint_json="{}",
    )
    try:
        asyncio.run(store.add(bad_input))
    except ValueError as exc:
        assert "3 dims" in str(exc) or "expects 4" in str(exc)
    else:
        raise AssertionError("Expected ValueError on dim mismatch")


# ─── End-to-end — requires sqlite-vec ──────────────────────────────


def test_open_and_close_creates_schema() -> None:
    if not SQLITE_VEC_AVAILABLE:
        print("    (skipping: sqlite-vec not installed)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "test.db")
        store = VectorStore(db_path=path, vec_dim=4)
        # First op opens the connection
        asyncio.run(store.stats())
        store.close()
        # File should now exist
        assert Path(path).exists()


def test_add_then_stats_increments() -> None:
    if not SQLITE_VEC_AVAILABLE:
        print("    (skipping: sqlite-vec not installed)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "test.db")
        store = VectorStore(db_path=path, vec_dim=4)
        try:
            stats0 = asyncio.run(store.stats())
            assert stats0["total_pairs"] == 0

            asyncio.run(store.add(IndexInput(
                pair_hash="p1",
                embedding=[0.1, 0.2, 0.3, 0.4],
                monday_item_id=1, sponsor="Acme", indication="cancer",
                phase="2", therapeutic_area="oncology", nct_id=None,
                has_protocol=True, form_design_path="/f1", protocol_path="/p1",
                fingerprint_json='{"sponsor":"Acme"}',
            )))

            stats1 = asyncio.run(store.stats())
            assert stats1["total_pairs"] == 1
            assert stats1["pairs_with_protocol"] == 1
            assert stats1["distinct_sponsors"] == 1
        finally:
            store.close()


def test_add_is_idempotent_on_pair_hash() -> None:
    """Re-ingesting the same pair_hash should overwrite, not duplicate."""
    if not SQLITE_VEC_AVAILABLE:
        print("    (skipping: sqlite-vec not installed)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "test.db")
        store = VectorStore(db_path=path, vec_dim=4)
        try:
            base = dict(
                pair_hash="same_hash",
                embedding=[0.1, 0.2, 0.3, 0.4],
                monday_item_id=1, sponsor="Acme", indication="cancer",
                phase="2", therapeutic_area="oncology", nct_id=None,
                has_protocol=False, form_design_path="/f1",
                protocol_path=None, fingerprint_json="{}",
            )
            asyncio.run(store.add(IndexInput(**base)))

            # Second add with new sponsor — should update, not duplicate
            base["sponsor"] = "Beta Corp"
            asyncio.run(store.add(IndexInput(**base)))

            stats = asyncio.run(store.stats())
            assert stats["total_pairs"] == 1  # still just one
        finally:
            store.close()


def test_query_returns_top_k_in_similarity_order() -> None:
    """Three vectors, query close to one of them — that one ranks first."""
    if not SQLITE_VEC_AVAILABLE:
        print("    (skipping: sqlite-vec not installed)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "test.db")
        store = VectorStore(db_path=path, vec_dim=4)
        try:
            # Three normalized 4-d vectors well-separated in space.
            # Vector 1 lives near (1,0,0,0); we'll query near it.
            v1 = [1.0, 0.0, 0.0, 0.0]
            v2 = [0.0, 1.0, 0.0, 0.0]
            v3 = [0.0, 0.0, 1.0, 0.0]

            for tag, vec in [("p1", v1), ("p2", v2), ("p3", v3)]:
                asyncio.run(store.add(IndexInput(
                    pair_hash=tag,
                    embedding=vec,
                    monday_item_id=None, sponsor=None, indication=None,
                    phase=None, therapeutic_area=None, nct_id=None,
                    has_protocol=False, form_design_path=f"/f/{tag}",
                    protocol_path=None, fingerprint_json="{}",
                )))

            # Query close to v1
            results = asyncio.run(store.query(v1, k=3))
            assert len(results) == 3
            # Top result should be p1
            assert results[0].pair_hash == "p1"
            # Similarity to itself should be ~1.0
            assert results[0].similarity > 0.99
        finally:
            store.close()


def test_query_with_filter_returns_only_matching() -> None:
    """Filter by sponsor — should restrict results."""
    if not SQLITE_VEC_AVAILABLE:
        print("    (skipping: sqlite-vec not installed)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "test.db")
        store = VectorStore(db_path=path, vec_dim=4)
        try:
            # Two pairs, different sponsors
            asyncio.run(store.add(IndexInput(
                pair_hash="p1", embedding=[1.0, 0.0, 0.0, 0.0],
                monday_item_id=None, sponsor="Acme", indication=None,
                phase=None, therapeutic_area=None, nct_id=None,
                has_protocol=False, form_design_path="/f1",
                protocol_path=None, fingerprint_json="{}",
            )))
            asyncio.run(store.add(IndexInput(
                pair_hash="p2", embedding=[0.99, 0.05, 0.05, 0.0],
                monday_item_id=None, sponsor="Beta Corp", indication=None,
                phase=None, therapeutic_area=None, nct_id=None,
                has_protocol=False, form_design_path="/f2",
                protocol_path=None, fingerprint_json="{}",
            )))

            # Query close to both, but filter to sponsor=Acme
            results = asyncio.run(store.query(
                [1.0, 0.0, 0.0, 0.0],
                k=10,
                filters={"sponsor": "Acme"},
            ))
            assert len(results) == 1
            assert results[0].pair_hash == "p1"
            assert results[0].sponsor == "Acme"
        finally:
            store.close()


def test_query_with_has_protocol_filter() -> None:
    if not SQLITE_VEC_AVAILABLE:
        print("    (skipping: sqlite-vec not installed)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "test.db")
        store = VectorStore(db_path=path, vec_dim=4)
        try:
            asyncio.run(store.add(IndexInput(
                pair_hash="with_p", embedding=[1.0, 0.0, 0.0, 0.0],
                monday_item_id=None, sponsor=None, indication=None,
                phase=None, therapeutic_area=None, nct_id=None,
                has_protocol=True, form_design_path="/f1",
                protocol_path="/p1", fingerprint_json="{}",
            )))
            asyncio.run(store.add(IndexInput(
                pair_hash="without_p", embedding=[0.95, 0.0, 0.0, 0.0],
                monday_item_id=None, sponsor=None, indication=None,
                phase=None, therapeutic_area=None, nct_id=None,
                has_protocol=False, form_design_path="/f2",
                protocol_path=None, fingerprint_json="{}",
            )))

            results_with = asyncio.run(store.query(
                [1.0, 0.0, 0.0, 0.0], k=10, filters={"has_protocol": True}
            ))
            results_without = asyncio.run(store.query(
                [1.0, 0.0, 0.0, 0.0], k=10, filters={"has_protocol": False}
            ))
            assert len(results_with) == 1 and results_with[0].pair_hash == "with_p"
            assert len(results_without) == 1 and results_without[0].pair_hash == "without_p"
        finally:
            store.close()


def test_persistence_across_close_reopen() -> None:
    """Insert, close, reopen, stats should still see the row."""
    if not SQLITE_VEC_AVAILABLE:
        print("    (skipping: sqlite-vec not installed)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "test.db")

        store1 = VectorStore(db_path=path, vec_dim=4)
        asyncio.run(store1.add(IndexInput(
            pair_hash="persisted", embedding=[1.0, 0.0, 0.0, 0.0],
            monday_item_id=None, sponsor="X", indication=None,
            phase=None, therapeutic_area=None, nct_id=None,
            has_protocol=False, form_design_path="/f",
            protocol_path=None, fingerprint_json="{}",
        )))
        store1.close()

        store2 = VectorStore(db_path=path, vec_dim=4)
        try:
            stats = asyncio.run(store2.stats())
            assert stats["total_pairs"] == 1
            results = asyncio.run(store2.query([1.0, 0.0, 0.0, 0.0], k=1))
            assert results[0].pair_hash == "persisted"
        finally:
            store2.close()


# ─── Script runner ─────────────────────────────────────────────────


if __name__ == "__main__":
    import traceback

    if not SQLITE_VEC_AVAILABLE:
        print("NOTE: sqlite-vec is not installed in this environment.")
        print("      Vector-layer tests will be SKIPPED. Pure-function and")
        print("      validation tests will still run. To run the full suite:")
        print("          pip install sqlite-vec")
        print()

    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    failed: list[tuple[str, str]] = []
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception:  # noqa: BLE001
            failed.append((t.__name__, traceback.format_exc()))
            print(f"  FAIL  {t.__name__}")

    print()
    print(f"Ran {len(tests)} tests, {len(failed)} failures.")
    for name, tb in failed:
        print()
        print(f"── {name} ──")
        print(tb)
    sys.exit(1 if failed else 0)
