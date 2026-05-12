"""
Trainer's monday.com API client.

Mirrors the patterns from oc-ai-pipeline's ``monday_client.py``:
same auth (env var ``MONDAY_API_TOKEN``), same GraphQL endpoint, same
multipart-upload shape for files. Different board, different column map.

What this module does:
  * Read a corpus board row → ``CorpusItem`` dataclass
  * Set Ingest Status / Trigger / Decision Needed labels
  * Append free-text notes (sponsor name, fingerprint summary, etc.)
  * Set date and text columns
  * Download files referenced by file columns; cache to local disk
  * Upload files (rare on the trainer side — mostly the pipeline does
    this when auto-stubbing)
  * Create a new corpus row (used by the pipeline's auto-stub flow,
    when the pipeline POSTs to /corpus/candidate-stub)

What this module does NOT do:
  * Webhook signature verification (lives in app/routes/webhook.py)
  * Job orchestration (lives in workers/ingest_worker.py)
  * Any business logic — this is pure I/O against monday's API

The token lookup is deferred to first-use so importing this module
doesn't require the env var to be set (matters for unit tests).
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    import httpx


# Logger — same shim pattern used elsewhere in core/.
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


# ─── Constants ────────────────────────────────────────────────────


MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_FILE_URL = "https://api.monday.com/v2/file"

# Corpus board ID. Hard-coded because there's only one in production.
# In tests we override via the ``board_id`` arg to the constructor.
CORPUS_BOARD_ID = 18410424473

# Column-ID map. These IDs were assigned by monday when columns were
# created and do not change unless a column is deleted and recreated.
COL: dict[str, str] = {
    # File columns
    "form_design":             "file_mm2tr9gs",   # ODM XML (actual build)
    "final_xls_forms":         "file_mm2yv234",   # Final XLSForm ZIP (actual build)
    "protocol":                "file_mm2tjj47",
    "protocol_analysis_json":  "file_mm2tc0md",
    "predicted_edc_zip":       "file_mm35j5ce",   # Pipeline-pushed EDC build ZIP (predicted side)
    "ctgov_protocol_pdf":      "file_mm2tw9nr",   # unused (CT.gov deferred)
    # Status columns
    "trigger":                 "color_mm2tw612",
    "ingest_status":           "color_mm2t8mek",
    "decision_needed":         "color_mm2t6xt2",
    "human_decision":          "color_mm2th07z",
    # Long-text columns
    "fingerprint":             "long_text_mm2tyxh5",
    "ctgov_top_match":         "long_text_mm2t6yg6",  # unused
    "ctgov_candidates":        "long_text_mm2t4p6n",  # unused
    "human_notes":             "long_text_mm2tskk2",
    # Text columns
    "indexed_pair_hash":       "text_mm2tn36k",
    "source_pipeline_item":    "text_mm2typsx",
    "sponsor_client":          "text_mm2tw420",
    "protocol_number":         "text_mm35s55p",   # Pipeline-pushed protocol number (dedup key with sponsor_client)
    "protocol_pdf_sha256":     "text_mm35j8r5",   # Pipeline-pushed PDF SHA-256 (drift detection on dedup hits)
    # Date columns
    "index_date":              "date_mm2tn53m",
    "accuracy_score":          "numeric_mm2y10gd",
    "accuracy_report":         "file_mm2yq3cp",
    # Path-M (migration) columns — added by
    # scripts/create_corpus_migration_columns.py.
    "source_system":           "text_mm392fb2",   # Vendor label (e.g. "Medidata Rave")
    "path":                    "color_mm39ea6a",  # Protocol (Path B) | Migration (Path M)
    "source_odm_xml":          "file_mm394z0b",   # Source EDC ODM XML (Path M)
}

# Path status labels (Path B vs Path M).
PATH_LABELS: dict[str, str] = {
    "protocol":   "Protocol (Path B)",
    "migration":  "Migration (Path M)",
}

# Trigger labels. Keys are stable internal names; values are the label
# strings as they appear in the monday UI.
TRIGGER_LABELS: dict[str, str] = {
    "send_to_trainer": "Send to Trainer",
    "dont_send":       "Don't Send",
}

# Ingest Status labels.
# NOTE: The new labels (missing_odm_xml, missing_xls_forms,
# missing_both_files, generating_predicted_build, comparing_builds)
# must be added to the monday column settings before they can be set.
INGEST_STATUS_LABELS: dict[str, str] = {
    "not_started":               "Not Started",
    "parsing_form":              "Parsing Form",
    "searching_ctgov":           "Searching CT.gov",      # currently unreachable
    "awaiting_human":            "Awaiting Human",
    "awaiting_build_completion": "Awaiting Build Completion",
    # ── New validation states ──
    "missing_odm_xml":           "Missing ODM XML",
    "missing_xls_forms":         "Missing XLS Forms",
    "missing_both_files":        "Missing Both Files",
    # ── New processing states ──
    "generating_predicted_build": "Generating Predicted Build",
    "comparing_builds":           "Comparing Builds",
    # ── Path-M (migration) states ──
    "pending_ps_review":         "Pending PS Review",
    # ── Terminal states ──
    "indexed":                   "Indexed",
    "failed":                    "Failed",
}

# Decision Needed labels (set when human input is required).
DECISION_LABELS: dict[str, str] = {
    "supply_protocol":            "Supply Protocol",
    "review_ctgov_match":         "Review CT.gov Match",   # unused
    "supply_form_design":         "Supply Form Design",    # unused
    "investigate_ingest_failure": "Investigate Failure",
}


# ─── Data shape — what we read from a row ─────────────────────────


@dataclass
class CorpusItem:
    """Minimal projection of a corpus-board row.

    Only fields the trainer cares about are populated. Raw asset
    metadata is preserved in ``assets_by_column`` so callers can
    pick out a specific file column's URL without re-querying.
    """

    item_id: int
    name: str

    # Status fields
    trigger: str | None = None
    ingest_status: str | None = None
    decision_needed: str | None = None
    human_decision: str | None = None

    # Free-text fields (curator-supplied)
    sponsor_client: str | None = None
    human_notes: str | None = None
    source_pipeline_item: str | None = None
    protocol_number: str | None = None
    protocol_pdf_sha256: str | None = None

    # Path-M (migration) fields
    source_system: str | None = None      # vendor label, e.g. "Medidata Rave"
    path: str | None = None               # "Protocol (Path B)" | "Migration (Path M)"

    # Pre-computed fields (populated by the trainer or pipeline)
    fingerprint: str | None = None
    indexed_pair_hash: str | None = None

    # File columns: a list of {"asset_id", "name"} per column.
    # monday returns these inside the column's `value` JSON. The list
    # is empty if no file is uploaded.
    files_by_column: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    # Asset URLs by asset_id — convenient for cross-referencing.
    asset_urls: dict[str, str] = field(default_factory=dict)


# ─── The client ───────────────────────────────────────────────────


class MondayClient:
    """
    Async monday.com API client for the trainer service.

    Construction:

      * No args → reads ``MONDAY_API_TOKEN`` from environment on
        first request.
      * ``token=`` overrides for one-off scripts.
      * ``http_client=`` (httpx.AsyncClient or test stub) lets unit
        tests substitute a fake transport.
      * ``board_id=`` overrides the default corpus board (used by
        tests).
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        http_client: "httpx.AsyncClient | None" = None,
        board_id: int = CORPUS_BOARD_ID,
        files_root: str | Path | None = None,
    ) -> None:
        self._token = token
        self._http_client = http_client
        self._owned_client = http_client is None
        self.board_id = board_id
        if files_root is None:
            # Default to the Railway persistent volume so cached files survive
            # container redeploys. The volume is mounted at /data (same place
            # HF_HOME lives). Fall back to a local path for unit tests.
            import os
            data_root = os.environ.get("CORPUS_FILES_ROOT", "/data/corpus/files")
            files_root = Path(data_root)
        self._files_root = Path(files_root)

    # ── HTTP plumbing ────────────────────────────────────────────

    def _resolve_token(self) -> str:
        if self._token is not None:
            return self._token
        token = os.environ.get("MONDAY_API_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "MONDAY_API_TOKEN not set. Add it to .env (same value "
                "the pipeline uses)."
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._resolve_token(),
            "Content-Type": "application/json",
            "API-Version": "2024-01",
        }

    async def _ensure_client(self) -> "httpx.AsyncClient":
        if self._http_client is not None:
            return self._http_client
        import httpx

        self._http_client = httpx.AsyncClient(timeout=30)
        return self._http_client

    async def aclose(self) -> None:
        """Close the owned httpx client. Safe to call multiple times."""
        if self._owned_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> "MondayClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    # ── Low-level GraphQL ────────────────────────────────────────

    async def _gql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a GraphQL query/mutation. Returns ``data``; raises on errors."""
        client = await self._ensure_client()
        payload = {"query": query, "variables": variables or {}}
        response = await client.post(
            MONDAY_API_URL,
            headers=self._headers(),
            json=payload,
        )
        body = response.json() if response.content else {}
        if response.status_code != 200:
            raise RuntimeError(
                f"monday API HTTP {response.status_code}: {response.text[:300]}"
            )
        if "errors" in body:
            raise RuntimeError(f"monday API errors: {body['errors']}")
        if "data" not in body:
            raise RuntimeError(f"monday API unexpected body: {body}")
        return body["data"]

    # ── Read row ─────────────────────────────────────────────────

    async def get_item(self, item_id: int) -> CorpusItem:
        """Fetch one corpus board row, projected to ``CorpusItem``."""
        query = """
        query($i:[ID!]) {
          items(ids:$i) {
            id
            name
            column_values { id type value text }
            assets { id name url public_url }
          }
        }
        """
        data = await self._gql(query, {"i": [str(item_id)]})
        items = data.get("items") or []
        if not items:
            raise ValueError(f"No item found with id {item_id}")
        return self._parse_item(items[0])

    @staticmethod
    def _parse_item(raw: dict[str, Any]) -> CorpusItem:
        col_values = {cv["id"]: cv for cv in (raw.get("column_values") or [])}

        def _text(col_key: str) -> str | None:
            v = col_values.get(COL[col_key])
            if not v:
                return None
            t = v.get("text")
            if t is None or t == "":
                return None
            return t

        def _label(col_key: str) -> str | None:
            v = col_values.get(COL[col_key])
            if not v:
                return None
            return v.get("text") or None

        # File columns: pull list of {asset_id, name} from the
        # column's `value` JSON. Includes the new final_xls_forms col.
        files_by_column: dict[str, list[dict[str, str]]] = {}
        for col_key in (
            "form_design",
            "final_xls_forms",
            "protocol",
            "protocol_analysis_json",
            "predicted_edc_zip",
            "ctgov_protocol_pdf",
            "source_odm_xml",
        ):
            v = col_values.get(COL[col_key])
            if not v:
                continue
            raw_value = v.get("value")
            if not raw_value:
                continue
            try:
                parsed = json.loads(raw_value)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
            files = parsed.get("files") if isinstance(parsed, dict) else None
            if not files:
                continue
            entries: list[dict[str, str]] = []
            for f in files:
                entries.append({
                    "asset_id": str(f.get("assetId") or f.get("asset_id") or ""),
                    "name": str(f.get("name") or ""),
                })
            files_by_column[col_key] = entries

        asset_urls: dict[str, str] = {}
        for asset in raw.get("assets") or []:
            aid = str(asset.get("id"))
            url = asset.get("public_url") or asset.get("url")
            if aid and url:
                asset_urls[aid] = url

        return CorpusItem(
            item_id=int(raw["id"]),
            name=raw.get("name") or "",
            trigger=_label("trigger"),
            ingest_status=_label("ingest_status"),
            decision_needed=_label("decision_needed"),
            human_decision=_label("human_decision"),
            sponsor_client=_text("sponsor_client"),
            human_notes=_text("human_notes"),
            source_pipeline_item=_text("source_pipeline_item"),
            protocol_number=_text("protocol_number"),
            protocol_pdf_sha256=_text("protocol_pdf_sha256"),
            source_system=_text("source_system"),
            path=_label("path"),
            fingerprint=_text("fingerprint"),
            indexed_pair_hash=_text("indexed_pair_hash"),
            files_by_column=files_by_column,
            asset_urls=asset_urls,
        )

    # ── Write back ───────────────────────────────────────────────

    async def set_ingest_status(self, item_id: int, status_key: str) -> None:
        """Set the Ingest Status by stable internal name."""
        if status_key not in INGEST_STATUS_LABELS:
            raise ValueError(
                f"Unknown ingest status {status_key!r}. "
                f"Allowed: {sorted(INGEST_STATUS_LABELS)}"
            )
        await self._set_status(item_id, COL["ingest_status"],
                               INGEST_STATUS_LABELS[status_key])

    async def set_trigger(self, item_id: int, trigger_key: str) -> None:
        if trigger_key not in TRIGGER_LABELS:
            raise ValueError(
                f"Unknown trigger {trigger_key!r}. "
                f"Allowed: {sorted(TRIGGER_LABELS)}"
            )
        await self._set_status(item_id, COL["trigger"],
                               TRIGGER_LABELS[trigger_key])

    async def set_decision_needed(self, item_id: int, decision_key: str | None) -> None:
        """Set or clear the Decision Needed column.

        Pass ``None`` to clear the column (sets value to empty).
        """
        if decision_key is None:
            await self._clear_status(item_id, COL["decision_needed"])
            return
        if decision_key not in DECISION_LABELS:
            raise ValueError(
                f"Unknown decision {decision_key!r}. "
                f"Allowed: {sorted(DECISION_LABELS)}"
            )
        await self._set_status(item_id, COL["decision_needed"],
                               DECISION_LABELS[decision_key])

    async def set_long_text(self, item_id: int, col_key: str, text: str) -> None:
        """Set a long-text column."""
        if col_key not in COL:
            raise ValueError(f"Unknown column key {col_key!r}")
        await self._set_column_value(
            item_id, COL[col_key], json.dumps({"text": text})
        )

    async def set_text(self, item_id: int, col_key: str, text: str) -> None:
        """Set a plain-text column."""
        if col_key not in COL:
            raise ValueError(f"Unknown column key {col_key!r}")
        await self._set_column_value(
            item_id, COL[col_key], json.dumps(text)
        )

    async def set_date(self, item_id: int, col_key: str, when: date) -> None:
        """Set a date column."""
        if col_key not in COL:
            raise ValueError(f"Unknown column key {col_key!r}")
        date_str = when.strftime("%Y-%m-%d")
        await self._set_column_value(
            item_id, COL[col_key], json.dumps({"date": date_str})
        )
    async def set_number(self, item_id: int, col_key: str, value: float) -> None:
        """Set a numbers column."""
        if col_key not in COL:
            raise ValueError(f"Unknown column key {col_key!r}")
        await self._set_column_value(
            item_id, COL[col_key], json.dumps(value)
        )
    async def _set_status(self, item_id: int, col_id: str, label: str) -> None:
        await self._set_column_value(
            item_id, col_id, json.dumps({"label": label})
        )

    async def _clear_status(self, item_id: int, col_id: str) -> None:
        await self._set_column_value(item_id, col_id, json.dumps({}))

    async def _set_column_value(
        self,
        item_id: int,
        col_id: str,
        value_json: str,
    ) -> None:
        mutation = """
        mutation($i:ID!, $b:ID!, $c:String!, $v:JSON!) {
          change_column_value(item_id:$i, board_id:$b, column_id:$c, value:$v) { id }
        }
        """
        await self._gql(mutation, {
            "i": str(item_id),
            "b": str(self.board_id),
            "c": col_id,
            "v": value_json,
        })
        logger.info("monday.column_set", item_id=item_id, col_id=col_id)

    # ── Files: download ──────────────────────────────────────────

    async def download_asset(
        self,
        url: str,
        dest_path: Path,
    ) -> int:
        """Download a file from monday → ``dest_path``. Returns byte count."""
        import httpx

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            response = await c.get(url)
        if response.status_code != 200:
            raise RuntimeError(
                f"monday asset download failed: HTTP {response.status_code}"
            )
        dest_path.write_bytes(response.content)
        return len(response.content)

    async def cache_files_for_pair(
        self,
        item: CorpusItem,
        pair_hash: str,
        column_keys: tuple[str, ...] = (
            "form_design",
            "final_xls_forms",
            "protocol",
            "protocol_analysis_json",
            "predicted_edc_zip",
        ),
    ) -> dict[str, Path]:
        """Download all files for a pair into the corpus cache.

        Returns a mapping ``column_key -> local_path`` for files that
        actually existed and downloaded successfully.
        """
        pair_dir = self._files_root / pair_hash
        out: dict[str, Path] = {}
        for col_key in column_keys:
            entries = item.files_by_column.get(col_key) or []
            if not entries:
                continue
            asset = entries[0]
            asset_id = asset.get("asset_id") or ""
            url = item.asset_urls.get(asset_id)
            if not url:
                logger.warning(
                    "monday.cache_files.missing_url",
                    item_id=item.item_id, col=col_key, asset_id=asset_id,
                )
                continue
            filename = asset.get("name") or f"{col_key}.bin"
            dest = pair_dir / filename
            byte_count = await self.download_asset(url, dest)
            logger.info(
                "monday.cached_file",
                item_id=item.item_id, col=col_key, dest=str(dest),
                bytes=byte_count,
            )
            out[col_key] = dest
        return out

    # ── Files: upload ────────────────────────────────────────────

    async def upload_file_to_column(
        self,
        item_id: int,
        col_key: str,
        filename: str,
        content: bytes,
    ) -> str:
        """Upload bytes as a file to a file-type column. Returns asset id."""
        if col_key not in COL:
            raise ValueError(f"Unknown column key {col_key!r}")
        col_id = COL[col_key]

        import httpx

        mutation_query = (
            "mutation ($file: File!) {"
            f' add_file_to_column(item_id: {item_id}, '
            f'column_id: "{col_id}", file: $file) {{ id }} '
            "}"
        )
        token = self._resolve_token()
        async with httpx.AsyncClient(timeout=120) as c:
            response = await c.post(
                MONDAY_FILE_URL,
                headers={"Authorization": token, "API-Version": "2023-10"},
                files={
                    "query":     (None, mutation_query),
                    "variables": (None, '{"file": null}'),
                    "map":       (None, '{"file": ["variables.file"]}'),
                    "file":      (filename, content, "application/octet-stream"),
                },
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"monday file upload HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        body = response.json() if response.content else {}
        if "errors" in body:
            raise RuntimeError(f"monday file upload errors: {body['errors']}")
        asset_id = (
            body.get("data", {})
                .get("add_file_to_column", {})
                .get("id", "")
        )
        logger.info(
            "monday.file_uploaded",
            item_id=item_id, col=col_key, filename=filename,
            bytes=len(content), asset_id=asset_id,
        )
        return str(asset_id)

    # ── Create new row (used by pipeline auto-stub) ──────────────

    async def create_row(
        self,
        name: str,
        sponsor_client: str | None = None,
        source_pipeline_item: str | None = None,
        ingest_status_key: str = "awaiting_build_completion",
        source_system: str | None = None,
        path_key: str | None = None,
    ) -> int:
        """Create a new corpus board row. Returns the new item_id.

        Path-M kwargs:
            source_system: vendor label (e.g. "Medidata Rave"). Written to
                the Source System text column when supplied.
            path_key: one of PATH_LABELS keys ("protocol" | "migration").
                Sets the Path status column. Omit on Path B legacy callers.
        """
        column_values: dict[str, Any] = {}
        if sponsor_client:
            column_values[COL["sponsor_client"]] = sponsor_client
        if source_pipeline_item:
            column_values[COL["source_pipeline_item"]] = source_pipeline_item
        if ingest_status_key:
            if ingest_status_key not in INGEST_STATUS_LABELS:
                raise ValueError(
                    f"Unknown ingest status {ingest_status_key!r}"
                )
            column_values[COL["ingest_status"]] = {
                "label": INGEST_STATUS_LABELS[ingest_status_key]
            }
        if source_system:
            column_values[COL["source_system"]] = source_system
        if path_key:
            if path_key not in PATH_LABELS:
                raise ValueError(
                    f"Unknown path {path_key!r}. Allowed: {sorted(PATH_LABELS)}"
                )
            column_values[COL["path"]] = {"label": PATH_LABELS[path_key]}

        mutation = """
        mutation($b:ID!, $n:String!, $cv:JSON!) {
          create_item(board_id:$b, item_name:$n, column_values:$cv) { id }
        }
        """
        data = await self._gql(mutation, {
            "b": str(self.board_id),
            "n": name,
            "cv": json.dumps(column_values),
        })
        new_id = int(data["create_item"]["id"])
        logger.info(
            "monday.row_created",
            item_id=new_id, name=name, sponsor=sponsor_client,
        )
        return new_id

    # ── Dedup query (used by /pending-row) ───────────────────────

    async def find_existing_row(
        self,
        sponsor_client: str,
        protocol_number: str,
    ) -> int | None:
        """Find a corpus row by (sponsor_client, protocol_number) AND-match.

        Used by /pending-row dedup: when the pipeline pushes a protocol
        already in the corpus, the caller skips create and (optionally)
        warns on PDF SHA drift. Returns the item_id of the first match,
        or None if no row matches both column values. Empty inputs
        return None.
        """
        if not sponsor_client or not protocol_number:
            return None

        query = """
        query($b:ID!, $cols:[ItemsPageByColumnValuesQuery!]!) {
          items_page_by_column_values(board_id:$b, columns:$cols) {
            items { id name }
          }
        }
        """
        variables = {
            "b": str(self.board_id),
            "cols": [
                {"column_id": COL["sponsor_client"],
                 "column_values": [sponsor_client]},
                {"column_id": COL["protocol_number"],
                 "column_values": [protocol_number]},
            ],
        }
        data = await self._gql(query, variables)
        page = data.get("items_page_by_column_values") or {}
        items = page.get("items") or []
        if not items:
            logger.info(
                "monday.find_existing_row.no_match",
                sponsor=sponsor_client, protocol_number=protocol_number,
            )
            return None
        item_id = int(items[0]["id"])
        logger.info(
            "monday.find_existing_row.matched",
            sponsor=sponsor_client, protocol_number=protocol_number,
            item_id=item_id, match_count=len(items),
        )
        return item_id

    async def find_existing_row_migration(
        self,
        source_system: str,
        dedup_key: str,
    ) -> int | None:
        """Find a Path-M corpus row by (source_system, dedup_key) AND-match.

        Path B keys on (sponsor_client, protocol_number). Path M doesn't
        have a sponsor at create time — the migration pipeline only knows
        the source EDC vendor and either the ODM-derived protocol number
        (when present) or the ODM study OID (always present) as the
        fallback. ``dedup_key`` is whichever of those the caller has —
        stored on the existing row as ``protocol_number``.

        Returns the item_id of the first match, or None if no row matches
        both column values. Empty inputs return None.
        """
        if not source_system or not dedup_key:
            return None

        query = """
        query($b:ID!, $cols:[ItemsPageByColumnValuesQuery!]!) {
          items_page_by_column_values(board_id:$b, columns:$cols) {
            items { id name }
          }
        }
        """
        variables = {
            "b": str(self.board_id),
            "cols": [
                {"column_id": COL["source_system"],
                 "column_values": [source_system]},
                {"column_id": COL["protocol_number"],
                 "column_values": [dedup_key]},
            ],
        }
        data = await self._gql(query, variables)
        page = data.get("items_page_by_column_values") or {}
        items = page.get("items") or []
        if not items:
            logger.info(
                "monday.find_existing_row_migration.no_match",
                source_system=source_system, dedup_key=dedup_key,
            )
            return None
        item_id = int(items[0]["id"])
        logger.info(
            "monday.find_existing_row_migration.matched",
            source_system=source_system, dedup_key=dedup_key,
            item_id=item_id, match_count=len(items),
        )
        return item_id


# Re-export convenient bits
__all__ = [
    "MondayClient",
    "CorpusItem",
    "COL",
    "INGEST_STATUS_LABELS",
    "TRIGGER_LABELS",
    "DECISION_LABELS",
    "PATH_LABELS",
    "CORPUS_BOARD_ID",
]
