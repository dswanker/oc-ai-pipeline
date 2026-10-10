"""
logic_coverage.py — logic-coverage audit and generation for an OpenClinica study.

For every form and field it decides which categories of a deterministic catalog apply (by field type and CDASH
concept, never by wording), whether each is already covered by logic in the form, already proposed, or missing,
and generates the missing checks from templates. A generated check is built on the form (Draft in the DVS) or
listed as a proposal (Status Proposed), by one rule:

  * a form the pipeline built, or a customer standard / existing form that carries NO logic of its own: applied;
  * a form that carries its own logic: proposed (a data manager's Approve adds it);
  * a check marked "review" (true for most studies, not for every one): always proposed;
  * report mode (an existing customer build under test): everything is proposed, nothing is changed.

Checks are written through the conventions engine's own effects (add_constraint, lookup_from), so they stack
with existing constraints, are idempotent and carry a stable check id. Derived helper items of structure-only
(ODM) forms are never left as free text: the calculation is rebuilt when the study itself shows it, otherwise the
item is made read-only and flagged.

  run(spec, mode="apply" | "report") -> summary dict; results in spec["study_meta"]["logic_coverage"]
  section(spec)   -> block for the Study Specification
  sheet_rows(spec)-> rows of the LOGIC_COVERAGE sheet of the DVS
No network, no AI call. Protocol-specific checks come from the pipeline's validated AI edit-check call and are
counted here as proposed.
"""
import copy
import hashlib
import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

VERSION = 1
SOURCE = "Logic Coverage"
CONV = "logic_coverage."
_NON_DATA = ("note", "begin group", "end group", "begin repeat", "end repeat", "begin_group", "end_group",
             "begin_repeat", "end_repeat")
_REF = re.compile(r"\$\{(\w+)\}")
_NUM_CMP = re.compile(r"[<>]=?\s*-?\d")

# (id, level, title, business purpose). The order is the order of the report.
CATEGORIES = [
    ("REQUIRED", "Field", "Required (CDASH highly recommended)", "Key data is not left blank."),
    ("DATE_FUTURE", "Field", "Date not in the future", "A recorded date cannot be later than the day of entry."),
    ("PARTIAL_DATE", "Field", "Partial date parts in range", "Year, month and day parts are plausible."),
    ("NUM_RANGE", "Field", "Numeric range", "Out-of-range numbers are caught at entry."),
    ("OTHER_SPECIFY", "Field", "Other, specify shown with Other", "The specify text is collected only with Other."),
    ("GATE", "Field", "Detail fields behind a yes/no or performed gate",
     "Details are collected only when the assessment or event occurred."),
    ("NOT_DONE_REASON", "Within form", "Not done requires a reason", "A missed assessment is explained."),
    ("START_END", "Within form", "End on or after start", "An end date cannot precede its start date."),
    ("ONGOING_END", "Within form", "Ongoing versus end date",
     "No end date while ongoing; an end date once it is not."),
    ("CLSIG_DESC", "Within form", "Clinically significant requires a description",
     "A clinically significant finding is described."),
    ("SERIOUS_CRITERIA", "Within form", "Serious requires a seriousness criterion",
     "A serious event states why it is serious."),
    ("FATAL_DEATH", "Within form", "Fatal outcome consistent with death", "Outcome and death details agree."),
    ("CONSENT_FLOOR", "Cross form", "On-study dates on or after informed consent",
     "No study activity is recorded before consent."),
    ("HISTORY_BEFORE_CONSENT", "Cross form", "History starts before consent",
     "A condition that starts after consent is an adverse event, not history."),
    ("AE_FIRST_DOSE", "Cross form", "Adverse event start versus first dose",
     "Events before the first dose are identified as pre-treatment."),
    ("DEATH_CONSISTENCY", "Cross form", "Death consistent across forms",
     "Death recorded on one form agrees with the others."),
    ("DOSING_WINDOW", "Cross form", "Dosing date within the visit window", "Dosing follows the schedule."),
    ("ELIGIBILITY_DOSING", "Cross form", "Dosing only when eligible", "Only eligible participants are dosed."),
    ("PREGNANCY_DOSING", "Cross form", "Pregnancy dates versus dosing", "Exposure in pregnancy is dated against dosing."),
    ("PROTOCOL_SPECIFIC", "Protocol", "Protocol-specific checks (validated AI proposals)",
     "Checks the protocol implies beyond the catalog."),
    ("DERIVED_ITEM", "Field", "Derived helper items are not free text",
     "A derived value is calculated or read-only, never typed in."),
]
_CAT = {c[0]: c for c in CATEGORIES}

# CDISC CORE rules that translate to a data-entry check, by the category that implements them.
CORE_RULES = {"NOT_DONE_REASON": "CORE-000440", "SERIOUS_CRITERIA": "CORE-000022",
              "DEATH_CONSISTENCY": "CORE-000034", "FATAL_DEATH": "SDTM.AEOUT_FATAL_AESDTH"}
CORE_DOSE_GATE = "CORE-000004/137"     # dose details only when the intervention occurred (a GATE on --OCCUR)

# Physiological defaults by CDISC vital signs test code: (low, high, unit they are stated in).
VS_RANGES = {"SYSBP": (60, 250, "mmHg"), "DIABP": (30, 150, "mmHg"), "PULSE": (30, 200, "beats/min"),
             "HR": (30, 200, "beats/min"), "RESP": (5, 60, "breaths/min"), "TEMP": (34, 42, "C"),
             "HEIGHT": (100, 250, "cm"), "WEIGHT": (20, 300, "kg"), "OXYSAT": (50, 100, "%"), "BMI": (10, 80, "kg/m2")}
# The same range in the other unit a site may record, keyed by CDISC unit submission value (upper case).
VS_ALT_UNITS = {"TEMP": ("F", 93, 108), "HEIGHT": ("IN", 39, 99), "WEIGHT": ("LB", 44, 660)}
# CDASH domains whose assessment / event dates are on study by definition (same set as the engine's consent rule).
ON_STUDY = {"AE", "EX", "EC", "LB", "VS", "EG", "PE", "QS", "MB", "PC", "BE", "BS", "DS", "DV", "DD", "IE"}
CONSENT_NAMES = ("ICFDAT", "RFICDAT", "CONSDAT", "ICDAT")
PART_SUFFIXES = {"YEAR": "Y", "YYYY": "Y", "YR": "Y", "MONTH": "M", "MON": "M", "MM": "M", "DAY": "D", "DD": "D"}
SERIOUS_CRITERIA = ("AESDTH", "AESLIFE", "AESHOSP", "AESDISAB", "AESCONG", "AESMIE")


