"""
odm_to_spec.py — ODM intermediate → OC4 Study Spec JSON transformer

Takes the OdmStudy dict produced by odm_reader.parse_odm_metadata() and
transforms it into the Study Spec JSON schema that the existing oc-ai-pipeline
consumes (the same schema produced by EDC_STRUCTURE_PROMPT from a PDF).

This module bridges Phase 1 of the migration pipeline:
  ODM XML  →  odm_reader  →  OdmStudy  →  odm_to_spec  →  Study Spec JSON
  PDF      →  call_claude(EDC_STRUCTURE_PROMPT)         →  Study Spec JSON
                                                            ↓ (same from here)
                                                         run_study_spec_files
                                                         run_edc_build
                                                         create_oc_study

Two modes
─────────
1. DETERMINISTIC (default): Pure rule-based mapping. Fast. No API calls.
   Good for well-formed ODM exports that follow CDASH conventions.
   Call: transform(odm_study)

2. AI-ASSISTED: Calls Claude to fill gaps, infer intent, map non-CDASH
   fields, and populate constraint/relevant/calculation expressions that
   are absent from raw ODM. Falls back to deterministic for fields it
   can resolve structurally.
   Call: transform_with_ai(odm_study, claude_client)
   (claude_client must expose call_claude(prompt, extra_parts=[]) -> str)

OC4 OID conventions applied (matching EDC_STRUCTURE_PROMPT rules)
──────────────────────────────────────────────────────────────────
- Events:      SE_<UPPERCASED_OID>   e.g. SE_SCREEN, SE_WEEK_1
- Forms:       plain short uppercase name e.g. DM, VS, AE
- Item groups: <FORM>.<GROUP>        e.g. DM.DM, AE.AE
- Items:       bare field name       e.g. SUBJID, AETERM
- Settings form_id starts with F_   e.g. F_DM_1
"""

import re
import json
from typing import Any

from odm_reader import (
    build_item_lookup,
    build_codelist_lookup,
    build_form_item_map,
    odm_datatype_to_xlsform,
    DATATYPE_MAP,
)

# ── CDASH domain detection ────────────────────────────────────────────────────

# Maps common ODM form names / OID fragments → CDASH domain
CDASH_DOMAIN_MAP = {
    "DM": "DM", "DEMOG": "DM", "DEMOGRAPHICS": "DM",
    "VS": "VS", "VITALS": "VS", "VITALSIGNS": "VS",
    "AE": "AE", "ADVERSEEVENT": "AE", "ADVERSE": "AE", "F_AE": "AE",
    "CM": "CM", "CONMEDS": "CM", "CONCOMITANT": "CM", "F_CM": "CM",
    "LB": "LB", "LABS": "LB", "LABORATORY": "LB", "F_LB": "LB",
    "EX": "EX", "EXPOSURE": "EX", "DOSING": "EX", "F_EX": "EX",
    "MH": "MH", "MEDHIST": "MH", "MEDICALHISTORY": "MH",
    "IE": "IE", "INCEXC": "IE", "INCLEXCL": "IE", "F_IE": "IE",
    "DS": "DS", "DISPOSITION": "DS", "DISPOS": "DS",
    "PE": "PE", "PHYSEXAM": "PE", "PHYSICAL": "PE",
    "ECG": "EG", "EG": "EG", "ELECTROCARDIOGRAM": "EG",
    "BIOSP": "BS", "BIOSAMPLES": "BS",
    "PREG": "PR", "PREGNANCY": "PR",
    "ICF": "ICF", "CONSENT": "ICF", "INFORMEDCONSENT": "ICF", "F_ICF": "ICF",
    "EN": "EN", "ENROLLMENT": "EN", "RANDOMIZATION": "EN", "F_EN": "EN",
    "DV": "DV", "DEVIATION": "DV", "PROTOCOLDEV": "DV",
    "PC": "PC", "PK": "PC", "PHARMACOKINETICS": "PC",
}

REPEATING_DOMAINS = {"AE", "CM", "MH", "DV", "PC", "PR", "EX"}

# Forms that must live only on SE_COMMON per OC-9
COMMON_VISIT_FORMS = {"AE", "CM", "DV", "AESAE"}

# ── OID normalisation ─────────────────────────────────────────────────────────

