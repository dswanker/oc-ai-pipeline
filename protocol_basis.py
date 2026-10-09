"""Protocol basis: the protocol defines the forms, so a form the protocol never asks for is not built.

Runs after standards matching and the protocol completeness check (protocol_forms.py), on the final forms. Every
form must have a protocol basis: either a verbatim protocol text that requires collecting its data (one validated AI
call: the protocol plus a compact list of the forms with their fields; every quote is verified against the protocol
text) or an assessment of the completeness check that already maps to it.

Not checked: forms the pipeline's own rules or a customer convention answer require. A form the completeness
check added is checked like every other form, and the assessment it was created for does not vouch for it.

A form without a basis
  content from the protocol analysis or CDASHIG   removed, with its event placements and stored check proposals;
                                                  listed in the Study Specification under "Forms not built". When
                                                  another form's logic reads one of its fields it is kept and flagged.
  content from a customer standard                kept and flagged: the customer's own standard indicates they
                                                  collect it operationally.

Forms change only on a fresh protocol analysis; for a reused or edited specification what the check would do is
recorded and flagged, never applied. When the AI call fails, or its answer cannot be trusted, nothing is removed.

State: study_meta.protocol_basis. Kill switch PROTOCOL_BASIS_CHECK=0. Study-agnostic.
"""
from __future__ import annotations
import hashlib
import json
import os
import re

import protocol_forms as pf

VERSION = 1
FLAG = "protocol_basis"
MAX_FIELDS = 30
MAX_QUOTES = 3
REASON = "no protocol text asks for this data"
KIND_STANDARD, KIND_CDASH, KIND_ANALYSIS = "customer standard", "CDASHIG", "protocol analysis"
REMOVED, KEPT, KEPT_STANDARD, KEPT_REFERENCED, KEPT_NOT_JUDGED, WOULD_REMOVE = (
    "removed", "kept", "kept: customer standard form", "kept: referenced by another form", "kept: not judged",
    "kept: reused specification")
NOT_FRESH = ("this Study Specification was reused, not freshly analysed (a reused or edited specification keeps its "
             "forms); the form is not built at the next fresh protocol analysis")

PROMPT = """You find, for each case report FORM listed below, the text of a clinical trial PROTOCOL that is about the
data the form holds, and you say what kind of statement that text is.

The protocol defines the forms: a form is built only when the protocol requires the site to record, collect,
report, perform or document the data the form holds. You do not decide that; you supply the evidence.

For every form give up to three passages of the protocol that are closest to requiring the data of the form's
FIELDS (judge by the fields, not by the title), and classify each passage by what the passage itself states:
__KINDS__
Rules:
1. Classify the passage as written. A passage is "record" only when its own words instruct recording, collecting,
   reporting, documenting or performing. Do not upgrade a passage because recording would be usual practice, or
   because the thing it describes takes place in the study.
2. "fields": the names of the form's fields whose data the "record" / "schedule" passages ask for; empty when
   there is no such passage.
3. Copy each passage VERBATIM (one sentence or one table-row fragment, at most 300 characters). Do not join
   separate passages, do not paraphrase. A passage that is not found in the protocol text is discarded.
4. When the protocol has nothing about a form's data, return it with an empty "quotes" list.
5. Answer for EVERY form in the list, once, using the form ID exactly as given.

Return ONLY JSON:
{"forms": [{"form_id": "<form ID>",
            "quotes": [{"text": "<verbatim protocol text>", "section": "<section or table>", "kind": "record"}],
            "fields": ["<field name>"], "why": "<one short sentence>"}]}
""".replace("__KINDS__", pf.KIND_TEXT)
KINDS_REQUIRE, KINDS = pf.KINDS_REQUIRE, pf.KINDS
KIND_WORDS = dict(pf.KIND_WORDS, record="the passage asks for none of the form's fields",
                  schedule="the passage asks for none of the form's fields")


def enabled():
    return os.environ.get("PROTOCOL_BASIS_CHECK", "1") != "0"


def _log(msg):
    print(f"[protocol-basis] {msg}", flush=True)


