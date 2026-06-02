"""
extract_dvs_from_forms.py — Mechanical DVS extractor with inferred UAT data

Walks a forms_json structure and builds a dvs_data dict ready for
generate_dvs.build_dvs(). The DVS is a MIRROR of the actual XLSForm content,
not an invention of new checks from the protocol.

For UAT_Cases, this extractor parses each constraint expression and emits
concrete test inputs. Range checks expand into 5 cases (below / at-min /
mid / at-max / above). Non-range checks emit 2+ cases with concrete sample
data.
"""

import re
from datetime import date, timedelta


# ── Baseline timeline for cross-form date references ─────────────────────────
# Used by the reference-bound date branch to produce concrete dates instead
# of "Set X to a base date first..." instructions. Tester populates each
# baseline field once; all dependent constraint checks reuse the same value.

_BASELINE_TIMELINE = {
    "ICFDAT":    "2026-01-20",
    "ICFDAT_CF": "2026-01-20",
    "ENRLDAT":   "2026-01-22",
    "RANDDAT":   "2026-01-22",
    "AESTDAT":   "2026-02-10",
    "AEENDAT":   "2026-02-15",
    "CMSTDAT":   "2026-01-15",
    "CMENDAT":   "2026-03-01",
    "EXSTDAT":   "2026-01-25",
    "EXENDAT":   "2026-03-20",
    "MHSTDAT":   "2020-06-01",
    "DSDAT":     "2026-04-01",
    "VSDAT":     "2026-01-22",
    "LBDAT":     "2026-01-20",
    "DOB":       "1974-03-22",
    "BRTHDAT":   "1974-03-22",
    "VISITDAT":  "2026-02-01",
    "PEDAT":     "2026-02-01",
}


def _date_offset(iso_date, days):
    """Return ISO date string offset by N days from given ISO date."""
    try:
        y, m, d = map(int, iso_date.split('-'))
        return (date(y, m, d) + timedelta(days=days)).isoformat()
    except (ValueError, AttributeError):
        return iso_date  # If unparseable, return unchanged


def _get_or_set_base_date(field_name, world):
    """
    Get a base date for a referenced field. Checks the per-run world state
    first (so the same field gets the same value across cases), then falls
    back to the global baseline timeline, then allocates a new date.
    """
    fu = (field_name or "").upper()
    if fu in world:
        return world[fu]
    if fu in _BASELINE_TIMELINE:
        world[fu] = _BASELINE_TIMELINE[fu]
        return world[fu]
    # Allocate sensibly for unknown date fields — use mid-2026
    world[fu] = "2026-03-15"
    return world[fu]



# ── Protocol-context-aware sample data ───────────────────────────────────────

