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

    Default mode — FAST(JS) test against the hardcoded TARGET_OIDS:
        python3 test_publisher.py
        ↳ reads session JSON from ~/oc-ai-pipeline/data/browser_sessions/
          (falls back to /data/browser_sessions/ for the Railway volume).

    Upload mode — minicard-scroll-click → set_input_files → wait per OID:
        python3 test_publisher.py --upload \
            --oids AE,AESAE,CM,DV,DS \
            --build-zip /tmp/crs135_forms/
        ↳ resolves <OID>.xlsx out of --build-zip (a directory of xlsx
          files, or a .zip we'll extract) and uploads one per OID,
          exercising the scroll_into_view_if_needed fix. Bypasses the
          full pipeline — no Monday, no EDC build, no FormPublisher.
          Prints success / timeout / failed per OID.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import zipfile
from pathlib import Path
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


async def _fetch_target_cards(
    token: str,
    target_oids: set[str] | None = None,
) -> list[dict]:
    """GET the board and pull out (card_id, oid, title, versions) for
    every non-archived card whose formOcoid is in `target_oids`. Defaults
    to TARGET_OIDS for backwards-compat with the default-mode run()."""
    filter_oids = target_oids if target_oids is not None else TARGET_OIDS
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
        if oid not in filter_oids:
            continue
        out.append({
            "card_id":  c.get("_id"),
            "oid":      oid,
            "title":    c.get("title", ""),
            "versions": c.get("versions") or [],
        })
    return out


# ── --upload mode helpers ───────────────────────────────────────────────────

def _resolve_xlsx_for_oids(
    build_zip_arg: str, oids: set[str],
) -> dict[str, str]:
    """Return {OID: xlsx_path} for each requested OID.

    * If --build-zip is a directory: walks it (recursive, case-insensitive)
      and picks the first <OID>.xlsx match per OID. Vendor builds put
      xlsx files in a forms/ subdir so the walk is necessary.
    * If --build-zip is a .zip: creates a fresh tempfile.mkdtemp() and
      extracts only the requested <OID>.xlsx members into it. Skips the
      noise of `extractall` and lands the files in /tmp where the OS
      will eventually reap them.

    Missing OIDs simply omit from the returned dict — the caller already
    surfaces those as `missing_xlsx` in the run summary.
    """
    p = Path(build_zip_arg).expanduser()
    # Fully uppercased — comparisons below use f.upper() / basename.upper()
    # so the extension half must be uppercase too or the set membership
    # check will never hit ("AE.XLSX" in {"AE.xlsx"} → False).
    target_names = {f"{o.upper()}.XLSX" for o in oids}
    found: dict[str, str] = {}

    if p.is_dir():
        for root, _dirs, files in os.walk(p):
            for f in files:
                u = f.upper()
                if u in target_names:
                    oid = u[:-len(".XLSX")]
                    found.setdefault(oid, os.path.join(root, f))
        return found

    if p.is_file() and p.suffix.lower() == ".zip":
        dest = tempfile.mkdtemp(prefix="test_publisher_xlsx_")
        print(f"[upload] extracting requested xlsx → {dest}")
        with zipfile.ZipFile(p) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                basename = os.path.basename(member)
                if basename.upper() not in target_names:
                    continue
                # zf.extract preserves the in-zip path; resolve to the
                # actual on-disk file we just wrote.
                zf.extract(member, dest)
                oid = basename.upper()[:-len(".XLSX")]
                found.setdefault(oid, os.path.join(dest, member))
        return found

    print(f"ERROR: --build-zip path {build_zip_arg!r} is neither a "
          f"directory nor a .zip file.", file=sys.stderr)
    sys.exit(1)


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


# ── Upload mode (--upload) ───────────────────────────────────────────────────

async def upload_mode(oids: set[str], build_path: str) -> int:
    """Click + upload + wait per OID, exercising the same primitives the
    publisher's FULL path uses (overlay-clear, scroll_into_view_if_needed,
    minicard click, set_input_files on the panel's file input, wait for
    radio/prevBtn). Bypasses the rest of the pipeline.

    Per-OID outcomes: success | timeout | failed.

    Returns 0 if every OID succeeded, 1 otherwise.
    """
    session_path = _find_session()
    print(f"[upload] session: {session_path}")

    # Resolve xlsx for every requested OID up front so we fail fast if
    # the build is incomplete before we open the browser.
    xlsx_map = _resolve_xlsx_for_oids(build_path, oids)
    missing_xlsx: list[str] = []
    for oid in sorted(oids):
        if oid in xlsx_map:
            print(f"  ✓ {oid:<8} → {xlsx_map[oid]}")
        else:
            missing_xlsx.append(oid)
            print(f"  ✗ {oid:<8} → no <{oid}>.xlsx in {build_path}")
    if not xlsx_map:
        print("[upload] no xlsx files resolved — exiting", file=sys.stderr)
        return 1

    token = await _get_oc_token()
    cards = await _fetch_target_cards(token, target_oids=set(xlsx_map.keys()))

    # First card wins per OID — a form's definition is shared across all
    # its cards on the board, so uploading once is sufficient.
    per_oid: dict[str, dict] = {}
    for c in cards:
        oid = c["oid"]
        if oid not in per_oid:
            per_oid[oid] = c

    print(f"[upload] {len(per_oid)} OIDs map to cards on the board:")
    for oid, c in per_oid.items():
        v = "has-versions" if c["versions"] else "no-versions"
        print(f"  {oid:<8} card_id={c['card_id']}  {v}  "
              f"title={c['title']!r}")
    no_card = sorted(set(xlsx_map.keys()) - set(per_oid.keys()))
    if no_card:
        print(f"[upload] xlsx resolved but no card on board for: {no_card}")

    # Per-OID outcomes: (oid, category, detail)
    # categories: success | timeout | failed
    results: list[tuple[str, str, str]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            context = await browser.new_context(storage_state=session_path)
            page = await context.new_page()

            print(f"[upload] navigating to {BOARD_URL}")
            await page.goto(BOARD_URL, wait_until="domcontentloaded")
            await page.wait_for_selector(".js-minicard", timeout=60_000)
            await page.wait_for_timeout(1500)
            board_url_after = page.url
            print(f"[upload] board ready (page.url={board_url_after})")

            for oid, card in per_oid.items():
                xlsx_path = xlsx_map[oid]
                cid = card["card_id"]
                print(f"\n[upload] ── {oid} card_id={cid} ──")
                print(f"         xlsx={xlsx_path}")

                # Match the publisher's FULL-path sequence exactly so this
                # test exercises the same scroll fix + selectors.
                try:
                    # 1. Force-clear board overlay (mirrors publisher).
                    try:
                        await page.evaluate(
                            "document.querySelectorAll('.board-overlay')"
                            ".forEach(el => el.remove())")
                        await page.wait_for_timeout(200)
                    except Exception:
                        pass

                    # 2. Scroll the minicard into view and click. Use a
                    # CSS attribute-ends-with selector so we target THIS
                    # card by id regardless of the surrounding slug.
                    mc_selector = f'.js-minicard[href$="/{cid}"]'
                    _mc = page.locator(mc_selector)
                    try:
                        await _mc.scroll_into_view_if_needed(timeout=5000)
                        await _mc.click()
                    except Exception as e:
                        print(f"         FAIL: minicard click — "
                              f"{type(e).__name__}: {e}")
                        results.append((oid, "failed",
                                        f"minicard click: {e}"))
                        continue

                    # 3. Defensive: click the Upload tab if present.
                    # The publisher's production path doesn't need this
                    # (file input is on the default tab) but we surface
                    # it as an explicit step so layout drift surfaces
                    # cleanly rather than as a phantom "no file input".
                    # Errors swallowed — if no tab exists, the file
                    # input is already directly available.
                    try:
                        upload_tab = page.locator(
                            'text=/^Upload$/i').first
                        if await upload_tab.count() > 0:
                            await upload_tab.click(timeout=2000)
                            await page.wait_for_timeout(300)
                    except Exception:
                        pass

                    # 4. Wait for the file input the panel renders.
                    try:
                        await page.wait_for_selector(
                            'input.js-design-form-input',
                            timeout=15_000)
                    except Exception as e:
                        print(f"         FAIL: file input not seen in 15s — "
                              f"{type(e).__name__}: {e}")
                        results.append((oid, "failed",
                                        f"no file input: {e}"))
                        continue

                    # 5. Upload — set_input_files doesn't need a click.
                    print(f"         uploading...")
                    await page.set_input_files(
                        'input.js-design-form-input', xlsx_path)

                    # 6. Wait for confirmation. Same selector the
                    # publisher uses post-upload. 35s per spec —
                    # tight enough to distinguish hung from slow.
                    try:
                        await page.wait_for_selector(
                            '#prevBtn:not(.disabled), input[type=radio]',
                            timeout=35_000)
                        print(f"         ✓ SUCCESS — confirmation signal "
                              f"visible")
                        results.append((oid, "success",
                                        "radio/prevBtn visible"))
                    except Exception as e:
                        print(f"         ⌛ TIMEOUT after 35s — "
                              f"{type(e).__name__}")
                        results.append((oid, "timeout",
                                        f"35s: {str(e)[:140]}"))

                except Exception as e:
                    print(f"         FAIL: {type(e).__name__}: {e}")
                    results.append((oid, "failed",
                                    f"{type(e).__name__}: {e}"))

                # Restore board between OIDs so the next minicard click
                # always starts from a clean board context. Cheaper than
                # debugging panel-stuck states across OIDs.
                try:
                    await page.goto(board_url_after,
                                    wait_until="domcontentloaded")
                    await page.wait_for_selector(".js-minicard",
                                                 timeout=30_000)
                    await page.wait_for_timeout(500)
                except Exception as e:
                    print(f"         BOARD RESTORE FAIL: {e}")

        finally:
            await browser.close()

    # ── Summary ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("UPLOAD SUMMARY")
    print("=" * 72)

    by_cat: dict[str, list[tuple[str, str]]] = {
        "success": [], "timeout": [], "failed": [],
    }
    for oid, cat, detail in results:
        by_cat.setdefault(cat, []).append((oid, detail))

    for cat, label in (
        ("success", "Uploaded successfully"),
        ("timeout", "Timed out waiting for confirmation"),
        ("failed",  "Failed before reaching confirmation"),
    ):
        items = by_cat.get(cat, [])
        print(f"\n{label} ({len(items)}):")
        if not items:
            print("  (none)")
            continue
        for oid, detail in items:
            print(f"  {oid:<8} {detail}")

    if missing_xlsx:
        print(f"\nMissing xlsx on disk (skipped): {missing_xlsx}")
    if no_card:
        print(f"Missing card on board (skipped): {no_card}")

    bad = len(by_cat.get("timeout", [])) + len(by_cat.get("failed", []))
    return 0 if bad == 0 else 1


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
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--capture-session",
        action="store_true",
        help="Open Chromium, navigate to the board, wait for interactive "
             "SSO login, then save storage_state to "
             "~/oc-ai-pipeline/data/browser_sessions/. No tests run.",
    )
    mode.add_argument(
        "--upload",
        action="store_true",
        help="Upload-only mode: click minicard + set_input_files + wait "
             "for confirmation per OID. Requires --oids and --build-zip.",
    )
    p.add_argument(
        "--oids",
        type=str,
        default=None,
        help="Comma-separated OIDs to upload (only with --upload). "
             "Example: --oids AE,AESAE,CM,DV,DS",
    )
    p.add_argument(
        "--build-zip",
        type=str,
        default=None,
        help="Path to a directory of <OID>.xlsx files (or a .zip we'll "
             "extract). Only with --upload.",
    )
    args = p.parse_args()
    if args.upload and (not args.oids or not args.build_zip):
        p.error("--upload requires both --oids and --build-zip")
    if (args.oids or args.build_zip) and not args.upload:
        p.error("--oids / --build-zip only apply with --upload")
    return args


if __name__ == "__main__":
    args = _parse_args()
    if args.capture_session:
        sys.exit(asyncio.run(capture_session()))
    if args.upload:
        oids = {o.strip().upper() for o in args.oids.split(",") if o.strip()}
        if not oids:
            print("ERROR: --oids parsed to empty set", file=sys.stderr)
            sys.exit(1)
        sys.exit(asyncio.run(upload_mode(oids, args.build_zip)))
    sys.exit(asyncio.run(run()))
