"""
Tests for ``core.embed``.

Two layers:

1. **Pure formatter** — ``format_protocol_analysis_for_embedding`` and
   helpers. Deterministic; fully tested here.
2. **Embedder class** — tested via the ``encode_fn=`` injection point
   so we don't have to download a 1.3 GB model in CI. The end-to-end
   sentence-transformers path gets exercised by the on-Mac integration
   test once the user installs the dep.

Run as a script::

    python tests/test_embedder.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Standalone-script support
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.embed import (
    Embedder,
    _render_dict,
    _render_list,
    _render_value,
    format_protocol_analysis_for_embedding,
)


# ─── format_protocol_analysis_for_embedding — determinism ─────────


def test_format_is_deterministic_for_same_input() -> None:
    """The most important property of all: same JSON in → same string out."""
    data = {"sponsor": "Acme", "phase": "2", "indication": "cancer"}
    a = format_protocol_analysis_for_embedding(data)
    b = format_protocol_analysis_for_embedding(data)
    assert a == b


def test_format_is_independent_of_input_key_order() -> None:
    """A dict with the same content but different key order produces
    the same output. Python dicts are insertion-ordered since 3.7;
    this matters for canonicalization."""
    a = format_protocol_analysis_for_embedding(
        {"sponsor": "Acme", "phase": "2", "indication": "cancer"}
    )
    b = format_protocol_analysis_for_embedding(
        {"indication": "cancer", "phase": "2", "sponsor": "Acme"}
    )
    assert a == b


def test_format_known_keys_ordered_canonically() -> None:
    """Known keys appear in the canonical order, regardless of input."""
    out = format_protocol_analysis_for_embedding({
        "phase": "2",
        "sponsor": "Acme",
        "study_name": "Study X",
    })
    lines = out.splitlines()
    # study_name must appear before sponsor, sponsor before phase
    sn = next(i for i, l in enumerate(lines) if l.startswith("study_name:"))
    sp = next(i for i, l in enumerate(lines) if l.startswith("sponsor:"))
    ph = next(i for i, l in enumerate(lines) if l.startswith("phase:"))
    assert sn < sp < ph


def test_format_unknown_keys_appear_alphabetically_at_end() -> None:
    out = format_protocol_analysis_for_embedding({
        "z_custom": "z",
        "a_custom": "a",
        "sponsor": "Acme",
    })
    lines = out.splitlines()
    sp = next(i for i, l in enumerate(lines) if l.startswith("sponsor:"))
    a_idx = next(i for i, l in enumerate(lines) if l.startswith("a_custom:"))
    z_idx = next(i for i, l in enumerate(lines) if l.startswith("z_custom:"))
    # known key first, then unknowns alphabetically
    assert sp < a_idx < z_idx


# ─── format — input shapes ─────────────────────────────────────────


def test_format_starts_with_header() -> None:
    out = format_protocol_analysis_for_embedding({"sponsor": "Acme"})
    assert out.startswith("protocol_analysis\n")


def test_format_accepts_string_json() -> None:
    s = json.dumps({"sponsor": "Acme", "phase": "2"})
    out = format_protocol_analysis_for_embedding(s)
    assert "sponsor: Acme" in out
    assert "phase: 2" in out


def test_format_handles_invalid_json_string_gracefully() -> None:
    """Bad JSON → degraded path, not an exception."""
    out = format_protocol_analysis_for_embedding("not json {{{")
    assert "protocol_analysis (unstructured)" in out
    assert "not json {{{" in out


def test_format_handles_unsupported_type_gracefully() -> None:
    out = format_protocol_analysis_for_embedding(42)  # type: ignore[arg-type]
    assert "unsupported type" in out


def test_format_handles_empty_dict() -> None:
    out = format_protocol_analysis_for_embedding({})
    assert out == "protocol_analysis"


# ─── format — value rendering ──────────────────────────────────────


def test_render_value_none() -> None:
    assert _render_value(None) == "(none)"


def test_render_value_booleans_become_yes_no() -> None:
    assert _render_value(True) == "yes"
    assert _render_value(False) == "no"


def test_render_value_numbers() -> None:
    assert _render_value(42) == "42"
    assert _render_value(3.14) == "3.14"


def test_render_value_collapses_whitespace_in_strings() -> None:
    assert _render_value("  hello   world\n  ") == "hello world"


def test_render_list_simple_one_line() -> None:
    assert _render_list(["a", "b", "c"]) == "a, b, c"


def test_render_list_empty() -> None:
    assert _render_list([]) == "(empty)"


def test_render_list_long_breaks_to_semicolons() -> None:
    """Long simple lists shouldn't render as a giant comma soup."""
    long_list = ["item " + str(i) * 10 for i in range(30)]
    rendered = _render_list(long_list)
    assert "; " in rendered


