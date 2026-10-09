"""Forms are protocol-driven: the PROTOCOL defines which forms exist; standards only supply their content.

After the protocol analysis and standards matching, every assessment the protocol requires data for (Schedule of
Activities rows and the study-procedures sections) must map to a form. A form covers an assessment when it collects
it: same CDASH domain, or its title / fields say so (a form titled for one treatment covers that treatment; an
administration form with dosing fields covers compliance with that treatment). Nothing is added twice: no form for an
assessment another form collects, none whose content would be a standard form another form already uses. A Death
Details form is added only when the protocol asks for death DETAILS (cause of death, autopsy, circumstances) beyond
the death itself, which Disposition and Adverse Events capture (CDASHIG: DD is optional), and the customer's
DEATH_DETAILS_FORM answer allows it. The assessments come from a validated AI call that must account for a
checklist read from the protocol itself (protocol_structure.py): every Schedule of Activities row and every heading
of the procedures / assessments chapter(s) is answered with its assessments or an explicit "none"; entries the
answer leaves out get one follow-up call and are otherwise recorded as "not assessed". Each assessment carries a
verbatim protocol quote that is verified against the protocol text, or it is discarded, and the kind of that
passage: a form is added only for an instruction to record or a schedule row, never for an eligibility criterion, a
heading or a process description. Mapping an assessment to a
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
_COMPLIANCE = re.compile(r"complian|adheren", re.I)
_DOSING_TITLE = re.compile(r"administ|dosing|\bdose|exposure|complian|study drug|study treatment", re.I)
_DOSING_FIELD = re.compile(r"dose|dosing|complian|administered|taken|missed", re.I)
_DEATH_DETAILS = re.compile(r"cause of (the )?death|autops|circumstances? of (the )?death|place of death|"
                            r"death certificate|death details", re.I)
DEATH_PROTOCOL, DEATH_ALWAYS, DEATH_NEVER = "protocol", "always", "never"


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

# what a quoted passage is, judged by its own words; only the first two are a basis for collecting data
KINDS_REQUIRE = ("record", "schedule")
KINDS = KINDS_REQUIRE + ("heading", "eligibility", "course", "process", "definition", "time", "other")
KIND_TEXT = """  "record"       an instruction that this data is recorded, collected, reported or documented, or that the
                 assessment which produces it is performed, for a participant
  "schedule"     a row of the Schedule of Activities / Schedule of Assessments (or its footnote) that schedules
                 this assessment. A column, a visit name or a study period is not such a row
  "heading"      a section heading or title on its own, without a sentence that instructs anything
  "eligibility"  a condition a person must meet to take part
  "course"       what happens to participants, what they receive, may do or must do, who is treated or assigned
  "process"      how the site or the sponsor handles a situation; a rule about taking part again, leaving,
                 replacing or counting participants
  "definition"   a definition of a term
  "time"         the term used as a point in time, a milestone or a study period
  "other"        anything else"""
KIND_WORDS = {"heading": "a section heading without an instruction to record", "eligibility": "an eligibility condition",
              "course": "a statement of what happens to participants",
              "process": "a description of how a situation is handled", "definition": "a definition",
              "time": "a point in time or study period", "other": "no instruction to record it"}

PROMPT = """You account, entry by entry, for what a clinical trial PROTOCOL requires the site to record for a participant.

You get a CHECKLIST read from the protocol itself: the rows of its Schedule of Activities / Schedule of Assessments
tables (type "row") and the headings of the sections that describe procedures and assessments (type "heading").
For EVERY checklist entry return either the assessments it contains, or an explicit "none" with a one-line reason.

An assessment is one distinct data collection the SITE records for a participant, for example: informed consent,
demographics, medical history, disease assessment / staging, each study treatment administration (investigational
product, prodrug, radiation and so on, separately), adverse events / serious adverse events, safety laboratory
tests, each specially named laboratory test or biomarker, physical examination, vital signs, ECG, concomitant
medications, concomitant procedures, biospecimen collection, questionnaires, pregnancy reporting, disposition / end
of study, death.
Rules:
1. A "row" entry: the assessments that row schedules. A "heading" entry: the assessments the text of that section
   requires the site to record (a sub-section with its own entry is answered under its own entry).
2. An entry that names or contains several assessments gives ONE ASSESSMENT PER DATA COLLECTION, each with a quote
   (the same quote when one sentence or row covers them). Read the entry word by word: every assessment it names
   must appear in your answer.
