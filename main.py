from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from auth_manager import AuthManager
import asyncio, hmac, hashlib, json, os, traceback

app = FastAPI()

MONDAY_SIGNING_SECRET = os.environ.get("MONDAY_SIGNING_SECRET", "")
TRIGGER_COLUMN_ID    = "single_select5ogcb0g"
TRIGGER_LABEL_INDEX  = 0      # 0 = "Send to AI"
TRIGGER_LABEL_TEXT   = "Send to AI"

# Publish-to-Test button
PUBLISH_BUTTON_COLUMN_ID = "button_mm3gwq70"

# Load-DVS-UAT-Data checkbox
LOAD_DVS_UAT_DATA_COLUMN_ID = "boolean_mm3gxe49"

# Concurrency guards
_pipeline_semaphore = asyncio.Semaphore(1)
_active_items: set = set()
_active_publishes: set = set()
_active_dvs_uat_loads: set = set()

@app.get("/health")
async def health():
    return {
        "status":       "ok",
        "active_items": list(_active_items),
        "queue_waiting": max(0, _pipeline_semaphore._value * -1
                             if hasattr(_pipeline_semaphore, '_value') else 0),
    }

@app.get("/auth")
async def auth_page(token: str):
    """
    Auth landing page - validates token and initiates OAuth
    """
    email = AuthManager.validate_token(token)
    
    if not email:
        return HTMLResponse(
            "<h1>Invalid or Expired Link</h1>"
            "<p>This authentication link has expired or already been used.</p>"
            "<p>Please trigger 'Send to AI' again from monday.com to get a new link.</p>",
            status_code=400
        )
    
    # Check if already authenticated
    if AuthManager.session_exists(email):
        return HTMLResponse(
            f"<h1>Already Authenticated</h1>"
            f"<p>Your OpenClinica account ({email}) is already authenticated.</p>"
            f"<p>You can trigger 'Send to AI' from monday.com now.</p>"
        )
    
    # Initiate OAuth flow
    authorization_url, state = AuthManager.initiate_oauth(email)
    
    # Redirect to Google
    return RedirectResponse(authorization_url)


