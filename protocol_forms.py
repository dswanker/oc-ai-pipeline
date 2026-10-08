"""Forms are protocol-driven: the PROTOCOL defines which forms exist; standards only supply their content.

After the protocol analysis, every assessment the protocol requires data for (Schedule of Activities rows and the
study-procedures sections) must map to a form. The assessments come from one validated AI call: each carries a
verbatim protocol quote that is verified against the protocol text, or it is discarded. Mapping an assessment to a
form is deterministic (CDASH domain, form title, field variables). An assessment no form covers gets a form through
the precedence chain

    customer OC4 standard / referenced OC study  ->  Customer CRF standards  ->  CDASHIG

and is logged with the protocol section that requires it. A customer standard form that matches a required
assessment is therefore always used. Nothing is ever removed, and a standard never adds a form on its own.

State: study_meta.protocol_forms. Kill switch PROTOCOL_FORMS_CHECK=0. On any error the spec is left unchanged.
"""
from __future__ import annotations
import hashlib
import json
import os
import re

VERSION = 1
MAX_ASSESSMENTS = 80
MIN_QUOTE = 12
SRC_STANDARD = "OC4 standard"
SRC_CRF = "Customer CRF standards"
SRC_CDASH = "CDASHIG"
SRC_PLACEHOLDER = "placeholder (no standard, no CDASH domain)"
FLAG = "protocol_required_form_added"
FLAG_REVIEW = "protocol_assessment_review"

# assessment wording -> CDASH domain (first hit wins; None = a recognised assessment without a CDASH CRF domain)
_RULES = [
    (r"informed consent|\bicf\b|\breconsent", None),
    (r"biospecimen|specimen|sample collection|biobank", None),
    (r"pregnan", None),
    (r"physical exam", "PE"),
    (r"vital sign", "VS"),
    (r"medical history|surgical history|disease history", "MH"),
    (r"demograph", "DM"),
    (r"serious adverse|adverse event|\bsaes?\b|\baes?\b", "AE"),
    (r"procedure", "PR"),
    (r"radiation|radiotherap", "PR"),
    (r"medication|concomitant therap|prior therap", "CM"),
    (r"electrocardiogram|\becg\b|\bekg\b", "EG"),
    (r"eligib|inclusion|exclusion", "IE"),
    (r"deviation", "DV"),
    (r"death", "DD"),
    (r"disposition|end of study|study completion|early termination|withdrawal|discontinuation", "DS"),
    (r"pharmacokinetic|\bpk\b", "PC"),
    (r"laborator|hematolog|haematolog|chemistr|urinalysis|coagulation|serolog|\blabs?\b", "LB"),
    (r"substance use|tobacco|alcohol use", "SU"),
    (r"administration|dosing|\bdose\b|injection|infusion|exposure|study drug|study treatment|"
     r"investigational product", "EX"),
]
_RULES = [(re.compile(p, re.I), d) for p, d in _RULES]
# a data collection kept as a running log for the whole study rather than done at a visit
_LOG_WORDS = re.compile(r"concomitant|\bprior\b|previous|\blog\b|adverse|deviation|medical history", re.I)
_LOG_DOMAINS = {"AE", "CM", "DV", "MH"}
_FILLER = {"assessment", "assessments", "test", "tests", "testing", "form", "review", "recording", "record", "prior",
           "previous", "concomitant", "clinical", "safety", "standard", "complete", "full", "brief", "targeted",
           "directed", "symptom", "log", "report", "reporting", "collection", "data", "and", "or", "of", "the", "a",
           "an", "for", "to", "in", "at", "with", "study", "result", "results", "evaluation", "evaluations", "s", "therapy", "treatment", "other", "new"}
# domains that are one family for finding the form (exposure as collected / as derived)
_FAMILY = {"EX": {"EX", "EC"}, "EC": {"EX", "EC"}}
_NOT_FIELDS = {"VISDAT", "STUDYID", "SITEID", "SUBJID", "USUBJID", "INVID", "INVNAM", "SPONSOR", "VISIT", "VISITNUM", "EPOCH"}
_PLACEHOLDER = re.compile(r"\[|\]|DD-MON-YYYY|YYYY")


def enabled():
    return os.environ.get("PROTOCOL_FORMS_CHECK", "1") != "0"


