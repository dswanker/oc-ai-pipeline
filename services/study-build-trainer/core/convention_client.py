"""
convention_client.py — Monday.com client for the Convention Rulebook board.

Board ID: 18411236453
Handles reading submissions, writing Claude questions back, and marking
rows complete after conventions are written to conventions.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

MONDAY_API_URL  = "https://api.monday.com/v2"
MONDAY_FILE_URL = "https://api.monday.com/v2/file"

RULEBOOK_BOARD_ID = 18411236453

# Column IDs on the Convention Rulebook board
COL = {
    "source_study":          "board_relation_mm2ygnzk",
    "submitted_appendix":    "file_mm2ygsrr",
    "review_status":         "color_mm2yp992",
    "submit_trigger":        "color_mm2y41kb",
    "round":                 "numeric_mm2y42sv",
    "claude_questions":      "long_text_mm2ykhdy",
    "conventions_extracted": "numeric_mm2ydr2z",
    "date_submitted":        "date_mm2y7ymx",
    "date_completed":        "date_mm2yqpsj",
    "protocol_number":       "text_mm2yzxg3",
}

# Status labels — set these in monday UI then update here
REVIEW_STATUS_LABELS = {
    "submitted":            "Submitted",
    "processing":           "Processing",
    "needs_clarification":  "Needs Clarification",
    "approved":             "Approved",
    "added_to_rulebook":    "Added to Rulebook",
}

TRIGGER_LABELS = {
    "submit_for_review": "Submit for Review",
    "awaiting_human":    "Awaiting Human",
}


@dataclass
class RulebookItem:
    item_id:      int
    name:         str
    protocol_number: str | None = None
    round_number:    int        = 1
    review_status:   str | None = None
    submit_trigger:  str | None = None
    # File asset references
    appendix_files: list[dict[str, str]] = field(default_factory=list)
    asset_urls:     dict[str, str]       = field(default_factory=dict)


class ConventionMondayClient:

    def __init__(
        self,
        token: str | None = None,
        http_client: "httpx.AsyncClient | None" = None,
        board_id: int = RULEBOOK_BOARD_ID,
    ) -> None:
        self._token       = token
        self._http_client = http_client
        self._owned       = http_client is None
        self.board_id     = board_id

    def _resolve_token(self) -> str:
        if self._token:
            return self._token
        tok = os.environ.get("MONDAY_API_TOKEN", "").strip()
        if not tok:
            raise RuntimeError("MONDAY_API_TOKEN not set")
        return tok

    def _headers(self):
        return {
            "Authorization":  self._resolve_token(),
            "Content-Type":   "application/json",
            "API-Version":    "2024-01",
        }

    async def _ensure_client(self):
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30)
        return self._http_client

    async def aclose(self):
        if self._owned and self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _gql(self, query: str, variables: dict | None = None) -> dict:
        client  = await self._ensure_client()
        payload = {"query": query, "variables": variables or {}}
        resp    = await client.post(MONDAY_API_URL, headers=self._headers(), json=payload)
        body    = resp.json() if resp.content else {}
        if resp.status_code != 200:
            raise RuntimeError(f"monday HTTP {resp.status_code}: {resp.text[:300]}")
        if "errors" in body:
            raise RuntimeError(f"monday errors: {body['errors']}")
        return body["data"]

    # ── Read ─────────────────────────────────────────────────────────────────

    async def get_item(self, item_id: int) -> RulebookItem:
        q = """
        query($i:[ID!]) {
          items(ids:$i) {
            id name
            column_values { id type value text }
            assets { id name url public_url }
          }
        }
        """
        data  = await self._gql(q, {"i": [str(item_id)]})
        items = data.get("items") or []
        if not items:
            raise ValueError(f"No rulebook item found: {item_id}")
        return self._parse_item(items[0])

    @staticmethod
    def _parse_item(raw: dict) -> RulebookItem:
        col_values = {cv["id"]: cv for cv in (raw.get("column_values") or [])}

        def _text(col_key):
            v = col_values.get(COL.get(col_key, ""))
            return (v.get("text") or None) if v else None

        def _label(col_key):
            v = col_values.get(COL.get(col_key, ""))
            return (v.get("text") or None) if v else None

        def _num(col_key):
            v = col_values.get(COL.get(col_key, ""))
            if not v:
                return 1
            try:
                return int(float(v.get("text") or 1))
            except (ValueError, TypeError):
                return 1

        # Parse appendix file column
        appendix_files = []
        v = col_values.get(COL["submitted_appendix"])
        if v and v.get("value"):
            try:
                parsed = json.loads(v["value"])
                for f in (parsed.get("files") or []):
                    appendix_files.append({
                        "asset_id": str(f.get("assetId") or ""),
                        "name":     str(f.get("name") or ""),
                    })
            except (json.JSONDecodeError, TypeError):
                pass

        asset_urls = {}
        for asset in raw.get("assets") or []:
            aid = str(asset.get("id"))
            url = asset.get("public_url") or asset.get("url")
            if aid and url:
                asset_urls[aid] = url

        return RulebookItem(
            item_id        = int(raw["id"]),
            name           = raw.get("name") or "",
            protocol_number= _text("protocol_number"),
            round_number   = _num("round"),
            review_status  = _label("review_status"),
            submit_trigger = _label("submit_trigger"),
            appendix_files = appendix_files,
            asset_urls     = asset_urls,
        )

    async def download_asset(self, url: str, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(url)
        if r.status_code != 200:
            raise RuntimeError(f"Download failed: HTTP {r.status_code}")
        dest.write_bytes(r.content)
        return len(r.content)

    # ── Write ─────────────────────────────────────────────────────────────────

    async def set_review_status(self, item_id: int, status_key: str) -> None:
        label = REVIEW_STATUS_LABELS.get(status_key)
        if not label:
            raise ValueError(f"Unknown review status: {status_key!r}")
        await self._set_col(item_id, COL["review_status"],
                            json.dumps({"label": label}))

    async def set_trigger(self, item_id: int, trigger_key: str) -> None:
        label = TRIGGER_LABELS.get(trigger_key)
        if not label:
            raise ValueError(f"Unknown trigger: {trigger_key!r}")
        await self._set_col(item_id, COL["submit_trigger"],
                            json.dumps({"label": label}))

    async def set_round(self, item_id: int, round_num: int) -> None:
        await self._set_col(item_id, COL["round"], json.dumps(round_num))

    async def set_claude_questions(self, item_id: int, text: str) -> None:
        await self._set_col(item_id, COL["claude_questions"],
                            json.dumps({"text": text}))

    async def set_conventions_extracted(self, item_id: int, count: int) -> None:
        await self._set_col(item_id, COL["conventions_extracted"],
                            json.dumps(count))

    async def set_date(self, item_id: int, col_key: str, when: date) -> None:
        await self._set_col(item_id, COL[col_key],
                            json.dumps({"date": when.strftime("%Y-%m-%d")}))

    async def set_protocol_number(self, item_id: int, protocol: str) -> None:
        await self._set_col(item_id, COL["protocol_number"],
                            json.dumps(protocol))

    async def _set_col(self, item_id: int, col_id: str, value_json: str) -> None:
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

    async def upload_file_to_column(
        self, item_id: int, col_key: str, filename: str, content: bytes
    ) -> str:
        col_id        = COL[col_key]
        mutation_query = (
            "mutation ($file: File!) {"
            f' add_file_to_column(item_id: {item_id}, '
            f'column_id: "{col_id}", file: $file) {{ id }} '
            "}"
        )
        token = self._resolve_token()
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                MONDAY_FILE_URL,
                headers={"Authorization": token, "API-Version": "2023-10"},
                files={
                    "query":     (None, mutation_query),
                    "variables": (None, '{"file": null}'),
                    "map":       (None, '{"file": ["variables.file"]}'),
                    "file":      (filename, content, "application/octet-stream"),
                },
            )
        if r.status_code != 200:
            raise RuntimeError(f"File upload HTTP {r.status_code}: {r.text[:300]}")
        body = r.json() if r.content else {}
        if "errors" in body:
            raise RuntimeError(f"File upload errors: {body['errors']}")
        return str(body.get("data", {}).get("add_file_to_column", {}).get("id", ""))
