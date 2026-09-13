#!/usr/bin/env python3
"""
oc_session_bootstrap.py — Capture a per-user OpenClinica SSO session
locally so the Railway pipeline can reuse it for unattended form uploads.

What it does:
 1. Opens a VISIBLE Chromium window (headless=False — the whole point).
 2. Navigates to https://{subdomain}.{host}/#/ocstafflogin
 3. Waits up to 5 minutes for you to complete Google SSO interactively.
 4. Confirms you landed on the OC designer via
    FormPublisher.AUTH_SUCCESS_SELECTOR (same heuristic the runtime uses).
 5. Saves the browser's storage_state JSON to ./sessions/{email}.json
    (override with --out).
 6. Prints next steps for copying the file to Railway's /data volume.

When to run:
 - First time a new user needs OC form-upload access.
 - Whenever the saved session has expired (Railway pipeline logs:
   "Saved SSO session for <email> appears expired — bootstrap a new one").

Usage:
    python scripts/oc_session_bootstrap.py <email> <subdomain> [options]

Examples:
    python scripts/oc_session_bootstrap.py dswanker@mac.com cust1
    python scripts/oc_session_bootstrap.py user@co.io acme \\
        --out /Volumes/railway-data/browser_sessions/user@co.io.json

    # Push directly to Railway instead of manual file transfer — grab
    # the one-time token from the /auth?token=... link on the paused
    # Monday item (works whether or not the pipeline is currently
    # paused-for-auth; token is single-use but this doesn't consume it
    # via the /auth page, only via the upload itself):
    python scripts/oc_session_bootstrap.py dswanker@openclinica.com bioIVT \\
        --push --token eyJlbWFpbCI6...

Requires: playwright + chromium (already in the project's deps; run
`playwright install chromium` if it's not on this machine yet).

Notes:
 - The AUTH_SUCCESS_SELECTOR heuristic is a guess (see oc_form_publisher.py).
   If Google SSO completes but the script reports "did not detect auth",
   inspect the visible browser — if you ARE on OC, the selector is wrong
   and needs updating in oc_form_publisher.py.
 - --push bypasses the OC Session Capture Chrome extension entirely.
   That extension only captures localStorage from openclinica.io tabs
   that happen to be OPEN in Chrome at the moment you click "Capture" —
   if the tab holding jhi-idtoken isn't open right then, it's silently
   dropped with no error. This script's Playwright context captures
   every origin actually visited during THIS login, so it doesn't have
   that failure mode.
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Add repo root to sys.path so we can import oc_form_publisher (this
# script lives in scripts/ which isn't on the default path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oc_form_publisher import FormPublisher  # noqa: E402

RAILWAY_BASE = "https://oc-ai-pipeline-production.up.railway.app"


async def push_to_railway(token: str, storage_state: dict) -> None:
    import httpx
    resp = httpx.post(
        f"{RAILWAY_BASE}/api/session/upload",
        json={"token": token, "storage_state": storage_state},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"❌ Railway rejected the upload: {resp.status_code} {resp.text}",
              file=sys.stderr)
        sys.exit(3)
    result = resp.json()
    if not result.get("ok"):
        print(f"❌ Railway reported failure: {result.get('error')}",
              file=sys.stderr)
        sys.exit(3)
    n_origins = len(storage_state.get("origins", []))
    n_ls = sum(len(o.get("localStorage", [])) for o in storage_state.get("origins", []))
    print(f"✅ Pushed to Railway for {result.get('email', '?')}: "
          f"{len(storage_state.get('cookies', []))} cookies, "
          f"{n_origins} origin(s), {n_ls} localStorage key(s).")
    print("   Return to Monday and re-trigger 'Send to AI'.")


async def bootstrap(email: str, subdomain: str, host: str, out_path: Path,
                     push: bool, token: str) -> None:
    sso_url    = f"https://{subdomain}.{host}/#/ocstafflogin"
    timeout_ms = FormPublisher.MANUAL_LOGIN_TIMEOUT_MS
    timeout_s  = timeout_ms // 1000

    print(f"Opening browser → {sso_url}")
    print(f"You have {timeout_s}s to complete Google SSO. Window will "
          f"close automatically when auth is detected.")

    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        print(f"❌ playwright not installed: {e}", file=sys.stderr)
        print(f"   Run: pip install playwright && playwright install chromium",
              file=sys.stderr)
        sys.exit(2)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            context = await browser.new_context()
            page    = await context.new_page()
            await page.goto(sso_url, wait_until="networkidle")
            try:
                await page.wait_for_selector(
                    FormPublisher.AUTH_SUCCESS_SELECTOR,
                    timeout=timeout_ms,
                )
            except Exception as e:
                print(f"❌ Did not detect successful auth after {timeout_s}s.",
                      file=sys.stderr)
                print(f"   Selector tried: {FormPublisher.AUTH_SUCCESS_SELECTOR}",
                      file=sys.stderr)
                print(f"   Underlying: {type(e).__name__}: {e}",
                      file=sys.stderr)
                print(f"   If the browser DID reach the OC designer, the "
                      f"selector heuristic is wrong — inspect the DOM and "
                      f"update FormPublisher.AUTH_SUCCESS_SELECTOR in "
                      f"oc_form_publisher.py.", file=sys.stderr)
                sys.exit(1)
            await context.storage_state(path=str(out_path))
        finally:
            await browser.close()

    print(f"✅ Saved session to {out_path}")

    if push:
        import json
        with open(out_path) as f:
            storage_state = json.load(f)
        await push_to_railway(token, storage_state)
        return

    print()
    print("Next steps — upload to the Railway volume:")
    print(f"   1. In Railway dashboard, ensure /data is a Volume mounted "
          f"on the form-upload service.")
    print(f"   2. Copy this file into the volume at "
          f"/data/browser_sessions/{email}.json")
    print(f"      (via `railway ssh` + `cat >`, scp, S3 sync, or whatever "
          f"transfer mechanism you use.)")
    print()
    print("   Or just rerun with --push --token <one-time-code> to skip "
          "all of this and push directly.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture per-user OC SSO session for unattended form uploads")
    parser.add_argument("email",
                        help="User email — also used as the saved-session filename")
    parser.add_argument("subdomain",
                        help="OC subdomain (e.g. 'cust1' for cust1.design.openclinica.io)")
    parser.add_argument("--out", default=None,
                        help="Output path for the session JSON "
                             "(default: ./sessions/{email}.json)")
    parser.add_argument("--host", default="design.openclinica.io",
                        help="OC host suffix (default: design.openclinica.io)")
    parser.add_argument("--push", action="store_true",
                        help="Push the captured session directly to Railway's "
                             "/api/session/upload instead of saving locally "
                             "for manual transfer. Requires --token.")
    parser.add_argument("--token", default="",
                        help="One-time auth token — the value of the "
                             "token= query param from the /auth?token=... "
                             "link posted on the paused Monday item. "
                             "Required when using --push.")
    args = parser.parse_args()

    if args.push and not args.token:
        print("❌ --push requires --token <one-time-code> (copy it from "
              "the /auth?token=... link on the Monday item).",
              file=sys.stderr)
        sys.exit(2)

    out_path = Path(args.out) if args.out else Path("sessions") / f"{args.email}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        print(f"⚠️  Overwriting existing session at {out_path}")

    asyncio.run(bootstrap(args.email, args.subdomain, args.host, out_path,
                          args.push, args.token))


if __name__ == "__main__":
    main()