def enabled():
    """LOGIC_COVERAGE=0 turns the whole step off."""
    return os.environ.get("LOGIC_COVERAGE", "1").strip() != "0"


# ── reading a form ────────────────────────────────────────────────────────────

def _t(row):
    return str(row.get("type") or "").strip().lower()


def _base(row):
    return _t(row).split(" ")[0]


def is_data(row):
    return isinstance(row, dict) and bool(row.get("name")) and _t(row) not in _NON_DATA


def data_rows(form):
    return [r for r in form.get("survey") or [] if is_data(r)]


def _fid(form):
    return str(form.get("form_id") or "")


def _calc(row):
    return _base(row) == "calculate" or bool(str(row.get("calculation") or "").strip())


def _entered(row):
    """A field a user types or picks: not calculated, not read-only."""
    return not _calc(row) and str(row.get("readonly") or "").strip().lower() not in ("yes", "true", "1")


def _concept(row):
    return str(row.get("concept") or "").strip().upper()


def _qual(row):
    return str(row.get("concept_qualifier") or "").strip().upper()


def _suffix(concept):
    """The CDASH variable fragment after the two-letter domain (AESTDAT -> STDAT); '' for a domain-less name."""
    return concept[2:] if len(concept) > 4 else ""


def _domain(concept):
    return concept[:2] if len(concept) > 4 else ""


def _codes(form, row):
    t = str(row.get("type") or "")
    if " " not in t:
        return []
    ln = t.split(" ", 1)[1].strip()
    return [c for c in form.get("choices") or [] if isinstance(c, dict) and str(c.get("list_name") or "").strip() == ln]


def _code_for(form, row, value):
    """The code this field's own list uses for a CDISC submission value / code (None when it has none)."""
    want = str(value).upper()
    for c in _codes(form, row):
        keys = {str(c.get("name") or "").strip().upper(), str(c.get("cdisc_submission_value") or "").strip().upper()}
        if want in keys:
            return str(c.get("name"))
    return None


def _yn(form, row):
    from conventions_engine.applies_when import _yes_no_codes
    codes = _yes_no_codes(row, form)
    return codes.get("_yes_code"), codes.get("_no_code")


def _sel(row, code):
    """Expression "this select holds `code`" for a select_one or select_multiple."""
    name = row["name"]
    return f"selected(${{{name}}}, '{code}')" if _base(row) == "select_multiple" else f"${{{name}}} = '{code}'"


def logic_free(form):
    """True when checks are built on this form rather than proposed (see the module docstring)."""
    if not form.get("customer_standard"):
        return True
    from conventions_engine import customer_standard as cs
    return cs.apply_when_logic_free() and cs.logic_free(form)


def _own(row, check):
    """True when this check on this row was added by an earlier run of the skill."""
    if check["cid"] in (row.get("edit_checks") or []):
        return True
    key = {"Conditional Display": "relevant", "Required": "required"}.get(check["check_type"])
    return bool(key) and str((row.get("edit_check_set_by") or {}).get(key) or "") == CONV + check["category"]


# ── a check: what it is, whether something covers it, how it is built ─────────

def _source(category, core=None):
    core = core or CORE_RULES.get(category, "")
    return f"CDISC CORE ({core})" if core.startswith("CORE-") else SOURCE


def _check(category, form, row, check_type, logic, message, covers, do, review=False, note="", cid=None,
           reference="", cross=None, core=None):
    core = core or CORE_RULES.get(category, "")
    return {"category": category, "form": _fid(form), "field": row["name"], "check_type": check_type,
            "logic": logic, "message": message, "covers": covers, "do": do, "review": review, "note": note,
            "cid": cid or f"LC.{category}", "cross": cross,
            "source": _source(category, core),
            "reference": reference or (core if core else ""), "purpose": _CAT[category][3]}


def _constraint(category, form, row, expr, message, covers, lookup=None, requires=None, **kw):
    def do(f, r, spec):
        from conventions_engine import EntityContext, effects
        cid = kw.get("cid") or f"LC.{category}"
        effect = {"add_constraint": {"expr": expr, "message": message, "check_id": cid}}
        if requires:
            effect["add_constraint"]["requires_field"] = requires
        if lookup:
            effect = {"lookup_from": lookup, **effect}
        ctx = EntityContext(kind="field", entity=r, parent=f, spec=spec, path=f"logic_coverage:{cid}")
        effects.apply_effect(effect, ctx, spec, CONV + category)
        return cid in (r.get("edit_checks") or [])
    return _check(category, form, row, "Cross-form" if lookup else "Constraint", expr, message, covers, do,
                  cross=(lookup or {}).get("from"), **kw)


def _relevant(category, form, row, expr, covers, required=False, **kw):
    label = _source(category, kw.get("core"))

    def do(f, r, spec):
        if str(r.get("relevant") or "").strip():
            return False
        r["relevant"] = expr
        r.setdefault("edit_check_set_by", {})["relevant"] = CONV + category
        r.setdefault("edit_check_source", {})["relevant"] = label
        if required and str(r.get("required") or "").strip().lower() != "yes":
            r["required"] = "yes"
            r.setdefault("edit_check_set_by", {})["required"] = CONV + category
            r.setdefault("edit_check_source", {})["required"] = label
        return True
    return _check(category, form, row, "Conditional Display", expr, "", covers, do, **kw)


def _required(category, form, row, **kw):
    def do(f, r, spec):
        r["required"] = "yes"
        r.setdefault("edit_check_set_by", {})["required"] = CONV + category
        r.setdefault("edit_check_source", {})["required"] = SOURCE
        return True
    return _check(category, form, row, "Required", "required = yes", "",
                  lambda r, text: str(r.get("required") or "").strip().lower() == "yes", do, **kw)


def _in_constraint(token):
    return lambda r, text: token in text


def _refs_relevant(name):
    return lambda r, text: ("${" + name + "}") in text


