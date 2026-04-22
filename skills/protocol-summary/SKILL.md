---
name: protocol-to-pricing-summary
description: >
  Reads a clinical trial protocol document (PDF or text) and extracts all
  information needed to generate a pricing quote for OpenClinica EDC system
  configuration services. Outputs both a human-readable pricing summary and a
  structured data block. Use this skill whenever a user uploads or references a
  clinical trial protocol and asks for a summary, pricing analysis, CRF estimate,
  study complexity assessment, or any output that will feed a pricing model or
  quote. Also trigger when the user mentions protocols, schedules of assessments,
  CRF counts, study builds, or EDC scoping. When in doubt, use this skill.
---

# Protocol-to-Pricing Summary Skill

## Purpose

Extract structured pricing-relevant information from a clinical trial protocol.
Output feeds OpenClinica's pricing model for EDC study configuration services.

## Before You Begin — Read Reference Files

**Always read both reference files before processing any protocol:**

1. `references/crf-complexity-rules.md` — Contains the current complexity
   classification tiers (Simple / Average / Complex). This definition changes
   over time. Always read the file; never rely on memory.

2. `references/crf-categorization-examples.md` — Contains human-corrected
   examples from previous runs. Entries here override general rules when a
   similar situation is encountered. Read all entries before classifying.

---

## Input Detection — Protocol Only vs. Protocol + Customer Library

**Before doing anything else, determine what inputs were provided:**

### Mode 1 — Protocol Only
The user has provided a single protocol document and no reference library.
Proceed with all steps below using CDASH standards for all domain estimates.
Tag every CRF in the output as `[CDASH ESTIMATE]`.

### Mode 2 — Protocol + Customer Reference Library
The user has provided a protocol AND one or more additional PDFs described as
a customer CRF library, reference library, or similar. Follow the
**Customer Library Processing** steps below before proceeding to Step 1.

---

## Customer Library Processing (Mode 2 Only)

### CL-1: Detect Library Format

Examine the provided library files:

**Multiple PDFs provided:**
Treat as one-domain-per-file format (Option A). For each PDF:
- Use the filename and document title as the primary domain identifier
- Extract the CDASH domain code if present (e.g., "AE", "VS", "LB")
- Extract all field names and count them
- Identify any repeating groups (table structures)
- Note any conditional logic described on the form

**Single PDF provided:**
Treat as a library booklet format (Option B). Scan the document for:
- Section headers or form titles that indicate domain boundaries
- CDASH domain codes or standard domain names
- For each identified section, extract fields, field count, and repeating groups

**Uncertain format:**
If it is unclear whether a single PDF contains one or multiple domains, attempt
both approaches. Flag as `[LIBRARY FORMAT UNCLEAR — REVIEW RECOMMENDED]` and
document your interpretation in Section 6.

### CL-2: Build a Customer Domain Index

After reading the library, build an internal index of customer CRFs:

For each customer CRF found, record:
- Customer form name / title
- Mapped CDASH domain code (infer if not explicit)
- Field count (actual from the form)
- Repeating groups present (yes/no, count)
- Any conditional logic noted
- Source file and page/section reference

### CL-3: Match Protocol Domains to Customer Library

For each domain identified in the protocol's Schedule of Assessments:

**Search the customer index for a match using this priority order:**
1. Exact CDASH domain code match (e.g., protocol needs AE, library has AE form)
2. Form title / domain name match (e.g., "Adverse Events" matches "AE Log")
3. Field content similarity (e.g., form collects HR + BP + Temp → likely VS)

**Classify each match as:**

`EXACT MATCH` — Domain code or title matches AND the customer form covers all
fields required by the protocol. Use customer form's actual field count and
structure for complexity classification. Tag: `[CUSTOMER LIBRARY — EXACT MATCH]`

`PARTIAL MATCH` — Domain matches but the customer form does NOT cover all
fields required by the protocol (protocol requires fields not present on the
customer form). **Use protocol requirements as the basis for classification,
not the customer form.** Tag: `[CUSTOMER LIBRARY — PARTIAL MATCH — CLASSIFIED
ON PROTOCOL REQUIREMENTS]`. In the Notes column, describe what the customer
form has vs. what the protocol requires.

