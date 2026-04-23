#!/usr/bin/env python3
"""
test_oc_board_import.py — Standalone test for OpenClinica board import.

ZERO Anthropic API cost. Uses existing fixtures/study_spec.json.

What this tests:
  1. Authenticate DIRECTLY against Keycloak (client_id=designer, password grant)
     — this is different from the user-service token flow which was getting 401.
  2. Get the study's board ID from the design service.
  3. POST the board.json to /api/importStudy/{boardId}.

Usage:
  # First time: install env vars
  export OC_API_USERNAME="dswanker@openclinica.com"
  export OC_API_PASSWORD="your-password"
  export OC_SUBDOMAIN="cust1"         # default: cust1
  export OC_IS_PRODUCTION=1           # 1 = use .io, 0 = use -dev.io (default 1)

  # Then run:
  python3 test_oc_board_import.py

Options:
  --auth-only       Just get the token — don't attempt import.
  --skip-import     Get token + fetch boardId — don't POST import.
  --verbose         Print full Keycloak response + token claims.
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)


# ── Configuration from environment ────────────────────────────────────────────

REPO_ROOT     = Path(__file__).resolve().parent
FIXTURES_DIR  = REPO_ROOT / "fixtures"
SPEC_JSON     = FIXTURES_DIR / "study_spec.json"

SUBDOMAIN     = os.environ.get("OC_SUBDOMAIN", "cust1").strip()
USERNAME      = os.environ.get("OC_API_USERNAME", "").strip()
PASSWORD      = os.environ.get("OC_API_PASSWORD", "").strip()
IS_PRODUCTION = os.environ.get("OC_IS_PRODUCTION", "1").strip() == "1"

# Based on the HAR, the Keycloak realm is named with a "-eu" suffix
# regardless of the customer subdomain. This may vary per customer.
KEYCLOAK_REALM = os.environ.get("OC_KEYCLOAK_REALM", f"{SUBDOMAIN}-eu").strip()
KEYCLOAK_HOST  = os.environ.get("OC_KEYCLOAK_HOST",
                                 "https://auth.openclinica.io").strip()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _env_suffix():
    """Return '' for production, '-dev' for test/dev."""
    return "" if IS_PRODUCTION else "-dev"


def _designer_base_url():
    return f"https://{SUBDOMAIN}.design.openclinica{_env_suffix()}.io"


def _study_service_base_url():
    return f"https://{SUBDOMAIN}.build.openclinica{_env_suffix()}.io"


def _decode_jwt_claims(token):
    """Decode JWT claims (unverified) for inspection."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception as e:
        return {"_decode_error": str(e)}


def _summarize_token(token, verbose=False):
    claims = _decode_jwt_claims(token) or {}
    aud    = claims.get("aud")
    azp    = claims.get("azp")
    scope  = claims.get("scope")
    email  = claims.get("preferred_username") or claims.get("email")
    print(f"  token for: {email}")
    print(f"  azp:       {azp}")
    print(f"  aud:       {aud}")
    print(f"  scope:     {scope}")
    if verbose:
        print(f"  all claims:")
        for k, v in claims.items():
            vs = json.dumps(v) if not isinstance(v, str) else v
            if len(vs) > 120:
                vs = vs[:120] + "..."
            print(f"    {k}: {vs}")


# ── Step 1a: Get user-service token (what engineering says should work) ──────

def get_user_service_token(verbose=False):
    """
    Call the user-service OAuth endpoint with username/password. This is the
    same token flow used by the /study-service/api/studies endpoints. Your
    engineering team says this token should also work against the design
    service /api/importStudy endpoint.

    Returns: access_token string on success, raises on failure.
    """
    if not USERNAME or not PASSWORD:
        raise RuntimeError("OC_API_USERNAME and OC_API_PASSWORD must be set.")

    url = f"{_study_service_base_url()}/user-service/api/oauth/token"
    print(f"\n─── Step 1 — user-service password grant ──────────────────────────")
    print(f"POST {url}")
    print(f"  username: {USERNAME}")

    with httpx.Client(timeout=30) as c:
        r = c.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"username": USERNAME, "password": PASSWORD},
        )

    print(f"→ HTTP {r.status_code}")

    if r.status_code != 200:
        body = r.text
        print(f"ERROR body (first 500 chars):\n  {body[:500]}")
        raise RuntimeError(f"user-service returned {r.status_code}")

    # Response is the raw token string, not JSON wrapped
    token = r.text.strip()
    # Strip surrounding quotes if present
    if token.startswith('"') and token.endswith('"'):
        token = token[1:-1]

    print(f"✓ Got access_token — {len(token)} chars")
    _summarize_token(token, verbose=verbose)
    return token


