"""
playwright_uat.py — Playwright-based UAT testing for OC4 TEST environment.

Handles test categories that cannot be tested via ODM:
  - Leave-blank / required field tests
  - Cross-field constraint tests  
  - Visibility / relevance tests

Called from run_uat_loader() after the ODM phase completes.
Updates the existing DVS UAT results XLSX in-place with Playwright results.
"""

import asyncio
import io
import os
import re
from typing import Optional

import openpyxl


SESSION_DIR  = "/data/browser_sessions"
PW_TIMEOUT   = 20_000   # ms — default element wait
FORM_TIMEOUT = 30_000   # ms — page navigation / save


# ── URL helpers ──────────────────────────────────────────────────────────────

def _legacy_base(subdomain: str) -> str:
    return f"https://{subdomain}.eu.openclinica.io/OpenClinica"


def _participant_dashboard_url(subdomain: str, subject_oid: str) -> str:
    base = _legacy_base(subdomain)
    return f"{base}/pages/studySub/studySubjectDashboard?studySubjectOID={subject_oid}"


def _form_entry_url(subdomain: str, subject_oid: str, event_oid: str,
                    form_oid: str, event_repeat: str = "1") -> str:
    """
    Build the data entry URL for a specific form/event/participant.
    OC legacy URL pattern:
      /OpenClinica/DataEntry?studySubjectOID=SS_...&studyEventOID=SE_...
                            &crfOID=F_...&studyEventRepeatKey=1
    """
    base = _legacy_base(subdomain)
    return (f"{base}/DataEntry?"
            f"studySubjectOID={subject_oid}"
            f"&studyEventOID={event_oid}"
            f"&crfOID={form_oid}"
            f"&studyEventRepeatKey={event_repeat}")


# ── Test case parser ──────────────────────────────────────────────────────────

def _classify_pw_row(row_dict: dict) -> Optional[str]:
    """
    Return test type for rows that need Playwright, or None if not applicable.
    Types: 'leave_blank', 'constraint', 'visibility'
    """
    lv  = str(row_dict.get("Load_Value") or "").strip()
    exp = str(row_dict.get("Expected Result") or "").strip()
    sc  = str(row_dict.get("Scenario") or "").strip()

    lv_lower = lv.lower()
    if lv_lower == "(leave blank)":
        return "leave_blank"
    if "then" in lv_lower and "=" in lv:
        return "constraint"
    if any(x in exp.upper() for x in ["VISIBLE", "HIDDEN", "RELEVANT"]):
        return "visibility"
    return None


def _parse_constraint_lv(lv: str) -> tuple[dict, str, str]:
    """
    Parse "FIELD1=val1, then FIELD2=val2" into:
      prereqs: {"FIELD1": "val1"}
      test_field: "FIELD2"
      test_val: "val2"
    """
    prereqs = {}
    test_field = test_val = ""
    parts = re.split(r",\s*then\s+", lv, flags=re.IGNORECASE)
    for part in parts[:-1]:
        if "=" in part:
            f, v = part.split("=", 1)
            prereqs[f.strip()] = v.strip()
    if parts and "=" in parts[-1]:
        f, v = parts[-1].split("=", 1)
        test_field = f.strip()
        test_val = v.strip()
    return prereqs, test_field, test_val


def _parse_visibility_lv(lv: str) -> tuple[str, str]:
    """
    Parse "GATEFIELD=val" into (gate_field, gate_val).
    """
    if "=" in lv:
        f, v = lv.split("=", 1)
        return f.strip(), v.strip()
    return "", ""


# ── Playwright helpers ────────────────────────────────────────────────────────

async def _fill_field(page, field_name: str, value: str, form_oid: str):
    """
    Fill a field on an OC data entry form.
    OC renders fields as inputs with name or id containing the item OID.
    Tries multiple selector strategies.
    """
    # Build item OID suffix: form_bare + "_" + field_name
    form_bare = form_oid.replace("F_", "", 1) if form_oid.startswith("F_") else form_oid
    item_suffix = f"{form_bare}_{field_name}"

    selectors = [
        f"[name*='{field_name}']",
        f"[id*='{field_name}']",
        f"[name*='{item_suffix}']",
        f"[id*='{item_suffix}']",
    ]

    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                tag = await el.get_attribute("type") or await el.evaluate("el => el.tagName")
                if "select" in str(tag).lower():
                    await el.select_option(value)
                else:
                    await el.fill("")
                    await el.fill(value)
                return True
        except Exception:
            continue
    return False


