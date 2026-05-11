"""
Tests for trainer_integration.py.

Standalone runner — no pytest required:
    python tests/test_trainer_integration.py

Covers:
  * format_examples_block — the most logic-rich function. Many cases.
  * retrieve_examples — HTTP success, HTTP errors, timeouts, network
    failures, malformed responses. All exercised via stubbed httpx.
  * run_protocol_analysis_quick — happy path and failure paths via
    stubbed call_claude/extract_json.
  * create_pending_row — SHA-256 compute-if-missing logic, caller-supplied
    preservation, empty-pdf short-circuit. Via stubbed httpx.
  * _normalize_sponsor — covers the equality-check edge cases.
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

from trainer_integration import (
    _normalize_sponsor,
    _trainer_url,
    create_pending_row,
    format_examples_block,
    retrieve_examples,
    run_protocol_analysis_quick,
)


# ─── Stub HTTP transport ─────────────────────────────────────────────────


class _StubResponse:
    """Mimics httpx.Response — only the fields we use."""

    def __init__(
        self,
        status_code: int = 200,
        json_body: dict | None = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.text = text if text is not None else json.dumps(self._json)

    def json(self) -> dict:
        return self._json


class _StubHttpClient:
    """Captures POST requests; returns canned responses or raises on demand."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.queued_responses: list[_StubResponse] = []
        # If set, the next post() call raises this exception instead of
        # returning a response. Allows simulating timeouts / network errors.
        self.raise_on_next: Exception | None = None
        self.is_closed = False

    def queue(self, response: _StubResponse) -> None:
        self.queued_responses.append(response)

    async def post(self, url: str, *, json: dict | None = None, files=None, data=None, **kw):
        self.posts.append({"url": url, "json": json, "files": files, "data": data})
        if self.raise_on_next is not None:
            err, self.raise_on_next = self.raise_on_next, None
            raise err
        if not self.queued_responses:
            return _StubResponse(json_body={"matches": []})
        return self.queued_responses.pop(0)

    async def aclose(self) -> None:
        self.is_closed = True


# ─── _normalize_sponsor ─────────────────────────────────────────────────


def test_normalize_sponsor_strips_whitespace_and_lowercases() -> None:
    assert _normalize_sponsor("  Acme  ") == "acme"
    assert _normalize_sponsor("ACME") == "acme"


def test_normalize_sponsor_strips_inc() -> None:
    assert _normalize_sponsor("Candel Therapeutics, Inc.") == "candel therapeutics"
    assert _normalize_sponsor("Acme Inc") == "acme"
    assert _normalize_sponsor("Acme inc.") == "acme"


def test_normalize_sponsor_strips_llc() -> None:
    assert _normalize_sponsor("Foo Bio LLC") == "foo bio"


def test_normalize_sponsor_strips_corp_and_ltd() -> None:
    assert _normalize_sponsor("Mega Corp.") == "mega"
    assert _normalize_sponsor("BioBig Ltd") == "biobig"


def test_normalize_sponsor_handles_empty_or_none() -> None:
    assert _normalize_sponsor(None) == ""
    assert _normalize_sponsor("") == ""
    assert _normalize_sponsor("   ") == ""


def test_normalize_sponsor_two_variants_compare_equal() -> None:
    """The whole point: two slightly different sponsor strings should normalize the same."""
    a = _normalize_sponsor("Candel Therapeutics, Inc.")
    b = _normalize_sponsor("candel therapeutics")
    assert a == b


# ─── _trainer_url ────────────────────────────────────────────────────────


def test_trainer_url_default() -> None:
    import os
    saved = os.environ.pop("TRAINER_URL", None)
    try:
        assert _trainer_url() == "http://localhost:8001"
    finally:
        if saved is not None:
            os.environ["TRAINER_URL"] = saved


def test_trainer_url_strips_trailing_slash() -> None:
    import os
    saved = os.environ.get("TRAINER_URL")
    try:
        os.environ["TRAINER_URL"] = "http://trainer.railway.internal:8001/  "
        assert _trainer_url() == "http://trainer.railway.internal:8001"
    finally:
        if saved is None:
            os.environ.pop("TRAINER_URL", None)
        else:
            os.environ["TRAINER_URL"] = saved


# ─── format_examples_block ──────────────────────────────────────────────


def _make_match(
    pair_hash: str = "row_1",
    similarity: float = 0.85,
    sponsor: str = "Acme Therapeutics",
    indication: str = "cancer",
    phase: str = "2",
    therapeutic_area: str = "oncology",
    form_design_path: str = "/data/corpus/files/row_1/form.odm.xml",
    protocol_path: str | None = "/data/corpus/files/row_1/protocol.pdf",
) -> dict:
    return {
        "pair_hash": pair_hash,
        "similarity": similarity,
        "sponsor": sponsor,
        "indication": indication,
        "phase": phase,
        "therapeutic_area": therapeutic_area,
        "form_design_path": form_design_path,
        "protocol_path": protocol_path,
    }