def state(spec):
    sm = (spec or {}).get("study_meta") if isinstance(spec, dict) else None
    return (sm or {}).get("protocol_basis") or {} if isinstance(sm, dict) else {}


def _forms(spec):
    return [f for f in (spec or {}).get("forms") or [] if isinstance(f, dict) and f.get("form_id")]


def fingerprint(spec, protocol_text):
    h = hashlib.sha256(pf._squash(protocol_text).encode()).hexdigest()
    return hashlib.sha256(json.dumps([VERSION, h, sorted(str(f["form_id"]) for f in _forms(spec))]).encode()).hexdigest()


def needs_check(spec, protocol_text):
    if not enabled() or not isinstance(spec, dict) or not str(protocol_text or "").strip() or not _forms(spec):
        return False
    return state(spec).get("fingerprint") != fingerprint(spec, protocol_text)


# ── Which forms are checked ──────────────────────────────────────────────────────

def source_kind(form):
    """Where the form's content comes from: a customer standard (OC4 standard or Customer CRF standards), CDASHIG,
    or the protocol analysis."""
    pr, cr = form.get("protocol_required") or {}, form.get("convention_required") or {}
    src = str(pr.get("content_source") or cr.get("content_source") or "")
    if form.get("customer_standard") or src.startswith(pf.SRC_STANDARD) or src == pf.SRC_CRF:
        return KIND_STANDARD
    lm = form.get("library_match") or {}
    st = f"{lm.get('source_type') or ''} {lm.get('status') or ''}".upper()
    if "CUSTOMER" in st or "LIBRARY" in st:
        return KIND_STANDARD
    return KIND_CDASH if ("CDASH" in st or src == pf.SRC_CDASH) else KIND_ANALYSIS


def exempt_reason(form, squashed, required_ids=(), answers=None):
    """Why a form is not checked, or "": required by a pipeline rule or by a customer convention answer. A form
    the completeness check added is checked like every other form."""
    fid = str(form.get("form_id") or "")
    if fid in set(required_ids or ()):
        return "required by a pipeline rule"
    if form.get("convention_required"):
        return f"customer convention {(form['convention_required'] or {}).get('convention') or ''}".strip()
    ans = answers or {}
    try:
        import form_conventions as fc
        if (ans.get("SAE_FORM") or {}).get("value") == "yes" and fc.is_sae_only(form.get("form_title")):
            return "customer convention SAE_FORM"
    except Exception:
        pass
    if (ans.get("DEATH_DETAILS_FORM") or {}).get("value") == pf.DEATH_ALWAYS and (
            "DD" in pf._form_domains(form) or fid.upper() == "DD"):
        return "customer convention DEATH_DETAILS_FORM"
    return ""


def candidates(spec, protocol_text, required_ids=(), answers=None):
    squashed = pf._squash(protocol_text)
    return [f for f in _forms(spec) if not exempt_reason(f, squashed, required_ids, answers)]


# ── The validated call ───────────────────────────────────────────────────────────

def _field_lines(form):
    import standards_match as sm
    out = []
    for r in form.get("survey") or []:
        if not isinstance(r, dict) or not r.get("name") or not sm.is_data_row(r):
            continue
        if str(r.get("type") or "").strip().lower().startswith(("calculate", "note")):
            continue
        label = re.sub(r"\s+", " ", str(r.get("label") or "")).strip()[:90]
        out.append(f"{r['name']}: {label}" if label else str(r["name"]))
    more = len(out) - MAX_FIELDS
    return out[:MAX_FIELDS] + ([f"(and {more} more)"] if more > 0 else [])


