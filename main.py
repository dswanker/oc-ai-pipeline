from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.middleware.sessions import SessionMiddleware
import hmac, hashlib, json, os, time, traceback, asyncio

# Auth manager — Chrome-extension session-capture flow
from auth_manager import (
    AuthManager,
    handle_session_upload,
    render_instructions_page,
)
from monday_client import COL, PIPELINE_CONFIG_ITEM_ID, download_column_file

app = FastAPI()

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

@app.get("/health")
async def health():
    return {"status": "ok"}

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
# TEMPORARY debug endpoint — DOM dumper for OC designer selector discovery.
# Gated by DEBUG_KEY env var. Remove or gate harder (or behind staff-only
# auth) once we've mapped the real upload-flow selectors.
# ─────────────────────────────────────────────────────────────────────────────

# JS executed via page.evaluate to extract interactive elements. Defined at
# module scope so the multi-line string isn't reconstructed on every call.
_DEBUG_DOM_JS = """
() => {
  const info = (el) => ({
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    cls: (typeof el.className === 'string' && el.className) ? el.className : null,
    text: (el.innerText || el.value || '').trim().slice(0, 80),
    type: el.getAttribute('type'),
    name: el.getAttribute('name'),
    placeholder: el.getAttribute('placeholder'),
    href: el.getAttribute('href'),
    data: Object.fromEntries([...el.attributes]
      .filter(a => a.name.startsWith('data-'))
      .map(a => [a.name, a.value])),
  });
  const grab = (q) => [...document.querySelectorAll(q)].map(info);
  return {
    buttons: grab('button, [role=button], input[type=button], input[type=submit]'),
    links: grab('a[href]').slice(0, 120),
    inputs: grab('input, select, textarea'),
    iframes: document.querySelectorAll('iframe').length,
    bodyText: (document.body ? document.body.innerText : '').slice(0, 2000),
  };
}
"""


@app.get("/debug/dom")
async def debug_dom(
    url: str,
    key: str = "",
    email: str = "dswanker@openclinica.com",
    wait: int = 4000,
    click: str = "",
    screenshot: bool = False,
    frame: int = -1,
    ready_sel: str = "",
):
    """Load a saved OC Playwright session, navigate, and return live DOM.

    Auth-gated and host-scoped so a captured signed-in session can't be
    used as a generic web navigator. See section header for removal plan.
    """
    # 1. Auth gate — empty DEBUG_KEY env disables the endpoint entirely.
    debug_key = os.environ.get("DEBUG_KEY", "")
    if not debug_key or key != debug_key:
        return JSONResponse(
            {"error": "debug disabled or bad key"}, status_code=403)

    # 2. Host safety — only allow OC hosts to limit blast radius.
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "")
    if not host.endswith("openclinica.io"):
        return JSONResponse(
            {"error": "url must be *.openclinica.io"}, status_code=400)

    # 3. Session check — reuse oc_form_publisher's path convention.
    from oc_form_publisher import SESSION_DIR
    session_path = os.path.join(SESSION_DIR, f"{email}.json")
    if not os.path.exists(session_path):
        return JSONResponse(
            {"error": f"no session file for {email}"}, status_code=404)

    # 4-10. Lazy-load playwright; capture URL/title BEFORE the browser is
    # torn down (page object is dead after browser.close()).
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    storage_state=session_path)
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle",
                                timeout=30000)

                # Optional readiness gate. wait_for_selector returns as
                # soon as the selector first appears in the DOM — much
                # tighter than a fixed sleep, which sometimes snapshots
                # mid-render (e.g. OC's "Processing user info..."
                # transient between SSO callback and board paint).
                # Best-effort: a timeout doesn't abort, it just gets
                # recorded so the partial DOM is still returned.
                # If `frame` is also set, wait inside that frame
                # instead of the top page (page.wait_for_selector only
                # searches the top-level frame's DOM).
                ready_error = None
                if ready_sel:
                    ctx = page
                    if 0 <= frame < len(page.frames):
                        ctx = page.frames[frame]
                    try:
                        await ctx.wait_for_selector(
                            ready_sel, timeout=20000)
                    except Exception as e:
                        ready_error = f"{type(e).__name__}: {e}"

                # Keep the fixed settle buffer after readiness — handy
                # for catching post-render reflow + late iframe load.
                await page.wait_for_timeout(wait)

                click_error = None
                if click:
                    try:
                        await page.click(click, timeout=5000)
                        await page.wait_for_timeout(wait)
                    except Exception as e:
                        click_error = f"{type(e).__name__}: {e}"

                # Enumerate all frames (top page + every iframe) so callers
                # can discover which index hosts the form they need.
                frames_info = [
                    {"index": i, "url": fr.url, "name": fr.name}
                    for i, fr in enumerate(page.frames)
                ]

                # Pick the eval target. frame=-1 (default) → top-level
                # page. 0..len-1 → that index in page.frames. Anything
                # out of range silently falls back to the top page —
                # consult the returned "frames" list to find a valid
                # index, then re-call.
                target = page
                if 0 <= frame < len(page.frames):
                    target = page.frames[frame]

                dom = await target.evaluate(_DEBUG_DOM_JS)

                shot_path = None
                if screenshot:
                    try:
                        shot_path = "/data/browser_sessions/debug_dom.png"
                        await page.screenshot(path=shot_path, full_page=True)
                    except Exception:
                        shot_path = None

                final_url = page.url
                title = await page.title()
            finally:
                await browser.close()

        return JSONResponse({
            "final_url":   final_url,
            "title":       title,
            "click":       click or None,
            "click_error": click_error,
            "ready_error": ready_error,
            "frames":      frames_info,
            "dom":         dom,
            "screenshot":  shot_path,
        })
    except Exception as e:
        return JSONResponse(
            {"error": f"{type(e).__name__}: {e}"}, status_code=500)


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
