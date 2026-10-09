"""Customer form conventions: questions whose answer decides which FORMS exist.

Asked like every customer convention question: a column on the AI Hub board whose title starts with "CQ " (or
"CQ_"); the answer is the column's text. An unanswered question takes its default. Applied after standards matching,
on the final forms; both answers are recorded in the Study Specification with their source (customer answer or
default).

  SAE_FORM            "Collect Serious Adverse Events on a separate SAE form?"
                      No  (default)  seriousness, criteria and outcome are captured on the AE form; no SAE form
                      Yes            a separate SAE form (the customer's standard SAE form, else CDASHIG)
  DEATH_DETAILS_FORM  "Collect death details (cause of death, autopsy) on a separate Death Details form?"
                      Only when the protocol asks for them (default) | Always | Never
                      (applied by the protocol completeness check, protocol_forms.py)

State: study_meta.form_conventions. Kill switch FORM_CONVENTIONS=0 (both questions take their defaults and nothing
is changed by SAE_FORM). Study-agnostic.
"""
from __future__ import annotations
import hashlib
import json
import os
import re

FLAG = "customer_form_convention"
SRC_CUSTOMER, SRC_DEFAULT = "customer answer", "default"

QUESTIONS = {
    "SAE_FORM": {
        "question": "Collect Serious Adverse Events on a separate SAE form?",
        "options": {"no": "No - capture seriousness, criteria and outcome on the AE form",
                    "yes": "Yes - add a separate SAE form (date became serious, hospitalization dates, narrative, "
                           "sponsor notification)"},
        "default": "no",
    },
    "DEATH_DETAILS_FORM": {
        "question": "Collect death details (cause of death, autopsy) on a separate Death Details form?",
        "options": {"protocol": "Only when the protocol asks for them", "always": "Always",
                    "never": "Never - death is captured in Disposition and Adverse Events"},
        "default": "protocol",
    },
}

_SAE_TITLE = re.compile(r"\bserious\b|\bsaes?\b", re.I)
_SAE_STRIP = re.compile(r"serious\s+adverse\s+events?|\bsaes?\b", re.I)
_AE_TITLE = re.compile(r"adverse\s+events?|\baes?\b", re.I)
_SERIOUS = re.compile(r"^AESER$|^SAE(YN)?$|^AESAE$|serious", re.I)
_CRITERIA_NAMES = ("AESDTH", "AESLIFE", "AESHOSP", "AESDISAB", "AESCONG", "AESMIE")
_CRITERIA = re.compile(r"AESDTH|AESLIFE|AESHOSP|AESDISAB|AESCONG|AESMIE|AESCRIT|SAECRIT|seriousness criteri|"
                       r"life.threatening|hospitali[sz]ation", re.I)
_OUTCOME = re.compile(r"^AEOUT|outcome", re.I)
_OUT_TERMS = [("RECOVERED/RESOLVED", "Recovered / Resolved"), ("RECOVERING/RESOLVING", "Recovering / Resolving"),
              ("NOT RECOVERED/NOT RESOLVED", "Not Recovered / Not Resolved"),
              ("RECOVERED/RESOLVED WITH SEQUELAE", "Recovered / Resolved with Sequelae"), ("FATAL", "Fatal"),
              ("UNKNOWN", "Unknown")]


def enabled():
    return os.environ.get("FORM_CONVENTIONS", "1") != "0"


def _log(msg):
    print(f"[form-conventions] {msg}", flush=True)


# ── Answers ──────────────────────────────────────────────────────────────────────

def _which(question):
    q = re.sub(r"[^a-z0-9]+", " ", str(question or "").lower()).strip()
    if "death details" in q or "death detail form" in q:
        return "DEATH_DETAILS_FORM"
    if "sae form" in q or (("serious adverse" in q or re.search(r"\bsaes?\b", q)) and "separate" in q):
        return "SAE_FORM"
    return None


def _value(qid, answer):
    a = re.sub(r"[^a-z0-9]+", " ", str(answer or "").lower()).strip()
    if qid == "SAE_FORM":
        if re.match(r"(yes|y|true|separate)\b", a):
            return "yes"
        if re.match(r"(no|n|false)\b", a):
            return "no"
        return None
    if re.match(r"always\b", a):
        return "always"
    if re.match(r"(never|no|n)\b", a):
        return "never"
    if re.match(r"only\b", a) or "protocol" in a:
        return "protocol"
    return None


