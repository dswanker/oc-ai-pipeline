"""
Authentication manager for OpenClinica form uploads via Chrome extension.

Flow:
1. Pipeline checks for a saved Playwright session for the user
2. If missing, generates a one-time auth link and posts it to monday
3. User clicks link -> /auth endpoint validates the token (peek-only) and
   renders an instructions page showing the token + extension download
4. User installs the OC Session Capture Chrome extension (sideload), signs
   into OpenClinica normally, clicks the extension icon, pastes the token,
   clicks "Capture & Send"
5. Extension POSTs cookies + localStorage to /api/session/upload
6. /api/session/upload validates the token (consuming it this time), then
   writes the Playwright storage_state JSON to /data/browser_sessions/{email}.json
7. User re-triggers the pipeline -> session is found -> forms upload succeeds
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SESSIONS_DIR = Path("/data/browser_sessions")
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Token serializer for one-time auth links. AUTH_SECRET_KEY is also used
# by the SessionMiddleware in main.py, but no longer for OAuth state (that
# flow was removed in favour of the Chrome extension approach).
SECRET_KEY = os.environ["AUTH_SECRET_KEY"]
serializer = URLSafeTimedSerializer(SECRET_KEY)

# Token lifetime in seconds. The user must click the auth link, sideload
# the extension, sign into OC, and POST cookies within this window. One
# hour is generous for the bootstrap flow.
TOKEN_MAX_AGE_SECONDS = 3600


# ─────────────────────────────────────────────────────────────────────────────
# Authentication Manager
# ─────────────────────────────────────────────────────────────────────────────

class AuthManager:
    """Manages one-time tokens and per-user browser session files."""

    def generate_auth_link(self, email: str, base_url: str,
                           context: str = "pipeline",
                           item_id: str = "") -> str:
        """
        Generate a one-time auth link for the given email.

        Args:
            email:    User's OC SSO email (e.g. user@openclinica.com)
            base_url: Railway public URL with no trailing slash
            context:  'pipeline' (default) or 'uat' — controls instructions page
            item_id:  Monday item ID — if provided, session upload will post
                      a completion notice back to the Monday item so the user
                      knows when auth is done and can re-trigger.

        Returns:
            Full /auth URL with a signed token query parameter.
        """
        payload = {"email": email, "item_id": item_id}
        token = serializer.dumps(payload, salt="auth-token")
        params = urlencode({"token": token, "context": context})
        return f"{base_url}/auth?{params}"

    def validate_token(self, token: str) -> tuple[str | None, str | None]:
        """
        Validate a signed auth token and return (email, error_message).

        Pure signature + max-age check — no "already used" bookkeeping.
        Payload may be a plain email string (legacy) or a dict with
        {"email": ..., "item_id": ...} (current).

        Returns:
            (email, None) on success; (None, error_message) on failure.
        """
        try:
            payload = serializer.loads(
                token, salt="auth-token", max_age=TOKEN_MAX_AGE_SECONDS
            )
        except SignatureExpired:
            return None, "This auth link has expired (valid for 1 hour)"
        except BadSignature:
            return None, "Invalid auth link"
        # Support both legacy string payload and new dict payload
        if isinstance(payload, dict):
            return payload.get("email"), None
        return payload, None

    def validate_token_full(self, token: str) -> tuple[str | None, str | None, str]:
        """Like validate_token but also returns item_id from the payload.

        Returns:
            (email, error_message, item_id)
        """
        try:
            payload = serializer.loads(
                token, salt="auth-token", max_age=TOKEN_MAX_AGE_SECONDS
            )
        except SignatureExpired:
            return None, "This auth link has expired (valid for 1 hour)", ""
        except BadSignature:
            return None, "Invalid auth link", ""
        if isinstance(payload, dict):
            return payload.get("email"), None, payload.get("item_id", "")
        return payload, None, ""

    def session_exists(self, email: str) -> bool:
        """Check if a saved Playwright session file exists for this email."""
        return (SESSIONS_DIR / f"{email}.json").exists()

    def get_session_path(self, email: str) -> Path:
        """Get the session file path for this email."""
        return SESSIONS_DIR / f"{email}.json"


# ─────────────────────────────────────────────────────────────────────────────
# Session upload (called from main.py's /api/session/upload route)
# ─────────────────────────────────────────────────────────────────────────────

def write_session_state(email: str, storage_state: dict) -> Path:
    """Write Playwright storage_state JSON for this user to disk."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{email}.json"
    path.write_text(json.dumps(storage_state))
    return path