def _oc_event_oid(raw_oid: str) -> str:
    """Ensure event OID starts with SE_"""
    oid = re.sub(r"[^A-Za-z0-9_]", "_", raw_oid).upper()
    if not oid.startswith("SE_"):
        oid = "SE_" + oid
    return oid


def _oc_form_id(raw_oid: str, raw_name: str = "") -> str:
    """
    Produce a plain short uppercase form ID.
    Try CDASH domain match first, then clean the OID.
    """
    # Try name-based CDASH match
    candidates = [raw_name.upper(), raw_oid.upper()]
    for cand in candidates:
        clean = re.sub(r"[^A-Z0-9]", "", cand)
        if clean in CDASH_DOMAIN_MAP:
            return CDASH_DOMAIN_MAP[clean]

    # Clean the OID: strip known prefixes (F_, F., CRF_, FORM_), uppercase
    clean_oid = re.sub(r"^(F[._]|CRF[._]|FORM[._])", "", raw_oid, flags=re.IGNORECASE)
    clean_oid = re.sub(r"[^A-Za-z0-9]", "_", clean_oid).upper().strip("_")

    # If it maps to a CDASH domain, use that
    if clean_oid in CDASH_DOMAIN_MAP:
        return CDASH_DOMAIN_MAP[clean_oid]

    # Return cleaned OID — cap at 20 chars but never truncate mid-word
    if len(clean_oid) <= 20:
        return clean_oid
    # Try to truncate at a natural underscore boundary within 20 chars
    parts = clean_oid[:20].rsplit("_", 1)
    return parts[0] if len(parts[0]) >= 4 else clean_oid[:20]


def _oc_item_name(raw_oid: str, raw_name: str = "", cdash_alias: str = "") -> str:
    """Return bare field name. Prefer CDASH alias if present."""
    if cdash_alias:
        return cdash_alias.upper()
    if raw_name:
        return re.sub(r"[^A-Za-z0-9]", "_", raw_name).upper().strip("_")
    return re.sub(r"^(I_[A-Z0-9]+_)", "", raw_oid).upper()


def _oc_itemgroup(form_id: str, group_oid: str = "", group_name: str = "") -> str:
    """
    Return the bind::oc:itemgroup value.
    Format: short group code only (no dots, no spaces).
    Convention: use form_id as group code when only one group per form.
    """
    if group_name:
        code = re.sub(r"[^A-Za-z0-9_]", "_", group_name).upper().strip("_")
    elif group_oid:
        # Strip prefixes like IG_, <FORM>_
        code = re.sub(r"^(IG_|" + re.escape(form_id) + r"_?)", "", group_oid,
                      flags=re.IGNORECASE)
        code = re.sub(r"[^A-Za-z0-9_]", "_", code).upper().strip("_")
    else:
        code = form_id

    return code if code else form_id


# ── Complexity scoring ────────────────────────────────────────────────────────

def _form_complexity(items: list[dict], has_repeating: bool) -> str:
    n = len(items)
    has_constraints = any(i.get("range_checks") for i in items)
    has_codelists   = any(i.get("codelist_ref") for i in items)
    score = n + (5 if has_repeating else 0) + (3 if has_constraints else 0) + (2 if has_codelists else 0)
    if score < 15:
        return "simple"
    if score < 35:
        return "average"
    return "complex"


# ── XLSForm survey row builder ────────────────────────────────────────────────

