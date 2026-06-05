#!/usr/bin/env python3
"""
scripts/diagnose_uat.py — UAT load diagnostic tool for oc-ai-pipeline.

Diagnoses a failed or suspect UAT load without requiring a full pipeline
re-run. Fetches all context automatically from the Monday item.

Usage:
    python3 scripts/diagnose_uat.py --item-id 11894915699

What it does:
    1. Fetches study_uuid, study_oid, and run log from Monday columns
    2. Extracts the most recent job_uuid and participant_oid from the run log
    3. Hits the OC job log to show Inserted/Failed counts with full error rows
    4. Probes the study board to verify common-form event assignments
    5. Runs the clinical data read-back to confirm whether data is present

Exit codes:
    0  — data confirmed present (UAT load succeeded)
    1  — data absent or import errors found (UAT load failed)
    2  — configuration/auth error
"""

import argparse
import asyncio
import csv
import io
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import httpx

# ── Load .env if present (local dev only) ────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ── Monday column IDs (from monday_client.py) ────────────────────────────────
COL_STUDY_UUID = "text_mm3ggzga"
COL_STUDY_OID  = "text_mm3gxekw"
COL_RUN_LOG    = "long_text_mm2h9mnq"
BOARD_ID       = "18409146946"

# ── OC common event patterns ─────────────────────────────────────────────────
COMMON_EVENT_PATTERNS = ("COMMON", "UNSCH")


# ── Monday helpers ────────────────────────────────────────────────────────────

