"""
playwright_uat.py — browser (Playwright) UAT for the cases a data import cannot test.

The data import stores values without running form logic, so three kinds of case are run in the real form:

  leave_blank  — the field is emptied, the form validates it, the field's own required message must show
  constraint   — the value under test is entered, the field's own constraint message must (or must not) show
  visibility   — the gate value is set in the form, the field must be shown or hidden

How a case is run (each point was a fault of the previous step, kept as playwright_uat_legacy.py):

  * The form is opened in the visit the case names (Study_Event_OID), not in the first visit that has a form of
    that name. A common-event form is opened from its own table (id = event OID + form OID).
  * A case is scored only on an open form. When the form cannot be opened the case is Blocked with the reason.
    The first participant page is opened alone: several pages opened at once before the legacy session exists
    race on the sign-in state cookie and some land on InvalidStateCookieWarning.
  * The result is read from the field's own question: the invalid-required / invalid-constraint class Enketo
    sets on it and the message it shows. Nothing else on the page counts as an error.
  * A field that holds a loaded value is emptied for the blank test and the value is put back afterwards.
  * A gate value is set in the form by its choice code (radio, checkbox, select or text), the form re-evaluates
    relevance, then the field is read. Every value the test changed is put back.

Result values: Pass, Fail, Blocked (could not be run: reason in Actual Result), Skip.
PW_UAT_ENGINE=legacy runs the previous implementation.
"""

import asyncio
import datetime as _dt
import io
import json
import os
import re
from collections import defaultdict
from typing import Optional

import openpyxl

SESSION_DIR  = os.environ.get("BROWSER_SESSION_DIR", "/data/browser_sessions")
NAV_TIMEOUT  = 45_000   # ms
RESULT_BLOCKED = "Blocked"

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


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


def _participant_url(subdomain: str, subject_oid: str) -> str:
    return f"{_legacy_base(subdomain)}/ParticipantDetailsPage?participantOid={subject_oid}"


# ── Pure helpers (unit-tested without a browser) ──────────────────────────────

def _classify_pw_row(row_dict: dict) -> Optional[str]:
    """Return 'leave_blank', 'constraint', 'visibility', or None."""
    lv  = str(row_dict.get("Load_Value") or "").strip()
    exp = str(row_dict.get("Expected Result") or "").strip()
    if lv.lower() == "(leave blank)":
        return "leave_blank"
    if any(x in exp.upper() for x in ["VISIBLE", "HIDDEN", "RELEVANT"]):
        return "visibility"
    if any(x in exp for x in ["Constraint fires", "Form does not save", "error shown",
                              "No constraint", "Form saves", "constraint"]):
        return "constraint"
    return None


def _field_name(row_dict: dict) -> str:
    """The XLSForm item name of a case: Item_Name when present (a real OID can carry a random suffix such as
    I_DEMOG_DIN_2803), else the OID with its I_<prefix>_ removed."""
    name = str(row_dict.get("Item_Name") or "").strip()
    if name:
        return name
    item = str(row_dict.get("Item_OID") or "").strip()
    if item.count("_") >= 2:
        return item.split("_", 2)[2]
    return item.split("_")[-1] if "_" in item else item


def _parse_gates(load_value: str) -> Optional[list]:
    """[(gate field, value)] from a visibility case's input ("GATE=Y", "A=1, B=2"; "(blank)" is the empty
    value). None when the input is not field=value pairs (the case then needs a manual check)."""
    text = str(load_value or "").strip()
    if not text or "=" not in text:
        return None
    gates = []
    for part in text.split(","):
        m = re.fullmatch(r"\s*(\w+)\s*=\s*(.*?)\s*", part)
        if not m:
            return None
        value = m.group(2).strip().strip("'\"")
        gates.append((m.group(1), "" if value.lower() in ("(blank)", "(leave blank)") else value))
    return gates


def _value_under_test(row_dict: dict) -> Optional[str]:
    """The single value a constraint case enters. A multi-step case enters its Test_Value (its setup was loaded
    and confirmed before the browser step). None when the input is not one plain value."""
    tv = str(row_dict.get("Test_Value") or "").strip()
    if tv and row_dict.get("Setup_Steps"):
        return tv
    lv = str(row_dict.get("Load_Value") or "").strip()
    if not lv or lv.lower() == "(leave blank)" or "then" in lv.lower() or "=" in lv:
        return None
    return lv


