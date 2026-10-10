"""edit_check_meta.py: metadata for every check written into the DVS (DVS_OC4 sheet): where it came from (Check
Source + rule/proposal id), a plain-English description, rationale/protocol reference, and the cross-form source it
reads. Used by the DVS extractor (skills/dvs-specification) so DVS_OC4 is the single editable list of checks:
build checks + AI-proposed checks (Status Proposed until a DM approves) + DM-added rows (Action = Add).
"""
import json, os, re

ACTIONS = ["Delete", "Change", "Approve", "Reject", "Add"]

_REF = re.compile(r"\$\{(\w+)\}")
_NON_DATA = ("note", "begin group", "end group", "begin repeat", "end repeat")


def _global_ids():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conventions_engine", "conventions", "global")
    try:
        return {f[:-5] for f in os.listdir(d) if f.endswith(".json")}
    except Exception:
        return set()


def source_of_check_id(cid):
    c = str(cid or "")
    if c.startswith("CORE-"):
        return f"CDISC CORE ({c})"
    if c.startswith(("SDTM.", "CDISC.")):
        return "CDISC Standard"
    if c.startswith("AI."):
        return "AI-Proposed"
    if c.startswith("DM."):
        return "DM-Added"
    return "Global Rule"


def source_of_convention(conv_id, global_ids):
    c = str(conv_id or "")
    if not c:
        return "Study Build"
    if "cdisc_reason_not_done" in c:
        return "CDISC CORE (CORE-000440)"
    if "cdisc_dose_only_when_occurred" in c:
        return "CDISC CORE (CORE-000004/137)"
    if "cdisc_" in c:
        return "CDISC Standard"
    return "Global Rule" if c in global_ids else "Customer Rule"


def _split_and(expr):
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills", "dvs-specification", "scripts"))
        from extract_dvs_from_forms import _split_and_clauses
        return _split_and_clauses(expr) or [expr]
    except Exception:
        return [expr]


class _FormCtx:
    """Labels, choice labels and cross-form helpers for one form, used to describe logic in plain English."""

    def __init__(self, form, survey, all_forms):
        self.form = form
        self.survey = survey
        self.labels = {r.get("name"): str(r.get("label") or r.get("name")) for r in survey if isinstance(r, dict)}
        self.types = {r.get("name"): str(r.get("type") or "").split(" ")[0].lower() for r in survey if isinstance(r, dict)}
        self.choices = {}
        for r in survey:
            t = str((r or {}).get("type") or "")
            if isinstance(r, dict) and t.startswith("select") and " " in t:
                ln = t.split(" ", 1)[1].strip()
                self.choices[r.get("name")] = {str(c.get("name")): str(c.get("label") or c.get("name"))
                                               for c in form.get("choices") or [] if c.get("list_name") == ln}
        self.helpers = {}
        for r in survey:
            calc = str((r or {}).get("calculation") or "") if isinstance(r, dict) else ""
            m = re.search(r"FormOID='([^']+)'.*?ItemName='([^']+)'", calc)
            if m and "instance('clinicaldata')" in calc:
                ev = re.search(r"StudyEventOID='([^']+)'", calc)
                src_label = m.group(2)
                for f in all_forms:
                    fid = str(f.get("form_id") or "")
                    if m.group(1) in (fid, "F_" + fid):
                        src_label = next((str(x.get("label") or x.get("name")) for x in f.get("survey") or []
                                          if isinstance(x, dict) and x.get("name") == m.group(2)), src_label)
                self.helpers[r.get("name")] = (m.group(1), m.group(2), ev.group(1) if ev else "any visit", src_label)

    def lab(self, name):
        if name in self.helpers:
            fo, item, _ev, label = self.helpers[name]
            return f"{label} ({fo})"
        return self.labels.get(name, name)

    def choice(self, name, code):
        return self.choices.get(name, {}).get(code, code)

    def dependency(self, text):
        deps = [f"{fo}.{item} ({ev})" for n in _REF.findall(str(text or "")) if n in self.helpers
                for fo, item, ev, _l in [self.helpers[n]]]
        return "; ".join(dict.fromkeys(deps))


