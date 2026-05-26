#!/usr/bin/env python3
"""
test_publisher.py — focused harness for iterating on the FAST(JS) path of
the OC form publisher.

What this does
──────────────
1. Authenticates against OC's user-service (same flow as pipeline.py /
   probe_board_api.py — OC_API_USERNAME / OC_API_PASSWORD env vars).
2. GETs /api/boards/DMjtshj8C8sC8yLgc and filters the cards array to
   the OIDs we still need to debug (LWD/SLEEP/SF12/AE/AESAE/CM/DV/DS).
3. Launches Chromium with the saved storage_state, navigates to the
   board page, then for every targeted card runs the SAME JS injection
   that oc_form_publisher.py uses on its FAST path (Cards.update with
   the raw integer version id).
4. On FAST(JS) failure, mirrors the publisher's fallback: page.goto the
   card's href, wait for the radio, click it. Records outcome.
5. Restores the board page between cards so the next FAST(JS) attempt
   sees an intact minimongo.
6. Prints a per-OID summary: fast_js / fallback_ok / failed_entirely.

What it deliberately doesn't do
───────────────────────────────
- No Monday API. No FormPublisher.publish_all_forms. No EDC zip download.
- No file uploads. We're testing the set-default-version primitive only,
  against forms that already have versions on the existing CRS-135 board.
- No warmup wait — these aren't just-uploaded forms in this run.

Run
───
    First time on a new machine — capture the SSO session locally:
        railway run python3 test_publisher.py --capture-session
        ↳ opens Chromium, navigates to the board, waits for you to
          complete Google SSO in the visible window, then saves
          storage_state to ~/oc-ai-pipeline/data/browser_sessions/.

    Subsequent runs — no Railway needed once you have OC creds in env:
        python3 test_publisher.py
        ↳ reads session JSON from ~/oc-ai-pipeline/data/browser_sessions/
          (falls back to /data/browser_sessions/ for the Railway volume).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import httpx
from playwright.async_api import async_playwright


# ── Config ───────────────────────────────────────────────────────────────────
AUTH_HOST   = "https://cust1.build.openclinica.io"
DESIGN_HOST = "https://cust1.design.openclinica.io"
BOARD_ID    = "DMjtshj8C8sC8yLgc"
BOARD_URL   = f"{DESIGN_HOST}/b/{BOARD_ID}/crs-135"

TARGET_OIDS = {"LWD", "SLEEP", "SF12", "AE", "AESAE", "CM", "DV", "DS"}

SESSION_CANDIDATES = [
    os.path.expanduser(
        "~/oc-ai-pipeline/data/browser_sessions/dswanker@openclinica.com.json"
    ),
    "/data/browser_sessions/dswanker@openclinica.com.json",
]


# ── Same JS injection oc_form_publisher.py uses on FAST PATH ────────────────
#
# Kept verbatim here for fast iteration — if the publisher's JS changes,
# paste the new body in. Returns the same diagnostic bag the publisher
# expects (versionId / versionIdRaw / versionObj / cardFields).
FAST_JS = """
async (cardId) => {
    if (typeof Cards === 'undefined' || typeof Meteor === 'undefined') {
        return { ok: false, reason: 'Cards/Meteor not in window scope' };
    }
    const card = Cards.findOne(cardId);
    if (!card) return { ok: false, reason: 'card not in minimongo' };
    if (!Array.isArray(card.versions) || card.versions.length === 0) {
        return { ok: false, reason: 'card has no versions',
                 keys: Object.keys(card) };
    }
    const versionIdRaw = card.versions[0].id || card.versions[0]._id;
    if (!versionIdRaw) {
        return { ok: false, reason: 'no version id found',
                 versionKeys: Object.keys(card.versions[0]),
                 versionObj: JSON.stringify(card.versions[0]) };
    }
    const versionId = String(versionIdRaw);
    const versionObj = card.versions[0];
    const cardFields = {
        currentVersion: card.currentVersion,
        defaultVersion: card.defaultVersion,
        _version: card._version,
    };
    try {
        Cards.update(cardId, { $set: { _version: versionIdRaw } });
        return { ok: true, versionId, versionIdRaw, versionObj, cardFields };
    } catch (e) {
        return { ok: false,
                 reason: 'Cards.update threw: ' + String(e.message || e),
                 versionId, versionIdRaw, versionObj, cardFields };
    }
}
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _find_session() -> str:
    for p in SESSION_CANDIDATES:
        if os.path.exists(p):
            return p
    print(
        f"ERROR: no session JSON found at any of:\n  "
        + "\n  ".join(SESSION_CANDIDATES),
        file=sys.stderr,
    )
    sys.exit(1)