def test_render_list_with_dicts_uses_semicolons() -> None:
    rendered = _render_list([{"name": "X"}, {"name": "Y"}])
    # Complex items should be separated with semicolons
    assert "; " in rendered


def test_render_dict_keys_sorted() -> None:
    out = _render_dict({"z": 1, "a": 2, "m": 3})
    # Order must be a, m, z regardless of input order
    assert out.index("a=") < out.index("m=") < out.index("z=")


def test_render_dict_empty() -> None:
    assert _render_dict({}) == "(empty)"


# ─── format — full PrTK05-shaped example ───────────────────────────


def test_format_full_example_resembles_what_pipeline_produces() -> None:
    """Smoke test against a realistic PrTK05-shaped payload."""
    data = {
        "sponsor": "Candel Therapeutics, Inc.",
        "intervention": ["aglatimagene besadenovec", "valacyclovir", "EBRT"],
        "indication": "intermediate-risk prostate cancer",
        "phase": "2",
        "study_type": "interventional",
        "therapeutic_area": "oncology",
        "study_name": "PrTK05",
        "forms": [
            {"oid": "F_DM", "name": "Demographics"},
            {"oid": "F_AE", "name": "Adverse Events"},
        ],
    }
    out = format_protocol_analysis_for_embedding(data)
    assert "study_name: PrTK05" in out
    assert "sponsor: Candel Therapeutics, Inc." in out
    assert "phase: 2" in out
    assert "therapeutic_area: oncology" in out
    # Forms list — should appear
    assert "forms:" in out
    assert "F_DM" in out


# ─── Embedder — via stub ───────────────────────────────────────────


def _make_stub_embedder(dim: int = 4):
    """Returns an Embedder with a deterministic stub encode function."""

    def stub_encode(texts: list[str]) -> list[list[float]]:
        # Vectors that are simple but distinguishable: hash modulo
        # the dim, scaled. Two identical texts produce identical
        # vectors; different texts produce different vectors.
        out = []
        for t in texts:
            seed = hash(t) % 1_000_000
            vec = [(seed + i) % 7 / 7.0 for i in range(dim)]
            out.append(vec)
        return out

    return Embedder(encode_fn=stub_encode)


def test_embed_returns_vector_of_correct_dim() -> None:
    emb = _make_stub_embedder(dim=8)
    vec = asyncio.run(emb.embed("hello"))
    assert len(vec) == 8


def test_embed_batch_returns_list_per_input() -> None:
    emb = _make_stub_embedder(dim=4)
    vecs = asyncio.run(emb.embed_batch(["a", "b", "c"]))
    assert len(vecs) == 3
    assert all(len(v) == 4 for v in vecs)


def test_embed_batch_empty_input_returns_empty_list() -> None:
    emb = _make_stub_embedder(dim=4)
    assert asyncio.run(emb.embed_batch([])) == []


def test_embed_same_input_same_output() -> None:
    """Determinism check at the embedder layer."""
    emb = _make_stub_embedder(dim=4)
    a = asyncio.run(emb.embed("hello"))
    b = asyncio.run(emb.embed("hello"))
    assert a == b


def test_embedder_dim_property() -> None:
    emb = _make_stub_embedder(dim=8)
    assert emb.dim == 8


def test_embed_protocol_analysis_combines_format_and_embed() -> None:
    """The convenience helper is just format + embed; verify it
    produces the same result as doing the two steps manually."""
    emb = _make_stub_embedder(dim=4)
    data = {"sponsor": "Acme", "phase": "2"}
    direct = asyncio.run(
        emb.embed(format_protocol_analysis_for_embedding(data))
    )
    combined = asyncio.run(emb.embed_protocol_analysis(data))
    assert direct == combined


# ─── Script runner ─────────────────────────────────────────────────


if __name__ == "__main__":
    import traceback

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
