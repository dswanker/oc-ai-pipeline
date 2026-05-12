import httpx, os, json
from datetime import datetime, timezone

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_FILE_URL = "https://api.monday.com/v2/file"
BOARD_ID = "18409146946"

COL = {
    # Input files (from monday.com)
    "protocol":          "files9__1",   # Protocol document — PDF, Word, or Google Doc link
    "crf_library":       "fileb5c8dt0c",  # Customer Specific CRF Standards
    "oc_standard":       "file_mm2mafjc",  # Customer OC4 XLSForm Standard(s)
    # Human-in-the-loop input columns
    "edited_spec_input": "file_mm2n3x71",    # Edited Study Specification XLSX
    "build_input":       "file_mm2nqghj",    # Edited Study Build Forms ZIP
    "dvs_input":         "file_mm2n517e",    # Edited DVS XLSX
    "quote_input":       "file_mm2npqge",    # Edited Quote XLSX
    "soe_input":         "file_mm2ns9hr",    # Edited Schedule of Events CSV
    # Trigger + metadata
    "ai_trigger":        "single_select5ogcb0g",
    "protocol_number":   "text_mm2hcfre",
    "client":            "text7__1",
    # Output file columns
    "spec_pdf":          "file_mm2gr5w4",
    "spec_xlsx":         "file_mm2gjqgx",
    "spec_json":         "file_mm2gefht",
    "pricing_summary":   "file_mm2gcbxc",
    "pricing_quote":     "file_mm2g16gn",
    "edc_build":         "file_mm2h51qw",
    "dvs_output":        "file_mm2hhwmk",
    # Status + logging
    "pipeline_status":   "color_mm2h9g3m",
    "ai_run_log":        "long_text_mm2h9mnq",
    # OpenClinica
    "oc_subdomain":      "color_mm2n247s",
    "create_study":      "boolean_mm2nbn5c",
    "oc_production":     "boolean_mm2ptpzd",
    "oc_study_url":      "text_mm2nbce5",
    # Discounts
    "subscription_discount": "numeric_mm2nkqbq",
    "services_discount":     "numeric_mm2n41x7",
    # Output selection
    "output_requested":  "dropdown_mm2nc7d4",
    # Build Preview file column (created by scripts/create_build_preview_column.py)
    "build_preview":     "file_mm2x1ey6",
    # Mapping review UI deep-link (populated after successful migration)
    "mapping_review_url": "link_mm397x44",
    # EDC migration input (created by scripts/create_migration_columns.py)
    "source_edc_export": "file_mm386dte",   # file: ODM XML or ZIP containing ODM XML
    "source_edc_system": "dropdown_mm382w7d",  # dropdown: vendor (auto-detected, overridable)
}

def get_token():
    return os.environ.get("MONDAY_API_TOKEN", "").strip()

def get_headers():
    return {"Authorization": get_token(), "Content-Type": "application/json", "API-Version": "2024-01"}

def make_mutation():
    return "mutation($i:ID!,$b:ID!,$c:String!,$v:JSON!){change_column_value(item_id:$i,board_id:$b,column_id:$c,value:$v){id}}"


def _check_monday_response(r, op_name):
    """
    Raise RuntimeError if a monday.com API call failed.
    Monday returns HTTP 200 even for many errors — actual failures are
    reported in the JSON body as an `errors` key or `error_code` key.

    Args:
      r:       httpx.Response
      op_name: short string used in the error message (e.g. "UPLOAD", "SET_STATUS")
    """
    if r.status_code != 200:
        raise RuntimeError(f"{op_name} failed — HTTP {r.status_code}: {r.text[:300]}")
    try:
        body = r.json()
    except Exception:
        # Non-JSON response on 200 — unusual but treat as success since the
        # status code said OK. Caller will see it in the printed status line.
        return
    # Monday returns errors in multiple shapes:
    #   - GraphQL: {"errors": [{"message": "..."}]}
    #   - REST-ish: {"error_code": "...", "error_message": "..."}
    #   - File uploads: {"status_code": 200, "errors": [...]} (mixed)
    if isinstance(body, dict):
        if body.get("errors"):
            raise RuntimeError(f"{op_name} failed — monday errors: "
                               f"{str(body['errors'])[:300]}")
        if body.get("error_code"):
            raise RuntimeError(f"{op_name} failed — {body.get('error_code')}: "
                               f"{body.get('error_message', '')[:300]}")

async def get_item(item_id):
    # Fetch item column values plus the board's column metadata (id+title) so
    # callers can identify columns by their human-readable titles. The titles
    # are merged into each column_value dict before returning. This keeps the
    # call shape backwards-compatible — existing code reading {id, value, text}
    # continues to work unchanged; new code can read .title when needed.
    q = ("query($i:[ID!]){items(ids:$i){"
         "id name "
         "column_values{id value text} "
         "board{columns{id title}}"
         "}}")
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
    item = items[0]
    # Merge column titles into each column_value dict
    title_by_id = {c["id"]: c.get("title", "")
                   for c in (item.get("board") or {}).get("columns", []) or []}
    for cv in item.get("column_values", []) or []:
        cv["title"] = title_by_id.get(cv.get("id"), "")
    return item

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
    _check_monday_response(r, f"SET_STATUS({col_id}={label_text})")

