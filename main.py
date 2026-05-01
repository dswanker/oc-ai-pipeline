from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
import asyncio, hmac, hashlib, json, os, traceback

app = FastAPI()
MONDAY_SIGNING_SECRET = os.environ.get("MONDAY_SIGNING_SECRET", "")

TRIGGER_COLUMN_ID    = "single_select5ogcb0g"
TRIGGER_LABEL_INDEX  = 0      # 0 = "Send to AI"
TRIGGER_LABEL_TEXT   = "Send to AI"

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