# ── the catalog: one finder per category; each yields the checks that APPLY to the form ──

def _find_required(spec, form):
    for r in data_rows(form):
        core = str((r.get("cdash") or {}).get("core") or "").upper()
        if _entered(r) and core.split("/")[0] == "HR":
            yield _required("REQUIRED", form, r, review=True, reference=f"CDASHIG {(r.get('cdash') or {}).get('variable', '')} (Highly Recommended)",
                            note="CDASH marks the item Highly Recommended; whether it is mandatory is a study decision.")


def _find_date_future(spec, form):
    for r in data_rows(form):
        if _base(r) not in ("date", "datetime") or not _entered(r):
            continue
        con = str(r.get("constraint") or "")
        if re.search(r">=?\s*(today|now)\(\)", con):
            continue                      # a date that must lie ahead (a planned date): the rule does not apply
        fn = "now()" if _base(r) == "datetime" else "today()"
        yield _constraint("DATE_FUTURE", form, r, f". <= {fn}", "Date cannot be in the future.",
                          lambda row, text: "today()" in text or "now()" in text)


def _parts(form):
    """{row name: (part letter, base name)} for the parts of a partial date: at least two siblings that share a
    base name and end in a year / month / day part."""
    by_base = {}
    for r in data_rows(form):
        name = str(r["name"])
        base, _, suffix = name.rpartition("_")
        part = PART_SUFFIXES.get(suffix.upper())
        if base and part:
            by_base.setdefault(base, {})[part] = name
    return {name: (part, base) for base, parts in by_base.items() if len(parts) >= 2 for part, name in parts.items()}


def _find_partial_date(spec, form):
    parts = _parts(form)
    for r in data_rows(form):
        if r["name"] not in parts or _base(r) not in ("integer", "decimal") or not _entered(r):
            continue
        part = parts[r["name"]][0]
        expr, msg = {"Y": (". >= 1900 and . <= number(format-date(today(), '%Y'))", "Year must be between 1900 and the current year."),
                     "M": (". >= 1 and . <= 12", "Month must be between 1 and 12."),
                     "D": (". >= 1 and . <= 31", "Day must be between 1 and 31.")}[part]
        yield _constraint("PARTIAL_DATE", form, r, expr, msg, lambda row, text: bool(_NUM_CMP.search(text)),
                          cid=f"LC.PARTIAL_DATE.{part}")


def _unit_row(form, row):
    """The unit item recorded with a result: the --ORRESU concept with the same qualifier."""
    c, q = _concept(row), _qual(row)
    if not c.endswith("ORRES"):
        return None
    return next((u for u in data_rows(form) if _concept(u) == c + "U" and _qual(u) == q
                 and _base(u).startswith("select")), None)


def _vs_range(form, row):
    test = _qual(row)
    if test not in VS_RANGES:
        return None
    lo, hi, unit = VS_RANGES[test]
    expr, msg = f". >= {lo} and . <= {hi}", f"Value is outside the expected range ({lo} to {hi} {unit}). Verify entry."
    alt, urow = VS_ALT_UNITS.get(test), _unit_row(form, row)
    if alt and urow is not None:
        alt_code = _code_for(form, urow, alt[0])
        if alt_code is not None:                      # the form offers the other unit: one range per unit
            u = urow["name"]
            expr = (f"(${{{u}}} = '{alt_code}' and . >= {alt[1]} and . <= {alt[2]}) or "
                    f"(${{{u}}} != '{alt_code}' and . >= {lo} and . <= {hi})")
            msg = (f"Value is outside the expected range ({lo} to {hi} {unit}, or {alt[1]} to {alt[2]} "
                   f"{alt[0].lower()}). Verify entry.")
    return expr, msg, f"physiological default for {test}"


def _find_num_range(spec, form):
    parts = _parts(form)
    for r in data_rows(form):
        if _base(r) not in ("integer", "decimal") or not _entered(r) or r["name"] in parts:
            continue
        covers = lambda row, text: bool(_NUM_CMP.search(text))
        c = _concept(r)
        got = _vs_range(form, r) if c == "VSORRES" else None
        if got:
            yield _constraint("NUM_RANGE", form, r, got[0], got[1], covers, reference=got[2])
        elif c == "AGE":
            yield _constraint("NUM_RANGE", form, r, ". >= 0 and . <= 120", "Age must be between 0 and 120.", covers,
                              reference="plausible human age")
        elif _base(r) == "integer" or c.endswith(("ORRES", "DOSE", "DOSTOT")):
            yield _constraint("NUM_RANGE", form, r, ". >= 0", "Value cannot be negative.", covers,
                              reference="a count, result or dose is not negative")
        else:
            yield _check("NUM_RANGE", form, r, "Constraint", "", "", covers, None,
                         note="no range template: a decimal without a CDASH concept (set its range from the protocol)")


def _find_other_specify(spec, form):
    rows = data_rows(form)
    for i, r in enumerate(rows):
        if not _base(r).startswith("select"):
            continue
        code = _code_for(form, r, "OTHER") or _code_for(form, r, "OTH")
        if code is None:
            continue
        name, c = str(r["name"]), _concept(r)
        spec_row = next((s for s in rows[i + 1:i + 3] if _base(s) == "text" and _entered(s) and (
            (c and _concept(s) == c + "OTH") or (str(s["name"]).upper().startswith(name.upper()) and s["name"] != name))), None)
        if spec_row is None:
            continue
        yield _relevant("OTHER_SPECIFY", form, spec_row, _sel(r, code), _refs_relevant(name),
                        reference=f"shown with {name} = {code}")


def _gates(form):
    """[(gate row, yes code, no code, detail rows, reason-not-done row)] for the yes/no or performed prompts of a
    form: a select_one whose CDASH concept is a --YN / --PERF / --OCCUR prompt. Its details are the entered rows
    that follow it in the same item group, up to the next prompt, and that carry a CDASH concept of the same
    domain: an item whose meaning is not known is never hidden (it could be the reason the assessment was not
    done)."""
    rows, out = data_rows(form), []
    is_gate = lambda r: _base(r) == "select_one" and _suffix(_concept(r)) in ("YN", "PERF", "OCCUR")
    for i, g in enumerate(rows):
        if not is_gate(g):
            continue
        yes, no = _yn(form, g)
        if not yes or not no:
            continue
        group, details, reason = g.get("bind__oc_itemgroup"), [], None
        for r in rows[i + 1:]:
            if is_gate(r):
                break
            if r.get("bind__oc_itemgroup") != group:
                continue
            if _suffix(_concept(r)) == "REASND":
                reason = r
            elif _concept(r) and _domain(_concept(r)) == _domain(_concept(g)):
                details.append(r)
        out.append((g, yes, no, [d for d in details if _entered(d)], reason))
    return out


