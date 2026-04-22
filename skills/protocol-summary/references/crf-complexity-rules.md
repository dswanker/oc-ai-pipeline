# CRF Complexity Classification Rules

**Last updated:** April 2026  
**Owner:** OpenClinica Service Delivery  
**How to update:** Edit this file only. The skill reads this file before every classification. Changes apply immediately to all future runs. Do not modify SKILL.md to change complexity rules.

---

## Classification Tiers

### Simple
- 20 or fewer data items on the CRF
- AND no repeating groups (no table/grid structures)

### Average
- 21 to 50 data items
- OR exactly 1 repeating group (one table/grid structure), regardless of field count

### Complex
- More than 50 data items
- OR more than 1 repeating group (two or more table/grid structures)
- A CRF with 2 tables plus non-table data items = Complex regardless of total field count

---

## Repeating Group Definition

A repeating group is a table-like structure on a CRF where the same set of fields can be filled in multiple times — one row per occurrence. Examples:

- Adverse Events (AE) — one row per event reported
- Concomitant Medications (CM) — one row per medication
- Medical History (MH) — one row per condition
- Prior Procedures — one row per procedure
- Laboratory Results (LB) — sometimes structured as a repeating panel

A CRF domain that contains TWO such tables (e.g., a form capturing both Adverse Events and Concomitant Medications in a single domain, plus some header fields) = 2 repeating groups = Complex.

---

## CDASH Domains Known to Contain Repeating Groups

Use this as a default classification guide when the protocol does not provide field-level detail:

| CDASH Domain | Repeating? | Default Tier |
|-------------|------------|--------------|
| AE — Adverse Events | Yes (1 group) | Average (minimum) |
| CM — Concomitant Medications | Yes (1 group) | Average (minimum) |
| MH — Medical History | Yes (1 group) | Average (minimum) |
| LB — Laboratory Test Results | Yes (1 group) | Average (minimum) |
| VS — Vital Signs | No | Simple (unless >20 fields) |
| DM — Demographics | No | Simple |
| DS — Disposition | No | Simple |
| EX — Exposure | No | Simple to Average |
| PE — Physical Examination | No | Simple to Average |
| PR — Procedures | Yes (1 group) | Average (minimum) |
| SC — Subject Characteristics | No | Simple |
| SV — Subject Visits | No | Simple |
| TU — Tumor Results | Yes (1+ groups) | Average to Complex |
| RS — Disease Response | No | Average |
| FA — Findings About | Varies | Review required |
| BS — Biospecimen | No | Simple to Average |

---

## Conditional Logic Note

Conditional branching logic (show/hide field rules, skip patterns) does NOT by itself change the complexity tier. However, it should be flagged in Section 7 of the output for CRF design review. A form with extensive conditional logic may warrant an upward tier adjustment at human reviewer discretion.

---

## Override Rule

If human review produces a correction to Claude's classification, that correction is recorded in `crf-categorization-examples.md` and takes precedence over these general rules for similar future cases.