def _simple_gate(relevant_expr: str) -> Optional[tuple]:
    """(field, value) when a relevance expression, as Enketo holds it, is one comparison path = 'value'
    (" /data/GRP/GATE ='Y'"). Used to bring a field into view before testing it. None for anything else."""
    m = re.fullmatch(r"\s*\(?\s*(?:\.\./|/)?[\w/.]*?(\w+)\s*=\s*'([^']*)'\s*\)?\s*", str(relevant_expr or ""))
    return (m.group(1), m.group(2)) if m else None


def _judge_required(state: dict) -> tuple:
    """(result, actual) for a blank test, from the field's state after it was emptied and validated."""
    if state.get("required"):
        return "Pass", f"Required message shown: {state.get('message') or '(no text)'}"[:200]
    return "Fail", "No required-field error shown on the field after it was left blank and validated"


def _judge_constraint(expect_error: bool, state: dict, value: str) -> tuple:
    shown = bool(state.get("constraint"))
    msg = (state.get("message") or "(no text)")[:150]
    if expect_error:
        return (("Pass", f"Constraint message shown for {value!r}: {msg}") if shown else
                ("Fail", f"No constraint message shown for {value!r} — expected one"))
    return (("Fail", f"Unexpected constraint message for {value!r}: {msg}") if shown else
            ("Pass", f"Value {value!r} accepted — no constraint message"))


def _judge_visibility(expect_visible: bool, state: dict, gates_text: str) -> tuple:
    """A field OpenClinica keeps on screen only because it still holds a value, flagged "no longer relevant",
    counts as not shown: its rule evaluated to false."""
    shown = bool(state.get("visible")) and not state.get("irrelevant_flag")
    what = ("shown" if shown else
            "not relevant (flagged because it holds a value)" if state.get("irrelevant_flag") else "hidden")
    ok = shown == expect_visible
    return ("Pass" if ok else "Fail",
            f"Field {what} with {gates_text}" + ("" if ok else f" — expected {'shown' if expect_visible else 'hidden'}"))


def _case_participant(row_dict: dict) -> str:
    """A multi-step case is tested on the participant its setup was loaded into; every other browser case on
    UAT-P001 (one form opened once for all its cases)."""
    if row_dict.get("Setup_Steps") and os.environ.get("UAT_SETUP_OWN_PARTICIPANT", "1").strip() != "0":
        return str(row_dict.get("Participant_ID") or "").strip() or "UAT-P001"
    return "UAT-P001"


def _concurrency() -> int:
    try:
        return max(1, min(12, int(os.environ.get("PW_CONCURRENCY", "6"))))
    except ValueError:
        return 6


# Why the last run did or did not test anything, for the loader's log and the Evidence of untested cases.
last_status = {"ran": False, "reason": ""}


class _Blocked(Exception):
    """The case (or the whole form) could not be run; the message is the reason recorded on the case."""


# ── In-page scripts ───────────────────────────────────────────────────────────
# Enketo names a field /data/NAME or /data/GROUP/NAME. The question element carries the validation classes.

_JS_FIND = """
function __find(fn) {
  return Array.prototype.slice.call(document.querySelectorAll('input,select,textarea')).filter(function (e) {
    var n = e.getAttribute('name') || '';
    return n === '/data/' + fn || n.slice(-(fn.length + 1)) === '/' + fn;
  });
}
function __question(e) {
  return e.closest('.question') || e.closest('.calculation') || e.closest('.note') || e.parentElement;
}
function __value(els) {
  var t = (els[0].getAttribute('type') || '').toLowerCase();
  if (t === 'radio' || t === 'checkbox') {
    return els.filter(function (e) { return e.checked; }).map(function (e) { return e.value; }).join(' ');
  }
  if (els[0].tagName === 'SELECT' && els[0].multiple) {
    return Array.prototype.slice.call(els[0].selectedOptions).map(function (o) { return o.value; }).join(' ');
  }
  return els[0].value || '';
}
"""