def test_format_empty_matches_returns_empty_string() -> None:
    assert format_examples_block([]) == ""


def test_format_single_match_includes_all_fields() -> None:
    m = _make_match(sponsor="Acme", indication="lung cancer", phase="3")
    out = format_examples_block([m])
    assert "EXAMPLE 1" in out
    assert "Sponsor: Acme" in out
    assert "Indication: lung cancer" in out
    assert "Phase: 3" in out
    assert "similarity 0.85" in out
    assert "/data/corpus/files/row_1/form.odm.xml" in out


def test_format_protocol_path_omitted_when_missing() -> None:
    m = _make_match(protocol_path=None)
    out = format_examples_block([m])
    assert "Protocol PDF" not in out


def test_format_numbers_examples_starting_at_1() -> None:
    matches = [
        _make_match(pair_hash="row_1", sponsor="A"),
        _make_match(pair_hash="row_2", sponsor="B"),
        _make_match(pair_hash="row_3", sponsor="C"),
    ]
    out = format_examples_block(matches)
    assert "EXAMPLE 1" in out
    assert "EXAMPLE 2" in out
    assert "EXAMPLE 3" in out
    # Order: A then B then C
    assert out.index("Sponsor: A") < out.index("Sponsor: B") < out.index("Sponsor: C")


def test_format_handles_missing_optional_fields() -> None:
    """Real data may be missing fields. Don't crash."""
    m = {"pair_hash": "row_1", "similarity": 0.5}
    out = format_examples_block([m])
    assert "EXAMPLE 1" in out
    assert "(sponsor unknown)" in out
    assert "(indication unknown)" in out


def test_format_handles_non_numeric_similarity() -> None:
    m = _make_match()
    m["similarity"] = "not a number"
    out = format_examples_block([m])
    assert "similarity ?" in out


# ─── format_examples_block — same-sponsor reservation ──────────────────


def test_reserve_moves_same_sponsor_to_slot_1() -> None:
    """Slot 1 gets reserved for the first same-sponsor match."""
    matches = [
        _make_match(pair_hash="row_a", sponsor="Other Co.", similarity=0.95),
        _make_match(pair_hash="row_b", sponsor="Acme Therapeutics", similarity=0.80),
        _make_match(pair_hash="row_c", sponsor="Third Co.", similarity=0.70),
    ]
    out = format_examples_block(matches, sponsor_hint="Acme Therapeutics")
    # row_b should appear before row_a in the output
    idx_b = out.index("row_b") if "row_b" in out else -1
    # We don't show pair_hash directly; check via sponsor placement
    a_pos = out.index("Sponsor: Other Co.")
    b_pos = out.index("Sponsor: Acme Therapeutics")
    assert b_pos < a_pos


def test_reserve_no_op_when_no_match_has_same_sponsor() -> None:
    """If no same-sponsor match exists, ordering is preserved."""
    matches = [
        _make_match(sponsor="X", similarity=0.9),
        _make_match(sponsor="Y", similarity=0.8),
    ]
    out = format_examples_block(matches, sponsor_hint="Different Sponsor")
    assert out.index("Sponsor: X") < out.index("Sponsor: Y")


def test_reserve_handles_corporate_suffix_variants() -> None:
    """Sponsor 'Acme Inc.' should match hint 'acme'."""
    matches = [
        _make_match(pair_hash="row_a", sponsor="Other Co.", similarity=0.95),
        _make_match(pair_hash="row_b", sponsor="Acme, Inc.", similarity=0.80),
    ]
    out = format_examples_block(matches, sponsor_hint="acme")
    assert out.index("Sponsor: Acme, Inc.") < out.index("Sponsor: Other Co.")


def test_reserve_only_moves_one_same_sponsor() -> None:
    """If multiple same-sponsor matches exist, only the FIRST moves to slot 1."""
    matches = [
        _make_match(pair_hash="row_a", sponsor="Other", similarity=0.95),
        _make_match(pair_hash="row_b", sponsor="Acme", similarity=0.80, indication="lung"),
        _make_match(pair_hash="row_c", sponsor="Acme", similarity=0.70, indication="breast"),
    ]
    out = format_examples_block(matches, sponsor_hint="Acme")
    # row_b moves to slot 1; row_c should NOT also move forward
    pos_b = out.index("Indication: lung")
    pos_a = out.index("Sponsor: Other")
    pos_c = out.index("Indication: breast")
    assert pos_b < pos_a < pos_c


