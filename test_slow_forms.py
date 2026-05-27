#!/usr/bin/env python3
"""
test_slow_forms.py — fast diagnostic for the 7 slow-processing forms.

Mirrors what the production publisher does for SLEEP / SF12 / EX / AE /
AESAE / CM / DV on the CRS-135 board, but scoped to just those forms and
with explicit per-stage timing instrumentation so we can pin down where
each form actually spends its 30-90s. Runs in ~5 minutes on a healthy
session vs. ~12 minutes for the full publish.

Per-form output:
    click+scroll       — time to scroll minicard into view + click
    panel open         — time until `input.js-design-form-input` appears
    set_input          — time inside page.set_input_files itself
    radio attached     — time until `input[type=radio]` is attached
                          (or "TIMEOUT" if the configured ceiling fires)
    in OC REST         — whether GET /api/boards/<id> shows a versions
                          entry for this OID after the upload

Usage:
    python3 test_slow_forms.py

Requires:
    * Browser session JSON at SESSION_PATH (Railway-volume hardcoded
      path; copy locally if running outside Railway).
    * xlsx files under FORMS_DIR (one per form: SLEEP.xlsx, SF12.xlsx,
      ...). If any are missing, the script tries to build them from
      SPEC_PATH using the project's edc-builder module.
    * OC_API_USERNAME / OC_API_PASSWORD env vars for the post-upload
      REST verification step. Without them the REST column reports
      "skipped" and the rest of the run continues normally.

Not pushed — local diagnostic only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.async_api import async_playwright


# ── Config — hardcoded for the Railway volume / CRS-135 board ──────────────
SESSION_PATH = "/data/browser_sessions/dswanker@openclinica.com.json"
BOARD_URL    = "https://cust1.design.openclinica.io/b/DMjtshj8C8sC8yLgc/crs-135"
BOARD_ID     = "DMjtshj8C8sC8yLgc"
SUBDOMAIN    = "cust1"
DESIGN_HOST  = f"https://{SUBDOMAIN}.design.openclinica.io"
AUTH_HOST    = f"https://{SUBDOMAIN}.build.openclinica.io"

SLOW_FORMS: list[str] = ["SLEEP", "SF12", "EX", "AE", "AESAE", "CM", "DV"]

# Where to look for prebuilt xlsx files. If not found here, the script
# falls back to building from SPEC_PATH using build_xlsforms.
FORMS_DIR = "/tmp/crs135_forms"
SPEC_PATH = "/tmp/crs135_spec.json"

# Deliberately exceeds the production 90s ceiling so we can see the
# real settle time rather than capping at the production limit.
RADIO_TIMEOUT_MS = 120_000

# Repo root — used to import build_xlsforms via sys.path injection.
REPO_ROOT = Path(__file__).resolve().parent
EDC_BUILDER_SCRIPTS = REPO_ROOT / "skills" / "edc-builder" / "scripts"


# ── OC REST: token + board snapshot ────────────────────────────────────────

async def _get_oc_token() -> str | None:
    """Same flow as monday_client / probe_board_api. Returns None when
    creds aren't set so the script can still run timing diagnostics
    without REST verification."""
    user = (os.environ.get("OC_API_USERNAME") or "").strip()
    pw   = (os.environ.get("OC_API_PASSWORD") or "").strip()
    if not user or not pw:
        return None
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{AUTH_HOST}/user-service/api/oauth/token",
            headers={"Content-Type": "application/json"},
            json={"username": user, "password": pw},
        )
    if r.status_code != 200:
        print(f"[oc-token] auth failed {r.status_code}: {r.text[:200]}",
              file=sys.stderr)
        return None
    return r.text.strip()


async def _fetch_board_cards(token: str) -> list[dict]:
    """GET /api/boards/{BOARD_ID} → cards array (raw)."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{DESIGN_HOST}/api/boards/{BOARD_ID}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
        )
    r.raise_for_status()
    return (r.json() or {}).get("cards") or []


async def _verify_version_via_rest(token: str, oid: str) -> bool:
    """True iff any non-archived card on the board with this formOcoid
    has a non-empty `versions` array."""
    cards = await _fetch_board_cards(token)
    target = oid.upper()
    for c in cards:
        if c.get("archived"):
            continue
        if (c.get("formOcoid") or "").upper() != target:
            continue
        if c.get("versions"):
            return True
    return False