async def _get_oc_token() -> str:
    user = (os.environ.get("OC_API_USERNAME") or "").strip()
    pw   = (os.environ.get("OC_API_PASSWORD") or "").strip()
    if not user or not pw:
        print("ERROR: OC_API_USERNAME / OC_API_PASSWORD not set "
              "(use `railway run` or export them manually).",
              file=sys.stderr)
        sys.exit(2)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{AUTH_HOST}/user-service/api/oauth/token",
            headers={"Content-Type": "application/json"},
            json={"username": user, "password": pw},
        )
    if r.status_code != 200:
        print(f"AUTH FAIL {r.status_code}: {r.text[:300]}", file=sys.stderr)
        sys.exit(3)
    return r.text.strip()


async def _fetch_target_cards(token: str) -> list[dict]:
    """GET the board and pull out (card_id, oid, title, versions, href-hint)
    for every non-archived card whose formOcoid is in TARGET_OIDS.

    The REST snapshot doesn't carry per-card hrefs, so we reconstruct an
    href in the publisher's expected shape (/b/{board}/{slug}/c/{card_id})
    after we navigate the browser to the board.
    """
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{DESIGN_HOST}/api/boards/{BOARD_ID}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
        )
    if r.status_code != 200:
        print(f"BOARD FETCH FAIL {r.status_code}: {r.text[:300]}",
              file=sys.stderr)
        sys.exit(4)
    data = r.json()
    out = []
    for c in (data.get("cards") or []):
        if c.get("archived"):
            continue
        oid = (c.get("formOcoid") or "").upper()
        if oid not in TARGET_OIDS:
            continue
        out.append({
            "card_id":  c.get("_id"),
            "oid":      oid,
            "title":    c.get("title", ""),
            "versions": c.get("versions") or [],
        })
    return out


async def _try_fallback(page, card_id: str) -> tuple[bool, str]:
    """Mirror oc_form_publisher.py's URL-nav fallback for one card.

    Navigates to the card detail page, waits for the radio, clicks the
    first radio. Returns (ok, detail) where detail is a short tag of
    what happened — kept terse so the summary stays readable.
    """
    # The board appends the card_id directly to its own URL — no /c/
    # prefix. Earlier guess of /c/{card_id} returned "Page not found".
    # Correct shape (from publisher logs):
    #   https://cust1.design.openclinica.io/b/{board}/{slug}/{card_id}
    abs_url = f"{BOARD_URL}/{card_id}"
    try:
        await page.goto(abs_url, wait_until="domcontentloaded")
    except Exception as e:
        return (False, f"goto failed: {e}")
    try:
        await page.wait_for_selector("input[type=radio]", timeout=15_000)
    except Exception as e:
        return (False, f"radio not seen: {e}")
    try:
        await page.locator("input[type=radio]").first.click(timeout=5_000)
    except Exception as e:
        return (False, f"radio click failed: {e}")
    return (True, "radio clicked")


# ── Session capture (--capture-session) ──────────────────────────────────────

