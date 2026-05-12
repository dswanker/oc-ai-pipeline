"""
Tests for ``app.routes.pending_row``.

The route opens its own ``MondayClient`` via ``async with`` so the test
strategy is: monkeypatch ``app.routes.pending_row.MondayClient`` with a
stub class that records calls and returns canned values. We exercise
the route via FastAPI's TestClient so all dependency-injected Form/File
defaults resolve the way they do in production.

Run as a script::

    python tests/test_pending_row.py
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# Standalone-script support
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import pending_row as pending_row_mod
from core.monday_client import CorpusItem, INGEST_STATUS_LABELS, PATH_LABELS


# ─── Stub MondayClient ─────────────────────────────────────────────


@dataclass
class _StubExisting:
    """What the stub returns from get_item() on a dedup hit."""
    item_id: int = 999
    ingest_status: str = "Awaiting Build Completion"
    protocol_pdf_sha256: str | None = None
    human_notes: str | None = None


class _StubMonday:
    """Records every method call. Configured per-test via class attrs."""

    # Per-test knobs (set on the class before instantiation).
    next_create_id: int = 101
    existing_b: int | None = None
    existing_m: int | None = None
    existing_payload: _StubExisting | None = None

    # Captured instances so tests can read back call lists after the request.
    _instances: list["_StubMonday"] = []

    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.upload_calls: list[dict] = []
        self.set_text_calls: list[dict] = []
        self.set_long_text_calls: list[dict] = []
        self.find_b_calls: list[tuple[str, str]] = []
        self.find_m_calls: list[tuple[str, str]] = []
        self.get_item_calls: list[int] = []
        type(self)._instances.append(self)

    async def __aenter__(self) -> "_StubMonday":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def find_existing_row(self, sponsor_client: str, protocol_number: str):
        self.find_b_calls.append((sponsor_client, protocol_number))
        return type(self).existing_b

    async def find_existing_row_migration(self, source_system: str, dedup_key: str):
        self.find_m_calls.append((source_system, dedup_key))
        return type(self).existing_m

    async def get_item(self, item_id: int) -> CorpusItem:
        self.get_item_calls.append(item_id)
        payload = type(self).existing_payload or _StubExisting(item_id=item_id)
        return CorpusItem(
            item_id=payload.item_id,
            name="existing",
            ingest_status=payload.ingest_status,
            protocol_pdf_sha256=payload.protocol_pdf_sha256,
            human_notes=payload.human_notes,
        )

    async def create_row(self, **kwargs: Any) -> int:
        self.create_calls.append(kwargs)
        return type(self).next_create_id

    async def upload_file_to_column(self, **kwargs: Any) -> str:
        self.upload_calls.append(kwargs)
        return "asset-1"

    async def set_text(self, item_id: int, col_key: str, value: str) -> None:
        self.set_text_calls.append(
            {"item_id": item_id, "col_key": col_key, "value": value}
        )

    async def set_long_text(self, item_id: int, col_key: str, value: str) -> None:
        self.set_long_text_calls.append(
            {"item_id": item_id, "col_key": col_key, "value": value}
        )


# ─── Test fixtures ─────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A FastAPI TestClient with the route mounted and MondayClient stubbed."""
    # Reset class-level state between tests.
    _StubMonday.existing_b = None
    _StubMonday.existing_m = None
    _StubMonday.existing_payload = None
    _StubMonday.next_create_id = 101
    _StubMonday._instances = []

    monkeypatch.setattr(pending_row_mod, "MondayClient", _StubMonday)

    app = FastAPI()
    app.include_router(pending_row_mod.router, prefix="/pending-row")
    return TestClient(app)


def _stub_last() -> _StubMonday:
    """Return the most-recently-instantiated stub."""
    assert _StubMonday._instances, "no stub MondayClient was instantiated"
    return _StubMonday._instances[-1]


# ─── Tests ─────────────────────────────────────────────────────────