def _build_sample_context(study_meta):
    """
    Build a field-name → sample-value lookup tailored to the protocol.

    Uses study_meta fields (therapeutic_area, indication, study_title,
    study_phase) to pick realistic values. Falls back to generic clinical
    defaults for unrecognised fields.
    """
    ta   = (study_meta.get("therapeutic_area") or "").lower()
    ind  = (study_meta.get("indication")       or "").lower()
    titl = (study_meta.get("study_title")      or "").lower()

    # ── Determine protocol domain ─────────────────────────────────────────────
    is_pain      = any(w in ind+titl for w in ("pain","spine","lumbar","cryo","nerve","ablation","facet"))
    is_onco      = any(w in ta+ind   for w in ("oncol","cancer","tumor","tumour","leukemia","lymphoma"))
    is_cardio    = any(w in ta+ind   for w in ("cardio","heart","coronary","hypertension","cardiac"))
    is_neuro     = any(w in ta+ind   for w in ("neuro","alzheimer","parkinson","epilepsy","seizure","stroke"))
    is_derm      = any(w in ta+ind   for w in ("derm","skin","psoriasis","eczema","atopic"))
    is_resp      = any(w in ta+ind   for w in ("pulmon","respiratory","asthma","copd","lung"))
    is_rheum     = any(w in ta+ind   for w in ("rheum","arthritis","lupus","fibromyalgia"))

    # ── Domain-specific values ────────────────────────────────────────────────
    if is_pain:
        ae_terms   = ["Injection site pain", "Headache", "Muscle spasm",
                      "Numbness at treatment site", "Bruising"]
        cm_drugs   = ["Ibuprofen 400mg", "Acetaminophen 500mg",
                      "Gabapentin 300mg", "Cyclobenzaprine 5mg"]
        mh_terms   = ["Chronic low back pain", "Hypertension", "Type 2 diabetes"]
        indication = ind or "chronic low back pain"
        body_site  = "lumbar spine"
    elif is_onco:
        ae_terms   = ["Nausea", "Fatigue", "Neutropenia", "Alopecia", "Anemia"]
        cm_drugs   = ["Ondansetron 8mg", "Dexamethasone 4mg",
                      "Filgrastim 300mcg", "Loratadine 10mg"]
        mh_terms   = ["Hypertension", "Type 2 diabetes", "Prior chemotherapy"]
        indication = ind or "malignant neoplasm"
        body_site  = "site of malignancy"
    elif is_cardio:
        ae_terms   = ["Palpitations", "Dyspnea", "Peripheral edema",
                      "Dizziness", "Chest discomfort"]
        cm_drugs   = ["Atorvastatin 40mg", "Metoprolol 25mg",
                      "Aspirin 81mg", "Lisinopril 10mg"]
        mh_terms   = ["Hypertension", "Hyperlipidemia", "Prior MI"]
        indication = ind or "cardiovascular disease"
        body_site  = "chest"
    elif is_neuro:
        ae_terms   = ["Dizziness", "Headache", "Fatigue",
                      "Cognitive impairment", "Insomnia"]
        cm_drugs   = ["Levetiracetam 500mg", "Memantine 10mg",
                      "Donepezil 5mg", "Sertraline 50mg"]
        mh_terms   = ["Depression", "Hypertension", "Sleep disorder"]
        indication = ind or "neurological disorder"
        body_site  = "head"
    elif is_resp:
        ae_terms   = ["Cough", "Dyspnea", "Upper respiratory infection",
                      "Throat irritation", "Wheezing"]
        cm_drugs   = ["Salbutamol 100mcg inhaler", "Fluticasone 250mcg",
                      "Montelukast 10mg", "Tiotropium 18mcg"]
        mh_terms   = ["Asthma", "Seasonal allergies", "GERD"]
        indication = ind or "respiratory disease"
        body_site  = "chest"
    else:
        ae_terms   = ["Headache", "Nausea", "Fatigue",
                      "Injection site reaction", "Dizziness"]
        cm_drugs   = ["Ibuprofen 400mg", "Paracetamol 500mg",
                      "Omeprazole 20mg", "Loratadine 10mg"]
        mh_terms   = ["Hypertension", "Type 2 diabetes", "Seasonal allergies"]
        indication = ind or "study indication"
        body_site  = "affected area"

    # ── Field-name prefix → sample value mapping ──────────────────────────────
    # Keys are lowercased field-name prefixes (matched with startswith).
    # Order matters — more specific prefixes first.
    ctx = {
        # AE domain
        "aeterm":    ae_terms[0],
        "aedecod":   ae_terms[0],
        "aesev":     "2",
        "aeser":     "0",
        "aerel":     "1",
        "aeout":     "1",
        "aeacn":     "0",
        "aestdat":   "2026-02-10",
        "aeendat":   "2026-02-15",
        "aeongo":    "0",
        "aeid":      "AE-001",
        "aebodsys":  "Musculoskeletal and connective tissue disorders",

        # CM domain
        "cmtrt":     cm_drugs[0].split()[0],
        "cmdose":    cm_drugs[0].split()[1] if len(cm_drugs[0].split()) > 1 else "1",
        "cmdosu":    "mg",
        "cmroute":   "oral",
        "cmfreq":    "BID",
        "cmstdat":   "2026-01-15",
        "cmendat":   "2026-03-01",
        "cmongo":    "0",
        "cmindic":   indication,
        "cmindc":    indication,

        # DM domain
        "age":       "52",
        "sex":       "M",
        "race":      "1",
        "ethnic":    "2",
        "country":   "USA",
        "dob":       "1974-03-22",
        "subjid":    "UAT-001",

        # MH domain
        "mhterm":    mh_terms[0],
        "mhdecod":   mh_terms[0],
        "mhbodsys":  "General disorders",
        "mhstdat":   "2020-06-01",
        "mhongo":    "1",

        # VS domain
        "sysbp":     "122",
        "diabp":     "78",
        "pulse":     "72",
        "temp":      "36.8",
        "weight":    "82",
        "height":    "175",
        "bmi":       "26.8",
        "resp":      "16",
        "vspos":     "supine",
        "vsorres":   "122",
        "vsorresu":  "mmHg",

        # LB domain
        "lbtest":    "Hemoglobin",
        "lbtestcd":  "HGB",
        "lborres":   "13.5",
        "lborresu":  "g/dL",
        "lbdat":     "2026-01-20",
        "lbnrind":   "Normal",

        # EX/dosing
        "extrt":     "Study drug",
        "exdose":    "10",
        "exdosu":    "mg",
        "exroute":   "oral",
        "exstdat":   "2026-01-25",
        "exendat":   "2026-03-20",

        # IE domain
        "ietest":    "Age ≥ 18 years",
        "iedecod":   "AGE_INCL",

        # ICF domain
        "icfdat":    "2026-01-20",
        "icfver":    "1.0",
        "agree":     "1",

        # EN/enrollment
        "endat":     "2026-01-22",
        "randnum":   "1001",
        "randdat":   "2026-01-22",

        # DS/disposition
        "dsterm":    "COMPLETED",
        "dsdecod":   "COMPLETED",
        "dsdat":     "2026-04-01",

        # Generic date / score / numeric
        "dat":       "2026-02-01",
        "date":      "2026-02-01",
        "score":     "5",
        "total":     "12",
        "nrs":       "4",
        "vas":       "45",
        "odi":       "28",
        "pcs":       "22",
        "phq":       "8",
        "pgic":      "3",
        "sf":        "55",
        "sat":       "3",
        "num":       "5",
        "id":        "001",
        "site":      "Site-01",
        "inv":       "Smith",
        "loc":       body_site,
    }
    return ctx


def _sample_for_field(field_name, row_type, choices_for_field, ctx):
    """
    Return a concrete sample value for a field, using protocol context where
    possible and falling back to type-based defaults.
    """
    fn = (field_name or "").lower()
    t  = (row_type  or "").lower()

    # Select fields — return first valid choice
    if t.startswith("select_one") or t.startswith("select_multiple"):
        if choices_for_field:
            return str(choices_for_field[0])
        # No choices loaded — pick a sensible default for common YN/severity selects
        if "yn" in fn or "ongo" in fn or "ser" in fn[-3:]:
            return "1"  # "Yes"
        if "sev" in fn:
            return "2"  # "Moderate"
        return "1"  # generic default selection

    # Try exact match first, then prefix match
    if fn in ctx:
        return ctx[fn]
    for prefix, val in ctx.items():
        if fn.startswith(prefix):
            return val

    # Type-based fallback
    if t == "integer":  return "1"
    if t == "decimal":  return "1.5"
    if t == "date":     return "2026-02-01"
    if t == "time":     return "09:00"
    if t == "datetime": return "2026-02-01 09:00"
    if t == "text":     return "Sample text"
    return "Test value"


