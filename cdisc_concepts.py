"""cdisc_concepts.py: tag every data field with the CDASH concept it represents, whatever it is named.

row["concept"]           = CDASHIG variable the field is equivalent to (e.g. "AESTDAT")
row["concept_qualifier"] = optional test code / category / term telling repeated uses of one variable
                           apart: VSORRES+SYSBP, IEORRES+INCLUSION, SUNCF+TOBACCO, MHOCCUR+SURGERY
row["concept_source"]    = "customer_alias" | "cdash_name" | "qrs_instrument" | "claude"

Precedence (a higher source is never overwritten by a lower one):
  1. customer_alias  conventions_engine/conventions/concept_aliases/<customer>.json, deterministic
  2. cdash_name      the field name already is a CDASHIG variable, deterministic
  3. qrs_instrument  questionnaire items matched to an instrument's CDISC test codes by item order
                     (cdisc_qrs.tag_by_order), deterministic; runs after the AI tags it builds on
  4. claude          one small Claude call for fields still untagged; every answer is validated
                     (variable must exist in CDASHIG, type must fit, high confidence only, one field
                     per concept per form) and anything that fails is discarded

Questionnaires, ratings and scales (QRS): concept QSORRES / FTORRES / RSORRES + qualifier = the instrument's
CDISC test code (e.g. QSORRES + PHQ0101). The AI may only use test codes of instruments the form names; the
code is validated against the instrument's test-code codelist in CDISC CT.

Runs after the whole study is defined and before the conventions engine, so edit-check conventions can
match field.concept on CDASH and non-CDASH forms alike. Never fails a build.
"""
import json, re
import spec_trim
from pathlib import Path

import cdisc_ct
import cdisc_cdash

_NON_DATA = ("begin group", "end group", "begin repeat", "end repeat", "note")
_RANK = {"customer_alias": 3, "cdash_name": 2, "qrs_instrument": 1.5, "claude": 1}
_DATE_TYPES = ("date", "datetime", "pdate")
_QUAL_OK = re.compile(r"^[A-Z0-9_\-/]{1,40}$")


def _rank(row):
    return _RANK.get(row.get("concept_source"), 0)


def _is_data_field(row):
    if not isinstance(row, dict) or not row.get("name"):
        return False
    t = str(row.get("type") or "").strip().lower()
    if t in _NON_DATA or t == "calculate":
        return False
    return not str(row["name"]).upper().endswith(("_CF", "_SF"))


def _set(row, concept, source, qualifier=None):
    if _rank(row) > _RANK[source]:
        return False
    if (row.get("concept"), row.get("concept_qualifier"), row.get("concept_source")) == (concept, qualifier, source):
        return False
    row["concept"], row["concept_source"] = concept, source
    if qualifier:
        row["concept_qualifier"] = qualifier
    else:
        row.pop("concept_qualifier", None)
    return True


def _split_alias(value):
    """Alias value "IEORRES" or "IEORRES:INCLUSION" -> (concept, qualifier)."""
    concept, _, qual = str(value).upper().partition(":")
    return concept, (qual or None)


def load_aliases(customer_subdomain="", client_name=""):
    """{"FIELD": "CONCEPT", "FORM.FIELD": "CONCEPT"} for the customer, or {}."""
    try:
        from conventions_engine import _default_data_root
        from conventions_engine.loader import resolve_customer
        root = Path(_default_data_root())
        folder = resolve_customer(root, customer_subdomain, client_name)
        base = root / "conventions" / "concept_aliases"
        for name in (folder, customer_subdomain, client_name):
            p = base / f"{(name or '').replace(' ', '')}.json"
            if name and p.exists():
                data = json.load(open(p))
                return {k.upper(): str(v).upper() for k, v in (data.get("aliases") or {}).items()}
    except Exception as e:
        print(f"[cdisc-concepts] alias load failed (none applied): {e}", flush=True)
    return {}


# ── Deterministic tagging (customer aliases, CDASH names) ───────────────────────

def _vs_tests(std):
    """Vital-sign test codes (CT VSTESTCD) as concepts for horizontal VS fields (BPSYS -> SYSBP)."""
    cl = std.ct.get("VSTESTCD") if std else None
    return {t["value"].upper(): t["preferred_term"] or t["value"] for t in (cl or {}).get("terms", {}).values()}


