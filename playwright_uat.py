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
    # Do NOT include crfOid or enketoOpen — those cause the outer JSP to send
    # a postMessage to study-runner-ui to auto-open the form, but in Playwright
    # that postMessage is not received. Instead we navigate to the plain
    # participant page and click Edit <form> directly to let Angular handle it.
    return (f"{base}/ParticipantDetailsPage?"
            f"participantOid={subject_oid}")


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
    Read visible constraint error messages from Enketo.
    Only reads CONSTRAINT errors (.invalid-constraint), not required-field
    validation (.invalid-required) — Enketo marks all unfilled required
    fields as .invalid-required by default before any user interaction,
    which would cause all constraint tests to falsely report errors.
    Constraint errors only appear after the field value triggers a
    constraint violation.
    """
    msgs = []
    # Only read constraint errors — not required-field default invalid state
    selectors = [
        ".invalid-constraint .constraint-message",
        ".question.invalid-constraint",
        ".alert-danger",
        ".errorMessage",
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


async def _fill_and_save(frame, field_name: str, form_oid: str):
    """
    For leave_blank: click Submit in Enketo to trigger required-field validation.
    Must operate on the Enketo iframe frame object, not the outer page.
    Enketo uses .form-footer submit button; OC wraps it with a specific class.
    """
    # Try to click Complete/Submit in the Enketo iframe frame
    for sel in [
        "button:has-text('Complete')",
        "button:has-text('Submit')",
        ".form-footer .btn-primary",
        "button[id*='submit']",
        "button.btn-primary",
    ]:
        try:
            btn = await frame.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await frame.wait_for_timeout(2000)
                return
        except Exception:
            pass



_PW_SEMAPHORE: asyncio.Semaphore = None  # set in run_playwright_uat

async def _test_one_form(
    context,
    fo: str, ev: str, form_rows: list,
    subdomain: str, subject_oid: str,
    study_uuid: str, study_env_uuid: str,
    col_idx: dict, now_str: str,
    fo_titles: dict = None,
) -> tuple:
    """Test one form/event pair. Returns (passed, failed, skipped, row_results).

    row_results is a list of (row_ref, actual, result, date) tuples written
    back to the workbook after all forms complete.
    """
    import asyncio as _asyncio
    async with _PW_SEMAPHORE:
        passed = failed = skipped = 0
        row_results = []
        page = await context.new_page()
        form_page = None  # kept for compatibility but no longer used
        try:
            url = _form_entry_url(subdomain, subject_oid, ev, fo,
                                  study_uuid=study_uuid,
                                  study_env_uuid=study_env_uuid)
            nav_ok = False
            form_frame = None
            form_abbrev = fo.replace("F_", "", 1) if fo.startswith("F_") else fo

            try:
                await page.goto(url, timeout=NAV_TIMEOUT, wait_until="networkidle")
                await page.wait_for_timeout(1000)

                # Find matrix frame (study-runner-ui)
                app_frame = None
                _elapsed = 0.0
                while _elapsed < 30:
                    for _f in page.frames:
                        try:
                            has_edit = await _f.evaluate(
                                "() => !!document.querySelector('[title^=\"Edit \"]')")
                            if has_edit:
                                app_frame = _f
                                n = await _f.evaluate(
                                    "() => document.querySelectorAll('[title^=\"Edit \"]').length")
                                print(f"[pw-uat] {fo}/{ev} matrix ready {_elapsed:.0f}s ({n} edits)", flush=True)
                                break
                        except Exception:
                            pass
                    if app_frame:
                        break
                    await _asyncio.sleep(1.0)
                    _elapsed += 1.0

                if app_frame:
                    # Determine click strategy based on event type.
                    # Repeating events (SE_COMMON etc) use three-dot menu → Edit.
                    # Scheduled events use the form card click ([title="Edit X"]).
                    _is_repeating = 'COMMON' in ev.upper() or ev.upper().startswith('SE_REP')

                    if _is_repeating:
                        # Repeating visit: expand common visit accordion, then
                        # find the form section by abbreviation text, click the
                        # pi-ellipsis-v three-dot button, then click Edit or Add.
                        try:
                            # Expand the accordion if collapsed
                            _n_exp = await app_frame.evaluate("""() => {
                                let n = 0;
                                document.querySelectorAll(
                                    '.p-accordion-toggle-icon.icon-caret-d, ' +
                                    '.p-accordion-header-link[aria-expanded="false"]'
                                ).forEach(el => {
                                    const link = el.closest('.p-accordion-header-link') || el;
                                    link.click(); n++;
                                });
                                return n;
                            }""")
                            if _n_exp:
                                await page.wait_for_timeout(1000)

                            # Find the form-section row containing our abbreviation
                            # OR the renamed board title (after board rename succeeds,
                            # the SE_COMMON accordion shows the full form_title like
                            # "Adverse Events" rather than the OID abbreviation "AE").
                            _fa = form_abbrev
                            _ft = (fo_titles or {}).get(fo, "")
                            _clicked = await app_frame.evaluate(f"""() => {{
                                // Search for abbreviation OR full display title
                                const targets = ['{_fa}', '{_ft}'].filter(Boolean);
                                const walker = document.createTreeWalker(
                                    document.body, NodeFilter.SHOW_TEXT);
                                let node;
                                while (node = walker.nextNode()) {{
                                    const txt = node.textContent.trim();
                                    if (targets.includes(txt)) {{
                                        let el = node.parentElement;
                                        for (let i = 0; i < 10; i++) {{
                                            if (!el) break;
                                            const menu = el.querySelector('.form-menu');
                                            if (menu) {{ menu.click(); return txt; }}
                                            el = el.parentElement;
                                        }}
                                    }}
                                }}
                                return null;
                            }}""")

                            if _clicked:
                                await page.wait_for_timeout(800)
                                # Click Edit or Add — first repeat instance may
                                # show Add (no prior entry); subsequent show Edit.
                                _edit_clicked = await app_frame.evaluate("""() => {
                                    const items = Array.from(
                                        document.querySelectorAll('.p-menuitem-link'));
                                    // Prefer Edit, fall back to Add
                                    const edit = items.find(
                                        i => i.textContent.trim() === 'Edit');
                                    if (edit) { edit.click(); return 'edit'; }
                                    const add = items.find(
                                        i => i.textContent.trim() === 'Add');
                                    if (add) { add.click(); return 'add'; }
                                    return null;
                                }""")
                                if _edit_clicked:
                                    print(f"[pw-uat] {fo}/{ev} three-dot "
                                          f"(label={_clicked!r}) → {_edit_clicked}", flush=True)
                                else:
                                    _menu_items = await app_frame.evaluate("""() =>
                                        Array.from(document.querySelectorAll('.p-menuitem-link'))
                                             .map(i => i.textContent.trim())
                                    """)
                                    print(f"[pw-uat] {fo}/{ev} Edit/Add not in menu: {_menu_items}", flush=True)
                            else:
                                print(f"[pw-uat] {fo}/{ev} form-menu not found for {_fa}", flush=True)
                        except Exception as _ce:
                            print(f"[pw-uat] {fo}/{ev} repeating click failed: {_ce}", flush=True)

                    else:
                        # Scheduled visit: click the form card directly.
                        # The participant page shows all visits; we need to find
                        # the Edit button for our specific form in any visible column.
                        # Primary: exact title match. Fallback: prefix/suffix match.
                        # Second fallback: scroll to the event section first.
                        edit_sel = f'[title="Edit {form_abbrev}"]'
                        # Also try the full display title (e.g. "Edit Date of Visit" for DOV)
                        _form_display_title = (fo_titles or {}).get(fo, "")
                        edit_sel_title = (f'[title="Edit {_form_display_title}"]'
                                          if _form_display_title else None)
                        _clicked_card = False
                        try:
                            # Try abbreviation selector first
                            _found_sel = None
                            for _try_sel in [edit_sel] + ([edit_sel_title] if edit_sel_title else []):
                                try:
                                    await app_frame.wait_for_selector(_try_sel, timeout=3000)
                                    _found_sel = _try_sel
                                    break
                                except Exception:
                                    continue
                            if _found_sel:
                                await page.wait_for_timeout(500)
                                await app_frame.click(_found_sel)
                                await page.wait_for_timeout(500)
                                print(f"[pw-uat] {fo}/{ev} clicked {_found_sel}", flush=True)
                                _clicked_card = True
                            else:
                                raise Exception("no selector matched")
                        except Exception:
                            # Fallback: find any Edit button whose suffix is a
                            # prefix/suffix of our form_abbrev or vice versa.
                            # Covers F_PHQ9 vs card title "PHQ", F_HCU vs "HCU", etc.
                            try:
                                _all_abbrevs = await app_frame.evaluate("""() =>
                                    Array.from(document.querySelectorAll('[title^="Edit "]'))
                                         .map(el => el.getAttribute('title').replace(/^Edit /, ''))
                                """)
                                _fa_upper = form_abbrev.upper()
                                _ft_upper = _form_display_title.upper() if _form_display_title else ""
                                _match = None
                                for _candidate in _all_abbrevs:
                                    _cu = _candidate.upper()
                                    # Exact match against full display title (ICF → "Informed Consent Form")
                                    if _ft_upper and _cu == _ft_upper:
                                        _match = _candidate
                                        break
                                    # Match if one is prefix of the other (PHQ ↔ PHQ9, NRS ↔ NRS Pain Intensity)
                                    if _fa_upper.startswith(_cu) or _cu.startswith(_fa_upper):
                                        _match = _candidate
                                        break
                                if _match:
                                    _fb_sel = f'[title="Edit {_match}"]'
                                    await app_frame.click(_fb_sel)
                                    await page.wait_for_timeout(500)
                                    print(f"[pw-uat] {fo}/{ev} clicked fallback {_fb_sel}", flush=True)
                                    _clicked_card = True
                                else:
                                    # Second fallback: scroll within the frame to find
                                    # our form — the matrix may have the edit button
                                    # outside the viewport.
                                    _scrolled_match = await app_frame.evaluate(f"""() => {{
                                        const all = Array.from(document.querySelectorAll('[title^="Edit "]'));
                                        const fa = '{form_abbrev}'.toUpperCase();
                                        for (const el of all) {{
                                            const t = el.getAttribute('title').replace(/^Edit /, '').toUpperCase();
                                            if (fa.startsWith(t) || t.startsWith(fa)) {{
                                                el.scrollIntoView({{block:'center'}});
                                                el.click();
                                                return el.getAttribute('title');
                                            }}
                                        }}
                                        return null;
                                    }}""")
                                    if _scrolled_match:
                                        await page.wait_for_timeout(500)
                                        print(f"[pw-uat] {fo}/{ev} scrolled+clicked {_scrolled_match}", flush=True)
                                        _clicked_card = True
                                    else:
                                        print(f"[pw-uat] {fo}/{ev} no matching Edit button among {_all_abbrevs}", flush=True)
                            except Exception as _ce2:
                                print(f"[pw-uat] {fo}/{ev} click failed: {_ce2}", flush=True)

                    _form_url = None
                    for _t in range(20):
                        for _f in page.frames:
                            if 'form.' in _f.url and 'openclinica' in _f.url:
                                _form_url = _f.url
                                break
                        if _form_url:
                            break
                        try:
                            inner = await app_frame.evaluate(
                                '() => Array.from(document.querySelectorAll("iframe")).map(f=>f.src)')
                            form_srcs = [s for s in inner if 'form.' in s]
                            if form_srcs:
                                _form_url = form_srcs[0]
                                break
                        except Exception:
                            pass
                        await page.wait_for_timeout(1000)

                    if _form_url:
                        # Use the iframe frame that is ALREADY loaded inside
                        # page — it has the eu.openclinica.io session cookies
                        # via the embed mechanism. Opening _form_url in a new
                        # page navigates to form.openclinica.io with no cookies
                        # → blank form → questions=0. Instead find the frame
                        # by URL in page.frames and use it directly.
                        form_frame = None
                        for _ff in page.frames:
                            if _ff.url == _form_url or (
                                'form.' in _ff.url and 'openclinica' in _ff.url
                            ):
                                form_frame = _ff
                                break
                        if form_frame is None:
                            # Frame not yet in page.frames — wait up to 10s
                            for _ in range(10):
                                await page.wait_for_timeout(1000)
                                for _ff in page.frames:
                                    if ('form.' in _ff.url and 'openclinica' in _ff.url):
                                        form_frame = _ff
                                        break
                                if form_frame:
                                    break
                        if form_frame:
                            # Wait for Enketo to render questions in the frame
                            try:
                                await form_frame.wait_for_selector(".question", timeout=15000)
                            except Exception:
                                pass
                            nav_ok = True
                            try:
                                _q = await form_frame.evaluate(
                                    "() => document.querySelectorAll('.question').length")
                                print(f"[pw-uat] {fo}/{ev} Enketo ready questions={_q}", flush=True)
                            except Exception as _qe:
                                print(f"[pw-uat] {fo}/{ev} question count error: {_qe}", flush=True)
                        else:
                            print(f"[pw-uat] {fo}/{ev} form frame not found in page.frames", flush=True)
                            form_frame = app_frame
                    else:
                        print(f"[pw-uat] {fo}/{ev} form.eu not found", flush=True)
                        form_frame = app_frame

                nav_ok = True
            except Exception as e:
                print(f"[pw-uat] {fo}/{ev} nav failed: {e}", flush=True)

            for row, row_dict, test_type in form_rows:
                uid  = str(row_dict.get("UAT Case ID") or "")
                lv   = str(row_dict.get("Load_Value") or "").strip()
                exp  = str(row_dict.get("Expected Result") or "").strip()
                item = str(row_dict.get("Item_OID") or "").strip()
                field_name = item.split("_")[-1] if "_" in item else item
                actual = result = ""

                if not nav_ok:
                    row_results.append((row, "Navigation failed", "Fail", now_str))
                    failed += 1
                    continue

                try:
                    if test_type == "leave_blank":
                        frame = form_frame or page
                        await _fill_and_save(frame, field_name, fo)
                        errors = await _read_field_errors(frame, field_name)
                        if errors:
                            actual = f"Error: {errors[0][:120]}"
                            result = "Pass"; passed += 1
                        else:
                            actual = "No required-field error shown"
                            result = "Fail"; failed += 1
                        # Reload the frame's page to reset form state
                        try:
                            if form_frame and form_frame != app_frame:
                                _fp = form_frame.page
                                if callable(_fp):
                                    _fp = _fp()
                                if _fp and not _fp.is_closed():
                                    await _fp.reload(wait_until="domcontentloaded")
                                    try:
                                        await form_frame.wait_for_selector(".question", timeout=8000)
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    elif test_type == "constraint":
                        frame = form_frame or page
                        errors = await _read_field_errors(frame, field_name)
                        expect_error = any(x in exp for x in
                            ["Constraint fires", "constraint", "error shown", "does not save"])
                        if expect_error:
                            if errors:
                                actual = f"Constraint: {errors[0][:120]}"
                                result = "Pass"; passed += 1
                            else:
                                actual = "No constraint shown — expected one"
                                result = "Fail"; failed += 1
                        else:
                            if not errors:
                                actual = "No constraint shown — correct"
                                result = "Pass"; passed += 1
                            else:
                                actual = f"Unexpected constraint: {errors[0][:120]}"
                                result = "Fail"; failed += 1

                    elif test_type == "visibility":
                        frame = form_frame or page
                        visible = await _is_field_visible(frame, field_name, fo)
                        expect_visible = "VISIBLE" in exp.upper()
                        if visible is None:
                            actual = f"Field {field_name} not found in DOM"
                            result = "Fail"; failed += 1
                        elif visible == expect_visible:
                            actual = f"Field {'visible' if visible else 'hidden'} — correct"
                            result = "Pass"; passed += 1
                        else:
                            actual = (f"Field {'visible' if visible else 'hidden'} "
                                      f"— expected {'visible' if expect_visible else 'hidden'}")
                            result = "Fail"; failed += 1
                    else:
                        skipped += 1
                        continue

                    row_results.append((row, actual, result, now_str))

                except Exception as e:
                    print(f"[pw-uat] {uid} error: {e}", flush=True)
                    row_results.append((row, f"Error: {str(e)[:100]}", "Fail", now_str))
                    failed += 1

        finally:
            # form_page removed — form_frame is now the iframe inside page
            await page.close()

        return passed, failed, skipped, row_results

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
    fo_titles: dict = None,
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
        # Match a real Chrome user-agent — OC server JSP conditionally renders
        # participants-details-page iframe based on browser detection.
        # Playwright's default headless UA may be treated as non-browser.
        _ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        if has_session:
            # Saved session has cookies for build app (cust1.build.openclinica.io)
            # which the Angular SPA needs to render.
            context = await browser.new_context(
                storage_state=session_path, user_agent=_ua)
            # Do NOT inject JSESSIONID from ODM import — it's a service-account
            # session that causes the server to render different HTML (no
            # participants-details-page iframe). The saved session already has
            # a valid browser JSESSIONID from the user's authentication.
            print(f"[pw-uat] using saved session (session-only, no JSESSIONID override)", flush=True)
        elif jsessionid:
            context = await browser.new_context(user_agent=_ua)
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
            context = await browser.new_context(user_agent=_ua)
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
            # Log localStorage keys to diagnose jhi-idtoken presence
            ls_keys = await warm_page.evaluate(
                "() => Object.keys(localStorage).join(',')")
            print(f"[pw-uat] build app ready: {warm_page.url} | localStorage keys: {ls_keys}", flush=True)
            # Detect Keycloak redirect — session expired during the run.
            # If warm_page landed on auth.openclinica.io, jhi-idtoken is gone
            # and every test will fail with no useful signal. Bail out early
            # so the operator knows to re-authenticate before the next run.
            if "auth.openclinica.io" in warm_page.url or "jhi-idtoken" not in ls_keys:
                print(
                    "[pw-uat] Session expired (landed on Keycloak or jhi-idtoken missing) "
                    "— skipping Playwright tests. Re-authenticate via the auth link.",
                    flush=True,
                )
                await warm_page.close()
                return dvs_bytes  # return unchanged — no Pass/Fail recorded
            # Also check eu domain localStorage via the main participant page later
            eu_base = _legacy_base(subdomain).replace("/OpenClinica","")
            print(f"[pw-uat] eu base: {eu_base}", flush=True)
        except Exception as _we:
            print(f"[pw-uat] build app warmup warning: {_we}", flush=True)
        finally:
            await warm_page.close()

        # Run all forms in parallel — each gets its own page, capped at 4 concurrent.
        global _PW_SEMAPHORE
        _PW_SEMAPHORE = asyncio.Semaphore(4)

        print(f"[pw-uat] running {len(by_form)} form(s) in parallel (max 4 concurrent)", flush=True)
        tasks = [
            _test_one_form(
                context, fo, ev, form_rows,
                subdomain, subject_oid,
                study_uuid, study_env_uuid,
                col_idx, now_str,
                fo_titles=fo_titles,
            )
            for (fo, ev), form_rows in by_form.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception):
                print(f"[pw-uat] form task error: {res}", flush=True)
                continue
            p, f, s, row_results = res
            passed += p; failed += f; skipped += s
            for row, actual, result, date in row_results:
                row[col_idx["Actual Result"] - 1].value = actual
                row[col_idx["Test Result"] - 1].value = result
                row[col_idx["Status"] - 1].value = result
                row[col_idx["Execution Date"] - 1].value = date
                row[col_idx["Notes"] - 1].value = "Playwright"

        await browser.close()

    print(f"[pw-uat] Done — Pass={passed} Fail={failed} Skip={skipped}", flush=True)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