@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str):
    """
    OAuth callback from Google
    """
    email = AuthManager.handle_callback(code, state)
    
    if not email:
        return HTMLResponse(
            "<h1>Authentication Failed</h1>"
            "<p>Something went wrong during authentication.</p>"
            "<p>Please try again or contact support.</p>",
            status_code=400
        )
    
    # Save placeholder session
    AuthManager.save_placeholder_session(email)
    
    return HTMLResponse(
        f"<h1>✅ Authentication Successful!</h1>"
        f"<p>Your OpenClinica account ({email}) has been authenticated.</p>"
        f"<p><strong>Next step:</strong> Go back to monday.com and trigger 'Send to AI' again.</p>"
        f"<p>The pipeline will now be able to upload forms to OpenClinica.</p>"
    )

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

    # Publish-to-Test button handler
    if col_id == PUBLISH_BUTTON_COLUMN_ID:
        print(f"PUBLISH BUTTON CLICKED: item={item_id}", flush=True)
        if item_id in _active_publishes:
            print(f"DUPLICATE PUBLISH SUPPRESSED: item {item_id} "
                  f"is already publishing — ignoring webhook", flush=True)
            return {"status": "duplicate_publish_suppressed", "item_id": item_id}

        async def safe_run_publish(iid):
            if iid in _active_publishes:
                print(f"DUPLICATE PUBLISH SUPPRESSED (task level): "
                      f"item {iid} already publishing", flush=True)
                return
            _active_publishes.add(iid)
            try:
                from pipeline import publish_to_test
                await publish_to_test(iid)
            except Exception as e:
                print(f"PUBLISH CRASHED: {e}", flush=True)
                print(traceback.format_exc(), flush=True)
            finally:
                _active_publishes.discard(iid)
                print(f"RELEASED: item {iid} publish slot freed", flush=True)

        background_tasks.add_task(safe_run_publish, item_id)
        return {"status": "publish_started", "item_id": item_id}

    # Load-DVS-UAT-Data checkbox handler
    if col_id == LOAD_DVS_UAT_DATA_COLUMN_ID:
        if isinstance(new_val, str):
            try:
                new_val = json.loads(new_val)
            except Exception:
                pass
        checked = bool(new_val.get("checked")) if isinstance(new_val, dict) else False
        print(f"LOAD DVS UAT DATA CHECKBOX: item={item_id} checked={checked}",
              flush=True)
        if not checked:
            return {"status": "ignored", "reason": "checkbox unchecked"}
        if item_id in _active_dvs_uat_loads:
            print(f"DUPLICATE LOAD SUPPRESSED: item {item_id} is already "
                  f"loading DVS UAT data — ignoring webhook", flush=True)
            return {"status": "duplicate_load_suppressed", "item_id": item_id}

        async def safe_run_load_dvs_uat_data(iid):
            if iid in _active_dvs_uat_loads:
                print(f"DUPLICATE LOAD SUPPRESSED (task level): "
                      f"item {iid} already loading", flush=True)
                return
            _active_dvs_uat_loads.add(iid)
            try:
                from pipeline import load_dvs_uat_data
                await load_dvs_uat_data(iid)
            except Exception as e:
                print(f"LOAD_DVS_UAT_DATA CRASHED: {e}", flush=True)
                print(traceback.format_exc(), flush=True)
            finally:
                _active_dvs_uat_loads.discard(iid)
                print(f"RELEASED: item {iid} dvs-uat-load slot freed",
                      flush=True)

        background_tasks.add_task(safe_run_load_dvs_uat_data, item_id)
        return {"status": "load_dvs_uat_data_started", "item_id": item_id}

    # Send-to-AI trigger handler
    if col_id != TRIGGER_COLUMN_ID:
        print(f"COLUMN CHANGE: item={item_id} label_index=? label_text=?", flush=True)
        print(f"IGNORED: column {col_id} is not the trigger column", flush=True)
        return {"status": "ignored"}

    # Extract the label index from the value
    if isinstance(new_val, str):
        try:
            new_val = json.loads(new_val)
        except Exception:
            pass
    label_idx  = new_val.get("label", {}).get("index") if isinstance(new_val, dict) else None
    label_text = new_val.get("label", {}).get("text", "") if isinstance(new_val, dict) else ""
    print(f"COLUMN CHANGE: item={item_id} label_index={label_idx} label_text='{label_text}'",
          flush=True)

    # Only trigger when set to "Send to AI"
    if label_idx != TRIGGER_LABEL_INDEX:
        print(f"IGNORED: '{label_text}' is not '{TRIGGER_LABEL_TEXT}' - no action taken",
              flush=True)
        return {"status": f"ignored - changed to '{label_text}', only fires on '{TRIGGER_LABEL_TEXT}'"}

    # Duplicate-run guard
    if item_id in _active_items:
        print(f"DUPLICATE SUPPRESSED: item {item_id} is already running — ignoring webhook",
              flush=True)
        return {"status": "duplicate_suppressed", "item_id": item_id}

    # All checks passed — start the pipeline
    print(f"TRIGGERED: item {item_id} set to '{TRIGGER_LABEL_TEXT}' - starting pipeline",
          flush=True)

    async def safe_run_pipeline(iid):
        if iid in _active_items:
            print(f"DUPLICATE SUPPRESSED (task level): item {iid} already active",
                  flush=True)
            return
        _active_items.add(iid)
        try:
            if _pipeline_semaphore.locked():
                print(f"QUEUED: item {iid} waiting for current pipeline run to finish",
                      flush=True)
            async with _pipeline_semaphore:
                print(f"RUNNING: item {iid} acquired pipeline slot", flush=True)
                from pipeline import run_pipeline
                await run_pipeline(iid)
        except Exception as e:
            print(f"PIPELINE CRASHED: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
        finally:
            _active_items.discard(iid)
            print(f"RELEASED: item {iid} pipeline slot freed", flush=True)

    background_tasks.add_task(safe_run_pipeline, item_id)
    return {"status": "pipeline_started", "item_id": item_id}
