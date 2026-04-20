import httpx, os, json
from datetime import datetime, timezone

MONDAY_API_URL = "https://api.monday.com/v2"
BOARD_ID       = "18409146946"

COL = {
    "protocol_pdf": "files9__1", "crf_library": "fileb5c8dt0c",
    "oc_standard": "filetzuzo13y", "ai_trigger": "single_select5ogcb0g",
    "protocol_number": "text_mm2hcfre", "client": "text7__1",
    "spec_pdf": "file_mm2gr5w4", "spec_xlsx": "file_mm2gjqgx",
    "spec_json": "file_mm2gefht", "pricing_summary": "file_mm2gcbxc",
    "pricing_quote": "file_mm2g16gn", "edc_build": "file_mm2h51qw",
    "dvs_output": "file_mm2hhwmk", "pipeline_status": "color_mm2h9g3m",
    "ai_run_log": "long_text_mm2h9mnq",
}

def get_token():
    # Strip removes any invisible newlines or spaces Railway may add
    token = os.environ.get("MONDAY_API_TOKEN", "").strip()
    print(f"TOKEN LENGTH: {len(token)} FIRST4: {token[:4]} LAST4: {token[-4:]}", flush=True)
    return token

def get_headers():
    return {
        "Authorization": get_token(),
        "Content-Type": "application/json",
        "API-Version": "2024-01"
    }

async def get_item(item_id):
    query = """
    query ($i: [ID!]) {
        items (ids: $i) {
            id
            name
            column_values { id value text }
        }
    }
    """
    print(f"GET_ITEM: calling Monday API for item {item_id}", flush=True)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            MONDAY_API_URL,
            headers=get_headers(),
            json={"query": query, "variables": {"i": [item_id]}}
        )
    print(f"GET_ITEM STATUS: {r.status_code}", flush=True)
    print(f"GET_ITEM RESPONSE: {r.text[:500]}", flush=True)
    resp = r.json()
    if "errors" in resp:
        raise Exception(f"Monday API errors: {resp['errors']}")
    if "data" not in resp:
        raise Exception(f"Monday API unexpected response: {resp}")
    items = resp["data"]["items"]
    if not items:
        raise Exception(f"No item found with id {item_id}")
    return items[0]

async def download_file(url):
    """Download a file using Monday API token for authentication."""
    print(f"DOWNLOADING: {url[:80]}", flush=True)
    token = get_token()
    # Try with token in Authorization header
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url, headers={"Authorization": token})
    print(f"DOWNLOAD STATUS: {r.status_code} SIZE: {len(r.content)}", flush=True)
    if r.status_code == 200 and len(r.content) > 1000:
        return r.content
    # Try with token as query param (some Monday URLs require this)
    sep = "&" if "?" in url else "?"
    auth_url = f"{url}{sep}token={token}"
    print(f"Retrying with token param...", flush=True)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(auth_url)
    print(f"RETRY STATUS: {r.status_code} SIZE: {len(r.content)}", flush=True)
    if r.status_code == 200 and len(r.content) > 1000:
        return r.content
    return b""

async def get_asset_url(item_id):
    """Get public download URLs for all assets attached to an item."""
    query = """
    query ($i: [ID!]) {
        items (ids: $i) {
            assets {
                id
                name
                url
                public_url
            }
        }
    }
    """
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
            json={"query": query, "variables": {"i": [item_id]}})
    resp = r.json()
    print(f"ASSETS RESPONSE: {str(resp)[:300]}", flush=True)
    items = resp.get("data", {}).get("items", [])
    if items:
        return items[0].get("assets", [])
    return []

async def set_status(item_id, col_id, label_id):
    m = """
    mutation ($i: ID!, $b: ID!, $c: String!, $v: JSON!) {
        change_column_value(item_id: $i, board_id: $b, column_id: $c, value: $v) { id }
    }
    """
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
            json={"query": m, "variables": {
                "i": item_id, "b": BOARD_ID, "c": col_id,
                "v": json.dumps({"label": {"id": label_id}})}})
    print(f"SET_STATUS {col_id}={label_id}: {r.status_code}", flush=True)

async def append_log(item_id, message):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    m = """
    mutation ($i: ID!, $b: ID!, $c: String!, $v: JSON!) {
        change_column_value(item_id: $i, board_id: $b, column_id: $c, value: $v) { id }
    }
    """
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(MONDAY_API_URL, headers=get_headers(),
            json={"query": m, "variables": {
                "i": item_id, "b": BOARD_ID, "c": COL["ai_run_log"],
                "v": json.dumps({"text": f"[{ts}] {message}"})}})

async def upload_file(item_id, col_id, filename, content):
    m = """
    mutation ($i: ID!, $c: String!) {
        add_file_to_column(item_id: $i, column_id: $c, file: $file) { id }
    }
    """
    async with httpx.AsyncClient(timeout=120) as c:
        await c.post(MONDAY_API_URL,
            headers={"Authorization": get_token()},
            data={"query": m,
                  "variables": f'{{"item_id":"{item_id}","col":"{col_id}"}}'},
            files={"variables[file]": (filename, content, "application/octet-stream")})