def answers(customer_conventions=None):
    """{question id: {"question", "value", "answer", "source", "note"}} from the CQ answers; defaults where a
    question is not answered (or the kill switch is set)."""
    out = {}
    for qid, q in QUESTIONS.items():
        out[qid] = {"question": q["question"], "value": q["default"], "answer": q["options"][q["default"]],
                    "source": SRC_DEFAULT, "note": ""}
    if not enabled():
        return out
    for question, answer in (customer_conventions or {}).items():
        qid = _which(question)
        if not qid or not str(answer or "").strip():
            continue
        v = _value(qid, answer)
        if v is None:
            out[qid]["note"] = f"the answer \"{str(answer).strip()[:80]}\" was not understood; the default applies"
        else:
            out[qid].update(value=v, answer=QUESTIONS[qid]["options"][v], source=SRC_CUSTOMER, note="")
    return out


def state(spec):
    sm = (spec or {}).get("study_meta") if isinstance(spec, dict) else None
    return (sm or {}).get("form_conventions") or {} if isinstance(sm, dict) else {}


def _fingerprint(ans):
    return hashlib.sha256(json.dumps({k: v["value"] for k, v in sorted((ans or {}).items())}).encode()).hexdigest()[:16]


def needs_apply(spec, ans):
    return enabled() and isinstance(spec, dict) and state(spec).get("fingerprint") != _fingerprint(ans or answers())


# ── Forms ────────────────────────────────────────────────────────────────────────

def _domains(form):
    import standards_match as sm
    doms = list(sm._protocol_domains(form))
    cs = form.get("customer_standard") or {}
    if cs.get("domain") and cs["domain"] not in doms:
        doms.append(cs["domain"])
    return doms


def is_sae_only(title):
    """A form for serious adverse events alone ("Serious Adverse Events", "SAE Report"), not the AE form itself
    ("Adverse Events / Serious Adverse Events")."""
    t = str(title or "")
    return bool(_SAE_TITLE.search(t)) and not _AE_TITLE.search(_SAE_STRIP.sub(" ", t))


def _rows(form):
    return [r for r in form.get("survey") or [] if isinstance(r, dict) and r.get("name")]


def _has(form, rx):
    for r in _rows(form):
        if rx.search(str(r.get("name") or "")) or rx.search(str(r.get("label") or "")):
            return True
    if rx is _CRITERIA:
        return any(isinstance(c, dict) and rx.search(str(c.get("label") or "")) for c in form.get("choices") or [])
    return False


def ae_form(spec):
    for f in spec.get("forms") or []:
        if isinstance(f, dict) and not is_sae_only(f.get("form_title")) and \
                ("AE" in _domains(f) or str(f.get("form_id") or "").upper() == "AE"):
            return f
    return None


def sae_forms(spec):
    return [f for f in spec.get("forms") or [] if isinstance(f, dict) and is_sae_only(f.get("form_title"))]


def _cdash(var, domain="AE"):
    try:
        import standards_match as sm
        return sm._cdash()[1].get((domain, var)) or {}
    except Exception:
        return {}


def _row(rtype, name, label, group, why):
    return {"type": rtype, "name": name, "label": label, "bind__oc_itemgroup": group, "completion_status": "FLAGGED",
            "library_source": "CDASH_DEFAULT", "flag_reason": why}


def _question(var, fallback, domain="AE"):
    q = str(_cdash(var, domain).get("question") or "")
    return q if q and "[" not in q else fallback


def _ny(form):
    if not any(isinstance(c, dict) and c.get("list_name") == "NY" for c in form.get("choices") or []):
        form.setdefault("choices", []).extend([{"list_name": "NY", "label": "No", "name": "N", "source": "STANDARD"},
                                               {"list_name": "NY", "label": "Yes", "name": "Y", "source": "STANDARD"}])


def _out_list(form):
    if not any(isinstance(c, dict) and c.get("list_name") == "AEOUT" for c in form.get("choices") or []):
        form.setdefault("choices", []).extend({"list_name": "AEOUT", "label": lab, "name": code, "source": "STANDARD"}
                                              for code, lab in _OUT_TERMS)