_JS_STATE = "(fn) => {" + _JS_FIND + """
  var els = __find(fn);
  if (!els.length) return null;
  var e0 = els[0], q = __question(e0), cls = q.classList;
  var visible = q.getClientRects().length > 0 && getComputedStyle(q).visibility !== 'hidden'
                && !q.closest('.or-branch.disabled') && !cls.contains('disabled');
  var msg = '';
  var want = cls.contains('invalid-constraint') ? '.or-constraint-msg' :
             cls.contains('invalid-required') ? '.or-required-msg' :
             cls.contains('invalid-relevant') ? '.or-relevant-msg' : null;
  if (want) {
    var cands = Array.prototype.slice.call(q.querySelectorAll(want));
    var shown = cands.filter(function (m) { return getComputedStyle(m).display !== 'none'; });
    var pick = shown.filter(function (m) { return m.classList.contains('active'); })[0] || shown[0] || cands[0];
    msg = pick ? pick.textContent.replace(/\\s+/g, ' ').trim() : '';
  }
  var type = (e0.getAttribute('type') || e0.tagName).toLowerCase();
  return {
    count: els.length, type: type, value: __value(els), visible: visible,
    required: cls.contains('invalid-required'), constraint: cls.contains('invalid-constraint'),
    irrelevant_flag: cls.contains('invalid-relevant'), message: msg,
    readonly: !!(e0.readOnly || e0.getAttribute('data-calculate') || type === 'hidden'),
    relevant: e0.getAttribute('data-relevant') || (q.closest('.or-branch') ?
              ((q.closest('.or-branch').querySelector('[data-relevant]') || {getAttribute: function () { return ''; }})
               .getAttribute('data-relevant') || '') : ''),
    options: (type === 'radio' || type === 'checkbox') ? els.map(function (e) { return e.value; }) :
             (e0.tagName === 'SELECT' ? Array.prototype.slice.call(e0.options).map(function (o) { return o.value; }) : null)
  };
}"""

# Sets a value the way a user's entry reaches Enketo: the control's value/checked state, then a change event
# (Enketo stores the value, validates the field and re-evaluates relevance on change).
_JS_SET = "([fn, val]) => {" + _JS_FIND + """
  var els = __find(fn);
  if (!els.length) return 'notfound';
  var e0 = els[0], t = (e0.getAttribute('type') || '').toLowerCase();
  if (e0.readOnly || e0.getAttribute('data-calculate') || t === 'hidden') return 'readonly';
  function fire(el, names) { names.forEach(function (n) { el.dispatchEvent(new Event(n, {bubbles: true})); }); }
  if (t === 'radio' || t === 'checkbox') {
    var want = String(val).split(' ').filter(function (x) { return x !== ''; });
    var have = els.map(function (e) { return e.value; });
    if (want.some(function (w) { return have.indexOf(w) < 0; })) return 'nooption';
    var hit = null;
    els.forEach(function (e) {
      var on = want.indexOf(e.value) >= 0;
      if (e.checked !== on) { e.checked = on; hit = e; }
    });
    fire(hit || e0, ['change']);
    return 'ok';
  }
  if (e0.tagName === 'SELECT') {
    var vals = Array.prototype.slice.call(e0.options).map(function (o) { return o.value; });
    if (val !== '' && vals.indexOf(val) < 0) return 'nooption';
    e0.value = val; fire(e0, ['change']);
    return 'ok';
  }
  e0.value = val; fire(e0, ['input', 'change']);
  return 'ok';
}"""

_JS_MARK_FORM_CARD = """([evName, titles, mark]) => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const visits = Array.from(document.querySelectorAll('div.visit'));
  const cardsOf = v => Array.from(v.querySelectorAll('.visit-form-card'));
  const cardTitle = c => norm((c.getAttribute('title') || '').replace(/^Edit\\s+/i, ''));
  const wanted = titles.map(norm).filter(t => t);
  const hasForm = v => cardsOf(v).some(c => wanted.includes(cardTitle(c)));
  let pool = visits, how = 'event';
  if (evName) {
    pool = visits.filter(v => { const h = v.querySelector('.event-name');
                                return h && norm(h.getAttribute('title') || h.textContent) === norm(evName); });
    if (!pool.length) return {status: 'novisit', visits: visits.map(v => {
      const h = v.querySelector('.event-name'); return h ? (h.getAttribute('title') || h.textContent).trim() : ''; })};
  } else {
    pool = visits.filter(hasForm); how = pool.length === 1 ? 'only-visit-with-form' : 'first-visit-with-form';
  }
  for (const v of pool) {
    const card = cardsOf(v).find(c => wanted.includes(cardTitle(c)));
    if (card) { card.scrollIntoView({block: 'center'}); card.setAttribute('data-pwuat', mark);
                return {status: 'ok', how: how}; }
  }
  return {status: 'noform', forms: pool.length ? cardsOf(pool[0]).map(c => (c.getAttribute('title') || '').trim()) : []};
}"""

_JS_EXPAND_ACCORDIONS = """() => {
  const closed = Array.from(document.querySelectorAll('.p-accordion-header-link[aria-expanded="false"]'));
  closed.forEach(a => a.click());
  return closed.length;
}"""

