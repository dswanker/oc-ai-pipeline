"""
uat_loader.py — UAT Data Loader for oc-ai-pipeline
====================================================
Triggered when the "Load DVS UAT Data" checkbox fires on the AI Hub board.

Workflow
--------
1.  Read Study UUID + OC Subdomain from monday.com item
2.  Fetch the DVS XLSX from the dvs_output column
3.  GET /api/studies/{studyUuid}/study-environments → find TEST env UUID
4.  Create a dated site in the TEST environment
5.  Parse UAT_Cases sheet → group rows by Participant_ID
6.  Create one OC participant per unique Participant_ID
7.  Build ODM XML from UAT_Cases load coordinates + Load_Value
8.  POST ODM to /clinicaldata/import for each participant
9.  Write Site_OID + Participant_Key back into the DVS XLSX
10. Upload result DVS to file_mm3h5s3h (UAT DVS Results column)
11. Update monday status columns

Site naming convention (locked in Step 0)
------------------------------------------
  Site Name: "UAT Automation Site - YYYY-MM-DD HH:MM"
  Site OID:  "UAT-YYYYMMDD-HHMMSS"

Participant naming convention
------------------------------
  Logical ID in DVS:   UAT-P001, UAT-P002, ...
  Run-scoped key:      UAT-YYYYMMDD-HHMMSS-P001
"""

import asyncio
import datetime
import io
import json
import os
import traceback
from pathlib import Path

import httpx
from openpyxl import load_workbook

from monday_client import (
    get_item, download_column_file, upload_file,
    append_log, set_status, COL,
)

# Playwright storage_state JSONs live here (same path as auth_manager.py)
_SESSIONS_DIR = Path("/data/browser_sessions")


def _load_cookies(email: str) -> dict:
    """
    Load Playwright storage_state JSON for email and return a flat
    {name: value} dict of cookies suitable for httpx.
    Returns empty dict if no session file exists.
    """
    session_path = _SESSIONS_DIR / f"{email}.json"
    if not session_path.exists():
        return {}
    try:
        with open(session_path) as f:
            state = json.load(f)
        cookies = state.get("cookies") or []
        return {c["name"]: c["value"] for c in cookies if c.get("name")}
    except Exception as e:
        print(f"[uat_loader] cookie load failed for {email}: {e}", flush=True)
        return {}


def _session_has_clinical_cookies(email: str, clinical_host: str) -> bool:
    """
    Check whether the saved session contains cookies for the clinical host.
    e.g. clinical_host = 'cust1.eu.openclinica.io'
    Returns True if at least one cookie domain matches.
    """
    session_path = _SESSIONS_DIR / f"{email}.json"
    if not session_path.exists():
        return False
    try:
        with open(session_path) as f:
            state = json.load(f)
        cookies = state.get("cookies") or []
        return any(
            clinical_host in (c.get("domain") or "")
            for c in cookies
        )
    except Exception:
        return False


def _generate_auth_link(email: str, context: str = "pipeline") -> str:
    """Generate a fresh OC auth link using AuthManager."""
    import os as _os
    from auth_manager import AuthManager
    base_url = _os.environ.get(
        "RAILWAY_PUBLIC_DOMAIN",
        "oc-ai-pipeline-production.up.railway.app"
    )
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    return AuthManager().generate_auth_link(email, base_url, context)

# ── Constants ─────────────────────────────────────────────────────────────────

UAT_STATUS = {
    "loading":  "Loading UAT Data",
    "complete": "UAT Data Loaded",
    "failed":   "UAT Load Failed",
}

# Column IDs for UAT output files on the AI Hub board
UAT_DVS_RESULTS_COL  = "file_mm3h5s3h"   # Updated DVS with runtime columns stamped
UAT_REPORT_COL       = "file_mm3hvbpb"   # UAT Validation Report (future)
UAT_MATRIX_COL       = "file_mm3h7r4"    # UAT Traceability Matrix (future)

ODM_NAMESPACE = (
    'xmlns="http://www.cdisc.org/ns/odm/v1.3" '
    'xmlns:OpenClinica="http://www.openclinica.com/ns/odm_ext_v130/v3.1"'
)


# ── Study Service API helpers ─────────────────────────────────────────────────

def _study_service_base(subdomain: str) -> str:
    return f"https://{subdomain}.build.openclinica.io/study-service"