def test_reserve_disabled_preserves_order() -> None:
    """With reserve_same_sponsor=False, no reordering."""
    matches = [
        _make_match(pair_hash="row_a", sponsor="Other", similarity=0.95),
        _make_match(pair_hash="row_b", sponsor="Acme", similarity=0.80),
    ]
    out = format_examples_block(matches, sponsor_hint="Acme",
                                reserve_same_sponsor=False)
    assert out.index("Sponsor: Other") < out.index("Sponsor: Acme")


def test_reserve_marks_same_sponsor_with_tag() -> None:
    """The same-sponsor row gets a visible (SAME SPONSOR) tag."""
    matches = [_make_match(sponsor="Acme")]
    out = format_examples_block(matches, sponsor_hint="Acme")
    assert "SAME SPONSOR" in out


def test_format_does_not_crash_on_empty_sponsor_hint() -> None:
    matches = [_make_match()]
    out = format_examples_block(matches, sponsor_hint=None)
    assert "EXAMPLE 1" in out
    assert "SAME SPONSOR" not in out  # no hint → no tagging


# ─── retrieve_examples — happy path ──────────────────────────────────────


def test_retrieve_returns_matches_on_success() -> None:
    stub = _StubHttpClient()
    stub.queue(_StubResponse(json_body={
        "matches": [
            {"pair_hash": "row_1", "similarity": 0.9, "sponsor": "Acme"},
            {"pair_hash": "row_2", "similarity": 0.7, "sponsor": "Beta"},
        ],
        "query_embedding_dim": 1024,
        "embedding_ms": 100.0,
        "search_ms": 5.0,
    }))
    out = asyncio.run(retrieve_examples(
        {"sponsor": "Acme", "phase": "2"},
        k=5,
        http_client=stub,
    ))
    assert len(out) == 2
    assert out[0]["pair_hash"] == "row_1"
    # Verify the request payload
    assert len(stub.posts) == 1
    posted = stub.posts[0]
    assert posted["url"].endswith("/retrieve")
    assert posted["json"]["analysis"] == {"sponsor": "Acme", "phase": "2"}
    assert posted["json"]["k"] == 5


def test_retrieve_empty_analysis_returns_empty() -> None:
    stub = _StubHttpClient()
    out = asyncio.run(retrieve_examples({}, http_client=stub))
    assert out == []
    # No HTTP call should have been made
    assert len(stub.posts) == 0


def test_retrieve_drops_matches_without_pair_hash() -> None:
    """Defensive — if the trainer ever returns malformed entries."""
    stub = _StubHttpClient()
    stub.queue(_StubResponse(json_body={
        "matches": [
            {"pair_hash": "row_1", "similarity": 0.9},
            {"similarity": 0.8},  # no pair_hash — should be dropped
            "not a dict",          # should be dropped
            {"pair_hash": "row_3", "similarity": 0.7},
        ],
    }))
    out = asyncio.run(retrieve_examples({"x": "y"}, http_client=stub))
    assert len(out) == 2
    assert {m["pair_hash"] for m in out} == {"row_1", "row_3"}


# ─── retrieve_examples — failure paths (graceful) ────────────────────────


def test_retrieve_returns_empty_on_connect_error() -> None:
    """Trainer not running → empty list, not exception."""
    try:
        import httpx
    except ImportError:
        print("    (skipping: httpx not installed)")
        return
    stub = _StubHttpClient()
    stub.raise_on_next = httpx.ConnectError("connection refused")
    out = asyncio.run(retrieve_examples({"x": "y"}, http_client=stub))
    assert out == []


def test_retrieve_returns_empty_on_timeout() -> None:
    try:
        import httpx
    except ImportError:
        print("    (skipping: httpx not installed)")
        return
    stub = _StubHttpClient()
    stub.raise_on_next = httpx.ReadTimeout("timed out")
    out = asyncio.run(retrieve_examples({"x": "y"}, http_client=stub))
    assert out == []


def test_retrieve_returns_empty_on_500() -> None:
    stub = _StubHttpClient()
    stub.queue(_StubResponse(status_code=500, text="Internal Server Error"))
    out = asyncio.run(retrieve_examples({"x": "y"}, http_client=stub))
    assert out == []


def test_retrieve_returns_empty_on_404() -> None:
    stub = _StubHttpClient()
    stub.queue(_StubResponse(status_code=404, text="Not Found"))
    out = asyncio.run(retrieve_examples({"x": "y"}, http_client=stub))
    assert out == []


def test_retrieve_returns_empty_when_response_missing_matches_key() -> None:
    stub = _StubHttpClient()
    stub.queue(_StubResponse(json_body={"some_other_field": []}))
    out = asyncio.run(retrieve_examples({"x": "y"}, http_client=stub))
    assert out == []


def test_retrieve_returns_empty_when_response_is_not_dict() -> None:
    stub = _StubHttpClient()
    stub.queue(_StubResponse(json_body=[1, 2, 3]))  # list, not dict
    out = asyncio.run(retrieve_examples({"x": "y"}, http_client=stub))
    assert out == []


