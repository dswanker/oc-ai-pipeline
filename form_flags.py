"""Form flag and permission tag PROPOSALS for the Study Configuration (study_config.py). Document only.

  participate      proposed true for a patient-reported instrument (a CDISC QRS questionnaire, or a form the analysis
                   marked as ePRO) when the protocol says the patient completes it
  hidden           proposed true for a form whose data the protocol says is loaded from a vendor (central
                   laboratory, central imaging read)
  permission tag   "Unblinded" for randomization / kit / drug accountability forms of a blinded study; "PII" for
                   contact-detail forms

Each proposal that rests on the protocol carries a verbatim sentence from the protocol text. Nothing is applied: the
form-at-event values stay as they are, and tags are never written to the design board (the link between a form and a
permission tag is not part of the card JSON and is not confirmed). allow_add (repeating forms on Common events) is
a value in the configuration itself, not a proposal.
"""
from __future__ import annotations
import re

SRC_PROTOCOL, SRC_DEFAULT, SRC_AI = "protocol", "pipeline default", "AI-proposed"
TAG_COLORS = {"Unblinded": "red", "PII": "orange"}
_PATIENT = re.compile(r"patient[- ]reported|participant[- ]reported|subject[- ]reported|self[- ]administered|"
                      r"self[- ]reported|completed by the (?:patient|participant|subject)|"
                      r"(?:patient|participant|subject)s? (?:will|must|should|are to|is to) complete|\bepro\b|"
                      r"(?:patient|participant|subject) diary", re.I)
_CENTRAL_LAB = re.compile(r"central laborator", re.I)
_CENTRAL_READ = re.compile(r"central(?:ly)? (?:read|review|imaging)|central reader|independent (?:central )?review|"
                           r"imaging core lab|core laborator", re.I)
_IMAGING = re.compile(r"imaging|\bmri\b|\bct\b|scan|radiograph|echocardiogra|tumou?r assessment|recist", re.I)
_BLINDED = re.compile(r"double[- ]blind|single[- ]blind|triple[- ]blind|observer[- ]blind|\bblinded\b", re.I)
_OPEN = re.compile(r"open[- ]label", re.I)
_UNBLIND_FORM = re.compile(r"randomi[sz]ation|\bkit\b|drug accountability|dispens|unblind|treatment assignment", re.I)
_PII_FORM = re.compile(r"contact (?:details|information)|\baddress\b|telephone|phone number|personal information|\bpii\b", re.I)
_STOP = {"form", "the", "of", "and", "assessment", "questionnaire", "scale", "index", "score", "log", "report"}


def sentences(text):
    """Sentences of the protocol with whitespace collapsed (a quote is a verbatim run of the protocol text)."""
    flat = re.sub(r"\s+", " ", str(text or ""))
    return [s.strip() for s in re.split(r"(?<=[.;:])\s+(?=[A-Z0-9(])", flat) if s.strip()]


