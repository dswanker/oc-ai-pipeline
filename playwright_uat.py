"""
playwright_uat.py — Playwright-based UAT for UI-observable test cases.

Architecture: ODM phase loads ALL field values (including gate fields,
prerequisites, and test values) into OC. Playwright then just navigates
to the form and reads what OC rendered — no form filling required except
for leave-blank tests which need fill+save to trigger required field errors.

Test types:
  leave_blank  — Playwright fills blank + saves, reads error message
  constraint   — ODM loaded prereq+test value; Playwright reads error indicator
  visibility   — ODM loaded gate field; Playwright checks DOM visibility
"""

import asyncio
import io
import os
import re
import datetime as _dt
from typing import Optional

import openpyxl

SESSION_DIR  = "/data/browser_sessions"
NAV_TIMEOUT  = 30_000   # ms
WAIT_MS      = 2_000    # ms after navigation before reading


def _legacy_base(subdomain: str) -> str:
    """Read bridge_url from customer_uuids.csv — handles eu/us/ap regions."""
    from pathlib import Path as _Path
    import csv as _csv
    csv_path = _Path(__file__).parent / "references" / "customer_uuids.csv"
    if csv_path.exists():
        with open(csv_path, newline="") as _f:
            for _row in _csv.DictReader(_f):
                if _row.get("subdomain", "").lower() == subdomain.lower():
                    bridge = _row.get("bridge_url", "").rstrip("/")
                    if bridge:
                        return bridge
    # Fallback to eu if not in CSV
    return f"https://{subdomain}.eu.openclinica.io/OpenClinica"


def _legacy_host(subdomain: str) -> str:
    """Return just the hostname (no path) for cookie domain setting."""
    base = _legacy_base(subdomain)
    from urllib.parse import urlparse as _up
    return _up(base).netloc  # e.g. cust1.eu.openclinica.io


def _form_entry_url(subdomain: str, subject_oid: str, event_oid: str,
                    form_oid: str, study_uuid: str = "",
                    study_env_uuid: str = "") -> str:
    """
    OC data entry lives on the legacy eu.openclinica.io interface.
    The form renders inside hub.html which needs both eu and build cookies.
    Both are captured when the user authenticates with the legacy tab open.
    """
    base = _legacy_base(subdomain)
    return (f"{base}/ParticipantDetailsPage?"
            f"participantOid={subject_oid}&enketoOpen=true"
            f"&studyEventOid={event_oid}&crfOid={form_oid}")


def _classify_pw_row(row_dict: dict) -> Optional[str]:
    """Return 'leave_blank', 'constraint', 'visibility', or None."""
    lv  = str(row_dict.get("Load_Value") or "").strip()
    exp = str(row_dict.get("Expected Result") or "").strip()
    lv_lower = lv.lower()
    if lv_lower == "(leave blank)":
        return "leave_blank"
    if any(x in exp.upper() for x in ["VISIBLE", "HIDDEN", "RELEVANT"]):
        return "visibility"
    # Constraint: has "then" or expected mentions Constraint/error
    if ("then" in lv_lower or "=" in lv) and any(
            x in exp for x in ["Constraint", "constraint", "error", "Form saves",
                                "Form does not save", "No constraint"]):
        return "constraint"
    return None


async def _read_field_errors(page, field_name: str) -> list[str]:
    """
    Read visible error/constraint messages.
    OC4 Enketo uses .invalid-required, .invalid-constraint, .question.invalid-*
    """
    msgs = []
    # Enketo validation message selectors
    selectors = [
        ".invalid-required .required-message",
        ".invalid-constraint .constraint-message",
        ".question.invalid-required",
        ".question.invalid-constraint",
        "[class*='invalid']",
        ".alert-danger",
        ".errorMessage",
        ".errorRequired",
    ]
    for sel in selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                if await el.is_visible():
                    txt = (await el.inner_text() or "").strip()
                    if txt and len(txt) > 2 and txt not in msgs:
                        msgs.append(txt)
        except Exception:
            pass
    return msgs