# ── XLSX resolution: cache → build → bail ──────────────────────────────────

class DiagnosticError(RuntimeError):
    """Raised by helpers when prerequisites are missing. The HTTP
    endpoint catches this and surfaces the message in its response;
    the CLI catches it and prints to stderr before exiting."""


def _resolve_xlsx_files() -> dict[str, str]:
    """Return {OID: xlsx_path} for every SLOW_FORM. Strategy:
      1. Look for FORMS_DIR/<OID>.xlsx (case-insensitive).
      2. For any missing form, try building from SPEC_PATH via the
         project's edc-builder.
      3. Anything still missing → raise DiagnosticError.
    """
    found: dict[str, str] = {}
    for oid in SLOW_FORMS:
        for cand in (
            f"{FORMS_DIR}/{oid}.xlsx",
            f"{FORMS_DIR}/{oid.lower()}.xlsx",
        ):
            if os.path.exists(cand):
                found[oid] = cand
                break

    missing = [o for o in SLOW_FORMS if o not in found]
    if not missing:
        return found

    if not os.path.exists(SPEC_PATH):
        raise DiagnosticError(
            f"missing xlsx for {missing} and no spec at {SPEC_PATH} "
            f"to build them from. Either drop the xlsx files into "
            f"{FORMS_DIR}/ or put the CRS-135 spec at {SPEC_PATH}."
        )

    print(f"[build] {len(missing)} forms missing on disk — building "
          f"from {SPEC_PATH}", flush=True)
    spec = json.load(open(SPEC_PATH))
    missing_upper = {m.upper() for m in missing}
    filtered = dict(spec)
    filtered["forms"] = [
        f for f in (spec.get("forms") or [])
        if (f.get("form_id") or "").upper() in missing_upper
    ]
    if not filtered["forms"]:
        raise DiagnosticError(
            f"spec at {SPEC_PATH} contains no forms matching {missing}"
        )

    sys.path.insert(0, str(EDC_BUILDER_SCRIPTS))
    from build_xlsforms import build_all_xlsforms

    out_dir = tempfile.mkdtemp(prefix="test_slow_forms_")
    build_log = {
        "forms_built": [], "forms_skipped": [], "build_errors": [],
        "build_warnings": [], "placeholder_applied": [],
    }
    build_all_xlsforms(filtered, out_dir, build_log)
    for f in filtered["forms"]:
        oid = (f.get("form_id") or "").upper()
        cand = f"{out_dir}/{oid}.xlsx"
        if os.path.exists(cand):
            found[oid] = cand

    still = [o for o in SLOW_FORMS if o not in found]
    if still:
        raise DiagnosticError(
            f"build completed but xlsx still missing for {still}; "
            f"build_errors={build_log['build_errors']}"
        )

    return found


# ── card_id lookup via the publisher's Cards.find() pattern ───────────────

CARD_ID_LOOKUP_JS = """
(slowForms) => {
    if (typeof Cards === 'undefined') {
        return { error: 'Cards collection not in window scope' };
    }
    const targets = new Set(slowForms.map(s => s.toUpperCase()));
    const result = {};
    const all = Cards.find({}).fetch();
    for (const c of all) {
        if (c.archived) continue;
        const oid = (c.formOcoid || '').toUpperCase();
        if (!targets.has(oid)) continue;
        if (!result[oid]) result[oid] = c._id;
    }
    return result;
}
"""


# ── Per-form upload + timing ──────────────────────────────────────────────