def _seriousness_rows(form, group, why, with_outcome=True):
    """CDASHIG rows for whatever of seriousness / criteria / outcome the form lacks."""
    rows = []
    if not _has(form, _SERIOUS):
        rows.append(_row("select_one NY", "AESER", _question("AESER", "Was the adverse event serious?"), group, why))
    if not _has(form, _CRITERIA):
        fall = {"AESDTH": "Did the adverse event result in death?", "AESLIFE": "Was the adverse event life threatening?",
                "AESHOSP": "Did the adverse event result in initial or prolonged hospitalization?",
                "AESDISAB": "Did the adverse event result in disability or permanent damage?",
                "AESCONG": "Was the adverse event associated with a congenital anomaly or birth defect?",
                "AESMIE": "Was the adverse event a medically important event not covered by other serious criteria?"}
        rows += [_row("select_one NY", v, _question(v, fall[v]), group, why) for v in _CRITERIA_NAMES]
    if with_outcome and not _has(form, _OUTCOME):
        rows.append(_row("select_one AEOUT", "AEOUT", _question("AEOUT", "What is the outcome of this adverse event?"), group, why))
    return rows


def _group_of(form, default):
    for r in reversed(_rows(form)):
        if r.get("bind__oc_itemgroup"):
            return str(r["bind__oc_itemgroup"])
    return default


def _flag(spec, msg):
    bucket = spec.setdefault("review_flags", {}).setdefault(FLAG, [])
    if msg not in bucket:
        bucket.append(msg)


def _remove_form(spec, form):
    fid = form.get("form_id")
    spec["forms"] = [f for f in spec.get("forms") or [] if f is not form]
    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict) and isinstance(soe.get("form_placements"), list):
        soe["form_placements"] = [p for p in soe["form_placements"] if not (isinstance(p, dict) and p.get("form_id") == fid)]
    dropped = 0
    for f in spec["forms"]:
        if isinstance(f, dict) and f.get("cross_form_dependencies"):
            keep = [d for d in f["cross_form_dependencies"] if not (isinstance(d, dict) and d.get("source_form") in (fid, f"F_{fid}"))]
            dropped += len(f["cross_form_dependencies"]) - len(keep)
            f["cross_form_dependencies"] = keep
    spec.setdefault("study_meta", {}).setdefault("form_conventions_removed", {})[fid] = form
    return dropped


def _standard_sae(sources, spec):
    used = {(f.get("customer_standard") or {}).get("form_oid") for f in spec.get("forms") or [] if isinstance(f, dict)}
    taken = {f.get("form_id") for f in spec.get("forms") or [] if isinstance(f, dict)}
    for s in (sources or {}).get("forms") or []:
        if s["form_oid"] not in used and s["form_oid"] not in taken and is_sae_only(s.get("title")):
            return s
    return None


def build_sae_form(spec, sources=None):
    """A separate SAE form: the customer's standard SAE form when there is an unused one (created with its id, its
    content is spliced by standards_match.splice_added), else CDASHIG seriousness fields plus the SAE reporting
    fields. Placed where the AE form is."""
    ae = ae_form(spec)
    std = _standard_sae(sources, spec)
    taken = {str(f.get("form_id") or "").upper() for f in spec.get("forms") or [] if isinstance(f, dict)}
    fid = std["form_oid"] if std else ("SAE" if "SAE" not in taken else "SAE2")
    title = (std["title"] if std else "") or "Serious Adverse Event"
    why = "form added by the customer convention SAE_FORM = Yes: review"
    form = {"choices": []}
    rows = [_row("text", "AESPID", "Adverse event number (from the Adverse Event form)", fid, why),
            _row("text", "AETERM", _question("AETERM", "What is the adverse event term?"), fid, why),
            _row("date", "AESERDAT", "Date the event became serious", fid, why)]
    rows += _seriousness_rows(form, fid, why, with_outcome=False)[1:]   # the criteria; every event here is serious
    rows += [_row("date", "HOSTDAT", "Hospital admission date", fid, why),
             _row("date", "HOENDAT", "Hospital discharge date", fid, why),
             _row("select_one AEOUT", "AEOUT", _question("AEOUT", "What is the outcome of this adverse event?"), fid, why),
             _row("text", "AENARR", "Narrative", fid, why),
             _row("date", "AESPNDAT", "Date the sponsor was notified", fid, why)]
    _ny(form)
    _out_list(form)
    events = [r.get("event") for r in ((spec.get("timepoint_csv") or {}).get("rows") or []) if isinstance(r, dict) and r.get("event")]
    visits = list((ae or {}).get("visits_assigned") or []) or [e for e in events if "COMMON" in str(e).upper()][:1] or events[:1]
    form.update({
        "form_id": fid, "form_title": title, "form_category": "CDASH_CLINICAL", "cdash_domain": "AE",
        "visits_assigned": visits, "has_repeating_group": True, "is_epro": False, "arm_applicability": "ALL",
        "library_match": {"status": "CDASH_DEFAULT", "source_type": "CDASH", "fields_from_library": len(rows),
                          "fields_extended_from_protocol": 0, "fields_from_cdash_default": len(rows)},
        "settings": {"form_title": title, "form_id": fid, "version": "1", "style": "theme-grid",
                     "namespaces": "oc=\"http://openclinica.org/xforms\"", "crossform_references": ""},
        "survey": rows, "cross_form_dependencies": [], "migration_status": "draft", "approved_by": "",
        "approved_at": "", "rejected_reason": "",
        "convention_required": {"convention": "SAE_FORM", "answer": "yes", "standard_form": std["form_oid"] if std else None,
                                "content_source": f"OC4 standard ({std['source']})" if std else "CDASHIG"},
    })
    return form


