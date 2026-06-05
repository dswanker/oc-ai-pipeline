#!/usr/bin/env python3
"""
migrate_board_card_oids.py — one-shot board-card OID migration.

Connects to the CRS-135 OpenClinica design board with a saved Playwright
session, then updates every non-archived form card whose `formOcoid` is a
bare OID (e.g. "AE", "SLEEP") to the F_-prefixed form OpenClinica stores
internally (e.g. "F_AE", "F_SLEEP"). Cards already F_-prefixed are left
alone (idempotent).

Mechanism: executes JavaScript in the loaded board page that calls the
Meteor minimongo API directly — `Cards.update(id, {$set: {formOcoid}})`.
This is the same client-side mutation path the publisher uses for the
set-default fix; persistence to the server relies on Wekan's allow rules.
Because the local update is optimistic, this script RELOADS the board
after applying and re-reads each card from the server to report what
actually persisted.

Usage:
    python3 migrate_board_card_oids.py              # prompts before changing
    python3 migrate_board_card_oids.py --dry-run    # plan only, no changes
    python3 migrate_board_card_oids.py --verify-only # report state, exit 0/1
    python3 migrate_board_card_oids.py --yes        # skip the confirm prompt
    python3 migrate_board_card_oids.py --headless # run without a visible window
    python3 migrate_board_card_oids.py --session /path/to/session.json

This is a one-shot utility — not part of the pipeline. Do not commit.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from urllib.parse import urlparse

BOARD_URL = "https://cust1.design.openclinica.io/b/DMjtshj8C8sC8yLgc/crs-135"
SESSION_FILE = "/data/browser_sessions/dswanker@openclinica.com.json"

# Selector that only appears once the board (and Meteor) has actually
# loaded — same signal the publisher waits on.
CARD_SELECTOR = ".js-minicard"


def _board_id_from_url(url: str) -> str:
    """Extract the Wekan board _id from a /b/{id}/{slug} URL."""
    parts = url.split("/b/")
    if len(parts) > 1:
        return parts[1].split("/")[0]
    return ""


def _f_prefixed(oid: str) -> str:
    """Return the F_-prefixed form of an OID. Idempotent and
    case-insensitive on the prefix: 'AE' -> 'F_AE', 'F_AE' -> 'F_AE'."""
    o = str(oid or "").strip()
    if not o:
        return o
    return o if o.upper().startswith("F_") else f"F_{o}"


def _on_auth_page(url: str) -> bool:
    """True when the page has been redirected to the OC/Keycloak login
    flow — i.e. the saved session expired."""
    u = url or ""
    return ("auth.openclinica.io" in u
            or "openid-connect/auth" in u
            or "/callback" in u)


# ── JS executed in the board page (Meteor client scope) ──────────────────────

# Read all non-archived cards on this board.
_JS_READ_CARDS = """
(boardId) => {
    if (typeof Cards === 'undefined') {
        return {error: 'Cards collection not in window scope'};
    }
    const sel = boardId
        ? {boardId: boardId, archived: false}
        : {archived: false};
    try {
        return {cards: Cards.find(sel).map(c => ({
            id: c._id,
            formOcoid: c.formOcoid || '',
            title: (c.title || c.name || '')
        }))};
    } catch (e) {
        return {error: e.toString()};
    }
}
"""

# Update a single card's formOcoid via minimongo.
_JS_UPDATE_ONE = """
(item) => {
    try {
        Cards.update(item.id, {$set: {formOcoid: item.newOid}});
        const c = Cards.findOne(item.id);
        return {ok: true, now: (c && c.formOcoid) || ''};
    } catch (e) {
        return {ok: false, error: e.toString()};
    }
}
"""


async def _load_board(p, headless: bool, session_file: str, board_url: str):
    """Launch chromium, load the saved session, navigate to the board, and
    wait until Meteor's card collection is live. Returns (browser, page).
    Raises RuntimeError with actionable guidance on session/auth problems."""
    browser = await p.chromium.launch(headless=headless)
    context = await browser.new_context(storage_state=session_file)
    page = await context.new_page()

    await page.goto(board_url, wait_until="domcontentloaded")

    # If the session expired we land on the SSO page. In a visible window the
    # operator can log in manually; headless can't, so fail with guidance.
    if _on_auth_page(page.url):
        if headless:
            await browser.close()
            raise RuntimeError(
                "Saved session expired — redirected to SSO login. "
                "Re-run WITHOUT --headless and complete the login in the "
                "browser window, or refresh the session file.")
        print("[migrate] Session appears expired — complete the SSO login "
              "in the browser window; waiting up to 180s for the board...",
              flush=True)
        await page.wait_for_selector(CARD_SELECTOR, timeout=180_000)
    else:
        try:
            await page.wait_for_selector(CARD_SELECTOR, timeout=20_000)
        except Exception as e:
            await browser.close()
            raise RuntimeError(
                f"Board did not load (no {CARD_SELECTOR} within 20s): {e}. "
                f"Is the board URL correct and the session valid?")

    # Cards render asynchronously after the selector appears; let minimongo
    # settle so the read sees the full set.
    await page.wait_for_timeout(1500)

    meteor_ready = await page.evaluate(
        "typeof Cards !== 'undefined' && typeof Meteor !== 'undefined'")
    if not meteor_ready:
        await browser.close()
        raise RuntimeError(
            "Meteor/Cards not in page scope after load — the page is not the "
            "live board (session issue or wrong URL).")
    return browser, page


async def _read_cards(page, board_id: str) -> list[dict]:
    res = await page.evaluate(_JS_READ_CARDS, board_id)
    if isinstance(res, dict) and res.get("error"):
        raise RuntimeError(f"reading cards failed: {res['error']}")
    return (res or {}).get("cards", [])


def _build_plan(cards: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split cards into (to_update, already_prefixed, no_oid).

    to_update entries carry id, title, oldOid, newOid.
    """
    to_update, already, no_oid = [], [], []
    for c in cards:
        oid = str(c.get("formOcoid") or "").strip()
        if not oid:
            no_oid.append(c)
            continue
        if oid.upper().startswith("F_"):
            already.append(c)
            continue
        to_update.append({
            "id": c["id"],
            "title": c.get("title", ""),
            "oldOid": oid,
            "newOid": _f_prefixed(oid),
        })
    return to_update, already, no_oid


