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
  QUOTE_PDF_FROM_XLSX_PROMPT       — edited Quote XLSX → Quote PDFs
"""

# ── JSON extraction prompts ────────────────────────────────────────────────────

EDC_STRUCTURE_PROMPT = """\
You are running the protocol-analysis skill.

Read the attached clinical trial protocol PDF and produce a complete Study
Specification following your skill instructions (Steps 1-8).

Return a single, complete, valid JSON object — no text before or after it.
Do not wrap in markdown code fences.

The JSON must include:
  study_meta    : protocol_number, study_title, sponsor, study_phase,
                  indication, total_study_duration_months
  timepoint_csv : filename and rows [{event, timepoint}]
  labranges_csv : filename, columns, rows
  forms         : list of CRF form objects, each with:
                  form_id, form_title, form_category, cdash_domain,
                  visits_assigned, has_repeating_group, is_epro,
                  arm_applicability, reuse_count, complexity,
                  settings {form_title, form_id, version, style, namespaces},
                  choices [{list_name, label, name}],
                  survey [{type, name, label, bind__oc_itemgroup, relevant,
                           required, constraint, calculation, readonly,
                           completion_status, library_source}]
  review_flags  : {site_specific, oid_confirmation, protocol_ambiguous,
                   constraint_review, custom_domain, pdf_mapping_uncertain,
                   name_deviation, choice_list_review} — each a list of strings
"""

PRICING_SUMMARY_PROMPT = """\
You are running the protocol-analysis skill — Protocol Summary step.

The Study Specification JSON is provided below.

Produce a complete Protocol Summary following your skill instructions
(Steps 9-14).

Return a single, complete, valid JSON object — no text before or after it.
Do not wrap in markdown code fences.

The JSON must include:
  study_meta:
    protocol_number, study_title, sponsor, study_phase, indication,
    customer_segment (COMMERCIAL|ACADEMIC|LOW_MARKET),
    volume_studies (integer), total_study_duration_months (integer)
  patient_population : total_enrollment, number_of_arms, arms list
  visit_summary      : arms list with visits_per_patient/patients/total_visits,
                       total_patient_visits_all_arms
  crf_summary        : total_unique_crfs, simple_crfs, average_crfs,
                       complex_crfs, total_reuse_crfs,
                       crf_detail [{domain_name, cdash_code, source,
                       visits_used, complexity, reuse_count, confidence, notes}]
  review_flags       : same categories as Study Specification
  complexity_flags   : list of strings
  modules_detected:
    is_epro_required (bool), is_econsent_required (bool),
    is_randomization_required (bool)
"""

DVS_TRANSLATE_PROMPT = """\
You are updating XLSForm files based on changes in a DVS XLSX.

The current XLSForm JSON and DVS changes are provided below.

Read the DVS changes and translate each into XLSForm field-level updates
(constraint, constraint_message, calculation, relevant columns).

Return a single, complete, valid JSON object — no text before or after it.

Structure:
{
  "forms": {
    "<filename>.xlsx": {
      "survey":   [{type, name, label, constraint, calculation, relevant, ...}],
      "choices":  [{list_name, name, label}],
      "settings": {form_title, form_id}
    }
  }
}

Rules:
- Keep all existing fields intact — only modify fields with DVS changes
- Preserve all original field names exactly
- Return ALL forms, not just modified ones
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
# These run inside the code execution sandbox where skill scripts are available.
# Specify exact import paths and function calls to avoid ambiguity.

GENERATE_STUDY_SPEC_PROMPT = """\
The Study Specification JSON data is provided below after "JSON DATA:".

Generate the Study Specification PDF and XLSX files using the skill scripts.

Run this Python code:
```python
import sys, json, os
sys.path.insert(0, '/skills/protocol-analysis/scripts')

data = json.loads("""DATA_PLACEHOLDER""")
protocol = data.get('study_meta', {}).get('protocol_number', 'STUDY')

from generate_study_spec_pdf import build_edc_pdf
build_edc_pdf(data, f'/mnt/user-data/outputs/{protocol}_Study_Specification.pdf')

from generate_study_spec_xlsx import build_edc_xlsx
build_edc_xlsx(data, f'/mnt/user-data/outputs/{protocol}_Study_Specification.xlsx')

print('Files generated successfully')
```

JSON DATA:
"""

GENERATE_PROTOCOL_SUMMARY_PROMPT = """\
The Protocol Summary JSON data is provided below after "JSON DATA:".

Generate the Protocol Summary PDF file using the skill script.

Run this Python code:
```python
import sys, json
sys.path.insert(0, '/skills/protocol-analysis/scripts')

data = json.loads("""DATA_PLACEHOLDER""")
protocol = data.get('study_meta', {}).get('protocol_number', 'STUDY')

from generate_protocol_summary_pdf import build_pricing_pdf
build_pricing_pdf(data, f'/mnt/user-data/outputs/{protocol}_Protocol_Summary.pdf')

print('Protocol Summary PDF generated successfully')
```

JSON DATA:
"""

PRICING_QUOTE_PROMPT = """\
The Protocol Summary JSON data is provided below after "JSON DATA:".

Generate the four quote output files using the pricing-quote skill scripts.

Run this Python code:
```python
import sys, json
sys.path.insert(0, '/skills/pricing-quote/scripts')

data = json.loads("""DATA_PLACEHOLDER""")
protocol = data.get('study_meta', {}).get('protocol_number', 'STUDY')

from pricing_engine import calculate_quote
from generate_quote_pdf import build_quote_pdfs
from generate_quote_xlsx import build_quote_xlsx

quote = calculate_quote(data)
build_quote_pdfs(
    quote,
    f'/mnt/user-data/outputs/{protocol}_Quote_Internal.pdf',
    f'/mnt/user-data/outputs/{protocol}_Quote_Client.pdf'
)
build_quote_xlsx(
    quote,
    f'/mnt/user-data/outputs/{protocol}_Quote_Internal.xlsx',
    f'/mnt/user-data/outputs/{protocol}_Quote_Client.xlsx'
)
print('Quote files generated successfully')
```

JSON DATA:
"""

EDC_BUILD_PROMPT = """\
The Study Specification JSON data is provided below after "JSON DATA:".

Build all XLSForm files and package them into a ZIP using the edc-builder skill.

Follow the SKILL.md instructions exactly — read the JSON, build all forms,
and produce the output ZIP file at:
/mnt/user-data/outputs/{protocol}_EDC_Build.zip

JSON DATA:
"""

DVS_PROMPT = """\
The Study Specification metadata and EDC Build survey data are provided below.

Generate the Data Validation Specification XLSX using the dvs-specification skill.

Follow the SKILL.md instructions exactly and produce the output at:
/mnt/user-data/outputs/{protocol}_DVS.xlsx

INPUT DATA:
"""

QUOTE_PDF_FROM_XLSX_PROMPT = """\
An edited Quote XLSX is attached. Regenerate the PDF outputs from it.

Run this Python code:
```python
import sys, io, base64
sys.path.insert(0, '/skills/pricing-quote/scripts')

# Read the attached XLSX (it will be available as an uploaded file)
import openpyxl

# Load the workbook and extract quote data
# Then regenerate PDFs using the pricing scripts
from generate_quote_pdf import build_quote_pdfs

# Extract data from XLSX and rebuild PDFs
print('Quote PDFs regenerated successfully')
```

Follow the pricing-quote SKILL.md for the exact steps to read the XLSX
and regenerate the internal and client PDF files.
"""
