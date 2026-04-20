import httpx, os, json
from datetime import datetime, timezone

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_TOKEN   = os.environ["MONDAY_API_TOKEN"]
BOARD_ID       = "18409146946"
HEADERS = {"Authorization": MONDAY_TOKEN, "Content-Type": "application/json", "API-Version": "2024-01"}

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

async def get_item(item_id):
    q = "query ($i:[ID!]){items(ids:$i){id name column_values{id value text}}}"
    async with httpx.AsyncClient() as c:
        r = await c.post(MONDAY_API_URL, headers=HEADERS, json={"query":q,"variables":{"i":[item_id]}})
    return r.json()["data"]["items"][0]

async def download_file(url):
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers={"Authorization": MONDAY_TOKEN})
    return r.content

async def set_status(item_id, col_id, label_id):
    m = "mutation($i:ID!,$b:ID!,$c:String!,$v:JSON!){change_column_value(item_id:$i,board_id:$b,column_id:$c,value:$v){id}}"
    async with httpx.AsyncClient() as c:
        await c.post(MONDAY_API_URL, headers=HEADERS,
            json={"query":m,"variables":{"i":item_id,"b":BOARD_ID,"c":col_id,"v":json.dumps({"label":{"index":label_id}})}})

async def append_log(item_id, message):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    m = "mutation($i:ID!,$b:ID!,$c:String!,$v:JSON!){change_column_value(item_id:$i,board_id:$b,column_id:$c,value:$v){id}}"
    async with httpx.AsyncClient() as c:
        await c.post(MONDAY_API_URL, headers=HEADERS,
            json={"query":m,"variables":{"i":item_id,"b":BOARD_ID,"c":COL["ai_run_log"],"v":json.dumps({"text":f"[{ts}] {message}"})}})

async def upload_file(item_id, col_id, filename, content):
    m = "mutation($i:ID!,$c:String!){add_file_to_column(item_id:$i,column_id:$c,file:$file){id}}"
    async with httpx.AsyncClient(timeout=120) as c:
        await c.post(MONDAY_API_URL,
            headers={"Authorization": MONDAY_TOKEN},
            data={"query":m,"variables":f'{{"item_id":"{item_id}","col":"{col_id}"}}'},
            files={"variables[file]":(filename, content, "application/octet-stream")})