def _fields_by_var():
    fields = cdisc_cdash.load_cdashig_fields()
    by_var = {}
    for (_d, v), rec in fields.items():
        by_var.setdefault(v, []).append(rec)
    return fields, by_var


def tag_deterministic(spec, std, aliases=None):
    """Customer aliases first, then CDASH names. Returns counts."""
    aliases = aliases or {}
    fields, by_var = _fields_by_var()
    known = set(by_var)
    n = {"customer_alias": 0, "cdash_name": 0, "alias_unknown_concept": 0}
    for form in spec.get("forms") or []:
        if not isinstance(form, dict):
            continue
        fid = str(form.get("form_id") or "").upper()
        domain = str(form.get("cdash_domain") or "").strip().upper()
        for row in form.get("survey") or []:
            if not _is_data_field(row):
                continue
            name = str(row["name"]).upper()
            alias = aliases.get(f"{fid}.{name}") or aliases.get(name)
            if alias:
                a_concept, a_qual = _split_alias(alias)
                if a_concept in known:
                    n["customer_alias"] += _set(row, a_concept, "customer_alias", a_qual)
                    continue
                n["alias_unknown_concept"] += 1
            rec = cdisc_cdash._resolve(fields, by_var, domain, cdisc_ct.field_variable(name, std))
            if rec:
                n["cdash_name"] += _set(row, rec["variable"], "cdash_name")
    return n


def untagged(spec):
    return [(f, r) for f in (spec.get("forms") or []) if isinstance(f, dict)
            for r in (f.get("survey") or []) if _is_data_field(r) and not r.get("concept")]


# ── Claude tagging (validated) ───────────────────────────────────────────────────

_COMMON_DOMAINS = ("DM", "AE", "CM", "MH", "VS", "EX", "DS", "IE", "LB", "PE", "SU", "PR")

PROMPT = """You map clinical trial eCRF fields to CDISC CDASHIG v2.3 variables.

For each FIELD below, decide whether it collects the same concept as one CDASHIG VARIABLE in the
CATALOGUE. Answer ONLY with JSON, no prose, no code fences:
{"tags": [{"form_id": "...", "field": "...", "concept": "<CDASHIG variable>", "qualifier": "<optional>",
           "confidence": "high"|"medium"}]}

Rules:
- Use only variables that appear in the CATALOGUE. Never invent a variable.
- Tag a field only when it clearly collects that concept (same meaning, compatible data type).
  Leave fields out when unsure. Omitting is always acceptable.
- Dates map to date variables (names ending in DAT), times to TIM variables.
- A yes/no screening question (e.g. "Any medical history?") maps to the domain's --YN variable when one exists.
- When several fields are repeated uses of ONE variable, add a "qualifier" that tells them apart:
    horizontal vital signs -> concept VSORRES, qualifier = VS test code from VITAL SIGN TESTS (e.g. SYSBP)
    inclusion / exclusion criterion responses -> concept IEORRES, qualifier exactly INCLUSION or EXCLUSION
      (the category, not the criterion number)
    substance-use status per substance -> concept SUNCF, qualifier TOBACCO / ALCOHOL / ...
    pre-specified history or event questions ("Surgery?") -> concept MHOCCUR (or AEOCCUR/CMOCCUR),
      qualifier = short upper-case term (e.g. SURGERY)
  Qualifiers are short upper-case codes. Omit "qualifier" when the field is the only use of the variable.
- Each concept + qualifier pair at most once per form.
"""


QRS_RULES = """
Questionnaires, ratings and scales:
- QRS INSTRUMENT ITEMS lists, for the forms named there, the instrument's items (test code | item).
  A field that collects the answer to one of those items gets the concept shown on the INSTRUMENT line
  (QSORRES, FTORRES or RSORRES) and qualifier = that item's test code (e.g. PHQ0101).
- Use only test codes listed for an instrument offered for the field's own form. Match by item meaning and
  item order. A total or subscale score field gets the instrument's total/subscale test code when one is listed.
- "Was it done?", date, reason and comment fields are not questionnaire items: leave them out.
"""