# ── Step 1b: Get a Keycloak-issued "designer" token ───────────────────────────

def get_designer_token(verbose=False):
    """
    Call Keycloak directly with Resource Owner Password Credentials (ROPC) grant
    against client_id='designer'. This is what the HAR showed the SPA getting
    via implicit flow — we're attempting the headless equivalent.

    NOTE: We confirmed earlier this returns "unauthorized_client — Client not
    allowed for direct access grants" because the designer Keycloak client
    is configured as a public SPA client. Left here for comparison testing.

    Returns: access_token string on success, raises on failure.
    """
    if not USERNAME or not PASSWORD:
        raise RuntimeError("OC_API_USERNAME and OC_API_PASSWORD must be set.")

    url = f"{KEYCLOAK_HOST}/auth/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    print(f"\n─── Step 1 — Keycloak password grant ──────────────────────────────")
    print(f"POST {url}")
    print(f"  client_id: designer")
    print(f"  username:  {USERNAME}")
    print(f"  scope:     openid profile")

    data = {
        "grant_type": "password",
        "client_id":  "designer",
        "username":   USERNAME,
        "password":   PASSWORD,
        "scope":      "openid profile",
        # The audience hint matches what the SPA sends — may or may not be used
        # by Keycloak depending on version/config.
        "audience":   "https://www.openclinica.com",
    }
    with httpx.Client(timeout=30) as c:
        r = c.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})

    print(f"→ HTTP {r.status_code}")

    if r.status_code != 200:
        # Keycloak returns helpful error details in JSON when things fail
        body = r.text
        print(f"ERROR body (first 500 chars):\n  {body[:500]}")
        hint = ""
        if "invalid_client" in body.lower():
            hint = ("\nLikely cause: client 'designer' does NOT have 'Direct Access "
                    "Grants' enabled in Keycloak. This is common for SPA clients "
                    "for security reasons. Move to Option B (service account) or "
                    "Option D (Playwright browser flow).")
        elif "invalid_grant" in body.lower():
            hint = ("\nLikely cause: username/password rejected. Check credentials, "
                    "or the user may be disabled for this realm.")
        elif "unauthorized_client" in body.lower():
            hint = ("\nLikely cause: the 'designer' client is configured to reject "
                    "password grants. Same mitigation as invalid_client.")
        raise RuntimeError(f"Keycloak returned {r.status_code}{hint}")

    tok = r.json()
    access_token = tok.get("access_token")
    if not access_token:
        raise RuntimeError(f"No access_token in response: {tok}")

    print(f"✓ Got access_token — expires_in: {tok.get('expires_in')}s")
    print(f"  Token type: {tok.get('token_type')}")
    _summarize_token(access_token, verbose=verbose)
    return access_token


# ── Step 2: Find the board ID for the target study ────────────────────────────

def get_board_id(token, study_uuid):
    """
    The board ID lives inside the study-service's study detail response as
    the `currentBoardUrl` field (e.g. https://cust1.design.openclinica.io/b/XXXXX/...).
    This is how pipeline.py successfully resolves the board ID.
    """
    url = f"{_study_service_base_url()}/study-service/api/studies/{study_uuid}"
    print(f"\n─── Step 2 — Resolve board ID for study ────────────────────────────")
    print(f"Study UUID: {study_uuid}")
    print(f"GET {url}")

    with httpx.Client(timeout=30) as c:
        r = c.get(url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        })
    print(f"  → HTTP {r.status_code}")

    if r.status_code != 200:
        raise RuntimeError(f"Could not fetch study details: {r.status_code} "
                           f"{r.text[:300]}")

    data      = r.json()
    board_url = data.get("currentBoardUrl", "")
    print(f"  currentBoardUrl: {board_url}")

    if "/b/" in board_url:
        parts    = board_url.split("/b/")
        board_id = parts[1].split("/")[0]
        print(f"✓ Extracted board ID: {board_id}")
        return board_id

    raise RuntimeError(f"Could not extract board ID from currentBoardUrl "
                       f"({board_url!r}). Full study response: "
                       f"{json.dumps(data)[:500]}")


# ── Step 3: Import the board.json ─────────────────────────────────────────────

