"""
prompts.py — Claude prompts for oc-ai-pipeline

Every prompt asks Claude to return structured JSON only.
No prompt asks Claude to return base64-encoded binary files —
that was the source of file corruption. Binary files are generated
by skill scripts running locally on this server.
"""

EDC_STRUCTURE_PROMPT = """\
You are running the protocol-to-edc-structure skill.

Read the attached clinical trial protocol PDF and produce a complete EDC
structure specification following your skill instructions.

Return a single, complete, valid JSON object — no text before or after it.

The JSON must include at minimum:
  study_meta       : protocol_number, study_title, sponsor, study_phase, indication
  forms            : list of CRF forms, each with name, oid, fields (name, type,
                     oid, label, codelist if applicable), and visit_schedule
  review_flags     : dict of flag category → list of flagged item strings
                     (categories: site_specific, oid_confirmation, protocol_ambiguous,
                      constraint_review, custom_domain, pdf_mapping_uncertain,
                      name_deviation, choice_list_review)
  constraints      : list of validation rules
  codelists        : dict of codelist name → list of {code, decode} items
"""

PRICING_SUMMARY_PROMPT = """\
You are running the protocol-summary skill.

The EDC structure JSON from the previous skill step is included below as text.

Produce a complete protocol summary following your skill instructions.

Return a single, complete, valid JSON object — no text before or after it.

The JSON must include:
  study_meta:
    protocol_number             (string)
    study_title                 (string)
    sponsor                     (string)
    study_phase                 (string)
    indication                  (string)
    customer_segment            (COMMERCIAL | ACADEMIC | LOW_MARKET)
    volume_studies              (integer — number of studies in this contract)
    total_study_duration_months (integer)
  review_flags:
    site_specific         (list of strings)
    oid_confirmation      (list of strings)
    protocol_ambiguous    (list of strings)
    constraint_review     (list of strings)
    custom_domain         (list of strings)
    pdf_mapping_uncertain (list of strings)
    name_deviation        (list of strings)
    choice_list_review    (list of strings)
  modules_detected:
    is_epro_required          (bool)
    is_econsent_required      (bool)
    is_randomization_required (bool)
"""

EDC_BUILD_PROMPT = """\
You are running the edc-builder skill.

The EDC structure JSON from the protocol-specification skill is included below.

Build all XLSForm files following your skill instructions.

Return a single, complete, valid JSON object — no text before or after it.

The JSON must be structured as:
{
  "forms": {
    "<form_filename>.xlsx": {
      "survey":  [ { "type": ..., "name": ..., "label": ..., ... }, ... ],
      "choices": [ { "list_name": ..., "name": ..., "label": ... }, ... ],
      "settings": { "form_title": ..., "form_id": ... }
    },
    ...
  },
  "study_checklist": { ... }
}
"""

DVS_PROMPT = """\
You are running the dvs-specification skill.

The XLSForm data from the EDC build is included below. This represents the
actual forms that have been built — use these as the authoritative source
for generating the DVS.

Generate the Data Validation Specification following your skill instructions.

Return a single, complete, valid JSON object — no text before or after it.

The JSON must capture all validation checks, edit checks, and UAT cases
derived directly from the form fields, constraints, and calculations
in the XLSForm data provided.

Structure:
{
  "checks": [
    {
      "form":        "form filename (e.g. DM.xlsx)",
      "field":       "field name/OID",
      "check_type":  "range | pattern | required | skip | calculation | edit_check",
      "expression":  "the validation expression or rule",
      "message":     "error message shown to user",
      "uat_case":    "description of how to test this check"
    },
    ...
  ]
}
"""

DVS_TRANSLATE_PROMPT = """\
You are updating XLSForm files based on changes specified in a DVS
(Data Validation Specification) XLSX.

The current XLSForm JSON and the DVS changes are provided below.

Your task:
1. Read the DVS changes — these describe new or modified validation rules,
   constraints, skip patterns, and calculations
2. Translate each DVS change into the correct XLSForm field-level updates
   (e.g. adding/updating the 'constraint', 'constraint_message',
   'calculation', 'relevant' columns in the survey sheet)
3. Return the complete updated XLSForm JSON with all changes applied

Return a single, complete, valid JSON object — no text before or after it.

Use the same structure as the input forms JSON:
{
  "forms": {
    "<form_filename>.xlsx": {
      "survey":  [ { "type": ..., "name": ..., "label": ...,
                     "constraint": ..., "constraint_message": ...,
                     "calculation": ..., "relevant": ... }, ... ],
      "choices": [ ... ],
      "settings": { ... }
    }
  }
}

Important rules:
- Keep all existing fields intact — only modify fields that have DVS changes
- Preserve all original field names exactly — do not rename any fields
- If a DVS check applies to a field not found in the forms, add a comment
  in the study_checklist noting the discrepancy
- Return ALL forms, not just the ones that changed
"""

SPEC_FROM_BUILD_PROMPT = """\
You are reverse-engineering a Protocol Specification from a set of built
XLSForm files.

The XLSForm JSON is provided below. Each form contains survey rows (fields),
choices (codelists), and settings.

Produce an updated EDC structure specification JSON that reflects the actual
built forms — as they exist in the XLSForms — not the original protocol.

Return a single, complete, valid JSON object — no text before or after it.

The JSON must include:
  study_meta  : preserve exactly as provided in the input
  forms       : derived from the XLSForm survey sheets — one entry per form,
                with name, oid, domain, fields (name, oid, type, label,
                codelist if applicable, required)
  codelists   : derived from the XLSForm choices sheets
  constraints : derived from constraint and relevant columns in survey sheets
  review_flags: DO NOT include — these will be injected from the original run

Important:
- Preserve all field names and OIDs exactly as they appear in the XLSForms
- Do not invent or rename anything
- If a field has a constraint expression, include it in constraints
- If a field has a calculation, note it in the field's notes
"""