# ── Check classification ──────────────────────────────────────────────────────

def _check_types_for_row(row):
    """Return a list of check dicts for a row (constraint / required / etc.)."""
    checks = []
    constraint     = row.get("constraint") or ""
    cons_message   = row.get("constraint_message") or ""
    required       = str(row.get("required") or "").lower()
    relevant       = row.get("relevant") or ""
    calculation    = row.get("calculation") or ""
    bind_external  = row.get("bind::oc:external") or ""
    row_type       = str(row.get("type") or "").strip().lower()

    if row_type.startswith("begin ") or row_type.startswith("end "):
        return checks

    if constraint:
        is_cross_form = "_CF" in constraint
        checks.append({
            "check_type": "Cross-form" if is_cross_form else "Constraint",
            "severity":   "Hard",
            "expression": constraint,
            "message":    cons_message or _synthesize_message(constraint, "constraint"),
            "oc4_pattern": "Cross-form XPath (instance('clinicaldata'))" if is_cross_form else "Local constraint (XPath)",
        })

    if required in ("yes", "true", "1"):
        checks.append({
            "check_type": "Required",
            "severity":   "Hard",
            "expression": "required=yes",
            "message":    "This field is required.",
            "oc4_pattern": "Required column",
        })

    if relevant:
        is_cross_form = "_CF" in relevant
        checks.append({
            "check_type": "Cross-form" if is_cross_form else "Conditional Display",
            "severity":   "Soft",
            "expression": relevant,
            "message":    _synthesize_message(relevant, "relevant"),
            "oc4_pattern": "Cross-form relevant" if is_cross_form else "Relevant (conditional display)",
        })

    if calculation and bind_external != "clinicaldata":
        checks.append({
            "check_type": "Calculation",
            "severity":   "Soft",
            "expression": calculation,
            "message":    f"Calculated value: {row.get('label') or row.get('name')}.",
            "oc4_pattern": "Calculate (derived value)",
        })

    return checks


def _synthesize_message(expression, kind):
    expr = expression.strip()
    if len(expr) > 120:
        expr = expr[:117] + "..."
    if kind == "constraint":
        return f"Value must satisfy: {expr}"
    return f"Field visibility rule: {expr}"


# ── Constraint expression parser (for UAT inference) ─────────────────────────

_NUM_RE = r"(-?\d+(?:\.\d+)?)"


def _parse_constraint(expr):
    """Parse a constraint expression into structured form for UAT inference."""
    if not expr:
        return {"kind": "unparseable"}

    e = expr.strip()

    # OR-enumeration: `. = 'A' or . = 'B' or . = 'C'`
    or_values = re.findall(r"\.\s*=\s*'([^']+)'", e)
    or_count  = len(re.findall(r"\bor\b", e))
    if or_count >= 1 and len(or_values) >= 2 and " and " not in e.lower():
        return {"kind": "one_of", "values": or_values}

    info = {"kind": "compound", "parts": {}}

    # Numeric bounds against literals
    for pattern, key in [
        (r"\.\s*>=\s*" + _NUM_RE, "min_inclusive"),
        (r"\.\s*>\s*"  + _NUM_RE, "min_exclusive"),
        (r"\.\s*<=\s*" + _NUM_RE, "max_inclusive"),
        (r"\.\s*<\s*"  + _NUM_RE, "max_exclusive"),
    ]:
        m = re.search(pattern, e)
        if m:
            info["parts"][key] = float(m.group(1))

    # Bounds referencing another field
    for pattern, key in [
        (r"\.\s*>=\s*\$\{(\w+)\}", "min_ref"),
        (r"\.\s*>\s*\$\{(\w+)\}",  "min_ref_excl"),
        (r"\.\s*<=\s*\$\{(\w+)\}", "max_ref"),
        (r"\.\s*<\s*\$\{(\w+)\}",  "max_ref_excl"),
    ]:
        m = re.search(pattern, e)
        if m:
            info["parts"][key] = m.group(1)

    # Date vs today()
    if re.search(r"\.\s*<=\s*today\(\s*\)", e):   info["parts"]["max_date"] = "today"
    if re.search(r"\.\s*<\s*today\(\s*\)",  e):   info["parts"]["max_date_excl"] = "today"
    if re.search(r"\.\s*>=\s*today\(\s*\)", e):   info["parts"]["min_date"] = "today"
    if re.search(r"\.\s*>\s*today\(\s*\)",  e):   info["parts"]["min_date_excl"] = "today"

    # today() - N days (e.g. biopsy within last year)
    m = re.search(r"\.\s*>=\s*\(\s*today\(\s*\)\s*-\s*(\d+)\s*\)", e)
    if m:
        info["parts"]["min_date_days_ago"] = int(m.group(1))

    # Equality to literal
    eq = re.search(r"\.\s*=\s*'([^']+)'\s*$", e)
    if eq and or_count == 0:
        info["parts"]["equals"] = eq.group(1)

    neq = re.search(r"\.\s*!=\s*'([^']+)'", e)
    if neq:
        info["parts"]["not_equals"] = neq.group(1)

    if not info["parts"]:
        return {"kind": "unparseable"}

    return info


# ── Helper: sample values (legacy wrapper) ───────────────────────────────────