async def _upload_with_timing(
    page, oid: str, card_id: str, xlsx_path: str,
    token: str | None,
) -> dict:
    """Click minicard → set_input_files → wait for radio → REST verify.
    Returns a result dict with per-stage timings (seconds, float)."""
    r: dict = {
        "oid": oid, "card_id": card_id, "xlsx": xlsx_path,
        "t_click": None, "t_panel": None, "t_setinput": None,
        "t_radio": None, "t_total": None,
        "radio_seen": False, "version_in_oc": None, "error": None,
    }
    t0 = time.monotonic()

    try:
        # Dismiss any stale panel from the previous iteration so its
        # overlay can't intercept our click.
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        except Exception:
            pass

        # Locate minicard by href ending with /{card_id} — robust
        # across slug differences.
        mcl = page.locator(f'.js-minicard[href$="/{card_id}"]').first
        await mcl.scroll_into_view_if_needed(timeout=5000)
        await mcl.click()
        r["t_click"] = time.monotonic() - t0

        # Wait for the upload input the panel renders.
        await page.wait_for_selector(
            "input.js-design-form-input", timeout=15_000,
        )
        r["t_panel"] = time.monotonic() - t0

        # Set the file — time the set_input_files call alone.
        t_si = time.monotonic()
        await page.set_input_files(
            "input.js-design-form-input", xlsx_path,
        )
        r["t_setinput"] = time.monotonic() - t_si

        # Wait for the post-upload radio (attached state — same
        # semantics as the production publisher).
        t_radio = time.monotonic()
        try:
            await page.wait_for_selector(
                "input[type=radio]",
                state="attached",
                timeout=RADIO_TIMEOUT_MS,
            )
            r["radio_seen"] = True
        except Exception as e:
            r["error"] = f"radio timeout: {type(e).__name__}"
        r["t_radio"] = time.monotonic() - t_radio

        # REST verification — sleep briefly to let OC surface the
        # version on the public board API, then probe.
        if token:
            await page.wait_for_timeout(2000)
            try:
                r["version_in_oc"] = await _verify_version_via_rest(
                    token, oid,
                )
            except Exception as e:
                r["version_in_oc"] = f"rest error: {e}"

    except Exception as e:
        r["error"] = f"{type(e).__name__}: {e}"

    finally:
        # Always close the panel before the next card.
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass
        r["t_total"] = time.monotonic() - t0

    return r


# ── Per-form result rendering ─────────────────────────────────────────────

def _fmt_t(v) -> str:
    if v is None:
        return "—"
    return f"{v:.1f}s"


def _print_result(r: dict) -> None:
    print(f"  click+scroll: {_fmt_t(r['t_click'])}", flush=True)
    print(f"  panel open:   {_fmt_t(r['t_panel'])}", flush=True)
    print(f"  set_input:    "
          f"{r['t_setinput']:.2f}s" if r["t_setinput"] is not None
          else "  set_input:    —", flush=True)
    if r["radio_seen"]:
        print(f"  radio:        {_fmt_t(r['t_radio'])} ✓", flush=True)
    else:
        print(f"  radio:        {_fmt_t(r['t_radio'])} ✗ TIMEOUT",
              flush=True)
    if r["version_in_oc"] is not None:
        print(f"  in OC REST:   {r['version_in_oc']}", flush=True)
    if r["error"]:
        print(f"  error:        {r['error']}", flush=True)


# ── Public entry: returns a dict, never raises ─────────────────────────────

