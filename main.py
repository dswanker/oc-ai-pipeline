from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from pipeline import run_pipeline
import hmac, hashlib, json, os

app = FastAPI()
MONDAY_SIGNING_SECRET = os.environ.get("MONDAY_SIGNING_SECRET", "")

def verify_monday_signature(body, signature):
    expected = hmac.new(
        MONDAY_SIGNING_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/webhook/monday")
async def monday_webhook(request: Request, background_tasks: BackgroundTasks):
    body    = await request.body()
    payload = json.loads(body)

    if "challenge" in payload:
        return {"challenge": payload["challenge"]}

    sig = request.headers.get("x-monday-signature", "")
    if MONDAY_SIGNING_SECRET and MONDAY_SIGNING_SECRET != "placeholder":
        if not verify_monday_signature(body, sig):
            raise HTTPException(status_code=401, detail="Invalid signature")

    event     = payload.get("event", {})
    item_id   = str(event.get("pulseId", ""))
    col_id    = event.get("columnId", "")
    new_val   = event.get("value", {})

    if col_id != "single_select5ogcb0g":
        return {"status": "ignored"}

    if new_val.get("label", {}).get("index") != 0:
        return {"status": "ignored"}

    background_tasks.add_task(run_pipeline, item_id)
    return {"status": "pipeline_started", "item_id": item_id}