`NO MATCH` — No customer CRF found for this domain. Fall back to CDASH
standard estimate. Tag: `[CDASH ESTIMATE — NO LIBRARY MATCH]`

### CL-4: Proceed to Step 1

With the customer domain index built and matches determined, proceed through
Steps 1–9 below. At every point where a CRF is classified, apply the match
result from CL-3 rather than defaulting to CDASH.

---

## Step 1: Extract Study Overview

Extract the following fields. If a field is not present in the protocol, output
the field name with an empty placeholder marked `[NOT SPECIFIED — PLEASE COMPLETE]`.

- Protocol Number
- Sponsor Name
- Study Title
- Therapeutic Area (map to standard category: Oncology, CNS, Cardiovascular,
  Infectious Disease, Rare Disease, Metabolic, Other — infer if not explicit)
- Study Phase (Phase 1, 1/2, 2, 2a, 2b, 3, 4, or Observational)
- Study Type (e.g., open-label, randomized, double-blind, multi-center, etc.)
- Number of Sites `[NOT SPECIFIED — PLEASE COMPLETE]` if not stated
- Region(s) `[NOT SPECIFIED — PLEASE COMPLETE]` if not stated
- Estimated First Patient In date
- Estimated Last Patient Complete date
- Total Study Duration in months (calculate if start/end dates are provided)

---

## Step 2: Extract Patient Population

- Total planned enrollment (all arms combined)
- Number of arms / groups
- For each arm: name, patient count (n=), and brief description of what
  distinguishes this arm

---

## Step 3: Build the Complete CRF List

**This step is critical. The CRF count produced here must match what
the protocol-to-edc-structure skill would produce — every unique
buildable XLSForm, not every unique CDASH domain.**

Work through ALL FOUR sources below. Do not rely on the SoA alone.

### Source 1 — Every Schedule of Assessments table (row by row)

Work through every SoA table for every arm separately. For each
assessment row × visit column:
- Map to its CDASH domain
- Note which visits it appears at and which arms
- Check whether the field set changes across visits (= new unique CRF)
- Check whether arm differences create distinct form designs

**Common CDASH domain mappings:**
- Demographics → DM (always separate from MH)
- Medical History → MH (always separate from DM)
- Informed Consent + Eligibility Criteria → IE (split by arm if criteria differ)
- Disease Assessment / Characteristics → DC (always separate from IE)
- Vital Signs → VS (split if field set changes between visits)
- Physical Examination → PE (split if full vs. symptom-directed)
- Laboratory Assessments (full panel) → LB
- PSA (if at different visits from full panel) → PSA (separate form)
- Adverse Events → AE
- Concomitant Medications → CM
- Concomitant Procedures → PR_CONCOM (always separate from study drug PR)
- Study Drug Administration → EX
- Prodrug/Companion Drug Administration → EC (separate from EX)
- Patient Diary / ePRO → separate ePRO CRF for each diary instrument
- Biospecimen Collection → BS/BE (split by arm if field sets differ)
- Semen Biospecimen (if separate collection procedure) → BES
- External Beam Radiation / Procedure → PR_EBRT (separate from PR_CONCOM)
- Disposition → DS
- Questionnaires / PRO → QS (one per distinct instrument)
- Pregnancy Reporting → PREGPART

### Source 2 — Protocol body sections

Read these sections for CRF requirements not explicit in the SoA:
- **Eligibility criteria section** — arm-specific criteria → 2 IE forms
- **Treatment section** — patient diary or compliance recording → ePRO CRF;
  prodrug dose adjustment table → EC form fields
- **Procedures section** — full PE at screening vs. symptom-directed
  at follow-up → 2 PE forms
- **Safety section** — SAE reporting, pregnancy reporting → PREGPART form
- **Study operations** — protocol deviation tracking → DV form (infrastructure)

### Source 3 — Apply ALL standing rules from the learning log

Before finalising the CRF list, apply every rule in
`references/crf-categorization-examples.md`. Key rules:

- DM and MH → always 2 separate CRFs
- I/E with arm-specific criteria → 2 unique IE CRFs (one per arm)
- Full assessment at screening + modified at follow-up → 2 unique CRFs
  (applies to PE, VS when height/weight baseline only, and any domain
  where field set changes between visits)
- Patient diary / ePRO → always a separate unique CRF; never fold into
  the related site-entered CRF (EC_DIARY ≠ EC)
