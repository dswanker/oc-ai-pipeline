"""
migration_pipeline.py — orchestrator for the EDC migration path.

Reads a Source EDC Export (ODM 1.3.x XML, optionally inside a ZIP) from an
AI Hub board row, runs it through:

    odm_validator  →  odm_reader  →  odm_to_spec

and uploads the resulting OC4 Study Spec JSON to the Protocol Specification
(JSON) file column on the same row. Once that JSON is in place, the rest
of pipeline.py (build / DVS / pricing / OC4 publish) consumes it just like
the PDF-derived spec.

Public entry point
──────────────────
    await run_migration(item_id, *, claude_client=None, ai_assist=False) -> dict

The returned dict has shape:

    {
        "status":           "ok" | "validation_failed" | "no_export" | "error",
        "summary":          str,                         # human one-liner
        "source_system":    str | None,                  # vendor detected
        "validation":       ValidationReport-as-dict | None,
        "stats":            {"events": N, "forms": N, "items": N, ...},
        "spec_json_bytes":  int | None,                  # size of JSON written
    }

Calling-side contract
─────────────────────
pipeline.py is expected to:
  1. Decide whether to run migration based on Source EDC Export column being
     populated (and, optionally, no Protocol PDF being present).
  2. Call run_migration(item_id, ...) and, on "ok", proceed with the normal
     downstream stages (it will find the spec JSON already populated).
  3. On "validation_failed" / "no_export" / "error", set the row's
     AI Pipeline Status appropriately and stop — migration does not touch
     that status itself, since pipeline.py owns end-to-end state.
"""

from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from typing import Any

import httpx

from monday_client import (
    BOARD_ID,
    COL,
    MONDAY_API_URL,
    append_log,
    download_column_file,
    get_headers,
    get_item,
    list_column_filenames,
    make_mutation,
    upload_file,
    _check_monday_response,
)

# odm_reader / odm_to_spec / odm_validator live in ./migration/ as a package.
# They import from each other via `from odm_reader import ...` (no package
# prefix), so we extend sys.path here rather than refactoring those imports.
import os as _os
_MIG_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "migration")
if _MIG_DIR not in sys.path:
    sys.path.insert(0, _MIG_DIR)

from odm_validator import validate_odm, format_report
from odm_reader import parse_odm_metadata
from odm_to_spec import transform, transform_with_ai
from gap_analysis import run_gap_analysis
from pathlib import Path as _Path
from trainer_integration import create_pending_row, trainer_enabled


