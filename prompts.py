"""
prompts.py — Claude prompts for oc-ai-pipeline

JSON extraction prompts (used with call_claude):
  EDC_STRUCTURE_PROMPT        — protocol PDF → Study Spec JSON
  PRICING_SUMMARY_PROMPT      — Study Spec JSON → Protocol Summary JSON
  DVS_TRANSLATE_PROMPT        — DVS changes + XLSForms → updated XLSForms JSON
  SPEC_FROM_BUILD_PROMPT      — built XLSForms → Study Spec JSON

File generation prompts (used with run_skill):
  GENERATE_STUDY_SPEC_PROMPT          — Study Spec JSON → PDF + XLSX
  GENERATE_PROTOCOL_SUMMARY_PROMPT    — Protocol Summary JSON → PDF
  PRICING_QUOTE_PROMPT                — Protocol Summary JSON → Quote PDFs + XLSXs
  EDC_BUILD_PROMPT                    — Study Spec JSON → EDC Build ZIP
  DVS_PROMPT                          — build info → DVS XLSX
"""

# ── JSON extraction prompts ────────────────────────────────────────────────────

EDC_STRUCTURE_PROMPT = """\
You are running the protocol-analysis skill.

Read the attached clinical trial protocol PDF and produce a complete Study
Specification following your skill instructions (Steps 1-8).

Return a single, complete, valid JSON object — no text before or after it.

The JSON must include at minimum:
  study_meta       : protocol_number, study_title, sponsor, study_phase,
                     indication, total_study_duration_months
  timepoint_csv    : filename and rows (event, timepoint)
  labranges_csv    : filename, columns, and rows
  forms            : list of all CRF forms with full XLSForm definitions
                     (settings, choices, survey rows)
  review_flags     : dict of category → list of flagged items
                     (site_specific, oid_confirmation, protocol_ambiguous,
                      constraint_review, custom_domain, pdf_mapping_uncertain,
                      name_deviation, choice_list_review)
"""

PRICING_SUMMARY_PROMPT = """\
You are running the protocol-analysis skill — Protocol Summary step.

The Study Specification JSON from the previous step is provided below.

Produce a complete Protocol Summary following your skill instructions
(Steps 9-14).

Return a single, complete, valid JSON object — no text before or after it.

The JSON must include:
  skill_meta       : mode, library_files_provided
  study_meta:
    protocol_number             (string)
    study_title                 (string)
    sponsor                     (string)
    study_phase                 (string)
    indication                  (string)
    customer_segment            (COMMERCIAL | ACADEMIC | LOW_MARKET)
    volume_studies              (integer)
    total_study_duration_months (integer)
  patient_population : total_enrollment, number_of_arms, arms
  visit_summary      : per arm and total
  crf_summary        : total_unique_crfs, simple/average/complex counts,
                       total_reuse_crfs, crf_detail list
  review_flags       : same categories as Study Specification
  complexity_flags   : list of study-level complexity factors
  confidence_review_notes : list
  conditional_branching   : list
  data_cleaning_estimate  : disclaimer + domains list
  modules_detected:
    is_epro_required          (bool)
    is_econsent_required      (bool)
    is_randomization_required (bool)
"""

DVS_TRANSLATE_PROMPT = """\
You are updating XLSForm files based on changes specified in a DVS
(Data Validation Specification) XLSX.

The current XLSForm JSON and the DVS changes are provided below.

1. Read the DVS changes — new or modified validation rules, constraints,
   skip patterns, and calculations
2. Translate each DVS change into the correct XLSForm field-level updates
   (constraint, constraint_message, calculation, relevant columns)
3. Return the complete updated XLSForm JSON with all changes applied

Return a single, complete, valid JSON object — no text before or after it.

Use this structure:
{
  "forms": {
    "<form_filename>.xlsx": {
      "survey":   [ { "type": ..., "name": ..., "label": ..., ... } ],
      "choices":  [ { "list_name": ..., "name": ..., "label": ... } ],
      "settings": { "form_title": ..., "form_id": ... }
    }
  }
}

Rules:
- Keep all existing fields intact — only modify fields that have DVS changes
- Preserve all original field names exactly
- Return ALL forms, not just modified ones
"""