_JS_MARK_COMMON = """([tableId, rowId, mark]) => {
  const table = document.getElementById(tableId);
  if (!table) return {status: 'notable'};
  const row = document.getElementById(rowId) || table.querySelector('tbody tr[id]');
  const menu = row ? row.querySelector('button.form-menu, .form-menu') : null;
  if (menu) { menu.scrollIntoView({block: 'center'}); menu.setAttribute('data-pwuat', mark); return {status: 'menu'}; }
  const add = table.querySelector('button.add-new-button');
  if (add) { add.scrollIntoView({block: 'center'}); add.setAttribute('data-pwuat', mark); return {status: 'add'}; }
  return {status: 'noentry'};
}"""

_JS_CLICK_MENU_EDIT = """() => {
  const items = Array.from(document.querySelectorAll('.p-menuitem-link'));
  const edit = items.find(i => i.textContent.trim() === 'Edit');
  if (edit) { edit.click(); return 'edit'; }
  return items.length ? 'noedit:' + items.map(i => i.textContent.trim()).join('|') : null;
}"""


# ── Browser operations ────────────────────────────────────────────────────────

def _is_form_frame(frame) -> bool:
    return bool(re.match(r"https://form\.[^/]*openclinica\.io/", frame.url or ""))


async def _open_participant(page, url: str):
    """Open the participant page and return its study-runner frame. Retries the sign-in race page; raises
    _Blocked with what the browser actually showed."""
    last = ""
    for attempt in range(3):
        try:
            await page.goto(url, timeout=NAV_TIMEOUT, wait_until="networkidle")
        except Exception as e:
            last = f"navigation error: {str(e)[:120]}"
            continue
        if "InvalidStateCookieWarning" in page.url or "auth.openclinica.io" in page.url:
            last = f"landed on {page.url.split('?')[0].rsplit('/', 1)[-1]}"
            await page.wait_for_timeout(1500 * (attempt + 1))
            continue
        for _ in range(40):
            for f in page.frames:
                if "study-runner-ui" not in (f.url or ""):
                    continue
                try:
                    if await f.evaluate("() => !!document.querySelector('p-accordiontab')"):
                        await page.wait_for_timeout(1200)   # let the visits render
                        return f
                except Exception:
                    pass
            await page.wait_for_timeout(750)
        last = "the participant page did not render its visits within 30s"
    raise _Blocked(f"participant page did not open ({last})")


async def _wait_form_frame(page, timeout_s: float = 30.0):
    """The Enketo form frame once it has rendered its questions, else None."""
    waited = 0.0
    while waited < timeout_s:
        for f in page.frames:
            if _is_form_frame(f) and not f.is_detached():
                try:
                    if await f.evaluate("() => document.querySelectorAll('.question').length") > 0:
                        await page.wait_for_timeout(1500)   # calculations and cross-form values settle
                        return f
                except Exception:
                    pass
        await page.wait_for_timeout(500)
        waited += 0.5
    return None


async def _open_form(page, app, ev: str, fo: str, ev_name: str, form_titles: list, is_common: bool):
    """Open form `fo` in event `ev` and return the Enketo frame. Raises _Blocked with the reason."""
    mark = "t" + os.urandom(4).hex()
    if is_common:
        if await app.evaluate(_JS_EXPAND_ACCORDIONS):
            await page.wait_for_timeout(2000)
        res = await app.evaluate(_JS_MARK_COMMON, [f"{ev}{fo}-table", f"{ev}{fo}", mark])
        status = res.get("status")
        if status == "notable":
            raise _Blocked(f"form {fo} is not listed under event {ev} on the participant page")
        if status == "noentry":
            raise _Blocked(f"no {fo} entry exists for this participant and none could be added")
        await app.click(f'[data-pwuat="{mark}"]')
        if status == "menu":
            clicked = None
            for _ in range(8):
                await page.wait_for_timeout(500)
                clicked = await app.evaluate(_JS_CLICK_MENU_EDIT)
                if clicked == "edit":
                    break
                if clicked is None:          # menu opened empty (page still settling): reopen it
                    await app.click(f'[data-pwuat="{mark}"]')
            if clicked != "edit":
                raise _Blocked(f"the {fo} entry has no Edit action ({clicked or 'menu did not open'})")
    else:
        res = await app.evaluate(_JS_MARK_FORM_CARD, [ev_name, form_titles, mark])
        status = res.get("status")
        if status == "novisit":
            raise _Blocked(f"visit {ev} ({ev_name}) is not scheduled for this participant "
                           f"(no data was loaded into it); visits present: {', '.join(res.get('visits') or []) or 'none'}")
        if status == "noform":
            raise _Blocked(f"form {fo} is not on visit {ev_name or ev} for this participant "
                           f"(forms there: {', '.join(res.get('forms') or []) or 'none'})")
        if res.get("how") == "first-visit-with-form":
            print(f"[pw-uat] {fo}/{ev}: event name unknown, opened the first visit that has the form", flush=True)
        await app.click(f'[data-pwuat="{mark}"]')
    frame = await _wait_form_frame(page)
    if frame is None:
        raise _Blocked(f"form {fo} did not open in the browser within 30s")
    return frame