def _find_gate(spec, form):
    for g, yes, _no, details, _reason in _gates(form):
        dose_gate = _suffix(_concept(g)) == "OCCUR" and _domain(_concept(g)) in ("EX", "EC")
        for d in details:
            yield _relevant("GATE", form, d, f"${{{g['name']}}} = '{yes}'",
                            lambda row, text: bool(text.strip()),          # an author's own show-when counts
                            core=CORE_DOSE_GATE if dose_gate else None,
                            reference=(f"{CORE_DOSE_GATE}: " if dose_gate else "") + f"collected when {g['name']} = {yes}")


def _find_not_done_reason(spec, form):
    for g, _yes, no, _details, reason in _gates(form):
        if reason is not None:
            yield _relevant("NOT_DONE_REASON", form, reason, f"${{{g['name']}}} = '{no}'", _refs_relevant(g["name"]),
                            required=True,
                            reference=f"CORE-000440: reason when {g['name']} = {no}")


def _pairs(form, a_suffix, b_suffix, b_type=None):
    """[(row A, row B)] whose CDASH concepts share a domain and qualifier and end in the two fragments."""
    rows = data_rows(form)
    for a in rows:
        ca = _concept(a)
        if _suffix(ca) != a_suffix:
            continue
        b = next((r for r in rows if _concept(r) == _domain(ca) + b_suffix and _qual(r) == _qual(a)
                  and (b_type is None or _base(r).startswith(b_type))), None)
        if b is not None:
            yield a, b


def _find_start_end(spec, form):
    for start, end in _pairs(form, "STDAT", "ENDAT"):
        if _base(start) in ("date", "datetime") and _base(end) in ("date", "datetime") and _entered(end):
            s = start["name"]
            yield _constraint("START_END", form, end, f". = '' or ${{{s}}} = '' or . >= ${{{s}}}",
                              "End date must be on or after the start date.", _in_constraint("${" + s + "}"),
                              requires=s, reference=f"{end['name']} >= {s}")


def _find_ongoing_end(spec, form):
    for ongo, end in _pairs(form, "ONGO", "ENDAT"):
        _yes, no = _yn(form, ongo)
        if no and _base(end) in ("date", "datetime") and _entered(end):
            yield _relevant("ONGOING_END", form, end, f"${{{ongo['name']}}} = '{no}'", _refs_relevant(ongo["name"]),
                            required=True, reference=f"end date when {ongo['name']} = {no}")


def _find_clsig_desc(spec, form):
    for sig, desc in _pairs(form, "CLSIG", "DESC"):
        yes, _no = _yn(form, sig)
        if yes and _base(desc) == "text" and _entered(desc):
            yield _relevant("CLSIG_DESC", form, desc, f"${{{sig['name']}}} = '{yes}'", _refs_relevant(sig["name"]),
                            required=True, reference=f"description when {sig['name']} = {yes}")


def _by_concept(form, concept):
    return next((r for r in data_rows(form) if _concept(r) == concept), None)


def _find_serious_criteria(spec, form):
    ser = _by_concept(form, "AESER")
    if ser is None:
        return
    yes, _no = _yn(form, ser)
    crit = [(r, _yn(form, r)[0]) for c in SERIOUS_CRITERIA for r in [_by_concept(form, c)] if r is not None]
    crit = [(r, y) for r, y in crit if y]
    if yes and crit:
        expr = f". != '{yes}' or (" + " or ".join(f"${{{r['name']}}} = '{y}'" for r, y in crit) + ")"
        yield _constraint("SERIOUS_CRITERIA", form, ser, expr,
                          "A serious event needs at least one seriousness criterion.",
                          lambda row, text: any(("${" + r["name"] + "}") in text for r, _y in crit),
                          cid="LC.CORE-000022", reference="CORE-000022")


def _find_fatal_death(spec, form):
    out, dth = _by_concept(form, "AEOUT"), _by_concept(form, "AESDTH")
    if out is None or dth is None:
        return
    fatal, (yes, _no) = _code_for(form, out, "FATAL"), _yn(form, dth)
    if fatal and yes:
        yield _constraint("FATAL_DEATH", form, out, f". != '{fatal}' or ${{{dth['name']}}} = '{yes}'",
                          "A fatal outcome requires 'results in death' to be Yes.",
                          _in_constraint("${" + dth["name"] + "}"), cid="LC.AEOUT_FATAL_AESDTH",
                          requires=dth["name"], reference="SDTM: AEOUT FATAL implies AESDTH Y")


def _forms(spec):
    return [f for f in spec.get("forms") or [] if isinstance(f, dict)]


def _study_field(spec, test):
    """(form, row) of the first field in the study that satisfies test(form, row)."""
    for f in _forms(spec):
        for r in data_rows(f):
            if test(f, r):
                return f, r
    return None, None


def _consent(spec):
    return _study_field(spec, lambda f, r: _base(r) in ("date", "datetime") and (
        _concept(r) == "RFICDAT" or str(r["name"]).upper() in CONSENT_NAMES))


def _first_dose(spec):
    return _study_field(spec, lambda f, r: _base(r) in ("date", "datetime") and _concept(r) in ("EXSTDAT", "ECSTDAT"))


def _lookup(src_form, src_row, helper, purpose, first_event=False):
    lk = {"from": f"{_fid(src_form)}.{src_row['name']}", "name": helper, "purpose": purpose}
    visits = src_form.get("visits_assigned") or []
    if first_event and visits:
        lk["event"] = visits[0]
    return lk


def _is_date(r):
    return _base(r) in ("date", "datetime") and _entered(r)