def _pages_base(subdomain: str) -> str:
    """
    Return the base URL for clinical data (participant + ODM) API calls.
    Reads bridge_url from customer_uuids.csv keyed by subdomain.
    e.g. cust1 -> https://cust1.eu.openclinica.io/OpenClinica
    Falls back to build host if not found (will likely 405 but keeps old behavior).
    """
    csv_path = Path(__file__).parent / "references" / "customer_uuids.csv"
    if csv_path.exists():
        import csv as _csv
        with open(csv_path, newline="") as f:
            for row in _csv.DictReader(f):
                if row.get("subdomain", "").lower() == subdomain.lower():
                    bridge = row.get("bridge_url", "").rstrip("/")
                    if bridge:
                        return bridge  # e.g. https://cust1.eu.openclinica.io/OpenClinica
    # Fallback
    return f"https://{subdomain}.build.openclinica.io"


async def _get_oc_token(subdomain: str) -> str:
    """Fetch a short-lived OC OAuth bearer token for study-service API calls.
    Matches the pattern used by pipeline.py _get_oc_token exactly.
    """
    import os
    username = os.environ.get("OC_API_USERNAME", "").strip()
    password = os.environ.get("OC_API_PASSWORD", "").strip()
    if not username or not password:
        raise ValueError("OC_API_USERNAME or OC_API_PASSWORD not set in env")
    url = (f"https://{subdomain}.build.openclinica.io"
           f"/user-service/api/oauth/token")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url,
                                 headers={"Content-Type": "application/json"},
                                 json={"username": username,
                                       "password": password})
    if resp.status_code != 200:
        raise RuntimeError(
            f"OC auth failed {resp.status_code}: {resp.text[:200]}")
    return resp.text.strip()


async def _get_test_env_uuid(subdomain: str, study_uuid: str,
                              cookies: dict) -> tuple:
    """
    GET /api/studies/{studyUuid}/study-environments
    Returns (test_env_uuid, test_study_oid) for environmentName == 'TEST'.
    Raises ValueError if TEST environment not found.
    Uses Bearer token (not cookies) — study-service requires OAuth.
    """
    url = (f"{_study_service_base(subdomain)}/api/studies"
           f"/{study_uuid}/study-environments")
    token = await _get_oc_token(subdomain)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url,
                                headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        envs = resp.json()

    for env in envs:
        if (env.get("environmentName") or "").upper() == "TEST":
            return env["uuid"], env.get("oid", "")

    names = [e.get("environmentName") for e in envs]
    raise ValueError(
        f"TEST environment not found for study {study_uuid}. "
        f"Found: {names}"
    )