def test_path_m_create_with_odm_xml_no_pdf(client: TestClient) -> None:
    """Path M without a protocol PDF should still create a row."""
    response = client.post(
        "/pending-row",
        data={
            "name": "CV3001",
            "source_system": "Medidata Rave",
            "path": "migration",
            "ingest_status_key": "pending_ps_review",
            "protocol_number": "CV3001",
            "source_pipeline_item": "123",
        },
        files={
            "odm_xml": ("source.xml", io.BytesIO(b"<ODM/>"), "application/xml"),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["item_id"] == _StubMonday.next_create_id
    assert body["status"] == INGEST_STATUS_LABELS["pending_ps_review"]

    stub = _stub_last()
    # create_row received the path-M kwargs
    assert len(stub.create_calls) == 1
    cc = stub.create_calls[0]
    assert cc["source_system"] == "Medidata Rave"
    assert cc["path_key"] == "migration"
    assert cc["ingest_status_key"] == "pending_ps_review"
    # ODM XML uploaded to source_odm_xml column; no PDF upload.
    cols_uploaded = [u["col_key"] for u in stub.upload_calls]
    assert "source_odm_xml" in cols_uploaded
    assert "protocol" not in cols_uploaded
    # protocol_pdf_sha256 NOT written (no PDF).
    sha_writes = [
        t for t in stub.set_text_calls if t["col_key"] == "protocol_pdf_sha256"
    ]
    assert sha_writes == []
    # protocol_number written (dedup key on Path M).
    pn_writes = [t for t in stub.set_text_calls if t["col_key"] == "protocol_number"]
    assert pn_writes and pn_writes[0]["value"] == "CV3001"


def test_path_m_dedup_hit(client: TestClient) -> None:
    """Path M dedup on (source_system, protocol_number) short-circuits to 200."""
    _StubMonday.existing_m = 555
    _StubMonday.existing_payload = _StubExisting(
        item_id=555, ingest_status="Pending PS Review",
    )

    response = client.post(
        "/pending-row",
        data={
            "name": "CV3001",
            "source_system": "Medidata Rave",
            "path": "migration",
            "ingest_status_key": "pending_ps_review",
            "protocol_number": "CV3001",
        },
        files={
            "odm_xml": ("source.xml", io.BytesIO(b"<ODM/>"), "application/xml"),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "action": "skipped",
        "existing_item_id": 555,
        "status": "Pending PS Review",
    }

    stub = _stub_last()
    assert stub.find_m_calls == [("Medidata Rave", "CV3001")]
    assert stub.find_b_calls == []
    assert stub.create_calls == [], "no row should be created on a dedup hit"


def test_missing_both_files_returns_400(client: TestClient) -> None:
    """Calling /pending-row with neither PDF nor ODM XML must 400."""
    response = client.post("/pending-row", data={"name": "X"})
    assert response.status_code == 400, response.text
    detail = response.json().get("detail", "")
    assert "protocol_pdf" in detail or "odm_xml" in detail


def test_path_b_default_behaviour_unchanged(client: TestClient) -> None:
    """A Path-B caller (PDF only, no `path` arg) keeps the legacy flow."""
    response = client.post(
        "/pending-row",
        data={
            "name": "ABT-123",
            "sponsor_client": "Acme",
            "protocol_number": "ABT-123",
            "source_pipeline_item": "999",
        },
        files={
            "protocol_pdf": ("p.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf"),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == INGEST_STATUS_LABELS["awaiting_build_completion"]

    stub = _stub_last()
    cc = stub.create_calls[0]
    assert cc["path_key"] == "protocol"
    assert cc["ingest_status_key"] == "awaiting_build_completion"
    cols_uploaded = {u["col_key"] for u in stub.upload_calls}
    assert "protocol" in cols_uploaded
    assert "source_odm_xml" not in cols_uploaded
    sha_writes = [
        t for t in stub.set_text_calls if t["col_key"] == "protocol_pdf_sha256"
    ]
    assert sha_writes, "protocol_pdf_sha256 should be set on Path B"


# ─── Script entry point ────────────────────────────────────────────


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