- Disease assessment data (PSA, staging, biopsy, ECOG) → always a
  separate DC form; never part of the IE form
- Same CDASH domain code ≠ same CRF. When PR, LB, VS, or any domain
  appears in two different contexts with different field sets or visit
  assignments → count each as a separate unique CRF
- Biospecimen forms that differ by arm (treatment has qPCR fields,
  control does not) → 2 separate unique CRFs

### Source 4 — Infrastructure forms (always include)

Always add these unless the protocol explicitly excludes them:
- DOV — Date of Visit (every visit)
- DV — Protocol Deviation Log (ongoing)
- SPELIG — Sponsor Eligibility Review (screening)

---

## Step 4: Identify Unique and Re-use CRFs

A **Unique CRF** is a distinct buildable XLSForm — one per distinct
combination of: form design + field set + arm applicability.

A **Re-use CRF** is every additional visit where an already-designed
form is deployed without any field changes.

**Process:**
1. Start from the complete CRF list built in Step 3
2. For each unique CRF, list every visit where it is used
3. Re-use count = (total visits for that form) − 1
4. Sum all unique CRFs and all re-use CRFs across all forms

**Completeness check:**
Every assessment row in every SoA table must map to at least one
unique CRF. Any unmapped assessment is a counting error — resolve
before finalising the count.

**The unique CRF count from this step must equal the count that
the protocol-to-edc-structure skill would produce for the same
protocol. If the two skills produce different counts, the pricing
summary is under-counting.**

**Example logic:**
- VS collected at 9 visits; baseline collects HR + BP + Temp +
  Height + Weight, follow-up collects HR + BP + Temp only →
  2 unique VS CRFs; VS re-use = 7 (8 follow-up visits − 1)
- AE collected at 11 visits with identical fields → 1 unique AE CRF;
  AE re-use = 10
- EC (prodrug, site-entered) and EC_DIARY (patient diary, ePRO) →
  2 unique CRFs even though both relate to valacyclovir dosing

**When field-level detail is absent from the protocol:**
Use CDASH standard domain field counts as estimates. Flag as
`[ESTIMATED — FIELD DETAIL NOT IN PROTOCOL]` in the output.

---

## Step 5: Classify Each Unique CRF

Read `references/crf-complexity-rules.md` and `references/crf-categorization-examples.md`
before classifying.

For each unique CRF, assign: **Simple**, **Average**, or **Complex**

Record:
- Your classification
- The primary reason (field count estimate, repeating group presence, etc.)
- Confidence level: **High** (explicit protocol detail), **Medium** (CDASH
  standard estimate), or **Low** (insufficient information)

---

## Step 6: Calculate Visit Totals

- Visits per patient for each arm
- Total patient visits = (visits per patient × patients) summed across all arms
- Note any visits that may vary per patient (e.g., early discontinuation visits,
  optional visits, replacement patient visits)

---

## Step 7: Identify Complexity Flags

Identify study-level factors that add pricing complexity beyond raw numbers.
Check for:

- Biological, gene therapy, or cell therapy investigational products
- Home-based sample or data collection
- Patient replacement rules
- SAE reporting timelines (24-hour requirements, etc.)
- Multi-arm differential scheduling (different visit schedules per arm)
- Optional or conditional visits
- Regulatory complexity (IND, multiple IRBs, international sites)
- Non-standard CDASH domains or custom assessments
- Any other protocol features your pricing team should be aware of

---

## Step 8: Identify Conditional Branching Points

Identify where branching logic will likely be needed in the EDC build.
Categorize each branching point by type:

**Arm-based branching** — forms or fields that apply to one arm but not another.
Example from this domain: "Inclusion criteria 5 and 8 not applicable to control group"

**Visit-based branching** — forms or fields that only appear at specific visits.
Example: "PSA collected at Screening, W2-3, W8-10, W16-18 only"

**Condition-based branching** — forms or fields triggered by a finding or result.
Example: "Recalculate creatinine clearance if creatinine is abnormal"

**Optional data collection** — items collected only when possible or applicable.
Example: "Semen samples when obtainable"

**Standard CDASH branching to always flag:**
- AE severity ≥ Grade 3 typically triggers SAE assessment fields
- Abnormal lab values typically trigger follow-up fields
- Positive pregnancy test triggers pregnancy reporting workflow