async def _create_site(subdomain: str, test_env_uuid: str,
                        site_name: str, site_oid: str,
                        cookies: dict) -> str:
    """
    POST /api/study-environments/{studyEnvironmentUuid}/sites
    Returns the created site OID.
    Uses Bearer token (not cookies) — study-service requires OAuth.
    """
    url = (f"{_study_service_base(subdomain)}/api/study-environments"
           f"/{test_env_uuid}/sites")
    token = await _get_oc_token(subdomain)
    today = datetime.date.today().isoformat()
    payload = {
        "name":                  site_name,
        "uniqueIdentifier":      site_oid,
        "oid":                   site_oid,
        "status":                "AVAILABLE",
        "principalInvestigator": "UAT Automation",
        "expectedEnrollment":    999,
        "timezone":              "America/New_York",
        "expectedStartDate":     today,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload,
                                 headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()
    return data.get("oid") or data.get("uniqueIdentifier") or site_oid


async def _set_env_available(subdomain: str, test_env_uuid: str,
                              test_env_oid: str, token: str) -> None:
    """
    PUT /api/study-environments — set TEST environment status to AVAILABLE.
    Required before participants can enroll on the EU OC instance.
    """
    url = f"{_study_service_base(subdomain)}/api/study-environments"
    payload = {
        "uuid":   test_env_uuid,
        "status": "AVAILABLE",
        "oid":    test_env_oid,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(url, json=payload,
                                headers={"Authorization": f"Bearer {token}"})
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Set env AVAILABLE failed {resp.status_code}: {resp.text[:200]}"
            )


async def _create_participant(subdomain: str, study_oid: str,
                               site_oid: str, subject_key: str,
                               token: str, cookies: dict) -> str:
    """
    POST /pages/auth/api/clinicaldata/studies/{studyOid}/sites/{siteOid}/participants
    Path confirmed 401 (auth required) on eu host — uses cookie session auth.
    Bearer token kept as fallback header.
    """
    import json as _json
    from urllib.parse import quote as _quote
    url = (f"{_pages_base(subdomain)}/pages/auth/api/clinicaldata"
           f"/studies/{_quote(study_oid, safe='')}"
           f"/sites/{_quote(site_oid, safe='')}/participants")
    async with httpx.AsyncClient(timeout=30, cookies=cookies) as client:
        resp = await client.post(
            url,
            content=_json.dumps({"subjectKey": subject_key}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
        )
        if not resp.is_success:
            raise RuntimeError(
                f"HTTP {resp.status_code} — body: {resp.text[:300]}"
            )
    try:
        return resp.json().get("subjectKey") or subject_key
    except Exception:
        return subject_key


# ── DVS parsing ───────────────────────────────────────────────────────────────

def _parse_uat_cases(dvs_bytes: bytes) -> list:
    """
    Read UAT_Cases sheet from DVS XLSX.
    Returns list of dicts; only rows with Study_Event_OID + Form_OID populated.
    """
    wb = load_workbook(io.BytesIO(dvs_bytes), read_only=True, data_only=True)
    if "UAT_Cases" not in wb.sheetnames:
        raise ValueError("DVS XLSX does not contain a UAT_Cases sheet.")
    ws = wb["UAT_Cases"]

    header_row_idx = None
    headers = []
    rows = []

    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        vals = [str(c).strip() if c is not None else "" for c in row]
        if header_row_idx is None:
            if vals and vals[0] == "UAT Case ID":
                header_row_idx = row_idx
                headers = vals
            continue
        if not any(vals):
            continue
        row_dict = dict(zip(headers, vals))
        if not row_dict.get("Study_Event_OID", "").strip():
            continue
        if not row_dict.get("Form_OID", "").strip():
            continue
        rows.append(row_dict)

    wb.close()
    return rows


def _group_by_participant(rows: list) -> dict:
    """Group UAT_Cases rows by Participant_ID, sorted by Load_Order."""
    groups = {}
    for row in rows:
        pid = row.get("Participant_ID", "UAT-P001").strip() or "UAT-P001"
        groups.setdefault(pid, []).append(row)
    for pid in groups:
        groups[pid].sort(key=lambda r: _safe_int(r.get("Load_Order", "999")))
    return groups


def _safe_int(val, default=999) -> int:
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return default


# ── ODM XML builder ───────────────────────────────────────────────────────────

def _xml_escape(val: str) -> str:
    return (val.replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;")
               .replace('"', "&quot;"))


def _build_odm_xml(study_oid: str, site_oid: str,
                   participant_key: str, rows: list) -> str:
    """Build ODM XML for one participant's data rows."""
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    # Tree: events[ev_oid][repeat_key][form_oid][ig_oid] = [(item_oid, val)]
    events = {}
    for row in rows:
        ev  = row.get("Study_Event_OID", "").strip()
        rk  = row.get("Event_Repeat_Key", "1").strip() or "1"
        fo  = row.get("Form_OID", "").strip()
        ig  = row.get("Item_Group_OID", "").strip()
        val = row.get("Load_Value", "").strip()
        item_oid = ig  # item OID = itemgroup OID as best proxy until DVS adds dedicated column
        if not ev or not fo or not ig or not val:
            continue
        (events
         .setdefault(ev, {})
         .setdefault(rk, {})
         .setdefault(fo, {})
         .setdefault(ig, [])
         .append((item_oid, val)))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<ODM {ODM_NAMESPACE}',
        f'    FileType="Transactional" FileOID="UAT-{now}" CreationDateTime="{now}">',
        f'  <ClinicalData StudyOID="{study_oid}" MetaDataVersionOID="v1">',
        f'    <SubjectData SubjectKey="{participant_key}">',
        f'      <SiteRef LocationOID="{site_oid}"/>',
    ]
    for ev_oid, repeats in events.items():
        for repeat_key, forms in repeats.items():
            lines.append(
                f'      <StudyEventData StudyEventOID="{ev_oid}" '
                f'StudyEventRepeatKey="{repeat_key}">'
            )
            for form_oid, igs in forms.items():
                lines.append(
                    f'        <FormData FormOID="{form_oid}" '
                    f'TransactionType="Insert">'
                )
                for ig_oid, items in igs.items():
                    lines.append(
                        f'          <ItemGroupData ItemGroupOID="{ig_oid}" '
                        f'TransactionType="Insert">'
                    )
                    for item_oid, val in items:
                        lines.append(
                            f'            <ItemData ItemOID="{item_oid}" '
                            f'Value="{_xml_escape(val)}"/>'
                        )
                    lines.append('          </ItemGroupData>')
                lines.append('        </FormData>')
            lines.append('      </StudyEventData>')
    lines += ['    </SubjectData>', '  </ClinicalData>', '</ODM>']
    return "\n".join(lines)


async def _import_odm(subdomain: str, study_oid: str,
                       odm_xml: str, cookies: dict) -> dict:
    """POST ODM XML as multipart form to /OpenClinica/ImportCRFData.
    This is the same endpoint used by the OC4 UI import screen.
    Sends as multipart/form-data with 'uploadFile' field + runLogic checkbox.
    """
    url = f"{_pages_base(subdomain)}/ImportCRFData"
    files = {
        "uploadFile": ("import.xml", odm_xml.encode("utf-8"), "text/xml"),
    }
    data = {
        "action":   "confirm",
        "runLogic": "true",
    }
    async with httpx.AsyncClient(timeout=60, cookies=cookies,
                                 follow_redirects=True) as client:
        resp = await client.post(url, files=files, data=data)
        if not resp.is_success:
            raise RuntimeError(
                f"ODM import HTTP {resp.status_code} at {url} — "
                f"body: {resp.text[:400]}"
            )
        # Log the final URL after redirect following — if it's SSO login,
        # the cookies aren't working for this endpoint
        final_url = str(resp.url)
        if "sso/login" in final_url or "login" in final_url.lower():
            raise RuntimeError(
                f"ODM import redirected to login page ({final_url}) — "
                f"EU session cookies not valid for ImportCRFData. "
                f"Need Option A extension fix."
            )
        # (not JS comments which also contain "error")
        body = resp.text
        body_lower = body.lower()
        error_phrases = [
            "import failed",
            "validation error",
            "invalid odm",
            "study not found",
            "subject not found",
            "please correct",
            "class=\"alert-danger\"",
            "class=\"error\"",
        ]
        matched = next((p for p in error_phrases if p in body_lower), None)
        if matched:
            idx = body_lower.find(matched)
            snippet = body[max(0, idx-50):idx+300]
            raise RuntimeError(f"ODM import error ({matched}): {snippet}")
        return {"status": "submitted", "url": str(resp.url), "body_length": len(body)}


# ── DVS stamping ──────────────────────────────────────────────────────────────

def _stamp_dvs(dvs_bytes: bytes, stamp_map: dict) -> bytes:
    """
    Write runtime Site_OID and Participant_Key into UAT_Cases sheet.
    stamp_map: { logical_pid -> {"site_oid": ..., "participant_key": ...} }
    """
    wb = load_workbook(io.BytesIO(dvs_bytes))
    if "UAT_Cases" not in wb.sheetnames:
        return dvs_bytes
    ws = wb["UAT_Cases"]

    header_row_idx = None
    col_idx = {}
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if row and row[0] == "UAT Case ID":
            header_row_idx = row_idx
            for ci, val in enumerate(row, start=1):
                if val:
                    col_idx[str(val).strip()] = ci
            break

    if not header_row_idx:
        return dvs_bytes

    site_col = col_idx.get("Site_OID")
    key_col  = col_idx.get("Participant_Key")
    pid_col  = col_idx.get("Participant_ID")
    if not (site_col and key_col and pid_col):
        return dvs_bytes

    for row in ws.iter_rows(min_row=header_row_idx + 1):
        pid = str(row[pid_col - 1].value or "").strip()
        if pid and pid in stamp_map:
            row[site_col - 1].value = stamp_map[pid]["site_oid"]
            row[key_col  - 1].value = stamp_map[pid]["participant_key"]

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_uat_loader(item_id: str) -> dict:
    """
    Execute the full UAT data loading workflow for one monday.com item.
    Loads OC session cookies from the saved Playwright storage_state file.
    Returns dict: success, site_oid, participants_created, odm_imports, errors.
    """
    result = {
        "success": False,
        "site_oid": None,
        "participants_created": [],
        "odm_imports": [],
        "errors": [],
    }

    # ── Step 1: Read item metadata ─────────────────────────────────────────
    await append_log(item_id, "UAT Loader: reading item metadata...")
    item = await get_item(item_id)
    cols = {c["id"]: c for c in item.get("column_values", [])}

    subdomain  = (cols.get(COL["oc_subdomain"], {}).get("text") or "").strip()
    study_uuid = (cols.get(COL["study_uuid"],   {}).get("text") or "").strip()
    study_oid  = (cols.get(COL["study_oid"],    {}).get("text") or "").strip()
    oc_email   = (cols.get(COL["oc_email"],     {}).get("text") or "").strip()

    if not subdomain:
        result["errors"].append("OC Subdomain is blank.")
        return result
    if not study_uuid:
        result["errors"].append("Study UUID is blank — run the pipeline first.")
        return result
    if not study_oid:
        result["errors"].append("Study OID is blank — publish to Test first.")
        return result

    # ── Load auth cookies from saved session ──────────────────────────────
    auth_cookies = _load_cookies(oc_email)

    # ── Session preflight: check for clinical host cookies ────────────────
    # Derive the clinical host from bridge_url in customer_uuids.csv.
    # e.g. https://cust1.eu.openclinica.io/OpenClinica -> cust1.eu.openclinica.io
    from urllib.parse import urlparse as _urlparse
    _bridge = _pages_base(subdomain)  # e.g. https://cust1.eu.openclinica.io/OpenClinica
    _clinical_host = _urlparse(_bridge).hostname or ""  # e.g. cust1.eu.openclinica.io

    _needs_auth = (
        not auth_cookies
        or (_clinical_host and not _session_has_clinical_cookies(oc_email, _clinical_host))
    )

    if _needs_auth:
        auth_link = _generate_auth_link(oc_email, context="uat")
        # Use existing "Paused for Authentication" status (not a new label)
        # and reset the AI trigger so it doesn't re-fire immediately
        await asyncio.gather(
            set_status(item_id, "color_mm2h9g3m", "Paused for Authentication"),
            set_status(item_id, COL["ai_trigger"],  "Do not Send To AI Yet"),
        )
        # Write fresh auth link to the OC Auth Link column
        import json as _json
        _link_val = _json.dumps({"url": auth_link, "text": "Authenticate OpenClinica"})
        from monday_client import make_mutation, BOARD_ID, get_headers, MONDAY_API_URL
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=15) as _c:
            await _c.post(MONDAY_API_URL, headers=get_headers(), json={
                "query": make_mutation(),
                "variables": {"i": item_id, "b": BOARD_ID,
                              "c": COL["oc_auth_link"], "v": _link_val},
            })
        await append_log(
            item_id,
            f"UAT Loader: session missing or expired for {_clinical_host}. "
            f"Click the OC Auth Link to re-authenticate. "
            f"The extension will automatically capture all OpenClinica tabs. "
            f"Then set AI Trigger back to 'Send to AI'."
        )
        result["errors"].append("Authentication required — see OC Auth Link column.")
        return result

    # ── Step 2: Download DVS ───────────────────────────────────────────────
    await append_log(item_id, "UAT Loader: downloading DVS...")
    dvs_bytes = await download_column_file(item_id, COL["dvs_output"])
    if not dvs_bytes:
        result["errors"].append(
            "No DVS found in DVS Output column. Run the pipeline first."
        )
        return result

    # ── Step 3: Get TEST environment UUID (on-the-fly) ────────────────────
    await append_log(item_id, "UAT Loader: locating TEST environment...")
    try:
        test_env_uuid, test_env_oid = await _get_test_env_uuid(
            subdomain, study_uuid, auth_cookies
        )
    except Exception as e:
        result["errors"].append(f"Could not find TEST environment: {e}")
        return result

    # ── Step 4: Create dated site ──────────────────────────────────────────
    now       = datetime.datetime.now()
    site_name = f"UAT Automation Site - {now.strftime('%Y-%m-%d %H:%M')}"
    site_oid  = f"UAT-{now.strftime('%Y%m%d-%H%M%S')}"

    await append_log(item_id, f"UAT Loader: creating site '{site_name}'...")
    try:
        created_site_oid = await _create_site(
            subdomain, test_env_uuid, site_name, site_oid, auth_cookies
        )
        result["site_oid"] = created_site_oid
        await append_log(item_id, f"UAT Loader: site created → {created_site_oid}")
    except Exception as e:
        result["errors"].append(f"Site creation failed: {e}")
        return result

    # ── Step 4b: Set TEST environment to AVAILABLE ─────────────────────────
    await append_log(item_id, "UAT Loader: setting TEST environment to AVAILABLE...")
    try:
        env_token = await _get_oc_token(subdomain)
        await _set_env_available(subdomain, test_env_uuid, test_env_oid, env_token)
        await append_log(item_id, "UAT Loader: TEST environment set to AVAILABLE")
    except Exception as e:
        await append_log(item_id, f"UAT Loader: WARNING — set env AVAILABLE failed (continuing): {e}")

    # ── Step 5: Parse UAT_Cases ────────────────────────────────────────────
    await append_log(item_id, "UAT Loader: parsing UAT_Cases sheet...")
    try:
        uat_rows = _parse_uat_cases(dvs_bytes)
    except Exception as e:
        result["errors"].append(f"DVS parse failed: {e}")
        return result

    if not uat_rows:
        result["errors"].append(
            "No loadable rows in UAT_Cases. "
            "Ensure DVS was generated with ODM load coordinates populated."
        )
        return result

    groups = _group_by_participant(uat_rows)
    await append_log(
        item_id,
        f"UAT Loader: {len(uat_rows)} rows, "
        f"{len(groups)} participant(s): {list(groups.keys())}"
    )

    token = await _get_oc_token(subdomain)
    stamp_map = {}

    for logical_pid, rows in groups.items():
        # ── Step 6: Create participant ─────────────────────────────────────
        p_suffix = logical_pid.replace("UAT-P", "P")
        run_key  = f"{site_oid}-{p_suffix}"

        await append_log(item_id, f"UAT Loader: creating participant {run_key}...")
        try:
            confirmed_key = await _create_participant(
                subdomain, study_oid, created_site_oid, run_key, token, auth_cookies
            )
            result["participants_created"].append(confirmed_key)
            stamp_map[logical_pid] = {
                "site_oid":        created_site_oid,
                "participant_key": confirmed_key,
            }
        except Exception as e:
            # Non-fatal: OC4 may auto-enroll participant on ODM import.
            # Log the warning but continue to attempt ODM import anyway.
            await append_log(item_id,
                f"UAT Loader: participant creation returned error (proceeding to ODM): {e}")
            result["participants_created"].append(run_key)
            stamp_map[logical_pid] = {
                "site_oid":        created_site_oid,
                "participant_key": run_key,
            }

        # ── Steps 7+8: Build and import ODM ───────────────────────────────
        await append_log(
            item_id,
            f"UAT Loader: importing ODM for {run_key} "
            f"({len(rows)} rows)..."
        )
        try:
            odm_xml = _build_odm_xml(
                study_oid, created_site_oid, run_key, rows
            )
            import_result = await _import_odm(
                subdomain, study_oid, odm_xml, auth_cookies
            )
            result["odm_imports"].append({
                "participant": run_key,
                "rows":        len(rows),
                "result":      import_result,
            })
            await append_log(item_id,
                             f"UAT Loader: ODM imported for {run_key}")
        except Exception as e:
            err = f"ODM import failed for {run_key}: {e}"
            result["errors"].append(err)
            await append_log(item_id, f"UAT Loader: ERROR — {err}")

    # ── Step 9: Stamp DVS ─────────────────────────────────────────────────
    if stamp_map:
        await append_log(item_id, "UAT Loader: stamping DVS with runtime OIDs...")
        try:
            stamped_bytes = _stamp_dvs(dvs_bytes, stamp_map)
        except Exception as e:
            stamped_bytes = dvs_bytes
            await append_log(item_id, f"UAT Loader: stamp failed (non-fatal): {e}")
    else:
        stamped_bytes = dvs_bytes

    # ── Step 10: Upload stamped DVS ────────────────────────────────────────
    protocol_number = (
        cols.get(COL["protocol_number"], {}).get("text") or "UAT"
    ).strip()
    dvs_filename = f"{protocol_number}_DVS_UAT_Results.xlsx"

    await append_log(item_id, "UAT Loader: uploading UAT DVS Results...")
    try:
        await upload_file(item_id, UAT_DVS_RESULTS_COL,
                          dvs_filename, stamped_bytes)
    except Exception as e:
        await append_log(item_id, f"UAT Loader: results upload failed: {e}")

    # ── Done ───────────────────────────────────────────────────────────────
    n_ok  = len(result["participants_created"])
    n_err = len(result["errors"])
    result["success"] = n_ok > 0 and n_err == 0

    await append_log(
        item_id,
        f"UAT Load complete. Site: {result['site_oid']}. "
        f"Participants: {n_ok}. ODM imports: {len(result['odm_imports'])}. "
        f"Errors: {n_err}."
    )
    return result
