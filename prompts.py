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

OUTPUT FORMAT — READ CAREFULLY:
  ✓ Your ENTIRE response must be a single valid JSON object.
  ✓ Start the response with `{` and end it with `}`.
  ✓ No explanation before or after the JSON.
  ✓ No markdown code fences (no ```json or ```).
  ✓ No reasoning or commentary anywhere in the output — not even inside
    the JSON as string values. Keep all string values concise and factual.
  ✓ The object's top-level keys MUST include: study_meta, timepoint_csv,
    labranges_csv, forms, review_flags.
  ✗ Do NOT output multiple JSON fragments.
  ✗ Do NOT output an example/stub object first and then the real one.
  ✗ Do NOT truncate — if you approach the token limit, shorten string
    values (especially survey row labels and flag_reason text) rather
    than omitting required structure keys.

────────────────────────────────────────────────────────────────────────────
REQUIRED TOP-LEVEL KEYS
────────────────────────────────────────────────────────────────────────────

study_meta:
  protocol_number          (str, e.g. "PrTK05")
  study_id                 (str — use protocol_number if no other identifier)
  study_title              (str, full title from protocol cover page)
  sponsor                  (str)
  study_phase              (str, e.g. "Phase 2a")
  indication               (str)
  therapeutic_area         (str, e.g. "Oncology")
  total_study_duration_months (int)
  type                     ("INTERVENTIONAL" | "OBSERVATIONAL")
  total_enrollment         (int)
  number_of_arms           (int)
  number_of_sites          (int or null)
  regions                  (str or null, e.g. "United States")
  start_date / end_date    (str or "—")
  arms                     (list of {arm_name, arm_code, planned_enrollment, description})
  customer_segment         ("COMMERCIAL" | "ACADEMIC" | "LOW_MARKET")
  input_mode               ("PROTOCOL_ONLY" | "PROTOCOL_PLUS_CRF")
  library_files_provided   (list of str, may be empty)

timepoint_csv:
  filename : "{protocol}_tpt.csv"
  rows     : list of {event, timepoint, visit_number, arm} — one row per
             scheduled visit per arm, covering SCREENING, BASELINE, every
             numbered visit, UNSCHEDULED, END_OF_TREATMENT, SAFETY_FOLLOWUP
             as applicable

labranges_csv:  (REQUIRED — populate every lab test from the protocol)
  filename : "{protocol}_labranges.csv"
  columns  : ["test_code","test_name","lower","upper","unit","lab_name"]
  rows     : list of {test_code, test_name, lower, upper, unit, lab_name}
             - test_code: CDASH LBTESTCD (e.g. "HGB","WBC","ALT","CREAT")
             - test_name: full name (e.g. "Hemoglobin","Alanine Aminotransferase")
             - lower/upper/unit/lab_name: "[PLACEHOLDER]" until site values known
             Include EVERY lab test mentioned in the protocol's laboratory
             safety assessments section. Do not leave rows empty.

forms: list of CRF form objects. For EACH form include:

  form_id                  (str, e.g. "F01_ICF","F02_DEMO")
  form_title               (str, human-readable)
  form_category            ("ADMINISTRATIVE"|"CDASH_CLINICAL"|"CDASH_SAFETY"|"INFRASTRUCTURE"|"CUSTOM")
  cdash_domain             (str or null, e.g. "DM","VS","LB","AE")
  visits_assigned          (list of event names from timepoint_csv, or ["ALL_EVENTS"])
  has_repeating_group      (bool)
  is_epro                  (bool)
  arm_applicability        ("ALL" or specific arm_code)
  reuse_count              (int — number of events this form is used at)
  complexity               ("simple"|"average"|"complex")
  library_match            ({status, source_type, fields_from_library,
                             fields_extended_from_protocol, fields_from_cdash_default})
  settings                 ({form_title, form_id, version, style, namespaces})
  choices                  (list of {list_name, label, name, source})
  survey                   (list of survey rows — see below)
  cross_form_dependencies  (list — see below)

