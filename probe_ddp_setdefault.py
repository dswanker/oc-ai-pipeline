#!/usr/bin/env python3
"""
probe_ddp_setdefault.py — Capture the Meteor DDP method that fires when
the user closes a card panel after selecting a default-version radio.

Earlier finding: the radio click itself fires no DDP method (handled
client-side). REST PATCH on /api/boards/{id}/cards/{id} also rejected
(read-only, OPTIONS returns only GET/HEAD). Working hypothesis: the
persist happens on panel close — Meteor batches the field write until
the panel commits.

Sequence:
    1. open panel (click first minicard)
    2. click radio
    3. wait 1s
    4. close panel (Escape key)
    5. wait 3s
    6. dump SENT msg=="method" frames only (excluding trackToPiwik,
       sub/unsub control, and frames containing 'events:')

Run:
    python3 probe_ddp_setdefault.py

Requires:
    - playwright installed (`pip install playwright && playwright install chromium`)
    - A valid storage_state JSON at SESSION_PATH below (already used by
      oc_form_publisher.py)
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

from playwright.async_api import async_playwright


SESSION_PATH = os.path.expanduser(
    "~/oc-ai-pipeline/data/browser_sessions/dswanker@openclinica.com.json"
)
BOARD_URL    = "https://cust1.design.openclinica.io/b/DMjtshj8C8sC8yLgc/crs-135"
POST_RADIO_WAIT_S = 1.0   # let the radio click settle before closing
POST_CLOSE_WAIT_S = 3.0   # capture window for the persist method


def _ts() -> str:
    return f"{time.time():.3f}"


def _attach_ws_listeners(ws: Any, frames: list[dict]) -> None:
    """Wire up framesent / framereceived on a single WebSocket and append
    every frame (with direction + timestamp + raw payload) to `frames`.
    Also prints them live so we can watch the flow happen."""
    url = ws.url
    print(f"[{_ts()}] WS OPEN  {url}", flush=True)

    def on_sent(payload: str) -> None:
        frames.append({"t": _ts(), "dir": "SENT", "url": url, "payload": payload})
        print(f"[{_ts()}] WS SENT  {payload}", flush=True)

    def on_recv(payload: str) -> None:
        frames.append({"t": _ts(), "dir": "RECV", "url": url, "payload": payload})
        print(f"[{_ts()}] WS RECV  {payload}", flush=True)

    def on_close() -> None:
        print(f"[{_ts()}] WS CLOSE {url}", flush=True)

    ws.on("framesent", on_sent)
    ws.on("framereceived", on_recv)
    ws.on("close", on_close)


async def main() -> int:
    frames: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            have_session = os.path.exists(SESSION_PATH)
            if have_session:
                context = await browser.new_context(storage_state=SESSION_PATH)
                print(f"[{_ts()}] loaded session {SESSION_PATH}", flush=True)
            else:
                context = await browser.new_context()
                print(f"[{_ts()}] no session file at {SESSION_PATH}",
                      flush=True)
            page = await context.new_page()

            # Wire WS capture BEFORE navigating so we catch the initial
            # DDP handshake (connect / login / sub frames).
            page.on("websocket", lambda ws: _attach_ws_listeners(ws, frames))

            print(f"[{_ts()}] navigating to {BOARD_URL}", flush=True)
            await page.goto(BOARD_URL, wait_until="domcontentloaded")

            if not have_session:
                # Manual SSO: block until the human signs in and the board
                # renders. input() is blocking — run it off the event loop
                # so Playwright can keep servicing the visible browser.
                print("No session file found — please log in manually in "
                      "the browser window, then press Enter here to "
                      "continue.", flush=True)
                await asyncio.to_thread(input)

            print(f"[{_ts()}] waiting for .js-minicard ...", flush=True)
            await page.wait_for_selector(".js-minicard", timeout=60_000)

            # Let any deferred DDP subscriptions settle so the post-click
            # frames stand out from the boot-time noise.
            await page.wait_for_timeout(1500)

            first_card = page.locator(".js-minicard").first
            print(f"[{_ts()}] clicking first minicard ...", flush=True)
            await first_card.click()

            print(f"[{_ts()}] waiting for input[type=radio] ...", flush=True)
            await page.wait_for_selector("input[type=radio]", timeout=15_000)

            # ── Marker frame so the post-click frames are easy to find ──
            marker_pre = {"t": _ts(), "dir": "MARK",
                          "url": "", "payload": "*** about to click radio ***"}
            frames.append(marker_pre)
            print(f"[{_ts()}] *** about to click radio ***", flush=True)

            radio = page.locator("input[type=radio]").first
            await radio.click()

            print(f"[{_ts()}] radio clicked — settling {POST_RADIO_WAIT_S}s "
                  f"before close ...", flush=True)
            await asyncio.sleep(POST_RADIO_WAIT_S)

            # ── Marker frame: close-panel boundary so the persist
            # method is trivial to locate in the transcript ─────────
            marker_close = {"t": _ts(), "dir": "MARK",
                            "url": "",
                            "payload": "*** about to close panel ***"}
            frames.append(marker_close)
            print(f"[{_ts()}] *** about to close panel ***", flush=True)

            # Press Escape to close the panel. OC's designer wires
            # Escape to the panel-close handler; this is the most
            # reliable cross-version close path.
            await page.keyboard.press("Escape")

            print(f"[{_ts()}] panel closed — collecting DDP for "
                  f"{POST_CLOSE_WAIT_S}s ...", flush=True)
            await asyncio.sleep(POST_CLOSE_WAIT_S)

            marker_post = {"t": _ts(), "dir": "MARK",
                           "url": "", "payload": "*** end capture ***"}
            frames.append(marker_post)

        finally:
            await browser.close()

    # ── Filtered dump: only SENT method-call frames, dropping the
    # known-noise calls so the persist method is easy to spot. ────────
    print("\n" + "=" * 72, flush=True)
    print("DDP METHOD CALLS (SENT, filtered)", flush=True)
    print("=" * 72, flush=True)

    NOISE = ("trackToPiwik", "events:")

    for f in frames:
        # Markers stay so the close boundary is visible.
        if f["dir"] == "MARK":
            print(f"[{f['t']}] MARK {f['payload']}", flush=True)
            continue
        if f["dir"] != "SENT":
            continue
        payload = f["payload"]
        if '"msg":"method"' not in payload:
            continue
        if any(n in payload for n in NOISE):
            continue
        print(f"[{f['t']}] SENT {payload}", flush=True)

    print("\n=== PROBE COMPLETE ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