async def handle_session_upload(token: str, storage_state) -> dict:
    """Validate a one-time token and persist storage_state to disk.

    Returns a dict suitable for JSONResponse:
      success: {"ok": True, "email": <email>, "cookies": <count>}
      failure: {"ok": False, "error": <msg>, "status": <http_status>}
    """
    am = AuthManager()
    email, error, item_id = am.validate_token_full(token)
    if error:
        return {"ok": False, "error": error, "status": 400}
    if not isinstance(storage_state, dict) or "cookies" not in storage_state:
        return {
            "ok": False,
            "error": "storage_state missing 'cookies'",
            "status": 400,
        }
    path = write_session_state(email, storage_state)
    n = len(storage_state.get("cookies", []))
    n_origins = len(storage_state.get("origins", []))
    n_ls = sum(len(o.get("localStorage", [])) for o in storage_state.get("origins", []))
    print(f"[auth] wrote session for {email}: {n} cookies, {n_origins} origins, "
          f"{n_ls} localStorage keys -> {path}", flush=True)

    # Post completion notice back to the Monday item so the user has a clear
    # signal that auth is done and can re-trigger "Send to AI".
    if item_id:
        try:
            import httpx as _httpx, os as _os
            _api_key = _os.environ.get("MONDAY_API_KEY", "")
            _board_id = _os.environ.get("MONDAY_BOARD_ID", "18409146946")
            if _api_key:
                _gql_log = """
                mutation($item_id: ID!, $body: String!) {
                    create_update(item_id: $item_id, body: $body) { id }
                }"""
                _body = (f"✅ OpenClinica session captured for {email}. "
                         f"You can now trigger <b>Send to AI</b> to resume the pipeline.")
                _r = _httpx.post(
                    "https://api.monday.com/v2",
                    json={"query": _gql_log, "variables": {
                        "item_id": str(item_id), "body": _body}},
                    headers={"Authorization": _api_key,
                             "API-Version": "2024-01"},
                    timeout=10,
                )
                _gql_status = """
                mutation($item_id: ID!, $board_id: ID!, $col_id: String!, $val: JSON!) {
                    change_column_value(item_id: $item_id, board_id: $board_id,
                                       column_id: $col_id, value: $val) { id }
                }"""
                _httpx.post(
                    "https://api.monday.com/v2",
                    json={"query": _gql_status, "variables": {
                        "item_id": str(item_id),
                        "board_id": str(_board_id),
                        "col_id": "color_mm2h9g3m",
                        "val": '{"label": "Paused for Authentication — Ready to Trigger"}',
                    }},
                    headers={"Authorization": _api_key,
                             "API-Version": "2024-01"},
                    timeout=10,
                )
                print(f"[auth] Monday callback sent for item {item_id}", flush=True)
        except Exception as _cb_err:
            print(f"[auth] Monday callback failed (non-fatal): {_cb_err}", flush=True)

    return {"ok": True, "email": email, "cookies": n}


# ─────────────────────────────────────────────────────────────────────────────
# Instructions page (rendered by main.py's /auth route)
# ─────────────────────────────────────────────────────────────────────────────