SURVEY ROWS (critical — every row needs these three metadata fields):

  Each survey row MUST include these keys:
    type                   (e.g. "text","integer","date","select_one X","calculate","begin group","end group")
    name                   (the field name / OID)
    label                  (question text)
    completion_status      ("COMPLETE" | "FLAGGED" | "PLACEHOLDER")
    library_source         ("CDASH_DEFAULT" | "CDASH_STANDARD" | "PROTOCOL_SPECIFIC" | "CUSTOM")
    flag_reason            (str — empty "" if COMPLETE; explain why if FLAGGED/PLACEHOLDER)

  Optional fields if applicable:
    bind__oc_itemgroup, calculation, relevant, required, constraint,
    constraint_message, readonly, appearance, bind__oc_external,
    bind__oc_briefdescription, bind__oc_description

  completion_status rules:
    COMPLETE     — field is fully specified and can be built as-is
    FLAGGED      — field is specified but needs reviewer confirmation
                   (e.g. ambiguous protocol language, uncertain constraint)
    PLACEHOLDER  — field has [PLACEHOLDER] values that MUST be filled in
                   (e.g. site-specific lab values, unit strings, unknown codes)

  Be generous with FLAGGED/PLACEHOLDER — aim to flag any field where a
  human reviewer should confirm the mapping. Typical flag rate: 10-30%.

CROSS_FORM_DEPENDENCIES (per form, list of dependency objects):
  Each dependency records one field on this form that references another form:
    source_form            (str, form_id of the OTHER form being referenced)
    source_field           (str, the field name on source_form being pulled)
    purpose                (str, why — e.g. "Randomization number from EN form")
    visit_context          (str, when — e.g. "All visits after Baseline")
    status                 ("FLAGGED — OID CONFIRMATION REQUIRED" typically)

  Typical cross-form deps: DM.SUBJID pulled into every form; EN.RANDNUM
  pulled into treatment forms; VS.VISIT_DT referenced by later visits.
  Populate these wherever the protocol implies cross-form data lookups.

review_flags: (ALL eight categories must be present, even if empty list)
  site_specific           : values that must be set per site (lab ranges, units, site codes)
  oid_confirmation        : fields whose OID path needs runtime confirmation
  protocol_ambiguous      : protocol language unclear / multiple interpretations
  constraint_review       : constraints inferred from protocol — need review
  choice_list_review      : choice lists built from protocol — need review
  custom_domain           : non-CDASH domains / custom forms
  pdf_mapping_uncertain   : fields where PDF CRF mapping was uncertain
  name_deviation          : field names that deviate from CDASH standard

────────────────────────────────────────────────────────────────────────────
QUALITY CHECKLIST (verify before returning)
────────────────────────────────────────────────────────────────────────────
  ✓ study_meta.total_enrollment > 0 and number_of_arms >= 1
  ✓ timepoint_csv.rows covers every visit in the Schedule of Assessments
  ✓ labranges_csv.rows has at least one entry per lab test in the protocol
  ✓ Every survey row has completion_status, library_source, flag_reason
  ✓ Every form has a cross_form_dependencies list (may be empty [])
  ✓ review_flags has all 8 categories as lists (may be empty)
  ✓ forms.visits_assigned references events from timepoint_csv by name
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

  import os, tempfile, shutil
  from build_xlsforms  import build_all_xlsforms, write_timepoint_csv, write_labranges_csv
  from build_checklist import build_checklist_pdf, build_checklist_xlsx
  from build_package   import build_package

  # build_log is a dict of list buckets — NOT an empty list
  build_log = {
      'forms_built':    [],
      'forms_skipped':  [],
      'build_errors':   [],
      'build_warnings': [],
  }

  with tempfile.TemporaryDirectory() as tmp:
      forms_dir     = os.path.join(tmp, 'forms')
      csv_dir       = os.path.join(tmp, 'csv')
      checklist_dir = os.path.join(tmp, 'checklist')
      package_dir   = os.path.join(tmp, 'package')
      for d in (forms_dir, csv_dir, checklist_dir, package_dir):
          os.makedirs(d, exist_ok=True)

      build_all_xlsforms(spec_data, forms_dir, build_log)
      write_timepoint_csv(spec_data.get('timepoint_csv', {}),
                          os.path.join(csv_dir, f'{protocol}_tpt.csv'),
                          build_log)
      write_labranges_csv(spec_data.get('labranges_csv', {}),
                          os.path.join(csv_dir, f'{protocol}_labranges.csv'),
                          build_log)
      build_checklist_pdf(spec_data, build_log,
                          os.path.join(checklist_dir,
                                       f'{protocol}_Build_Checklist.pdf'))
      build_checklist_xlsx(spec_data, build_log,
                           os.path.join(checklist_dir,
                                        f'{protocol}_Build_Checklist.xlsx'))

      # build_package writes a date-stamped zip into package_dir and
      # returns its path. Copy it to the required outputs path.
      produced_zip = build_package(spec_data, build_log,
                                   forms_dir, csv_dir,
                                   checklist_dir, package_dir)
      shutil.copy(produced_zip,
                  f'/mnt/user-data/outputs/{protocol}_EDC_Build.zip')

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
