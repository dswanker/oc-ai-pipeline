"""
prompts.py — Claude prompts for oc-ai-pipeline

JSON extraction prompts (used with call_claude — no skills, no code execution):
  EDC_STRUCTURE_PROMPT     — protocol PDF → Study Spec JSON
  PRICING_SUMMARY_PROMPT   — Study Spec JSON → Protocol Summary JSON
  DVS_TRANSLATE_PROMPT     — DVS changes + XLSForms → updated XLSForms JSON
  SPEC_FROM_BUILD_PROMPT   — built XLSForms → Study Spec JSON

File generation prompts (used with run_skill — Skills API + code execution):
  GENERATE_STUDY_SPEC_PROMPT       — Study Spec JSON → PDF + XLSX
  GENERATE_PROTOCOL_SUMMARY_PROMPT — Protocol Summary JSON → PDF
  PRICING_QUOTE_PROMPT             — Protocol Summary JSON → Quote PDFs + XLSXs
  EDC_BUILD_PROMPT                 — Study Spec JSON → EDC Build ZIP
  DVS_PROMPT                       — build info → DVS XLSX
"""

# ── JSON extraction prompts (call_claude only, no skill) ──────────────────────

EDC_STRUCTURE_PROMPT = """\
You are running the protocol-analysis skill.

Read the attached clinical trial protocol PDF and produce a complete Study
Specification following your skill instructions (Steps 1-8).

Return a single, complete, valid JSON object — no text before or after it.
Do not wrap in markdown code fences.

The JSON must include:
  study_meta:
    protocol_number, study_title, sponsor, study_phase, indication,
    total_study_duration_months, type (INTERVENTIONAL|OBSERVATIONAL),
    total_enrollment (integer), number_of_arms (integer),
    arms (list of {arm_name, arm_code, planned_enrollment, description}),
    customer_segment (COMMERCIAL|ACADEMIC|LOW_MARKET — infer from sponsor)
  timepoint_csv : filename and rows [{event, timepoint, visit_number, arm}]
  labranges_csv : filename, columns, rows
  forms         : list of CRF form objects, each with:
                  form_id, form_title, form_category, cdash_domain,
                  visits_assigned, has_repeating_group, is_epro,
                  arm_applicability (ALL|specific arm_code),
                  reuse_count, complexity (simple|average|complex),
                  settings, choices, survey rows
  review_flags  : each category a list of strings

Critical fields for downstream pricing:
  - study_meta.total_enrollment, number_of_arms, arms must be populated
  - timepoint_csv.rows must list every scheduled visit
  - forms.visits_assigned must reference timepoint events by name
"""

PRICING_SUMMARY_PROMPT = """\
You are running the protocol-analysis skill — Protocol Summary step.

The Study Specification JSON is provided below.

Produce a complete Protocol Summary following your skill instructions
(Steps 9-14).

Return a single, complete, valid JSON object — no text before or after it.
Do not wrap in markdown code fences.

Required top-level keys: study_meta, patient_population, visit_summary,
crf_summary, review_flags, complexity_flags, modules_detected.
"""

DVS_TRANSLATE_PROMPT = """\
You are updating XLSForm files based on changes in a DVS XLSX.

The current XLSForm JSON and DVS changes are provided below.

Read the DVS changes and translate each into XLSForm field-level updates
(constraint, constraint_message, calculation, relevant columns).

Return a single, complete, valid JSON object — no text before or after it.

Rules:
- Keep all existing fields intact — only modify fields with DVS changes
- Preserve all original field names exactly
- Return ALL forms, not just modified ones
- Structure: {"forms": {"<filename>.xlsx": {"survey": [...], "choices": [...], "settings": {...}}}}
"""

SPEC_FROM_BUILD_PROMPT = """\
You are reverse-engineering a Study Specification from built XLSForm files.

The XLSForm JSON is provided below.

Produce an updated Study Specification JSON reflecting the actual built forms.
Return a single, complete, valid JSON object — no text before or after it.

Include: study_meta (preserve exactly), forms (from survey sheets),
codelists (from choices sheets), constraints (from constraint/relevant columns).
Do NOT include review_flags — those will be injected separately.

Preserve all field names and OIDs exactly. Do not invent or rename anything.
"""


# ── File generation prompts (used with run_skill) ─────────────────────────────

GENERATE_STUDY_SPEC_PROMPT = """\
You are running the protocol-analysis skill in file generation mode.

IMPORTANT: The Study Specification data is provided as JSON at the end of
this message. DO NOT attempt to read it from any file. Parse the JSON
directly from the message content.

Task: Generate TWO output files and save both to /mnt/user-data/outputs/:
  1. {protocol}_Study_Specification.pdf
  2. {protocol}_Study_Specification.xlsx
  (where {protocol} is the study_meta.protocol_number from the JSON)

Use these scripts from your scripts/ folder:
  from generate_study_spec_pdf  import build_study_spec_pdf
  from generate_study_spec_xlsx import build_study_spec_xlsx

Call each with (data_dict, output_path).

Study Specification JSON follows this line:
"""