def build_request(spec, protocol_text, required_ids=(), answers=None, with_text=True, max_chars=600_000):
    """(prompt, extra_text) or None when there is nothing to check or no protocol text to verify quotes against.
    with_text=False when the caller passes the protocol itself (PDF) alongside."""
    if not str(protocol_text or "").strip():
        return None
    cands = candidates(spec, protocol_text, required_ids, answers)
    if not cands:
        return None
    blocks = []
    for f in cands:
        blocks.append(f"FORM {f['form_id']} | {f.get('form_title') or ''}\n  fields: " + "; ".join(_field_lines(f) or ["(none)"]))
    extra = "FORMS (form ID | title, then its fields as name: question):\n" + "\n".join(blocks)
    if with_text:
        extra += "\n\nPROTOCOL TEXT:\n" + str(protocol_text)[:max_chars]
    return PROMPT, extra


def validate_response(spec, response_text, protocol_text, required_ids=(), answers=None):
    """{"ok": bool, "forms": {form_id: {"basis", "quotes" (verified: text, section, kind), "unverified", "fields",
    "why"}}, "rejected"}. basis is derived here, not taken from the model: "required" when a verified passage is an
    instruction to record or a schedule row AND names a field of the form; else "mentioned" / "none".
    ok is False when the response is unusable (nothing is then removed)."""
    data = pf._parse(response_text)
    items = (data or {}).get("forms") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {"ok": False, "forms": {}, "rejected": {"unparseable response": 1}}
    squashed = pf._squash(protocol_text)
    cands = candidates(spec, protocol_text, required_ids, answers)
    ids = {str(f["form_id"]) for f in cands}
    fields = {str(f["form_id"]): {str(r.get("name")) for r in f.get("survey") or [] if isinstance(r, dict) and r.get("name")}
              for f in cands}
    # The eligibility form (CDASH domain IE) exists to record the protocol's inclusion/exclusion criteria, so a verified
    # eligibility passage is its basis. For every other form an eligibility criterion is never a basis.
    ie_ids = {str(f["form_id"]) for f in cands if str(f.get("cdash_domain") or "").strip().upper() == "IE"}
    out, rejected = {}, {}

    def rej(why):
        rejected[why] = rejected.get(why, 0) + 1

    for it in items:
        fid = str(it.get("form_id") or "").strip() if isinstance(it, dict) else ""
        if fid not in ids:
            rej("unknown form id")
            continue
        if fid in out:
            rej("duplicate")
            continue
        quotes, unverified = [], 0
        for q in (it.get("quotes") if isinstance(it.get("quotes"), list) else [])[:MAX_QUOTES]:
            text = str(q.get("text") or "").strip() if isinstance(q, dict) else ""
            if not text:
                continue
            if not pf.quote_in(text, squashed):
                unverified += 1
                rej("quote not found in the protocol")
                continue
            kind = str(q.get("kind") or "").strip().lower()
            if kind not in KINDS:
                rej("unknown kind of passage")
                kind = "other"
            quotes.append({"text": text[:400], "section": str(q.get("section") or "").strip()[:60], "kind": kind})
        names = fields.get(fid) or set()
        asked = [str(x).strip() for x in (it.get("fields") if isinstance(it.get("fields"), list) else []) if str(x).strip() in names]
        requiring = [q for q in quotes if q["kind"] in KINDS_REQUIRE]
        if fid in ie_ids:
            eligibility = [q for q in quotes if q["kind"] == "eligibility"]
            if eligibility and not requiring:
                out[fid] = {"basis": "required", "quotes": eligibility, "unverified": unverified, "fields": asked[:20],
                            "why": "the eligibility form records the protocol's inclusion/exclusion criteria"}
                continue
        if requiring and not asked:
            # the passage exists but asks for none of this form's fields: the protocol mentions it, no more
            rej("requirement without a field of the form")
            requiring = []
        basis = "required" if requiring else "mentioned" if quotes else "none"
        out[fid] = {"basis": basis, "quotes": requiring or quotes, "unverified": unverified, "fields": asked[:20],
                    "why": str(it.get("why") or "").strip()[:200]}
    return {"ok": bool(out), "forms": out, "rejected": rejected}


# ── References and removal ───────────────────────────────────────────────────────

def _texts(row):
    return " ".join(str(v) for k, v in row.items() if isinstance(v, str) and k not in ("label", "hint", "flag_reason"))