SPEC_FROM_BUILD_PROMPT = """\
You are reverse-engineering a Study Specification from built XLSForm files.

The XLSForm JSON is provided below. Each form has survey rows, choices,
and settings.

Produce an updated Study Specification JSON reflecting the actual built forms.

Return a single, complete, valid JSON object — no text before or after it.

Include:
  study_meta  : preserve exactly as provided in the input
  forms       : derived from XLSForm survey sheets — name, oid, domain,
                fields (name, oid, type, label, codelist if applicable)
  codelists   : from XLSForm choices sheets
  constraints : from constraint and relevant columns in survey sheets
  review_flags: DO NOT include — injected from the original run

Rules:
- Preserve all field names and OIDs exactly as they appear in the XLSForms
- Do not invent or rename anything
"""

# ── File generation prompts (used with run_skill) ─────────────────────────────

GENERATE_STUDY_SPEC_PROMPT = """\
The Study Specification JSON is provided below in the attached text.

Run the generate_study_spec_pdf.py and generate_study_spec_xlsx.py scripts
from your scripts/ folder to generate the output files.

Steps:
1. Parse the JSON provided
2. Run generate_study_spec_pdf.py → {PROTOCOL}_Study_Specification.pdf
3. Run generate_study_spec_xlsx.py → {PROTOCOL}_Study_Specification.xlsx
4. Both files should appear in /mnt/user-data/outputs/

Follow the skill instructions exactly.
"""

GENERATE_PROTOCOL_SUMMARY_PROMPT = """\
The Protocol Summary JSON is provided below in the attached text.

Run the generate_protocol_summary_pdf.py script from your scripts/ folder
to generate the output file.

Steps:
1. Parse the JSON provided
2. Run generate_protocol_summary_pdf.py → {PROTOCOL}_Protocol_Summary.pdf
3. The file should appear in /mnt/user-data/outputs/

Follow the skill instructions exactly.
"""

PRICING_QUOTE_PROMPT = """\
The Protocol Summary JSON is provided below in the attached text.

Run the pricing-quote skill scripts to generate all four quote output files.

Steps:
1. Parse the JSON provided
2. Run pricing_engine.py to calculate the quote
3. Run generate_quote_pdf.py → {PROTOCOL}_Quote_Internal.pdf
                             → {PROTOCOL}_Quote_Client.pdf
4. Run generate_quote_xlsx.py → {PROTOCOL}_Quote_Internal.xlsx
                              → {PROTOCOL}_Quote_Client.xlsx
5. All files should appear in /mnt/user-data/outputs/

Follow the skill instructions in SKILL.md exactly.
"""

EDC_BUILD_PROMPT = """\
The Study Specification JSON is provided below in the attached text.

Run the edc-builder skill to build all XLSForm files and produce the
EDC build package.

Steps:
1. Parse the JSON provided
2. Build all XLSForm .xlsx files per the skill instructions
3. Package everything into {PROTOCOL}_EDC_Build.zip
4. The ZIP should appear in /mnt/user-data/outputs/

Follow the skill instructions in SKILL.md exactly.
"""

DVS_PROMPT = """\
The Study Specification JSON and EDC Build information are provided below.

Run the dvs-specification skill to generate the Data Validation Specification.

Steps:
1. Parse the inputs provided
2. Generate the DVS XLSX → {PROTOCOL}_DVS.xlsx
3. The file should appear in /mnt/user-data/outputs/

Follow the skill instructions in SKILL.md exactly.
"""

QUOTE_PDF_FROM_XLSX_PROMPT = """\
The edited Quote XLSX is provided below as base64.

Run the pricing-quote skill scripts to regenerate the PDF outputs from
this edited XLSX.

Steps:
1. Decode and read the XLSX
2. Extract the quote data from the XLSX
3. Run generate_quote_pdf.py → {PROTOCOL}_Quote_Internal.pdf
                             → {PROTOCOL}_Quote_Client.pdf
4. Files should appear in /mnt/user-data/outputs/

Follow the skill instructions in SKILL.md exactly.
"""