async def append_log(item_id, message):
    """Append a timestamped line to the AI Run Log column.

    Reads the existing long-text value first and prepends the new entry on
    top so the column accumulates a full per-run history instead of
    overwriting on every call. Failures are logged to stdout — never
    raised — because callers include every error handler in the pipeline.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_line = f"[{ts}] {message}"

    # Read existing column value so we can append rather than overwrite.
    existing = ""
    read_q = """
    query($i:[ID!], $c:[String!]) {
      items(ids:$i) { column_values(ids:$c) { text } }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            rr = await c.post(MONDAY_API_URL, headers=get_headers(),
                              json={"query": read_q,
                                    "variables": {"i": [item_id],
                                                  "c": [COL["ai_run_log"]]}})
        existing = (((rr.json().get("data") or {}).get("items") or [{}])[0]
                    .get("column_values") or [{}])[0].get("text") or ""
    except Exception as exc:  # noqa: BLE001
        # If the read fails, write the new line on its own rather than
        # losing the message entirely.
        print(f"APPEND_LOG read failed (continuing with new-line only): "
              f"{type(exc).__name__}: {exc}", flush=True)

    combined = f"{new_line}\n{existing}" if existing else new_line
    val = json.dumps({"text": combined})
    variables = {"i": item_id, "b": BOARD_ID, "c": COL["ai_run_log"], "v": val}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
                         json={"query": make_mutation(), "variables": variables})
    if r.status_code != 200:
        print(f"APPEND_LOG failed — HTTP {r.status_code}: {r.text[:200]}", flush=True)

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
    _check_monday_response(r, f"UPLOAD({filename})")

async def set_text(item_id, col_id, text_value):
    """Set a text column value on a monday.com item."""
    q = make_mutation()
    v = json.dumps(text_value)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
                         json={"query": q, "variables": {
                             "i": item_id, "b": BOARD_ID,
                             "c": col_id, "v": v}})
    print(f"SET_TEXT {col_id}: {r.status_code}", flush=True)
    _check_monday_response(r, f"SET_TEXT({col_id})")


async def set_link(item_id, col_id, url, text=None):
    """Set a link column value on a monday.com item.

    Monday link columns expect {"url": "...", "text": "..."}. When `text`
    is omitted the URL is shown as its own label.
    """
    q = make_mutation()
    v = json.dumps({"url": url, "text": text or url})
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
                         json={"query": q, "variables": {
                             "i": item_id, "b": BOARD_ID,
                             "c": col_id, "v": v}})
    print(f"SET_LINK {col_id}: {r.status_code}", flush=True)
    _check_monday_response(r, f"SET_LINK({col_id})")


async def download_column_file(item_id, col_id):
    """
    Download a file from a specific file column on a monday.com item.
    Returns bytes if a file is found, None otherwise.
    """
    q = """
    query($i:[ID!]) {
      items(ids:$i) {
        column_values {
          id
          ... on FileValue {
            files {
              ... on FileAssetValue {
                asset_id
              }
            }
          }
        }
      }
    }
    """
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
                         json={"query": q, "variables": {"i": [item_id]}})
    resp = r.json()
    items = resp.get("data", {}).get("items", [])
    if not items:
        return None

    for cv in items[0].get("column_values", []):
        if cv.get("id") != col_id:
            continue
        files = cv.get("files", [])
        if not files:
            return None
        asset_id = files[-1].get("asset_id")
        if not asset_id:
            return None

        # Fetch the asset URL
        asset_q = "query($ids:[ID!]!){assets(ids:$ids){id public_url}}"
        async with httpx.AsyncClient(timeout=30) as c:
            ar = await c.post(MONDAY_API_URL, headers=get_headers(),
                              json={"query": asset_q,
                                    "variables": {"ids": [asset_id]}})
        assets = ar.json().get("data", {}).get("assets", [])
        if not assets:
            return None
        url = assets[0].get("public_url")
        if not url:
            return None
        return await download_file(url)

    return None


async def list_column_filenames(item_id, col_id):
    """
    Return a list of filenames (str) attached to a monday.com file column.
    Empty list if no files or column not found.
    """
    q = """
    query($i:[ID!]) {
      items(ids:$i) {
        column_values {
          id
          ... on FileValue {
            files {
              ... on FileAssetValue {
                asset_id
                name
              }
            }
          }
        }
      }
    }
    """
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MONDAY_API_URL, headers=get_headers(),
                         json={"query": q, "variables": {"i": [item_id]}})
    resp = r.json()
    items = resp.get("data", {}).get("items", [])
    if not items:
        return []

    for cv in items[0].get("column_values", []):
        if cv.get("id") != col_id:
            continue
        files = cv.get("files", []) or []
        # If `name` isn't populated (older API), fall back to asset lookup
        out = []
        missing_name_ids = []
        for f in files:
            n = f.get("name")
            if n:
                out.append(n)
            elif f.get("asset_id"):
                missing_name_ids.append(f["asset_id"])
        if missing_name_ids:
            asset_q = "query($ids:[ID!]!){assets(ids:$ids){id name}}"
            async with httpx.AsyncClient(timeout=30) as c:
                ar = await c.post(MONDAY_API_URL, headers=get_headers(),
                                  json={"query": asset_q,
                                        "variables": {"ids": missing_name_ids}})
            for a in ar.json().get("data", {}).get("assets", []) or []:
                if a.get("name"):
                    out.append(a["name"])
        return out

    return []