async def fetch_monday_item(item_id: str, monday_token: str) -> dict:
    query = """
    query($id: ID!) {
      items(ids: [$id]) {
        column_values {
          id
          text
          value
        }
      }
    }
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.monday.com/v2",
            headers={"Authorization": monday_token,
                     "Content-Type": "application/json"},
            json={"query": query, "variables": {"id": item_id}},
        )
    if not resp.is_success:
        sys.exit(f"Monday API error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    items = data.get("data", {}).get("items", [])
    if not items:
        sys.exit(f"Monday item {item_id!r} not found")
    cols = {cv["id"]: cv for cv in items[0]["column_values"]}
    return cols


def extract_job_uuid(run_log: str) -> str | None:
    """Pull the most recent job_uuid from the pipeline run log text."""
    matches = re.findall(r"job_uuid=([0-9a-f-]{36})", run_log or "")
    return matches[-1] if matches else None


def extract_participant_oid(run_log: str) -> str | None:
    """Pull the most recent participant OC OID (SS_…) from the run log."""
    matches = re.findall(r"'subjectOid':\s*'(SS_[^']+)'", run_log or "")
    return matches[-1] if matches else None


def extract_subdomain(run_log: str) -> str | None:
    """Pull the subdomain from the run log."""
    m = re.search(r"SSO session live for (\w+)", run_log or "")
    return m.group(1) if m else None


# ── OC helpers ────────────────────────────────────────────────────────────────

async def get_oc_token(subdomain: str, username: str, password: str) -> str:
    url = f"https://{subdomain}.build.openclinica.io/user-service/api/oauth/token"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"username": username, "password": password},
        )
    if not resp.is_success:
        sys.exit(f"OC auth failed {resp.status_code}: {resp.text[:200]}")
    return resp.text.strip()


async def check_job_log(subdomain: str, job_uuid: str,
                        token: str) -> tuple[int, int, list]:
    """Return (inserted, failed, failed_rows)."""
    eu_base = f"https://{subdomain}.eu.openclinica.io/OpenClinica"
    url = f"{eu_base}/pages/auth/api/jobs/{job_uuid}/downloadFile"
    print(f"\n[job-log] GET {url}")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    print(f"[job-log] HTTP {resp.status_code}")
    body = resp.text or ""

    if "errorCode.jobInProgress" in body:
        print("[job-log] Job still in progress")
        return 0, 0, []
    if "errorCode.invalidUuid" in body:
        print("[job-log] Job UUID invalid or expired on server")
        return 0, 0, []

    inserted = failed = 0
    failed_rows = []
    try:
        reader = csv.DictReader(io.StringIO(body))
        for row in reader:
            status = (row.get("Status") or "").strip()
            if status == "Inserted":
                inserted += 1
            elif status == "Failed":
                failed += 1
                failed_rows.append(row)
    except Exception as e:
        print(f"[job-log] CSV parse error: {e}")
        print(f"[job-log] raw (first 500):\n{body[:500]}")

    print(f"[job-log] Inserted={inserted}  Failed={failed}")
    if failed_rows:
        print(f"[job-log] All {len(failed_rows)} failed rows:")
        for r in failed_rows:
            print(f"  Event={r.get('StudyEventOID')}  Form={r.get('FormOID')}  "
                  f"IG={r.get('ItemGroupOID')}  "
                  f"Message={r.get('Message')}")
    return inserted, failed, failed_rows


async def probe_board_assignments(subdomain: str, study_uuid: str,
                                  token: str,
                                  target_forms: set[str]) -> None:
    """Fetch the board and report which event each target form is assigned to."""
    base = f"https://{subdomain}.build.openclinica.io"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base}/study-service/api/studies/{study_uuid}",
            headers={"Authorization": f"Bearer {token}"},
        )
    if not resp.is_success:
        print(f"[board] study-service {resp.status_code} — skipping board probe")
        return

    study = resp.json()
    board_url = study.get("currentBoardUrl", "")
    m = re.search(r"/b/([^/]+)/", board_url)
    if not m:
        print(f"[board] could not extract board_id from {board_url!r}")
        return

    board_id = m.group(1)
    print(f"\n[board] board_id={board_id!r}")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://{subdomain}.design.openclinica.io/api/boards/{board_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    if not resp.is_success:
        print(f"[board] HTTP {resp.status_code} — skipping")
        return

    board = resp.json()
    lists = board.get("lists", [])
    all_cards = board.get("cards", [])

    cards_by_list: dict[str, list[str]] = {}
    for card in all_cards:
        lid = card.get("listId", "")
        foid = (card.get("formOcoid") or card.get("ocoid") or "")
        cards_by_list.setdefault(lid, []).append(foid)

    form_to_events: dict[str, list[str]] = {f: [] for f in target_forms}
    print(f"[board] {len(lists)} lists, {len(all_cards)} cards")
    print("[board] Target form → event assignments:")
    for lst in lists:
        lid      = lst.get("_id", "")
        list_oid = lst.get("eventOcoid", "")
        for foid in cards_by_list.get(lid, []):
            if foid in target_forms:
                form_to_events[foid].append(list_oid)

    for form, events in form_to_events.items():
        status = "OK" if events else "NOT FOUND IN ANY EVENT"
        print(f"  {form} → {events or status}")


async def check_clinical_data(subdomain: str, study_oid: str,
                               participant_oid: str, token: str) -> int:
    """Return item count from clinical data read-back."""
    eu_base = f"https://{subdomain}.eu.openclinica.io/OpenClinica"
    url = (f"{eu_base}/pages/auth/api/clinicaldata"
           f"/{quote(study_oid, safe='')}"
           f"/{quote(participant_oid, safe='')}"
           f"/*/*"
           f"?clinicalData=y&includeMetadata=n&includeDN=n"
           f"&includeAudits=n&showArchived=n")
    print(f"\n[readback] GET .../{participant_oid}/*/*")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url,
                                headers={"Authorization": f"Bearer {token}",
                                         "Accept": "application/xml"})
    print(f"[readback] HTTP {resp.status_code}")
    if not resp.is_success:
        print(f"[readback] body: {resp.text[:200]}")
        return 0

    ODM_NS = "http://www.cdisc.org/ns/odm/v1.3"
    count = 0
    sample = []
    try:
        root = ET.fromstring((resp.text or "").encode("utf-8"))
        for item in root.iter(f"{{{ODM_NS}}}ItemData"):
            count += 1
            if len(sample) < 5:
                sample.append(f"  {item.get('ItemOID')} = {item.get('Value')!r}")
    except Exception as e:
        print(f"[readback] XML parse error: {e}")
        return 0

    print(f"[readback] item count: {count}")
    for s in sample:
        print(s)
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose a UAT load for a pipeline Monday item."
    )
    parser.add_argument("--item-id", required=True,
                        help="Monday item ID (e.g. 11894915699)")
    parser.add_argument("--job-uuid",
                        help="Override job UUID (skip run-log extraction)")
    parser.add_argument("--participant-oid",
                        help="Override participant OID (skip run-log extraction)")
    args = parser.parse_args()

    # ── Credentials ──────────────────────────────────────────────────────────
    monday_token = os.environ.get("MONDAY_API_TOKEN", "").strip()
    oc_username  = os.environ.get("OC_API_USERNAME", "").strip()
    oc_password  = os.environ.get("OC_API_PASSWORD", "").strip()

    if not monday_token:
        sys.exit("ERROR: MONDAY_API_TOKEN not set")
    if not oc_username or not oc_password:
        sys.exit("ERROR: OC_API_USERNAME or OC_API_PASSWORD not set")

    # ── Fetch Monday item ─────────────────────────────────────────────────────
    print(f"[monday] fetching item {args.item_id}...")
    cols = await fetch_monday_item(args.item_id, monday_token)

    study_uuid = (cols.get(COL_STUDY_UUID, {}).get("text") or "").strip()
    study_oid  = (cols.get(COL_STUDY_OID,  {}).get("text") or "").strip()
    run_log    = (cols.get(COL_RUN_LOG,    {}).get("text") or "").strip()

    print(f"[monday] study_uuid={study_uuid!r}")
    print(f"[monday] study_oid={study_oid!r}")

    if not study_uuid:
        sys.exit("ERROR: study_uuid column is blank — has the pipeline run yet?")
    if not study_oid:
        print("WARNING: study_oid column is blank — clinical read-back will be skipped")

    # ── Extract dynamic values from run log ───────────────────────────────────
    job_uuid       = args.job_uuid or extract_job_uuid(run_log)
    participant_oid = args.participant_oid or extract_participant_oid(run_log)
    subdomain       = extract_subdomain(run_log) or "cust1"

    print(f"[run-log] subdomain={subdomain!r}")
    print(f"[run-log] job_uuid={job_uuid!r}")
    print(f"[run-log] participant_oid={participant_oid!r}")

    if not job_uuid:
        print("WARNING: could not extract job_uuid from run log — "
              "job log check will be skipped")
    if not participant_oid:
        print("WARNING: could not extract participant_oid from run log — "
              "clinical read-back will be skipped")

    # ── OC auth ───────────────────────────────────────────────────────────────
    print(f"\n[auth] getting OC token for {subdomain}...")
    token = await get_oc_token(subdomain, oc_username, oc_password)
    print(f"[auth] token obtained ({len(token)} chars)")

    # ── 1. Job log ────────────────────────────────────────────────────────────
    inserted = failed = 0
    if job_uuid:
        inserted, failed, failed_rows = await check_job_log(
            subdomain, job_uuid, token)

    # ── 2. Board event assignments ────────────────────────────────────────────
    TARGET_FORMS = {"F_AE", "F_AESAE", "F_CM", "F_DV"}
    await probe_board_assignments(subdomain, study_uuid, token, TARGET_FORMS)

    # ── 3. Clinical data read-back ────────────────────────────────────────────
    item_count = 0
    if study_oid and participant_oid:
        item_count = await check_clinical_data(
            subdomain, study_oid, participant_oid, token)
    else:
        print("\n[readback] skipped — missing study_oid or participant_oid")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("VERDICT")
    if item_count > 0:
        print(f"  ✓ Clinical data present: {item_count} items loaded")
        print(f"  ✓ UAT load succeeded")
        return 0
    elif inserted > 0 and item_count == 0:
        print(f"  ⚠ Job log shows {inserted} rows inserted but read-back returned 0")
        print(f"    Possible: wrong study_oid, or data is in a different participant")
    elif failed > 0:
        print(f"  ✗ ODM import failed: {inserted} inserted, {failed} failed")
        print(f"    Fix the errors shown above and re-run UAT-only")
        return 1
    elif not job_uuid:
        print(f"  ? Could not extract job_uuid — check run log manually")
        return 1
    else:
        print(f"  ✗ No data loaded — job inserted 0 rows")
        return 1
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
