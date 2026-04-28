"""
Tests for ``core.monday_client``.

The whole module is testable without httpx: the ``MondayClient`` class
accepts an injected ``http_client``. We provide a stub that captures
each request and returns canned responses.

Run as a script::

    python tests/test_monday_client.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Standalone-script support
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.monday_client import (
    COL,
    CORPUS_BOARD_ID,
    DECISION_LABELS,
    INGEST_STATUS_LABELS,
    TRIGGER_LABELS,
    CorpusItem,
    MondayClient,
)


# ─── Stub HTTP transport ────────────────────────────────────────────


class _StubResponse:
    """Mimics httpx.Response — only the fields we use."""

    def __init__(
        self,
        status_code: int = 200,
        json_body: dict | None = None,
        text: str | None = None,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self._json = json_body if json_body is not None else {"data": {}}
        self.text = text if text is not None else json.dumps(self._json)
        self.content = content if content else self.text.encode()

    def json(self) -> dict:
        return self._json


class _StubHttpClient:
    """Captures POST requests; returns the next queued response."""

    def __init__(self) -> None:
        self.posts: list[dict] = []  # each entry: {url, headers, json, files}
        self.queued_responses: list[_StubResponse] = []

    def queue(self, response: _StubResponse) -> None:
        self.queued_responses.append(response)

    async def post(
        self,
        url: str,
        *,
        headers: dict | None = None,
        json: dict | None = None,
        files: dict | None = None,
    ):
        self.posts.append({
            "url": url,
            "headers": headers or {},
            "json": json,
            "files": files,
        })
        if not self.queued_responses:
            return _StubResponse(json_body={"data": {}})
        return self.queued_responses.pop(0)

    async def aclose(self) -> None:
        pass


def _make_client(token: str = "test-token") -> tuple[MondayClient, _StubHttpClient]:
    """Convenience: build a client wired to a fresh stub."""
    stub = _StubHttpClient()
    client = MondayClient(token=token, http_client=stub)
    return client, stub


# ─── Constants & shape ────────────────────────────────────────────


def test_corpus_board_id_is_set() -> None:
    assert CORPUS_BOARD_ID == 18410424473


def test_col_map_contains_required_keys() -> None:
    """The column map must include every key the trainer needs."""
    required = {
        "form_design", "protocol", "protocol_analysis_json",
        "trigger", "ingest_status", "decision_needed", "human_decision",
        "fingerprint", "human_notes",
        "indexed_pair_hash", "source_pipeline_item", "sponsor_client",
        "index_date",
    }
    missing = required - COL.keys()
    assert not missing, f"Missing column keys: {missing}"


def test_status_label_dicts_have_expected_keys() -> None:
    """Make sure status label maps contain the keys we'll be using."""
    assert "send_to_trainer" in TRIGGER_LABELS
    assert "dont_send" in TRIGGER_LABELS

    expected_ingest = {
        "not_started", "parsing_form", "awaiting_human",
        "awaiting_build_completion", "indexed", "failed",
    }
    missing = expected_ingest - INGEST_STATUS_LABELS.keys()
    assert not missing, f"Missing ingest status keys: {missing}"


def test_status_labels_match_ui_values() -> None:
    """Cross-check the human-readable strings."""
    assert INGEST_STATUS_LABELS["awaiting_build_completion"] == "Awaiting Build Completion"
    assert INGEST_STATUS_LABELS["indexed"] == "Indexed"
    assert TRIGGER_LABELS["send_to_trainer"] == "Send to Trainer"
    assert TRIGGER_LABELS["dont_send"] == "Don't Send"


# ─── Token resolution ────────────────────────────────────────────


def test_token_uses_explicit_value_when_provided() -> None:
    client, _ = _make_client(token="explicit-tok")
    assert client._resolve_token() == "explicit-tok"


def test_token_strips_whitespace_from_env_var() -> None:
    import os

    saved = os.environ.get("MONDAY_API_TOKEN")
    try:
        os.environ["MONDAY_API_TOKEN"] = "  env-tok  \n"
        client = MondayClient()
        assert client._resolve_token() == "env-tok"
    finally:
        if saved is not None:
            os.environ["MONDAY_API_TOKEN"] = saved
        else:
            os.environ.pop("MONDAY_API_TOKEN", None)


def test_token_missing_raises_clear_error() -> None:
    import os

    saved = os.environ.get("MONDAY_API_TOKEN")
    try:
        os.environ.pop("MONDAY_API_TOKEN", None)
        client = MondayClient()
        try:
            client._resolve_token()
        except RuntimeError as exc:
            assert "MONDAY_API_TOKEN" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError")
    finally:
        if saved is not None:
            os.environ["MONDAY_API_TOKEN"] = saved


# ─── get_item ────────────────────────────────────────────────────