def _print_plan(to_update, already, no_oid, total):
    print("\n" + "=" * 72)
    print(f"PLAN — {total} non-archived card(s) on board")
    print("=" * 72)
    print(f"  to update          : {len(to_update)}")
    print(f"  already F_-prefixed : {len(already)}  (skip)")
    print(f"  no/empty formOcoid  : {len(no_oid)}  (skip — cannot prefix)")
    if to_update:
        print("\n  Cards that WILL change:")
        for item in to_update:
            t = (item["title"][:34] + "…") if len(item["title"]) > 35 \
                else item["title"]
            print(f"    {item['id']:20}  {item['oldOid']:12} -> "
                  f"{item['newOid']:14}  [{t}]")
    if no_oid:
        print("\n  Cards skipped for empty formOcoid:")
        for c in no_oid:
            print(f"    {c['id']:20}  (title: {c.get('title','')[:40]})")
    print()


def _print_verify(to_update, already, no_oid, total):
    """Report current migration state (read-only). `to_update` here means
    'still bare' — cards that have NOT yet been migrated."""
    print("\n" + "=" * 72)
    print(f"VERIFY — {total} non-archived card(s) on board")
    print("=" * 72)
    print(f"  F_-prefixed (migrated)  : {len(already)}")
    print(f"  bare OID (NOT migrated) : {len(to_update)}")
    print(f"  no/empty formOcoid      : {len(no_oid)}  (cannot migrate)")
    if to_update:
        print("\n  Cards still bare (need migration):")
        for item in to_update:
            t = (item["title"][:34] + "…") if len(item["title"]) > 35 \
                else item["title"]
            print(f"    {item['id']:20}  {item['oldOid']:12} (would become "
                  f"{item['newOid']})  [{t}]")
    if no_oid:
        print("\n  Cards with no formOcoid (cannot migrate):")
        for c in no_oid:
            print(f"    {c['id']:20}  (title: {c.get('title','')[:40]})")
    print()
    if not to_update:
        print("VERDICT: FULLY MIGRATED — every card with an OID is "
              "F_-prefixed.")
    else:
        print(f"VERDICT: INCOMPLETE — {len(to_update)} card(s) still have "
              f"bare OIDs.")


async def _apply(page, to_update: list[dict]) -> tuple[int, int]:
    """Apply each update individually so one failure never aborts the run.
    Returns (updated, failed)."""
    updated = failed = 0
    for item in to_update:
        try:
            res = await asyncio.wait_for(
                page.evaluate(_JS_UPDATE_ONE, item), timeout=20)
        except Exception as e:  # noqa: BLE001 — isolate per-card
            res = {"ok": False, "error": f"evaluate failed: {e}"}
        if res.get("ok"):
            updated += 1
            print(f"  [OK]   {item['id']}: {item['oldOid']} -> "
                  f"{item['newOid']}", flush=True)
        else:
            failed += 1
            print(f"  [FAIL] {item['id']}: {item['oldOid']} -> "
                  f"{item['newOid']}  ({res.get('error')})", flush=True)
    return updated, failed


