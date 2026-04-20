from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
import hmac, hashlib, json, os, traceback

app = FastAPI()
MONDAY_SIGNING_SECRET = os.environ.get("MONDAY_SIGNING_SECRET", "")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/webhook/monday")
async def monday_webhook(request: Request, background_tasks: BackgroundTasks):
    body    = await request.body()
    payload = json.loads(body)

    if "challenge" in payload:
        return {"challenge": payload["challenge"]}

    event   = payload.get("event", {})
    item_id = str(event.get("pulseId", ""))
    col_id  = event.get("columnId", "")
    new_val = event.get("value", {})

    # Log everything so we can see what Monday is actually sending
    print(f"FULL PAYLOAD: {json.dumps(payload)}", flush=True)
    print(f"COL_ID: {repr(col_id)}", flush=True)
    print(f"NEW_VAL: {repr(new_val)}", flush=True)
    print(f"ITEM_ID: {repr(item_id)}", flush=True)

    # Accept the trigger regardless of column/value for now
    # so we can confirm the pipeline fires
    if not item_id:
        return {"status": "no_item_id"}

    print(f"STARTING PIPELINE for item {item_id}", flush=True)

    async def safe_run_pipeline(iid):
        try:
            from pipeline import run_pipeline
            await run_pipeline(iid)
        except Exception as e:
            print(f"PIPELINE CRASHED: {e}", flush=True)
            print(traceback.format_exc(), flush=True)

    background_tasks.add_task(safe_run_pipeline, item_id)
    return {"status": "pipeline_started", "item_id": item_id}