3. Give an assessment under EVERY entry that contains it, ALWAYS UNDER THE SAME NAME; repeats are merged by name
   afterwards. Do not answer "none" because another entry already has it.
4. "none" when the entry requires nothing to be recorded for a participant: background, a definition, a rule, a
   laboratory method or an analysis run later on collected samples, a statistical or sponsor activity, or a label
   that is not an assessment. Give the reason in one line.
5. Do not list a single measurement that is part of a listed assessment (height and weight belong to vital signs;
   one analyte belongs to its laboratory panel unless the schedule gives it its own row).
6. Do not invent assessments. Every assessment needs a "quote" copied VERBATIM from the protocol (one sentence or
   one table-row fragment, at most 300 characters). An assessment whose quote is not found in the protocol text is
   discarded.
7. "kind": what the quoted passage itself states, one of
__KINDS__
   Classify the passage as written. It is "record" only when its own words instruct recording, collecting,
   reporting, documenting or performing. Prefer the sentence of the section that carries such an instruction; quote
   a heading on its own only when the section has none. Something that appears only as an inclusion / exclusion
   criterion is "eligibility".
8. "section": the protocol section number or table name the quote is from.
9. "events": the event OIDs from the EVENTS list at which the protocol schedules it. Use only OIDs from the list.
   Empty when it is collected continuously (a log) or the timing is not stated.
10. "log": true when it is recorded continuously over the study, false when it is done at visits.
11. "cdash_domain": the CDASH domain code when you are sure (PE, VS, LB, AE, CM, PR, MH, DM, EX, EC, DS, DD, EG, IE,
    DV, PC, SU, QS, RS, TU, TR), else null.
12. "extra": assessments of a schedule row or a procedures section that is NOT on the checklist (the checklist may
    be incomplete or empty). Same fields. Empty list when there are none.

Return ONLY JSON:
{"entries": [{"id": "<checklist id>",
              "assessments": [{"name": "<assessment name>", "cdash_domain": "<domain or null>",
                               "section": "<section or table>", "quote": "<verbatim protocol text>",
                               "kind": "record", "events": ["<event OID>"], "log": false}]},
             {"id": "<checklist id>", "none": "<one-line reason>"}],
 "extra": []}