_WORDS = {">=": ("on or after", "at least"), ">": ("after", "greater than"), "<=": ("on or before", "at most"),
          "<": ("before", "less than"), "=": ("equal to", "equal to"), "!=": ("different from", "different from")}


def describe(clause, field, cx):
    """Plain-English description of one check clause on `field` (deterministic patterns; else a pointer)."""
    e = " ".join(str(clause or "").split())
    me = cx.lab(field)
    is_date = cx.types.get(field) in ("date", "datetime")
    m = re.match(r"^\$\{(\w+)\}\s*!=\s*'([^']*)'\s+or\s+\((.*)\)$", e)
    if m:
        return f"When {cx.lab(m.group(1))} is {cx.choice(m.group(1), m.group(2))}: " + describe(m.group(3), field, cx)
    m = re.match(r"^\$\{(\w+)\}\s*!=\s*'([^']*)'\s+or\s+\$\{(\w+)\}\s*=\s*''\s+or\s+\.\s*=\s*\$\{\3\}$", e)
    if m:
        return f"When {cx.lab(m.group(1))} is {cx.choice(m.group(1), m.group(2))}, {me} must equal {cx.lab(m.group(3))}."
    m = (re.match(r"^\.\s*=\s*''\s+or\s+\$\{(\w+)\}\s*=\s*''\s+or\s+\.\s*(>=|>|<=|<|=|!=)\s*\$\{\1\}$", e)
         or re.match(r"^\.\s*(>=|>|<=|<|=|!=)\s*\$\{(\w+)\}$", e))
    if m:
        ref, op = (m.group(1), m.group(2)) if m.re.pattern.startswith("^\\.\\s*=\\s*''") else (m.group(2), m.group(1))
        return f"{me} must be {_WORDS[op][0 if is_date else 1]} {cx.lab(ref)}."
    m = re.match(r"^\.\s*(<=|<|>=|>)\s*today\(\)$", e)
    if m:
        return {"<=": f"{me} cannot be in the future.", "<": f"{me} must be before today.",
                ">=": f"{me} must be today or later.", ">": f"{me} must be in the future."}[m.group(1)]
    m = re.match(r"^\.\s*(=|!=)\s*'([^']*)'$", e)
    if m:
        return f"{me} must {'be' if m.group(1) == '=' else 'not be'} {cx.choice(field, m.group(2))}."
    m = re.match(r"^\.\s*!=\s*'([^']*)'\s+or\s+\((.*)\)$", e)
    if m:
        parts = re.findall(r"\$\{(\w+)\}\s*=\s*'([^']*)'", m.group(2))
        if parts and " and " not in m.group(2):
            return (f"If {me} is {cx.choice(field, m.group(1))}, at least one of "
                    f"{', '.join(cx.lab(n) for n, _ in parts)} must be {cx.choice(parts[0][0], parts[0][1])}.")
    m = re.match(r"^\.\s*!=\s*'([^']*)'\s+or\s+\$\{(\w+)\}\s*=\s*'([^']*)'$", e)
    if m:
        return (f"If {me} is {cx.choice(field, m.group(1))}, {cx.lab(m.group(2))} must be "
                f"{cx.choice(m.group(2), m.group(3))}.")
    m = re.match(r"^\(?\.\s*(>=|>)\s*(-?[\d.]+)\s+and\s+\.\s*(<=|<)\s*(-?[\d.]+)\)?$", e)
    if m:
        return f"{me} must be between {m.group(2)} and {m.group(4)}."
    m = re.match(r"^\.\s*(>=|>|<=|<)\s*(-?[\d.]+)$", e)
    if m:
        return f"{me} must be {_WORDS[m.group(1)][1]} {m.group(2)}."
    return "Custom logic (see Logic column)."