NOT_FRESH = ("not applied: this Study Specification was reused, not freshly analysed (a reused or edited "
             "specification keeps its forms); it takes effect at the next fresh protocol analysis")


def apply_sae(spec, ans=None, sources=None, fresh=True):
    """Apply SAE_FORM to the final forms. Mutates spec; returns the log lines. Records both answers.
    fresh=False (a saved or edited specification is reused): forms are never added, removed or changed; what the
    answer would do is recorded and flagged instead."""
    ans = ans or answers()
    a = ans["SAE_FORM"]
    actions = []
    ae = ae_form(spec)
    saes = sae_forms(spec)
    if not fresh:
        if a["value"] == "yes" and not saes:
            actions.append(f"SAE_FORM = Yes, but there is no separate SAE form; {NOT_FRESH}")
        elif a["value"] != "yes" and saes:
            actions.append(f"SAE_FORM = No, but there is a separate SAE form "
                           f"({', '.join(str(f.get('form_id')) for f in saes)}); {NOT_FRESH}")
        elif a["value"] != "yes" and ae is not None:
            missing = [n for n, rx in (("seriousness", _SERIOUS), ("SAE criteria", _CRITERIA), ("outcome", _OUTCOME)) if not _has(ae, rx)]
            if missing:
                actions.append(f"{ae.get('form_id')}: the AE form has no {', '.join(missing)} field; {NOT_FRESH}")
        for m in actions:
            _flag(spec, m)
    elif a["value"] == "yes":
        if saes:
            actions.append(f"separate SAE form kept: {', '.join(str(f.get('form_id')) for f in saes)}")
        else:
            form = build_sae_form(spec, sources)
            spec.setdefault("forms", []).append(form)
            soe = spec.get("schedule_of_events")
            if isinstance(soe, dict) and isinstance(soe.get("form_placements"), list):
                for v in form["visits_assigned"]:
                    soe["form_placements"].append({"target_visit_oid": v, "form_id": form["form_id"], "required": False,
                                                   "repeating": True, "notes": "Customer convention SAE_FORM = Yes"})
            msg = (f"{form['form_id']} ({form['form_title']}): separate SAE form added (SAE_FORM = Yes); content from "
                   f"{form['convention_required']['content_source']}")
            actions.append(msg)
            _flag(spec, msg)
    else:
        if ae is None:
            if saes:
                msg = (f"SAE_FORM = No, but there is no Adverse Event form to carry seriousness: "
                       f"{', '.join(str(f.get('form_id')) for f in saes)} kept, review")
                actions.append(msg)
                _flag(spec, msg)
        else:
            aid = ae.get("form_id")
            missing = [n for n, rx in (("seriousness", _SERIOUS), ("SAE criteria", _CRITERIA), ("outcome", _OUTCOME)) if not _has(ae, rx)]
            if missing and ae.get("customer_standard"):
                msg = (f"{aid}: the customer standard AE form has no {', '.join(missing)} field; PROPOSAL: add "
                       f"{', '.join(missing)} (CDASHIG AESER, the six seriousness criteria, AEOUT). A customer standard "
                       f"form is not changed without approval")
                actions.append(msg)
                _flag(spec, msg)
            elif missing:
                why = "added by the customer convention SAE_FORM = No (seriousness on the AE form): review"
                rows = _seriousness_rows(ae, _group_of(ae, str(aid)), why)
                if any(r["type"] == "select_one NY" for r in rows):
                    _ny(ae)
                if any(r["name"] == "AEOUT" for r in rows):
                    _out_list(ae)
                ae.setdefault("survey", []).extend(rows)
                msg = f"{aid}: {', '.join(missing)} added from CDASHIG ({', '.join(r['name'] for r in rows)})"
                actions.append(msg)
                _flag(spec, msg)
            carries = _has(ae, _SERIOUS)
            for f in saes:
                fid = f.get("form_id")
                if f.get("customer_standard"):
                    msg = (f"{fid} ({f.get('form_title')}): a customer standard SAE form, kept although SAE_FORM = No; "
                           f"review with the customer")
                elif not carries:
                    msg = (f"{fid} ({f.get('form_title')}): kept although SAE_FORM = No, because the AE form {aid} "
                           f"does not carry seriousness; review")
                else:
                    dropped = _remove_form(spec, f)
                    msg = (f"{fid} ({f.get('form_title')}): separate SAE form from the analysis removed (SAE_FORM = No); "
                           f"seriousness, criteria and outcome are on {aid}"
                           + (f"; {dropped} cross-form reference(s) to it removed" if dropped else ""))
                actions.append(msg)
                _flag(spec, msg)
            if not saes and not missing:
                actions.append(f"no separate SAE form; seriousness, criteria and outcome are on {aid}")
    for m in actions:
        _log(m)
    recs = []
    for qid in QUESTIONS:
        r = dict(ans[qid], id=qid, applied=actions if qid == "SAE_FORM" else [])
        recs.append(r)
    old = {r.get("id"): r for r in state(spec).get("answers") or []}
    if old.get("DEATH_DETAILS_FORM", {}).get("applied") and old["DEATH_DETAILS_FORM"].get("value") == ans["DEATH_DETAILS_FORM"]["value"]:
        recs[1]["applied"] = old["DEATH_DETAILS_FORM"]["applied"]
    spec.setdefault("study_meta", {})["form_conventions"] = {"fingerprint": _fingerprint(ans), "answers": recs}
    return summary_lines(spec)