def _find_consent_floor(spec, form):
    cf, cr = _consent(spec)
    if cr is None or cf is form or any(_concept(r) == "RFICDAT" or str(r["name"]).upper() in CONSENT_NAMES
                                       for r in data_rows(form) if _base(r) in ("date", "datetime")):
        return
    for r in data_rows(form):
        c = _concept(r)
        if not _is_date(r) or _suffix(c) not in ("DAT", "STDAT", "ENDAT") or _domain(c) in ("MH", "CM", "PR", "SU", "DM"):
            continue
        on_study = _domain(c) in ON_STUDY
        yield _constraint("CONSENT_FLOOR", form, r, ". = '' or ${ICFDAT_CF} = '' or . >= ${ICFDAT_CF}",
                          "Date cannot be before the informed consent date.", _in_constraint("${ICFDAT_CF}"),
                          lookup=_lookup(cf, cr, "ICFDAT_CF", "Informed consent date, for the consent-date floor check"),
                          requires="ICFDAT_CF", review=not on_study,
                          note="" if on_study else ("the domain can hold assessments made before consent (history, "
                                                    "diagnosis): confirm against the protocol before approving"),
                          reference=f"{r['name']} >= {_fid(cf)}.{cr['name']}")


def _find_history_before_consent(spec, form):
    cf, cr = _consent(spec)
    if cr is None or cf is form:
        return
    for r in data_rows(form):
        if _is_date(r) and _concept(r) == "MHSTDAT":
            yield _constraint("HISTORY_BEFORE_CONSENT", form, r, ". = '' or ${ICFDAT_CF} = '' or . <= ${ICFDAT_CF}",
                              "Medical history starts on or before the informed consent date; a later condition is an adverse event.",
                              _in_constraint("${ICFDAT_CF}"), review=True,
                              lookup=_lookup(cf, cr, "ICFDAT_CF", "Informed consent date, for the history check"),
                              requires="ICFDAT_CF", reference=f"{r['name']} <= {_fid(cf)}.{cr['name']}",
                              note="CDASH: medical history is what precedes the study; confirm the protocol's cut-off (consent or first dose)")


def _find_ae_first_dose(spec, form):
    df, dr = _first_dose(spec)
    if dr is None or df is form:
        return
    r = _by_concept(form, "AESTDAT")
    if r is not None and _is_date(r):
        yield _constraint("AE_FIRST_DOSE", form, r, ". = '' or ${FIRSTDOSE_CF} = '' or . >= ${FIRSTDOSE_CF}",
                          "The event starts before the first dose: confirm it is a pre-treatment event.",
                          _in_constraint("${FIRSTDOSE_CF}"), review=True,
                          lookup=_lookup(df, dr, "FIRSTDOSE_CF", "First dose date, to identify pre-treatment events", True),
                          requires="FIRSTDOSE_CF", reference=f"{r['name']} >= {_fid(df)}.{dr['name']}",
                          note="events before the first dose are legitimate where the protocol collects them from consent: "
                               "a flag for review, not an error")


def _find_death_consistency(spec, form):
    """A death date recorded on this form must equal the death date recorded on another form (CORE-000034)."""
    mine = _by_concept(form, "DTHDAT")
    if mine is None or not _is_date(mine):
        return
    of, orow = _study_field(spec, lambda f, r: f is not form and _concept(r) == "DTHDAT" and _base(r) in ("date", "datetime"))
    if orow is not None:
        yield _constraint("DEATH_CONSISTENCY", form, mine, ". = '' or ${DTHDAT_CF} = '' or . = ${DTHDAT_CF}",
                          f"Date of death differs from the date of death on {of.get('form_title') or _fid(of)}.",
                          _in_constraint("${DTHDAT_CF}"), cid="LC.CORE-000034",
                          lookup=_lookup(of, orow, "DTHDAT_CF", "Date of death on the other form, for consistency"),
                          requires="DTHDAT_CF", reference=f"CORE-000034: {mine['name']} = {_fid(of)}.{orow['name']}")


def _find_dosing_window(spec, form):
    """Applies to dosing dates; generated only when the specification carries visit windows (none do today)."""
    for r in data_rows(form):
        if _is_date(r) and _concept(r) in ("EXSTDAT", "ECSTDAT"):
            yield _check("DOSING_WINDOW", form, r, "Cross-form", "", "", lambda row, text: False, None,
                         note="not generated: the specification holds no visit windows; the calendaring rules "
                              "(scheduler) are where a visit window is enforced")


def _find_eligibility_dosing(spec, form):
    ef, er = _study_field(spec, lambda f, r: _concept(r) == "IEYN" and _base(r) == "select_one")
    if er is None or ef is form:
        return
    yes, _no = _yn(ef, er)
    r = next((x for x in data_rows(form) if _concept(x) in ("EXSTDAT", "ECSTDAT") and _is_date(x)), None)
    if yes and r is not None:
        yield _constraint("ELIGIBILITY_DOSING", form, r, f". = '' or ${{IEYN_CF}} = '' or ${{IEYN_CF}} = '{yes}'",
                          "A dose is recorded for a participant who did not meet the eligibility criteria.",
                          _in_constraint("${IEYN_CF}"), review=True,
                          lookup=_lookup(ef, er, "IEYN_CF", "Eligibility verdict, for the dosing check"),
                          requires="IEYN_CF", reference=f"dosing only when {_fid(ef)}.{er['name']} = {yes}",
                          note="confirm the protocol has no waiver or re-screening path before approving")


def _find_pregnancy_dosing(spec, form):
    df, dr = _first_dose(spec)
    if dr is None or df is form or str(form.get("cdash_domain") or "").upper() != "RP":
        return
    for r in data_rows(form):
        if _is_date(r) and _suffix(_concept(r)) in ("DAT", "STDAT"):
            yield _constraint("PREGNANCY_DOSING", form, r, ". = '' or ${FIRSTDOSE_CF} = '' or . >= ${FIRSTDOSE_CF}",
                              "The date is before the first dose: confirm the pregnancy is reportable.",
                              _in_constraint("${FIRSTDOSE_CF}"), review=True,
                              lookup=_lookup(df, dr, "FIRSTDOSE_CF", "First dose date, for the pregnancy check", True),
                              requires="FIRSTDOSE_CF", reference=f"{r['name']} >= {_fid(df)}.{dr['name']}")