# ─── run_protocol_analysis_quick — happy path ───────────────────────────


def test_quick_analysis_returns_parsed_dict() -> None:
    """Pre-step happy path: call_claude returns JSON, extract_json parses."""
    parsed = {"sponsor": "Acme", "phase": "2", "therapeutic_area": "oncology"}

    async def fake_call(prompt, *, pdf_bytes=None, **kw):
        assert pdf_bytes == b"%PDF-1.4 fake"
        return json.dumps(parsed)

    def fake_extract(text):
        return json.loads(text)

    out = asyncio.run(run_protocol_analysis_quick(
        b"%PDF-1.4 fake",
        call_claude_fn=fake_call,
        extract_json_fn=fake_extract,
    ))
    assert out == parsed


def test_quick_analysis_empty_pdf_returns_empty() -> None:
    """No PDF bytes → don't call Claude, just return {}."""

    async def fake_call(*a, **kw):
        raise AssertionError("Should not be called for empty PDF")

    out = asyncio.run(run_protocol_analysis_quick(
        b"",
        call_claude_fn=fake_call,
        extract_json_fn=lambda t: {},
    ))
    assert out == {}


def test_quick_analysis_returns_empty_on_call_claude_failure() -> None:
    """Any exception in call_claude → graceful empty dict."""

    async def fake_call(*a, **kw):
        raise RuntimeError("Anthropic API exploded")

    out = asyncio.run(run_protocol_analysis_quick(
        b"%PDF-1.4 fake",
        call_claude_fn=fake_call,
        extract_json_fn=lambda t: {},
    ))
    assert out == {}


def test_quick_analysis_returns_empty_on_unparseable_json() -> None:
    """Claude returns garbage → graceful empty dict."""

    async def fake_call(*a, **kw):
        return "this is not json at all"

    def fake_extract(text):
        raise ValueError("no JSON found")

    out = asyncio.run(run_protocol_analysis_quick(
        b"%PDF-1.4 fake",
        call_claude_fn=fake_call,
        extract_json_fn=fake_extract,
    ))
    assert out == {}


def test_quick_analysis_returns_empty_when_extract_returns_non_dict() -> None:
    """Defensive — what if extract_json returns a list?"""

    async def fake_call(*a, **kw):
        return "[1, 2, 3]"

    def fake_extract(text):
        return [1, 2, 3]

    out = asyncio.run(run_protocol_analysis_quick(
        b"%PDF-1.4 fake",
        call_claude_fn=fake_call,
        extract_json_fn=fake_extract,
    ))
    assert out == {}


# ─── create_pending_row ──────────────────────────────────────────────────


def test_create_pending_row_computes_sha256_when_missing() -> None:
    """Caller omits protocol_pdf_sha256 → function computes from pdf bytes."""
    import hashlib
    pdf = b"%PDF-1.4 fake content for sha test\n"
    expected_sha = hashlib.sha256(pdf).hexdigest()

    stub = _StubHttpClient()
    stub.queue(_StubResponse(
        status_code=201,
        json_body={"item_id": 12345, "status": "Awaiting Build Completion"},
    ))

    result = asyncio.run(create_pending_row(
        pdf,
        name="TEST-PROTOCOL",
        protocol_filename="test.pdf",
        http_client=stub,
    ))

    assert result == 12345
    assert len(stub.posts) == 1
    posted = stub.posts[0]
    assert posted["data"]["protocol_pdf_sha256"] == expected_sha


def test_create_pending_row_preserves_caller_supplied_sha256() -> None:
    """Caller supplies protocol_pdf_sha256 → function passes it through verbatim."""
    pdf = b"%PDF-1.4 fake content\n"
    caller_value = "CALLER-SUPPLIED-deadbeef"

    stub = _StubHttpClient()
    stub.queue(_StubResponse(
        status_code=201,
        json_body={"item_id": 99999, "status": "Awaiting Build Completion"},
    ))

    result = asyncio.run(create_pending_row(
        pdf,
        name="TEST-PROTOCOL",
        protocol_filename="test.pdf",
        protocol_pdf_sha256=caller_value,
        http_client=stub,
    ))

    assert result == 99999
    posted = stub.posts[0]
    assert posted["data"]["protocol_pdf_sha256"] == caller_value


def test_create_pending_row_empty_pdf_returns_none() -> None:
    """Empty protocol_pdf short-circuits to None — no HTTP call made."""
    stub = _StubHttpClient()
    result = asyncio.run(create_pending_row(
        b"",
        name="TEST-PROTOCOL",
        protocol_filename="test.pdf",
        http_client=stub,
    ))
    assert result is None
    assert len(stub.posts) == 0


# ─── Script runner ──────────────────────────────────────────────────────


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
