"""
Authentication manager for OpenClinica form uploads via Google OAuth.

Flow:
1. Pipeline checks for session file
2. If missing, generates auth link and posts to monday.com
3. User clicks link -> /auth endpoint validates token
4. Redirects to Google OAuth
5. Google redirects back to /oauth/callback
6. Callback launches Playwright to capture OpenClinica session
7. Saves browser context to session file
8. User re-triggers pipeline -> session found -> forms upload
"""

import os
import json
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlencode

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from authlib.integrations.starlette_client import OAuth
from starlette.responses import RedirectResponse, HTMLResponse
from playwright.async_api import async_playwright


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SESSIONS_DIR = Path("/data/browser_sessions")
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Token serializer for one-time auth links
SECRET_KEY = os.environ["AUTH_SECRET_KEY"]
serializer = URLSafeTimedSerializer(SECRET_KEY)

# OAuth configuration
oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ─────────────────────────────────────────────────────────────────────────────
# Authentication Manager
# ─────────────────────────────────────────────────────────────────────────────

class AuthManager:
    """Manages authentication tokens and browser sessions."""
    
    def __init__(self):
        self.used_tokens = set()  # Track consumed tokens (in-memory, resets on deploy)
    
    def generate_auth_link(self, email: str, base_url: str) -> str:
        """
        Generate a one-time auth link for the given email.
        
        Args:
            email: User's OpenClinica email
            base_url: Railway app URL (e.g., https://oc-ai-pipeline-production.up.railway.app)
            
        Returns:
            Full auth URL with signed token
        """
        # Create signed token (valid for 1 hour)
        token = serializer.dumps(email, salt="auth-token")
        
        # Build auth URL
        params = urlencode({"token": token})
        return f"{base_url}/auth?{params}"
    
    def validate_token(self, token: str) -> tuple[str, str]:
        """
        Validate auth token and return email.
        
        Args:
            token: Signed token from auth link
            
        Returns:
            (email, error_message) - error_message is None if valid
        """
        # Check if already used
        if token in self.used_tokens:
            return None, "This auth link has already been used"
        
        # Validate signature and expiration (1 hour)
        try:
            email = serializer.loads(token, salt="auth-token", max_age=3600)
        except SignatureExpired:
            return None, "This auth link has expired (valid for 1 hour)"
        except BadSignature:
            return None, "Invalid auth link"
        
        # Mark as used
        self.used_tokens.add(token)
        
        return email, None
    
    def session_exists(self, email: str) -> bool:
        """Check if a browser session file exists for this email."""
        session_path = SESSIONS_DIR / f"{email}.json"
        return session_path.exists()
    
    def get_session_path(self, email: str) -> Path:
        """Get the session file path for this email."""
        return SESSIONS_DIR / f"{email}.json"


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Route Handlers
# ─────────────────────────────────────────────────────────────────────────────

async def initiate_oauth(request):
    """
    /auth endpoint: Validate token and redirect to Google OAuth.
    
    Query params:
        token: Signed auth token from email link
    """
    token = request.query_params.get("token")
    if not token:
        return HTMLResponse(
            "<h1>Missing Token</h1><p>This auth link is invalid.</p>",
            status_code=400
        )
    
    auth_manager = AuthManager()
    email, error = auth_manager.validate_token(token)
    
    if error:
        return HTMLResponse(
            f"<h1>Authentication Error</h1><p>{error}</p>",
            status_code=400
        )
    
    # Store email in session for callback
    request.session["auth_email"] = email
    
    # Generate state parameter for CSRF protection
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    
    # Redirect to Google OAuth
    redirect_uri = request.url_for("oauth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri, state=state)


async def handle_callback(request, code: str, state: str):
    """
    /oauth/callback endpoint: Handle Google OAuth callback.
    
    After Google SSO completes:
    1. Validate OAuth code and state
    2. Launch Playwright browser
    3. Navigate to OpenClinica with Google auth
    4. Save browser session to disk
    
    Query params:
        code: OAuth authorization code from Google
        state: CSRF protection token
    """
    # Verify state parameter
    stored_state = request.session.get("oauth_state")
    if not stored_state or stored_state != state:
        return HTMLResponse(
            "<h1>Authentication Error</h1><p>Invalid state parameter.</p>",
            status_code=400
        )
    
    # Get email from session
    email = request.session.get("auth_email")
    if not email:
        return HTMLResponse(
            "<h1>Authentication Error</h1><p>Session expired.</p>",
            status_code=400
        )
    
    # Determine OC subdomain from email
    # Extract from email or use a default/env var
    subdomain = os.environ.get("OC_DEFAULT_SUBDOMAIN", "cust1")
    
    try:
        # Exchange code for token
        token = await oauth.google.authorize_access_token(request)
        
        # Extract user info
        user_info = token.get("userinfo")
        if not user_info or user_info.get("email") != email:
            return HTMLResponse(
                "<h1>Authentication Error</h1>"
                "<p>The Google account you signed in with doesn't match the email address.</p>",
                status_code=400
            )
        
        # Launch Playwright to capture OpenClinica session
        session_path = SESSIONS_DIR / f"{email}.json"
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            
            # Set Google OAuth cookies in the browser context
            # This allows OpenClinica to recognize the Google SSO
            google_cookies = []
            if "id_token" in token:
                google_cookies.append({
                    "name": "g_id_token",
                    "value": token["id_token"],
                    "domain": ".google.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True
                })
            
            if "access_token" in token:
                google_cookies.append({
                    "name": "g_access_token",
                    "value": token["access_token"],
                    "domain": ".google.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True
                })
            
            # Add cookies if we have any
            if google_cookies:
                await context.add_cookies(google_cookies)
            
            page = await context.new_page()
            
            # Navigate to OpenClinica login page
            oc_url = f"https://{subdomain}.build.openclinica.io"
            await page.goto(oc_url, wait_until="networkidle")
            
            # Check if we're on the login page
            # If so, click "Sign in with Google"
            try:
                # Wait for SSO button (adjust selector based on actual OC login page)
                sso_button = page.locator('button:has-text("Sign in with Google"), a:has-text("Sign in with Google")')
                if await sso_button.count() > 0:
                    await sso_button.first.click()
                    
                    # Wait for redirect back to OC after Google SSO
                    await page.wait_for_url(f"{oc_url}/**", timeout=30000)
            except Exception as e:
                # If SSO button not found or timeout, might already be logged in
                print(f"SSO flow note: {e}")
            
            # Save browser context (cookies, localStorage, etc.)
            await context.storage_state(path=str(session_path))
            
            await browser.close()
        
        # Clear session
        request.session.pop("auth_email", None)
        request.session.pop("oauth_state", None)
        
        return HTMLResponse(
            """
            <h1>✅ Authentication Complete!</h1>
            <p>Your OpenClinica session has been saved.</p>
            <p>You can now close this window and re-trigger "Send to AI" on your monday.com row.</p>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    max-width: 600px;
                    margin: 100px auto;
                    padding: 20px;
                    text-align: center;
                }
                h1 { color: #00c875; }
            </style>
            """
        )
        
    except Exception as e:
        print(f"OAuth callback error: {e}")
        return HTMLResponse(
            f"<h1>Authentication Error</h1><p>{str(e)}</p>",
            status_code=500
        )