def _load_migration_skill() -> str:
    """Load the migration-analysis SKILL.md content for use in AI prompts.

    The SKILL.md is the single source of truth for the ODM → OC4 mapping
    rules. Loading it here ensures the AI-assisted transform path uses the
    same rules as the documented skill — no drift between what the skill
    says and what the pipeline does.

    Returns empty string if the skill file is not found (falls back to
    transform_with_ai's built-in prompts).
    """
    skill_path = (_Path(__file__).resolve().parent
                  / "skills" / "user" / "migration-analysis" / "SKILL.md")
    field_map_path = (skill_path.parent / "references"
                      / "odm-to-oc4-field-mapping.md")
    parts = []
    for p in (skill_path, field_map_path):
        try:
            parts.append(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
    if parts:
        return "\n\n---\n\n".join(parts)
    return ""


# ── Migrations AI Hub board — second-board target for gap report + Syndeo ────
#
# This board is the long-lived per-study record. A study OID owns exactly one
# row here for its entire lifecycle; every subsequent run for the same source
# study UPDATES that row rather than creating a new one. The AI Study Hub
# board (BOARD_ID, defined in monday_client) is the per-pipeline-run board.

MIGRATIONS_HUB_BOARD_ID = 18414959764

# Column IDs confirmed against the live board. Treat this dict as the source
# of truth — any new column on the hub goes here, not scattered as literals.
MIGRATIONS_HUB_COLUMNS: dict[str, str] = {
    "study_oid":          "text_mm3qqcwc",
    "source_edc_system":  "dropdown_mm3qpxra",
    "source_odm_xml":     "file_mm3qcwxm",
    "target_oc4_xml":     "file_mm3qx4rk",
    "gap_report":         "file_mm3qcpnr",
    "syndeo_url":         "link_mm3qqgzk",
    "pipeline_status":    "color_mm3qby19",
    "last_pipeline_run":  "date_mm3qyxjq",
    "notes":              "long_text_mm3qq5vr",
}

MIGRATIONS_HUB_GROUPS: dict[str, str] = {
    "awaiting_build":     "topics",
    "in_flight":          "group_mm3q4xew",
    "testing":            "group_mm3qvfg1",
    "production":         "group_mm3qyjen",
    "complete":           "group_mm3qnzys",
}

# Syndeo UI fronts the gap report — URL pattern points at THIS board's row,
# not the AI Study Hub row. Syndeo fetches gap-report file content from the
# file column on the targeted row.
SYNDEO_URL_BASE = "https://mapping-ui-production.up.railway.app"


# ── ZIP unwrap ────────────────────────────────────────────────────────────────

ZIP_MAGIC = b"PK\x03\x04"


def _extract_odm_xml(raw: bytes, source_name: str = "") -> bytes:
    """
    Return ODM XML bytes from `raw`.

    If raw is already XML (starts with `<` or BOM+`<`), return as-is.
    If raw is a ZIP, extract the largest .xml entry (largest = most likely
    to be the metadata, not a tiny manifest). Raise ValueError if no XML
    found inside the ZIP or if the bytes are neither XML nor ZIP.
    """
    if not raw:
        raise ValueError("empty file")

    # XML BOM or plain XML start
    head = raw[:4].lstrip(b"\xef\xbb\xbf")
    if head.startswith(b"<"):
        return raw

    if raw[:4] == ZIP_MAGIC:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            xml_entries = [n for n in zf.namelist()
                           if n.lower().endswith(".xml") and not n.endswith("/")]
            if not xml_entries:
                raise ValueError(f"ZIP '{source_name}' contains no .xml file")
            # Pick the largest XML entry — for vendor exports that bundle a
            # tiny manifest + the real metadata XML, the larger one is the
            # ODM. Stable tiebreak by name.
            xml_entries.sort(key=lambda n: (-zf.getinfo(n).file_size, n))
            chosen = xml_entries[0]
            return zf.read(chosen)

    raise ValueError(
        f"'{source_name}' is neither XML nor ZIP "
        f"(first bytes: {raw[:8]!r})"
    )


# ── Dropdown helpers ──────────────────────────────────────────────────────────
# monday_client only exposes set_status (status column). Dropdowns use a
# different wire format: {"labels": ["Label"]} vs {"label": "Label"}.
# Kept local to this module rather than adding to monday_client.py since
# this is the only caller today.

async def _read_dropdown_value(item_id) -> list[str]:
    """Return the currently-selected labels on the Source EDC System dropdown."""
    item = await get_item(item_id)
    for cv in item.get("column_values", []) or []:
        if cv.get("id") != COL["source_edc_system"]:
            continue
        raw = cv.get("value")
        if not raw:
            return []
        try:
            val = json.loads(raw)
        except Exception:
            return []
        # Dropdown value shape: {"ids":[1,2], "changed_at":"..."}.
        # `text` field on the column_value already carries the resolved
        # label string (comma-separated for multi-select), which we prefer.
        text = (cv.get("text") or "").strip()
        if text:
            return [t.strip() for t in text.split(",") if t.strip()]
        return []
    return []


async def _set_dropdown_value(item_id, col_id, label: str) -> None:
    """Set a dropdown column to a single label by name."""
    val = json.dumps({"labels": [label]})
    variables = {"i": item_id, "b": BOARD_ID, "c": col_id, "v": val}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
                         json={"query": make_mutation(), "variables": variables})
    print(f"SET_DROPDOWN {col_id}={label}: {r.status_code}", flush=True)
    _check_monday_response(r, f"SET_DROPDOWN({col_id}={label})")


# ── Vendor → dropdown label mapping ───────────────────────────────────────────
# odm_reader._detect_vendor returns strings that *mostly* match dropdown
# labels exactly, but the OpenClinica case is split (3 vs 4) on the dropdown
# while the detector reports "OpenClinica" (3) or "OpenClinica 4". UNKNOWN
# is mapped to "Other".

_VENDOR_LABEL_MAP = {
    "Medidata Rave":      "Medidata Rave",
    "Oracle InForm":      "Oracle InForm",
    "Viedoc":             "Viedoc",
    "Castor EDC":         "Castor EDC",
    "REDCap":             "REDCap",
    "OpenClinica":        "OpenClinica 3",
    "OpenClinica 4":      "OpenClinica 4",
    "Zelta (Merative)":   "Zelta (Merative)",
    "Medrio":             "Medrio",
    "Veeva Vault CDMS":   "Veeva Vault CDMS",
    "UNKNOWN":            "Other",
}


