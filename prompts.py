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
You are running the protocol-to-pricing-summary skill.

The attached PDF is the clinical trial protocol.
The EDC structure JSON from the previous skill step is included below as text.

Produce a complete pricing summary following your skill instructions.

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

The EDC structure JSON from the protocol-to-edc-structure skill is included below.

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

The EDC structure JSON and XLSForm data are included below.

Generate the Data Validation Specification following your skill instructions.

Return a single, complete, valid JSON object — no text before or after it.

The JSON must capture all validation checks, edit checks, and UAT cases
in a structure that can be written to the DVS XLSX template.
"""