def _sample_value_for_type(row_type, choices_for_field, field_name="", ctx=None):
    """Return a concrete sample value — context-aware if ctx provided."""
    if ctx is not None:
        return _sample_for_field(field_name, row_type, choices_for_field, ctx)
    # Fallback (no context)
    t = (row_type or "").lower()
    if t.startswith("select_one") or t.startswith("select_multiple"):
        return choices_for_field[0] if choices_for_field else "1"
    if t == "integer":  return "1"
    if t == "decimal":  return "1.0"
    if t == "date":     return "Today's date"
    if t == "time":     return "12:00"
    if t == "datetime": return "Today's date 12:00"
    return "Test value"


def _choices_for_field(row, choices_list):
    t = str(row.get("type") or "")
    if not (t.startswith("select_one") or t.startswith("select_multiple")):
        return []
    parts = t.split()
    if len(parts) < 2:
        return []
    list_name = parts[1]
    return [c.get("name") for c in (choices_list or [])
            if c.get("list_name") == list_name and c.get("name")]


def _out_of_set_sample(allowed):
    if not allowed:
        return "ZZZ_INVALID"
    for candidate in ("ZZZ_INVALID", "X", "999", "INVALID"):
        if candidate not in allowed:
            return candidate
    return str(allowed[0]) + "_INVALID"


def _fmt_num(x):
    return str(int(x)) if x == int(x) else f"{x:g}"


def _evaluate_gate(expr, ctx):
    """
    Parse a gate/relevance expression and return (satisfies_value, fails_value)
    as concrete strings. Used for conditional display and unparseable
    constraint cases.
    """
    if not expr:
        return ("(any value)", "(any other value)")

    e = expr.strip()

    # ORDER MATTERS: compound operators (!=, >=, <=) BEFORE simple ones

    # Inequality with quoted string: ${FIELD} != 'X' or != ''
    m = re.search(r"!=\s*'([^']*)'", e)
    if m:
        forbidden = m.group(1)
        ref_match = re.search(r"\$\{(\w+)\}", e)
        ref_name = ref_match.group(1) if ref_match else ""
        if forbidden == "":
            satisfies = (_sample_for_field(ref_name.lower(), "text", [], ctx)
                         if ctx else "Sample text")
            return (str(satisfies), "")
        return ("0" if forbidden != "0" else "1", forbidden)

    # Greater-than-or-equal
    m = re.search(r">=\s*(-?\d+\.?\d*)", e)
    if m:
        n = float(m.group(1))
        return (_fmt_num(n + 10), _fmt_num(n - 1))

    # Less-than-or-equal
    m = re.search(r"<=\s*(-?\d+\.?\d*)", e)
    if m:
        n = float(m.group(1))
        return (_fmt_num(n - 10), _fmt_num(n + 1))

    # Strict greater-than (not >=)
    m = re.search(r">(?!=)\s*(-?\d+\.?\d*)", e)
    if m:
        n = float(m.group(1))
        return (_fmt_num(n + 10), _fmt_num(n))

    # Strict less-than (not <=)
    m = re.search(r"<(?!=)\s*(-?\d+\.?\d*)", e)
    if m:
        n = float(m.group(1))
        return (_fmt_num(n - 10), _fmt_num(n))

    # Equality with quoted string (not !=, >=, <=)
    m = re.search(r"(?<![!<>])=\s*'([^']*)'", e)
    if m:
        target = m.group(1)
        fail = "0" if target == "1" else "1" if target == "0" else "ZZZ"
        return (target, fail)

    # Equality with unquoted value
    m = re.search(r"(?<![!<>])=\s*(-?\d+\.?\d*)", e)
    if m:
        target = m.group(1)
        try:
            v = float(target)
            return (target, _fmt_num(v + 1))
        except ValueError:
            return (target, "0")

    # selected()
    if "selected(" in e:
        sm = re.search(r"selected\([^,]+,\s*'([^']+)'\)", e)
        if sm:
            target = sm.group(1)
            fail = "0" if target != "0" else "1"
            return (target, fail)

    # Generic fallback
    ref_match = re.search(r"\$\{(\w+)\}", e)
    ref_name = ref_match.group(1) if ref_match else ""
    if ctx and ref_name:
        sample = _sample_for_field(ref_name.lower(), "text", [], ctx)
        return (str(sample), "")
    return ("Sample text", "")



# ── UAT test case inference ─────────────────────────────────────────────────