def record_death(spec, ans=None):
    """What DEATH_DETAILS_FORM did, from the protocol completeness check's state."""
    st = state(spec)
    if not st:
        return
    pf = ((spec.get("study_meta") or {}).get("protocol_forms") or {})
    rec = next((r for r in st.get("answers") or [] if r.get("id") == "DEATH_DETAILS_FORM"), None)
    if rec is None or pf.get("status") != "done" or pf.get("death_details_form") != rec.get("value"):
        return
    out = []
    for r in pf.get("assessments") or []:
        if r.get("domain") == "DD":
            out.append(f"Death Details form {r.get('form')} added (\"{r['assessment']}\")" if r.get("added")
                       else f"\"{r['assessment']}\": {r.get('basis')}" + (f" (recorded on {r['form']})" if r.get("form") else ""))
    rec["applied"] = out or ["the protocol lists no death assessment; no Death Details form"]


def section(spec):
    """(title, note, headers, rows, column weights) for the Study Specification, or None when nothing is recorded."""
    st = state(spec)
    if not st.get("answers"):
        return None
    rows = [[r.get("id"), r.get("question"), r.get("answer"), r.get("source") + (f" ({r['note']})" if r.get("note") else ""),
             "; ".join(r.get("applied") or []) or "—"] for r in st["answers"]]
    return ("CUSTOMER FORM CONVENTIONS", "questions whose answer decides which forms exist; an unanswered question "
            "takes its default", ["Convention", "Question", "Answer", "Source", "Applied"], rows, [3, 6, 6, 3, 10])


def summary_lines(spec):
    st = state(spec)
    lines = []
    for r in st.get("answers") or []:
        lines.append(f"Customer convention {r['id']}: {r['answer']} ({r['source']})"
                     + (f" [{r['note']}]" if r.get("note") else ""))
        lines += [f"  {a}" for a in r.get("applied") or []]
    return lines