def import_board(token, board_id, board_json, dump_path=None):
    """POST board.json to /api/importStudy/{boardId}."""
    designer = _designer_base_url()
    url = f"{designer}/api/importStudy/{board_id}"
    print(f"\n─── Step 3 — Import board.json ─────────────────────────────────────")
    print(f"POST {url}")
    print(f"  board size: {len(json.dumps(board_json))} chars")
    print(f"  labels:     {len(board_json.get('labels', []))}")
    print(f"  lists:      {len(board_json.get('lists', []))}")
    print(f"  cards:      {len(board_json.get('cards', []))}")

    if dump_path:
        with open(dump_path, "w") as f:
            json.dump(board_json, f, indent=2)
        print(f"  (dumped to {dump_path} for inspection)")

    with httpx.Client(timeout=60) as c:
        r = c.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            json=board_json,
        )
    print(f"→ HTTP {r.status_code}")
    print(f"  response headers: {dict(r.headers)}")
    print(f"  response body:    {r.text[:2000]}")

    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"Board import failed with {r.status_code}")
    print(f"\n✓ SUCCESS — board imported.")


# ── Board JSON builder ────────────────────────────────────────────────────────
# Imports pipeline.py's _build_board_json so we always test the same code
# path that runs in production. This avoids drift between the two versions.

def build_board_json(struct_json):
    """Build a board.json payload from Study Spec JSON — delegates to pipeline.py."""
    from pipeline import _build_board_json
    return _build_board_json(struct_json)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-mode",
                    choices=["user-service", "keycloak-designer"],
                    default="user-service",
                    help="Which OAuth flow to try. 'user-service' = what "
                         "engineering says should work. 'keycloak-designer' = "
                         "what the browser actually uses (will fail — client "
                         "not allowed for direct access grants).")
    ap.add_argument("--auth-only",   action="store_true",
                    help="Stop after getting token.")
    ap.add_argument("--skip-import", action="store_true",
                    help="Get token + board ID, but don't POST import.")
    ap.add_argument("--verbose",     action="store_true",
                    help="Dump full token claims.")
    ap.add_argument("--study-uuid",  default=None,
                    help="Override study UUID (defaults to env OC_STUDY_UUID).")
    ap.add_argument("--board-id",    default=None,
                    help="Skip board ID lookup — pass it directly (env OC_BOARD_ID).")
    args = ap.parse_args()

    print(f"Subdomain:    {SUBDOMAIN}")
    print(f"Environment:  {'PRODUCTION (.io)' if IS_PRODUCTION else 'TEST (-dev.io)'}")
    print(f"Designer URL: {_designer_base_url()}")
    print(f"Auth mode:    {args.auth_mode}")
    if args.auth_mode == "keycloak-designer":
        print(f"Keycloak:     {KEYCLOAK_HOST}/auth/realms/{KEYCLOAK_REALM}")

    # Step 1: Get token via chosen flow
    try:
        if args.auth_mode == "user-service":
            token = get_user_service_token(verbose=args.verbose)
        else:
            token = get_designer_token(verbose=args.verbose)
    except Exception as e:
        print(f"\n✗ FAILED at Step 1 (token): {e}")
        sys.exit(1)

    if args.auth_only:
        print(f"\n✓ --auth-only — done.")
        return

    # Resolve board ID + study UUID
    study_uuid = args.study_uuid or os.environ.get("OC_STUDY_UUID", "").strip()
    board_id   = args.board_id   or os.environ.get("OC_BOARD_ID",   "").strip()

    # Step 2: Look up board ID if not provided
    if not board_id:
        if not study_uuid:
            print("\n✗ Provide --board-id (or OC_BOARD_ID env) OR")
            print("  provide --study-uuid (or OC_STUDY_UUID env) so we can look it up.")
            sys.exit(1)
        try:
            board_id = get_board_id(token, study_uuid)
        except Exception as e:
            print(f"\n✗ FAILED at Step 2 (board ID lookup): {e}")
            sys.exit(1)

    if args.skip_import:
        print(f"\n✓ --skip-import — got board_id={board_id}")
        return

    # Step 3: Build board.json from fixture + import
    if not SPEC_JSON.exists():
        print(f"\n✗ {SPEC_JSON} not found. Run test_json_extraction.py --spec first.")
        sys.exit(1)
    with SPEC_JSON.open() as f:
        struct_json = json.load(f)
    board_json = build_board_json(struct_json)

    try:
        import_board(token, board_id, board_json,
                     dump_path=str(REPO_ROOT / "fixtures" / "_debug_board.json"))
    except Exception as e:
        print(f"\n✗ FAILED at Step 3 (import): {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