def _infer_test_cases(check, row, choices_for_field, ctx=None, world=None):
    """Generate a list of UAT test case dicts for this check."""
    if world is None:
        world = {}
    check_type  = check["check_type"]
    expr        = check["expression"]
    msg_short   = check["message"][:80]
    row_type    = str(row.get("type") or "").lower()
    field_name  = row.get("name") or ""
    field_label = row.get("label") or field_name or ""

    # Required → blank-sad + populated-happy
    if check_type == "Required":
        sample = _sample_value_for_type(row_type, choices_for_field,
                                        field_name=field_name, ctx=ctx)
        return [
            {"scenario":   "Sad path: field left blank",
             "input_data": "(leave blank)",
             "expected":   "Required-field error shown. Form does not save."},
            {"scenario":   f"Happy path: field populated with '{sample}'",
             "input_data": sample,
             "expected":   "No required-field error. Form saves."},
        ]

    # Calculation — one case showing inputs → expected
    if check_type == "Calculation":
        refs = re.findall(r"\$\{(\w+)\}", expr)
        if refs:
            sample_inputs = ", ".join(
                f"{r}={_sample_for_field(r, '', [], ctx) if ctx else '<sample>'}"
                for r in refs[:4]
            )
            return [{
                "scenario":   f"Calc path: populate {', '.join(refs[:4])}",
                "input_data": sample_inputs,
                "expected":   f"Field {field_label or check['expression'][:30]} displays correctly computed value per formula.",
            }]
        return [{
            "scenario":   "Calc path: trigger calculation",
            "input_data": "Populate any source fields with valid values, then verify the calculated value displays correctly.",
            "expected":   "Calculated value displays.",
        }]

    # Conditional Display — shown vs hidden
    if check_type == "Conditional Display":
        gate = re.search(r"\$\{(\w+)\}", expr)
        gate_field = gate.group(1) if gate else None
        target = field_label or row.get("name", "(this field)")
        sat_val, fail_val = _evaluate_gate(expr, ctx)
        if gate_field:
            return [
                {"scenario":   f"Shown path: gate field {gate_field} satisfies the rule",
                 "input_data": f"{gate_field}={sat_val}",
                 "expected":   f"Field '{target}' is VISIBLE."},
                {"scenario":   f"Hidden path: gate field {gate_field} does NOT satisfy the rule",
                 "input_data": f"{gate_field}={fail_val}",
                 "expected":   f"Field '{target}' is HIDDEN."},
            ]
        return [
            {"scenario":   f"Shown path: {expr[:60]} is true",
             "input_data": f"Set referenced fields so expression is true (e.g. {sat_val})",
             "expected":   f"Field '{target}' is VISIBLE."},
            {"scenario":   f"Hidden path: {expr[:60]} is false",
             "input_data": f"Set referenced fields so expression is false (e.g. {fail_val})",
             "expected":   f"Field '{target}' is HIDDEN."},
        ]

    # Constraint / Cross-form — parse for structured UAT generation
    parsed = _parse_constraint(expr)

    # Unparseable — fallback to generic, but still try to give concrete values
    if parsed["kind"] == "unparseable":
        sat_val, fail_val = _evaluate_gate(expr, ctx)
        # If gate eval also returned generic placeholders, fall back to type sample
        if sat_val.startswith("(") or sat_val.startswith("Set "):
            sat_val = _sample_value_for_type(row_type, choices_for_field,
                                             field_name=field_name, ctx=ctx)
        if fail_val.startswith("(") or fail_val.startswith("Set "):
            fail_val = _out_of_set_sample([sat_val])
        return [
            {"scenario":   f"Happy path: value that satisfies {expr[:60]}",
             "input_data": str(sat_val),
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Sad path: value that violates {expr[:60]}",
             "input_data": str(fail_val),
             "expected":   f"Constraint fires. Message: {msg_short}"},
        ]

    # OR-enumeration
    if parsed["kind"] == "one_of":
        allowed = parsed["values"]
        cases = [{
            "scenario":   f"Happy path: value = '{v}' (in allowed set)",
            "input_data": v,
            "expected":   "No error. Form saves."} for v in allowed]
        cases.append({
            "scenario":   f"Sad path: value outside allowed set {allowed}",
            "input_data": _out_of_set_sample(allowed),
            "expected":   f"Constraint fires. Message: {msg_short}"})
        return cases

    parts = parsed["parts"]
    has_lit_min = any(k in parts for k in ("min_inclusive", "min_exclusive"))
    has_lit_max = any(k in parts for k in ("max_inclusive", "max_exclusive"))
    has_ref     = any(k.startswith(("min_ref", "max_ref")) for k in parts)
    has_date_today = any(k in parts for k in ("max_date","min_date","max_date_excl","min_date_excl"))

    # ── Numeric range both-bounds → 5-case ────────────────────────────────
    if has_lit_min and has_lit_max and not has_ref:
        lo = parts.get("min_inclusive", parts.get("min_exclusive"))
        hi = parts.get("max_inclusive", parts.get("max_exclusive"))
        lo_is_incl = "min_inclusive" in parts
        hi_is_incl = "max_inclusive" in parts
        mid = (lo + hi) / 2
        below = lo - 1 if lo_is_incl else lo
        above = hi + 1 if hi_is_incl else hi
        at_min = lo if lo_is_incl else lo + 1
        at_max = hi if hi_is_incl else hi - 1
        return [
            {"scenario":   f"Below-range sad path: value {_fmt_num(below)} (allowed min is {_fmt_num(lo)}{'' if lo_is_incl else ' exclusive'})",
             "input_data": _fmt_num(below),
             "expected":   f"Constraint fires. Message: {msg_short}"},
            {"scenario":   f"At-min happy path: value {_fmt_num(at_min)}",
             "input_data": _fmt_num(at_min),
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Mid-range happy path: value {_fmt_num(round(mid,2))}",
             "input_data": _fmt_num(round(mid, 2)),
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"At-max happy path: value {_fmt_num(at_max)}",
             "input_data": _fmt_num(at_max),
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Above-range sad path: value {_fmt_num(above)} (allowed max is {_fmt_num(hi)}{'' if hi_is_incl else ' exclusive'})",
             "input_data": _fmt_num(above),
             "expected":   f"Constraint fires. Message: {msg_short}"},
        ]

    # ── One-sided numeric (min only) → 3-case ─────────────────────────────
    if has_lit_min and not has_lit_max and not has_ref:
        lo = parts.get("min_inclusive", parts.get("min_exclusive"))
        lo_is_incl = "min_inclusive" in parts
        return [
            {"scenario":   f"Below-bound sad path: value {_fmt_num(lo-1)} (allowed min is {_fmt_num(lo)}{'' if lo_is_incl else ' exclusive'})",
             "input_data": _fmt_num(lo - 1),
             "expected":   f"Constraint fires. Message: {msg_short}"},
            {"scenario":   f"At/near-min happy path: value {_fmt_num(lo if lo_is_incl else lo+1)}",
             "input_data": _fmt_num(lo if lo_is_incl else lo + 1),
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Well-above happy path: value {_fmt_num(lo+10)}",
             "input_data": _fmt_num(lo + 10),
             "expected":   "No constraint error. Form saves."},
        ]

    # ── One-sided numeric (max only) → 3-case ─────────────────────────────
    if has_lit_max and not has_lit_min and not has_ref:
        hi = parts.get("max_inclusive", parts.get("max_exclusive"))
        hi_is_incl = "max_inclusive" in parts
        return [
            {"scenario":   f"Well-below happy path: value {_fmt_num(hi-10)}",
             "input_data": _fmt_num(hi - 10),
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"At/near-max happy path: value {_fmt_num(hi if hi_is_incl else hi-1)}",
             "input_data": _fmt_num(hi if hi_is_incl else hi - 1),
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Above-bound sad path: value {_fmt_num(hi+1)} (allowed max is {_fmt_num(hi)}{'' if hi_is_incl else ' exclusive'})",
             "input_data": _fmt_num(hi + 1),
             "expected":   f"Constraint fires. Message: {msg_short}"},
        ]

    # ── Reference-bound (e.g. `. < ${SYSBP}` or `. >= ${ICFDAT_CF}`) ──────
    if has_ref:
        ref_min = parts.get("min_ref") or parts.get("min_ref_excl")
        ref_max = parts.get("max_ref") or parts.get("max_ref_excl")
        ref = ref_min or ref_max
        direction_str = "on-or-after" if ref_min else "on-or-before"
        is_date_field = (row_type == "date" or "DAT" in (row.get("name") or ""))

        if is_date_field:
            # Resolve the reference date from world state (cross-case continuity)
            base_date = _get_or_set_base_date(ref, world)
            # Happy path: this date 5 days later (if min_ref) or 5 days earlier
            happy_offset = +5 if ref_min else -5
            sad_offset   = -5 if ref_min else +5
            happy_date   = _date_offset(base_date, happy_offset)
            sad_date     = _date_offset(base_date, sad_offset)
            return [
                {"scenario":   f"Happy path: this date is {direction_str} {ref} value",
                 "input_data": f"{ref}={base_date}, then this date={happy_date}",
                 "expected":   "No constraint error. Form saves."},
                {"scenario":   f"Sad path: this date violates the {direction_str} {ref} rule",
                 "input_data": f"{ref}={base_date}, then this date={sad_date}",
                 "expected":   f"Constraint fires. Message: {msg_short}"},
            ]

        # Numeric reference-bound (non-date)
        # Use a concrete numeric base if we can derive one; otherwise show with explicit op
        ref_value = world.get((ref or "").upper())
        if ref_value is None:
            # Allocate a sensible numeric baseline for unknown numeric refs
            ref_value = "100"
            world[(ref or "").upper()] = ref_value
        # Try to compute happy/sad numerics
        try:
            base_num = float(ref_value)
            happy_num = base_num + 10 if ref_min else base_num - 10
            sad_num   = base_num - 10 if ref_min else base_num + 10
            happy_str = _fmt_num(happy_num)
            sad_str   = _fmt_num(sad_num)
        except (ValueError, TypeError):
            happy_str = f"a value satisfying {expr[:40]}"
            sad_str   = f"a value violating {expr[:40]}"
        return [
            {"scenario":   f"Happy path: value satisfies ${{{ref}}} relationship",
             "input_data": f"{ref}={ref_value}, then this value={happy_str}",
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Sad path: value violates ${{{ref}}} relationship",
             "input_data": f"{ref}={ref_value}, then this value={sad_str}",
             "expected":   f"Constraint fires. Message: {msg_short}"},
        ]

    # ── Date vs today() ───────────────────────────────────────────────────
    if has_date_today:
        if "max_date" in parts or "max_date_excl" in parts:
            return [
                {"scenario":   "Happy path: today's date",
                 "input_data": "Today's date",
                 "expected":   "No constraint error. Form saves."},
                {"scenario":   "Happy path: a date in the past",
                 "input_data": "Yesterday's date (or any past date)",
                 "expected":   "No constraint error. Form saves."},
                {"scenario":   "Sad path: future date",
                 "input_data": "Tomorrow's date (any future date)",
                 "expected":   f"Constraint fires. Message: {msg_short}"},
            ]
        if "min_date" in parts or "min_date_excl" in parts:
            return [
                {"scenario":   "Happy path: today's date",
                 "input_data": "Today's date",
                 "expected":   "No constraint error. Form saves."},
                {"scenario":   "Happy path: future date",
                 "input_data": "Tomorrow's date or later",
                 "expected":   "No constraint error. Form saves."},
                {"scenario":   "Sad path: past date",
                 "input_data": "Yesterday's date or earlier",
                 "expected":   f"Constraint fires. Message: {msg_short}"},
            ]

    if "min_date_days_ago" in parts:
        days = parts["min_date_days_ago"]
        return [
            {"scenario":   f"Happy path: date within last {days} days",
             "input_data": f"Date {days // 2} days ago",
             "expected":   "No constraint error. Form saves."},
            {"scenario":   "Happy path: today's date",
             "input_data": "Today's date",
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Sad path: date older than {days} days ago",
             "input_data": f"Date {days + 10} days ago",
             "expected":   f"Constraint fires. Message: {msg_short}"},
        ]

    # ── Equality ──────────────────────────────────────────────────────────
    if "equals" in parts:
        required_val = parts["equals"]
        return [
            {"scenario":   f"Happy path: value = '{required_val}'",
             "input_data": required_val,
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Sad path: value != '{required_val}'",
             "input_data": _out_of_set_sample([required_val]),
             "expected":   f"Constraint fires. Message: {msg_short}"},
        ]

    if "not_equals" in parts:
        forbidden = parts["not_equals"]
        return [
            {"scenario":   f"Happy path: value != '{forbidden}'",
             "input_data": _out_of_set_sample([forbidden]),
             "expected":   "No constraint error. Form saves."},
            {"scenario":   f"Sad path: value = '{forbidden}'",
             "input_data": forbidden,
             "expected":   f"Constraint fires. Message: {msg_short}"},
        ]

    # Final safety fallback — produce concrete typed values
    safe_sample = _sample_value_for_type(row_type, choices_for_field,
                                         field_name=field_name, ctx=ctx)
    safe_invalid = _out_of_set_sample([safe_sample])
    return [
        {"scenario":   f"Happy path: value that satisfies {expr[:60]}",
         "input_data": str(safe_sample),
         "expected":   "No constraint error. Form saves."},
        {"scenario":   f"Sad path: value that violates {expr[:60]}",
         "input_data": str(safe_invalid),
         "expected":   f"Constraint fires. Message: {msg_short}"},
    ]