async def _click_save(page):
    """Click the Save button on an OC data entry form."""
    for sel in ["input[type='submit'][value*='Save']",
                "button:text('Save')",
                "input[value='Save']",
                "#btnSave"]:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                return
        except Exception:
            continue


async def _read_errors(page) -> list[str]:
    """Read visible validation/constraint error messages from the form."""
    msgs = []
    for sel in [".errorRequired", ".errorMessage", ".alert-danger",
                "[class*='error']", ".oc-error", "span.errors"]:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                if await el.is_visible():
                    txt = (await el.inner_text() or "").strip()
                    if txt:
                        msgs.append(txt)
        except Exception:
            continue
    return msgs


async def _is_field_visible(page, field_name: str, form_oid: str) -> Optional[bool]:
    """
    Return True if field is visible, False if hidden, None if not found.
    """
    form_bare = form_oid.replace("F_", "", 1) if form_oid.startswith("F_") else form_oid
    item_suffix = f"{form_bare}_{field_name}"

    for sel in [f"[name*='{field_name}']", f"[id*='{field_name}']",
                f"[name*='{item_suffix}']", f"[id*='{item_suffix}']"]:
        try:
            el = await page.query_selector(sel)
            if el:
                return await el.is_visible()
        except Exception:
            continue
    return None


# ── Main Playwright UAT runner ────────────────────────────────────────────────

