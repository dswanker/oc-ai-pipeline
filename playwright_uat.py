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
    # Constraint: expected result mentions a constraint or error condition.
    # Two sub-cases:
    # (a) multi-step: load value has "then" or "=" (field=value setup) — ODM
    #     loads prereqs, Playwright reads the error indicator
    # (b) plain value: load value is a direct field value (e.g. "-1", "2026-06-17",
    #     "ZZZ_INVALID") — Playwright enters the value, saves, reads the error
    _is_constraint_exp = any(x in exp for x in [
        "Constraint fires", "Form does not save", "error shown",
        "No constraint", "Form saves", "constraint"])
    if _is_constraint_exp:
        return "constraint"
    return None


_read_field_errors_diag_logged = set()

async def _read_field_errors(page, field_name: str) -> list[str]:
    """
    Read visible validation error messages from Enketo.
    Reads both CONSTRAINT errors (.invalid-constraint) and REQUIRED errors
    (.invalid-required) that are visible after a save attempt.
    Enketo shows .invalid-required on all unfilled required fields by default,
    but only makes them visible (not display:none) after a submit attempt.
    """
    msgs = []
    selectors = [
        ".invalid-constraint .constraint-message",
        ".question.invalid-constraint",
        ".invalid-required .required-message",
        ".question.invalid-required .required-message",
        ".alert-danger",
        ".errorMessage",
    ]
    for sel in selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                # Check both visible AND non-zero-size elements
                # OC may render constraint messages with display:block but opacity/color change
                try:
                    visible = await el.is_visible()
                    box = await el.bounding_box()
                    has_size = box and box.get('width', 0) > 0 and box.get('height', 0) > 0
                    if visible or has_size:
                        txt = (await el.inner_text() or "").strip()
                        if txt and len(txt) > 2 and txt not in msgs:
                            msgs.append(txt)
                except Exception:
                    pass
        except Exception:
            pass

    # Also do a full text scan of the form for constraint-like messages
    # OC auto-saves and returns server-side constraint text inline
    if not msgs:
        try:
            found_texts = await page.evaluate("""
                (function() {
                    var texts = [];
                    // Scan ALL text nodes for constraint-like content
                    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    var node;
                    while (node = walker.nextNode()) {
                        var txt = node.textContent.trim();
                        if (txt.length > 5 && txt.length < 200) {
                            var p = node.parentElement;
                            if (!p) continue;
                            var style = window.getComputedStyle(p);
                            // Look for red/orange colored text (constraint indicators)
                            var color = style.color;
                            var isRed = color.includes('rgb(') && (
                                color.startsWith('rgb(2') || color.startsWith('rgb(1') ||
                                color.includes('255, 0') || color.includes('220,') ||
                                color.includes('204,') || color.includes('190,')
                            );
                            // Also check parent chain for error-indicating classes
                            var hasErrorClass = false;
                            var el = p;
                            for (var i = 0; i < 5 && el; i++) {
                                var cn = el.className || '';
                                if (cn.includes('invalid') || cn.includes('constraint') || 
                                    cn.includes('error') || cn.includes('alert') ||
                                    cn.includes('warning') || cn.includes('danger')) {
                                    hasErrorClass = true; break;
                                }
                                el = el.parentElement;
                            }
                            if (isRed || hasErrorClass) {
                                texts.push(txt);
                            }
                        }
                    }
                    return texts.slice(0, 5);
                })()
            """)
            for t in (found_texts or []):
                if t and t not in msgs:
                    msgs.append(t)
        except Exception:
            pass

    # Diagnostic: if no errors found, dump DOM state for the field
    # so we can see what Enketo actually renders after our events
    if not msgs and field_name not in _read_field_errors_diag_logged:
        _read_field_errors_diag_logged.add(field_name)
        try:
            diag = await page.evaluate(f"""
                (function() {{
                    var fn = {repr(field_name)};
                    var flo = fn.toLowerCase();
                    // 1. Find the question container for this field
                    var q = null;
                    var all = document.querySelectorAll('.question');
                    for (var i = 0; i < all.length; i++) {{
                        var dn = all[i].getAttribute('data-name') || '';
                        var inp = all[i].querySelector('input,select,textarea');
                        if (dn.includes(fn) || dn.includes(flo) ||
                            (inp && (inp.name||'').includes(fn))) {{
                            q = all[i]; break;
                        }}
                    }}
                    // 2. Get all classes on .question divs and constraint-related elements
                    var classes = [];
                    document.querySelectorAll('.question').forEach(function(el) {{
                        el.classList.forEach(function(c) {{ if (classes.indexOf(c)<0) classes.push(c); }});
                    }});
                    // 3. Check if .invalid-constraint exists at all (hidden or not)
                    var anyConstraint = document.querySelectorAll('.invalid-constraint').length;
                    var anyRequired = document.querySelectorAll('.invalid-required').length;
                    var constraintMsg = document.querySelectorAll('.constraint-message').length;
                    var reqMsg = document.querySelectorAll('.required-message').length;
                    // 4. Check for OC-specific error classes
                    var alertDanger = document.querySelectorAll('.alert-danger').length;
                    var hasError = document.querySelectorAll('.has-error,.error,.is-invalid').length;
                    return JSON.stringify({{
                        field: fn,
                        questionFound: !!q,
                        questionClasses: q ? Array.from(q.classList) : [],
                        allQuestionClasses: classes.slice(0,30),
                        anyInvalidConstraint: anyConstraint,
                        anyInvalidRequired: anyRequired,
                        constraintMsgEls: constraintMsg,
                        requiredMsgEls: reqMsg,
                        alertDanger: alertDanger,
                        hasError: hasError
                    }});
                }})()
            """)
            print(f"[pw-uat] DOM-DIAG field={field_name}: {diag}", flush=True)
        except Exception as _de:
            print(f"[pw-uat] DOM-DIAG error: {{_de}}", flush=True)

    return msgs