# ── Row builders (one per sheet) ──────────────────────────────────────────────

def _dvs_row(check_id, qt_id, uat_ids, form_id, field_name, field_label, check):
    target_item_oid = f"{form_id}.{field_name}" if field_name else ""
    source_form_oid = source_item_oid = source_event_oid = ""
    if "_CF" in check["expression"] or "FormOID=" in check["expression"]:
        m = re.search(r"@FormOID='([^']+)'", check["expression"])
        if m: source_form_oid = m.group(1)
        m = re.search(r"@ItemOID='([^']+)'", check["expression"])
        if m: source_item_oid = m.group(1)
        m = re.search(r"@StudyEventOID='([^']+)'", check["expression"])
        if m: source_event_oid = m.group(1)

    check_name = f"{form_id}.{field_name} — {check['check_type']}"
    if field_label:
        label_short = field_label[:40] + ("…" if len(field_label) > 40 else "")
        business_purpose = f"Enforce {check['check_type'].lower()} on {field_name} ({label_short})"
    else:
        business_purpose = f"Enforce {check['check_type'].lower()} on {form_id}.{field_name}"

    return {
        "Check ID":                check_id,
        "Status":                  "Draft",
        "Check Name":              check_name,
        "Business Purpose":        business_purpose,
        "Protocol Reference":      "(mirrored from XLSForm)",
        "Source Section":          f"XLSForm: {form_id}",
        "Check Type":              check["check_type"],
        "Severity":                check["severity"],
        "Trigger Point":           "Real-time on form entry",
        "Event Scope":             "",
        "Source Event OID(s)":     source_event_oid,
        "Current Event Needed?":   "Yes" if source_event_oid else "No",
        "crossform_references":    source_form_oid if source_form_oid != form_id else "",
        "Target Form OID":         form_id,
        "Target Item Name":        field_name,
        "Target Item OID":         target_item_oid,
        "Source Form OID(s)":      source_form_oid,
        "Source Item Name(s)":     "",
        "Source Item OID(s)":      source_item_oid,
        "Helper Calculate Item Needed?": "Yes" if "_CF" in check["expression"] else "No",
        "Helper Item OID":         "",
        "OC4 Logic Pattern":       check["oc4_pattern"],
        "Expression / Calculation": check["expression"],
        "Constraint / Required / Relevant Message": check["message"],
        "Query Text ID":           qt_id,
        "Expected Site Action":    "Review entry, correct if invalid",
        "Build Owner":             "",
        "Priority":                "",
        "UAT Case ID(s)":          ", ".join(uat_ids),
        "Notes":                   "",
    }


