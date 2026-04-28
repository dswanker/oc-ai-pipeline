"""
fetch_jsons_from_monday.py — Download the latest Study Spec JSON and
Protocol Summary JSON from monday.com for the test item.

Saves them as fixtures/study_spec.json and fixtures/protocol_summary.json
so test_skills_locally.py can run against them.

Usage:
    export MONDAY_API_TOKEN=<your token>
    python3 fetch_jsons_from_monday.py
"""

import asyncio, httpx, json, os, sys
from pathlib import Path

ITEM_ID = "11779964503"   # Candel PrTK05 monday item

COLUMNS = {
    "spec_json":       "file_mm2gefht",   # Study Specification (JSON) column
    "pricing_summary": "file_mm2gcbxc",   # Protocol Summary lives here
}

MONDAY_API = "https://api.monday.com/v2"
FIXTURES = Path(__file__).parent / "fixtures"


async def fetch_latest_json(client, col_id, label):
    """Fetch the latest .json file in a monday column via the raw column value."""
    q = """
    query($i:[ID!]) {
      items(ids:$i) {
        id
        column_values { id value text }
      }
    }
    """
    token = os.environ.get("MONDAY_API_TOKEN", "").strip()
    if not token:
        sys.exit("ERROR: MONDAY_API_TOKEN env var not set")

    r = await client.post(
        MONDAY_API,
        headers={"Authorization": token, "Content-Type": "application/json",
                 "API-Version": "2024-01"},
        json={"query": q, "variables": {"i": [ITEM_ID]}},
    )
    resp = r.json()
    if "errors" in resp or not resp.get("data"):
        print(f"  ⚠ Monday API response status={r.status_code}")
        print(f"  raw response[:800]: {str(resp)[:800]}")
        return None
    items = resp.get("data", {}).get("items", []) or []
    if not items:
        print(f"  ✗ {label}: no items returned for ITEM_ID={ITEM_ID}")
        return None

    col_value_raw = None
    col_found = False
    for cv in items[0].get("column_values", []):
        if cv.get("id") == col_id:
            col_found = True
            col_value_raw = cv.get("value")
            break
    if not col_found:
        print(f"  ✗ {label}: column id {col_id} not found on the item")
        print(f"    Available column ids (first 30): "
              f"{[cv.get('id') for cv in items[0].get('column_values', [])][:30]}")
        return None
    if not col_value_raw:
        print(f"  ✗ {label}: column {col_id} has no value (empty)")
        return None

    try:
        parsed = json.loads(col_value_raw)
    except Exception as e:
        print(f"  ✗ {label}: could not parse column value as JSON: {e}")
        print(f"    raw value[:300]: {col_value_raw[:300]}")
        return None

    files = parsed.get("files", []) if isinstance(parsed, dict) else []
    if not files:
        print(f"  ✗ {label}: no files recorded in column value")
        print(f"    parsed value: {parsed}")
        return None

    json_files = [f for f in files
                  if (f.get("name") or "").lower().endswith(".json")]
    if not json_files:
        print(f"  ✗ {label}: no .json file in column {col_id}")
        print(f"    Files found: {[f.get('name') for f in files]}")
        return None

    def _ts(f):
        return (f.get("createdAt") or f.get("created_at") or
                f.get("uploadedAt") or str(f.get("fileId") or ""))
    json_files.sort(key=_ts, reverse=True)

    # Download candidates and pick the LARGEST one by actual content size.
    # The Study Spec JSON column has several historical uploads — some are
    # 139-byte stubs from early failed runs. We want the biggest one.
    MAX_CANDIDATES = 5
    print(f"  Evaluating {min(len(json_files), MAX_CANDIDATES)} "
          f"candidates by content size...")
    best = None
    best_size = -1
    best_name = None
    for f in json_files[:MAX_CANDIDATES]:
        asset_id = (f.get("assetId") or f.get("asset_id") or f.get("fileId"))
        if not asset_id:
            continue
        q2 = "query($ids:[ID!]!){assets(ids:$ids){id public_url}}"
        r2 = await client.post(
            MONDAY_API,
            headers={"Authorization": token, "Content-Type": "application/json",
                     "API-Version": "2024-01"},
            json={"query": q2, "variables": {"ids": [str(asset_id)]}},
        )
        assets = (r2.json().get("data") or {}).get("assets", []) or []
        if not assets:
            continue
        url = assets[0].get("public_url")
        if not url:
            continue
        dl = await client.get(url, follow_redirects=True)
        if dl.status_code != 200:
            continue
        size = len(dl.content)
        print(f"    - {f.get('name'):<55}  {size:>8,} bytes")
        if size > best_size:
            best_size = size
            best = dl.content
            best_name = f.get("name")

    if best is None:
        print(f"  ✗ Could not download any candidate")
        return None
    print(f"  → Largest: {best_name} ({best_size:,} bytes)")
    return best


async def main():
    FIXTURES.mkdir(exist_ok=True)

    async with httpx.AsyncClient(timeout=60) as client:
        print(f"═══ Fetching Study Spec JSON (col {COLUMNS['spec_json']}) ═══")
        data = await fetch_latest_json(client, COLUMNS["spec_json"], "Study Spec JSON")
        if data:
            out = FIXTURES / "study_spec.json"
            out.write_bytes(data)
            try:
                parsed = json.loads(data)
                print(f"  ✓ Saved {out} — {len(data)} bytes, "
                      f"{len(parsed.get('forms', []))} forms, "
                      f"keys: {list(parsed.keys())}")
            except Exception:
                print(f"  ⚠ Saved but couldn't parse content as JSON")

        print(f"\n═══ Fetching Protocol Summary JSON (col {COLUMNS['pricing_summary']}) ═══")
        data = await fetch_latest_json(client, COLUMNS["pricing_summary"],
                                       "Protocol Summary JSON")
        if data:
            out = FIXTURES / "protocol_summary.json"
            out.write_bytes(data)
            try:
                parsed = json.loads(data)
                print(f"  ✓ Saved {out} — {len(data)} bytes, "
                      f"keys: {list(parsed.keys())}")
            except Exception:
                print(f"  ⚠ Saved but couldn't parse content as JSON")

    print(f"\n═══ Done. Fixtures in {FIXTURES}/ ═══")
    if FIXTURES.exists():
        for f in sorted(FIXTURES.iterdir()):
            if f.is_file():
                print(f"  {f.name}  ({f.stat().st_size:,} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
