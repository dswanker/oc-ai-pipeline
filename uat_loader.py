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


async def _get_test_env_uuid(subdomain: str, study_uuid: str) -> tuple:
    """
    GET /api/studies/{studyUuid}/study-environments
    Returns (test_env_uuid, test_study_oid) for environmentName == 'TEST'.
    Raises ValueError if TEST environment not found.
    Uses Bearer token via _get_oc_token.
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


async def _wait_for_test_available(
    subdomain: str, study_uuid: str,
    timeout_s: int = 60, interval_s: int = 5,
) -> None:
    """Poll study-environments until the TEST environment status == 'AVAILABLE'.

    Per study-service.adoc / Study_Service_API.md the StudyEnvironmentDTO.status
    is an enum (DESIGN, AVAILABLE, FROZEN, LOCKED, ARCHIVED). We only proceed
    when the TEST env reaches AVAILABLE. Bearer token is fetched fresh per
    poll because _get_oc_token returns a short-lived OAuth token.

    Raises RuntimeError if TEST is never AVAILABLE within timeout_s.
    """
    url = (f"{_study_service_base(subdomain)}/api/studies"
           f"/{study_uuid}/study-environments")
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_status = "(not seen)"
    while True:
        token = await _get_oc_token(subdomain)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            envs = resp.json() or []
            for env in envs:
                if (env.get("environmentName") or "").upper() == "TEST":
                    last_status = (env.get("status") or "").upper()
                    if last_status == "AVAILABLE":
                        print(
                            f"[uat_loader] TEST environment AVAILABLE "
                            f"for study {study_uuid}", flush=True)
                        return
                    break
        else:
            last_status = f"HTTP {resp.status_code}"
        if asyncio.get_event_loop().time() >= deadline:
            raise RuntimeError(
                f"TEST environment for study {study_uuid} never reached "
                f"AVAILABLE within {timeout_s}s "
                f"(last status: {last_status!r}). "
                f"Verify the study is published to Test and try again."
            )
        await asyncio.sleep(interval_s)


async def _create_site(subdomain: str, test_env_uuid: str,
                        site_name: str, site_oid: str) -> str:
    """
    POST /api/study-environments/{studyEnvironmentUuid}/sites
    Returns the created site OID.
    Uses Bearer token via _get_oc_token.
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


def _validate_odm_xml(odm_xml: str) -> list[str]:
    """
    Tier 1 — XSD structural validation of ODM XML.
    Returns a list of error strings (empty = valid).
    Uses the bundled minimal ODM 1.3.2 transactional XSD.
    """
    from lxml import etree
    xsd_path = (Path(__file__).parent
                / "skills" / "dvs-specification" / "references"
                / "ODM1-3-2-transactional.xsd")
    errors = []
    try:
        schema_doc = etree.parse(str(xsd_path))
        schema     = etree.XMLSchema(schema_doc)
        doc        = etree.fromstring(odm_xml.encode("utf-8"))
        if not schema.validate(doc):
            errors = [str(e) for e in schema.error_log]
    except etree.XMLSyntaxError as e:
        errors = [f"XML syntax error: {e}"]
    except Exception as e:
        errors = [f"Validation error: {e}"]
    return errors