async def run_playwright_uat(
    dvs_bytes: bytes,
    subdomain: str,
    subject_oid: str,       # SS_UAT20260_xxxx — the OC internal OID
    user_email: str,
    stamp_map: dict,
) -> bytes:
    """
    Run Playwright-based UAT tests for rows marked 'Not Testable via ODM'.
    Returns updated DVS bytes with Playwright results filled in.

    subject_oid: the OC internal subject OID created by the ODM phase.
    """
    from playwright.async_api import async_playwright

    session_path = os.path.join(SESSION_DIR, f"{user_email}.json")
    if not os.path.exists(session_path):
        print(f"[pw-uat] No session file for {user_email} — skipping Playwright UAT",
              flush=True)
        return dvs_bytes

    # Load DVS and find Not Testable rows
    wb = openpyxl.load_workbook(io.BytesIO(dvs_bytes))
    ws = wb["UAT_Cases"]
    rows_list = list(ws.iter_rows())

    # Find header
    header_row = None
    col_idx = {}
    for row in rows_list:
        if row and row[0].value == "UAT Case ID":
            header_row = row
            col_idx = {str(c.value).strip(): c.column for c in row if c.value}
            break

    if not header_row:
        return dvs_bytes

    import datetime as _dt
    now_str = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Collect rows to test
    pw_rows = []
    for row in rows_list:
        if row[0].row <= header_row[0].row:
            continue
        uid = str(row[col_idx["UAT Case ID"] - 1].value or "").strip()
        if not uid:
            continue
        ar = str(row[col_idx["Actual Result"] - 1].value or "").strip()
        if ar != "Not Testable via ODM":
            continue
        row_dict = {str(header_row[i].value or "").strip(): row[i].value
                    for i in range(len(header_row)) if header_row[i].value}
        test_type = _classify_pw_row(row_dict)
        if test_type:
            pw_rows.append((row, row_dict, test_type))

    print(f"[pw-uat] {len(pw_rows)} rows to test via Playwright", flush=True)
    if not pw_rows:
        return dvs_bytes

    passed = failed = skipped = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=session_path)
        page = await context.new_page()

        # Group rows by form to minimise page navigations
        from collections import defaultdict
        by_form = defaultdict(list)
        for row, row_dict, test_type in pw_rows:
            fo  = str(row_dict.get("Form_OID") or "").strip()
            ev  = str(row_dict.get("Study_Event_OID") or "").strip()
            by_form[(fo, ev)].append((row, row_dict, test_type))

        for (fo, ev), form_rows in by_form.items():
            print(f"[pw-uat] Testing form {fo} event {ev} — {len(form_rows)} rows",
                  flush=True)

            # Navigate to the form
            url = _form_entry_url(subdomain, subject_oid, ev, fo)
            try:
                await page.goto(url, timeout=FORM_TIMEOUT,
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[pw-uat] Navigation failed for {fo}/{ev}: {e}", flush=True)
                for row, _, _ in form_rows:
                    row[col_idx["Actual Result"] - 1].value = f"Navigation failed: {e}"
                    row[col_idx["Test Result"] - 1].value = "Fail"
                    row[col_idx["Status"] - 1].value = "Fail"
                    failed += len(form_rows)
                continue

            for row, row_dict, test_type in form_rows:
                uid     = str(row_dict.get("UAT Case ID") or "")
                lv      = str(row_dict.get("Load_Value") or "").strip()
                exp     = str(row_dict.get("Expected Result") or "").strip()
                item_oid = str(row_dict.get("Item_OID") or "").strip()
                # field name = last segment of item OID e.g. I_AE_AEYN -> AEYN
                field_name = item_oid.split("_")[-1] if "_" in item_oid else item_oid

                try:
                    if test_type == "leave_blank":
                        # Clear the field and attempt save
                        await _fill_field(page, field_name, "", fo)
                        await _click_save(page)
                        await page.wait_for_timeout(1500)
                        errors = await _read_errors(page)
                        if errors:
                            actual = f"Error shown: {errors[0][:100]}"
                            result = "Pass"
                            passed += 1
                        else:
                            actual = "No error shown — form saved"
                            result = "Fail"
                            failed += 1

                    elif test_type == "constraint":
                        prereqs, test_field, test_val = _parse_constraint_lv(lv)
                        # Set prerequisite fields
                        for pf, pv in prereqs.items():
                            await _fill_field(page, pf, pv, fo)
                        # Set the test field
                        await _fill_field(page, test_field or field_name, test_val, fo)
                        await _click_save(page)
                        await page.wait_for_timeout(1500)
                        errors = await _read_errors(page)
                        constraint_expected = "Constraint fires" in exp or "error" in exp.lower()
                        if constraint_expected:
                            if errors:
                                actual = f"Constraint fired: {errors[0][:100]}"
                                result = "Pass"
                                passed += 1
                            else:
                                actual = "No constraint fired — expected one"
                                result = "Fail"
                                failed += 1
                        else:
                            if not errors:
                                actual = "No constraint fired — form saved"
                                result = "Pass"
                                passed += 1
                            else:
                                actual = f"Unexpected constraint: {errors[0][:100]}"
                                result = "Fail"
                                failed += 1

                    elif test_type == "visibility":
                        gate_field, gate_val = _parse_visibility_lv(lv)
                        await _fill_field(page, gate_field, gate_val, fo)
                        await page.wait_for_timeout(1000)
                        visible = await _is_field_visible(page, field_name, fo)
                        expect_visible = "VISIBLE" in exp.upper()
                        if visible is None:
                            actual = f"Field {field_name} not found in DOM"
                            result = "Fail"
                            failed += 1
                        elif visible == expect_visible:
                            actual = f"Field {'visible' if visible else 'hidden'} as expected"
                            result = "Pass"
                            passed += 1
                        else:
                            actual = f"Field {'visible' if visible else 'hidden'} — expected {'visible' if expect_visible else 'hidden'}"
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
                    print(f"[pw-uat] Error on {uid}: {e}", flush=True)
                    row[col_idx["Actual Result"] - 1].value = f"Playwright error: {str(e)[:100]}"
                    row[col_idx["Test Result"] - 1].value = "Fail"
                    row[col_idx["Status"] - 1].value = "Fail"
                    failed += 1

        await browser.close()

    print(f"[pw-uat] Done — Pass={passed} Fail={failed} Skip={skipped}", flush=True)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
