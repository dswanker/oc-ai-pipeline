from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
import asyncio, hmac, hashlib, json, os, traceback

app = FastAPI()
MONDAY_SIGNING_SECRET = os.environ.get("MONDAY_SIGNING_SECRET", "")

TRIGGER_COLUMN_ID    = "single_select5ogcb0g"
TRIGGER_LABEL_INDEX  = 0      # 0 = "Send to AI"
TRIGGER_LABEL_TEXT   = "Send to AI"

# Publish-to-Test button. Fires a column_value_changed event with
# columnId=PUBLISH_BUTTON_COLUMN_ID. Value payload is assumed empty
# ({} or null) — buttons don't carry values like status columns do.
# Verify payload shape on first real click and adjust if needed.
PUBLISH_BUTTON_COLUMN_ID = "button_mm3gwq70"

# Load-DVS-UAT-Data checkbox. Unlike a button, a checkbox event carries
# a value payload like {"checked": true} — we only fire the workflow on
# the transition TO checked (ignore unchecks). User can re-check after
# fixing whatever caused a previous failure.
LOAD_DVS_UAT_DATA_COLUMN_ID = "boolean_mm3gxe49"

# ── Concurrency guards ────────────────────────────────────────────────────────
# Limit to 1 simultaneous full pipeline run.  A second trigger while one is
# already running is queued and starts immediately after the first completes.
# Raising the limit risks OOM (each run holds ~3-4 MB in memory + a Chromium
# process for the build preview).
_pipeline_semaphore = asyncio.Semaphore(1)

# Track which monday item_ids are currently being processed.
# Prevents a double-click / double-webhook from spawning duplicate runs
# against the same row, which would upload duplicate files and set
# conflicting status values.
_active_items: set = set()

# Separate dedupe set for the Publish-to-Test button. Independent of
# _active_items because (a) publish is much shorter than the main
# pipeline (just 2 OC API calls) and doesn't share _pipeline_semaphore,
# and (b) a row should be allowed to publish while its main pipeline
# is queued/running.
_active_publishes: set = set()

# Dedupe set for the Load-DVS-UAT-Data checkbox. Same rationale as
# _active_publishes — short workflow, independent of the main pipeline.
_active_dvs_uat_loads: set = set()

@app.get("/health")
async def health():
    return {
        "status":       "ok",
        "active_items": list(_active_items),
        "queue_waiting": max(0, _pipeline_semaphore._value * -1
                             if hasattr(_pipeline_semaphore, '_value') else 0),
    }

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

    # ── Publish-to-Test button handler ──────────────────────────────────
    # The Publish to Test button fires a column_value_changed event with
    # columnId=PUBLISH_BUTTON_COLUMN_ID. Per design, no value payload is
    # required — the click itself IS the action. We don't read new_val
    # here because button events likely send {} or null (unverified —
    # actual payload shape needs confirmation on first real click).
    if col_id == PUBLISH_BUTTON_COLUMN_ID:
        print(f"PUBLISH BUTTON CLICKED: item={item_id}", flush=True)

        if item_id in _active_publishes:
            print(f"DUPLICATE PUBLISH SUPPRESSED: item {item_id} "
                  f"is already publishing — ignoring webhook", flush=True)
            return {"status": "duplicate_publish_suppressed", "item_id": item_id}

        async def safe_run_publish(iid):
            """Run pipeline.publish_to_test() with dedupe + error trap.

            Doesn't share _pipeline_semaphore because publish is a
            short, OC-API-only flow (2 calls) — gating it on the main
            pipeline's heavy-work semaphore would unnecessarily queue
            it behind builds. Dedupe via _active_publishes only.
            """
            if iid in _active_publishes:
                print(f"DUPLICATE PUBLISH SUPPRESSED (task level): "
                      f"item {iid} already publishing", flush=True)
                return
            _active_publishes.add(iid)
            try:
                # Lazy import — pipeline.publish_to_test may not exist
                # yet at deploy time. If missing, ImportError lands in
                # the except below with a clear traceback.
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

    # ── Load-DVS-UAT-Data checkbox handler ──────────────────────────────
    # Checkbox events carry a value payload like {"checked": true|false}.
    # We only fire when transitioning TO checked — uncheck events are
    # silently ignored so the user can clear the checkbox without
    # re-running the workflow.
    if col_id == LOAD_DVS_UAT_DATA_COLUMN_ID:
        # Parse the checkbox value defensively (Monday sometimes sends
        # the value as a JSON string)
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
            """Run pipeline.load_dvs_uat_data() with dedupe + error trap.

            Same pattern as safe_run_publish — short workflow, no semaphore
            gating, lazy import so pipeline.load_dvs_uat_data may not exist
            yet without breaking this module's import.
            """
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

    # ── Send-to-AI trigger handler (existing) ───────────────────────────
    # Only care about our specific trigger column
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

    # ── Duplicate-run guard ───────────────────────────────────────────────
    if item_id in _active_items:
        print(f"DUPLICATE SUPPRESSED: item {item_id} is already running — ignoring webhook",
              flush=True)
        return {"status": "duplicate_suppressed", "item_id": item_id}

    # All checks passed — start the pipeline
    print(f"TRIGGERED: item {item_id} set to '{TRIGGER_LABEL_TEXT}' - starting pipeline",
          flush=True)

    async def safe_run_pipeline(iid):
        # Guard: prevent the same item running twice simultaneously
        if iid in _active_items:
            print(f"DUPLICATE SUPPRESSED (task level): item {iid} already active",
                  flush=True)
            return
        _active_items.add(iid)
        try:
            # Semaphore: queue this run until the current run (if any) finishes.
            # We log when waiting so Railway shows what's happening.
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