async def _state(frame, name: str) -> Optional[dict]:
    return await frame.evaluate(_JS_STATE, name)


async def _wait_state(frame, name: str, done, timeout_ms: int = 3000) -> Optional[dict]:
    """Poll the field's state until done(state) or the timeout; returns the last state read."""
    st, waited = None, 0
    while True:
        st = await _state(frame, name)
        if st is not None and done(st):
            return st
        if waited >= timeout_ms:
            return st
        await asyncio.sleep(0.25)
        waited += 250


async def _set(frame, name: str, value: str) -> str:
    return await frame.evaluate(_JS_SET, [name, value])


class _Restore:
    """Values a case changed, put back in reverse order when it ends."""

    def __init__(self, frame):
        self.frame, self.items = frame, []

    async def change(self, name: str, value: str) -> str:
        st = await _state(self.frame, name)
        res = await _set(self.frame, name, value)
        if res == "ok" and st is not None:
            self.items.append((name, st["value"]))
        return res

    async def undo(self):
        for name, value in reversed(self.items):
            try:
                await _set(self.frame, name, value)
                await asyncio.sleep(0.15)
            except Exception:
                pass
        self.items = []


async def _bring_into_view(frame, name: str, restore: _Restore, depth: int = 0) -> dict:
    """The field's state, after setting the one-comparison gates that hide it (up to three levels). Raises
    _Blocked when the field is not in the form or cannot be brought into view."""
    st = await _state(frame, name)
    if st is None:
        raise _Blocked(f"field {name} not found in the published form")
    if st["visible"] and not st["irrelevant_flag"]:
        return st
    gate = _simple_gate(st.get("relevant"))
    if not gate or depth >= 3 or gate[0] == name:
        raise _Blocked(f"field {name} is hidden by its display rule ({(st.get('relevant') or 'unknown').strip()[:90]}) "
                       f"and the rule is not a single field = value the test can set")
    await _bring_into_view(frame, gate[0], restore, depth + 1)
    res = await restore.change(gate[0], gate[1])
    if res != "ok":
        raise _Blocked(f"field {name} is hidden and its gate {gate[0]} could not be set to {gate[1]!r} ({res})")
    st = await _wait_state(frame, name, lambda s: s["visible"])
    if not st or not st["visible"]:
        raise _Blocked(f"field {name} stayed hidden after setting {gate[0]}={gate[1]}")
    return st


async def _run_leave_blank(frame, name: str) -> tuple:
    restore = _Restore(frame)
    try:
        st = await _bring_into_view(frame, name, restore)
        if st["readonly"]:
            raise _Blocked(f"field {name} is read-only or calculated: it cannot be left blank by a user")
        res = await restore.change(name, "")          # empties a loaded value; on an empty field it just validates
        if res != "ok":
            raise _Blocked(f"field {name} could not be emptied ({res})")
        after = await _wait_state(frame, name, lambda s: s["required"], 3000)
        return _judge_required(after or {})
    finally:
        await restore.undo()


async def _run_constraint(frame, name: str, value: Optional[str], expect_error: bool) -> tuple:
    if value is None:
        raise _Blocked("the input is not a single value the browser can enter (multi-step text without "
                       "structured setup, or field=value pairs) — needs a manual check")
    restore = _Restore(frame)
    try:
        st = await _bring_into_view(frame, name, restore)
        if st["readonly"]:
            raise _Blocked(f"field {name} is read-only or calculated: a value cannot be entered")
        res = await restore.change(name, value)
        if res == "nooption":
            raise _Blocked(f"{value!r} is not an option of {name}: it cannot be entered in the form")
        if res != "ok":
            raise _Blocked(f"value {value!r} could not be entered in {name} ({res})")
        if expect_error:
            after = await _wait_state(frame, name, lambda s: s["constraint"], 3500)
        else:
            await asyncio.sleep(1.2)
            after = await _state(frame, name)
        after = after or {}
        if str(after.get("value", "")).strip() != str(value).strip() and not after.get("constraint"):
            raise _Blocked(f"value {value!r} was not kept by field {name} (it shows {after.get('value')!r})")
        return _judge_constraint(expect_error, after, value)
    finally:
        await restore.undo()