For each branching point output:
- Description of the branch
- Type (arm / visit / condition / optional)
- Affected CRF domain(s)
- Confidence: High / Medium / Low
- Note: `[FIELD-LEVEL DETAIL REQUIRES CRF SPEC CONFIRMATION]` where applicable

---

## Step 9: Estimate Data Cleaning Complexity

For each CDASH domain present in the study, estimate data cleaning complexity
and list implied logical checks.

**Per-domain complexity rating:** Low / Medium / High

**Always include these check categories where applicable:**
- Range checks (physiological or study-defined limits)
- Required field checks (mandatory vs. optional per protocol)
- Cross-form consistency checks (e.g., AE start date must be after first dose date)
- Domain-specific checks (e.g., lab values vs. eligibility thresholds defined in protocol)
- Arm-specific checks (fields required only for certain arms)
- Derived field / calculation checks (e.g., creatinine clearance formula)
- Date logic checks (visit windows, sequence checks)

**Always include this note in the output:**
> Precise check counts require downstream CRF specification and data management
> plan review. This estimate is directional only.

---

## Output Format

Produce ALL THREE outputs below. Do not omit any of them.

---

### OUTPUT 0: Generate the PDF

Before producing any text output, generate the PDF pricing summary using the
script at `scripts/generate_pdf.py`. Follow these steps exactly:

1. Extract all data from the protocol following Steps 1–9 above
2. Assemble the complete data dictionary matching the JSON structure in Output B
3. Run the PDF generation script:

```python
import subprocess
import json

# Write data to a temp file and call the script
data = { ... }  # your assembled data dict

# Save script path
script_path = "/path/to/protocol-to-pricing-summary/scripts/generate_pdf.py"

# Import and call directly
import sys
sys.path.insert(0, "/path/to/protocol-to-pricing-summary/scripts")
from generate_pdf import build_pricing_pdf

output_path = "/mnt/user-data/outputs/{protocol_number}_Pricing_Summary.pdf"
build_pricing_pdf(data, output_path)
```

4. Name the output file using the protocol number:
   `{PROTOCOL_NUMBER}_Pricing_Summary.pdf`
   e.g., `PrTK05_Pricing_Summary.pdf`

5. After generating, use the `present_files` tool to share the PDF with the user

**Important notes for PDF generation:**
- Never use Unicode subscript/superscript characters in ReportLab — use
  `<sub>` and `<super>` XML tags inside Paragraph objects instead
- Use `null` (Python `None`) for any field not found in the protocol
- The `study_title` key should be set at the top level of the data dict
  (not nested inside `study_overview`) for the cover header
- If the script fails, report the error clearly and still produce
  Outputs A and B in text form so the user has the data

---

---

### OUTPUT A: Human-Readable Pricing Summary

Use this exact structure:

```
PROTOCOL PRICING SUMMARY
========================
Generated by: Claude (protocol-to-pricing-summary skill)
Review status: PENDING HUMAN REVIEW
Date: [date]

SECTION 1 — STUDY OVERVIEW
Protocol Number:
Sponsor:
Study Title:
Therapeutic Area:
Study Phase:
Study Type:
Number of Sites: [value or NOT SPECIFIED — PLEASE COMPLETE]
Region(s): [value or NOT SPECIFIED — PLEASE COMPLETE]
Estimated Start Date:
Estimated End Date:
Study Duration (months):

SECTION 2 — PATIENT POPULATION
Total Planned Enrollment:
Number of Arms / Groups:
  [Arm 1 Name]: n= | [description]
  [Arm 2 Name]: n= | [description]
  (add rows as needed)

SECTION 3 — VISIT SUMMARY
  [Arm 1]: X visits per patient × N patients = X total patient visits
  [Arm 2]: X visits per patient × N patients = X total patient visits
  TOTAL PATIENT VISITS (all arms): X

SECTION 4 — CRF SUMMARY
Total Unique CRFs: X
  Simple: X
  Average: X
  Complex: X
Total Re-use CRFs: X

CRF DETAIL:
| Domain | CDASH | Source | Visits Used | Complexity | Re-uses | Confidence | Notes |
|--------|-------|--------|-------------|------------|---------|------------|-------|
(one row per unique CRF)

Source values:
  CDASH ESTIMATE
  CUSTOMER LIBRARY — EXACT MATCH
  CUSTOMER LIBRARY — PARTIAL MATCH — CLASSIFIED ON PROTOCOL REQUIREMENTS
  CDASH ESTIMATE — NO LIBRARY MATCH

SECTION 5 — COMPLEXITY FLAGS
(narrative list of study-level complexity factors)

SECTION 6 — CONFIDENCE & REVIEW NOTES
(list every field or classification where confidence is Medium or Low,
with a plain-language explanation of what is uncertain and what a human
reviewer should verify or complete)

SECTION 7 — CONDITIONAL BRANCHING INDICATORS
(list each branching point with type, affected domain, confidence,
and confirmation note where applicable)

SECTION 8 — DATA CLEANING COMPLEXITY ESTIMATE
(per-domain complexity rating, list of implied checks by category,
followed by the standard precision disclaimer)
```