def _build_odm_xml(study_oid: str, site_oid: str,
                   participant_key: str, rows: list) -> str:
    """Build ODM XML for one participant's data rows."""
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    # Tree: events[ev_oid][repeat_key][form_oid][ig_oid] = [(item_oid, val)]
    events = {}
    for row in rows:
        ev       = row.get("Study_Event_OID", "").strip()
        rk       = row.get("Event_Repeat_Key", "1").strip() or "1"
        fo       = row.get("Form_OID", "").strip()
        ig       = row.get("Item_Group_OID", "").strip()
        item_oid = row.get("Item_OID", "").strip()
        # Item_OID must differ from ItemGroup_OID — fallback derives it
        if not item_oid or item_oid == ig:
            field = row.get("field_name", "").strip()
            item_oid = f"{fo}.{field}" if field else ig
        val = row.get("Load_Value", "").strip()
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
                       odm_xml: str) -> dict:
    """POST ODM XML to OC4 Clinical Data Import API and poll the async job.

    Per How_and_When_to_Use_APIs.pdf (page 9):
      POST {eu_base}/pages/auth/api/clinicaldata/import/xml
        Headers: Authorization: Bearer {token}
                 Content-Type: multipart/form-data; boundary=...
        Body:    -F 'file=@<odm.xml>'
        Returns plain text "job uuid: <uuid>" on success.

    Then poll for completion:
      GET {eu_base}/pages/auth/api/jobs/{job_uuid}/downloadFile
        Headers: Authorization: Bearer {token}
        While body contains "errorCode.jobInProgress" the job is still running.
        On completion the body is the CSV log file with per-item Inserted /
        Failed rows.

    Bearer token only — no cookies, no Study Runner session. Token is fetched
    fresh per HTTP call because _get_oc_token's token is short-lived and the
    poll loop can outlive a single token's TTL.
    """
    base = _pages_base(subdomain)
    submit_url = f"{base}/pages/auth/api/clinicaldata/import/xml"

    # ── Step 1: Submit the ODM XML and capture the job UUID ───────────────
    submit_token = await _get_oc_token(subdomain)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            submit_url,
            files={"file": ("import.xml",
                            odm_xml.encode("utf-8"),
                            "text/xml")},
            headers={"Authorization": f"Bearer {submit_token}"},
        )
    if not resp.is_success:
        raise RuntimeError(
            f"ODM import submit HTTP {resp.status_code} — "
            f"body: {resp.text[:300]}"
        )

    body = resp.text or ""
    if "errorCode." in body:
        raise RuntimeError(f"ODM import submit returned error: {body[:300]}")

    import re as _re
    m = _re.search(r"job\s*uuid\s*:\s*([0-9a-fA-F\-]+)", body)
    if not m:
        raise RuntimeError(
            f"ODM import submit succeeded ({resp.status_code}) but no "
            f"job uuid in response: {body[:300]}"
        )
    job_uuid = m.group(1)
    print(f"[uat_loader] ODM import submitted, job_uuid={job_uuid}",
          flush=True)

    # ── Step 2: Poll the job until it completes (or 120s deadline) ───────
    poll_url = f"{base}/pages/auth/api/jobs/{job_uuid}/downloadFile"
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 120
    interval = 5
    last_status: int | None = None
    last_body = ""
    while True:
        poll_token = await _get_oc_token(subdomain)
        async with httpx.AsyncClient(timeout=30) as client:
            poll_resp = await client.get(
                poll_url,
                headers={"Authorization": f"Bearer {poll_token}"},
            )
        last_status = poll_resp.status_code
        last_body = poll_resp.text or ""

        if "errorCode.invalidUuid" in last_body:
            raise RuntimeError(
                f"ODM import job {job_uuid}: server reports invalid UUID "
                f"({last_body[:300]})"
            )

        # jobInProgress is the OC4-defined "still running" signal.
        if (poll_resp.is_success
                and "errorCode.jobInProgress" not in last_body):
            return {
                "status":   "completed",
                "job_uuid": job_uuid,
                "log":      last_body[:4000],
            }

        if loop.time() >= deadline:
            raise RuntimeError(
                f"ODM import job {job_uuid} did not complete within 120s "
                f"(last HTTP {last_status}, body: {last_body[:300]})"
            )
        await asyncio.sleep(interval)


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
            subdomain, study_uuid
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
            subdomain, test_env_uuid, site_name, site_oid
        )
        result["site_oid"] = created_site_oid
        await append_log(item_id, f"UAT Loader: site created → {created_site_oid}")
    except Exception as e:
        result["errors"].append(f"Site creation failed: {e}")
        return result

    # ── Step 4b: Activate TEST environment (PUT status → AVAILABLE) ──────
    # _activate_test_environment lives in pipeline.py; lazy-imported here
    # because pipeline.py already imports run_uat_loader from this module at
    # module-load time — a top-level "from pipeline import …" would loop.
    await append_log(item_id,
        "UAT Loader: activating TEST environment → AVAILABLE...")
    try:
        from pipeline import _activate_test_environment
        await _activate_test_environment(subdomain, study_uuid)
        await append_log(item_id, "UAT Loader: TEST environment activated")
    except Exception as e:
        result["errors"].append(f"TEST environment activation failed: {e}")
        return result

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

    # ── Study availability gate ───────────────────────────────────────────
    # Per Study_Service_API.md StudyEnvironmentDTO.status enum, TEST must be
    # AVAILABLE before participants can be created. Poll up to 60s.
    await append_log(item_id, "UAT Loader: waiting for TEST environment to be AVAILABLE...")
    try:
        await _wait_for_test_available(subdomain, study_uuid,
                                       timeout_s=60, interval_s=5)
    except Exception as e:
        result["errors"].append(f"TEST environment not AVAILABLE: {e}")
        return result

    token = await _get_oc_token(subdomain)
    stamp_map = {}

    # ── Pass 1: Create ALL participants first ─────────────────────────────
    # Decouples creation from ODM import so OC has time to propagate every
    # new participant before any data lands. A failed creation is recorded
    # in result["errors"] and the participant is excluded from Pass 2
    # (stamp_map gets no entry for it).
    for logical_pid, rows in groups.items():
        p_suffix = logical_pid.replace("UAT-P", "P")
        run_key  = f"{site_oid}-{p_suffix}"

        await append_log(item_id, f"UAT Loader: creating participant {run_key}...")
        try:
            confirmed_key = await _create_participant(
                subdomain, study_oid, created_site_oid, run_key, token, {}
            )
            result["participants_created"].append(confirmed_key)
            stamp_map[logical_pid] = {
                "site_oid":        created_site_oid,
                "participant_key": confirmed_key,
            }
            await append_log(
                item_id,
                f"UAT Loader: participant {run_key} → OC SubjectKey={confirmed_key}"
            )
        except Exception as e:
            err = f"Participant creation failed for {run_key}: {e}"
            result["errors"].append(err)
            await append_log(
                item_id,
                f"UAT Loader: ERROR — {err} (skipping this participant)"
            )

    # Give OC time to propagate all participant creations before importing
    # data against any of them.
    await asyncio.sleep(2)

    # ── Pass 2: Build + import ODM for each successfully-created participant ──
    for logical_pid, rows in groups.items():
        if logical_pid not in stamp_map:
            continue  # creation failed in Pass 1 — skip ODM
        confirmed_key = stamp_map[logical_pid]["participant_key"]
        p_suffix = logical_pid.replace("UAT-P", "P")
        run_key  = f"{site_oid}-{p_suffix}"

        await append_log(
            item_id,
            f"UAT Loader: importing ODM for {run_key} "
            f"({len(rows)} rows)..."
        )
        try:
            odm_xml = _build_odm_xml(
                study_oid, created_site_oid, confirmed_key, rows
            )
            # ── Tier 1: XSD structural validation ────────────────────────
            odm_errors = _validate_odm_xml(odm_xml)
            if odm_errors:
                err_summary = "; ".join(odm_errors[:3])
                raise RuntimeError(
                    f"ODM XML failed XSD validation ({len(odm_errors)} errors): "
                    f"{err_summary}"
                )
            await append_log(item_id, f"UAT Loader: ODM XML valid (XSD passed)")
            import_result = await _import_odm(
                subdomain, study_oid, odm_xml
            )
            result["odm_imports"].append({
                "participant": run_key,
                "rows":        len(rows),
                "result":      import_result,
            })
            await append_log(
                item_id,
                f"UAT Loader: ODM import for {run_key} — "
                f"status={import_result.get('status')} "
                f"url={import_result.get('url','?')} "
                f"snippet={str(import_result.get('log','')[:500])[:200]}"
            )
            # Parse the job log CSV to count Inserted vs Failed rows.
            import csv as _csv
            import io as _csvio
            _log_text = import_result.get("log", "") or ""
            _inserted, _failed_rows = 0, []
            if _log_text:
                reader = _csv.DictReader(_csvio.StringIO(_log_text))
                for _row in reader:
                    _status = (_row.get("Status") or "").strip()
                    if _status == "Inserted":
                        _inserted += 1
                    elif _status == "Failed":
                        _failed_rows.append(_row)
            _job_uuid = import_result.get("job_uuid", "?")
            await append_log(
                item_id,
                f"UAT Loader: ODM job {_job_uuid} — "
                f"Inserted: {_inserted}, Failed: {len(_failed_rows)}"
            )
            if _failed_rows:
                for _fr in _failed_rows[:3]:
                    _msg  = (_fr.get("Message") or "").strip()
                    _evt  = (_fr.get("StudyEventOID") or "").strip()
                    _form = (_fr.get("FormOID") or "").strip()
                    _item = (_fr.get("ItemOID") or "").strip()
                    await append_log(
                        item_id,
                        f"  FAILED row — Event={_evt} Form={_form} "
                        f"Item={_item} Message={_msg}"
                    )
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