def describe_relevant(rel, cx):
    e = " ".join(str(rel or "").split())
    m = re.match(r"^\$\{(\w+)\}\s*=\s*'([^']*)'$", e)
    if m:
        return f"Shown only when {cx.lab(m.group(1))} is {cx.choice(m.group(1), m.group(2))}."
    m = re.match(r"^selected\(\s*\$\{(\w+)\}\s*,\s*'([^']*)'\s*\)$", e)
    if m:
        return f"Shown only when {cx.lab(m.group(1))} includes {cx.choice(m.group(1), m.group(2))}."
    return "Shown only when its condition is met (see Logic column)."




def check_meta(struct_form, struct_row, clause, check_type, all_forms):
    """Metadata for one DVS check on struct_row. check_type: Constraint | Required | Conditional Display | other.
    Returns {source, rule_id, plain, rationale, protocol_reference, item_standard}."""
    gids = _global_ids()
    survey = [r for r in (struct_form or {}).get("survey") or [] if isinstance(r, dict)]
    cx = _FormCtx(struct_form or {}, survey, all_forms or [])
    row = struct_row or {}
    name = row.get("name")
    set_by = row.get("edit_check_set_by") or {}
    out = {"source": "Study Build", "rule_id": "", "plain": "", "rationale": "", "protocol_reference": ""}
    try:  # questionnaire items carry their instrument and response codelist (cdisc_qrs.py)
        import cdisc_qrs
        out["item_standard"] = cdisc_qrs.describe(row)
    except Exception:
        out["item_standard"] = ""
    if check_type == "Constraint":
        exprs = row.get("edit_check_exprs") or {}
        cid = next((k for k, v in exprs.items() if v and " ".join(str(v).split()) == " ".join(str(clause).split())), None)
        det = next((d for d in row.get("edit_check_details") or [] if isinstance(d, dict) and d.get("id") == cid), {})
        out["source"] = det.get("source") or (source_of_check_id(cid) if cid else
                                              source_of_convention(set_by.get("constraint"), gids))
        out["rule_id"] = cid or ""
        out["rationale"] = det.get("rationale") or ""
        out["protocol_reference"] = det.get("protocol_reference") or ""
        out["plain"] = describe(clause, name, cx)
    elif check_type == "Conditional Display":
        out["source"] = source_of_convention(set_by.get("relevant"), gids)
        out["plain"] = describe_relevant(clause, cx)
    elif check_type == "Required":
        out["source"] = source_of_convention(set_by.get("required"), gids)
        out["plain"] = f"{cx.lab(name)} is required" + (" when shown." if row.get("relevant") else ".")
    if (struct_form or {}).get("customer_standard"):
        # A customer standard form's own logic is the customer's; logic a DM approved keeps its proposal's source.
        kind = {"Conditional Display": "relevant", "Required": "required"}.get(check_type)
        approved = (row.get("edit_check_source") or {}).get(kind) if kind else None
        if approved:
            out["source"] = approved
        elif out["source"] == "Study Build" and row.get("provenance") != "Added from protocol":
            out["source"] = "Customer Standard"
    out["applied_reason"] = ""
    if (struct_form or {}).get("customer_standard"):
        try:  # a check built on a logic-free customer standard says so (conventions_engine.customer_standard)
            from conventions_engine import customer_standard as _cs
            kind = {"Conditional Display": "relevant", "Required": "required"}.get(check_type)
            out["applied_reason"] = _cs.applied_reason(
                struct_form, name, check_type,
                check_id=out["rule_id"] or None,
                convention_id=(set_by.get(kind) if kind else None))
        except Exception:
            pass
    if out["source"].startswith("CDISC CORE (") and not out["rule_id"]:
        out["rule_id"] = out["source"][len("CDISC CORE ("):-1]
    return out