GENERATE_PROTOCOL_SUMMARY_PROMPT = """\
You are running the protocol-analysis skill in Protocol Summary PDF mode.

IMPORTANT: The Protocol Summary data is provided as JSON at the end of
this message. DO NOT attempt to read it from any file. Parse the JSON
directly from the message content.

Task: Generate ONE output file and save to /mnt/user-data/outputs/:
  1. {protocol}_Protocol_Summary.pdf
  (where {protocol} is the study_meta.protocol_number from the JSON)

Use this script from your scripts/ folder:
  from generate_protocol_summary_pdf import build_protocol_summary_pdf

Call with (data_dict, output_path).

Protocol Summary JSON follows this line:
"""

PRICING_QUOTE_PROMPT = """\
You are running the pricing-quote skill.

IMPORTANT: The Protocol Summary data is provided as JSON at the end of
this message. DO NOT attempt to read it from any file. Parse the JSON
directly from the message content.

Task: Generate FOUR output files and save all to /mnt/user-data/outputs/:
  1. {protocol}_Quote_Internal.pdf
  2. {protocol}_Quote_Client.pdf
  3. {protocol}_Quote_Internal.xlsx
  4. {protocol}_Quote_Client.xlsx
  (where {protocol} is the study_meta.protocol_number from the JSON)

Steps:
  1. from pricing_engine      import calculate_quote
  2. from generate_quote_pdf  import build_quote_pdfs
  3. from generate_quote_xlsx import build_quote_xlsx
  4. quote = calculate_quote(protocol_summary_dict)
  5. build_quote_pdfs(quote, internal_pdf_path, client_pdf_path)
  6. build_quote_xlsx(quote, internal_xlsx_path, client_xlsx_path)

Protocol Summary JSON follows this line:
"""

EDC_BUILD_PROMPT = """\
You are running the edc-builder skill.

IMPORTANT: The Study Specification data is provided as JSON at the end of
this message. DO NOT attempt to read any XLSX or PDF file. Skip SKILL.md
Step 1 entirely. Parse the JSON directly from the message content into a
dict called spec_data.

Task: Build all XLSForms, generate CSVs and checklists, and package into
ONE output ZIP saved to /mnt/user-data/outputs/:
  {protocol}_EDC_Build.zip
  (where {protocol} is spec_data['study_meta']['protocol_number'])

Use the scripts from your scripts/ folder:

  import os, tempfile
  from build_xlsforms  import build_all_xlsforms, write_timepoint_csv, write_labranges_csv
  from build_checklist import build_checklist_pdf, build_checklist_xlsx
  from build_package   import build_package

  build_log = []
  with tempfile.TemporaryDirectory() as tmp:
      forms_dir = os.path.join(tmp, 'forms')
      csv_dir   = os.path.join(tmp, 'csv')
      os.makedirs(forms_dir)
      os.makedirs(csv_dir)

      build_all_xlsforms(spec_data, forms_dir, build_log)
      write_timepoint_csv(spec_data.get('timepoint_csv', {}),
                          os.path.join(csv_dir, f'{protocol}_tpt.csv'),
                          build_log)
      write_labranges_csv(spec_data.get('labranges_csv', {}),
                          os.path.join(csv_dir, f'{protocol}_labranges.csv'),
                          build_log)

      checklist_pdf  = os.path.join(tmp, f'{protocol}_checklist.pdf')
      checklist_xlsx = os.path.join(tmp, f'{protocol}_checklist.xlsx')
      build_checklist_pdf(spec_data, build_log, checklist_pdf)
      build_checklist_xlsx(spec_data, build_log, checklist_xlsx)

      zip_path = f'/mnt/user-data/outputs/{protocol}_EDC_Build.zip'
      build_package(spec_data, build_log, forms_dir, csv_dir,
                    [checklist_pdf, checklist_xlsx], zip_path)

Follow SKILL.md Steps 2 onwards for the logic details (Step 2: process forms,
Step 3: handle PLACEHOLDER fields, etc.).

Study Specification JSON follows this line:
"""

DVS_PROMPT = """\
You are running the dvs-specification skill in Mode A (generate DVS from
XLSForm data).

IMPORTANT: The input data is provided as JSON at the end of this message.
DO NOT attempt to read any ZIP or XLSForm file. Skip SKILL.md Step 1
entirely. Parse the JSON directly from the message content.

Input structure:
  {
    "study_meta": { protocol_number, ... },
    "forms": { "<filename>": { "survey": [ {constraint, calculation, ...} ] } }
  }

Task: Follow SKILL.md Steps 2-6 to build the dvs_data dict with keys:
  study_meta, protocol_extraction, dvs_oc4, query_text_library, uat_cases

Then call:
  from generate_dvs import build_dvs
  build_dvs(dvs_data, f'/mnt/user-data/outputs/{protocol}_DVS.xlsx')
  (where {protocol} is study_meta.protocol_number)

Input JSON follows this line:
"""

QUOTE_PDF_FROM_XLSX_PROMPT = """\
You are running the pricing-quote skill in PDF regeneration mode.

An edited Quote XLSX is attached as base64 data above.

Follow the pricing-quote SKILL.md instructions to read the XLSX and
regenerate the PDFs. Save both files to /mnt/user-data/outputs/:
  {protocol}_Quote_Internal.pdf
  {protocol}_Quote_Client.pdf
"""