def _vendor_to_label(source_system: str) -> str:
    return _VENDOR_LABEL_MAP.get(source_system, "Other")


# ── Filename helpers ──────────────────────────────────────────────────────────

def _safe_slug(s: str) -> str:
    """Filename-safe slug: alphanumerics and `_-` only, max 64 chars."""
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", (s or "").strip())
    s = s.strip("_-") or "study"
    return s[:64]


def _spec_json_filename(spec_json: dict) -> str:
    """
    Match the naming convention pipeline.py uses for the spec JSON file:
        {protocol_num}_Study_Specification_{version}.json
    Falls back to "Migrated_Study_Specification_v1.json" if metadata absent.
    """
    sm = spec_json.get("study_meta", {}) or {}
    protocol = sm.get("protocol_number") or sm.get("protocol") or "Migrated"
    version  = sm.get("protocol_version") or sm.get("version") or "v1"
    return f"{_safe_slug(protocol)}_Study_Specification_{_safe_slug(version)}.json"


# ── Migrations AI Hub: row search / create / update ──────────────────────────

async def _find_migrations_hub_row(study_oid: str) -> str | None:
    """Search the Migrations AI Hub for an existing row with this study OID.

    Returns the item_id (string) of the first match, or None if no row
    exists yet. Uses Monday's items_page_by_column_values which scales
    to large boards better than fetching all rows. Fails open (returns
    None) on any API hiccup — caller's upsert logic will fall through
    to create, which is the right behavior if our search couldn't see
    a row that does exist (worst case: a duplicate that the operator
    resolves manually, vs. a crash blocking the build).
    """
    if not study_oid:
        return None
    query = """
    query ($board_id: ID!, $col: String!, $val: [String]!) {
      items_page_by_column_values(
        board_id: $board_id,
        columns: [{column_id: $col, column_values: $val}],
        limit: 5
      ) {
        items { id name }
      }
    }
    """
    variables = {
        "board_id": MIGRATIONS_HUB_BOARD_ID,
        "col":      MIGRATIONS_HUB_COLUMNS["study_oid"],
        "val":      [study_oid],
    }
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(MONDAY_API_URL, headers=get_headers(),
                             json={"query": query, "variables": variables})
        if r.status_code != 200:
            print(f"[migrations-hub] search HTTP {r.status_code}: "
                  f"{r.text[:200]}", flush=True)
            return None
        body = r.json()
        items = (body.get("data") or {}).get(
            "items_page_by_column_values", {}
        ).get("items") or []
        if items:
            return str(items[0]["id"])
        return None
    except Exception as e:
        print(f"[migrations-hub] search failed: {e}", flush=True)
        return None


async def _create_migrations_hub_row(
    study_oid: str, source_system: str,
) -> str | None:
    """Create a new row in the Migrations Hub's awaiting_build group.

    Item name = study OID (operator-readable handle). Pre-populates the
    study_oid text column and source_edc_system dropdown so the row is
    immediately recognizable in the board UI. Returns the new item_id
    or None on failure (caller skips the rest of the upsert silently)."""
    name = study_oid or "MIGRATED_STUDY"
    column_values = {
        MIGRATIONS_HUB_COLUMNS["study_oid"]: study_oid or "",
    }
    if source_system:
        column_values[MIGRATIONS_HUB_COLUMNS["source_edc_system"]] = {
            "labels": [source_system],
        }
    query = """
    mutation ($b: ID!, $g: String!, $n: String!, $cv: JSON!) {
      create_item(board_id: $b, group_id: $g, item_name: $n,
                  column_values: $cv) { id }
    }
    """
    variables = {
        "b":  MIGRATIONS_HUB_BOARD_ID,
        "g":  MIGRATIONS_HUB_GROUPS["awaiting_build"],
        "n":  name,
        "cv": json.dumps(column_values),
    }
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(MONDAY_API_URL, headers=get_headers(),
                             json={"query": query, "variables": variables})
        _check_monday_response(r, "MIGRATIONS_HUB_CREATE")
        new_id = (r.json().get("data") or {}).get(
            "create_item", {}).get("id")
        print(f"[migrations-hub] created row {new_id} for study "
              f"OID={study_oid!r} in group=awaiting_build", flush=True)
        return str(new_id) if new_id else None
    except Exception as e:
        print(f"[migrations-hub] create failed: {e}", flush=True)
        return None