async def capture_session() -> int:
    """Interactive SSO bootstrap. Opens Chromium with no storage_state,
    navigates to the OC designer board (which redirects through Google
    SSO), waits for the user to complete login, then saves the resulting
    storage_state to the first path in SESSION_CANDIDATES (the local
    home-relative path).

    The user presses Enter on the terminal to confirm login is done —
    we don't watch for any specific selector because OC SSO redirects
    can vary per tenant, and a human confirming "I'm in" is the most
    robust signal.
    """
    save_path = SESSION_CANDIDATES[0]
    save_dir  = os.path.dirname(save_path)
    os.makedirs(save_dir, exist_ok=True)

    print(f"[capture] target path: {save_path}")
    if os.path.exists(save_path):
        size = os.path.getsize(save_path)
        print(f"[capture] note: an existing session ({size} bytes) "
              f"will be overwritten on success.")
    print(f"[capture] launching Chromium (visible) ...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            print(f"[capture] navigating to {BOARD_URL}")
            await page.goto(BOARD_URL, wait_until="domcontentloaded")

            print()
            print("─" * 72)
            print(" Please complete the SSO login in the browser window.")
            print(" When you can see the CRS-135 designer board (cards "
                  "rendered),")
            print(" return here and press Enter to save the session.")
            print("─" * 72)
            # input() is blocking — run it off the asyncio loop so
            # Playwright keeps servicing the visible browser tab.
            await asyncio.to_thread(input)

            await context.storage_state(path=save_path)
            try:
                size = os.path.getsize(save_path)
            except OSError:
                size = -1
            print(f"[capture] saved session to {save_path} ({size} bytes)")
        finally:
            await browser.close()

    return 0


# ── Main ─────────────────────────────────────────────────────────────────────

async def run() -> int:
    session_path = _find_session()
    print(f"[test] session: {session_path}")

    token = await _get_oc_token()
    print(f"[test] OC token: {len(token)} chars")

    targets = await _fetch_target_cards(token)
    print(f"[test] {len(targets)} cards across "
          f"{len(set(t['oid'] for t in targets))} of "
          f"{len(TARGET_OIDS)} target OIDs:")
    found_oids = {t["oid"] for t in targets}
    for oid in sorted(TARGET_OIDS):
        marker = "✓" if oid in found_oids else "✗"
        print(f"   {marker} {oid}")
    for t in targets:
        v = "yes" if t["versions"] else "NO_VERSIONS"
        print(f"     OID={t['oid']:<7} _id={t['card_id']:<20}  "
              f"versions={v}  title={t['title']!r}")

    if not targets:
        print("[test] no targets found — nothing to do")
        return 0

    # Per-card outcomes: list of (oid, card_id, category, detail)
    # categories: fast_js | fallback_ok | failed_entirely
    results: list[tuple[str, str, str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            context = await browser.new_context(storage_state=session_path)
            page = await context.new_page()

            print(f"[test] navigating to {BOARD_URL}")
            await page.goto(BOARD_URL, wait_until="domcontentloaded")
            await page.wait_for_selector(".js-minicard", timeout=60_000)
            await page.wait_for_timeout(1500)

            board_url_after = page.url
            print(f"[test] board ready (page.url={board_url_after})")

            for t in targets:
                oid = t["oid"]
                cid = t["card_id"]
                print(f"\n[test] ── {oid} card_id={cid} ──")
                js_result = await page.evaluate(FAST_JS, cid)

                ok = isinstance(js_result, dict) and js_result.get("ok")
                if ok:
                    vid = js_result.get("versionId")
                    print(f"       FAST(JS) OK versionId={vid!r}")
                    results.append((oid, cid, "fast_js", vid))
                    continue

                # FAST(JS) failed — print full diagnostic bag and try fallback.
                reason = (js_result.get("reason")
                          if isinstance(js_result, dict) else str(js_result))
                print(f"       FAST(JS) FAIL reason={reason!r}")
                if isinstance(js_result, dict):
                    for k in ("versionId", "versionIdRaw", "versionObj",
                              "cardFields", "versionKeys", "keys"):
                        if k in js_result:
                            print(f"       {k}={js_result[k]!r}")

                fb_ok, fb_detail = await _try_fallback(page, cid)
                if fb_ok:
                    print(f"       FALLBACK OK ({fb_detail})")
                    results.append((oid, cid, "fallback_ok",
                                    f"{reason} / {fb_detail}"))
                else:
                    print(f"       FALLBACK FAIL ({fb_detail})")
                    results.append((oid, cid, "failed_entirely",
                                    f"{reason} / {fb_detail}"))

                # Restore board context so the next card's FAST(JS) sees
                # the same minimongo state the publisher would.
                try:
                    await page.goto(board_url_after,
                                    wait_until="domcontentloaded")
                    if "Cards/Meteor not in window scope" in str(reason):
                        await page.wait_for_timeout(2000)
                except Exception as e:
                    print(f"       BOARD RESTORE FAIL: {e}")

        finally:
            await browser.close()

    # ── Summary ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)

    by_cat: dict[str, list[tuple[str, str, Any]]] = {
        "fast_js": [], "fallback_ok": [], "failed_entirely": [],
    }
    for oid, cid, cat, detail in results:
        by_cat.setdefault(cat, []).append((oid, cid, detail))

    for cat in ("fast_js", "fallback_ok", "failed_entirely"):
        items = by_cat.get(cat, [])
        label = {
            "fast_js":          "FAST(JS) succeeded",
            "fallback_ok":      "Fell back to URL-nav (FAST failed, fallback OK)",
            "failed_entirely":  "Failed entirely",
        }[cat]
        print(f"\n{label} ({len(items)}):")
        if not items:
            print("  (none)")
            continue
        for oid, cid, detail in items:
            print(f"  {oid:<8} {cid:<20}  {detail!r}")

    missing = sorted(TARGET_OIDS - {t["oid"] for t in targets})
    if missing:
        print(f"\nTarget OIDs with no card on the board (skipped): {missing}")

    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="FAST(JS) test harness for the OC form publisher.",
    )
    p.add_argument(
        "--capture-session",
        action="store_true",
        help="Open Chromium, navigate to the board, wait for interactive "
             "SSO login, then save storage_state to "
             "~/oc-ai-pipeline/data/browser_sessions/. No tests run in "
             "this mode.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.capture_session:
        sys.exit(asyncio.run(capture_session()))
    sys.exit(asyncio.run(run()))