def referenced_by(spec, form):
    """IDs of the other forms whose logic reads a field of this form."""
    fid = str(form.get("form_id"))
    rx = re.compile(rf"(?<![A-Za-z0-9_])F_{re.escape(fid)}(?![A-Za-z0-9_])")
    out = []
    for f in _forms(spec):
        if f is form:
            continue
        hit = any(isinstance(d, dict) and str(d.get("source_form") or "") in (fid, f"F_{fid}")
                  for d in f.get("cross_form_dependencies") or [])
        hit = hit or any(isinstance(r, dict) and rx.search(_texts(r)) for r in f.get("survey") or [])
        if hit:
            out.append(str(f["form_id"]))
    for p in _proposals(spec):
        if str(p.get("source_form") or "") == fid and str(p.get("target_form") or fid) != fid:
            out.append(str(p["target_form"]))
    return list(dict.fromkeys(out))


def _proposals(spec):
    aec = ((spec.get("study_meta") or {}).get("ai_edit_checks") or {})
    return [p for p in aec.get("proposals") or [] if isinstance(p, dict)]


def events_with_forms(spec):
    """event OID -> form ids placed there (visits_assigned and the schedule's form placements)."""
    out = {}
    for f in _forms(spec):
        for v in f.get("visits_assigned") or []:
            out.setdefault(str(v), set()).add(str(f["form_id"]))
    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict):
        for p in soe.get("form_placements") or []:
            if isinstance(p, dict) and p.get("form_id") and p.get("target_visit_oid"):
                out.setdefault(str(p["target_visit_oid"]), set()).add(str(p["form_id"]))
    return out


def remove_form(spec, form):
    """Take the form out of the spec: the form, its event placements, stored check proposals on it and its entries
    in the matching record. The form itself is kept in study_meta.protocol_basis_removed."""
    fid = str(form.get("form_id"))
    spec["forms"] = [f for f in spec.get("forms") or [] if f is not form]
    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict) and isinstance(soe.get("form_placements"), list):
        soe["form_placements"] = [p for p in soe["form_placements"] if not (isinstance(p, dict) and str(p.get("form_id")) == fid)]
    meta = spec.setdefault("study_meta", {})
    aec = meta.get("ai_edit_checks")
    if isinstance(aec, dict) and isinstance(aec.get("proposals"), list):
        aec["proposals"] = [p for p in aec["proposals"] if not (isinstance(p, dict) and fid in (str(p.get("target_form")), str(p.get("source_form"))))]
    sms = meta.get("standards_match")
    if isinstance(sms, dict) and isinstance(sms.get("protocol_forms_without_standard"), list):
        sms["protocol_forms_without_standard"] = [x for x in sms["protocol_forms_without_standard"] if x != fid]
    if isinstance(spec.get("standards_originals"), dict):
        spec["standards_originals"].pop(fid, None)
    meta.setdefault("protocol_basis_removed", {})[fid] = form


# ── Apply ────────────────────────────────────────────────────────────────────────

def _flag(spec, msg):
    bucket = spec.setdefault("review_flags", {}).setdefault(FLAG, [])
    if msg not in bucket:
        bucket.append(msg)


def _not_run(spec, protocol_text, note):
    st = dict(state(spec))
    st.update(version=VERSION, status="not_run", note=note)
    st.setdefault("forms", [])
    st.setdefault("removed", [])
    spec.setdefault("study_meta", {})["protocol_basis"] = st
    _log(f"check did not run ({note}); no form removed")
    return st