def _build_survey_row(
    item: dict,
    form_id: str,
    group_code: str,
    codelist_lookup: dict,
) -> dict:
    """Convert one ODM ItemDef into a survey row dict."""
    name  = _oc_item_name(item["oid"], item["name"], item["cdash_alias"])
    dtype = item["data_type"]
    cl_oid = item.get("codelist_ref")

    # Determine XLSForm type
    if cl_oid and cl_oid in codelist_lookup:
        cl = codelist_lookup[cl_oid]
        n_items = len(cl.get("items", []))
        # Use select_multiple only when ODM DataType hints at it (rare) or name implies it
        if "multiple" in item.get("name", "").lower() or n_items > 20:
            xlsform_type = "select_multiple " + _safe_list_name(cl_oid)
        else:
            xlsform_type = "select_one " + _safe_list_name(cl_oid)
    else:
        xlsform_type = DATATYPE_MAP.get(dtype, "text")

    # Label — prefer ODM Question text, fall back to name
    label = item.get("label") or item.get("name") or name

    # Required — treat ODM mandatory items as required
    # (mandatory comes from ItemRef, not ItemDef — we check item-level hint)
    required = ""

    # Constraint from RangeCheck
    constraints = []
    messages     = []
    for rc in item.get("range_checks", []):
        comp = rc["comparator"]
        val  = rc["check_value"]
        comp_map = {"LT": "<", "LE": "<=", "GT": ">", "GE": ">=", "EQ": "=", "NE": "!="}
        op = comp_map.get(comp.upper(), comp)
        constraints.append(f". {op} {val}")
        messages.append(f"Value must be {comp} {val}")

    constraint = " and ".join(constraints) if constraints else ""
    constraint_msg = "; ".join(messages) if messages else ""

    # Appearance heuristics
    appearance = ""
    if xlsform_type == "text":
        if item.get("length", 0) and item["length"] > 100:
            appearance = "multiline"
        else:
            appearance = "w4"
    elif xlsform_type in ("integer", "decimal"):
        appearance = "w2"
    elif xlsform_type in ("date", "time", "dateTime"):
        appearance = "w2"
    elif xlsform_type.startswith("select_one"):
        n_choices = len(codelist_lookup.get(cl_oid, {}).get("items", []))
        if n_choices <= 4:
            appearance = "w3 horizontal"
        else:
            appearance = "w3 minimal"
    elif xlsform_type.startswith("select_multiple"):
        appearance = "w4"

    # Completion status
    has_placeholder = "[PLACEHOLDER]" in label or not label
    status = "PLACEHOLDER" if has_placeholder else "COMPLETE"
    flag_reason = "Label is missing or placeholder — review required." if has_placeholder else ""

    row = {
        "type":                     xlsform_type,
        "name":                     name,
        "label":                    label,
        "bind__oc_itemgroup":       group_code,
        "appearance":               appearance,
        "required":                 required,
        "constraint":               constraint,
        "constraint_message":       constraint_msg,
        "relevant":                 "",
        "calculation":              "",
        "readonly":                 "",
        "hint":                     item.get("description", ""),
        "bind__oc_briefdescription": item.get("comment", ""),
        "bind__oc_description":     item.get("description", ""),
        "completion_status":        status,
        "library_source":           "CDASH_DEFAULT" if item.get("cdash_alias") else "PROTOCOL_SPECIFIC",
        "flag_reason":              flag_reason,
    }
    return row