async def _is_field_visible(page, field_name: str, form_oid: str) -> Optional[bool]:
    """
    True=visible, False=hidden, None=not found.
    OC4 Enketo renders fields as .question divs with data-name attribute.
    The field container is hidden via CSS display:none when not relevant.
    """
    fn_lower = field_name.lower()
    form_bare = (form_oid.replace("F_", "", 1) if form_oid.startswith("F_") else form_oid).lower()

    # Enketo selectors — field name appears in data-name, name, or id attributes
    candidates = [
        f"[data-name='{field_name}']",
        f"[data-name='{field_name.lower()}']",
        f".question[data-name*='{fn_lower}']",
        f"[name='{field_name}']",
        f"[name='{field_name.lower()}']",
        f"input[name*='{fn_lower}'], select[name*='{fn_lower}'], textarea[name*='{fn_lower}']",
        # OC4 sometimes uses full item OID path
        f"[name*='/{field_name}']",
        f"[data-name*='/{field_name}']",
    ]
    for sel in candidates:
        try:
            el = await page.query_selector(sel)
            if el is not None:
                # Walk up to the .question container which has visibility
                container = await el.evaluate_handle(
                    "el => el.closest('.question') || el.closest('.form-group') || el"
                )
                cel = container.as_element()
                if cel:
                    return await cel.is_visible()
        except Exception:
            pass

    # Last resort: search by label text
    try:
        labels = await page.query_selector_all("label, .question-label")
        for lbl in labels:
            txt = (await lbl.inner_text() or "").strip().upper()
            if field_name.upper() in txt:
                parent = await lbl.evaluate_handle(
                    "el => el.closest('.question') || el.closest('.form-group') || el.parentElement")
                pel = parent.as_element()
                if pel:
                    return await pel.is_visible()
    except Exception:
        pass

    return None


async def _fill_and_save(page, field_name: str, form_oid: str):
    """
    For leave_blank: try to submit/close form without filling required field.
    OC4 Enketo uses a Complete/Submit button.
    """
    # Try to click Complete/Submit without filling the field
    for sel in [
        "button:has-text('Complete')",
        "button:has-text('Submit')",
        "button.btn-primary",
        ".form-footer button[type='submit']",
        "button[id*='submit']",
    ]:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(2000)
                return
        except Exception:
            pass


