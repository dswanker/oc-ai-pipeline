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
    return f"https://{subdomain}.eu.openclinica.io/OpenClinica"


def _form_entry_url(subdomain: str, subject_oid: str, event_oid: str,
                    form_oid: str, study_uuid: str = "",
                    study_env_uuid: str = "") -> str:
    """
    OC legacy ParticipantDetailsPage with enketoOpen.
    The form renders inline — content is in the about:srcdoc iframe
    which is same-origin and accessible to Playwright.
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
    if not jsessionid and not has_session:
        print(f"[pw-uat] No jsessionid or session file — skipping", flush=True)
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
        browser = await p.chromium.launch(headless=True)
        if jsessionid:
            context = await browser.new_context()
            await context.add_cookies([{
                "name": "JSESSIONID",
                "value": jsessionid,
                "domain": f"{subdomain}.eu.openclinica.io",
                "path": "/OpenClinica",
                "httpOnly": True,
                "secure": True,
            }])
            print(f"[pw-uat] using JSESSIONID cookie auth", flush=True)
        elif has_session:
            context = await browser.new_context(storage_state=session_path)
            print(f"[pw-uat] using saved session for {user_email}", flush=True)
        else:
            context = await browser.new_context()
        page = await context.new_page()

        # Group by (form, event) to minimise navigations
        from collections import defaultdict
        by_form = defaultdict(list)
        for row, row_dict, test_type in pw_rows:
            fo = str(row_dict.get("Form_OID") or "").strip()
            ev = str(row_dict.get("Study_Event_OID") or "").strip()
            by_form[(fo, ev)].append((row, row_dict, test_type))

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
                await page.wait_for_timeout(3000)
                actual_url = page.url
                page_title = await page.title()
                print(f"[pw-uat] landed: {actual_url[:120]} title={page_title!r}", flush=True)

                # OC4 renders forms via Enketo in an iframe
                # Wait for the Enketo iframe to appear
                # Form renders in about:srcdoc iframe (same-origin, accessible)
                # Wait for it to load, then switch to that frame
                await page.wait_for_timeout(3000)
                form_frame = page  # default
                for f_obj in page.frames:
                    if f_obj.url == "about:srcdoc":
                        try:
                            # Verify it has form content
                            body = await f_obj.evaluate("() => document.body.innerHTML.length")
                            if body > 500:
                                form_frame = f_obj
                                print(f"[pw-uat] using srcdoc frame (len={body})", flush=True)
                                break
                        except Exception:
                            pass
                if form_frame is page:
                    print(f"[pw-uat] using main page frame", flush=True)

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