def _canned_item_response(
    *,
    item_id: int = 1234,
    name: str = "Test Study",
    sponsor: str = "Acme Therapeutics",
    ingest_status_text: str = "Awaiting Human",
    asset_id: str = "9001",
    asset_url: str = "https://files.monday.com/test.pdf",
    file_col_key: str = "protocol",
) -> dict:
    """Build the GraphQL response for a typical item read."""
    column_values = []
    # Free-text columns
    column_values.append({
        "id": COL["sponsor_client"], "type": "text",
        "value": json.dumps(sponsor), "text": sponsor,
    })
    # Status columns
    column_values.append({
        "id": COL["ingest_status"], "type": "color",
        "value": json.dumps({"label": {"text": ingest_status_text}}),
        "text": ingest_status_text,
    })
    # File column — protocol
    column_values.append({
        "id": COL[file_col_key], "type": "file",
        "value": json.dumps({"files": [
            {"assetId": asset_id, "name": "protocol.pdf",
             "isImage": "false", "fileType": "ASSET"}
        ]}),
        "text": "protocol.pdf",
    })

    return {
        "data": {
            "items": [{
                "id": str(item_id),
                "name": name,
                "column_values": column_values,
                "assets": [
                    {"id": asset_id, "name": "protocol.pdf",
                     "url": asset_url, "public_url": asset_url},
                ],
            }],
        },
    }


def test_get_item_parses_basic_fields() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body=_canned_item_response()))
    item = asyncio.run(client.get_item(1234))
    assert item.item_id == 1234
    assert item.name == "Test Study"
    assert item.sponsor_client == "Acme Therapeutics"
    assert item.ingest_status == "Awaiting Human"


def test_get_item_extracts_file_metadata() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body=_canned_item_response(
        asset_id="42", asset_url="https://files.monday.com/x.pdf",
    )))
    item = asyncio.run(client.get_item(1234))
    assert "protocol" in item.files_by_column
    files = item.files_by_column["protocol"]
    assert len(files) == 1
    assert files[0]["asset_id"] == "42"
    assert files[0]["name"] == "protocol.pdf"
    assert item.asset_urls["42"] == "https://files.monday.com/x.pdf"


def test_get_item_raises_when_not_found() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"items": []}}))
    try:
        asyncio.run(client.get_item(9999))
    except ValueError as exc:
        assert "9999" in str(exc)
    else:
        raise AssertionError("Expected ValueError on missing item")


def test_get_item_raises_when_api_returns_errors() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={
        "errors": [{"message": "rate limit exceeded"}],
    }))
    try:
        asyncio.run(client.get_item(1234))
    except RuntimeError as exc:
        assert "rate limit" in str(exc).lower() or "errors" in str(exc).lower()
    else:
        raise AssertionError("Expected RuntimeError on API errors")


def test_get_item_sends_correct_auth_header() -> None:
    client, stub = _make_client(token="my-token-42")
    stub.queue(_StubResponse(json_body=_canned_item_response()))
    asyncio.run(client.get_item(1234))
    assert len(stub.posts) == 1
    assert stub.posts[0]["headers"]["Authorization"] == "my-token-42"


# ─── set_ingest_status ────────────────────────────────────────────


def test_set_ingest_status_sends_correct_label() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"change_column_value": {"id": "x"}}}))
    asyncio.run(client.set_ingest_status(123, "indexed"))

    assert len(stub.posts) == 1
    posted = stub.posts[0]["json"]
    variables = posted["variables"]
    assert variables["i"] == "123"
    assert variables["c"] == COL["ingest_status"]
    # The value must be a JSON-encoded dict {"label": "..."}
    value_dict = json.loads(variables["v"])
    assert value_dict == {"label": "Indexed"}


def test_set_ingest_status_rejects_unknown_key() -> None:
    client, _ = _make_client()
    try:
        asyncio.run(client.set_ingest_status(123, "totally_made_up"))
    except ValueError as exc:
        assert "totally_made_up" in str(exc)
    else:
        raise AssertionError("Expected ValueError on bad status key")


def test_set_ingest_status_awaiting_build_completion() -> None:
    """The label we added today should round-trip correctly."""
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"change_column_value": {"id": "x"}}}))
    asyncio.run(client.set_ingest_status(123, "awaiting_build_completion"))
    value = json.loads(stub.posts[0]["json"]["variables"]["v"])
    assert value["label"] == "Awaiting Build Completion"


# ─── set_trigger ──────────────────────────────────────────────────


def test_set_trigger_dont_send() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"change_column_value": {"id": "x"}}}))
    asyncio.run(client.set_trigger(456, "dont_send"))
    value = json.loads(stub.posts[0]["json"]["variables"]["v"])
    assert value["label"] == "Don't Send"


