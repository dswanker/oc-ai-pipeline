"""
gmail_oauth.py
Per-team-member Gmail OAuth2 flow for the email-change-intake skill.

Flow:
1. email_change_intake detects missing token for a team member
2. Calls generate_gmail_auth_link(monday_user_id) → posts bell notification
3. Team member clicks link → GET /auth/gmail/{monday_user_id}
4. Redirects to Google consent screen (gmail.readonly scope)
5. Google redirects to GET /auth/gmail/callback?code=...&state=...
6. Callback exchanges code for token, saves to
   /data/gmail_sessions/{monday_user_id}.json
7. Next hourly run finds token → email monitoring starts

Required environment variables:
  GOOGLE_CLIENT_ID      — from Google Cloud Console OAuth2 credentials
  GOOGLE_CLIENT_SECRET  — from Google Cloud Console OAuth2 credentials
  RAILWAY_PUBLIC_DOMAIN — set automatically by Railway
  AUTH_SECRET_KEY       — already used by auth_manager.py

Google Cloud Console setup (one-time):
  1. Go to console.cloud.google.com → APIs & Services → Credentials
  2. Create OAuth 2.0 Client ID (Web application)
  3. Add authorised redirect URI:
     https://{RAILWAY_PUBLIC_DOMAIN}/auth/gmail/callback
  4. Enable the Gmail API under APIs & Services → Enabled APIs
  5. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to Railway env vars
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# ── Constants ─────────────────────────────────────────────────────────────────

GMAIL_SESSIONS_DIR = Path("/data/gmail_sessions")
GMAIL_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

GOOGLE_AUTH_URL    = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL   = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL= "https://www.googleapis.com/oauth2/v3/userinfo"
GMAIL_API_BASE     = "https://gmail.googleapis.com/gmail/v1"

# Read-only Gmail scope — we never send, delete, or modify email
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# State token lifetime — user has 30 minutes to complete the OAuth flow
STATE_MAX_AGE = 1800

SECRET_KEY = os.environ.get("AUTH_SECRET_KEY", "")
_serializer = URLSafeTimedSerializer(SECRET_KEY)


# ── Token storage ─────────────────────────────────────────────────────────────

def _token_path(monday_user_id: str) -> Path:
    return GMAIL_SESSIONS_DIR / f"{monday_user_id}.json"

def token_exists(monday_user_id: str) -> bool:
    return _token_path(monday_user_id).exists()

def load_token(monday_user_id: str) -> dict | None:
    p = _token_path(monday_user_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None

def save_token(monday_user_id: str, token_data: dict):
    GMAIL_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _token_path(monday_user_id).write_text(json.dumps(token_data))

def delete_token(monday_user_id: str):
    p = _token_path(monday_user_id)
    if p.exists():
        p.unlink()


# ── OAuth state helpers ───────────────────────────────────────────────────────

def _make_state(monday_user_id: str) -> str:
    """Sign monday_user_id into a tamper-proof state parameter."""
    return _serializer.dumps(monday_user_id, salt="gmail-oauth-state")

def _verify_state(state: str) -> tuple[str | None, str | None]:
    """Verify state and return (monday_user_id, error)."""
    try:
        monday_user_id = _serializer.loads(
            state, salt="gmail-oauth-state", max_age=STATE_MAX_AGE)
        return monday_user_id, None
    except SignatureExpired:
        return None, "OAuth state expired — please request a new auth link."
    except BadSignature:
        return None, "Invalid OAuth state — possible CSRF attempt."


# ── Auth URL generation ───────────────────────────────────────────────────────

def build_auth_url(monday_user_id: str) -> str:
    """
    Build the Google OAuth2 consent URL for this team member.
    Called by generate_gmail_auth_link() in pipeline.py.
    """
    client_id    = os.environ.get("GOOGLE_CLIENT_ID", "")
    base_url     = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    redirect_uri = f"{base_url}/auth/gmail/callback"
    state        = _make_state(monday_user_id)

    params = {
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         GMAIL_SCOPE,
        "access_type":   "offline",   # request refresh token
        "prompt":        "consent",   # force consent to always get refresh token
        "state":         state,
    }
    from urllib.parse import urlencode
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def build_initiation_url(monday_user_id: str) -> str:
    """
    URL the team member clicks from their bell notification.
    Points to our /auth/gmail/{monday_user_id} route which then
    redirects to Google.
    """
    base_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    return f"{base_url}/auth/gmail/{monday_user_id}"


# ── Token exchange ────────────────────────────────────────────────────────────

async def exchange_code_for_token(code: str,
                                   monday_user_id: str) -> dict | None:
    """
    Exchange an authorisation code for access + refresh tokens.
    Saves the token to disk and returns it, or returns None on failure.
    """
    client_id     = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    base_url      = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    redirect_uri  = f"{base_url}/auth/gmail/callback"

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(GOOGLE_TOKEN_URL, data={
            "code":          code,
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        })

    if r.status_code != 200:
        print(f"gmail_oauth: token exchange failed {r.status_code}: "
              f"{r.text}", flush=True)
        return None

    token_data = r.json()
    token_data["obtained_at"] = int(time.time())
    token_data["monday_user_id"] = monday_user_id

    # Fetch the Gmail address for logging
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            ui = await c.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"}
            )
        token_data["gmail_address"] = ui.json().get("email", "")
    except Exception:
        token_data["gmail_address"] = ""

    save_token(monday_user_id, token_data)
    print(f"gmail_oauth: token saved for monday_user_id={monday_user_id} "
          f"email={token_data.get('gmail_address', '?')}", flush=True)
    return token_data


# ── Token refresh ─────────────────────────────────────────────────────────────

async def refresh_token_if_needed(monday_user_id: str) -> dict | None:
    """
    Load the stored token, refresh it if it is within 5 minutes of expiry,
    and return the (possibly refreshed) token dict.
    Returns None if no token exists or refresh fails.
    """
    token_data = load_token(monday_user_id)
    if not token_data:
        return None

    expires_in   = token_data.get("expires_in", 3600)
    obtained_at  = token_data.get("obtained_at", 0)
    expires_at   = obtained_at + expires_in
    now          = int(time.time())
    refresh_token= token_data.get("refresh_token")

    # Still valid with >5 min buffer
    if now < expires_at - 300:
        return token_data

    # Need refresh
    if not refresh_token:
        print(f"gmail_oauth: no refresh token for {monday_user_id} — "
              f"re-auth required", flush=True)
        return None

    client_id     = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(GOOGLE_TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id":     client_id,
            "client_secret": client_secret,
            "grant_type":    "refresh_token",
        })

    if r.status_code != 200:
        print(f"gmail_oauth: refresh failed for {monday_user_id}: "
              f"{r.status_code} {r.text}", flush=True)
        return None

    new_data = r.json()
    token_data["access_token"] = new_data["access_token"]
    token_data["expires_in"]   = new_data.get("expires_in", 3600)
    token_data["obtained_at"]  = int(time.time())
    # Google only returns refresh_token on first auth — keep existing one
    if "refresh_token" in new_data:
        token_data["refresh_token"] = new_data["refresh_token"]

    save_token(monday_user_id, token_data)
    print(f"gmail_oauth: token refreshed for {monday_user_id}", flush=True)
    return token_data


# ── Gmail API helpers ─────────────────────────────────────────────────────────

async def fetch_unread_emails(monday_user_id: str,
                               after_date: str | None = None,
                               max_results: int = 50) -> list[dict]:
    """
    Fetch unread emails for a team member using the stored OAuth token.
    after_date: "YYYY-MM-DD" string — if provided, only fetch emails after this date.
    Returns list of message dicts with subject, from, body, received_at etc.
    Raises GmailAuthRequired if token missing or refresh fails.
    """
    token_data = await refresh_token_if_needed(monday_user_id)
    if not token_data:
        raise GmailAuthRequired(
            f"No valid Gmail token for monday_user_id={monday_user_id}. "
            f"Team member must complete Gmail OAuth setup."
        )

    access_token = token_data["access_token"]
    auth_header  = {"Authorization": f"Bearer {access_token}"}

    # Build search query
    query_parts = ["is:unread", "-from:me", "-category:promotions",
                   "-category:social", "-category:updates"]
    if after_date:
        date_fmt = after_date.replace("-", "/")
        query_parts.append(f"after:{date_fmt}")
    query = " ".join(query_parts)

    # Step 1: list message IDs
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{GMAIL_API_BASE}/users/me/messages",
            headers=auth_header,
            params={"q": query, "maxResults": max_results,
                    "fields": "messages/id,nextPageToken"},
        )

    if r.status_code == 401:
        # Token invalid even after refresh attempt — re-auth needed
        delete_token(monday_user_id)
        raise GmailAuthRequired(
            f"Gmail token rejected for {monday_user_id}. Re-authentication required."
        )

    if r.status_code != 200:
        print(f"gmail_oauth: message list failed {r.status_code}: "
              f"{r.text[:200]}", flush=True)
        return []

    message_ids = [m["id"] for m in r.json().get("messages", [])]
    if not message_ids:
        return []

    # Step 2: fetch each message in full
    emails = []
    async with httpx.AsyncClient(timeout=30) as c:
        for msg_id in message_ids:
            try:
                mr = await c.get(
                    f"{GMAIL_API_BASE}/users/me/messages/{msg_id}",
                    headers=auth_header,
                    params={"format": "full",
                            "fields": "id,threadId,internalDate,"
                                      "payload/headers,payload/parts,"
                                      "payload/body"},
                )
                if mr.status_code != 200:
                    continue
                msg = mr.json()
                emails.append(_parse_message(msg))
            except Exception as e:
                print(f"gmail_oauth: failed to fetch message {msg_id}: "
                      f"{e}", flush=True)
                continue

    return emails


def _parse_message(msg: dict) -> dict:
    """Extract subject, from, plain-text body, and timestamp from a Gmail message."""
    headers = {}
    for h in msg.get("payload", {}).get("headers", []):
        headers[h["name"].lower()] = h["value"]

    subject     = headers.get("subject", "(no subject)")
    from_raw    = headers.get("from", "")
    received_ts = int(msg.get("internalDate", 0)) // 1000

    # Parse "Display Name <email@example.com>" format
    import re
    match = re.match(r'^"?([^"<]+?)"?\s*<([^>]+)>$', from_raw.strip())
    if match:
        from_name  = match.group(1).strip()
        from_email = match.group(2).strip()
    else:
        from_name  = from_raw
        from_email = from_raw

    # Extract plain text body
    body = _extract_body(msg.get("payload", {}))

    from datetime import datetime, timezone
    received_at = datetime.fromtimestamp(
        received_ts, tz=timezone.utc
    ).isoformat() if received_ts else ""

    return {
        "message_id":  msg.get("id", ""),
        "thread_id":   msg.get("threadId", ""),
        "subject":     subject,
        "from_name":   from_name,
        "from_email":  from_email,
        "body":        body,
        "received_at": received_at,
    }


def _extract_body(payload: dict) -> str:
    """Recursively extract plain text from a Gmail message payload."""
    import base64

    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        try:
            return base64.urlsafe_b64decode(
                body_data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    # Recurse into parts
    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    # Fallback: try html part and strip tags
    if mime_type == "text/html" and body_data:
        try:
            html = base64.urlsafe_b64decode(
                body_data + "==").decode("utf-8", errors="replace")
            import re
            return re.sub(r"<[^>]+>", " ", html).strip()
        except Exception:
            pass

    return ""


class GmailAuthRequired(Exception):
    pass


# ── Success/error HTML pages ──────────────────────────────────────────────────

def render_success_page(gmail_address: str, member_name: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Gmail Connected</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 520px; margin: 80px auto; padding: 0 24px;
          text-align: center; color: #222; }}
  .icon {{ font-size: 56px; margin-bottom: 16px; }}
  h1 {{ color: #1B3A6B; font-size: 22px; }}
  .pill {{ display: inline-block; background: #EAF3DE; color: #27500A;
           border-radius: 20px; padding: 4px 14px; font-size: 14px;
           margin: 8px 0 20px; }}
  p {{ color: #555; line-height: 1.6; }}
  .close {{ margin-top: 32px; font-size: 13px; color: #999; }}
</style>
</head>
<body>
<div class="icon">✅</div>
<h1>Gmail Connected</h1>
<div class="pill">{gmail_address}</div>
<p>Hi <strong>{member_name}</strong> — your Gmail inbox is now connected
to the OpenClinica email monitoring system.</p>
<p>The system will check your inbox every hour and route any study build
change requests through the pipeline automatically, based on your mode
setting on the OC Staff board.</p>
<p class="close">You can close this window.</p>
</body>
</html>"""


def render_error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Gmail Connection Error</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 520px; margin: 80px auto; padding: 0 24px;
          text-align: center; color: #222; }}
  .icon {{ font-size: 56px; margin-bottom: 16px; }}
  h1 {{ color: #C0392B; font-size: 22px; }}
  p {{ color: #555; line-height: 1.6; }}
</style>
</head>
<body>
<div class="icon">❌</div>
<h1>Connection Failed</h1>
<p>{message}</p>
<p>Please contact Dan to request a new auth link.</p>
</body>
</html>"""