async def run_playwright_uat(
    dvs_bytes: bytes,
    subdomain: str,
    subject_oid: str,
    user_email: str,
    stamp_map: dict,
    bearer_token: str = "",
    jsessionid: str = "",
    study_uuid: str = "",
    study_env_uuid: str = "",
) -> bytes:
    """
    Run Playwright UAT. ODM has already loaded all field values.
    Playwright navigates to forms and reads UI state.
    Auth priority: jsessionid cookie > saved session file.
    Bearer token NOT used as HTTP header (causes OC logout).
    """
    from playwright.async_api import async_playwright

    session_path = os.path.join(SESSION_DIR, f"{user_email}.json")
    has_session = os.path.exists(session_path)
    if not has_session and not jsessionid:
        print(f"[pw-uat] No session file — skipping. Run full pipeline to create one.", flush=True)
        return dvs_bytes

    wb = openpyxl.load_workbook(io.BytesIO(dvs_bytes))
    ws = wb["UAT_Cases"]
    rows_list = list(ws.iter_rows())

    # Find header
    col_idx = {}
    header_row_num = 0
    for row in rows_list:
        if row and row[0].value == "UAT Case ID":
            col_idx = {str(c.value).strip(): c.column for c in row if c.value}
            header_row_num = row[0].row
            break
    if not col_idx:
        return dvs_bytes

    now_str = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Collect Not Testable rows that have a Playwright test type
    pw_rows = []
    for row in rows_list:
        if row[0].row <= header_row_num:
            continue
        uid = str(row[col_idx["UAT Case ID"] - 1].value or "").strip()
        if not uid:
            continue
        # Skip rows already evaluated by ODM phase (Pass or Fail with a real value)
        tr = str(row[col_idx["Test Result"] - 1].value or "").strip()
        ar = str(row[col_idx["Actual Result"] - 1].value or "").strip()
        if tr in ("Pass", "Fail") and ar not in ("Not Testable via ODM", ""):
            continue
        row_dict = {k: row[v - 1].value for k, v in col_idx.items()}
        test_type = _classify_pw_row(row_dict)
        if test_type:
            pw_rows.append((row, row_dict, test_type))

    print(f"[pw-uat] {len(pw_rows)} rows to test via Playwright", flush=True)
    if not pw_rows:
        return dvs_bytes

    passed = failed = skipped = 0

    async with async_playwright() as p:
        build_base = f"https://{subdomain}.build.openclinica.io"

        browser = await p.chromium.launch(headless=True)
        if has_session:
            # Saved session has cookies for build app (cust1.build.openclinica.io)
            # which the Angular SPA needs to render.
            context = await browser.new_context(storage_state=session_path)
            if jsessionid:
                # Also inject fresh JSESSIONID for legacy eu domain nav
                await context.add_cookies([{
                    "name": "JSESSIONID",
                    "value": jsessionid,
                    "domain": _legacy_host(subdomain),
                    "path": "/OpenClinica",
                    "httpOnly": True,
                    "secure": True,
                }])
            print(f"[pw-uat] using saved session (+ JSESSIONID={bool(jsessionid)})", flush=True)
        elif jsessionid:
            context = await browser.new_context()
            await context.add_cookies([{
                "name": "JSESSIONID",
                "value": jsessionid,
                "domain": f"{subdomain}.eu.openclinica.io",
                "path": "/OpenClinica",
                "httpOnly": True,
                "secure": True,
            }])
            print(f"[pw-uat] JSESSIONID only — SPA may not render (no build session)", flush=True)
        else:
            context = await browser.new_context()
            print(f"[pw-uat] no auth — tests will likely fail", flush=True)
        page = await context.new_page()

        # Group by (form, event) to minimise navigations
        from collections import defaultdict
        by_form = defaultdict(list)
        for row, row_dict, test_type in pw_rows:
            fo = str(row_dict.get("Form_OID") or "").strip()
            ev = str(row_dict.get("Study_Event_OID") or "").strip()
            by_form[(fo, ev)].append((row, row_dict, test_type))

        # PW_FORMS env var: comma-separated form OIDs to test (e.g. "F_DM,F_IE")
        # Leave unset to test all forms. Use for fast iteration during development.
        _pw_forms_filter = os.environ.get("PW_FORMS", "").strip()
        if _pw_forms_filter:
            _allowed = {f.strip().upper() for f in _pw_forms_filter.split(",")}
            by_form = {k: v for k, v in by_form.items() if k[0].upper() in _allowed}
            print(f"[pw-uat] PW_FORMS filter active: {_allowed} — {len(by_form)} form/event pairs", flush=True)

        # Warm up build app first — participant matrix on eu page requires
        # build app localStorage to be populated via CrossStorage before it renders.
        print(f"[pw-uat] warming up build app session...", flush=True)
        warm_page = await context.new_page()
        try:
            await warm_page.goto(
                f"{build_base}/#/account-study",
                timeout=30000, wait_until="networkidle"
            )
            await warm_page.wait_for_timeout(3000)
            print(f"[pw-uat] build app ready: {warm_page.url}", flush=True)
        except Exception as _we:
            print(f"[pw-uat] build app warmup warning: {_we}", flush=True)
        finally:
            await warm_page.close()

        for (fo, ev), form_rows in by_form.items():
            print(f"[pw-uat] {fo}/{ev} — {len(form_rows)} rows", flush=True)

            url = _form_entry_url(subdomain, subject_oid, ev, fo,
                                  study_uuid=study_uuid,
                                  study_env_uuid=study_env_uuid)
            nav_ok = False
            form_frame = None
            try:
                await page.goto(url, timeout=NAV_TIMEOUT,
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                actual_url = page.url
                page_title = await page.title()
                print(f"[pw-uat] landed: {actual_url[:120]} title={page_title!r}", flush=True)

                # Form abbreviation: F_DM -> DM, F_AE -> AE etc.
                form_abbrev = fo.replace("F_", "", 1) if fo.startswith("F_") else fo
                form_frame = page  # fallback
                app_frame = None

                # Form cards are in the about:srcdoc iframe (frame[1], ~145KB, same-origin).
                # frame[0] is hub.html (CORS-blocked, CrossStorage bridge only).
                # Poll the srcdoc frame for [title^="Edit {ABBREV}"] to appear.
                _poll_max = 20
                _elapsed = 0.0
                while _elapsed < _poll_max:
                    try:
                        srcdoc_frame = next(
                            (f for f in page.frames if f.url == "about:srcdoc"),
                            None
                        )
                        if srcdoc_frame:
                            has_edit = await srcdoc_frame.evaluate(
                                '() => !!document.querySelector(\'[title^="Edit "]\')')
                            if has_edit:
                                app_frame = srcdoc_frame
                                n = await srcdoc_frame.evaluate(
                                    '() => document.querySelectorAll(\'[title^="Edit "]\').length')
                                print(f"[pw-uat] srcdoc frame ready after {_elapsed:.1f}s ({n} Edit elements)", flush=True)
                                break
                            if _elapsed == 0 or _elapsed % 5 == 0:
                                body_len = await srcdoc_frame.evaluate(
                                    "() => document.body ? document.body.innerHTML.length : 0")
                                print(f"[pw-uat] srcdoc t={_elapsed:.0f}s len={body_len}", flush=True)
                        else:
                            if _elapsed == 0 or _elapsed % 5 == 0:
                                all_frames = [f.url[:60] for f in page.frames]
                                print(f"[pw-uat] t={_elapsed:.0f}s no srcdoc frame yet. frames={all_frames}", flush=True)
                    except Exception as _pe:
                        if _elapsed == 0:
                            print(f"[pw-uat] poll error: {_pe}", flush=True)
                    await asyncio.sleep(1.0)
                    _elapsed += 1.0
                if not app_frame:
                    print(f"[pw-uat] srcdoc frame not ready after {_poll_max}s", flush=True)

                if app_frame:
                    # Click the form card to open the form
                    try:
                        edit_sel = f'[title="Edit {form_abbrev}"]'
                        await app_frame.wait_for_selector(edit_sel, timeout=5000)
                        await app_frame.click(edit_sel)
                        print(f"[pw-uat] clicked {edit_sel}", flush=True)
                        # Wait for Enketo form to render inside the Angular app
                        await app_frame.wait_for_selector(
                            ".question, [data-name], .enketo-form, .form-group",
                            timeout=10000
                        )
                        form_frame = app_frame
                        print(f"[pw-uat] form open in app frame", flush=True)
                    except Exception as _ce:
                        print(f"[pw-uat] form click failed: {_ce}", flush=True)
                        form_frame = app_frame or page
                else:
                    print(f"[pw-uat] Angular app frame not found", flush=True)

                nav_ok = True
            except Exception as e:
                print(f"[pw-uat] nav failed {fo}/{ev}: {e}", flush=True)
                print(f"[pw-uat] attempted URL: {url}", flush=True)

            for row, row_dict, test_type in form_rows:
                uid  = str(row_dict.get("UAT Case ID") or "")
                lv   = str(row_dict.get("Load_Value") or "").strip()
                exp  = str(row_dict.get("Expected Result") or "").strip()
                item = str(row_dict.get("Item_OID") or "").strip()
                field_name = item.split("_")[-1] if "_" in item else item

                if not nav_ok:
                    row[col_idx["Actual Result"] - 1].value = "Navigation failed"
                    row[col_idx["Test Result"] - 1].value = "Fail"
                    row[col_idx["Status"] - 1].value = "Fail"
                    failed += 1
                    continue

                try:
                    if test_type == "leave_blank":
                        # Playwright: clear field, save, read error
                        frame = form_frame or page
                        await _fill_and_save(frame, field_name, fo)
                        errors = await _read_field_errors(frame, field_name)
                        if errors:
                            actual = f"Error: {errors[0][:120]}"
                            result = "Pass"
                            passed += 1
                        else:
                            actual = "No required-field error shown"
                            result = "Fail"
                            failed += 1
                        # Re-navigate to restore form state for next tests
                        await page.goto(url, timeout=NAV_TIMEOUT,
                                        wait_until="domcontentloaded")
                        await page.wait_for_timeout(3000)

                    elif test_type == "constraint":
                        # ODM already loaded prereq + test value.
                        # Just read whether a constraint error is shown.
                        frame = form_frame or page
                        errors = await _read_field_errors(frame, field_name)
                        expect_error = any(x in exp for x in
                            ["Constraint fires", "constraint", "error shown",
                             "does not save"])
                        if expect_error:
                            if errors:
                                actual = f"Constraint: {errors[0][:120]}"
                                result = "Pass"
                                passed += 1
                            else:
                                actual = "No constraint shown — expected one"
                                result = "Fail"
                                failed += 1
                        else:
                            # Happy path — no error expected
                            if not errors:
                                actual = "No constraint shown — correct"
                                result = "Pass"
                                passed += 1
                            else:
                                actual = f"Unexpected constraint: {errors[0][:120]}"
                                result = "Fail"
                                failed += 1

                    elif test_type == "visibility":
                        # ODM already loaded gate field value.
                        # Just read DOM visibility.
                        frame = form_frame or page
                        visible = await _is_field_visible(frame, field_name, fo)
                        expect_visible = "VISIBLE" in exp.upper()
                        if visible is None:
                            actual = f"Field {field_name} not found in DOM"
                            result = "Fail"
                            failed += 1
                        elif visible == expect_visible:
                            actual = f"Field {'visible' if visible else 'hidden'} — correct"
                            result = "Pass"
                            passed += 1
                        else:
                            actual = (f"Field {'visible' if visible else 'hidden'} "
                                      f"— expected {'visible' if expect_visible else 'hidden'}")
                            result = "Fail"
                            failed += 1
                    else:
                        skipped += 1
                        continue

                    row[col_idx["Actual Result"] - 1].value = actual
                    row[col_idx["Test Result"] - 1].value = result
                    row[col_idx["Status"] - 1].value = result
                    row[col_idx["Execution Date"] - 1].value = now_str
                    row[col_idx["Notes"] - 1].value = "Playwright"

                except Exception as e:
                    print(f"[pw-uat] {uid} error: {e}", flush=True)
                    row[col_idx["Actual Result"] - 1].value = f"Error: {str(e)[:100]}"
                    row[col_idx["Test Result"] - 1].value = "Fail"
                    row[col_idx["Status"] - 1].value = "Fail"
                    failed += 1

        await browser.close()

    print(f"[pw-uat] Done — Pass={passed} Fail={failed} Skip={skipped}", flush=True)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