async def _run_visibility(frame, name: str, load_value: str, expect_visible: bool) -> tuple:
    gates = _parse_gates(load_value)
    if not gates:
        raise _Blocked("the condition is not given as field=value — needs a manual check")
    if await _state(frame, name) is None:
        raise _Blocked(f"field {name} not found in the published form")
    restore = _Restore(frame)
    try:
        for gate, value in gates:
            gst = await _state(frame, gate)
            if gst is None:
                raise _Blocked(f"gate field {gate} not found in the published form")
            if gst["readonly"]:
                if gst["value"] == value:
                    continue
                raise _Blocked(f"gate {gate} is calculated or read-only (now {gst['value']!r}): the test cannot "
                               f"set it to {value!r} in the form — needs a manual check")
            await _bring_into_view(frame, gate, restore)
            res = await restore.change(gate, value)
            if res == "nooption":
                raise _Blocked(f"{value!r} is not an option of gate {gate} (options: "
                               f"{', '.join((gst.get('options') or [])[:8])})")
            if res != "ok":
                raise _Blocked(f"gate {gate} could not be set to {value!r} ({res})")
        gates_text = ", ".join(f"{g}={v if v != '' else '(blank)'}" for g, v in gates)
        want = (lambda s: s["visible"] and not s["irrelevant_flag"]) if expect_visible else \
               (lambda s: not s["visible"] or s["irrelevant_flag"])
        after = await _wait_state(frame, name, want, 3000)
        return _judge_visibility(expect_visible, after or {}, gates_text)
    finally:
        await restore.undo()


async def _run_case(frame, row_dict: dict, test_type: str) -> tuple:
    """(result, actual) for one case on an open form."""
    name = _field_name(row_dict)
    exp = str(row_dict.get("Expected Result") or "")
    try:
        if test_type == "leave_blank":
            return await _run_leave_blank(frame, name)
        if test_type == "constraint":
            expect_error = any(x in exp for x in ["Constraint fires", "error shown", "does not save"])
            return await _run_constraint(frame, name, _value_under_test(row_dict), expect_error)
        if test_type == "visibility":
            return await _run_visibility(frame, name, str(row_dict.get("Load_Value") or ""),
                                         "VISIBLE" in exp.upper())
    except _Blocked as b:
        return RESULT_BLOCKED, f"Blocked: {b}"
    return RESULT_BLOCKED, "Blocked: unknown test type"


_SEMAPHORE: asyncio.Semaphore = None  # set in run_playwright_uat


async def _test_one_form(context, fo: str, ev: str, form_rows: list, url: str, ev_name: str,
                         form_titles: list, is_common: bool) -> list:
    """Run all cases of one form in one event on one participant. Returns [(row, actual, result)]."""
    async with _SEMAPHORE:
        out = []
        page = await context.new_page()
        try:
            try:
                app = await _open_participant(page, url)
                frame = await _open_form(page, app, ev, fo, ev_name, form_titles, is_common)
                print(f"[pw-uat] {fo}/{ev} form open ({len(form_rows)} case(s))", flush=True)
            except _Blocked as b:
                print(f"[pw-uat] {fo}/{ev} BLOCKED: {b}", flush=True)
                return [(row, f"Blocked: {b}", RESULT_BLOCKED) for row, _d, _t in form_rows]
            except Exception as e:
                print(f"[pw-uat] {fo}/{ev} could not be opened: {e}", flush=True)
                return [(row, f"Blocked: form could not be opened ({str(e)[:140]})", RESULT_BLOCKED)
                        for row, _d, _t in form_rows]
            for row, row_dict, test_type in form_rows:
                uid = str(row_dict.get("UAT Case ID") or "")
                try:
                    if frame.is_detached():
                        frame = await _wait_form_frame(page, 10)
                        if frame is None:
                            raise _Blocked("the form closed during the test")
                    result, actual = await _run_case(frame, row_dict, test_type)
                except _Blocked as b:
                    result, actual = RESULT_BLOCKED, f"Blocked: {b}"
                except Exception as e:
                    result, actual = RESULT_BLOCKED, f"Blocked: browser error ({str(e)[:140]})"
                out.append((row, actual, result))
                if result != "Pass":
                    print(f"[pw-uat] {result.upper()} {uid} {fo}.{_field_name(row_dict)} "
                          f"type={test_type} actual={actual[:110]!r}", flush=True)
            await page.wait_for_timeout(2500)        # let the last restore reach the server
        finally:
            await page.close()
        return out


