from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
import hmac, hashlib, json, os, time, traceback, asyncio

# Auth manager — Chrome-extension session-capture flow
from auth_manager import (
    AuthManager,
    handle_session_upload,
    render_instructions_page,
)
from monday_client import COL, PIPELINE_CONFIG_ITEM_ID, download_column_file
from migration_pipeline import MIGRATIONS_HUB_COLUMNS

app = FastAPI()

# CORS — the Syndeo UI (mapping-ui) is a separate Railway service that
# fetches gap reports from this API via cross-origin XHR. Restricted to
# the known UI origins; webhook endpoints are server-to-server (Monday)
# and don't go through CORS preflight.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mapping-ui-production.up.railway.app",
        "http://localhost:3000",  # CRA dev
        "http://localhost:5173",  # Vite dev
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Add session middleware (required for OAuth state management)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["AUTH_SECRET_KEY"],
    max_age=3600  # 1 hour session lifetime
)

MONDAY_SIGNING_SECRET = os.environ.get("MONDAY_SIGNING_SECRET", "")

TRIGGER_COLUMN_ID    = "single_select5ogcb0g"
TRIGGER_LABEL_INDEX  = 0      # 0 = "Send to AI"
TRIGGER_LABEL_TEXT   = "Send to AI"

# New columns for OC study creation
CREATE_STUDY_CHECKBOX = "boolean_mm2nbn5c"   # "Would you like AI to create Study, SOE, and Form cards in OC4?"
PUBLISH_TEST_CHECKBOX = "boolean_mm3g2vzf"   # "Publish to Test" checkbox
LOAD_UAT_CHECKBOX     = "boolean_mm3gxe49"   # "Load UAT Test Data"

@app.get("/health")
async def health():
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Syndeo UI — gap report fetch
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/gap-report/{item_id}")
async def get_gap_report(item_id: str):
    """Return the gap-analysis report JSON for a Migrations Hub row.

    Reads the file currently in the gap_report file column
    (file_mm3qcpnr) on the Migrations AI Hub board for the given item_id,
    downloads its bytes, parses as JSON, and returns to the browser.

    The Migrations Hub row is the long-lived per-study record produced
    by migration_pipeline.run_gap_analysis_and_hub_upsert — the file
    on this column is overwritten on every pipeline run for that study,
    so this endpoint always serves the latest version. Each report has
    a `generated_at` ISO timestamp inside it for client-side staleness
    checks.

    Errors:
      404 — column empty (pipeline hasn't run gap analysis yet, or
            item_id points at a row on a different board).
      500 — Monday API failure, or file present but not valid JSON.
    """
    try:
        blob = await download_column_file(
            item_id, MIGRATIONS_HUB_COLUMNS["gap_report"],
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Monday fetch failed for item {item_id}: {e}",
        )
    if not blob:
        raise HTTPException(
            status_code=404,
            detail=(f"No gap report uploaded on Migrations Hub item "
                    f"{item_id} — either the pipeline hasn't produced "
                    f"one yet, or this item is on a different board."),
        )
    try:
        report = json.loads(blob.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=(f"Gap report file on item {item_id} is not valid "
                    f"JSON: {e}"),
        )
    return JSONResponse(report)