async def _update_migrations_hub_row(
    row_id: str, column_values: dict,
) -> None:
    """Apply a multi-column update in one mutation. column_values is the
    Monday wire-format dict (string column → string label, link column →
    {"url", "text"}, date column → {"date"}, etc.). No-op if row_id is
    falsy or column_values is empty."""
    if not row_id or not column_values:
        return
    query = """
    mutation ($b: ID!, $i: ID!, $cv: JSON!) {
      change_multiple_column_values(
        board_id: $b, item_id: $i, column_values: $cv) { id }
    }
    """
    variables = {
        "b":  MIGRATIONS_HUB_BOARD_ID,
        "i":  row_id,
        "cv": json.dumps(column_values),
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
                         json={"query": query, "variables": variables})
    _check_monday_response(r, "MIGRATIONS_HUB_UPDATE")


async def _upload_to_migrations_hub_file_col(
    row_id: str, col_id: str, filename: str, content: bytes,
) -> None:
    """Upload `content` as `filename` to a file column on the hub board.

    Reuses the project's upload_file helper but targets the hub board's
    row instead of the AI Study Hub row. upload_file takes item_id +
    column_id; the board_id is implicit per-mutation."""
    await upload_file(row_id, col_id, filename, content)


async def run_gap_analysis_and_hub_upsert(
    item_id,
    odm_metadata: dict,
    spec_json: dict,
    source_system: str,
    source_odm_bytes: bytes | None = None,
    source_odm_filename: str | None = None,
) -> dict | None:
    """Non-blocking post-build hook: run gap analysis, upsert the
    Migrations AI Hub row, upload the gap report, write the Syndeo URL.

    Designed to never raise — every step is wrapped in try/except and
    the build's success status is unaffected by failures here. Returns
    the gap report dict on success (so callers can log/inspect) or
    None on any failure.

    Idempotency: re-running for the same study OID updates the existing
    hub row (uses MIGRATIONS_HUB_COLUMNS["study_oid"] as the dedup key).
    The gap report file column is overwritten on every run — the JSON
    carries `generated_at` so history can be reconstructed from prior
    monday revisions if needed.
    """
    import datetime as _dt

    try:
        report = run_gap_analysis(odm_metadata, spec_json, source_system)
    except Exception as e:
        print(f"[gap-analysis] generation failed (non-fatal): "
              f"{type(e).__name__}: {e}", flush=True)
        try:
            await append_log(
                item_id,
                f"Gap analysis failed (non-fatal): "
                f"{type(e).__name__}: {e}",
            )
        except Exception:
            pass
        return None

    study_oid = report.get("source_study_oid") or ""
    summary   = report.get("summary", {}) or {}
    print(f"[gap-analysis] generated report for study_oid={study_oid!r}: "
          f"total={summary.get('total')} clean={summary.get('clean')} "
          f"warning={summary.get('warning')} "
          f"data_loss_risk={summary.get('data_loss_risk')} "
          f"blocking={summary.get('blocking')} "
          f"unmapped={summary.get('unmapped')}", flush=True)

    # ── Upsert row on Migrations AI Hub ────────────────────────────────
    hub_row_id = await _find_migrations_hub_row(study_oid)
    if hub_row_id is None:
        hub_row_id = await _create_migrations_hub_row(study_oid, source_system)
    else:
        print(f"[migrations-hub] reusing existing row {hub_row_id} for "
              f"study_oid={study_oid!r}", flush=True)

    if not hub_row_id:
        print("[migrations-hub] no row available — skipping uploads "
              "(report still generated and logged above)", flush=True)
        return report

    # Status + date + Syndeo URL go in a single multi-column update.
    syndeo_url = f"{SYNDEO_URL_BASE}?item_id={hub_row_id}"
    today = _dt.date.today().isoformat()
    hub_updates: dict[str, Any] = {
        MIGRATIONS_HUB_COLUMNS["pipeline_status"]: {
            "label": "Gap Analysis Complete",
        },
        MIGRATIONS_HUB_COLUMNS["last_pipeline_run"]: {"date": today},
        MIGRATIONS_HUB_COLUMNS["syndeo_url"]: {
            "url": syndeo_url, "text": "Open in Syndeo",
        },
    }
    if source_system:
        hub_updates[MIGRATIONS_HUB_COLUMNS["source_edc_system"]] = {
            "labels": [source_system],
        }
    try:
        await _update_migrations_hub_row(hub_row_id, hub_updates)
    except Exception as e:
        print(f"[migrations-hub] column update failed (non-fatal): "
              f"{e}", flush=True)

    # Upload the gap report JSON. Overwrites prior versions for this row.
    try:
        gap_filename = (
            f"{study_oid or 'Migrated'}_Gap_Report_"
            f"{report['generated_at'][:10]}.json"
        )
        gap_bytes = json.dumps(report, indent=2, default=str).encode("utf-8")
        await _upload_to_migrations_hub_file_col(
            hub_row_id,
            MIGRATIONS_HUB_COLUMNS["gap_report"],
            gap_filename,
            gap_bytes,
        )
        print(f"[migrations-hub] uploaded {gap_filename} "
              f"({len(gap_bytes)} bytes) → row {hub_row_id}", flush=True)
    except Exception as e:
        print(f"[migrations-hub] gap-report upload failed (non-fatal): "
              f"{e}", flush=True)

    # Optionally mirror the source ODM XML onto the hub row — only on
    # the FIRST run for this study OID, since the source is immutable
    # and re-uploading on every run wastes Monday storage. We detect
    # "first run" as "the file column is currently empty".
    if source_odm_bytes:
        try:
            existing = await list_column_filenames(
                hub_row_id, MIGRATIONS_HUB_COLUMNS["source_odm_xml"],
            )
            if not existing:
                fname = source_odm_filename or f"{study_oid or 'source'}.xml"
                await _upload_to_migrations_hub_file_col(
                    hub_row_id,
                    MIGRATIONS_HUB_COLUMNS["source_odm_xml"],
                    fname, source_odm_bytes,
                )
                print(f"[migrations-hub] mirrored source ODM {fname} "
                      f"({len(source_odm_bytes)} bytes) → row "
                      f"{hub_row_id}", flush=True)
        except Exception as e:
            print(f"[migrations-hub] source-ODM mirror failed (non-fatal): "
                  f"{e}", flush=True)

    try:
        await append_log(
            item_id,
            f"Gap analysis complete: "
            f"{summary.get('clean', 0)} clean, "
            f"{summary.get('warning', 0)} warning, "
            f"{summary.get('data_loss_risk', 0)} data-loss-risk, "
            f"{summary.get('blocking', 0)} blocking, "
            f"{summary.get('unmapped', 0)} unmapped. "
            f"Open in Syndeo: {syndeo_url}",
        )
    except Exception:
        pass

    return report