# Run order. A field has one show-when: the specific rules (other/specify, reason not done, end date when not
# ongoing, description when significant) come before the general gate, which then finds those fields covered.
FINDERS = [("REQUIRED", _find_required), ("DATE_FUTURE", _find_date_future), ("PARTIAL_DATE", _find_partial_date),
           ("NUM_RANGE", _find_num_range), ("OTHER_SPECIFY", _find_other_specify),
           ("NOT_DONE_REASON", _find_not_done_reason), ("START_END", _find_start_end),
           ("ONGOING_END", _find_ongoing_end), ("CLSIG_DESC", _find_clsig_desc), ("GATE", _find_gate),
           ("SERIOUS_CRITERIA", _find_serious_criteria), ("FATAL_DEATH", _find_fatal_death),
           ("CONSENT_FLOOR", _find_consent_floor), ("HISTORY_BEFORE_CONSENT", _find_history_before_consent),
           ("AE_FIRST_DOSE", _find_ae_first_dose), ("DEATH_CONSISTENCY", _find_death_consistency),
           ("DOSING_WINDOW", _find_dosing_window), ("ELIGIBILITY_DOSING", _find_eligibility_dosing),
           ("PREGNANCY_DOSING", _find_pregnancy_dosing)]


# ── derived helper items ──────────────────────────────────────────────────────

def _structure_only(form):
    """A form that came as structure without logic: an ODM, or an XLSForm with no logic of its own. Only there
    can an item that should be calculated have ended up as a plain question."""
    if not form.get("customer_standard"):
        return False
    from conventions_engine import customer_standard as cs
    return cs.logic_free(form)


def _is_helper(row):
    """A derived helper of a structure-only form: an item with no user-facing label (none, or just its own name)
    and no calculation. In an ODM a calculated item has no question text; nothing a user answers is label-less."""
    if not is_data(row) or _base(row) not in ("text", "integer", "decimal", "date", "datetime", "time") \
            or str(row.get("calculation") or "").strip():
        return False
    if str(row.get("required") or "").strip().lower() == "yes":
        return False                      # a user must answer it: not a derived value, whatever its label
    label = re.sub(r"<[^>]+>|\*", "", str(row.get("label") or "")).strip()
    return not label or label == str(row["name"])


def _calc_donor(spec, form, row):
    """A same-named item on another form of the study that IS calculated, with the helper rows its calculation
    needs: the study itself shows how the value is derived."""
    for f in _forms(spec):
        if f is form:
            continue
        rows = {r.get("name"): r for r in f.get("survey") or [] if isinstance(r, dict) and r.get("name")}
        d = rows.get(row["name"])
        calc = str((d or {}).get("calculation") or "").strip()
        if not calc:
            continue
        need, ok = [], True
        for ref in dict.fromkeys(_REF.findall(calc)):
            src = rows.get(ref)
            if src is None or not str(src.get("calculation") or "").strip() or _REF.findall(str(src.get("calculation"))):
                ok = False
                break
            need.append(src)
        if ok:
            return f, d, need
    return None, None, []


def _handle_helpers(spec, form, apply):
    """Derived helper items of a structure-only form. Returns [{form, item, action, detail}]."""
    out = []
    if not _structure_only(form):
        return out
    # Default REPORT ONLY. Verified 2026-10-10 on the PrTK05 ODM: the "label-less" items of BIOSPECIMENC are real
    # data-entry fields (status, collection time, operator, cryovial count) whose labels begin with HTML markup;
    # making them read-only would block data entry. LOGIC_COVERAGE_HELPERS=1 applies the changes once the
    # detection is proven against a customer's original forms.
    if os.environ.get("LOGIC_COVERAGE_HELPERS", "0").strip() != "1":
        apply = False                     # report them, change none
    survey = form.get("survey") or []
    referenced = {n for r in survey if isinstance(r, dict)
                  for k in ("calculation", "relevant", "constraint") for n in _REF.findall(str(r.get(k) or ""))}
    for row in list(survey):
        if not isinstance(row, dict):
            continue
        done = row.get("logic_coverage_helper")
        if done:
            out.append({"form": _fid(form), "item": row["name"], "action": done["action"], "detail": done["detail"]})
            continue
        if not _is_helper(row):
            continue
        donor_form, donor, need = _calc_donor(spec, form, row)
        if donor is not None:
            action = "calculation rebuilt"
            detail = f"same item is calculated on {_fid(donor_form)}: {str(donor.get('calculation'))[:120]}"
        else:
            action = "made read-only"
            detail = ("no calculation could be derived from the study; read-only so it is not typed in"
                      + ("; it is referenced by other logic" if row["name"] in referenced else ""))
        if not apply:
            out.append({"form": _fid(form), "item": row["name"], "action": "reported (" + action + " when applied)",
                        "detail": detail})
            continue
        if donor is not None:
            have = {r.get("name") for r in survey if isinstance(r, dict)}
            idx = survey.index(row)
            for src in need:
                if src.get("name") not in have:
                    survey.insert(idx, {k: copy.deepcopy(v) for k, v in src.items()
                                        if k in ("type", "name", "label", "calculation", "bind__oc_external")}
                                  | {"library_source": "PROTOCOL_SPECIFIC", "completion_status": "COMPLETE",
                                     "standard_proposal": "LC.DERIVED_ITEM"})
                    idx += 1
            row["calculation"] = donor["calculation"]
        row["readonly"] = "yes"
        row["completion_status"], row["flag_reason"] = "FLAGGED", f"Derived helper item: {action}. {detail}"
        row["logic_coverage_helper"] = {"action": action, "detail": detail}
        cs = form.get("customer_standard") or {}
        cs.setdefault("approved", []).append({"id": f"LC.DERIVED_ITEM.{row['name']}", "convention_id": CONV + "DERIVED_ITEM",
                                              "field": row["name"], "source": SOURCE, "check_type": "Calculation",
                                              "auto": "Applied: a derived value is never left as free text."})
        out.append({"form": _fid(form), "item": row["name"], "action": action, "detail": detail})
    return out


# ── proposals ─────────────────────────────────────────────────────────────────

def state(spec):
    return (spec.get("study_meta") or {}).get("logic_coverage") or {}


def proposals(spec):
    return state(spec).get("proposals") or []