def _collect_rows(ws, col_idx: dict, header_row_num: int) -> list:
    """[(row, row_dict, test_type)] of the cases the browser runs: not yet scored by the data import, not
    blocked by their setup, and of a kind the browser can test."""
    pw_rows = []
    for row in ws.iter_rows(min_row=header_row_num + 1):
        uid = str(row[col_idx["UAT Case ID"] - 1].value or "").strip()
        if not uid:
            continue
        tr = str(row[col_idx["Test Result"] - 1].value or "").strip()
        ar = str(row[col_idx["Actual Result"] - 1].value or "").strip()
        if tr in ("Pass", "Fail") and ar not in ("Not Testable via ODM", ""):
            continue
        if tr == RESULT_BLOCKED or ar.startswith("Setup failed"):
            continue
        row_dict = {k: row[v - 1].value for k, v in col_idx.items()}
        test_type = _classify_pw_row(row_dict)
        if test_type:
            pw_rows.append((row, row_dict, test_type))
    return pw_rows


def _write(row, col_idx: dict, actual: str, result: str, when: str, note: str = "Playwright") -> None:
    row[col_idx["Actual Result"] - 1].value = actual
    row[col_idx["Test Result"] - 1].value = result
    if "Status" in col_idx:
        row[col_idx["Status"] - 1].value = result
    row[col_idx["Execution Date"] - 1].value = when
    row[col_idx["Notes"] - 1].value = note


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
    ev_titles: dict = None,
    common_events: set = None,
    session_path: str = None,
    _context_hook=None,
) -> bytes:
    """Run the browser cases and write their results into the workbook.

    fo_titles / ev_titles: form OID -> title and event OID -> name as the participant page shows them (the
    loader reads both from the study metadata). common_events: OIDs of common (repeating) events.
    Auth: the saved browser session of user_email only; without it nothing is run.
    """
    if os.environ.get("PW_UAT_ENGINE", "").strip().lower() == "legacy":
        from playwright_uat_legacy import run_playwright_uat as _legacy_run
        print("[pw-uat] PW_UAT_ENGINE=legacy — running the previous browser step", flush=True)
        return await _legacy_run(dvs_bytes, subdomain, subject_oid, user_email, stamp_map,
                                 bearer_token=bearer_token, jsessionid=jsessionid, study_uuid=study_uuid,
                                 study_env_uuid=study_env_uuid, fo_titles=fo_titles)

    from playwright.async_api import async_playwright

    last_status.update(ran=False, reason="")
    session_path = session_path or os.path.join(SESSION_DIR, f"{user_email}.json")
    if not os.path.exists(session_path):
        print("[pw-uat] No session file — skipping. Run full pipeline to create one.", flush=True)
        last_status["reason"] = f"no saved browser login for {user_email}"
        return dvs_bytes

    wb = openpyxl.load_workbook(io.BytesIO(dvs_bytes))
    ws = wb["UAT_Cases"]
    col_idx, header_row_num = {}, 0
    for row in ws.iter_rows(min_row=1, max_row=8):
        if row and row[0].value == "UAT Case ID":
            col_idx = {str(c.value).strip(): c.column for c in row if c.value}
            header_row_num = row[0].row
            break
    if not col_idx:
        return dvs_bytes

    now_str = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pw_rows = _collect_rows(ws, col_idx, header_row_num)
    print(f"[pw-uat] {len(pw_rows)} rows to test via Playwright", flush=True)
    if not pw_rows:
        last_status.update(ran=True, reason="no case needed the browser")
        return dvs_bytes

    fo_titles, ev_titles = dict(fo_titles or {}), dict(ev_titles or {})
    common_events = {str(e).upper() for e in (common_events or set())}

    # F_IE stays skipped by default, as before (PW_TEST_F_IE=1 runs it).
    skip_forms = set() if os.environ.get("PW_TEST_F_IE", "").strip() == "1" else {"F_IE"}
    only = {f.strip().upper() for f in os.environ.get("PW_FORMS", "").split(",") if f.strip()}
    by_form, counts = defaultdict(list), defaultdict(int)
    for row, row_dict, test_type in pw_rows:
        fo = str(row_dict.get("Form_OID") or "").strip()
        ev = str(row_dict.get("Study_Event_OID") or "").strip()
        if only and fo.upper() not in only:
            continue
        if fo.upper() in skip_forms:
            _write(row, col_idx, "Skipped — radio/select field not testable via Playwright", "Skip", now_str)
            counts["Skip"] += 1
            continue
        by_form[(fo, ev, _case_participant(row_dict))].append((row, row_dict, test_type))
    if only:
        print(f"[pw-uat] PW_FORMS filter active: {sorted(only)} — {len(by_form)} form/event pairs", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=session_path, user_agent=_UA,
                                            viewport={"width": 1500, "height": 1000})
        if _context_hook is not None:     # tests serve fixture pages instead of the network
            await _context_hook(context)
        # The participant page needs the build app's sign-in in localStorage.
        warm = await context.new_page()
        try:
            await warm.goto(f"https://{subdomain}.build.openclinica.io/#/account-study",
                            timeout=NAV_TIMEOUT, wait_until="networkidle")
            await warm.wait_for_timeout(1500)
            ls_keys = await warm.evaluate("() => Object.keys(localStorage).join(',')")
            if "auth.openclinica.io" in warm.url or "jhi-idtoken" not in ls_keys:
                print("[pw-uat] Session expired (landed on Keycloak or jhi-idtoken missing) — skipping "
                      "Playwright tests. Re-authenticate via the auth link.", flush=True)
                await browser.close()
                last_status["reason"] = ("the saved browser login has expired (authenticate again with the item's "
                                         "auth link, then rerun: a saved login is usable for about an hour)")
                return dvs_bytes  # unchanged — no results recorded
        except Exception as _we:
            print(f"[pw-uat] build app warmup warning: {_we}", flush=True)
        finally:
            await warm.close()

        def _oid(pid):
            return (stamp_map or {}).get(pid, {}).get("oc_oid") or (subject_oid if pid == "UAT-P001" else "")

        # One participant page alone first: it creates the legacy session the parallel pages then share.
        first_oid = next((o for o in (_oid(pid) for (_f, _e, pid) in by_form) if o), "")
        if first_oid:
            warm2 = await context.new_page()
            try:
                await _open_participant(warm2, _participant_url(subdomain, first_oid))
                print("[pw-uat] legacy session ready", flush=True)
            except _Blocked as b:
                print(f"[pw-uat] warm-up of the participant page failed: {b}", flush=True)
            finally:
                await warm2.close()

        global _SEMAPHORE
        _SEMAPHORE = asyncio.Semaphore(_concurrency())
        print(f"[pw-uat] running {len(by_form)} form(s), at most {_concurrency()} at a time", flush=True)

        async def _one(key, form_rows):
            fo, ev, pid = key
            oid = _oid(pid)
            if not oid:
                return [(row, f"Blocked: participant {pid} was not created", RESULT_BLOCKED)
                        for row, _d, _t in form_rows]
            titles = [t for t in (fo_titles.get(fo), fo_titles.get(fo.upper()),
                                  fo[2:] if fo.upper().startswith("F_") else fo) if t]
            is_common = ev.upper() in common_events if common_events else \
                ("COMMON" in ev.upper() or ev.upper().startswith("SE_REP"))
            return await _test_one_form(context, fo, ev, form_rows, _participant_url(subdomain, oid),
                                        ev_titles.get(ev) or ev_titles.get(ev.upper()) or "", titles, is_common)

        keys = list(by_form.keys())
        results = await asyncio.gather(*[_one(k, by_form[k]) for k in keys], return_exceptions=True)
        for key, res in zip(keys, results):
            if isinstance(res, Exception):
                print(f"[pw-uat] form task error {key[0]}/{key[1]}: {res}", flush=True)
                res = [(row, f"Blocked: browser test error ({str(res)[:160]})", RESULT_BLOCKED)
                       for row, _d, _t in by_form[key]]
            for row, actual, result in res:
                _write(row, col_idx, actual, result, now_str)
                counts[result] += 1
        await browser.close()

    last_status.update(ran=True, reason="")
    print(f"[pw-uat] Done — Pass={counts['Pass']} Fail={counts['Fail']} "
          f"Blocked={counts[RESULT_BLOCKED]} Skip={counts['Skip']}", flush=True)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