def _safe_list_name(oid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", oid)


# ── Choices builder ───────────────────────────────────────────────────────────

def _build_choices(form_items: list[dict], codelist_lookup: dict) -> list[dict]:
    """Build the choices list for all codelists used by items in this form."""
    seen_codelists = set()
    choices = []
    for item in form_items:
        cl_oid = item.get("codelist_ref")
        if not cl_oid or cl_oid in seen_codelists:
            continue
        seen_codelists.add(cl_oid)
        cl = codelist_lookup.get(cl_oid)
        if not cl:
            continue
        list_name = _safe_list_name(cl_oid)
        for cli in cl.get("items", []):
            choices.append({
                "list_name": list_name,
                "name":      cli["coded_value"],
                "label":     cli["decode"] or cli["coded_value"],
                "source":    "ODM_CODELIST",
            })
    return choices


# ── Form settings builder ─────────────────────────────────────────────────────

def _build_settings(form_id: str, form_title: str, event_oids: list[str]) -> dict:
    crossform = ", ".join(event_oids) if event_oids else ""
    return {
        "form_title": form_title,
        "form_id":    f"F_{form_id}_1",
        "version":    "1",
        "style":      "theme-grid",
        "crossform_references": crossform,
        "namespaces": 'oc="http://openclinica.org/xforms" , OpenClinica="http://openclinica.com/odm"',
    }


# ── Visit assignment ──────────────────────────────────────────────────────────

def _build_visit_assignment(form_oid: str, form_id: str, odm_study: dict) -> list[str]:
    """
    Determine which OC4 events (SE_...) this form should be assigned to.
    Sources: StudyEventDef FormRef lists in the ODM.
    Applies OC-9: AE/CM/DV/AESAE → SE_COMMON only.
    """
    if form_id in COMMON_VISIT_FORMS:
        return ["SE_COMMON"]

    assigned = []
    for ev in odm_study.get("events", []):
        if form_oid in ev.get("form_refs", []):
            assigned.append(_oc_event_oid(ev["oid"]))

    return assigned if assigned else ["SE_UNSCHEDULED"]


# ── Event list builder ────────────────────────────────────────────────────────

def _build_timepoint_rows(odm_study: dict, protocol_number: str) -> list[dict]:
    """Build timepoint_csv.rows from ODM events."""
    rows = []
    seen = set()
    for i, ev in enumerate(odm_study.get("events", [])):
        oc_oid = _oc_event_oid(ev["oid"])
        if oc_oid in seen:
            continue
        seen.add(oc_oid)

        # Derive a human timepoint label from name
        label = ev.get("name") or ev["oid"]

        rows.append({
            "event":        oc_oid,
            "timepoint":    label,
            "visit_number": str(i + 1),
            "arm":          "",
        })

    # Ensure SE_COMMON exists (OC-9)
    if not any(r["event"] == "SE_COMMON" for r in rows):
        rows.append({
            "event":        "SE_COMMON",
            "timepoint":    "Common Visit",
            "visit_number": "99",
            "arm":          "",
        })

    return rows


# ── Study meta extraction ─────────────────────────────────────────────────────

def _extract_study_meta(odm_study: dict) -> dict:
    s = odm_study.get("study", {})
    proto = odm_study.get("protocol", {})

    # Arms from ODM protocol element
    arms = []
    for arm in proto.get("arms", []):
        arms.append({
            "arm_name":           arm["name"],
            "arm_code":           re.sub(r"[^A-Z0-9]", "_", arm["name"].upper()),
            "planned_enrollment": 0,
            "description":        arm["name"],
        })
    if not arms:
        arms = [{"arm_name": "Treatment", "arm_code": "TRT", "planned_enrollment": 0, "description": ""}]

    return {
        "protocol_number":             s.get("protocol_name") or s.get("name", "MIGRATION_STUDY"),
        "study_id":                    s.get("oid", ""),
        "study_title":                 s.get("name", ""),
        "sponsor":                     "",  # not available in ODM metadata
        "study_phase":                 "",
        "indication":                  "",
        "therapeutic_area":            "",
        "total_study_duration_months": 0,
        "type":                        "INTERVENTIONAL",
        "total_enrollment":            0,
        "number_of_arms":              max(len(arms), 1),
        "number_of_sites":             None,
        "regions":                     None,
        "start_date":                  "—",
        "end_date":                    "—",
        "arms":                        arms,
        "customer_segment":            "COMMERCIAL",
        "input_mode":                  "ODM_XML",
        "library_files_provided":      [],
        "_odm_source":                 odm_study.get("source_system", "UNKNOWN"),
        "_odm_version":                odm_study.get("odm_version", ""),
        "_parse_warnings":             odm_study.get("parse_warnings", []),
    }


# ── Review flags builder ──────────────────────────────────────────────────────

def _build_review_flags(odm_study: dict, forms: list[dict]) -> dict:
    """Populate the 8 review flag categories from parse warnings and field analysis."""
    flags: dict[str, list] = {
        "site_specific":        [],
        "oid_confirmation":     [],
        "protocol_ambiguous":   [],
        "constraint_review":    [],
        "choice_list_review":   [],
        "custom_domain":        [],
        "pdf_mapping_uncertain":[],
        "name_deviation":       [],
    }

    # Parse warnings → protocol_ambiguous
    for w in odm_study.get("parse_warnings", []):
        flags["protocol_ambiguous"].append({"form": "GLOBAL", "field": "", "note": w})

    # Study meta gaps
    s = odm_study.get("study", {})
    if not s.get("protocol_name"):
        flags["protocol_ambiguous"].append({
            "form": "study_meta", "field": "protocol_number",
            "note": "Protocol name not found in ODM GlobalVariables — set manually."
        })

    # Item-level flags
    item_lookup = build_item_lookup(odm_study)
    for form in forms:
        form_id = form.get("form_id", "")
        for row in form.get("survey", []):
            name = row.get("name", "")
            if row.get("completion_status") == "PLACEHOLDER":
                flags["site_specific"].append({"form": form_id, "field": name,
                                                "note": row.get("flag_reason", "")})
            if row.get("constraint") and row.get("completion_status") == "FLAGGED":
                flags["constraint_review"].append({"form": form_id, "field": name,
                                                    "note": "Constraint inferred from RangeCheck."})

        # CDASH name deviation check
        cdash_domain = form.get("cdash_domain", "")
        if not cdash_domain:
            flags["custom_domain"].append({"form": form_id, "field": "",
                                            "note": "Form does not map to a known CDASH domain."})

    return flags


# ── Main deterministic transform ──────────────────────────────────────────────

def transform(odm_study: dict) -> dict:
    """
    Transform an OdmStudy dict into an OC4 Study Spec JSON (deterministic).

    Returns the Study Spec JSON dict that pipeline.py expects.
    """
    item_lookup     = build_item_lookup(odm_study)
    codelist_lookup = build_codelist_lookup(odm_study)
    form_item_map   = build_form_item_map(odm_study)
    ig_lookup       = {ig["oid"]: ig for ig in odm_study.get("item_groups", [])}

    study_meta   = _extract_study_meta(odm_study)
    protocol_num = study_meta["protocol_number"]

    # ── Build forms list ──────────────────────────────────────────────────────
    spec_forms = []
    for odm_form in odm_study.get("forms", []):
        form_oid   = odm_form["oid"]
        form_name  = odm_form.get("name", "")
        form_id    = _oc_form_id(form_oid, form_name)
        form_title = form_name or form_id

        # Determine CDASH domain
        cdash_domain = CDASH_DOMAIN_MAP.get(form_id, None)
        is_repeating = odm_form.get("repeating", False) or form_id in REPEATING_DOMAINS

        # Get all items for this form in order
        ordered_item_oids = form_item_map.get(form_oid, [])
        form_items = [item_lookup[oid] for oid in ordered_item_oids if oid in item_lookup]

        # Visit assignment (OC-9 applied)
        visits_assigned = _build_visit_assignment(form_oid, form_id, odm_study)

        # Build survey rows
        survey_rows = []
        # Inject SUBJID row if not present (every OC4 form needs it)
        item_names = [_oc_item_name(i["oid"], i["name"], i.get("cdash_alias", "")) for i in form_items]
        if "SUBJID" not in item_names:
            survey_rows.append({
                "type":                "calculate",
                "name":                "SUBJID",
                "label":               "Subject ID",
                "bind__oc_itemgroup":  form_id,
                "appearance":          "w2",
                "required":            "",
                "constraint":          "",
                "constraint_message":  "",
                "relevant":            "",
                "calculation":         "instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey",
                "readonly":            "",
                "hint":                "",
                "bind__oc_briefdescription": "",
                "bind__oc_description": "",
                "completion_status":   "COMPLETE",
                "library_source":      "CDASH_DEFAULT",
                "flag_reason":         "",
            })

        # Group rows — iterate item groups for this form
        for ig_oid in odm_form.get("item_group_refs", []):
            ig = ig_lookup.get(ig_oid)
            if not ig:
                continue

            group_code = _oc_itemgroup(form_id, ig_oid, ig.get("name", ""))
            ig_items_ordered = [
                item_lookup[ir["oid"]]
                for ir in sorted(ig["item_refs"], key=lambda x: x["order"])
                if ir["oid"] in item_lookup
            ]

            # begin group wrapper
            survey_rows.append({
                "type": "begin group",
                "name": group_code,
                "label": ig.get("name", group_code),
                "bind__oc_itemgroup": "",
                "appearance": "field-list",
                "required": "", "constraint": "", "constraint_message": "",
                "relevant": "", "calculation": "", "readonly": "",
                "hint": "", "bind__oc_briefdescription": "", "bind__oc_description": "",
                "completion_status": "COMPLETE", "library_source": "CDASH_DEFAULT", "flag_reason": "",
            })

            # Track field names within this group so multiple items sharing a
            # CDASH alias (e.g. IE inclusion/exclusion criteria both aliased to
            # IETESTCD) don't collide — pyxform requires unique names within
            # the nearest parent.
            seen_names_in_group: set[str] = set()

            for item in ig_items_ordered:
                row = _build_survey_row(item, form_id, group_code, codelist_lookup)
                # Apply mandatory from ItemRef
                for ir in ig["item_refs"]:
                    if ir["oid"] == item["oid"] and ir["mandatory"]:
                        row["required"] = "yes"

                if row["name"] in seen_names_in_group:
                    odm_name = re.sub(r"[^A-Za-z0-9]", "_",
                                      item.get("name", "")).upper().strip("_")
                    candidate = (f"{row['name']}_{odm_name}"
                                 if odm_name and odm_name != row["name"] else row["name"])
                    if candidate in seen_names_in_group or candidate == row["name"]:
                        n = 2
                        while f"{row['name']}_{n}" in seen_names_in_group:
                            n += 1
                        candidate = f"{row['name']}_{n}"
                    row["name"] = candidate

                seen_names_in_group.add(row["name"])
                survey_rows.append(row)

            # end group
            survey_rows.append({
                "type": "end group", "name": "", "label": "",
                "bind__oc_itemgroup": "", "appearance": "", "required": "",
                "constraint": "", "constraint_message": "", "relevant": "",
                "calculation": "", "readonly": "", "hint": "",
                "bind__oc_briefdescription": "", "bind__oc_description": "",
                "completion_status": "COMPLETE", "library_source": "CDASH_DEFAULT", "flag_reason": "",
            })

        # Repeating forms — add the OC8 repeating structure at the end
        if is_repeating:
            repeat_group_code = cdash_domain or form_id
            # The inner item group above already uses `form_id` as its name when
            # the ODM ItemGroup name matches the form (e.g. AE → IG.AE/"AE").
            # Naming the repeat wrapper `form_id` too produces a duplicate-name
            # collision at the form-root scope, which pyxform rejects. Suffix
            # the repeat so it's unique among its siblings.
            repeat_name = f"{form_id}_LOG"
            survey_rows.extend([
                {
                    "type": "begin repeat", "name": repeat_name,
                    "bind__oc_itemgroup": repeat_group_code,
                    "label": "", "appearance": "", "required": "", "constraint": "",
                    "constraint_message": "", "relevant": "", "calculation": "",
                    "readonly": "", "hint": "", "bind__oc_briefdescription": "",
                    "bind__oc_description": "", "completion_status": "COMPLETE",
                    "library_source": "CDASH_DEFAULT", "flag_reason": "",
                },
                {
                    "type": "end group", "name": "",
                    "bind__oc_itemgroup": repeat_group_code,
                    "label": "", "appearance": "", "required": "", "constraint": "",
                    "constraint_message": "", "relevant": "", "calculation": "",
                    "readonly": "", "hint": "", "bind__oc_briefdescription": "",
                    "bind__oc_description": "", "completion_status": "COMPLETE",
                    "library_source": "CDASH_DEFAULT", "flag_reason": "",
                },
                {
                    "type": "end repeat", "name": "",
                    "bind__oc_itemgroup": repeat_group_code,
                    "label": "", "appearance": "", "required": "", "constraint": "",
                    "constraint_message": "", "relevant": "", "calculation": "",
                    "readonly": "", "hint": "", "bind__oc_briefdescription": "",
                    "bind__oc_description": "", "completion_status": "COMPLETE",
                    "library_source": "CDASH_DEFAULT", "flag_reason": "",
                },
            ])

        choices  = _build_choices(form_items, codelist_lookup)
        settings = _build_settings(form_id, form_title, visits_assigned)

        spec_forms.append({
            "form_id":           form_id,
            "form_title":        form_title,
            "form_category":     _form_category(form_id, cdash_domain),
            "cdash_domain":      cdash_domain or "",
            "visits_assigned":   visits_assigned,
            "has_repeating_group": is_repeating,
            "is_epro":           False,
            "arm_applicability": "ALL",
            "reuse_count":       len(visits_assigned),
            "complexity":        _form_complexity(form_items, is_repeating),
            "library_match":     {
                "status":                    "CDASH_MATCH" if cdash_domain else "CUSTOM",
                "source_type":               "CDASH_STANDARD" if cdash_domain else "CUSTOM",
                "fields_from_library":       len([i for i in form_items if i.get("cdash_alias")]),
                "fields_extended_from_protocol": 0,
                "fields_from_cdash_default": 0,
            },
            "settings":          settings,
            "choices":           choices,
            "survey":            survey_rows,
            "cross_form_dependencies": [],  # AI-assist pass fills these
        })

    # ── Timepoint CSV ─────────────────────────────────────────────────────────
    timepoint_rows = _build_timepoint_rows(odm_study, protocol_num)

    # ── Lab ranges CSV (placeholder — ODM RangeChecks on LB items if present) ─
    lb_items = [i for i in odm_study.get("items", [])
                if i.get("cdash_alias", "").startswith("LB") or
                   "lab" in (i.get("name") or "").lower()]
    labranges_rows = []
    for item in lb_items:
        labranges_rows.append({
            "test_code":  item.get("cdash_alias") or item["name"][:8].upper(),
            "test_name":  item.get("label") or item["name"],
            "lower":      "[PLACEHOLDER]",
            "upper":      "[PLACEHOLDER]",
            "unit":       "[PLACEHOLDER]",
            "lab_name":   "[PLACEHOLDER]",
        })

    # ── Review flags ──────────────────────────────────────────────────────────
    review_flags = _build_review_flags(odm_study, spec_forms)

    return {
        "study_meta":     study_meta,
        "timepoint_csv":  {
            "filename": f"{protocol_num}_tpt.csv",
            "rows":     timepoint_rows,
        },
        "labranges_csv":  {
            "filename": f"{protocol_num}_labranges.csv",
            "columns":  ["test_code", "test_name", "lower", "upper", "unit", "lab_name"],
            "rows":     labranges_rows,
        },
        "forms":          spec_forms,
        "review_flags":   review_flags,
    }


def _form_category(form_id: str, cdash_domain: str | None) -> str:
    if form_id in ("ICF", "EN", "IE", "DM"):
        return "ADMINISTRATIVE"
    if cdash_domain in ("AE", "CM", "DV", "MH"):
        return "CDASH_SAFETY"
    if cdash_domain:
        return "CDASH_CLINICAL"
    return "CUSTOM"


# ── AI-assisted transform ─────────────────────────────────────────────────────

AI_ASSIST_PROMPT = """\
You are migrating a clinical study from a source EDC into OpenClinica 4.
You have:
  1. A partially-built OC4 Study Spec JSON, deterministically generated from
     the source ODM XML export. This is your STRUCTURAL baseline.
  2. (Optionally) the study Protocol PDF attached as a document part. Use it
     ONLY as a source of clinical context that the ODM does not carry.

INPUT HIERARCHY (highest priority first — never invert this order):
  1. OC Standards OC-1 through OC-9 — always applied, never overridden.
  2. CDASH conventions — form IDs, field names, domain codes.
  3. OC4 naming conventions — SE_ prefix on events, F_<FORM>_N for form_id
     in settings, IG_ / short-code item groups.
  4. Customer library files (if referenced by the row).
  5. ODM XML from the source EDC — exact, authoritative structure:
     events, forms, items, codelists, visit assignments.
  6. Protocol PDF — clinical intent, rationale, eligibility, indication,
     sponsor, arms, study-specific constraints not captured in ODM.
  7. You (the model) interpret everything above. You do not invent structure.

HARD RULES:
  a) USE the ODM structure as AUTHORITATIVE for events, forms, items,
     codelists and visit assignments. Do NOT reinvent these from the protocol.
  b) ENRICH with protocol-derived context where the ODM is silent: indication,
     phase, therapeutic_area, sponsor, arms, eligibility, and clinical
     constraints/validations that the ODM RangeChecks did not capture.
  c) NEVER override OC-1 through OC-9 compliance — these standards win over
     anything the protocol or ODM might suggest.
  d) NEVER change CDASH form IDs that already match (DM, VS, AE, CM, LB, EX,
     MH, IE, DS, PE, EG, ICF, EN, DV, PC, etc.).
  e) NEVER alter visits_assigned that already comply with OC-9 (AE / CM / DV /
     AESAE pinned to SE_COMMON; all other forms keep their ODM-derived list).
  f) FILL study_meta gaps from the protocol PDF where available:
     protocol_number, indication, therapeutic_area, study_phase, sponsor,
     arms (names, descriptions, planned_enrollment), study_title.
     Do NOT overwrite a study_meta field that is already populated and
     plausible.
  g) ADD meaningful `constraint`, `constraint_message`, and `relevant`
     expressions on survey rows where the protocol specifies eligibility
     thresholds or data-validation rules that the ODM did not encode.
     Preserve any constraints already present.
  h) Cross-form dependencies (SUBJID from DM, ICFDAT from ICF, etc.) may be
     added to each form's `cross_form_dependencies` list as full XPath
     expressions.
  i) Flag rows whose mapping you genuinely cannot resolve: set
     `completion_status` to "FLAGGED" and populate `flag_reason`.

OUTPUT FORMAT:
  - Return ONLY the improved Study Spec JSON object.
  - Preserve the existing structure exactly. Only fill empty fields or
    sharpen plainly-wrong ones.
  - No markdown, no explanation, no preamble — just the JSON.

STUDY SPEC JSON TO IMPROVE (ODM-derived baseline):
{spec_json}

SOURCE ODM SUMMARY:
{odm_summary}
"""


_DOCX_TEXT_MARKER = b"%%DOCX_TEXT%%"


async def transform_with_ai(
    odm_study: dict,
    claude_client: Any,
    protocol_bytes: bytes | None = None,
) -> dict:
    """
    AI-assisted transform. Runs the deterministic `transform` first to obtain
    a baseline Study Spec, then calls Claude with that JSON plus (optionally)
    the protocol PDF to enrich study_meta, constraints, and cross-form deps.

    Args:
        odm_study:      OdmStudy dict from odm_reader.parse_odm_metadata().
        claude_client:  module or object with an async
                        `call_claude(prompt, pdf_bytes=None, extra_text=None)`
                        callable (matches the project's claude_client module).
        protocol_bytes: optional bytes for the protocol PDF. If they start
                        with the b"%%DOCX_TEXT%%" sentinel produced by
                        pipeline.py for Word-doc fallbacks, the inner text is
                        passed as extra_text instead of as a PDF document.

    Returns:
        Improved Study Spec JSON dict. On any AI failure, falls back to the
        deterministic baseline (callers always receive a valid spec).
    """
    from odm_reader import summarise

    spec = transform(odm_study)

    spec_json_str = json.dumps(spec, indent=2, default=str)
    odm_summary   = summarise(odm_study)
    prompt = AI_ASSIST_PROMPT.format(
        spec_json=spec_json_str,
        odm_summary=odm_summary,
    )

    pdf_arg: bytes | None = None
    extra_text_arg: str | None = None
    if protocol_bytes:
        if protocol_bytes.startswith(_DOCX_TEXT_MARKER):
            extra_text_arg = protocol_bytes[len(_DOCX_TEXT_MARKER):].decode(
                "utf-8", errors="replace"
            )
        else:
            pdf_arg = protocol_bytes

    try:
        response_text = await claude_client.call_claude(
            prompt, pdf_bytes=pdf_arg, extra_text=extra_text_arg,
        )
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned.rstrip())
        return json.loads(cleaned)
    except Exception as e:
        print(f"[odm_to_spec] AI-assist failed ({e}) — returning deterministic spec.", flush=True)
        return spec


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from odm_reader import parse_odm_metadata

    if len(sys.argv) < 2:
        print("Usage: python odm_to_spec.py <odm_file.xml> [--ai] [--out spec.json]")
        sys.exit(1)

    path   = sys.argv[1]
    use_ai = "--ai" in sys.argv
    out    = None
    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        out = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

    with open(path, "rb") as f:
        xml_bytes = f.read()

    odm_study = parse_odm_metadata(xml_bytes)
    print(f"Parsed ODM: {len(odm_study['events'])} events, "
          f"{len(odm_study['forms'])} forms, "
          f"{len(odm_study['items'])} items", flush=True)

    if use_ai:
        # Requires ANTHROPIC_API_KEY in environment and claude_client.py on path
        import asyncio
        sys.path.insert(0, ".")
        import claude_client
        spec = asyncio.run(transform_with_ai(odm_study, claude_client))
    else:
        spec = transform(odm_study)

    output_str = json.dumps(spec, indent=2, default=str)

    if out:
        with open(out, "w") as f:
            f.write(output_str)
        print(f"Study Spec JSON written to {out}")
    else:
        print(output_str)