def test_set_trigger_rejects_unknown_key() -> None:
    client, _ = _make_client()
    try:
        asyncio.run(client.set_trigger(123, "make_coffee"))
    except ValueError as exc:
        assert "make_coffee" in str(exc)
    else:
        raise AssertionError("Expected ValueError on bad trigger key")


# ─── set_decision_needed ──────────────────────────────────────────


def test_set_decision_needed_with_label() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"change_column_value": {"id": "x"}}}))
    asyncio.run(client.set_decision_needed(123, "supply_protocol"))
    value = json.loads(stub.posts[0]["json"]["variables"]["v"])
    assert value["label"] == "Supply Protocol"


def test_set_decision_needed_clears_with_none() -> None:
    """Passing None to set_decision_needed should clear the column."""
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"change_column_value": {"id": "x"}}}))
    asyncio.run(client.set_decision_needed(123, None))
    value_str = stub.posts[0]["json"]["variables"]["v"]
    # JSON-encoded null when clearing
    assert json.loads(value_str) is None


def test_set_decision_needed_rejects_unknown() -> None:
    client, _ = _make_client()
    try:
        asyncio.run(client.set_decision_needed(123, "made_up"))
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")


# ─── set_long_text / set_text / set_date ──────────────────────────


def test_set_long_text_writes_dict_with_text_key() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"change_column_value": {"id": "x"}}}))
    asyncio.run(client.set_long_text(123, "fingerprint",
                                     "sponsor=Acme; phase=2"))
    value = json.loads(stub.posts[0]["json"]["variables"]["v"])
    assert value == {"text": "sponsor=Acme; phase=2"}


def test_set_text_writes_bare_string() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"change_column_value": {"id": "x"}}}))
    asyncio.run(client.set_text(123, "indexed_pair_hash", "abc123"))
    value = json.loads(stub.posts[0]["json"]["variables"]["v"])
    # Plain text columns: just a bare JSON-encoded string
    assert value == "abc123"


def test_set_date_writes_iso_format() -> None:
    from datetime import date as _date

    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={"data": {"change_column_value": {"id": "x"}}}))
    asyncio.run(client.set_date(123, "index_date", _date(2026, 4, 27)))
    value = json.loads(stub.posts[0]["json"]["variables"]["v"])
    assert value == {"date": "2026-04-27"}


def test_set_long_text_rejects_unknown_column_key() -> None:
    client, _ = _make_client()
    try:
        asyncio.run(client.set_long_text(123, "no_such_column", "x"))
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError on bad col_key")


# ─── create_row ──────────────────────────────────────────────────


def test_create_row_returns_new_id() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={
        "data": {"create_item": {"id": "987654"}}
    }))
    new_id = asyncio.run(client.create_row(
        name="PrTK05",
        sponsor_client="Candel Therapeutics, Inc.",
        source_pipeline_item="18394025687",
    ))
    assert new_id == 987654


def test_create_row_serializes_column_values_correctly() -> None:
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={
        "data": {"create_item": {"id": "1"}}
    }))
    asyncio.run(client.create_row(
        name="X", sponsor_client="Acme", source_pipeline_item="12345",
        ingest_status_key="awaiting_build_completion",
    ))
    posted = stub.posts[0]["json"]
    cv = json.loads(posted["variables"]["cv"])
    assert cv[COL["sponsor_client"]] == "Acme"
    assert cv[COL["source_pipeline_item"]] == "12345"
    assert cv[COL["ingest_status"]]["label"] == "Awaiting Build Completion"


def test_create_row_omits_unset_fields() -> None:
    """Fields not passed should not appear in column_values."""
    client, stub = _make_client()
    stub.queue(_StubResponse(json_body={
        "data": {"create_item": {"id": "1"}}
    }))
    asyncio.run(client.create_row(name="X", ingest_status_key="not_started"))
    cv = json.loads(stub.posts[0]["json"]["variables"]["cv"])
    assert COL["sponsor_client"] not in cv
    assert COL["source_pipeline_item"] not in cv
    # But ingest_status IS set
    assert COL["ingest_status"] in cv


# ─── cache_files_for_pair (uses tmp dir) ──────────────────────────


def test_cache_files_for_pair_skips_columns_with_no_file() -> None:
    """A column with no uploaded file shouldn't be downloaded."""
    client, _ = _make_client()
    # Item with NO files attached
    item = CorpusItem(
        item_id=1, name="x",
        files_by_column={},
        asset_urls={},
    )
    with tempfile.TemporaryDirectory() as tmp:
        client._files_root = Path(tmp)
        result = asyncio.run(client.cache_files_for_pair(item, "phash"))
    assert result == {}


# Skipping a real download test here — that would require either a
# httpx-based mock or actually hitting the network. The pipeline's
# monday_client.py download function is the same code path; we trust
# it. Integration check covers the live download.


# ─── Script runner ────────────────────────────────────────────────


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