# ── Public entry point ───────────────────────────────────────────────────────

async def run_migration(
    item_id,
    *,
    raw_bytes: bytes | None = None,
    protocol_bytes: bytes | None = None,
    claude_client: Any = None,
    ai_assist: bool = False,
) -> dict:
    """
    Run the EDC migration path for a single AI Hub row.

    Parameters
    ----------
    item_id        : monday.com item id (int or str).
    raw_bytes      : optional pre-downloaded Source EDC Export bytes. When
                     provided, skips the Monday download step. Callers that
                     already fetch the bytes (e.g. pipeline.py's parallel
                     input gather) should pass them in.
    protocol_bytes : optional pre-downloaded protocol document bytes. When
                     present (or when COL["protocol"] yields a file), Path M runs
                     in "ODM+Protocol enrichment mode (AI-assisted)" — the
                     deterministic ODM transform is enriched by Claude with
                     protocol-derived study_meta, eligibility constraints,
                     and clinical context. When absent, Path M runs in
                     "ODM-only mode" (pure deterministic transform).
    claude_client  : optional claude client (module or object with an async
                     call_claude(prompt, pdf_bytes=None, extra_text=None)
                     callable). Defaults to the project's claude_client
                     module when enrichment mode is selected.
    ai_assist      : legacy toggle for AI-assist without a protocol PDF.
                     Protocol-driven enrichment auto-engages whenever
                     protocol_bytes are present and is preferred.

    Trainer corpus
    --------------
    After a successful migration this function creates a "Pending PS
    Review" row on the trainer corpus board with the source ODM XML
    attached. Gated only on ``trainer_enabled()`` (TRAINER_URL set in
    env) — no per-row Monday toggle. Dedup on the trainer side prevents
    duplicates.

    Returns
    -------
    dict (see module docstring for shape).
    """
    print(f"[MIGRATION] starting for item {item_id}", flush=True)

    # 1. Acquire the source export bytes — caller may pass them in to avoid
    # a redundant Monday download.
    raw = raw_bytes if raw_bytes is not None else \
          await download_column_file(item_id, COL["source_edc_export"])
    if not raw:
        msg = "No Source EDC Export file uploaded on this row"
        print(f"[MIGRATION] {msg}", flush=True)
        await append_log(item_id, f"Migration: {msg}")
        return {
            "status": "no_export", "summary": msg,
            "source_system": None, "validation": None,
            "stats": {}, "spec_json_bytes": None,
        }

    filenames = await list_column_filenames(item_id, COL["source_edc_export"])
    source_name = filenames[-1] if filenames else "source_edc_export"
    print(f"[MIGRATION] downloaded {len(raw)} bytes from '{source_name}'", flush=True)

    # 2. Unwrap if ZIP
    try:
        xml_bytes = _extract_odm_xml(raw, source_name)
    except (ValueError, zipfile.BadZipFile) as e:
        msg = f"Could not extract ODM XML: {e}"
        print(f"[MIGRATION] {msg}", flush=True)
        await append_log(item_id, f"Migration FAIL: {msg}")
        return {
            "status": "error", "summary": msg,
            "source_system": None, "validation": None,
            "stats": {}, "spec_json_bytes": None,
        }

    # 3. Validate
    report = validate_odm(xml_bytes)
    print(f"[MIGRATION] validation: passed={report.passed} "
          f"can_proceed={report.can_proceed} odm_version={report.odm_version}",
          flush=True)
    if not report.can_proceed:
        await append_log(item_id,
                         "Migration FAIL: ODM validation blocked migration.\n"
                         + format_report(report))
        return {
            "status": "validation_failed",
            "summary": f"ODM validation failed: {report.summary}",
            "source_system": None,
            "validation": _report_to_dict(report),
            "stats": report.stats or {},
            "spec_json_bytes": None,
        }

    # 4. Parse
    odm_study = parse_odm_metadata(xml_bytes)
    source_system = odm_study.get("source_system") or "UNKNOWN"
    print(f"[MIGRATION] parsed ODM: source_system={source_system!r}, "
          f"events={len(odm_study.get('events', []))}, "
          f"forms={len(odm_study.get('forms', []))}, "
          f"items={len(odm_study.get('items', []))}",
          flush=True)

    # 5. Auto-populate Source EDC System dropdown if empty
    try:
        current_labels = await _read_dropdown_value(item_id)
        if not current_labels:
            label = _vendor_to_label(source_system)
            await _set_dropdown_value(item_id, COL["source_edc_system"], label)
            print(f"[MIGRATION] auto-set Source EDC System → {label}", flush=True)
        else:
            print(f"[MIGRATION] Source EDC System already set: "
                  f"{current_labels} — not overwriting", flush=True)
    except Exception as e:
        # Non-fatal — log and continue. The spec JSON is the load-bearing
        # output; the dropdown is metadata for human review.
        print(f"[MIGRATION] dropdown auto-populate failed (non-fatal): {e}",
              flush=True)
        await append_log(item_id, f"Migration warning: dropdown auto-populate failed: {e}")

    # 6. Transform → Study Spec JSON
    # If the caller did not pre-fetch the protocol document, try Monday now —
    # this lets run_migration be used standalone (e.g. from the CLI) and
    # still pick up enrichment context when a protocol is attached.
    if protocol_bytes is None:
        try:
            protocol_bytes = await download_column_file(item_id, COL["protocol"])
        except Exception as e:
            print(f"[MIGRATION] protocol document fetch failed (non-fatal): {e}",
                  flush=True)
            protocol_bytes = None

    # Load the migration-analysis skill rules — single source of truth for
    # the ODM → OC4 mapping logic. Passed to transform_with_ai so the AI
    # uses the same rules as the documented skill (no drift).
    _skill_content = _load_migration_skill()
    if _skill_content:
        print(f"[MIGRATION] migration-analysis skill loaded "
              f"({len(_skill_content)} chars)", flush=True)
    else:
        print("[MIGRATION] migration-analysis skill not found — "
              "using transform_with_ai built-in prompts", flush=True)

    use_enrichment = bool(protocol_bytes) or ai_assist
    if use_enrichment:
        mode_label = ("ODM+Protocol enrichment mode (AI-assisted)"
                      if protocol_bytes else "ODM-only AI-assist mode")
        print(f"[MIGRATION] Path M: {mode_label}", flush=True)
        if claude_client is None:
            import claude_client as _cc_mod  # default to the project module
            claude_client = _cc_mod
        spec_json = await transform_with_ai(
            odm_study, claude_client, protocol_bytes=protocol_bytes,
            source_system=source_system,
            skill_content=_skill_content or None,
        )
    else:
        print(f"[MIGRATION] Path M: ODM-only mode", flush=True)
        spec_json = transform(odm_study)

    # 7. Upload Study Spec JSON to the row
    spec_bytes = json.dumps(spec_json, indent=2).encode("utf-8")
    filename = _spec_json_filename(spec_json)
    await upload_file(item_id, COL["spec_json"], filename, spec_bytes)
    print(f"[MIGRATION] uploaded {filename} ({len(spec_bytes)} bytes) → "
          f"COL['spec_json']", flush=True)

    summary = (f"Migrated {source_system} export → Study Spec JSON "
               f"({len(odm_study.get('forms', []))} forms, "
               f"{len(odm_study.get('items', []))} items)")
    await append_log(item_id, f"Migration OK: {summary}")

    # Mapping review URL is written by pipeline.py after struct_json is
    # confirmed — fires for both Path B and Path M.

    # ── Trainer: create pending corpus row on Path-M completion ──────────
    # Mirrors pipeline.py's Path-B block (best-effort, never blocks).
    # Fires unconditionally on every successful migration when the
    # trainer is wired up (TRAINER_URL set). Path M's dedup key on the
    # trainer side is (source_system, dedup_key) where dedup_key is the
    # ODM protocol_number when present, otherwise the ODM study_oid.
    if trainer_enabled():
        try:
            sm = spec_json.get("study_meta", {}) or {}
            protocol_num = (sm.get("protocol_number")
                            or sm.get("study_title")
                            or f"MIG-{item_id}")
            dedup_key = (sm.get("protocol_number")
                         or sm.get("study_id")
                         or odm_study.get("study", {}).get("oid")
                         or "")
            print(f"[trainer] Path M: creating pending row name={protocol_num!r} "
                  f"source_system={source_system!r} dedup_key={dedup_key!r}",
                  flush=True)
            new_trainer_item_id = await create_pending_row(
                protocol_pdf=protocol_bytes if protocol_bytes else None,
                protocol_filename=f"{protocol_num}.pdf",
                odm_xml=raw,
                odm_xml_filename=f"{protocol_num}_source.xml",
                name=protocol_num,
                source_system=source_system,
                path="migration",
                ingest_status_key="pending_ps_review",
                protocol_number=dedup_key or None,
                source_pipeline_item=str(item_id),
                study_spec_json=spec_bytes,
            )
            if new_trainer_item_id:
                await append_log(
                    item_id,
                    f"Trainer pending row created: item_id={new_trainer_item_id}",
                )
        except Exception as _trainer_exc:  # noqa: BLE001
            print(f"[trainer] Path M create_pending_row failed: "
                  f"{_trainer_exc} — continuing without trainer row",
                  flush=True)

    return {
        "status": "ok",
        "summary": summary,
        "source_system": source_system,
        "validation": _report_to_dict(report),
        "stats": {
            "events":      len(odm_study.get("events", [])),
            "forms":       len(odm_study.get("forms", [])),
            "item_groups": len(odm_study.get("item_groups", [])),
            "items":       len(odm_study.get("items", [])),
            "codelists":   len(odm_study.get("codelists", [])),
        },
        "spec_json_bytes": len(spec_bytes),
        # Post-build hooks (gap analysis, Migrations Hub upsert) need the
        # parsed ODM dict and the raw bytes. Carry them in the result
        # rather than re-parsing in pipeline.py.
        "odm_metadata":    odm_study,
        "spec_json":       spec_json,
        "source_odm_bytes":    raw,
        "source_odm_filename": source_name,
    }


def _report_to_dict(report) -> dict:
    """Serialise a ValidationReport for inclusion in the result dict."""
    return {
        "passed":        report.passed,
        "can_proceed":   report.can_proceed,
        "summary":       report.summary,
        "odm_version":   report.odm_version,
        "layer_results": dict(report.layer_results or {}),
        "compliance":    dict(report.compliance or {}),
        "stats":         dict(report.stats or {}),
    }