# ─────────────────────────────────────────────────────────────────────────────
# Temporary admin endpoint — clear false-positive conflict OIDs from
# the per-item upload record (CRS-135 one-time fix, May 2026).
#
# DELETE THIS ROUTE once the operational backlog of stale records is
# cleared. Keeping a write-anywhere endpoint in production is a liability
# even when gated by a shared secret.
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/admin/clear-upload-record-oids")
async def clear_upload_record_oids(request: Request):
    """Remove specific OIDs from an item's upload record so the
    conflict detector stops flagging them on the next publish-to-test.

    Body  : {"item_id": "<numeric>", "oids": ["AE", "CM", ...]}
    Header: X-Admin-Secret must match the ADMIN_SECRET env var.

    Errors:
      503 — ADMIN_SECRET env var is not set. The endpoint refuses to
            run with an empty default so an unset env doesn't quietly
            authorise everyone.
      403 — secret header missing or mismatched.
      400 — item_id missing/non-numeric, or oids list empty.
      404 — no upload record file on disk for that item.
    """
    admin_secret = os.environ.get("ADMIN_SECRET", "")
    if not admin_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "ADMIN_SECRET env var not set — endpoint disabled. "
                "Set it on Railway before calling."
            ),
        )
    if request.headers.get("X-Admin-Secret", "") != admin_secret:
        raise HTTPException(status_code=403, detail="unauthorized")

    body = await request.json()
    item_id = str(body.get("item_id", "")).strip()
    oids_to_remove = set(body.get("oids", []) or [])
    # item_id is interpolated into a filesystem path — keep it strictly
    # numeric to block path-traversal even though the secret check
    # already gates access.
    if not item_id or not item_id.isdigit():
        raise HTTPException(
            status_code=400,
            detail="item_id is required and must be numeric",
        )
    if not oids_to_remove:
        raise HTTPException(
            status_code=400,
            detail="oids must be a non-empty list",
        )

    path = f"/data/pipeline_upload_records/{item_id}.json"
    if not os.path.exists(path):
        raise HTTPException(
            status_code=404,
            detail=f"upload record not found: {path}",
        )

    with open(path) as f:
        rec = json.load(f)
    before = set(rec.get("uploaded_oids", []) or [])
    rec["uploaded_oids"] = sorted(before - oids_to_remove)
    # Conflict-detector store: pipeline.py reads
    # rec["forms"][oid]["pipeline_version_ids"] to decide whether an
    # OC version is pipeline-managed or human-edited. Clearing the
    # entry here lets the OID fall through the conflict detector's
    # "OID not in stored forms → unmanaged → upload fresh" branch on
    # the next run.
    if "forms" in rec and isinstance(rec["forms"], dict):
        for oid in oids_to_remove:
            rec["forms"].pop(oid, None)
    # Legacy key — older records used a flat top-level oc_version_ids
    # dict. Pop it too if present so old records don't carry stale
    # state forward after a clear.
    if "oc_version_ids" in rec and isinstance(rec["oc_version_ids"], dict):
        for oid in oids_to_remove:
            rec["oc_version_ids"].pop(oid, None)
    with open(path, "w") as f:
        json.dump(rec, f, indent=2)

    return {
        "item_id":   item_id,
        "removed":   sorted(oids_to_remove),
        "remaining": rec["uploaded_oids"],
        "forms_after": sorted((rec.get("forms") or {}).keys()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Temporary diagnostic — slow-forms upload timing test
#
# Drives the upload sequence for the 7 OIDs that historically time out
# during normal publish runs (SLEEP, SF12, EX, AE, AESAE, CM, DV) and
# returns per-form timing + OC REST verification. Runs inside the
# Railway container where the SSO session JSON and the prebuilt xlsx
# files already live.
#
# DELETE THIS ROUTE once the slow-form upload timing is resolved.
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/test/slow-forms")
async def test_slow_forms_endpoint(
    x_admin_secret: str = Header(None, alias="X-Admin-Secret"),
):
    """Run the slow-forms diagnostic and return the result dict.

    Gated by X-Admin-Secret header against the ADMIN_SECRET env var
    (default fallback "oc-admin-2026" so local invocations work
    without env wiring).

    Returns the dict from test_slow_forms.run_test() — see that
    function's docstring for the response shape. Per-form prints
    still flow to server stdout so Railway logs show live progress.
    """
    expected_secret = os.environ.get("ADMIN_SECRET", "oc-admin-2026")
    if x_admin_secret != expected_secret:
        raise HTTPException(status_code=403, detail="unauthorized")
    from test_slow_forms import run_test
    return await run_test()


# ─────────────────────────────────────────────────────────────────────────────
# Auth bootstrap (Chrome extension session-capture flow)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/auth")
async def auth_page(token: str = ""):
    """Render the bootstrap instructions page for a one-time auth link.

    Validates the token (signature + 1-hour max-age) but does NOT
    consume it — the same token is reused by /api/session/upload below.
    """
    if not token:
        return HTMLResponse("<h1>Missing token</h1>", status_code=400)
    am = AuthManager()
    email, error = am.validate_token(token)
    if error:
        return HTMLResponse(
            f"<h1>Auth link problem</h1><p>{error}</p>",
            status_code=400,
        )
    return HTMLResponse(render_instructions_page(token, email))


@app.post("/api/session/upload")
async def session_upload(request: Request):
    """Endpoint the OC Session Capture Chrome extension POSTs to.

    Body: {"token": "<signed token>", "storage_state": <playwright dict>}
    Response: {"ok": true, "email": ..., "cookies": N} on success;
              {"ok": false, "error": ...} on failure (HTTP 400).
    """
    data = await request.json()
    result = await handle_session_upload(
        data.get("token", ""),
        data.get("storage_state"),
    )
    status = result.pop("status", 200 if result.get("ok") else 400)
    return JSONResponse(result, status_code=status)


# ─────────────────────────────────────────────────────────────────────────────
# Extension proxy (serves the zipped Chrome extension stored in monday)
# ─────────────────────────────────────────────────────────────────────────────

# Module-level 5-min TTL cache so repeat downloads don't re-hit monday
# on every install. Resets on each Railway deploy.
_extension_zip_cache: tuple[bytes, float] | None = None
EXTENSION_CACHE_TTL_S = 300


async def _get_extension_zip_bytes() -> bytes:
    """Fetch the latest extension-zip bytes from monday (with TTL cache)."""
    global _extension_zip_cache
    now = time.time()
    if _extension_zip_cache is not None and now < _extension_zip_cache[1]:
        return _extension_zip_cache[0]
    blob = await download_column_file(
        PIPELINE_CONFIG_ITEM_ID, COL["pipeline_extension"],
    )
    if not blob:
        raise HTTPException(
            status_code=503,
            detail="Extension not uploaded to monday yet",
        )
    _extension_zip_cache = (blob, now + EXTENSION_CACHE_TTL_S)
    return blob


@app.get("/extension.zip")
async def extension_zip():
    """Serve the OC Session Capture Chrome extension (zip) to users.

    Authoritative source is the file column on monday's Pipeline
    Configuration row — drop a new zip there and it propagates within
    5 minutes (or immediately on the next Railway deploy).
    """
    blob = await _get_extension_zip_bytes()
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                'attachment; filename="oc-session-capture.zip"',
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Monday.com Webhooks
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/webhook/monday")
async def monday_webhook(request: Request, background_tasks: BackgroundTasks):
    body    = await request.body()
    payload = json.loads(body)

    # Monday one-time verification challenge
    if "challenge" in payload:
        print("CHALLENGE received - responding", flush=True)
        return {"challenge": payload["challenge"]}

    event   = payload.get("event", {})
    item_id = str(event.get("pulseId", ""))
    col_id  = event.get("columnId", "")
    new_val = event.get("value", {})

    # Only care about our specific trigger column
    if col_id != TRIGGER_COLUMN_ID:
        print(f"IGNORED: column {col_id} is not the trigger column", flush=True)
        return {"status": "ignored"}

    # Extract the label index from the value
    if isinstance(new_val, str):
        try:
            new_val = json.loads(new_val)
        except:
            pass

    label_idx  = new_val.get("label", {}).get("index") if isinstance(new_val, dict) else None
    label_text = new_val.get("label", {}).get("text", "") if isinstance(new_val, dict) else ""

    print(f"COLUMN CHANGE: item={item_id} label_index={label_idx} label_text='{label_text}'", flush=True)

    # Only trigger when set to "Send to AI"
    if label_idx != TRIGGER_LABEL_INDEX:
        print(f"IGNORED: '{label_text}' is not '{TRIGGER_LABEL_TEXT}' - no action taken", flush=True)
        return {"status": f"ignored - changed to '{label_text}', only fires on '{TRIGGER_LABEL_TEXT}'"}

    # All checks passed - start the pipeline
    print(f"TRIGGERED: item {item_id} set to '{TRIGGER_LABEL_TEXT}' - starting pipeline", flush=True)

    async def safe_run_pipeline(iid):
        try:
            from pipeline import run_pipeline
            await run_pipeline(iid)
        except Exception as e:
            print(f"PIPELINE CRASHED: {e}", flush=True)
            print(traceback.format_exc(), flush=True)

    background_tasks.add_task(safe_run_pipeline, item_id)
    return {"status": "pipeline_started", "item_id": item_id}


@app.post("/webhook/create_study")
async def create_study_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Triggered when boolean_mm2nbn5c checkbox is checked.
    Creates OC study, imports board, then uploads forms to create versions.
    """
    body = await request.body()
    payload = json.loads(body)
    
    # Monday challenge response
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    
    event = payload.get("event", {})
    item_id = str(event.get("pulseId", ""))
    col_id = event.get("columnId", "")
    new_val = event.get("value", {})
    
    # Only respond to CREATE_STUDY_CHECKBOX column
    if col_id != CREATE_STUDY_CHECKBOX:
        return {"status": "ignored"}
    
    # Parse checkbox value
    if isinstance(new_val, str):
        try:
            new_val = json.loads(new_val)
        except:
            pass
    
    # Check if checkbox is checked ({"checked": "true"})
    is_checked = new_val.get("checked") == "true" if isinstance(new_val, dict) else False
    
    if not is_checked:
        print(f"CREATE STUDY: Checkbox unchecked on item {item_id} - ignoring", flush=True)
        return {"status": "ignored - checkbox unchecked"}
    
    print(f"CREATE STUDY: Checkbox checked on item {item_id} - starting", flush=True)
    
    async def safe_create_study(iid):
        try:
            from oc_study_creator import create_oc_study_with_forms
            await create_oc_study_with_forms(iid)
        except Exception as e:
            print(f"CREATE STUDY CRASHED: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
    
    background_tasks.add_task(safe_create_study, item_id)
    return {"status": "study_creation_started", "item_id": item_id}


@app.post("/webhook/publish_test")
async def publish_test_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Triggered when Publish to Test checkbox is checked.
    Waits for forms to be loaded, then publishes study to Test environment.
    """
    body = await request.body()
    payload = json.loads(body)
    
    # Monday challenge response
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    
    event = payload.get("event", {})
    item_id = str(event.get("pulseId", ""))
    col_id = event.get("columnId", "")
    new_val = event.get("value", {})
    
    # Only respond to PUBLISH_TEST_CHECKBOX column
    if col_id != PUBLISH_TEST_CHECKBOX:
        return {"status": "ignored"}
    
    # Parse checkbox value
    if isinstance(new_val, str):
        try:
            new_val = json.loads(new_val)
        except:
            pass
    
    # Check if checkbox is checked
    is_checked = new_val.get("checked") == "true" if isinstance(new_val, dict) else False
    
    if not is_checked:
        print(f"PUBLISH TEST: Checkbox unchecked on item {item_id} - ignoring", flush=True)
        return {"status": "ignored - checkbox unchecked"}
    
    print(f"PUBLISH TEST: Checkbox checked on item {item_id} - starting", flush=True)
    
    async def safe_publish_test(iid):
        try:
            from oc_study_creator import publish_to_test_with_wait
            await publish_to_test_with_wait(iid)
        except Exception as e:
            print(f"PUBLISH TEST CRASHED: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
    
    background_tasks.add_task(safe_publish_test, item_id)
    return {"status": "publish_test_started", "item_id": item_id}


@app.post("/webhook/load_uat")
async def load_uat_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Triggered when Load UAT Test Data checkbox is checked.
    Loads DVS-derived UAT test data into the published Test environment.
    NOTE: Not yet implemented — logs a clear message and returns.
    """
    body = await request.body()
    payload = json.loads(body)
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    event = payload.get("event", {})
    item_id = str(event.get("pulseId", ""))
    col_id = event.get("columnId", "")
    new_val = event.get("value", {})
    if col_id != LOAD_UAT_CHECKBOX:
        return {"status": "ignored"}
    if isinstance(new_val, str):
        try:
            new_val = json.loads(new_val)
        except:
            pass
    is_checked = new_val.get("checked") == "true" if isinstance(new_val, dict) else False
    if not is_checked:
        return {"status": "ignored - checkbox unchecked"}
    print(f"LOAD UAT: Checkbox checked on item {item_id} — "
          f"UAT data loading not yet implemented.", flush=True)
    # TODO: implement UAT data loading via OC participant/data API
    return {"status": "load_uat_acknowledged_not_implemented",
            "item_id": item_id}
