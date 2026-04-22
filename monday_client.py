import httpx, os, json
from datetime import datetime, timezone

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_FILE_URL = "https://api.monday.com/v2/file"
BOARD_ID = "18409146946"

COL = {
    "protocol_pdf": "files9__1", "crf_library": "fileb5c8dt0c",
    "oc_standard": "filetzuzo13y", "ai_trigger": "single_select5ogcb0g",
    "protocol_number": "text_mm2hcfre", "client": "text7__1",
    "spec_pdf": "file_mm2gr5w4", "spec_xlsx": "file_mm2gjqgx",
    "spec_json": "file_mm2gefht", "pricing_summary": "file_mm2gcbxc",
    "pricing_quote": "file_mm2g16gn", "edc_build": "file_mm2h51qw",
    "dvs_output": "file_mm2hhwmk", "pipeline_status": "color_mm2h9g3m",
    "ai_run_log": "long_text_mm2h9mnq",
    "oc_subdomain": "color_mm2n247s",
    "create_study": "boolean_mm2nbn5c",
    "oc_study_url": "text_mm2nbce5",
    "subscription_discount": "numeric_mm2nkqbq",
    "services_discount":     "numeric_mm2n41x7",
    "output_requested":      "dropdown_mm2nc7d4",
}

def get_token():
    return os.environ.get("MONDAY_API_TOKEN", "").strip()

def get_headers():
    return {"Authorization": get_token(), "Content-Type": "application/json", "API-Version": "2024-01"}

def make_mutation():
    return "mutation($i:ID!,$b:ID!,$c:String!,$v:JSON!){change_column_value(item_id:$i,board_id:$b,column_id:$c,value:$v){id}}"

async def get_item(item_id):
    q = "query($i:[ID!]){items(ids:$i){id name column_values{id value text}}}"
    print(f"GET_ITEM: calling Monday API for item {item_id}", flush=True)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(), json={"query": q, "variables": {"i": [item_id]}})
    print(f"GET_ITEM STATUS: {r.status_code}", flush=True)
    print(f"GET_ITEM RESPONSE: {r.text[:500]}", flush=True)
    resp = r.json()
    if "data" not in resp:
        raise Exception(f"Monday API error: {resp}")
    items = resp["data"]["items"]
    if not items:
        raise Exception(f"No item found with id {item_id}")
    return items[0]

async def get_asset_url(item_id):
    q = "query($i:[ID!]){items(ids:$i){assets{id name url public_url}}}"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(), json={"query": q, "variables": {"i": [item_id]}})
    resp = r.json()
    print(f"ASSETS RESPONSE: {str(resp)[:300]}", flush=True)
    items = resp.get("data", {}).get("items", [])
    if items:
        return items[0].get("assets", [])
    return []

async def download_file(url):
    print(f"DOWNLOADING: {url[:80]}", flush=True)
    is_s3 = "s3.amazonaws.com" in url or "files-monday-com" in url
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        if is_s3:
            r = await c.get(url)
        else:
            r = await c.get(url, headers={"Authorization": get_token()})
    print(f"DOWNLOAD STATUS: {r.status_code} SIZE: {len(r.content)}", flush=True)
    if r.status_code == 200 and len(r.content) > 100:
        return r.content
    return b""

async def set_status(item_id, col_id, label_text):
    val = json.dumps({"label": label_text})
    variables = {"i": item_id, "b": BOARD_ID, "c": col_id, "v": val}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(), json={"query": make_mutation(), "variables": variables})
    print(f"SET_STATUS {col_id}={label_text}: {r.status_code}", flush=True)

async def set_text(item_id, col_id, text_value):
    val = json.dumps(text_value)
    variables = {"i": item_id, "b": BOARD_ID, "c": col_id, "v": val}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(), json={"query": make_mutation(), "variables": variables})
    print(f"SET_TEXT {col_id}={str(text_value)[:60]}: {r.status_code}", flush=True)

async def append_log(item_id, message):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    val = json.dumps({"text": f"[{ts}] {message}"})
    variables = {"i": item_id, "b": BOARD_ID, "c": COL["ai_run_log"], "v": val}
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(MONDAY_API_URL, headers=get_headers(), json={"query": make_mutation(), "variables": variables})

async def upload_file(item_id, col_id, filename, file_content):
    print(f"UPLOADING: {filename} ({len(file_content)} bytes) to col {col_id}", flush=True)
    mutation_query = f"""
    mutation ($file: File!) {{
        add_file_to_column(item_id: {item_id}, column_id: "{col_id}", file: $file) {{ id }}
    }}
    """
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            MONDAY_FILE_URL,
            headers={"Authorization": get_token(), "API-Version": "2023-10"},
            files={
                "query":     (None, mutation_query),
                "variables": (None, '{"file": null}'),
                "map":       (None, '{"file": ["variables.file"]}'),
                "file":      (filename, file_content, "application/octet-stream"),
            }
        )
    print(f"UPLOAD STATUS: {r.status_code} {r.text[:300]}", flush=True)