def _pe_row(check_id, form_id, field_name, field_label, check, form_filename, row_num):
    return {
        "Source Section":                "XLSForm: " + form_filename,
        "Protocol Reference":            "[mirrored from form build]",
        "Category":                      check["check_type"],
        "Structured Requirement / Fact": f"{form_id}.{field_name} {check['check_type']}: {check['expression']}",
        "Raw Protocol Text Summary":     (
            f"Row {row_num} of {form_filename} — field {field_name}"
            + (f" ({field_label})" if field_label else "")
        ),
        "Downstream Build Object":       f"{form_id}.{field_name}",
        "Potential Check Needed?":       "Already built",
        "Candidate Check ID":            check_id,
        "Related Event OID":             "",
        "Related Form OID":              form_id,
        "Related Item Name / OID":       field_name,
        "Priority":                      "",
        "Owner":                         "",
        "Status":                        "Mirrored",
        "Notes":                         "",
    }


def _qt_row(qt_id, check_id, message, check_type):
    return {
        "Query Text ID":        qt_id,
        "Status":               "Draft",
        "Standard Message":     message,
        "Audience":             "Site",
        "When to Use":          f"On {check_type.lower()} failure",
        "Avoid / Notes":        "",
        "Related Check ID(s)":  check_id,
        "Priority":             "",
        "Owner":                "",
        "Version Notes":        "",
    }


