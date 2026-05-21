"""
auth_manager.py - Web-based OAuth authentication for OpenClinica form uploads
"""
import os
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from authlib.integrations.requests_client import OAuth2Session

# Environment variables
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL", "https://oc-ai-pipeline-production.up.railway.app")

# OAuth config
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = f"{BASE_URL}/oauth/callback"
SCOPE = ["openid", "email", "profile"]

# Session storage
SESSION_DIR = Path("/data/browser_sessions")

# Token serializer (for secure one-time links)
serializer = URLSafeTimedSerializer(AUTH_SECRET_KEY)


class AuthManager:
    """Manages authentication tokens and OAuth flow"""
    
    # In-memory token store (maps token -> {email, created_at})
    # In production, use Redis for multi-instance support
    _pending_tokens: Dict[str, dict] = {}
    
    @classmethod
    def generate_auth_link(cls, email: str) -> str:
        """Generate secure one-time auth link for user"""
        # Create signed token with email embedded
        token = serializer.dumps(email, salt="auth-token")
        
        # Store in memory for validation (expires in 1 hour)
        cls._pending_tokens[token] = {
            "email": email,
            "created_at": datetime.utcnow()
        }
        
        # Clean up expired tokens (older than 1 hour)
        cls._cleanup_expired_tokens()
        
        return f"{BASE_URL}/auth?token={token}"
    
    @classmethod
    def validate_token(cls, token: str) -> Optional[str]:
        """
        Validate token and return email if valid.
        Returns None if token is invalid/expired/already used.
        """
        try:
            # Verify signature and extract email (max age: 1 hour)
            email = serializer.loads(token, salt="auth-token", max_age=3600)
            
            # Check if token exists in pending store
            if token not in cls._pending_tokens:
                return None
            
            # Consume token (one-time use)
            token_data = cls._pending_tokens.pop(token)
            
            # Verify email matches
            if token_data["email"] != email:
                return None
            
            return email
            
        except (SignatureExpired, BadSignature):
            return None
    
    @classmethod
    def _cleanup_expired_tokens(cls):
        """Remove tokens older than 1 hour"""
        cutoff = datetime.utcnow() - timedelta(hours=1)
        expired = [
            token for token, data in cls._pending_tokens.items()
            if data["created_at"] < cutoff
        ]
        for token in expired:
            cls._pending_tokens.pop(token, None)
    
    @classmethod
    def initiate_oauth(cls, email: str) -> tuple[str, str]:
        """
        Start Google OAuth flow.
        Returns (authorization_url, state)
        """
        oauth = OAuth2Session(
            GOOGLE_CLIENT_ID,
            redirect_uri=REDIRECT_URI,
            scope=SCOPE
        )
        
        # Generate state parameter (CSRF protection)
        state = secrets.token_urlsafe(32)
        
        # Store email with state for callback validation
        cls._pending_tokens[f"state_{state}"] = {
            "email": email,
            "created_at": datetime.utcnow()
        }
        
        authorization_url, _ = oauth.create_authorization_url(GOOGLE_AUTH_URL)
        
        # Add state parameter
        authorization_url = f"{authorization_url}&state={state}"
        
        return authorization_url, state
    
    @classmethod
    def handle_callback(cls, code: str, state: str) -> Optional[str]:
        """
        Handle OAuth callback from Google.
        Returns email if successful, None otherwise.
        """
        # Validate state
        state_key = f"state_{state}"
        if state_key not in cls._pending_tokens:
            return None
        
        token_data = cls._pending_tokens.pop(state_key)
        email = token_data["email"]
        
        # Exchange code for tokens
        oauth = OAuth2Session(
            GOOGLE_CLIENT_ID,
            redirect_uri=REDIRECT_URI
        )
        
        try:
            token = oauth.fetch_token(
                GOOGLE_TOKEN_URL,
                code=code,
                client_secret=GOOGLE_CLIENT_SECRET
            )
            
            # We don't actually need the OAuth token - just confirming auth worked
            # The real session comes from Playwright capturing browser cookies
            
            return email
            
        except Exception as e:
            print(f"OAuth token exchange failed: {e}")
            return None
    
    @classmethod
    def session_exists(cls, email: str) -> bool:
        """Check if user has a saved session file"""
        session_path = SESSION_DIR / f"{email}.json"
        return session_path.exists()
    
    @classmethod
    def save_placeholder_session(cls, email: str) -> None:
        """
        Save a placeholder session file.
        The real session will be captured by Playwright during first form upload.
        This just marks the user as 'authenticated via OAuth'.
        """
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        session_path = SESSION_DIR / f"{email}.json"
        
        placeholder = {
            "_oauth_authenticated": True,
            "_authenticated_at": datetime.utcnow().isoformat(),
            "cookies": [],  # Empty - will be populated by Playwright
            "origins": []
        }
        
        with open(session_path, "w") as f:
            json.dump(placeholder, f, indent=2)
