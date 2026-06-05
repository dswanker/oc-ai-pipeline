"""
test_upload_slow_forms.py — Step-by-step Playwright upload probe for CRS-135

Usage:
    cd ~/oc-ai-pipeline
    python3 test_upload_slow_forms.py [FORM_OID]

    FORM_OID: one of AE, AESAE, CM, DV  (default: AE)

Screenshots saved to: ~/Desktop/oc_probe_screenshots/
Console output: step name + elapsed ms from run start.

Copy this file into ~/oc-ai-pipeline/ before running.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

SESSION_PATH   = "/Users/danswanker/oc-ai-pipeline/data/browser_sessions/dswanker@openclinica.com.json"
BOARD_URL      = "https://cust1.design.openclinica.io/b/DMjtshj8C8sC8yLgc/crs-135"
FORMS_DIR      = Path.home() / "Downloads" / "CRS-135_EDC_Build_20260527" / "forms"
SCREENSHOT_DIR = Path.home() / "Desktop" / "oc_probe_screenshots"

FORM_NAMES = {
    "AE":    "Adverse Events",
    "AESAE": "Serious Adverse Event Report",
    "CM":    "Concomitant Medications",
    "DV":    "Protocol Deviations",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_run_start = time.time()
_step = 0

def log(msg: str):
    elapsed_ms = int((time.time() - _run_start) * 1000)
    print(f"[{elapsed_ms:>7}ms] {msg}", flush=True)

async def shot(page, label: str):
    global _step
    _step += 1
    filename = SCREENSHOT_DIR / f"{_step:02d}_{label}.png"
    try:
        await page.screenshot(path=str(filename), full_page=False)
        log(f"  📸  {filename.name}")
    except Exception as e:
        log(f"  📸  FAILED ({e})")
    return filename

async def poll_for_radio(page, timeout_ms: int = 60_000):
    """
    Poll every 2s for input[type=radio]. Screenshot at each poll.
    Returns (found: bool, elapsed_ms: int).
    """
    start = time.time()
    deadline = start + timeout_ms / 1000
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        elapsed_ms = int((time.time() - start) * 1000)

        radio = await page.query_selector("input[type=radio]")
        if radio:
            await shot(page, f"RADIO_FOUND_poll{attempt:02d}")
            log(f"  ✅ Radio FOUND at poll {attempt} — {elapsed_ms}ms after upload")
            return True, elapsed_ms

        # Capture any visible alert/banner
        try:
            banners = await page.evaluate("""
                () => [...document.querySelectorAll('.alert, [class*="error"], [class*="Error"], [class*="banner"]')]
                    .slice(0, 3).map(el => el.innerText.slice(0, 100))
            """)
        except Exception:
            banners = []

        # Capture visible text summary
        try:
            snippet = await page.evaluate(
                "() => document.body.innerText.replace(/\\s+/g,' ').slice(0, 200)")
        except Exception:
            snippet = "<eval failed>"

        await shot(page, f"poll{attempt:02d}_no_radio_{elapsed_ms}ms")
        log(f"  ⏳ Poll {attempt} +{elapsed_ms}ms — no radio"
            + (f" | banners: {banners}" if banners else "")
            + f" | page: {snippet[:100]!r}")

        await asyncio.sleep(2)

    elapsed_ms = int((time.time() - start) * 1000)
    await shot(page, f"TIMEOUT_after_{elapsed_ms}ms")
    log(f"  ⏰ TIMEOUT — radio never appeared after {elapsed_ms}ms")
    return False, elapsed_ms

# ── Main probe ────────────────────────────────────────────────────────────────

async def probe(form_oid: str):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    # Clean out old screenshots from this directory
    for f in SCREENSHOT_DIR.glob("*.png"):
        f.unlink()
    log(f"Cleared old screenshots from {SCREENSHOT_DIR}")

    xlsx_path = FORMS_DIR / f"{form_oid}.xlsx"
    if not xlsx_path.exists():
        log(f"ERROR: {xlsx_path} not found")
        log(f"       Check FORMS_DIR = {FORMS_DIR}")
        return

    log(f"=== PROBING: {form_oid} — {FORM_NAMES.get(form_oid, '?')} ===")
    log(f"    xlsx:        {xlsx_path}")
    log(f"    session:     {SESSION_PATH}")
    log(f"    screenshots: {SCREENSHOT_DIR}")
    log("")

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)

        if not os.path.exists(SESSION_PATH):
            log(f"ERROR: session file not found: {SESSION_PATH}")
            await browser.close()
            return

        context = await browser.new_context(storage_state=SESSION_PATH)
        page = await context.new_page()
        # Check session freshness
        await page.goto(BOARD_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        if "auth.openclinica.io" in page.url or "logout" in page.url or "openid-connect" in page.url:
            log("Session stale — navigate to OC in the browser window and log in.")
            log("Once the board is fully loaded (cards visible), press Enter here.")
            input("  >> Press Enter after logging in: ")
            await context.storage_state(path=SESSION_PATH)
            log("Fresh session saved. Continuing test...")
            await page.goto(BOARD_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
        else:
            log(f"Session valid: {page.url[:80]}")
        page.set_default_timeout(30_000)

        # Console capture
        page.on("console", lambda m: (
            log(f"  [console.{m.type}] {m.text[:120]}")
            if m.type in ("error", "warning") else None
        ))

        # ── 1. Load board ─────────────────────────────────────────────────
        log("── STEP 1: Load board ──────────────────────────────────────")
        t = time.time()
        await page.goto(BOARD_URL, wait_until="domcontentloaded")
        log(f"  goto() returned in {int((time.time()-t)*1000)}ms  url={page.url}")
        await shot(page, "01_board_loaded")

        # ── 2. Wait for minicards ─────────────────────────────────────────
        log("── STEP 2: Wait for minicards ──────────────────────────────")
        t = time.time()
        try:
            await page.wait_for_selector(".js-minicard", timeout=30_000)
            await page.wait_for_timeout(1500)
            n = await page.evaluate(
                "() => document.querySelectorAll('.js-minicard').length")
            log(f"  {n} minicards in {int((time.time()-t)*1000)}ms")
        except Exception as e:
            log(f"  ERROR: {e}")
            await shot(page, "02_minicards_ERROR")
            await browser.close()
            return
        await shot(page, "02_minicards_visible")

        # ── 3. Locate target card ─────────────────────────────────────────
        log(f"── STEP 3: Find first card for OID={form_oid} ──────────────")
        t = time.time()

        # Query Meteor's Cards collection for this OID
        meteor_cards = await page.evaluate(f"""
            () => {{
                try {{
                    return Cards.find({{archived:false, formOcoid:'{form_oid}'}})
                        .fetch().slice(0,5)
                        .map(c => ({{id:c._id, oid:c.formOcoid||'', title:c.title||c.name||''}}) );
                }} catch(e) {{ return {{error: String(e)}}; }}
            }}
        """)
        log(f"  Meteor Cards result: {meteor_cards}")

        target = None
        if isinstance(meteor_cards, list) and meteor_cards:
            first_id = meteor_cards[0].get("id", "")
            if first_id:
                target = page.locator(f'.js-minicard[href*="{first_id}"]').first
                log(f"  Targeting minicard by Meteor ID: {first_id}")

        if target is None:
            display = FORM_NAMES[form_oid]
            target = page.locator(".js-minicard").filter(has_text=display).first
            log(f"  Fallback: matching by display name: {display!r}")

        await shot(page, "03_before_card_click")

        # ── 4. Scroll + click ─────────────────────────────────────────────
        log("── STEP 4: Scroll card into view + click ───────────────────")
        t = time.time()
        try:
            await target.scroll_into_view_if_needed(timeout=5_000)
            await shot(page, "04a_card_scrolled_into_view")
            await target.click()
            log(f"  click() done in {int((time.time()-t)*1000)}ms")
        except Exception as e:
            log(f"  ERROR: {e}")
            await shot(page, "04_click_ERROR")
            await browser.close()
            return

        # ── 5. Watch panel open — screenshot every second for 5s ─────────
        log("── STEP 5: Watching panel open (screenshot every 1s) ───────")
        for i in range(1, 6):
            await page.wait_for_timeout(1000)
            elapsed = int((time.time() - _run_start) * 1000)
            # Check what's visible
            try:
                panel_visible = await page.evaluate(
                    "() => !!document.querySelector('input.js-design-form-input')")
                radio_visible = await page.evaluate(
                    "() => !!document.querySelector('input[type=radio]')")
            except Exception:
                panel_visible = radio_visible = "?"
            await shot(page, f"05_{i}s_panel={panel_visible}_radio={radio_visible}")
            log(f"  {i}s: file_input={panel_visible}  radio={radio_visible}")
            if panel_visible and panel_visible != "?":
                break

        # ── 6. Confirm file input present + read OID ──────────────────────
        log("── STEP 6: Confirm file input + read panel OID ─────────────")
        try:
            await page.wait_for_selector("input.js-design-form-input",
                                         timeout=15_000)
            log("  ✅ File input confirmed present")
        except Exception as e:
            log(f"  ❌ File input NOT found: {e}")
            try:
                body = await page.evaluate(
                    "() => document.body.innerText.replace(/\\s+/g,' ').slice(0,400)")
                log(f"  body: {body!r}")
            except Exception:
                pass
            await shot(page, "06_file_input_MISSING")
            await browser.close()
            return

        oid_el = await page.query_selector("input#formOcOidValue")
        panel_oid = (await oid_el.input_value()).upper() if oid_el else "(not found)"
        log(f"  Panel OID: {panel_oid}")

        existing_radio = await page.query_selector("input[type=radio]")
        log(f"  Radio BEFORE upload: {'YES' if existing_radio else 'NO'}")
        await shot(page, f"06_panel_open_oid={panel_oid}_preRadio={'YES' if existing_radio else 'NO'}")

        # ── 7. set_input_files ────────────────────────────────────────────
        log("── STEP 7: set_input_files() ───────────────────────────────")
        log(f"  File: {xlsx_path.name}  ({xlsx_path.stat().st_size:,} bytes)")
        t_upload = time.time()
        file_input = page.locator("input.js-design-form-input")
        await file_input.set_input_files(str(xlsx_path))
        elapsed_set = int((time.time() - t_upload) * 1000)
        log(f"  set_input_files() returned in {elapsed_set}ms")
        await shot(page, f"07_immediately_after_set_input_files_{elapsed_set}ms")

        # ── 8. Watch for 5 seconds with screenshots ───────────────────────
        log("── STEP 8: First 5s after upload — screenshot each second ──")
        for i in range(1, 6):
            await page.wait_for_timeout(1000)
            try:
                radio_now = await page.evaluate(
                    "() => !!document.querySelector('input[type=radio]')")
                alerts = await page.evaluate("""
                    () => [...document.querySelectorAll('.alert')].map(
                        el => el.innerText.slice(0,80)).join(' | ')
                """)
            except Exception:
                radio_now = "?"
                alerts = ""
            elapsed_total = int((time.time() - t_upload) * 1000)
            await shot(page, f"08_{i}s_post_upload_radio={radio_now}")
            log(f"  +{i}s ({elapsed_total}ms): radio={radio_now}"
                + (f" | alert: {alerts!r}" if alerts else ""))
            if radio_now is True:
                log("  🎉 Radio appeared within first 5s!")
                break

        # ── 9. Poll up to 60s more ────────────────────────────────────────
        log("── STEP 9: Polling for radio (2s intervals, 60s budget) ────")
        found, poll_elapsed = await poll_for_radio(page, timeout_ms=60_000)

        # ── 10. Full DOM dump ─────────────────────────────────────────────
        log("── STEP 10: DOM state at end ───────────────────────────────")
        try:
            dom = await page.evaluate("""
                () => ({
                    url: window.location.href,
                    radios: document.querySelectorAll('input[type=radio]').length,
                    fileInputs: document.querySelectorAll('input.js-design-form-input').length,
                    alerts: [...document.querySelectorAll('.alert')].map(
                        el => ({cls: el.className.slice(0,60), txt: el.innerText.slice(0,100)})),
                    panelVisible: !!document.querySelector('.card-detail'),
                    cardTitle: (document.querySelector('.card-detail-title, .js-card-title') || {}).innerText || '',
                    versionItems: document.querySelectorAll('[class*="version"], .form-version').length,
                    bodySnippet: document.body.innerText.replace(/\\s+/g,' ').slice(0,300)
                })
            """)
            for k, v in dom.items():
                log(f"  {k}: {v}")
        except Exception as e:
            log(f"  DOM dump failed: {e}")
        await shot(page, "10_final_dom_state")

        # ── Summary ───────────────────────────────────────────────────────
        total_ms = int((time.time() - _run_start) * 1000)
        log("")
        log("=" * 60)
        log(f"SUMMARY for {form_oid}:")
        log(f"  set_input_files() returned: {elapsed_set}ms")
        log(f"  Radio found: {'YES' if found else 'NO'}")
        log(f"  Time until radio (or timeout): {poll_elapsed}ms")
        log(f"  Total run time: {total_ms}ms")
        log(f"  Screenshots: {SCREENSHOT_DIR}")
        log("=" * 60)

        log("Keeping browser open for 15s...")
        await asyncio.sleep(15)
        await browser.close()


if __name__ == "__main__":
    oid = sys.argv[1].upper() if len(sys.argv) > 1 else "AE"
    if oid not in FORM_NAMES:
        print(f"Unknown: {oid!r}. Choose from: {list(FORM_NAMES)}")
        sys.exit(1)
    asyncio.run(probe(oid))