def _existing_proposals(spec):
    """{(form, field): [logic text]} of the proposals other steps already made (engine, AI)."""
    out = {}
    sm = spec.get("study_meta") or {}
    for p in ((sm.get("standards_match") or {}).get("proposals") or []) + ((sm.get("ai_edit_checks") or {}).get("proposals") or []):
        if isinstance(p, dict) and p.get("target_field"):
            out.setdefault((_norm(p.get("target_form")), p.get("target_field")), []).append(str(p.get("logic") or ""))
    return out


def _norm(fid):
    s = str(fid or "").upper()
    return s[2:] if s.startswith("F_") else s


def _propose(spec, form, check):
    """The check as a proposal: its effect on a copy of the form, recorded as the operations Approve replays."""
    from conventions_engine import customer_standard as cs
    shadow = copy.deepcopy(form)
    shadow_spec = dict(spec)
    shadow_spec["forms"] = [shadow if f is form else f for f in spec.get("forms") or []]
    shadow_spec["review_flags"] = copy.deepcopy(spec.get("review_flags") or {})
    srow = next(r for r in shadow["survey"] if isinstance(r, dict) and r.get("name") == check["field"])
    if not check["do"](shadow, srow, shadow_spec):
        return None
    ops = cs._diff(form, shadow)
    if not ops:
        return None
    for op in ops:
        if op.get("op") == "row" and "constraint" in (op.get("set") or {}):
            op["clause"], op["message"] = check["logic"], check["message"]
    pid = "LCP-" + hashlib.sha1(f"{check['category']}|{check['form']}|{check['field']}|{check['cid']}".encode()).hexdigest()[:8].upper()
    why = (check["note"] or "review before building") if check["review"] else (
        "the form carries its own logic, so the audit does not change it")
    return {"id": pid, "kind": "coverage", "convention_id": CONV + check["category"], "check_id": check["cid"],
            "source": check["source"], "title": _CAT[check["category"]][2], "category": check["category"],
            "target_form": check["form"], "target_field": check["field"],
            "check_type": "Constraint" if check["check_type"] == "Cross-form" else check["check_type"],
            "logic": check["logic"], "message": check["message"], "rationale": check["purpose"],
            "protocol_reference": check["reference"], "cross_form": check["cross"],
            "note": f"Proposed by the logic coverage audit: {why}. ", "ops": ops}


def _record_applied(form, row, check):
    if check["check_type"] in ("Constraint", "Cross-form"):
        if not any(isinstance(d, dict) and d.get("id") == check["cid"] for d in row.get("edit_check_details") or []):
            row.setdefault("edit_check_details", []).append(
                {"id": check["cid"], "source": check["source"], "category": check["category"],
                 "rationale": check["purpose"], "protocol_reference": check["reference"]})
    cs = form.get("customer_standard")
    if isinstance(cs, dict):
        aid = f"LC.{check['category']}.{check['field']}"
        if not any(a.get("id") == aid for a in cs.get("approved") or []):
            from conventions_engine import customer_standard as _cs
            cs.setdefault("approved", []).append({
                "id": aid, "convention_id": CONV + check["category"], "field": check["field"],
                "source": check["source"], "check_id": check["cid"], "check_type": check["check_type"],
                "auto": _cs.LOGIC_FREE_REASON.format(source=cs.get("source") or "structure-only source")})


# ── the run ───────────────────────────────────────────────────────────────────

def _text_for(row, check):
    key = {"Conditional Display": "relevant", "Required": "required"}.get(check["check_type"], "constraint")
    return str(row.get(key) or "")