async def run_test() -> dict:
    """Run the diagnostic and return a structured result dict.

    Suitable for direct HTTP exposure — every failure path is captured
    into the returned dict instead of raising or calling sys.exit, so
    callers always get a clean JSON-serialisable response. Live
    progress is still logged via print() so server tail logs show
    what the diagnostic is doing in real time.

    Shape:
        {
          "ok": bool,
          "summary": {"total": N, "radio_ok": N, "radio_fail": N,
                      "no_card_id": N},
          "results": [<per-form dict from _upload_with_timing>, …],
          "missing_card_ids": [<OID>, …],
          "warnings": [<str>, …],
          "error": <str | None>,   # set when a prerequisite failed
                                    # before any per-form work ran
        }
    """
    out: dict = {
        "ok": False,
        "summary": {"total": 0, "radio_ok": 0, "radio_fail": 0,
                    "no_card_id": 0},
        "results": [],
        "missing_card_ids": [],
        "warnings": [],
        "error": None,
    }

    if not os.path.exists(SESSION_PATH):
        out["error"] = (
            f"session JSON not found at {SESSION_PATH}. Bootstrap via "
            f"test_publisher.py --capture-session, or copy the Railway "
            f"volume file locally."
        )
        return out

    print(f"[test] resolving xlsx files for {len(SLOW_FORMS)} forms",
          flush=True)
    try:
        xlsx_by_oid = _resolve_xlsx_files()
    except DiagnosticError as e:
        out["error"] = str(e)
        return out
    except Exception as e:
        out["error"] = f"xlsx resolution crashed: {type(e).__name__}: {e}"
        return out
    for oid in SLOW_FORMS:
        print(f"  ✓ {oid}: {xlsx_by_oid[oid]}", flush=True)

    token = await _get_oc_token()
    if not token:
        out["warnings"].append(
            "OC_API_USERNAME/OC_API_PASSWORD not set — REST version "
            "verification skipped"
        )
        print(f"[test] note: {out['warnings'][-1]}", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                storage_state=SESSION_PATH,
            )
            page = await context.new_page()
            print(f"[test] navigating to {BOARD_URL}", flush=True)
            try:
                await page.goto(BOARD_URL, wait_until="domcontentloaded")
                await page.wait_for_selector(
                    ".js-minicard", timeout=60_000)
                await page.wait_for_timeout(1500)
            except Exception as e:
                out["error"] = (f"board nav failed: "
                                f"{type(e).__name__}: {e}")
                return out

            # Resolve card_ids via Cards.find() — same pattern the
            # publisher's enumeration uses, just scoped to our 7 OIDs.
            print(f"[test] resolving card_ids via Cards.find()...",
                  flush=True)
            lookup = await page.evaluate(
                CARD_ID_LOOKUP_JS, SLOW_FORMS)
            if isinstance(lookup, dict) and "error" in lookup:
                out["error"] = f"card_id lookup: {lookup['error']}"
                return out
            cards_by_oid: dict[str, str] = lookup or {}
            for oid in SLOW_FORMS:
                cid = cards_by_oid.get(oid, "<MISSING>")
                marker = "✓" if cid != "<MISSING>" else "✗"
                print(f"  {marker} {oid}: {cid}", flush=True)
            out["missing_card_ids"] = [
                o for o in SLOW_FORMS if o not in cards_by_oid
            ]

            for oid in SLOW_FORMS:
                if oid not in cards_by_oid:
                    print(f"\n[test] SKIP {oid}: no card_id found on board",
                          flush=True)
                    out["summary"]["no_card_id"] += 1
                    continue
                print(f"\n[test] ── {oid} ──", flush=True)
                result = await _upload_with_timing(
                    page, oid,
                    cards_by_oid[oid],
                    xlsx_by_oid[oid],
                    token,
                )
                out["results"].append(result)
                _print_result(result)
        finally:
            await browser.close()

    # ── Summary ────────────────────────────────────────────────────────
    out["summary"]["total"] = len(out["results"])
    out["summary"]["radio_ok"] = sum(
        1 for r in out["results"] if r["radio_seen"])
    out["summary"]["radio_fail"] = sum(
        1 for r in out["results"] if not r["radio_seen"])
    out["ok"] = (out["summary"]["radio_fail"] == 0
                 and out["summary"]["no_card_id"] == 0)

    print("\n" + "=" * 72, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 72, flush=True)
    print(f"{'OID':<8} {'radio':<6} {'t_radio':>9} {'t_total':>9} "
          f"{'in_oc':<10} error", flush=True)
    print("─" * 72, flush=True)
    for r in out["results"]:
        radio = "OK" if r["radio_seen"] else "FAIL"
        in_oc = str(r["version_in_oc"]) \
                if r["version_in_oc"] is not None else "skipped"
        err = (r["error"] or "")[:50]
        print(f"{r['oid']:<8} {radio:<6} {_fmt_t(r['t_radio']):>9} "
              f"{_fmt_t(r['t_total']):>9} {in_oc:<10} {err}",
              flush=True)

    return out


# ── CLI wrapper around run_test() ─────────────────────────────────────────

async def run() -> int:
    """CLI entry point. Delegates to run_test() and returns an exit code
    based on the dict's `ok` flag."""
    result = await run_test()
    if result.get("error"):
        print(f"ERROR: {result['error']}", file=sys.stderr)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