def _uat_row(uat_id, check_id, form_id, field_name, field_label, case,
             form_event_map=None):
    event_oid = (form_event_map or {}).get(form_id.upper(), "")
    return {
        "UAT Case ID":       uat_id,
        "Status":            "Not Run",
        "Related Check ID":  check_id,
        "Scenario":          case["scenario"],
        "Preconditions":     f"Open {form_id} form with required upstream data populated",
        "Test Steps":        f"1. Navigate to {form_id}.{field_name}\n2. Apply the input data below\n3. Attempt to save",
        "Input Data":        case["input_data"],
        "Expected Result":   case["expected"],
        "Actual Result":     "",
        "Test Result":       "",
        "Tester":            "",
        "Execution Date":    "",
        "Defect / Ticket":   "",
        "Retest Needed?":    "",
        "Priority":          "",
        "Notes":             "",
        # ODM load coordinate columns (17–25) — populated here so the
        # UAT loader can create participants and load data without blanks.
        "Site_OID":          "",        # stamped at runtime by uat_loader
        "Participant_Key":   "",        # stamped at runtime by uat_loader
        "Study_Event_OID":   event_oid,
        "Event_Repeat_Key":  "1",
        "Form_OID":          form_id,
        "Item_Group_OID":    f"IG_{form_id}_1",
        "Participant_ID":    "UAT-P001",
        "Load_Order":        "",        # set below by caller if needed
        "Load_Value":        case.get("input_data", ""),
    }


# ── Main extraction function ──────────────────────────────────────────────────

def _build_form_event_map(struct_json):
    """Return {form_id_upper: first_event_oid} from Study Spec JSON.

    Uses each form's visits_assigned list (first entry = primary event).
    Common forms (AE, CM, DV, etc.) typically have a single Common event.
    Falls back to empty string when no mapping is found.
    """
    mapping = {}
    if not isinstance(struct_json, dict):
        return mapping
    for form in struct_json.get("forms", []):
        fid = (form.get("form_id") or "").upper()
        visits = form.get("visits_assigned") or []
        if fid and visits:
            mapping[fid] = visits[0]
    return mapping


def extract_dvs_data(struct_json, forms_json):
    """Walk forms_json and emit a dvs_data dict ready for build_dvs()."""
    protocol_extraction = []
    dvs_oc4             = []
    query_text_library  = []
    uat_cases           = []

    check_counter = 0
    qt_counter    = 0
    uat_counter   = 0
    message_to_qt = {}

    # Build protocol-context-aware sample data lookup once per run
    _meta = struct_json.get("study_meta", {}) if isinstance(struct_json, dict) else {}
    sample_ctx = _build_sample_context(_meta)
    # Cross-form world state — accumulates date/numeric values for referenced
    # fields so the same field gets the same value across all UAT cases.
    cross_form_world = {}

    # Build form→event mapping from Study Spec so UAT_Cases ODM columns
    # (Study_Event_OID, Form_OID) are populated for the UAT loader.
    form_event_map = _build_form_event_map(struct_json)

    forms = forms_json.get("forms", {}) if isinstance(forms_json, dict) else {}
    for form_filename in sorted(forms.keys()):
        form_data = forms[form_filename] or {}
        survey    = form_data.get("survey") or []
        choices   = form_data.get("choices") or []

        form_id = form_filename
        if form_id.lower().endswith(".xlsx"):
            form_id = form_id[:-5]

        for row_idx, row in enumerate(survey, start=2):
            if not isinstance(row, dict):
                continue

            field_name  = row.get("name") or ""
            field_label = row.get("label") or ""
            choices_for_field = _choices_for_field(row, choices)

            for check in _check_types_for_row(row):
                check_counter += 1
                check_id = f"DVS-{check_counter:03d}"

                msg_key = (check["check_type"], check["message"])
                if msg_key in message_to_qt:
                    qt_id = message_to_qt[msg_key]
                    for qrow in query_text_library:
                        if qrow["Query Text ID"] == qt_id:
                            existing = qrow["Related Check ID(s)"]
                            qrow["Related Check ID(s)"] = f"{existing}, {check_id}" if existing else check_id
                            break
                else:
                    qt_counter += 1
                    qt_id = f"QT-{qt_counter:03d}"
                    message_to_qt[msg_key] = qt_id
                    query_text_library.append(_qt_row(
                        qt_id, check_id, check["message"], check["check_type"]))

                # Infer UAT cases — variable count per check
                inferred_cases = _infer_test_cases(check, row, choices_for_field,
                                                   ctx=sample_ctx,
                                                   world=cross_form_world)
                uat_ids_for_this_check = []
                for case in inferred_cases:
                    uat_counter += 1
                    uat_id = f"UAT-{uat_counter:03d}"
                    uat_ids_for_this_check.append(uat_id)
                    _row = _uat_row(
                        uat_id, check_id, form_id, field_name, field_label,
                        case, form_event_map)
                    _row["Load_Order"] = str(uat_counter)
                    uat_cases.append(_row)

                dvs_oc4.append(_dvs_row(
                    check_id, qt_id, uat_ids_for_this_check,
                    form_id, field_name, field_label, check))

                protocol_extraction.append(_pe_row(
                    check_id, form_id, field_name, field_label,
                    check, form_filename, row_idx))

    return {
        "study_meta":          struct_json.get("study_meta", {}) if isinstance(struct_json, dict) else {},
        "protocol_extraction": protocol_extraction,
        "dvs_oc4":             dvs_oc4,
        "query_text_library":  query_text_library,
        "uat_cases":           uat_cases,
    }


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 3:
        print("Usage: python extract_dvs_from_forms.py <struct.json> <forms.json>")
        sys.exit(1)
    struct_json = json.load(open(sys.argv[1]))
    forms_json  = json.load(open(sys.argv[2]))
    dvs_data    = extract_dvs_data(struct_json, forms_json)
    print(json.dumps(dvs_data, indent=2))