def run(spec, mode="apply"):
    """Audit the study and fill the gaps. mode "apply": build or propose by the rule in the module docstring;
    "report": propose everything, change no form. Idempotent. Returns the summary stored in
    spec["study_meta"]["logic_coverage"]."""
    if not isinstance(spec, dict):
        return {}
    sm = spec.setdefault("study_meta", {})
    prev = sm.get("logic_coverage") if isinstance(sm.get("logic_coverage"), dict) else {}
    rejected = set(prev.get("rejected_ids") or [])
    existing = _existing_proposals(spec)
    counts, added, new_props, not_generated, helpers = {}, [], [], [], []

    def bump(fid, cat, key, field):
        c = counts.setdefault((fid, cat), {"applicable": 0, "covered": 0, "proposed_before": 0, "added": 0,
                                           "proposed": 0, "missing": 0, "examples": []})
        c[key] += 1
        if key != "applicable" and key != "covered" and len(c["examples"]) < 3 and field not in c["examples"]:
            c["examples"].append(field)

    for form in _forms(spec):
        fid = _fid(form)
        can_apply = mode == "apply" and logic_free(form)
        for h in _handle_helpers(spec, form, can_apply):
            helpers.append(h)
            bump(fid, "DERIVED_ITEM", "applicable", h["item"])
            bump(fid, "DERIVED_ITEM", "added" if not h["action"].startswith("reported") else "missing", h["item"])
        for cat, finder in FINDERS:
            for check in list(finder(spec, form)):
                row = next((r for r in form.get("survey") or [] if isinstance(r, dict) and r.get("name") == check["field"]), None)
                if row is None:
                    continue
                bump(fid, cat, "applicable", check["field"])
                if _own(row, check):
                    added.append(_public(check))
                    bump(fid, cat, "added", check["field"])
                    continue
                if check["covers"](row, _text_for(row, check)):
                    bump(fid, cat, "covered", check["field"])
                    continue
                if any(check["covers"](row, t) for t in existing.get((_norm(fid), check["field"]), []) if t):
                    bump(fid, cat, "proposed_before", check["field"])
                    continue
                if check["do"] is None:
                    not_generated.append({**_public(check), "reason": check["note"]})
                    bump(fid, cat, "missing", check["field"])
                    continue
                if can_apply and not check["review"]:
                    try:
                        ok = check["do"](form, row, spec)
                    except Exception as e:
                        ok, check["note"] = False, f"could not be built ({type(e).__name__})"
                    if ok:
                        _record_applied(form, row, check)
                        added.append(_public(check))
                        bump(fid, cat, "added", check["field"])
                        continue
                    not_generated.append({**_public(check), "reason": check["note"] or "the form does not allow it "
                                          "(a referenced field is missing or the field already has a show-when)"})
                    bump(fid, cat, "missing", check["field"])
                    continue
                try:
                    prop = _propose(spec, form, check)
                except Exception:
                    prop = None
                if prop is None or prop["id"] in rejected:
                    not_generated.append({**_public(check), "reason": "rejected earlier" if prop else "no change results"})
                    bump(fid, cat, "missing", check["field"])
                    continue
                new_props.append(prop)
                bump(fid, cat, "proposed", check["field"])
    ai = ((sm.get("ai_edit_checks") or {}).get("proposals") or [])
    by_norm = {_norm(_fid(f)): _fid(f) for f in _forms(spec)}
    for p in ai:
        fid = by_norm.get(_norm(p.get("target_form")), str(p.get("target_form")))
        bump(fid, "PROTOCOL_SPECIFIC", "applicable", p.get("target_field"))
        bump(fid, "PROTOCOL_SPECIFIC", "proposed_before", p.get("target_field"))

    order = {c[0]: i for i, c in enumerate(CATEGORIES)}
    form_ids = [_fid(f) for f in _forms(spec)]
    rows = []
    for fid in form_ids:
        for cat, level, title, _p in CATEGORIES:
            c = counts.get((fid, cat))
            rows.append({"form": fid, "category": cat, "level": level, "title": title,
                         **(c or {"applicable": 0, "covered": 0, "proposed_before": 0, "added": 0, "proposed": 0,
                                  "missing": 0, "examples": []})})
    totals = {}
    for r in rows:
        t = totals.setdefault(r["category"], {k: 0 for k in ("applicable", "covered", "proposed_before", "added", "proposed", "missing")})
        for k in t:
            t[k] += r[k]
    result = {
        "version": VERSION, "mode": mode, "rows": rows, "totals": totals,
        "added": added, "proposals": new_props, "rejected_ids": sorted(rejected), "not_generated": not_generated,
        "helpers": helpers,
        "cross_form": [a for a in added if a.get("cross")] + [{**_public_prop(p), "status": "proposed"} for p in new_props if p.get("cross_form")],
        "core_rules": sorted({str(x.get("reference") or "").split(":")[0] for x in added
                              if str(x.get("reference") or "").startswith("CORE-")}
                             | {str(p.get("protocol_reference") or "").split(":")[0] for p in new_props
                                if str(p.get("protocol_reference") or "").startswith("CORE-")}),
        "counts": {"applied": len(added), "proposed": len(new_props),
                   "helpers": len(helpers), "not_generated": len(not_generated),
                   "applicable": sum(t["applicable"] for t in totals.values()),
                   "covered_before": sum(t["covered"] for t in totals.values())},
        "category_order": sorted(order, key=order.get),
    }
    sm["logic_coverage"] = result
    return result


def _public(check):
    return {k: check[k] for k in ("category", "form", "field", "check_type", "logic", "message", "source",
                                  "reference", "cross", "cid")}


def _public_prop(p):
    return {"category": p.get("category"), "form": p.get("target_form"), "field": p.get("target_field"),
            "check_type": p.get("check_type"), "logic": p.get("logic"), "message": p.get("message"),
            "source": p.get("source"), "reference": p.get("protocol_reference"), "cross": p.get("cross_form"),
            "cid": p.get("check_id")}


# ── reporting ─────────────────────────────────────────────────────────────────

def _status(r):
    if not r["applicable"]:
        return "Not applicable"
    parts = [f"{r[k]} {label}" for k, label in (("covered", "covered"), ("proposed_before", "proposed"),
                                                ("added", "added"), ("proposed", "newly proposed"),
                                                ("missing", "missing")) if r[k]]
    return ", ".join(parts)


SHEET_HEADERS = ["Form", "Level", "Category", "Applicable", "Covered by existing logic", "Already proposed",
                 "Added (built, Draft)", "Newly proposed", "Missing", "Status", "Examples"]


def sheet_rows(spec):
    """Rows of the LOGIC_COVERAGE sheet: one per form and category, then the derived helper items."""
    st = state(spec)
    rows = [[r["form"], r["level"], r["title"], r["applicable"], r["covered"], r["proposed_before"], r["added"],
             r["proposed"], r["missing"], _status(r), ", ".join(str(x) for x in r["examples"])] for r in st.get("rows") or []]
    return rows


def helper_rows(spec):
    return [[h["form"], h["item"], h["action"], h["detail"]] for h in state(spec).get("helpers") or []]


def summary_lines(spec):
    st = state(spec)
    if not st:
        return []
    c = st["counts"]
    t = st["totals"]
    helpers = {}
    for h in st.get("helpers") or []:
        helpers[h["action"].split(" (")[0]] = helpers.get(h["action"].split(" (")[0], 0) + 1
    lines = [f"Logic coverage ({st.get('mode')}): {c['applicable']} applicable checks in the catalog; "
             f"{c['covered_before']} already covered, {c['applied']} added (Draft), {c['proposed']} proposed, "
             f"{c['not_generated']} missing without a template."]
    top = [(k, v) for k, v in t.items() if v["added"] or v["proposed"]]
    if top:
        lines.append("  By category: " + "; ".join(f"{_CAT[k][2]}: {v['added']} added, {v['proposed']} proposed"
                                                    for k, v in top))
    if helpers:
        lines.append("  Derived helper items: " + ", ".join(f"{n} {k}" for k, n in sorted(helpers.items())) + ".")
    return lines


def section(spec):
    """(title, note, headers, rows, column weights) for the Study Specification, or None before a run."""
    st = state(spec)
    if not st.get("totals"):
        return None
    rows = []
    for cat, level, title, _p in CATEGORIES:
        t = st["totals"].get(cat)
        if t and t["applicable"]:
            rows.append([level, title, t["applicable"], t["covered"], t["proposed_before"], t["added"], t["proposed"], t["missing"]])
    return ("LOGIC COVERAGE", "which checks of the catalog apply to this study and where each stands; per form in "
            "the DVS (LOGIC_COVERAGE sheet)", ["Level", "Category", "Applicable", "Covered", "Already proposed",
                                               "Added", "Newly proposed", "Missing"], rows, [3, 9, 2, 2, 2, 2, 2, 2])