async def _is_field_visible(page, field_name: str, form_oid: str) -> Optional[bool]:
    """
    True=visible, False=hidden, None=not found.
    OC4 Enketo renders fields as .question divs with data-name attribute.
    The field container is hidden via CSS display:none when not relevant.
    """
    fn_lower = field_name.lower()
    form_bare = (form_oid.replace("F_", "", 1) if form_oid.startswith("F_") else form_oid).lower()

    # OC4 Enketo confirmed format: name="/data/FIELDNAME" (flat) or
    # name="/data/GROUPNAME/FIELDNAME" (grouped). Use ends-with selector
    # to match both: input[name$='/FIELDNAME']
    # JS-based lookup — Playwright CSS engine mishandles '/' in attribute values
    async def _js_find_visible(frm, fname):
        return await frm.evaluate(f"""
            (function() {{
                var fn = {repr(fname)};
                var flo = fn.toLowerCase();
                var all = document.querySelectorAll('input,select,textarea');
                for (var i=0; i<all.length; i++) {{
                    var n = all[i].name || '';
                    if (n.endsWith('/'+fn) || n.endsWith('/'+flo) ||
                        n === '/data/'+fn || n === '/data/'+flo) {{
                        var q = all[i].closest('.question') || all[i].closest('.form-group') || all[i];
                        var s = window.getComputedStyle(q);
                        if (s.display === 'none' || s.visibility === 'hidden') return 'hidden';
                        return 'visible';
                    }}
                }}
                return 'notfound';
            }})()
        """)

    candidates = ["__js__"]  # sentinel
    for sel in candidates:
        try:
            el = None  # unused in JS path
            _vis = await _js_find_visible(page, field_name)
            if _vis == 'notfound':
                break
            return _vis == 'visible'
        except Exception:
            pass
    # fallback: original CSS approach (kept for safety)
    for sel in [f"[name='/data/{field_name}']", f"[name='/data/{fn_lower}']"]:
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
    For leave_blank: trigger Enketo required-field validation without navigating.
    Clicking .btn-primary navigates the iframe (kills frame for subsequent tests).
    Instead, dispatch Enketo's internal validate mechanism via JS events.
    """
    try:
        result = await frame.evaluate("""
            (function() {
                // Strategy 1: call enketo's validate() API if exposed
                if (window.form && typeof window.form.validate === 'function') {
                    window.form.validate();
                    return 'enketo-validate';
                }
                // Strategy 2: dispatch 'beforesend' event on the form — enketo
                // listens for this and runs validate() before submission
                var form = document.querySelector('form.or');
                if (form) {
                    // Trigger the checkConstraints flow by dispatching input events
                    // on all empty required fields to mark them as invalid
                    var inputs = form.querySelectorAll('input[data-required], input.required, [required]');
                    inputs.forEach(function(inp) {
                        if (!inp.value) {
                            inp.dispatchEvent(new Event('change', {bubbles: true}));
                            inp.dispatchEvent(new Event('blur', {bubbles: true}));
                        }
                    });
                    // Dispatch validate event that enketo listens for
                    form.dispatchEvent(new CustomEvent('enketo-validate', {bubbles: true}));
                    // Also try the submit button's contained validate logic
                    // by dispatching a 'submit' but with cancelable=true and
                    // preventing the actual navigation
                    var evt = new Event('submit', {bubbles: true, cancelable: true});
                    var prevented = !form.dispatchEvent(evt);
                    return 'form-events:prevented=' + prevented;
                }
                return 'no-form';
            })()
        """)
        await frame.wait_for_timeout(1200)
    except Exception:
        pass


async def _get_live_frame(page, cached_frame):
    """
    Re-acquire the Enketo form frame from page.frames by URL match.
    The cached frame reference goes stale when concurrent workers share
    the same page and navigate different participants. This returns the
    freshest matching frame, falling back to the cached one if not found.
    """
    if cached_frame is None:
        return cached_frame
    cached_url = cached_frame.url
    if not cached_url or 'form.' not in cached_url:
        return cached_frame
    # Find the frame in page.frames that matches the cached URL
    for f in page.frames:
        if f.url == cached_url and not f.is_detached():
            return f
    # If exact URL not found, try any form. frame
    for f in page.frames:
        if 'form.' in f.url and 'openclinica' in f.url and not f.is_detached():
            return f
    return cached_frame


async def _enter_field_value(frame, field_name: str, value: str, form_oid: str):
    """
    Enter a value into a specific field in Enketo and save.
    Used for constraint-fires tests where ODM doesn't pre-load the value —
    Playwright enters the value directly in the browser.
    Handles text inputs, date inputs, select_one (radio/select), and integers.
    """
    fn_lower = field_name.lower()
    # OC4 Enketo: name="/data/FIELDNAME" (flat) or name="/data/GRP/FIELDNAME" (grouped)
    # Use ends-with selector to match both patterns
    # Use JS evaluate for field lookup: Playwright's CSS engine mishandles '/'
    # in attribute values (e.g. name$='/FIELDNAME' silently returns nothing).
    # frame.evaluate() uses the browser's native querySelectorAll which works correctly.
    async def _js_find_field(frm, fname):
        return await frm.evaluate(f"""
            (function() {{
                var fn = {repr(fname)};
                var flo = fn.toLowerCase();
                var all = document.querySelectorAll('input,select,textarea');
                for (var i=0; i<all.length; i++) {{
                    var n = all[i].name || '';
                    if (n.endsWith('/'+fn) || n.endsWith('/'+flo) ||
                        n === '/data/'+fn || n === '/data/'+flo) {{
                        return i;
                    }}
                }}
                return -1;
            }})()
        """)

    async def _js_get_el(frm, fname, idx):
        """Get element handle by its index in document.querySelectorAll result."""
        return await frm.evaluate_handle(f"""
            (function() {{
                var fn = {repr(fname)};
                var flo = fn.toLowerCase();
                var all = document.querySelectorAll('input,select,textarea');
                for (var i=0; i<all.length; i++) {{
                    var n = all[i].name || '';
                    if (n.endsWith('/'+fn) || n.endsWith('/'+flo) ||
                        n === '/data/'+fn || n === '/data/'+flo) {{
                        return all[i];
                    }}
                }}
                return null;
            }})()
        """)

    # Pure-JS field entry — avoids stale ElementHandle issues.
    # All DOM interaction happens inside a single evaluate() call in the live frame.
    try:
        result = await frame.evaluate(f"""
            (function() {{
                var fn = {repr(field_name)};
                var flo = fn.toLowerCase();
                var val = {repr(value)};
                var all = document.querySelectorAll('input,select,textarea');
                var el = null;
                for (var i = 0; i < all.length; i++) {{
                    var n = all[i].name || '';
                    if (n.endsWith('/'+fn) || n.endsWith('/'+flo) ||
                        n === '/data/'+fn || n === '/data/'+flo) {{
                        el = all[i];
                        break;
                    }}
                }}
                if (!el) {{
                    var cnt = all.length;
                    return 'notfound:' + cnt;
                }}
                var tag = el.tagName.toLowerCase();
                var type = (el.getAttribute('type') || '').toLowerCase();
                if (tag === 'select') {{
                    for (var j = 0; j < el.options.length; j++) {{
                        if (el.options[j].value === val || el.options[j].text === val) {{
                            el.options[j].selected = true;
                            el.dispatchEvent(new Event('change', {{bubbles:true}}));
                            return 'ok';
                        }}
                    }}
                    return 'select-nooption';
                }} else if (type === 'radio' || type === 'checkbox') {{
                    // For radio: find the radio with matching value
                    for (var k = 0; k < all.length; k++) {{
                        var rn = all[k].name || '';
                        var rt = (all[k].getAttribute('type') || '').toLowerCase();
                        if ((rn.endsWith('/'+fn) || rn.endsWith('/'+flo) ||
                             rn === '/data/'+fn || rn === '/data/'+flo) &&
                            (rt === 'radio' || rt === 'checkbox') &&
                            all[k].value === val) {{
                            all[k].click();
                            return 'ok';
                        }}
                    }}
                    // Click first matching radio if no value match
                    el.click();
                    return 'ok';
                }} else {{
                    // text / date / number / textarea
                    el.focus();
                    el.value = '';
                    el.value = val;
                    el.dispatchEvent(new Event('input', {{bubbles:true}}));
                    el.dispatchEvent(new Event('change', {{bubbles:true}}));
                    return 'ok';
                }}
            }})()
        """)
        if result == 'ok':
            await frame.wait_for_timeout(400)
            return True
        elif result and result.startswith('notfound:'):
            cnt = result.split(':')[1]
            _url = frame.url
            print(f"[pw-uat] JS field lookup: {field_name} not found, frame has {cnt} inputs, url={_url[:80]}", flush=True)
        # else: select-nooption or other issue — fall through to return False
    except Exception as _efe:
        print(f"[pw-uat] _enter_field_value outer exception for {field_name!r}: {_efe}", flush=True)
    return False



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

                # Find matrix frame (study-runner-ui).
                # Visit-based events: look for [title^="Edit "] buttons.
                # SE_COMMON/repeating events: look for the p-accordion element
                # that holds the common-event section — it renders even when no
                # event instance exists yet (ODM load creates instances, but the
                # accordion is present regardless). Without this distinction,
                # repeating forms time out after 30s because [title^="Edit "]
                # never appears for SE_COMMON (those use three-dot menus).
                _is_repeating_ev = 'COMMON' in ev.upper() or ev.upper().startswith('SE_REP')
                app_frame = None
                _elapsed = 0.0
                while _elapsed < 30:
                    for _f in page.frames:
                        try:
                            if _is_repeating_ev:
                                # SE_COMMON: detect the accordion regardless of
                                # whether any instances are loaded yet.
                                has_matrix = await _f.evaluate(
                                    "() => !!document.querySelector('.p-accordion, [class*=accordion]')")
                            else:
                                has_matrix = await _f.evaluate(
                                    "() => !!document.querySelector('[title^=\"Edit \"]')")
                            if has_matrix:
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

                            # Find the form-section containing our form.
                            # The SE_COMMON page shows sections like:
                            #   "Concomitant Medications" (F_CM)
                            #   "Serious Adverse Event / SADE Report" (F_AESAE)
                            #   "Protocol Deviation" (F_DV)
                            #   "Pregnancy Report" (F_PREG)
                            # These headings don't match form_abbrev ("CM","AESAE") and
                            # may not match fo_titles exactly either (subtitle after " / ").
                            #
                            # Approach: each section is a p-card or similar container with
                            # a heading AND a .form-menu button in the same Actions column.
                            # Find the form-section containing our form.
                            # fo_titles has the exact OC board heading, e.g.:
                            #   F_CM    -> 'Concomitant Medications'
                            #   F_AESAE -> 'Serious Adverse Event / SADE Report'
                            #   F_DV    -> 'Protocol Deviation'
                            #   F_PREG  -> 'Pregnancy Report'
                            #
                            # Strategy: find the element whose text CONTAINS fo_titles,
                            # then walk UP until an ancestor contains a .form-menu.
                            # Walking up from the heading is reliable because the heading
                            # is unique per section; walking up from the menu was failing
                            # because the stop condition fired on the wrong div.
                            _fa = form_abbrev
                            _ft = (fo_titles or {}).get(fo, "")
                            _clicked = await app_frame.evaluate(f"""() => {{
                                const title  = '{_ft}'.toLowerCase();
                                const abbrev = '{_fa}'.toLowerCase();

                                const score = (txt) => {{
                                    const t = txt.toLowerCase();
                                    if (title && t.includes(title)) return 2;
                                    if (abbrev.length >= 5 && t.includes(abbrev)) return 1;
                                    return 0;
                                }};

                                // Find best-matching leaf-ish element (few children, short text)
                                let bestEl = null, bestScore = 0, bestLabel = '';
                                for (const el of document.querySelectorAll('*')) {{
                                    if (el.children.length > 3) continue;
                                    const txt = el.textContent.trim();
                                    if (!txt || txt.length > 120) continue;
                                    const s = score(txt);
                                    if (s > bestScore) {{
                                        bestScore = s; bestEl = el; bestLabel = txt;
                                    }}
                                }}

                                if (!bestEl || bestScore === 0) return null;

                                // Walk UP from matched heading until we reach a container
                                // that has a .form-menu button somewhere inside it.
                                let container = bestEl;
                                for (let i = 0; i < 20; i++) {{
                                    if (!container) break;
                                    const menu = container.querySelector('.form-menu');
                                    if (menu) {{ menu.click(); return bestLabel; }}
                                    container = container.parentElement;
                                }}
                                return null;
                            }}""")

                            if _clicked:
                                # Wait for PrimeNG to render the popup menu.
                                # On early page load, the menu can open with zero items
                                # because PrimeNG hasn't finished mounting the component.
                                # Strategy: check for items; if empty, dismiss the popup
                                # (press Escape) and re-click the menu button. Repeat up
                                # to 6 times (3s total). Re-clicking forces PrimeNG to
                                # re-evaluate menu content after page settles.
                                _edit_clicked = None
                                for _menu_attempt in range(6):
                                    await page.wait_for_timeout(500)
                                    _edit_clicked = await app_frame.evaluate(f"""() => {{
                                        const items = Array.from(
                                            document.querySelectorAll('.p-menuitem-link'));
                                        const edit = items.find(
                                            i => i.textContent.trim() === 'Edit');
                                        if (edit) {{ edit.click(); return 'edit'; }}
                                        const add = items.find(
                                            i => i.textContent.trim() === 'Add');
                                        if (add) {{ add.click(); return 'add'; }}
                                        // Menu is empty — dismiss it and re-find the button
                                        // so the caller can re-click on next iteration.
                                        const open = document.querySelector(
                                            '.p-menu.p-component:not([style*="display: none"]),' +
                                            '.p-tieredmenu, .p-contextmenu');
                                        if (open) {{
                                            // Close by clicking elsewhere
                                            document.body.click();
                                        }}
                                        // Re-click the form-menu for this section
                                        const title = '{_ft}'.toLowerCase();
                                        const abbrev = '{_fa}'.toLowerCase();
                                        const score = (txt) => {{
                                            const t = txt.toLowerCase();
                                            if (title && t.includes(title)) return 2;
                                            if (abbrev.length >= 5 && t.includes(abbrev)) return 1;
                                            return 0;
                                        }};
                                        let bestEl = null, bestScore = 0;
                                        for (const el of document.querySelectorAll('*')) {{
                                            if (el.children.length > 3) continue;
                                            const txt = el.textContent.trim();
                                            if (!txt || txt.length > 120) continue;
                                            const s = score(txt);
                                            if (s > bestScore) {{ bestScore = s; bestEl = el; }}
                                        }}
                                        if (!bestEl || bestScore === 0) return null;
                                        let container = bestEl;
                                        for (let i = 0; i < 20; i++) {{
                                            if (!container) break;
                                            const menu = container.querySelector('.form-menu');
                                            if (menu) {{ menu.click(); return null; }}
                                            container = container.parentElement;
                                        }}
                                        return null;
                                    }}""")
                                    if _edit_clicked:
                                        break
                                # Click Edit or Add — first repeat instance may
                                # show Add (no prior entry); subsequent show Edit.
                                if _edit_clicked:
                                    print(f"[pw-uat] {fo}/{ev} three-dot "
                                          f"(label={_clicked!r}) → {_edit_clicked}", flush=True)
                                else:
                                    _menu_items = await app_frame.evaluate("""() =>
                                        Array.from(document.querySelectorAll('.p-menuitem-link'))
                                             .map(i => i.textContent.trim())
                                    """)
                                    print(f"[pw-uat] {fo}/{ev} Edit/Add not in menu after 3s: {_menu_items}", flush=True)
                                    nav_ok = False  # form didn't open; skip rows
                            else:
                                print(f"[pw-uat] {fo}/{ev} form-menu not found for {_fa}", flush=True)
                                nav_ok = False  # form didn't open; skip rows
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
                                try:
                                    _sample = await form_frame.evaluate("""() => {
                                        // Check inputs with name attr
                                        const byName = Array.from(document.querySelectorAll('input[name],select[name],textarea[name]')).slice(0,6);
                                        // Check .question elements for any attr
                                        const questions = Array.from(document.querySelectorAll('.question')).slice(0,3);
                                        const qAttrs = questions.map(q => ({
                                            id: q.id,
                                            cn: q.className.substring(0,40),
                                            dn: q.getAttribute('data-name'),
                                            n: q.getAttribute('name'),
                                            // first input child
                                            inp: (q.querySelector('input,select,textarea') || {name:'?',type:'?',className:'?'}).name
                                        }));
                                        return {inputs: byName.map(e=>({tag:e.tagName,type:e.type,name:e.name.substring(0,40)})), questions: qAttrs};
                                    }""")
                                    if _sample:
                                        print(f"[pw-uat] {fo}/{ev} DIAG2: {_sample}", flush=True)
                                except Exception as _de:
                                    print(f"[pw-uat] {fo}/{ev} DIAG2 err: {_de}", flush=True)
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

            _visibility_save_done = False  # trigger Enketo relevance re-eval once per form
            for row, row_dict, test_type in form_rows:
                uid  = str(row_dict.get("UAT Case ID") or "")
                lv   = str(row_dict.get("Load_Value") or "").strip()
                exp  = str(row_dict.get("Expected Result") or "").strip()
                item = str(row_dict.get("Item_OID") or "").strip()
                # Extract field name from item OID: "I_DOMAIN_FIELDNAME"
                # Split on "_" with maxsplit=2, take the last part — handles
                # compound field names like I_DEMOG_AGE_DISP → "AGE_DISP"
                if item.count("_") >= 2:
                    field_name = item.split("_", 2)[2]
                elif "_" in item:
                    field_name = item.split("_")[-1]
                else:
                    field_name = item
                actual = result = ""

                if not nav_ok:
                    row_results.append((row, "Navigation failed", "Fail", now_str))
                    failed += 1
                    continue

                try:
                    # Re-acquire live frame at start of every row.
                    # Poll up to 10s (20×0.5s) for a form frame that is both live
                    # AND has Enketo inputs rendered (input_count > 0).
                    # "Frame has 0 inputs" means the frame is attached but Enketo
                    # hasn't finished rendering yet — we must wait for it.
                    if form_frame and form_frame != app_frame:
                        for _retry in range(20):
                            _live = None
                            for _ff in page.frames:
                                if 'form.' in _ff.url and 'openclinica' in _ff.url and not _ff.is_detached():
                                    try:
                                        _count = await _ff.evaluate(
                                            "document.querySelectorAll('input').length"
                                        )
                                        if _count and int(_count) > 0:
                                            _live = _ff
                                    except Exception:
                                        pass
                                    break
                            if _live:
                                form_frame = _live
                                break
                            await asyncio.sleep(0.5)
                        else:
                            print(f"[pw-uat] {fo}/{ev} WARNING: form frame had 0 inputs after 10s", flush=True)
                    frame = form_frame if form_frame else page

                    if test_type == "leave_blank":
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
                                    # Re-acquire form_frame after reload — old reference is detached
                                    try:
                                        for _ff in _fp.frames:
                                            if 'form.' in _ff.url and 'openclinica' in _ff.url:
                                                form_frame = _ff
                                                break
                                        await form_frame.wait_for_selector(".question", timeout=8000)
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    elif test_type == "constraint":
                        # Two sub-cases:
                        # (a) multi-step (lv has "=" or "then"): ODM pre-loaded
                        #     the value; just read the error indicator now.
                        # (b) plain value: enter the value, save, read errors,
                        #     then clear the field to reset form state (faster
                        #     than a full page reload between tests).
                        _lv_is_plain = (
                            "then" not in lv.lower()
                            and "=" not in lv
                            and lv.lower() not in ("(leave blank)", "")
                        )
                        if _lv_is_plain and form_frame:
                            _entered = await _enter_field_value(
                                frame, field_name, lv, fo)
                            if _entered:
                                # Trigger blur to fire Enketo constraint validation
                                # (constraint errors fire on blur, not on submit)
                                try:
                                    await frame.evaluate(f"""
                                        (function() {{
                                            var fn = {repr(field_name)};
                                            var flo = fn.toLowerCase();
                                            var all = document.querySelectorAll('input,select,textarea');
                                            for (var i = 0; i < all.length; i++) {{
                                                var n = all[i].name || '';
                                                if (n.endsWith('/'+fn) || n.endsWith('/'+flo) ||
                                                    n === '/data/'+fn || n === '/data/'+flo) {{
                                                    all[i].focus();
                                                    all[i].dispatchEvent(new Event('input', {{bubbles:true}}));
                                                    all[i].dispatchEvent(new Event('change', {{bubbles:true}}));
                                                    all[i].blur();
                                                    all[i].dispatchEvent(new Event('focusout', {{bubbles:true}}));
                                                    break;
                                                }}
                                            }}
                                        }})()
                                    """)
                                except Exception:
                                    pass
                                await page.wait_for_timeout(3000)
                            else:
                                # Field not found — record and skip rather than
                                # hanging on wait_for_timeout
                                actual = f"Field {field_name} not found in DOM"
                                result = "Fail"; failed += 1
                                row_results.append((row, actual, result, now_str))
                                if result == "Fail":
                                    print(f"[pw-uat] FAIL {uid} {fo}/{item} type={test_type} actual={actual[:60]!r}", flush=True)
                                continue
                        else:
                            # Multi-step constraint: ODM pre-loaded condition value.
                            # Trigger blur on the target field to fire constraint check.
                            try:
                                await frame.evaluate(f"""
                                    (function() {{
                                        var fn = {repr(field_name)};
                                        var flo = fn.toLowerCase();
                                        var all = document.querySelectorAll('input,select,textarea');
                                        for (var i = 0; i < all.length; i++) {{
                                            var n = all[i].name || '';
                                            if (n.endsWith('/'+fn) || n.endsWith('/'+flo) ||
                                                n === '/data/'+fn || n === '/data/'+flo) {{
                                                all[i].focus();
                                                all[i].dispatchEvent(new Event('input', {{bubbles:true}}));
                                                all[i].dispatchEvent(new Event('change', {{bubbles:true}}));
                                                all[i].blur();
                                                all[i].dispatchEvent(new Event('focusout', {{bubbles:true}}));
                                                break;
                                            }}
                                        }}
                                    }})()
                                """)
                            except Exception:
                                pass
                            await page.wait_for_timeout(3000)
                        errors = await _read_field_errors(frame, field_name)
                        expect_error = any(x in exp for x in
                            ["Constraint fires", "error shown", "does not save"])
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
                        # Clear field after plain-value test to reset error state
                        # (avoids full page reload; Enketo clears constraint on empty)
                        if _lv_is_plain and form_frame:
                            try:
                                await _enter_field_value(frame, field_name, "", fo)
                                await page.wait_for_timeout(300)
                            except Exception:
                                pass

                    elif test_type == "visibility":
                        # Enketo relevance formulas re-evaluate after a save attempt.
                        # The ODM has loaded the gate-condition value; trigger submit
                        # once per form so Enketo shows/hides gated fields correctly.
                        # We use a per-form flag to avoid re-submitting every row.
                        if not _visibility_save_done:
                            # Trigger Enketo relevance re-evaluation via change events
                            # on all visible inputs (no submit — that kills the frame)
                            try:
                                await frame.evaluate("""
                                    (function() {
                                        var all = document.querySelectorAll('input,select,textarea');
                                        for (var i = 0; i < all.length; i++) {
                                            all[i].dispatchEvent(new Event('change', {bubbles:true}));
                                        }
                                    })()
                                """)
                            except Exception:
                                pass
                            await page.wait_for_timeout(3000)
                            _visibility_save_done = True
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
                    if result == "Fail":
                        print(f"[pw-uat] FAIL {uid} {fo}/{item} type={test_type} actual={actual[:60]!r}", flush=True)

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

        # Group by (form, event, participant_id) — each participant needs their
        # own navigation since their data lives on a different participant page.
        from collections import defaultdict
        by_form = defaultdict(list)
        # Skip F_IE: radio select_one fields (INCL/EXCL criteria) can't be tested
        # via enter-value approach. Each exclusion criterion times out at 60s,
        # causing a 13-criterion * 7-participant = 91-task tail.
        # Collapse all participants to UAT-P001: constraint logic is identical
        # across participants, so 1 participant per form is sufficient for Playwright.
        _PW_SKIP_FORMS = {"F_IE"}
        _skip_row_results = []
        for row, row_dict, test_type in pw_rows:
            fo  = str(row_dict.get("Form_OID") or "").strip()
            ev  = str(row_dict.get("Study_Event_OID") or "").strip()
            if fo.upper() in _PW_SKIP_FORMS:
                # Write "Skip" result — radio select_one fields not testable via enter-value
                _skip_row_results.append((row, "Skipped — radio/select field not testable via Playwright", "Skip", now_str))
                skipped += 1
                continue
            # Use only UAT-P001 for Playwright: constraint logic is identical
            # across participants; 1 participant per form cuts 7x redundant opens.
            by_form[(fo, ev, "UAT-P001")].append((row, row_dict, test_type))

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
        _PW_SEMAPHORE = asyncio.Semaphore(8)

        print(f"[pw-uat] running {len(by_form)} form(s) in parallel (max 8 concurrent)", flush=True)
        tasks = [
            _test_one_form(
                context, fo, ev, form_rows,
                subdomain,
                # Use the OC OID for this participant from stamp_map;
                # fall back to the global subject_oid for UAT-P001 or unknowns.
                (stamp_map or {}).get(pid, {}).get("oc_oid", subject_oid) or subject_oid,
                study_uuid, study_env_uuid,
                col_idx, now_str,
                fo_titles=fo_titles,
            )
            for (fo, ev, pid), form_rows in by_form.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Write skip results for forms not tested via Playwright
        for row, actual, result, date in _skip_row_results:
            row[col_idx["Actual Result"] - 1].value = actual
            row[col_idx["Test Result"] - 1].value = result
            row[col_idx["Status"] - 1].value = result
            row[col_idx["Execution Date"] - 1].value = date
            row[col_idx["Notes"] - 1].value = "Playwright"

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