def apply(spec, response_text, protocol_text, fresh=False, required_ids=(), answers=None):
    """Judge every form and act on it. Mutates spec; returns the state. Nothing is removed when the response is
    unusable or leaves most forms without a basis (an answer that cannot be trusted)."""
    squashed = pf._squash(protocol_text)
    v = validate_response(spec, response_text, protocol_text, required_ids, answers)
    if not v["ok"]:
        return _not_run(spec, protocol_text, "the AI answer could not be read")
    # an assessment of the completeness check supports the form it maps to when its own passage is a requirement
    # (an instruction to record or a schedule row); the assessment a form was created for does not vouch for it
    mapped = {}
    for r in pf.state(spec).get("assessments") or []:
        if r.get("form") and not r.get("added") and r.get("kind", "record") in KINDS_REQUIRE:
            mapped.setdefault(str(r["form"]), r)
    records, unsupported = [], []
    for f in _forms(spec):
        fid = str(f["form_id"])
        rec = {"form_id": fid, "form_title": f.get("form_title") or "", "content_source": source_kind(f),
               "supported": True, "basis": "", "section": "", "quote": "", "quotes": [], "action": KEPT, "reason": ""}
        ex = exempt_reason(f, squashed, required_ids, answers)
        if ex:
            pr = f.get("protocol_required") or {}
            rec.update(basis=ex, checked=False, section=pr.get("section") or "", quote=pr.get("quote") or "")
            records.append(rec)
            continue
        rec["checked"] = True
        a, m = v["forms"].get(fid), mapped.get(fid)
        if a and a["basis"] == "required" and a["quotes"]:
            q = a["quotes"][0]
            rec.update(basis=f"verified protocol quote ({'instruction to record' if q['kind'] == 'record' else 'Schedule of Activities row'})",
                       section=q["section"], quote=q["text"], quotes=a["quotes"], fields=a["fields"], why=a["why"])
        elif m:
            rec.update(basis=f"assessment of the completeness check (\"{m.get('assessment')}\")",
                       section=m.get("section") or "", quote=m.get("quote") or "")
        elif a is None:
            rec.update(supported=None, action=KEPT_NOT_JUDGED, reason="the AI answer did not cover this form")
        else:
            q = (a["quotes"] or [{}])[0]
            detail = (f"the protocol only mentions it: {KIND_WORDS.get(q.get('kind'), 'no instruction to record it')}"
                      if a["basis"] == "mentioned" else
                      "the quoted text is not in the protocol" if a["unverified"] else "nothing in the protocol")
            rec.update(supported=False, reason=f"{REASON} ({detail})", mention=q.get("text") or "",
                       section=q.get("section") or "", why=a["why"])
            unsupported.append((f, rec))
        records.append(rec)
    checked = [r for r in records if r.get("checked")]
    if len(unsupported) >= 4 and len(unsupported) * 2 > len(checked):
        return _not_run(spec, protocol_text, f"the AI answer left {len(unsupported)} of {len(checked)} forms without "
                                              f"a protocol basis, which is not credible")
    before = events_with_forms(spec)
    removable = [(f, rec) for f, rec in unsupported if rec["content_source"] != KIND_STANDARD]
    for f, rec in unsupported:
        if rec["content_source"] == KIND_STANDARD:
            rec.update(action=KEPT_STANDARD, reason="customer standard form, no protocol text found")
    # a form another form's logic reads stays (unless every form that reads it goes too)
    changed = True
    while changed:
        changed = False
        going = {rec["form_id"] for _, rec in removable}
        for f, rec in list(removable):
            readers = [x for x in referenced_by(spec, f) if x not in going]
            if readers:
                rec.update(action=KEPT_REFERENCED, referenced_by=readers,
                           reason=f"{rec['reason']}; kept because {', '.join(readers)} reads its fields")
                removable.remove((f, rec))
                changed = True
    for f, rec in removable:
        if fresh:
            remove_form(spec, f)
            rec["action"] = REMOVED
            for r in pf.state(spec).get("assessments") or []:
                if r.get("added") and str(r.get("form")) == rec["form_id"]:
                    r.update(added=False, form=None, basis="form not built (protocol basis check)")
        else:
            rec.update(action=WOULD_REMOVE, reason=f"{rec['reason']}; {NOT_FRESH}")
    for r in records:
        if r["action"] == REMOVED:
            msg = f"{r['form_id']} ({r['form_title']}): not built, {r['reason']}"
        elif r["action"] == WOULD_REMOVE:
            msg = f"{r['form_id']} ({r['form_title']}): {r['reason']}"
        elif r["action"] == KEPT_STANDARD:
            msg = f"{r['form_id']} ({r['form_title']}): customer standard form, no protocol text found; kept, review with the customer"
        elif r["action"] == KEPT_REFERENCED:
            msg = f"{r['form_id']} ({r['form_title']}): {r['reason']}; review"
        elif r["action"] == KEPT_NOT_JUDGED:
            msg = f"{r['form_id']} ({r['form_title']}): protocol basis not judged ({r['reason']}); kept, review"
        else:
            continue
        _flag(spec, msg)
        _log(msg)
    after = events_with_forms(spec)
    emptied = sorted(e for e, fs in before.items() if fs and not after.get(e))
    for e in emptied:
        msg = f"event {e} has no form left: {', '.join(sorted(before[e]))} was removed and was its only form; review"
        _flag(spec, msg)
        _log(msg)
    old_removed = [r for r in state(spec).get("removed") or [] if isinstance(r, dict)]
    now_removed = [r for r in records if r["action"] == REMOVED]
    gone = {r["form_id"] for r in now_removed}
    present = {str(f["form_id"]) for f in _forms(spec)}
    st = {"version": VERSION, "status": "done", "fresh": bool(fresh), "fingerprint": fingerprint(spec, protocol_text),
          "forms": [r for r in records if r["action"] != REMOVED],
          "removed": [r for r in old_removed if r.get("form_id") not in gone | present] + now_removed,
          "events_emptied": emptied, "rejected": v["rejected"]}
    spec.setdefault("study_meta", {})["protocol_basis"] = st
    return st