""".replace("__KINDS__", KIND_TEXT)
FOLLOW_UP = ("Your previous answer left out the checklist entries below. Answer ONLY these entries now, each with its "
             "assessments or an explicit \"none\"; leave \"extra\" empty.")


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


def checklist_enabled():
    return os.environ.get("PROTOCOL_FORMS_CHECKLIST", "1") != "0"


def build_request(spec, protocol_text, with_text=True, max_chars=600_000, checklist=None, only=None):
    """(prompt, extra_text) or None when there is no protocol text to verify quotes against. with_text=False when
    the caller passes the protocol itself (PDF) alongside. checklist: protocol_structure.checklist(); only: the
    entry ids of a follow-up request for entries the first answer left out."""
    if not str(protocol_text or "").strip():
        return None
    ev = "\n".join(f"{oid} | {title}" for oid, title in events_of(spec))
    extra = "EVENTS (event OID | name):\n" + (ev or "(none)")
    entries = [e for e in (checklist or {}).get("entries") or [] if only is None or e["id"] in set(only)]
    lines = [f"{e['id']} | {e['type']} | {e.get('table') or e.get('number') or ''} | {e['label']}" for e in entries]
    extra += ("\n\n" + (FOLLOW_UP + "\n" if only is not None else "")
              + "CHECKLIST (id | type | table or section number | row label or heading; a row label may carry a "
                "footnote number):\n" + ("\n".join(lines) or "(empty: give every assessment under \"extra\")"))
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


def validate_response(spec, response_text, protocol_text, checklist=None, schedule_text=""):
    """{"assessments": [...], "rejected": {reason: n}, "coverage": {entry id: {...}}, "missing": [entry ids]}.
    Only assessments with a verified verbatim quote survive. Every checklist entry must be answered (assessments
    or an explicit none); the ones that are not are "missing". An assessment carries the kind of its quoted
    passage; a "schedule" passage must be on a schedule-table page when those are known."""
    rejected, out, by_key = {}, [], {}

    def rej(why):
        rejected[why] = rejected.get(why, 0) + 1

    entries = {e["id"]: e for e in (checklist or {}).get("entries") or []}
    data = _parse(response_text)
    legacy = isinstance(data, dict) and isinstance(data.get("assessments"), list) and "entries" not in data
    if not isinstance(data, dict) or not (legacy or isinstance(data.get("entries"), list) or isinstance(data.get("extra"), list)):
        return {"assessments": [], "rejected": {"unparseable response": 1}, "coverage": {}, "missing": list(entries)}
    squashed = _squash(protocol_text)
    sched = _squash(schedule_text)
    known_events = {oid for oid, _ in events_of(spec)}
    known_domains = _cdash_domains()
    coverage = {}
    row_keys = {re.sub(r"\d", "", _squash(e["label"])) for e in entries.values() if e["type"] == "row"} - {""}

    def take(it, entry_id):
        """The validated assessment, or None. Merged into an earlier one of the same name."""
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            rej("no name")
            return None
        quote = str(it.get("quote") or "").strip()
        # a schedule row's label may be shorter than a quote has to be: it counts when it IS a row of the checklist
        is_row = bool(quote) and re.sub(r"\d", "", _squash(quote)) in row_keys
        if not quote_in(quote, squashed) and not is_row:
            rej("quote not found in the protocol")
            return None
        name = str(it["name"]).strip()
        kind = None
        if not legacy:
            kind = str(it.get("kind") or "").strip().lower()
            if kind not in KINDS:
                rej("unknown kind of passage")
                kind = "other"
            if kind == "schedule" and sched and not is_row and _squash(quote) not in sched:
                rej("schedule passage is not in a schedule table")
                kind = "other"
        events = [e for e in (it.get("events") or []) if e in known_events]
        key = _norm_phrase(name)
        old = by_key.get(key)
        if old is not None:
            # the same assessment under another entry: keep the passage that requires it, add the events
            if old.get("kind") not in KINDS_REQUIRE and kind in KINDS_REQUIRE:
                old.update(kind=kind, quote=quote[:400], section=str(it.get("section") or "").strip()[:60])
            old["events"] += [e for e in events if e not in old["events"]]
            if entry_id and entry_id not in old["entries"]:
                old["entries"].append(entry_id)
            if legacy:
                rej("duplicate")
            return old
        if len(out) >= MAX_ASSESSMENTS:
            rej("over the limit")
            return None
        dom, basis = assessment_domain(name, it.get("cdash_domain"), known_domains or None)
        a = {"name": name, "domain": dom, "domain_basis": basis, "section": str(it.get("section") or "").strip()[:60],
             "quote": quote[:400], "events": events, "kind": kind, "entries": [entry_id] if entry_id else [],
             "log": bool(it.get("log")) or bool(_LOG_WORDS.search(name)) or dom in _LOG_DOMAINS}
        by_key[key] = a
        out.append(a)
        return a

    if legacy:
        for it in data["assessments"]:
            take(it, None)
        return {"assessments": out, "rejected": rejected, "coverage": {}, "missing": list(entries)}
    for en in data.get("entries") or []:
        eid = str(en.get("id") or "").strip() if isinstance(en, dict) else ""
        if eid not in entries:
            rej("unknown checklist entry")
            continue
        if eid in coverage:
            rej("checklist entry answered twice")
            continue
        items = en.get("assessments") if isinstance(en.get("assessments"), list) else []
        if items:
            names, dropped = [], 0
            for it in items:
                a = take(it, eid)
                if a is None:
                    dropped += 1
                elif a["name"] not in names:
                    names.append(a["name"])
            coverage[eid] = {"status": "assessments" if names else "assessments (quotes not verified)",
                             "assessments": names, "discarded": dropped}
        elif str(en.get("none") or "").strip():
            coverage[eid] = {"status": "none", "reason": str(en["none"]).strip()[:200], "assessments": []}
        # an entry with neither assessments nor a reason is not an answer: it stays missing
    for it in data.get("extra") if isinstance(data.get("extra"), list) else []:
        take(it, None)
    return {"assessments": out, "rejected": rejected, "coverage": coverage,
            "missing": [eid for eid in entries if eid not in coverage]}


def merge_validated(first, second):
    """The first answer completed by the follow-up answer for the entries it left out."""
    by_key = {_norm_phrase(a["name"]): a for a in first["assessments"]}
    for a in second["assessments"]:
        old = by_key.get(_norm_phrase(a["name"]))
        if old is None:
            first["assessments"].append(a)
            by_key[_norm_phrase(a["name"])] = a
            continue
        if old.get("kind") not in KINDS_REQUIRE and a.get("kind") in KINDS_REQUIRE:
            old.update(kind=a["kind"], quote=a["quote"], section=a["section"])
        old["events"] += [e for e in a["events"] if e not in old["events"]]
        old["entries"] += [e for e in a["entries"] if e not in old["entries"]]
    for k, n in second["rejected"].items():
        first["rejected"][k] = first["rejected"].get(k, 0) + n
    for eid, c in second["coverage"].items():
        first["coverage"].setdefault(eid, c)
    first["missing"] = [eid for eid in first["missing"] if eid not in first["coverage"]]
    return first


async def assess(spec, protocol_text, call, checklist=None, with_text=True):
    """The validated assessments for a protocol. `call(prompt, extra_text)` is the (async) AI call. Entries of the
    checklist the answer leaves out get ONE follow-up call for just those; what is still unanswered stays in
    "missing" and is recorded as "not assessed". None when there is no protocol text."""
    req = build_request(spec, protocol_text, with_text=with_text, checklist=checklist)
    if req is None:
        return None
    sched = (checklist or {}).get("schedule_text") or ""
    v = validate_response(spec, await call(req[0], req[1]), protocol_text, checklist, sched)
    if checklist and v["missing"] and "unparseable response" not in v["rejected"]:
        missing = list(v["missing"])
        _log(f"{len(missing)} checklist entry(ies) not answered, asking again: {', '.join(missing[:40])}")
        try:
            req2 = build_request(spec, protocol_text, with_text=with_text, checklist=checklist, only=missing)
            part = {"entries": [e for e in checklist["entries"] if e["id"] in set(missing)]}
            v = merge_validated(v, validate_response(spec, await call(req2[0], req2[1]), protocol_text, part, sched))
        except Exception as e:
            _log(f"follow-up call failed ({type(e).__name__}); the entries stay not assessed")
        if v["missing"]:
            _log(f"not assessed after the follow-up: {', '.join(v['missing'][:40])}")
    return v


def coverage_records(checklist, validated):
    """One record per checklist entry: what it is and how it was accounted for ("not assessed" when it never was)."""
    out = []
    cov = (validated or {}).get("coverage") or {}
    for e in (checklist or {}).get("entries") or []:
        c = cov.get(e["id"]) or {"status": "not assessed", "assessments": []}
        out.append({"id": e["id"], "type": e["type"], "label": e["label"], "where": e.get("table") or e.get("number") or "",
                    "status": c["status"], "assessments": c.get("assessments") or [], "reason": c.get("reason") or "",
                    "discarded": c.get("discarded") or 0})
    return out


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
    import standards_match as sm
    # by meaning, whatever the domains say: a form titled for this assessment collects it
    best_t = max(forms, key=lambda f: sm.title_meaning(name, f.get("form_title")), default=None)
    if best_t is not None and sm.title_meaning(name, best_t.get("form_title")) >= 0.75:
        return best_t, "form title (by meaning)"
    if _COMPLIANCE.search(name):
        f = _dosing_form(assessment, forms)
        if f is not None:
            return f, "dosing / compliance fields on the administration form"
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
            # ... by its title, by a field that names it, or by questions that name its subject (the word of the
            # assessment no candidate's title has)
            subject = [t for t in a_tok if len(t) >= 5 and not any(_same(t, x) for f in cands for x in _tokens(f.get("form_title")))]
            named = sorted(((_overlap(a_tok, _tokens(f.get("form_title"))) + (1 if _labels_have(f, a_tok) else 0)
                             + (1 if subject and all(_labels_have(f, [t]) for t in subject) else 0), -i, f)
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
        if not a_generic and a_tok:
            # a form of another (or no) domain whose title names this assessment
            for f in forms:
                if f not in cands and _overlap(a_tok, _tokens(f.get("form_title"))) >= 1.0:
                    return f, "named in the form title"
        if not a_generic:
            f = _title_and_fields(a_tok, [f for f in forms if f not in cands])
            if f is not None:
                return f, "named in the form title and its fields"
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
    f = _title_and_fields(a_tok, forms)
    if f is not None:
        return f, "named in the form title and its fields"
    return None, ""


def _title_and_fields(a_tok, forms):
    """The form whose title has at least half of the assessment's words and whose fields name every other one: the
    same data collection under a longer name (the model may name one assessment differently under two entries)."""
    if len(a_tok) < 2:
        return None
    for f in forms:
        ft = _tokens(f.get("form_title"))
        rest = [t for t in a_tok if not any(_same(t, x) for x in ft)]
        if rest and len(rest) * 2 <= len(a_tok) and all(_labels_have(f, [t]) for t in rest):
            return f
    return None


def _dosing_form(assessment, forms):
    """The administration form that records compliance with a treatment: an exposure form (or one titled for
    administration / dosing) with dosing or compliance fields; the one the protocol wording names when several."""
    words = _tokens(assessment["name"]) + _tokens(assessment.get("quote"))
    best = None
    for i, f in enumerate(forms):
        if not ({"EX", "EC"} & set(_form_domains(f)) or _DOSING_TITLE.search(str(f.get("form_title") or ""))):
            continue
        rows = [r for r in f.get("survey") or [] if isinstance(r, dict) and r.get("name")]
        if not any(_DOSING_FIELD.search(f"{r.get('name')} {r.get('label') or ''}") for r in rows):
            continue
        title_tok = [t for t in _tokens(f.get("form_title")) if not _DOSING_TITLE.search(t)]
        named = sum(1 for t in title_tok if any(_same(t, w) for w in words))
        score = (named, 1 if any(_COMPLIANCE.search(f"{r.get('name')} {r.get('label') or ''}") for r in rows) else 0, -i)
        if best is None or score > best[0]:
            best = (score, f)
    return best[1] if best else None


def _death_form(forms):
    """The form that records a death when there is no Death Details form: Disposition, else Adverse Events."""
    for dom in ("DS", "AE"):
        for f in forms:
            if dom in _form_domains(f) or str(f.get("form_id") or "").upper() == dom:
                return f
    return None


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


def fingerprint(protocol_text, sources=None, death=DEATH_PROTOCOL):
    h = hashlib.sha256(_squash(protocol_text).encode()).hexdigest()
    parts = [VERSION, h, (sources or {}).get("fingerprint") or ""]
    if death != DEATH_PROTOCOL:   # the default answer leaves earlier fingerprints valid
        parts.append(death)
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def needs_check(spec, protocol_text, sources=None, death=DEATH_PROTOCOL):
    if not enabled() or not isinstance(spec, dict) or not str(protocol_text or "").strip():
        return False
    return state(spec).get("fingerprint") != fingerprint(protocol_text, sources, death)


def _add(spec, forms, a, sources, crf_forms, note=""):
    form, source, placement = build_form(a, spec, sources, crf_forms)
    spec.setdefault("forms", []).append(form)
    forms.append(form)
    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict) and isinstance(soe.get("form_placements"), list):
        for v in form["visits_assigned"]:
            soe["form_placements"].append({"target_visit_oid": v, "form_id": form["form_id"],
                                           "required": not a.get("log"), "repeating": bool(a.get("log")),
                                           "notes": f"Required by protocol {a.get('section') or ''}".strip()})
    why = note or (f"protocol {('section ' + a['section']) if a.get('section') else 'text'} requires \"{a['name']}\" "
                   f"and no form collected it")
    msg = (f"{form['form_id']} ({form['form_title']}): added because {why}; content from {source}; "
           f"visits: {placement}")
    bucket = spec.setdefault("review_flags", {}).setdefault(FLAG, [])
    if msg not in bucket:
        bucket.append(msg)
    _log(msg)
    return form, source, placement


def apply(spec, assessments, sources=None, crf_forms=None, protocol_text="", rejected=None, death=DEATH_PROTOCOL,
          checklist=None):
    """Map every assessment to a form; add a form for each one nothing covers, provided its protocol passage is an
    instruction to record or a schedule row (an eligibility criterion, a heading, a process description is not a
    basis for a form). Mutates spec; returns the state.
    death: the customer's DEATH_DETAILS_FORM answer (DEATH_PROTOCOL | DEATH_ALWAYS | DEATH_NEVER).
    checklist: coverage_records() of the protocol's schedule rows and procedures headings."""
    forms = [f for f in spec.get("forms") or [] if isinstance(f, dict)]
    records, added, skipped, not_built = [], [], [], []

    def by_standard():
        return {(f.get("customer_standard") or {}).get("form_oid") or (f.get("protocol_required") or {}).get("standard_form"): f
                for f in forms if f.get("customer_standard") or (f.get("protocol_required") or {}).get("standard_form")}

    for a in assessments:
        f, basis = cover(a, forms)
        rec = {"assessment": a["name"], "domain": a.get("domain"), "section": a.get("section") or "",
               "quote": a.get("quote") or "", "log": bool(a.get("log"))}
        if a.get("kind"):
            rec.update(kind=a["kind"], entries=list(a.get("entries") or []))
        requires = a.get("kind") is None or a.get("kind") in KINDS_REQUIRE
        skip = ""
        if f is None and a.get("domain") == "DD":
            asks = bool(_DEATH_DETAILS.search(f"{a['name']} {a.get('quote') or ''}"))
            if death == DEATH_NEVER or (death != DEATH_ALWAYS and not asks):
                f = _death_form(forms)
                basis = ("death is recorded in Disposition and Adverse Events; no Death Details form: "
                         + ("the customer does not use one (DEATH_DETAILS_FORM = Never)" if death == DEATH_NEVER else
                            "the protocol does not ask for death details (cause of death, autopsy, circumstances)"))
                skip = basis
        if f is None and not skip and not requires:
            why = (f"no form built: the protocol text for \"{a['name']}\" is {KIND_WORDS.get(a['kind'], KIND_WORDS['other'])}, "
                   f"not a requirement to record data")
            rec.update(form=None, basis=why, added=False, not_built=True, reason=why)
            not_built.append(rec)
            _log(why + (f" (protocol {a['section']})" if a.get("section") else ""))
            records.append(rec)
            continue
        if f is None:
            # the standard form that would supply the content is already another form's content: that form collects it
            std = _standard_for(a, sources, set())
            holder = by_standard().get(std["form_oid"]) if std else None
            if holder is not None:
                f, basis = holder, f"its content would be the standard form {std['form_oid']}, which {holder.get('form_id')} already uses"
                skip = basis
        if f is not None or skip:
            rec.update(form=f.get("form_id") if f is not None else None, basis=basis, added=False)
            other_domain = bool(a.get("domain")) and f is not None and not (_FAMILY.get(a["domain"], {a["domain"]}) & set(_form_domains(f)))
            if skip or "compliance" in basis or "named in the form title" in basis or ("by meaning" in basis and other_domain):
                rec["skipped_addition"] = True
                skipped.append(rec)
                _log(f"no form added for \"{a['name']}\": covered by {rec['form'] or 'no form'} ({basis})")
        else:
            form, source, placement = _add(spec, forms, a, sources, crf_forms)
            rec.update(form=form["form_id"], basis="added", added=True, content_source=source, placement=placement)
            added.append(rec)
        records.append(rec)
    if death == DEATH_ALWAYS and not any("DD" in _form_domains(f) or str(f.get("form_id") or "").upper() == "DD" for f in forms):
        a = {"name": "Death details", "domain": "DD", "section": "", "quote": "", "events": [], "log": True}
        form, source, placement = _add(spec, forms, a, sources, crf_forms,
                                       note="the customer always collects death details on a separate form "
                                            "(DEATH_DETAILS_FORM = Always)")
        rec = {"assessment": a["name"], "domain": "DD", "section": "", "quote": "", "log": True, "form": form["form_id"],
               "basis": "added", "added": True, "content_source": source, "placement": placement,
               "convention": "DEATH_DETAILS_FORM = Always"}
        records.append(rec)
        added.append(rec)
    st = {"version": VERSION, "fingerprint": fingerprint(protocol_text, sources, death), "status": "done",
          "assessments": records, "added": [r["form"] for r in added], "rejected": dict(rejected or {}),
          "death_details_form": death}
    if checklist is not None:
        st["checklist"] = list(checklist)
        st["not_assessed"] = [c["id"] for c in checklist if c.get("status") == "not assessed"]
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
    for r in recs:
        if r.get("not_built"):
            lines.append(f"  x \"{r['assessment']}\" (protocol {r.get('section') or 'section not given'}): {r.get('reason')}")
    cl = st.get("checklist")
    if cl:
        n = lambda status: sum(1 for c in cl if c["status"].startswith(status))
        lines.append(f"  Checklist from the protocol: {sum(1 for c in cl if c['type'] == 'row')} Schedule of Activities "
                     f"row(s) and {sum(1 for c in cl if c['type'] == 'heading')} procedures heading(s); "
                     f"{n('assessments')} with assessments, {n('none')} none, {n('not assessed')} not assessed.")
        for c in cl:
            if c["status"] == "not assessed":
                lines.append(f"  ? not assessed: {c['type']} {c['where']} \"{c['label']}\"")
    for r in recs:
        if r.get("skipped_addition"):
            lines.append(f"  = \"{r['assessment']}\": no form added, covered by {r.get('form') or 'no form'} ({r.get('basis')})")
    return lines