def _log(msg):
    print(f"[protocol-forms] {msg}", flush=True)


# ── Text helpers ─────────────────────────────────────────────────────────────────

def _squash(text):
    """Letters and digits only, lower case: a quote survives PDF line breaks, hyphenation and table spacing."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def quote_in(quote, squashed_protocol):
    q = _squash(quote)
    return len(q) >= MIN_QUOTE and q in squashed_protocol


def _tokens(text):
    out = []
    for t in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if t in _FILLER or len(t) < 2:
            continue
        out.append(t[:-1] if len(t) > 4 and t.endswith("s") else t)
    return out


def _same(a, b):
    """Two words name the same thing when one starts the other (exam / examination, pregnancy / pregnant)."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.startswith(short[:max(4, len(short) - 2)])


def _overlap(a_tokens, b_tokens):
    """Share of a_tokens that have a counterpart in b_tokens."""
    if not a_tokens:
        return 0.0
    return sum(1 for a in a_tokens if any(_same(a, b) for b in b_tokens)) / len(a_tokens)


def _similar(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return round(max(min(_overlap(ta, tb), _overlap(tb, ta)), 0.6 * max(_overlap(ta, tb), _overlap(tb, ta))), 3)


def _domain_names():
    import standards_match as sm
    return sm.DOMAIN_NAMES


def _norm_phrase(text):
    return " ".join(t[:-1] if len(t) > 4 and t.endswith("s") else t for t in re.findall(r"[a-z0-9]+", str(text or "").lower()))


def is_generic(name, domain):
    """True when the name is just the domain's own name ("Concomitant Procedures" for PR, "Clinical labs" for LB),
    not a specific kind of it ("External Beam Radiation Therapy", "PSA")."""
    if not domain:
        return False
    n = _norm_phrase(name)
    if n.upper() == domain:
        return True
    for phrase in sorted(_domain_names().get(domain, ()), key=len, reverse=True):
        p = _norm_phrase(phrase)
        if re.search(rf"\b{re.escape(p)}", n):
            rest = [t for t in re.sub(rf"\b{re.escape(p)}\w*", " ", n).split() if t not in _FILLER]
            return not rest
    return False


def assessment_domain(name, ai_domain=None, known=None):
    """(domain or None, basis). Wording decides; the model's own domain is used only for wording we do not know."""
    for rx, dom in _RULES:
        if rx.search(str(name or "")):
            return dom, "assessment name"
    n = _norm_phrase(name)
    for dom, phrases in _domain_names().items():
        if any(re.search(rf"\b{re.escape(_norm_phrase(p))}", n) for p in phrases):
            return dom, "assessment name"
    d = str(ai_domain or "").strip().upper()
    if d and (known is None or d in known):
        return d, "AI-proposed domain"
    return None, "no CDASH domain"


# ── The validated extraction call ────────────────────────────────────────────────

PROMPT = """You list the assessments a clinical trial PROTOCOL requires data to be collected for.

Use ONLY the Schedule of Activities / Schedule of Assessments tables and the study-procedures / study-assessments
sections of the protocol. List what the SITE records for a participant: a row of the Schedule of Activities, or a
statement that something "will be recorded / collected / performed / documented". Do NOT list: laboratory methods
or analyses run later on collected samples, statistical or sponsor activities, a reason for withdrawal, a general
compliance statement, or a single measurement that is part of a listed assessment (height and weight belong to
vital signs; one analyte belongs to its laboratory panel unless the Schedule of Activities gives it its own row).
Rules:
1. One entry per distinct data collection the protocol requires, for example: informed consent, demographics,
   medical history, disease assessment / staging, each study treatment administration (investigational product,
   prodrug, radiation and so on, separately), adverse events / serious adverse events, safety laboratory tests, each
   specially named laboratory test or biomarker, physical examination, vital signs, ECG, concomitant medications,
   concomitant procedures, biospecimen collection, questionnaires, pregnancy reporting, disposition / end of study,
   death.
2. A combined row or heading gives ONE ENTRY PER ASSESSMENT: "history and physical" is medical history AND physical
   examination; "concomitant medications and procedures" is concomitant medications AND concomitant procedures.
3. Do NOT list something that appears only as an inclusion / exclusion criterion (a test that is only an
   eligibility requirement is not an assessment). List eligibility itself once.
4. Do not invent assessments. Every entry needs a quote copied VERBATIM from the protocol (one sentence or table
   row fragment, at most 300 characters) that shows the protocol requires it. An entry whose quote is not found in
   the protocol text is discarded.
5. "section": the protocol section number or table name the quote is from (for example "10.5.2", "Table 1 SoA").
6. "events": the event OIDs from the EVENTS list below at which the protocol schedules it. Use only OIDs from the
   list. Empty when it is collected continuously (a log) or the timing is not stated.
7. "log": true when it is recorded continuously over the study (adverse events, concomitant medications and
   procedures, deviations), false when it is done at visits.
8. "cdash_domain": the CDASH domain code when you are sure (PE, VS, LB, AE, CM, PR, MH, DM, EX, EC, DS, DD, EG, IE,
   DV, PC, SU, QS, RS, TU, TR), else null.

Return ONLY JSON:
{"assessments": [{"name": "Physical examination", "cdash_domain": "PE", "section": "10.5.2",
                  "quote": "...", "events": ["SE_SCREENING"], "log": false}]}
"""


def events_of(spec):
    out, seen = [], set()
    for r in ((spec or {}).get("timepoint_csv") or {}).get("rows") or []:
        oid = r.get("event") if isinstance(r, dict) else None
        if oid and oid not in seen:
            seen.add(oid)
            out.append((oid, str(r.get("timepoint") or "")))
    for e in (spec or {}).get("events") or []:
        oid = e.get("event_oid") if isinstance(e, dict) else None
        if oid and oid not in seen:
            seen.add(oid)
            out.append((oid, str(e.get("event_title") or "")))
    return out


def build_request(spec, protocol_text, with_text=True, max_chars=600_000):
    """(prompt, extra_text) or None when there is no protocol text to verify quotes against. with_text=False when
    the caller passes the protocol itself (PDF) alongside."""
    if not str(protocol_text or "").strip():
        return None
    ev = "\n".join(f"{oid} | {title}" for oid, title in events_of(spec))
    extra = "EVENTS (event OID | name):\n" + (ev or "(none)")
    if with_text:
        extra += "\n\nPROTOCOL TEXT:\n" + str(protocol_text)[:max_chars]
    return PROMPT, extra


def _parse(text):
    t = str(text or "")
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _split_combined(name):
    """A combined name the model did not split ("medications and procedures") becomes one entry per assessment."""
    n = str(name or "")
    if re.search(r"medication", n, re.I) and re.search(r"procedure", n, re.I):
        return ["Concomitant medications", "Concomitant procedures"]
    if re.search(r"history", n, re.I) and re.search(r"physical", n, re.I):
        return ["Medical history", "Physical examination"]
    return [n]


def validate_response(spec, response_text, protocol_text):
    """{"assessments": [...], "rejected": {reason: n}}. Only entries with a verified verbatim quote survive."""
    rejected, out, seen = {}, [], set()

    def rej(why):
        rejected[why] = rejected.get(why, 0) + 1

    data = _parse(response_text)
    items = (data or {}).get("assessments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {"assessments": [], "rejected": {"unparseable response": 1}}
    squashed = _squash(protocol_text)
    known_events = {oid for oid, _ in events_of(spec)}
    known_domains = _cdash_domains()
    for it in items:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            rej("no name")
            continue
        quote = str(it.get("quote") or "").strip()
        if not quote_in(quote, squashed):
            rej("quote not found in the protocol")
            continue
        for name in _split_combined(str(it["name"]).strip()):
            key = _norm_phrase(name)
            if key in seen:
                rej("duplicate")
                continue
            if len(out) >= MAX_ASSESSMENTS:
                rej("over the limit")
                continue
            seen.add(key)
            dom, basis = assessment_domain(name, it.get("cdash_domain"), known_domains or None)
            out.append({"name": name, "domain": dom, "domain_basis": basis,
                        "section": str(it.get("section") or "").strip()[:60], "quote": quote[:400],
                        "events": [e for e in (it.get("events") or []) if e in known_events],
                        "log": bool(it.get("log")) or bool(_LOG_WORDS.search(name)) or dom in _LOG_DOMAINS})
    return {"assessments": out, "rejected": rejected}


# ── Deterministic mapping: assessment -> form ────────────────────────────────────

def _cdash_domains():
    try:
        import standards_match as sm
        return set(sm._cdash()[3])
    except Exception:
        return set()


def _form_domains(form):
    import standards_match as sm
    doms = list(sm._protocol_domains(form))
    cs = form.get("customer_standard") or {}
    if cs.get("domain") and cs["domain"] not in doms:
        doms.append(cs["domain"])
    return doms


def _field_domain_count(form, domain):
    import standards_match as sm
    n = 0
    for r in form.get("survey") or []:
        if isinstance(r, dict) and sm.is_data_row(r):
            try:
                if domain in (sm._field_domains(r) or ()):
                    n += 1
            except Exception:
                pass
    return n


def _is_log_form(form, domain):
    title = str(form.get("form_title") or "")
    visits = [str(v).upper() for v in form.get("visits_assigned") or []]
    return (is_generic(title, domain) or bool(_LOG_WORDS.search(title)) or bool(form.get("has_repeating_group"))
            or any("COMMON" in v for v in visits))


def _labels_have(form, tokens):
    if not tokens:
        return False
    for r in (form.get("survey") or []) + (form.get("choices") or []):
        if isinstance(r, dict):
            lt = _tokens(f"{r.get('label') or ''} {r.get('name') or ''}")
            if _overlap(tokens, lt) >= 1.0:
                return True
    return False


def cover(assessment, forms):
    """(form, basis) for the form that collects this assessment, or (None, "")."""
    name, dom, quote = assessment["name"], assessment.get("domain"), assessment.get("quote") or ""
    a_tok = _tokens(name)
    scored = sorted(((_similar(name, f.get("form_title")), i) for i, f in enumerate(forms)), key=lambda t: (-t[0], t[1]))
    if dom:
        fam = _FAMILY.get(dom, {dom})
        cands = [f for f in forms if fam & set(_form_domains(f)) or str(f.get("form_id") or "").upper() in fam]
        a_generic = is_generic(name, dom)
        best = max(cands, key=lambda f: _similar(name, f.get("form_title")), default=None)
        if best is not None and _similar(name, best.get("form_title")) >= 0.6:
            return best, f"CDASH domain {dom}, form title"
        if not a_generic:
            # a specific kind of the domain (one treatment, one test): the form that names it, before a generic one
            named = sorted(((_overlap(a_tok, _tokens(f.get("form_title"))) + (1 if _labels_have(f, a_tok) else 0), -i, f)
                            for i, f in enumerate(cands)), key=lambda t: (-t[0], -t[1]))
            if named and named[0][0] > 0 and (len(named) == 1 or named[0][0] > named[1][0]):
                return named[0][2], f"CDASH domain {dom}, named on the form"
        generic = [f for f in cands if is_generic(f.get("form_title"), dom) or str(f.get("form_id") or "").upper() == dom]
        if generic:
            return generic[0], f"CDASH domain {dom}"
        if assessment.get("log"):
            logs = [f for f in cands if _is_log_form(f, dom)]
            if logs:
                return logs[0], f"CDASH domain {dom}, log form"
        elif a_generic and cands:
            return cands[0], f"CDASH domain {dom}"
        if not a_generic:
            for f in cands:
                if _overlap(a_tok, _tokens(f.get("form_title"))) > 0 or _labels_have(f, a_tok):
                    return f, f"CDASH domain {dom}, named on the form"
        # a form of another domain that carries this domain's fields (a combined form)
        for f in forms:
            if f not in cands and _field_domain_count(f, dom) >= 2:
                return f, f"{dom} fields on the form"
        if not assessment.get("log") or not a_generic:
            for f in forms:
                if _labels_have(f, a_tok):
                    return f, "named on a field or choice"
        return None, ""
    if scored and scored[0][0] >= 0.5:
        return forms[scored[0][1]], "form title"
    nq = _tokens(name) + _tokens(quote)
    for f in forms:
        ft = _tokens(f.get("form_title"))
        if ft and _overlap(ft, a_tok) >= 0.5:
            return f, "form title"
    for f in forms:
        ft = _tokens(f.get("form_title"))
        if len(ft) >= 1 and _overlap(ft, nq) >= 1.0 and _overlap(a_tok, ft) > 0:
            return f, "form title in the protocol wording"
    return None, ""


# ── Building a form for an assessment nothing covers ─────────────────────────────

def _standard_for(assessment, sources, used_oids):
    """The unused customer standard form that collects this assessment, by source priority, or None."""
    name, dom = assessment["name"], assessment.get("domain")
    best = None
    for s in (sources or {}).get("forms") or []:
        if s["form_oid"] in used_oids:
            continue
        sim = _similar(name, s.get("title"))
        if dom and s.get("domain") == dom:
            ok = sim >= 0.5 or (is_generic(name, dom) and (is_generic(s.get("title"), dom) or str(s["form_oid"]).upper() == dom))
        elif not dom or not s.get("domain"):
            ok = sim >= 0.6
        else:
            ok = False
        if ok and (best is None or sim > best[0]):
            best = (sim, s)
    return best[1] if best else None


def _crf_key_for(assessment, crf_forms):
    name, dom = assessment["name"], assessment.get("domain")
    for k in crf_forms or {}:
        if dom and str(k).upper() == dom:
            return k
    for k in crf_forms or {}:
        if _similar(name, k) >= 0.6:
            return k
    return None


def _row(rtype, name, label, group, **extra):
    r = {"type": rtype, "name": name, "label": label, "bind__oc_itemgroup": group, "completion_status": "FLAGGED",
         "library_source": "CDASH_DEFAULT", "flag_reason": "form added by the protocol completeness check: review"}
    r.update(extra)
    return r


def _cdash_rows(domain):
    """Rows from CDASHIG for a domain: the Highly Recommended and Recommended/Conditional variables."""
    import standards_match as sm
    fields = sm._cdash()[1]
    rows, need_ny = [], False
    for (dom, var), rec in fields.items():
        if (dom != domain or var in _NOT_FIELDS or rec.get("core") not in ("HR", "R/C")
                or not re.fullmatch(r"[A-Z][A-Z0-9]*", var)):
            continue
        q = rec.get("question") or ""
        label = q if q and not _PLACEHOLDER.search(q) else (rec.get("label") or var)
        if var.endswith("DAT"):
            rtype = "date"
        elif var.endswith(("YN", "PERF")):
            rtype, need_ny = "select_one NY", True
        elif (rec.get("type") or "").lower().startswith("num"):
            rtype = "decimal"
        else:
            rtype = "text"
        rows.append(_row(rtype, var, label, domain))
    rows.sort(key=lambda r: (0 if r["type"] == "date" else 1))
    return rows, need_ny


def _form_id(base, taken):
    fid = re.sub(r"[^A-Z0-9]", "", str(base or "").upper())[:12] or "FORM"
    if fid[0].isdigit():
        fid = "F" + fid
    out, i = fid, 2
    while out in taken:
        out, i = f"{fid}{i}", i + 1
    return out


def _acronym(name):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", str(name or "")) if w.lower() not in ("and", "of", "the", "or")]
    return (words[0][:8] if len(words) == 1 else "".join(w[0] for w in words)).upper()


def build_form(assessment, spec, sources=None, crf_forms=None):
    """A form for a required assessment nothing covers, with the source of its content. Standard content itself is
    spliced by standards_match right after (the form is created with the standard's id so it is matched)."""
    name, dom = assessment["name"], assessment.get("domain")
    taken = {str(f.get("form_id") or "").upper() for f in spec.get("forms") or [] if isinstance(f, dict)}
    used = {(f.get("customer_standard") or {}).get("form_oid") for f in spec.get("forms") or [] if isinstance(f, dict)}
    std = _standard_for(assessment, sources, used | taken)
    crf_key = None if std else _crf_key_for(assessment, crf_forms)
    rows, need_ny = _cdash_rows(dom) if dom else ([], False)
    title = name[:1].upper() + name[1:]
    if std:
        fid, title, source = std["form_oid"], std["title"] or title, f"{SRC_STANDARD} ({std['source']})"
    elif crf_key:
        fid, source = (str(crf_key).upper() if str(crf_key).upper() not in taken else _form_id(crf_key, taken)), SRC_CRF
    elif rows:
        fid, source = _form_id(dom if dom not in taken else _acronym(name), taken), SRC_CDASH
    else:
        fid, source = _form_id(_acronym(name), taken), SRC_PLACEHOLDER
    group = re.sub(r"[^A-Z0-9]", "", fid.upper())[:20] or "IG"
    if not rows:
        need_ny = True
        rows = [_row("select_one NY", f"{group}PERF", f"Was {name[:1].lower() + name[1:]} performed / recorded?", group),
                _row("date", f"{group}DAT", "Date", group)]
    for r in rows:
        r["bind__oc_itemgroup"] = group
    events = [oid for oid, _ in events_of(spec)]
    visits = [e for e in assessment.get("events") or [] if e in events]
    placement = "protocol schedule"
    if not visits or assessment.get("log"):
        common = [e for e in events if "COMMON" in e.upper()]
        if assessment.get("log") and common:
            visits, placement = common[:1], "log: common event"
        elif not visits:
            visits, placement = events[:1], "NOT FOUND in the protocol: placed at the first event, review"
    form = {
        "form_id": fid, "form_title": title, "form_category": "CDASH_CLINICAL" if dom else "CUSTOM",
        "cdash_domain": dom, "visits_assigned": visits, "has_repeating_group": bool(assessment.get("log")),
        "is_epro": False, "arm_applicability": "ALL",
        "library_match": {"status": "CDASH_DEFAULT" if dom else "PROTOCOL_ONLY", "source_type": "CDASH" if dom else "PROTOCOL",
                          "fields_from_library": len(rows) if dom else 0, "fields_extended_from_protocol": 0,
                          "fields_from_cdash_default": len(rows) if dom else 0},
        "settings": {"form_title": title, "form_id": fid, "version": "1", "style": "theme-grid",
                     "namespaces": "oc=\"http://openclinica.org/xforms\"", "crossform_references": ""},
        "choices": ([{"list_name": "NY", "label": "No", "name": "N", "source": "STANDARD"},
                     {"list_name": "NY", "label": "Yes", "name": "Y", "source": "STANDARD"}] if need_ny else []),
        "survey": rows, "cross_form_dependencies": [], "migration_status": "draft", "approved_by": "",
        "approved_at": "", "rejected_reason": "",
        "protocol_required": {"assessment": name, "section": assessment.get("section") or "",
                              "quote": assessment.get("quote") or "", "content_source": source,
                              "placement": placement, "standard_form": std["form_oid"] if std else None},
    }
    return form, source, placement


# ── Apply ────────────────────────────────────────────────────────────────────────

def state(spec):
    sm = (spec or {}).get("study_meta") if isinstance(spec, dict) else None
    return (sm or {}).get("protocol_forms") or {} if isinstance(sm, dict) else {}


def fingerprint(protocol_text, sources=None):
    h = hashlib.sha256(_squash(protocol_text).encode()).hexdigest()
    return hashlib.sha256(json.dumps([VERSION, h, (sources or {}).get("fingerprint") or ""]).encode()).hexdigest()


def needs_check(spec, protocol_text, sources=None):
    if not enabled() or not isinstance(spec, dict) or not str(protocol_text or "").strip():
        return False
    return state(spec).get("fingerprint") != fingerprint(protocol_text, sources)


def apply(spec, assessments, sources=None, crf_forms=None, protocol_text="", rejected=None):
    """Map every assessment to a form; add a form for each one nothing covers. Mutates spec; returns the state."""
    forms = [f for f in spec.get("forms") or [] if isinstance(f, dict)]
    records, added = [], []
    for a in assessments:
        f, basis = cover(a, forms)
        rec = {"assessment": a["name"], "domain": a.get("domain"), "section": a.get("section") or "",
               "quote": a.get("quote") or "", "log": bool(a.get("log"))}
        if f is not None:
            rec.update(form=f.get("form_id"), basis=basis, added=False)
        else:
            form, source, placement = build_form(a, spec, sources, crf_forms)
            spec.setdefault("forms", []).append(form)
            forms.append(form)
            soe = spec.get("schedule_of_events")
            if isinstance(soe, dict) and isinstance(soe.get("form_placements"), list):
                for v in form["visits_assigned"]:
                    soe["form_placements"].append({"target_visit_oid": v, "form_id": form["form_id"],
                                                   "required": not a.get("log"), "repeating": bool(a.get("log")),
                                                   "notes": f"Required by protocol {a.get('section') or ''}".strip()})
            msg = (f"{form['form_id']} ({form['form_title']}): added because protocol "
                   f"{('section ' + a['section']) if a.get('section') else 'text'} requires \"{a['name']}\" and no "
                   f"form collected it; content from {source}; visits: {placement}")
            bucket = spec.setdefault("review_flags", {}).setdefault(FLAG, [])
            if msg not in bucket:
                bucket.append(msg)
            rec.update(form=form["form_id"], basis="added", added=True, content_source=source, placement=placement)
            added.append(rec)
            _log(msg)
        records.append(rec)
    st = {"version": VERSION, "fingerprint": fingerprint(protocol_text, sources), "status": "done",
          "assessments": records, "added": [r["form"] for r in added], "rejected": dict(rejected or {})}
    spec.setdefault("study_meta", {})["protocol_forms"] = st
    return st


def refresh_sources(spec):
    """After standards matching: an added form that was created for a standard form takes that id; keep the records
    and the form's content source in step with what was actually spliced."""
    st = state(spec)
    if not st:
        return
    by_assessment = {}
    for f in spec.get("forms") or []:
        pr = f.get("protocol_required") if isinstance(f, dict) else None
        if pr:
            cs = f.get("customer_standard") or {}
            if cs:
                pr["content_source"] = f"{SRC_STANDARD} ({cs.get('source')})"
            elif pr.get("standard_form"):
                pr["content_source"] = SRC_CDASH if f.get("cdash_domain") else SRC_PLACEHOLDER
            by_assessment[pr.get("assessment")] = (f.get("form_id"), pr["content_source"])
    for r in st.get("assessments") or []:
        if r.get("added") and r.get("assessment") in by_assessment:
            r["form"], r["content_source"] = by_assessment[r["assessment"]]
    st["added"] = [r["form"] for r in st.get("assessments") or [] if r.get("added")]


def form_sources(spec):
    """One record per form: where its content comes from and which protocol section(s) require it."""
    st = state(spec)
    req = {}
    for r in st.get("assessments") or []:
        req.setdefault(r.get("form"), []).append(r)
    out = []
    for f in spec.get("forms") or []:
        if not isinstance(f, dict):
            continue
        cs, pr = f.get("customer_standard") or {}, f.get("protocol_required") or {}
        if cs:
            source = f"{SRC_STANDARD} ({cs.get('source')}): {cs.get('form_oid') or cs.get('form_name')}"
        elif pr:
            source = pr.get("content_source") or SRC_CDASH
        else:
            lm = f.get("library_match") or {}
            st_ = str(lm.get("source_type") or lm.get("status") or "").upper()
            source = (SRC_CRF if "CUSTOMER" in st_ or "LIBRARY" in st_ else
                      SRC_CDASH if "CDASH" in st_ else "Protocol analysis")
        rs = req.get(f.get("form_id")) or []
        out.append({"form_id": f.get("form_id"), "form_title": f.get("form_title"), "source": source,
                    "added": bool(pr), "required_by": [{"assessment": r["assessment"], "section": r.get("section") or "",
                                                         "quote": r.get("quote") or ""} for r in rs]})
    return out


def summary_lines(spec):
    st = state(spec)
    if not st or st.get("status") != "done":
        return []
    recs = st.get("assessments") or []
    added = [r for r in recs if r.get("added")]
    lines = [f"Protocol-required forms: {len(recs)} assessment(s) found in the Schedule of Activities / study "
             f"procedures (each with a verified protocol quote); {len(recs) - len(added)} already had a form, "
             f"{len(added)} form(s) added."
             + (f" {sum((st.get('rejected') or {}).values())} entry(ies) discarded by validation." if st.get("rejected") else "")]
    for r in added:
        lines.append(f"  + {r['form']}: \"{r['assessment']}\" (protocol {r.get('section') or 'section not given'}); "
                     f"content from {r.get('content_source')}; visits: {r.get('placement')}")
    return lines