# ── Reporting ────────────────────────────────────────────────────────────────────

def not_built(spec):
    """Records of the forms that are not built (removed), and of those a fresh analysis would not build."""
    st = state(spec)
    out = list(st.get("removed") or []) + [r for r in st.get("forms") or [] if r.get("action") == WOULD_REMOVE]
    try:   # forms the duplicate-subject guard of the standards matching did not build
        import standards_global
        out += standards_global.not_built(spec)
    except Exception:
        pass
    return out


def section(spec):
    """(title, note, headers, rows, column weights) for the Study Specification, or None when every form is built."""
    recs = not_built(spec)
    rows = [[r.get("form_id"), r.get("form_title"), r.get("content_source"),
             "Not built" if r.get("action") == REMOVED else "Kept (reused specification)", r.get("reason") or REASON,
             r.get("mention") or "—"] for r in recs]
    # assessments of the completeness check that got no form: their protocol passage is not a requirement
    rows += [["—", r.get("assessment"), "—", "Not built", r.get("reason") or REASON, r.get("quote") or "—"]
             for r in pf.state(spec).get("assessments") or [] if r.get("not_built")]
    if not rows:
        return None
    return ("FORMS NOT BUILT", "the protocol defines the forms: a form the protocol analysis created that no protocol "
            "text asks for is not built", ["Form", "Title", "Content from", "Status", "Reason",
                                           "Where the protocol mentions it"], rows, [2, 4, 3, 3, 8, 8])


def summary_lines(spec):
    st = state(spec)
    if not st:
        return []
    if st.get("status") != "done":
        return [f"Protocol basis check did not run ({st.get('note')}): no form was removed."]
    recs = st.get("forms") or []
    checked = [r for r in recs if r.get("checked")] + list(st.get("removed") or [])
    lines = [f"Protocol basis: {len(checked)} form(s) checked against the protocol; "
             f"{sum(1 for r in recs if r.get('checked') and r.get('supported'))} have a protocol basis, "
             f"{len(st.get('removed') or [])} not built."]
    for r in st.get("removed") or []:
        lines.append(f"  - {r['form_id']} ({r['form_title']}): not built, {r.get('reason')}")
    for r in recs:
        if r.get("action") not in (KEPT, None):
            lines.append(f"  ! {r['form_id']} ({r['form_title']}): {r.get('action')}; {r.get('reason')}")
    for e in st.get("events_emptied") or []:
        lines.append(f"  ! event {e} has no form left")
    return lines