def _quote(sents, *patterns, limit=300):
    """The first sentence that matches every pattern, cut to `limit` characters around the first match."""
    for s in sents:
        ms = [p.search(s) if hasattr(p, "search") else re.search(p, s, re.I) for p in patterns]
        if all(ms):
            if len(s) <= limit:
                return s
            start = max(0, min(m.start() for m in ms) - limit // 3)
            return s[start:start + limit].strip()
    return ""


def _title_words(form):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", str(form.get("form_title") or "")) if w.lower() not in _STOP]
    return [w for w in words if len(w) >= 3]


def _is_questionnaire(form):
    if form.get("is_epro"):
        return True, "the protocol analysis marked this form as patient-reported (ePRO)"
    for i in form.get("qrs_instruments") or []:
        if isinstance(i, dict) and str(i.get("domain") or "").upper() == "QS":
            return True, f"CDISC QRS questionnaire {i.get('instrument')}"
    for r in form.get("survey") or []:
        q = r.get("qrs") if isinstance(r, dict) else None
        if isinstance(q, dict) and str(q.get("domain") or "").upper() == "QS":
            return True, f"CDISC QRS questionnaire {q.get('instrument_name') or q.get('instrument')}"
    return False, ""


def _domains(form):
    doms = {d for d in re.split(r"[^A-Z0-9]+", str(form.get("cdash_domain") or "").upper()) if d}
    cs = form.get("customer_standard") or {}
    if cs.get("domain"):
        doms.add(str(cs["domain"]).upper())
    return doms


def propose(spec, protocol_text=""):
    """{"proposals": [...], "permission_tags": [...]}. Deterministic; needs the protocol text for anything that
    claims the protocol says so."""
    sents = sentences(protocol_text)
    out, tags = [], {}
    blinded_quote = "" if not sents else _quote(sents, _BLINDED)
    open_label = bool(sents) and any(_OPEN.search(s) for s in sents[:400]) and not blinded_quote
    lab_quote = _quote(sents, _CENTRAL_LAB) if sents else ""
    read_quote = _quote(sents, _CENTRAL_READ) if sents else ""
    for form in spec.get("forms") or []:
        if not isinstance(form, dict) or not form.get("form_id"):
            continue
        fid, title = form["form_id"], str(form.get("form_title") or form["form_id"])
        target = f"{fid} ({title}), every event"
        # participate
        is_q, why = _is_questionnaire(form)
        if is_q:
            q = ""
            for w in _title_words(form):
                q = _quote(sents, re.compile(rf"\b{re.escape(w)}", re.I), _PATIENT)
                if q:
                    break
            if q:
                out.append({"kind": "participate", "target": target, "proposed": True, "current": False,
                            "source": SRC_PROTOCOL, "quote": q,
                            "rationale": f"Patient-completed instrument ({why}): offer the form to the participant "
                                         f"(OpenClinica Participate)."})
            elif form.get("is_epro"):
                out.append({"kind": "participate", "target": target, "proposed": True, "current": False,
                            "source": SRC_AI, "quote": "",
                            "rationale": f"{why}; no protocol sentence stating that the patient completes it was "
                                         f"found: confirm before enabling Participate."})
        # hidden: loaded from a vendor
        doms = _domains(form)
        if lab_quote and "LB" in doms:
            out.append({"kind": "hidden", "target": target, "proposed": True, "current": False,
                        "source": SRC_PROTOCOL, "quote": lab_quote,
                        "rationale": "The protocol uses a central laboratory: if these results are loaded from the "
                                     "vendor, hide the form from site data entry. Confirm which tests are central."})
        elif read_quote and (_IMAGING.search(title) or doms & {"TU", "TR", "RS"}):
            out.append({"kind": "hidden", "target": target, "proposed": True, "current": False,
                        "source": SRC_PROTOCOL, "quote": read_quote,
                        "rationale": "The protocol uses a central / independent read: if the read is loaded from "
                                     "the vendor, hide the form from site data entry."})
        # permission tags
        if _UNBLIND_FORM.search(title) or "DA" in doms:
            if blinded_quote:
                tags["Unblinded"] = True
                out.append({"kind": "permission tag", "target": target, "proposed": "Unblinded", "current": None,
                            "source": SRC_PROTOCOL, "quote": blinded_quote,
                            "rationale": "Blinded study: randomization, kit and drug accountability data can "
                                         "unblind; restrict the form to unblinded roles."})
            elif not open_label and not sents:
                pass  # no protocol text: blinding unknown, nothing proposed
        if _PII_FORM.search(title):
            tags["PII"] = True
            out.append({"kind": "permission tag", "target": target, "proposed": "PII", "current": None,
                        "source": SRC_DEFAULT, "quote": "",
                        "rationale": "Contact details are personal data: restrict the form to roles that need them."})
    if tags:
        out.append({"kind": "permission tag link", "target": "all forms with a proposed tag", "proposed": "set in the study designer",
                    "current": None, "source": SRC_DEFAULT, "quote": "",
                    "rationale": "UNRESOLVED: a tag is created as a board label {name, color, isConfigPermission: true, "
                                 "type: \"Form\"}, but the link between a form and its tag is not in the card JSON and "
                                 "is not confirmed. Tags are therefore not written to the design board."})
    return {"proposals": out,
            "permission_tags": [{"name": n, "color": TAG_COLORS.get(n, "gray"), "isConfigPermission": True, "type": "Form"}
                                for n in sorted(tags)]}
