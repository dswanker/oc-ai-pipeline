"""
Opt-in end-to-end check — embed + index + query against real models.

Purpose
-------
Index three different protocol-analysis JSONs into a fresh SQLite DB,
embed them with the real BAAI/bge-large-en-v1.5 model, then query
with one of the three and verify it ranks first. This is the
"the whole pipeline actually works" smoke test.

Why opt-in
----------
* Requires ``sentence-transformers`` installed (~1.3 GB model download
  on first run; ~500 MB-1 GB RAM at runtime).
* Requires ``sqlite-vec`` installed.
* Slow on first run (model download); fast after.

How to run
----------
1. Install deps if you haven't already::

     pip install sentence-transformers sqlite-vec

2. Run::

     python tests/integration_embedder_vector_store.py

The script uses an in-memory database so nothing persists between runs.

Expected output
---------------

  PrTK05-style query
  Top 3:
    1. prtk05         similarity=1.0000   ← exact match
    2. <oncology>     similarity=~0.7-0.8 ← same TA, different drug
    3. <cardio>       similarity=~0.4-0.5 ← unrelated

  Filtered query (sponsor=Acme):
    Returns only the Acme-sponsored entry.

  Stats:
    total_pairs=3
    distinct_sponsors=3
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.embed import Embedder, format_protocol_analysis_for_embedding
from core.vector_store import IndexInput, VectorStore


# Three deliberately different studies
PROTOCOLS: list[dict] = [
    {
        "_label": "prtk05",
        "study_name": "PrTK05",
        "sponsor": "Candel Therapeutics, Inc.",
        "intervention": ["aglatimagene besadenovec", "valacyclovir", "EBRT"],
        "indication": "intermediate-risk prostate cancer",
        "phase": "2",
        "therapeutic_area": "oncology",
        "study_type": "interventional",
    },
    {
        "_label": "oncology_lung",
        "study_name": "AstroBio-2024-007",
        "sponsor": "Acme Therapeutics",
        "intervention": ["pembrolizumab", "carboplatin"],
        "indication": "advanced non-small-cell lung cancer",
        "phase": "3",
        "therapeutic_area": "oncology",
        "study_type": "interventional",
    },
    {
        "_label": "cardio",
        "study_name": "BetaHeart-2024-001",
        "sponsor": "Beta Cardio Inc.",
        "intervention": ["metoprolol XR"],
        "indication": "hypertensive heart disease",
        "phase": "4",
        "therapeutic_area": "cardiology",
        "study_type": "interventional",
    },
]


async def main() -> None:
    print("Loading embedding model (this is slow on first run)...")
    embedder = Embedder()
    dim = embedder.dim
    print(f"  Model loaded. Dimension: {dim}")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        store = VectorStore(db_path=db_path, vec_dim=dim)

        print()
        print(f"Indexing {len(PROTOCOLS)} protocols into {db_path}...")
        for p in PROTOCOLS:
            label = p["_label"]
            payload = {k: v for k, v in p.items() if not k.startswith("_")}
            text = format_protocol_analysis_for_embedding(payload)
            vec = await embedder.embed(text)

            await store.add(IndexInput(
                pair_hash=label,
                embedding=vec,
                monday_item_id=None,
                sponsor=payload["sponsor"],
                indication=payload["indication"],
                phase=payload["phase"],
                therapeutic_area=payload["therapeutic_area"],
                nct_id=None,
                has_protocol=True,
                form_design_path=f"/cache/{label}.xml",
                protocol_path=f"/cache/{label}.pdf",
                fingerprint_json=json.dumps(payload),
            ))
            print(f"  Indexed: {label}")

        # ── Query 1: PrTK05 ─────────────────────────────────────
        print()
        print("─── QUERY 1: prtk05 ────────────────────────────────────────")
        prtk05_payload = {
            k: v for k, v in PROTOCOLS[0].items() if not k.startswith("_")
        }
        query_vec = await embedder.embed_protocol_analysis(prtk05_payload)
        results = await store.query(query_vec, k=3)
        for r in results:
            print(f"  {r.pair_hash:20s} similarity={r.similarity:.4f}  "
                  f"({r.therapeutic_area}, {r.sponsor})")

        if results[0].pair_hash == "prtk05":
            print("  ✓ Top match is the prtk05 entry — as expected")
        else:
            print(f"  ✗ Top match is {results[0].pair_hash}, expected prtk05")

        # ── Query 2: filter by sponsor ──────────────────────────
        print()
        print("─── QUERY 2: filter by sponsor=Acme Therapeutics ───────────")
        filtered = await store.query(
            query_vec, k=10,
            filters={"sponsor": "Acme Therapeutics"},
        )
        for r in filtered:
            print(f"  {r.pair_hash:20s} similarity={r.similarity:.4f}")
        if len(filtered) == 1 and filtered[0].pair_hash == "oncology_lung":
            print("  ✓ Filter narrowed correctly")
        else:
            print(f"  ✗ Expected only oncology_lung; got {[r.pair_hash for r in filtered]}")

        # ── Query 3: filter by therapeutic_area ─────────────────
        print()
        print("─── QUERY 3: filter by therapeutic_area=oncology ───────────")
        onc = await store.query(
            query_vec, k=10,
            filters={"therapeutic_area": "oncology"},
        )
        for r in onc:
            print(f"  {r.pair_hash:20s} similarity={r.similarity:.4f}")
        if len(onc) == 2 and {r.pair_hash for r in onc} == {"prtk05", "oncology_lung"}:
            print("  ✓ Both oncology entries returned, cardio excluded")
        else:
            print(f"  ✗ Expected prtk05 + oncology_lung; got {[r.pair_hash for r in onc]}")

        # ── Stats ───────────────────────────────────────────────
        print()
        print("─── CORPUS STATS ───────────────────────────────────────────")
        stats = await store.stats()
        for k, v in stats.items():
            print(f"  {k:25s} {v}")

        store.close()
        print()
        print("Done.")


if __name__ == "__main__":
    # Verify deps before doing anything heavy
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        print("ERROR: sentence-transformers not installed.")
        print("       Run: pip install sentence-transformers")
        sys.exit(2)
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        print("ERROR: sqlite-vec not installed.")
        print("       Run: pip install sqlite-vec")
        sys.exit(2)

    asyncio.run(main())