def _qrs_index(std):
    try:
        import cdisc_qrs
        return cdisc_qrs.build_index(std.ct) if std is not None else None
    except Exception as e:
        print(f"[cdisc-qrs] instrument index unavailable: {e}", flush=True)
        return None


def build_request(spec, std=None, qrs=True):
    """(prompt, extra_text) for the untagged fields, or None when nothing needs Claude.
    qrs=False leaves the questionnaire section out (CDISC_QRS=0)."""
    todo = untagged(spec)
    if not todo:
        return None
    fields, _ = _fields_by_var()
    domains = set(_COMMON_DOMAINS) | {str(f.get("cdash_domain") or "").upper() for f in spec.get("forms") or []
                                      if isinstance(f, dict)}
    cat = [f"{d} {v} | {r['label']} | {r['type']}" for (d, v), r in sorted(fields.items()) if d in domains
           and v not in cdisc_cdash._NOT_CRF_QUESTIONS]
    lines, last_head = [], None
    for form, row in todo:
        lists = {}
        for c in form.get("choices") or []:
            if isinstance(c, dict):
                lists.setdefault(c.get("list_name"), []).append(str(c.get("label") or c.get("name")))
        t = str(row.get("type") or "")
        ln = t.split(" ", 1)[1].strip() if " " in t else ""
        ch = f" | choices: {'; '.join(spec_trim.text(x) for x in lists.get(ln, [])[:8])}" if ln in lists else ""
        head = f"{form.get('form_id')} ({form.get('form_title', '')}; domain {form.get('cdash_domain') or '-'})"
        if spec_trim.enabled():   # the form heading once per form instead of on every field line
            if head != last_head:
                lines.append(f"FORM {head}")
                last_head = head
            lines.append(f"  {row['name']} | {t} | {spec_trim.label(row.get('label'), 120)}{ch}")
        else:
            lines.append(f"{head} | {row['name']} | {t} | {spec_trim.label(row.get('label'), 120)}{ch}")
    vs = _vs_tests(std)
    if vs:
        cat.append("\nVITAL SIGN TESTS (VS test code | test):")
        cat += [f"VS {k} | {v}" for k, v in sorted(vs.items())]
    prompt = PROMPT
    ix = _qrs_index(std) if qrs else None
    if ix is not None:
        import cdisc_qrs
        todo_forms = {id(f) for f, _r in todo}
        sub = {"forms": [f for f in spec.get("forms") or [] if id(f) in todo_forms]}
        q_lines, per_form = cdisc_qrs.catalogue_lines(sub, ix)
        if q_lines:
            prompt = PROMPT + QRS_RULES
            cat.append("\nQRS INSTRUMENT ITEMS (instrument id | category | name | concept; then test code | item):")
            cat += q_lines
            cat.append("Instruments offered per form: " + "; ".join(f"{f}: {', '.join(i)}"
                                                                     for f, i in sorted(per_form.items()) if i))
    fields_head = ("FIELDS (a FORM line: form id (title; domain); then its fields: field | type | label):"
                   if spec_trim.enabled() else "FIELDS (form | field | type | label):")
    extra = "CATALOGUE (domain variable | label | type):\n" + "\n".join(cat) + \
            "\n\n" + fields_head + "\n" + "\n".join(lines)
    return prompt, extra


def _parse(text):
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        data = json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        data = json.loads(m.group(0)) if m else {}
    return data.get("tags") if isinstance(data, dict) else []


def _type_fits(row, concept, rec):
    t = str(row.get("type") or "").strip().lower().split(" ")[0]
    is_date_field = t in _DATE_TYPES
    is_date_concept = concept.endswith(("DAT", "DTC"))
    if is_date_field != is_date_concept:
        return False
    if concept.endswith("TIM") and t not in ("time", "text"):
        return False
    if rec and rec.get("type") == "Num" and t.startswith(("select", "date")):
        return False
    return True