---

### OUTPUT B: Structured Data Block

After the human-readable summary, output the following JSON block.
Use `null` for any field that is empty or not specified.

```json
{
  "skill_meta": {
    "mode": "PROTOCOL_ONLY or PROTOCOL_WITH_CUSTOMER_LIBRARY",
    "library_files_provided": [],
    "library_format_detected": "MULTI_FILE or SINGLE_BOOKLET or UNCLEAR"
  },
  "study_overview": {
    "protocol_number": "",
    "sponsor": "",
    "therapeutic_area": "",
    "study_phase": "",
    "study_type": "",
    "number_of_sites": null,
    "regions": null,
    "start_date": "",
    "end_date": "",
    "duration_months": null
  },
  "patient_population": {
    "total_enrollment": null,
    "number_of_arms": null,
    "arms": [
      { "name": "", "n": null, "description": "" }
    ]
  },
  "visit_summary": {
    "arms": [
      { "name": "", "visits_per_patient": null, "patients": null, "total_visits": null }
    ],
    "total_patient_visits_all_arms": null
  },
  "crf_summary": {
    "total_unique_crfs": null,
    "simple_crfs": null,
    "average_crfs": null,
    "complex_crfs": null,
    "total_reuse_crfs": null,
    "crf_detail": [
      {
        "domain_name": "",
        "cdash_code": "",
        "source": "CDASH_ESTIMATE | CUSTOMER_LIBRARY_EXACT | CUSTOMER_LIBRARY_PARTIAL | CDASH_NO_LIBRARY_MATCH",
        "customer_form_name": null,
        "visits_used": [],
        "complexity": "",
        "reuse_count": null,
        "confidence": "",
        "notes": ""
      }
    ]
  },
  "complexity_flags": [],
  "confidence_review_notes": [],
  "conditional_branching": [
    {
      "description": "",
      "type": "",
      "affected_domains": [],
      "confidence": "",
      "note": ""
    }
  ],
  "data_cleaning_estimate": {
    "disclaimer": "Precise check counts require downstream CRF specification and data management plan review.",
    "domains": [
      {
        "domain": "",
        "cdash_code": "",
        "complexity_rating": "",
        "implied_checks": []
      }
    ]
  }
}
```

---

## Human Review Instructions

At the end of every output, include this block:

```
─────────────────────────────────────────────
HUMAN REVIEW REQUIRED
─────────────────────────────────────────────
A PDF pricing summary has been generated for your review and download.
Please review the PDF before using it in a pricing model.

1. COMPLETE any fields marked [NOT SPECIFIED — PLEASE COMPLETE]
   → Number of Sites is the most critical missing field for pricing
2. VERIFY all CRF complexity classifications — especially any marked
   Medium or Low confidence
3. REVIEW any CRFs tagged CUSTOMER LIBRARY — PARTIAL MATCH and confirm
   whether the existing customer form can be adapted or requires a new build
4. REVIEW any CRFs tagged CDASH ESTIMATE — NO LIBRARY MATCH and confirm
   whether the customer library has a form that was not detected
5. CONFIRM conditional branching points are complete
6. ADD corrections to references/crf-categorization-examples.md using
   the template in that file so future runs improve automatically
7. UPDATE references/crf-complexity-rules.md if the classification
   definition itself needs to change

Questions or corrections? Add them to crf-categorization-examples.md.
─────────────────────────────────────────────
```