def render_instructions_page(token: str, email: str,
                             context: str = "pipeline",
                             clinical_host: str = "") -> str:
    """Self-contained HTML page for the bootstrap instructions.

    context: 'pipeline' — standard auth (build host only)
             'uat'      — UAT auth (must also have clinical host open)
    clinical_host: e.g. 'cust1.eu.openclinica.io' — shown in UAT instructions
    """
    from html import escape as _esc
    # Derive subdomain from clinical_host (e.g. "cust1.eu.openclinica.io" → "cust1")
    # Fall back to env var only if clinical_host is not provided.
    if clinical_host:
        subdomain = clinical_host.split(".")[0]
    else:
        subdomain = os.environ.get("OC_DEFAULT_SUBDOMAIN", "cust1")
    build_url = f"https://{subdomain}.build.openclinica.io/#/account-study"
    email_esc       = _esc(email)
    token_esc       = _esc(token)
    designer_esc    = _esc(build_url)
    clinical_esc    = _esc(f"https://{clinical_host}/OpenClinica" if clinical_host else "")

    is_uat = (context == "uat")
    heading = "OpenClinica Session Setup — UAT Data Load" if is_uat else "OpenClinica Session Setup"
    lead = (
        f"Hi <strong>{email_esc}</strong> — the UAT loader needs your OpenClinica "
        f"session. Make sure you have at least one OpenClinica tab open, then follow "
        f"the steps below (~2 minutes). The extension captures all sessions automatically."
        if is_uat else
        f"Hi <strong>{email_esc}</strong> — the pipeline needs your "
        f"OpenClinica session before it can publish forms. Steps below take ~90 seconds."
    )

    # For UAT: add a step to open the legacy data entry interface so its
    # session cookies get captured alongside the build app cookies.
    if is_uat and clinical_host:
        clinical_step = (
            f'<li>Open this tab in your browser (keep it open): '
            f'<a href="https://{subdomain}.build.openclinica.io/#/account-study" '
            f'target="_blank" class="designer">https://{subdomain}.build.openclinica.io/#/account-study</a> '
            f'— this captures the data entry session needed for UI testing.</li>'
        )
    else:
        clinical_step = ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>OpenClinica Session Setup</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          max-width: 720px; margin: 40px auto; padding: 0 24px; color: #222;
          line-height: 1.5; }}
  h1 {{ color: #0085ff; font-size: 22px; margin-bottom: 4px; }}
  .lead {{ color: #555; margin-top: 0; }}
  .code-box {{ display: flex; align-items: center; gap: 8px; margin: 16px 0;
               padding: 12px 14px; background: #f6f8fa; border: 1px solid #d1d9e0;
               border-radius: 6px; }}
  code {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px;
          word-break: break-all; flex: 1; }}
  button {{ padding: 6px 12px; background: #0085ff; color: white; border: none;
            border-radius: 4px; font-size: 12px; cursor: pointer; white-space: nowrap; }}
  button:hover {{ background: #006fdb; }}
  .btn-link {{ display: inline-block; padding: 10px 16px; margin: 8px 0;
               background: #0085ff; color: white; text-decoration: none;
               border-radius: 4px; font-weight: 500; }}
  .btn-link:hover {{ background: #006fdb; }}
  ol {{ padding-left: 22px; }}
  ol li {{ margin: 8px 0; }}
  .designer {{ font-family: ui-monospace, monospace; background: #f6f8fa;
               padding: 1px 6px; border-radius: 3px; color: #0050a0;
               text-decoration: none; }}
  .toast {{ display: none; margin-left: 8px; color: #1c6e3d; font-size: 12px; }}
  .toast.show {{ display: inline; }}
  .uat-banner {{ background: #fff8e1; border: 1px solid #f9a825; border-radius: 6px;
                 padding: 10px 14px; margin-bottom: 16px; font-size: 14px; }}
</style>
</head>
<body>
<h1>{_esc(heading)}</h1>
{'<div class="uat-banner">ℹ️ UAT mode — make sure you have at least one OpenClinica tab open in this browser. The extension will automatically capture all OpenClinica sessions.</div>' if is_uat else ''}
<p class="lead">{lead}</p>

<p><strong>1. Your one-time code:</strong></p>
<div class="code-box">
  <code id="token">{token_esc}</code>
  <button onclick="copyToken()">Copy</button>
  <span class="toast" id="toast">Copied!</span>
</div>

<p><strong>2. Download &amp; install the Chrome extension (first time only):</strong></p>
<p><a class="btn-link" href="https://drive.google.com/uc?export=download&id=17R55ZMYvV9YR9B12YrPIrqbA-YuGdGzC" target="_blank">Download extension from Google Drive (v1.0.1)</a></p>
<ol>
  <li>Unzip the downloaded file — you will get a folder called <code>oc-session-capture</code>.</li>
  <li><strong>Save this folder somewhere permanent</strong> (e.g. your Documents folder) — Chrome loads the extension from this folder every time, so don't delete it.</li>
  <li>Open <span class="designer">chrome://extensions</span>, toggle
      <em>Developer mode</em> (top right), click <em>Load unpacked</em>,
      and select the saved folder.</li>
  <li>The extension will appear as <strong>OC Session Capture 1.0.1</strong>.
      You only need to do this once — skip to step 3 on future runs.</li>
</ol>

<p><strong>3. Capture your session:</strong></p>
<ol>
  <li>Make sure you are signed into
      <a class="designer" href="{designer_esc}" target="_blank">OpenClinica</a>
      in this browser (any OpenClinica tab is fine).</li>{clinical_step}
  <li>Click the <strong>OC Session Capture</strong> extension icon in your Chrome toolbar,
      paste the one-time code above, and click <em>Capture &amp; Send</em>.</li>
  <li>You will see a green ✅ confirmation with the number of cookies and tabs captured.</li>
  <li>Return to monday and re-trigger your pipeline (set the trigger
      column back to "Send to AI").</li>
</ol>

<script>
function copyToken() {{
  const tok = document.getElementById('token').textContent;
  navigator.clipboard.writeText(tok).then(() => {{
    const t = document.getElementById('toast');
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 1500);
  }});
}}
</script>
</body>
</html>"""