def apply_ai_response(spec, std, response_text, qrs=True):
    """Validate Claude's tags and apply the ones that pass. Returns a summary with reasons for rejects."""
    fields, by_var = _fields_by_var()
    ix = _qrs_index(std) if qrs else None
    if ix is not None:
        import cdisc_qrs
    rejects = {}
    accepted = 0

    def reject(reason):
        rejects[reason] = rejects.get(reason, 0) + 1

    try:
        tags = _parse(response_text) or []
    except Exception:
        return {"accepted": 0, "rejected": {"unparseable_response": 1}}
    forms = {str(f.get("form_id")): f for f in spec.get("forms") or [] if isinstance(f, dict)}
    proposed = {}
    for tag in tags:
        if not isinstance(tag, dict):
            reject("malformed"); continue
        form = forms.get(str(tag.get("form_id")))
        concept = str(tag.get("concept") or "").strip().upper()
        if form is None:
            reject("unknown_form"); continue
        row = next((r for r in form.get("survey") or [] if isinstance(r, dict)
                    and str(r.get("name")) == str(tag.get("field"))), None)
        if row is None or not _is_data_field(row):
            reject("unknown_field"); continue
        if row.get("concept"):
            reject("already_tagged"); continue
        if tag.get("confidence") != "high":
            reject("not_high_confidence"); continue
        qual = str(tag.get("qualifier") or "").strip().upper().replace(" ", "_") or None
        if ix is not None and concept in cdisc_qrs.QRS_CONCEPTS and (qual in ix.by_testcd or concept not in by_var):
            # questionnaire item: the test code must be in CDISC CT, belong to an instrument this form names,
            # and the concept follows the instrument's domain (QS / FT / RS)
            inst = ix.instrument_of(qual)
            if inst is None:
                reject("qrs_test_code_not_in_ct"); continue
            if inst["id"] not in cdisc_qrs.candidates(form, ix):
                reject("qrs_instrument_not_named_on_form"); continue
            if str(row.get("type") or "").strip().lower().split(" ")[0] not in ("select_one", "integer", "decimal", "text"):
                reject("type_mismatch"); continue
            proposed.setdefault((id(form), cdisc_qrs.CONCEPT_BY_DOMAIN[inst["domain"]], qual), []).append(row)
            continue
        if concept not in by_var:
            reject("concept_not_in_cdashig"); continue
        if qual and not _QUAL_OK.match(qual):
            reject("bad_qualifier"); continue
        if concept == "VSORRES" and (not qual or qual not in _vs_tests(std)):
            reject("vs_qualifier_not_a_vs_test_code"); continue
        if concept == "IEORRES" and qual not in ("INCLUSION", "EXCLUSION"):
            # an unambiguous criterion number is turned into its category (INC01 -> INCLUSION)
            qual = ("INCLUSION" if re.match(r"^IN(C|CL)?\d", qual or "") else
                    "EXCLUSION" if re.match(r"^EX(C|CL)?\d", qual or "") else qual)
        if concept == "IEORRES" and qual not in ("INCLUSION", "EXCLUSION"):
            reject("ie_qualifier_not_inclusion_or_exclusion"); continue
        domain = str(form.get("cdash_domain") or "").upper()
        rec = cdisc_cdash._resolve(fields, by_var, domain, concept)
        if concept == "VSORRES":
            rec = {"type": "Num"}
        if not _type_fits(row, concept, rec):
            reject("type_mismatch"); continue
        proposed.setdefault((id(form), concept, qual), []).append(row)
    for (fid, concept, qual), rows in proposed.items():
        form = next(f for f in forms.values() if id(f) == fid)
        repeatable = concept == "IEORRES"  # many criteria per category on one form, by design
        taken = any(r.get("concept") == concept and r.get("concept_qualifier") == qual
                    for r in form.get("survey") or [] if isinstance(r, dict))
        if not repeatable and (len(rows) > 1 or taken):
            for _ in rows:
                reject("concept_not_unique_on_form")
            continue
        for row in rows:
            accepted += _set(row, concept, "claude", qual)
    return {"accepted": accepted, "rejected": rejects}


def summary(spec):
    counts = {}
    for f in spec.get("forms") or []:
        for r in (f.get("survey") or []) if isinstance(f, dict) else []:
            if _is_data_field(r):
                k = r.get("concept_source") or "untagged"
                counts[k] = counts.get(k, 0) + 1
    return counts