async def _verify_persisted(page, board_url, board_id, to_update) -> int:
    """Reload the board (re-subscribes from the server) and re-read each
    updated card to confirm the new OID actually persisted server-side —
    minimongo updates are optimistic, so this is the real check."""
    await page.wait_for_timeout(3000)  # let DDP flush to the server
    try:
        await page.goto(board_url, wait_until="domcontentloaded")
        await page.wait_for_selector(CARD_SELECTOR, timeout=20_000)
        await page.wait_for_timeout(1500)
        cards = await _read_cards(page, board_id)
    except Exception as e:  # noqa: BLE001 — verification is best-effort
        print(f"[migrate] reload-verify skipped ({e})", flush=True)
        return -1
    by_id = {c["id"]: str(c.get("formOcoid") or "") for c in cards}
    persisted = sum(
        1 for item in to_update
        if by_id.get(item["id"], "").upper() == item["newOid"].upper()
    )
    return persisted


async def run(dry_run: bool, headless: bool, session_file: str,
              board_url: str, assume_yes: bool,
              verify_only: bool = False) -> int:
    if not os.path.exists(session_file):
        print(f"ERROR: session file not found: {session_file}\n"
              f"  Pass --session <path> to a valid Playwright storage_state "
              f"JSON.", file=sys.stderr)
        return 2

    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        print(f"ERROR: playwright not installed: {e}\n"
              f"  pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 2

    board_id = _board_id_from_url(board_url)
    print(f"[migrate] board   : {board_url}")
    print(f"[migrate] board_id: {board_id or '(could not parse)'}")
    print(f"[migrate] session : {session_file}")
    _mode = "VERIFY-ONLY" if verify_only else ("DRY-RUN" if dry_run else "APPLY")
    print(f"[migrate] mode    : {_mode}")

    async with async_playwright() as p:
        browser, page = await _load_board(p, headless, session_file, board_url)
        try:
            cards = await _read_cards(page, board_id)
            to_update, already, no_oid = _build_plan(cards)

            # --verify-only: report current state and exit, never write.
            # The page load re-subscribed fresh from the server, so this
            # read reflects true persisted state. rc=0 iff fully migrated.
            if verify_only:
                _print_verify(to_update, already, no_oid, len(cards))
                return 0 if not to_update else 1

            _print_plan(to_update, already, no_oid, len(cards))

            if not to_update:
                print("Nothing to migrate — all cards already F_-prefixed "
                      "(or have no OID). Done.")
                return 0

            if dry_run:
                print("--dry-run: no changes made.")
                return 0

            if not assume_yes:
                try:
                    ans = input(f"Apply {len(to_update)} update(s)? "
                                f"[y/N] ").strip().lower()
                except EOFError:
                    ans = ""
                if ans not in ("y", "yes"):
                    print("Aborted — no changes made.")
                    return 1

            print("\n[migrate] applying updates...")
            updated, failed = await _apply(page, to_update)

            persisted = await _verify_persisted(
                page, board_url, board_id, to_update)

            print("\n" + "=" * 72)
            print("SUMMARY")
            print("=" * 72)
            print(f"  updated (call ok) : {updated}")
            print(f"  skipped           : {len(already)} already F_ + "
                  f"{len(no_oid)} no-OID = {len(already) + len(no_oid)}")
            print(f"  failed            : {failed}")
            if persisted >= 0:
                print(f"  persisted (reload): {persisted}/{len(to_update)} "
                      f"confirmed F_-prefixed after server re-read")
                if persisted < updated:
                    print("  WARNING: fewer cards persisted than updated — "
                          "Wekan may not allow client-side formOcoid writes. "
                          "Verify via the board API before relying on this.")
            return 0 if failed == 0 else 1
        finally:
            await browser.close()


def main():
    ap = argparse.ArgumentParser(
        description="Migrate board card formOcoid values to F_-prefixed OIDs.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan without making any changes.")
    ap.add_argument("--verify-only", action="store_true",
                    help="Report current migration state and exit (no writes, "
                         "no prompt). Exit 0 if fully F_-prefixed, 1 otherwise.")
    ap.add_argument("--yes", action="store_true",
                    help="Skip the confirmation prompt (apply immediately).")
    ap.add_argument("--headless", action="store_true",
                    help="Run without a visible browser window.")
    ap.add_argument("--session", default=SESSION_FILE,
                    help=f"Playwright storage_state JSON (default: {SESSION_FILE}).")
    ap.add_argument("--board-url", default=BOARD_URL,
                    help="Override the board URL.")
    args = ap.parse_args()

    rc = asyncio.run(run(
        dry_run=args.dry_run,
        headless=args.headless,
        session_file=args.session,
        board_url=args.board_url,
        assume_yes=args.yes,
        verify_only=args.verify_only,
    ))
    sys.exit(rc)


if __name__ == "__main__":
    main()
