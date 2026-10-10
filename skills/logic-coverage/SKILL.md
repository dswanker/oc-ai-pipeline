---
name: logic-coverage
description: >
  Audits the edit-check and form logic of an OpenClinica 4 study against a deterministic coverage catalog and
  fills the gaps: required fields, date and numeric rules, other/specify and gate show-when logic, within-form
  consistency (start/end, ongoing, clinically significant, serious, fatal, not done), cross-form checks (consent
  floor, first dose, death, eligibility) and derived helper items. Works inside the pipeline on the Study
  Specification, or standalone on a customer's existing build (XLSForm files or ZIP, an ODM export, or the forms
  of an existing OpenClinica study). Use it whenever a user asks what logic a study is missing, to add standard
  checks to forms that have none, or to prepare an existing study for UAT.
---

# Logic Coverage Skill

## Purpose

A build can be structurally complete and still carry almost no logic: a customer standard supplied as an ODM gives
items and code lists but no constraints, no show-when rules and no calculations. This skill answers three questions
for every form and field, and acts on the answer:

1. Which checks of the catalog **apply** here? Decided from the field's type and its CDASH concept
   (`cdisc_concepts.py`, CDASHIG metadata, CDISC controlled terminology). Never from the wording of a label.
2. Is each one **covered** by logic already in the form, already **proposed**, or **missing**?
3. For a missing one: **build** it or **propose** it.

Everything is deterministic. No AI call is made by the skill; protocol-specific checks come from the pipeline's
validated AI edit-check call and are counted here as proposed.

## Build or propose

| Form | Missing check is |
|---|---|
| Built by the pipeline from the protocol | built (Draft in the DVS) |
| Customer standard or existing form with **no logic of its own** (ODM source, or an XLSForm with no constraint, show-when or calculation) | built (Draft), with the reason in the DVS Notes |
| Customer standard or existing form that **carries its own logic** | proposed (Status Proposed; Action = Approve builds it) |
| Any form, a check marked **review** | proposed, with what to confirm |
| Any form, **report mode** | proposed; no form is changed |

`STANDARD_LOGIC_FREE_APPLY=0` turns the second row into "proposed". `LOGIC_COVERAGE=0` turns the skill off in the
pipeline. `LOGIC_COVERAGE_HELPERS=0` reports the derived helper items and changes none of them.

## The catalog

Field level

| Category | Applies to | Check |
|---|---|---|
| Required | items CDASH marks Highly Recommended | required (review: a study decision) |
| Date not in the future | date and datetime fields, unless the field already requires a future date | `. <= today()` |
| Partial date parts | integer year / month / day parts that share a base name | year 1900 to current, month 1 to 12, day 1 to 31 |
| Numeric range | integer and decimal fields | vital signs by CDISC test code (per unit when the form offers two); age 0 to 120; counts, results and doses not negative. A decimal without a concept has no template and is reported as missing |
| Other, specify | a pick list with the CDISC value OTHER and the text item that follows it | specify shown only with Other |
| Gate | a `--YN` / `--PERF` / `--OCCUR` prompt and the items of the same domain after it in its item group | details shown only when the prompt is Yes (dose details: CORE-000004/137). An item without a concept is never hidden |
| Derived helper items | label-less, non-required items of a form that came without logic | calculation rebuilt when the same item is calculated elsewhere in the study, otherwise read-only and flagged |

Within a form

| Category | Check |
|---|---|
| Not done requires a reason | `--REASND` shown and required when the prompt is No (CORE-000440) |
| End on or after start | `--ENDAT >= --STDAT` |
| Ongoing versus end date | end date shown and required when not ongoing |
| Clinically significant | `--DESC` shown and required when `--CLSIG` is Yes |
| Serious requires a criterion | CORE-000022 |
| Fatal outcome | `AEOUT` FATAL requires `AESDTH` Yes |

Across forms (each adds a hidden cross-form fetch and the check)

| Category | Check |
|---|---|
| Consent floor | on-study dates `>=` the informed consent date. Domains that can hold pre-consent assessments: review |
| History before consent | `MHSTDAT <=` consent date (review) |
| AE start versus first dose | flags a pre-treatment event (review: not an error where the protocol collects events from consent) |
| Death consistency | a death date equals the death date on the other form (CORE-000034) |
| Dosing only when eligible | needs an overall eligibility verdict item (review) |
| Pregnancy dates versus dosing | forms of the pregnancy domain (review) |
| Dosing within the visit window | reported, not generated: the specification holds no visit windows; the scheduler rules enforce them |

Checks are written with the conventions engine's `add_constraint` and `lookup_from`, so they are combined with an
existing constraint, never duplicate one that is already there, and carry a stable check id (`LC.<CATEGORY>`).

## Inputs

- **In the pipeline:** the Study Specification (`pipeline._logic_coverage_step`), after the conventions engine and
  the AI proposals, before the build, the DVS and the Study Specification files are written.
- **Standalone:** `scripts/standalone.py` takes XLSForm files, a ZIP of them, an ODM export, or forms fetched
  read-only from an OpenClinica study by `reference_studies.py`, plus (optionally) which form is on which event.

## Outputs

- `study_meta.logic_coverage` in the specification: the audit rows, what was added, the proposals, what could not
  be generated and why, the cross-form checks, the derived helper items.
- DVS: every added check is a Draft row (Check Source `Logic Coverage` or the CDISC CORE rule), every proposal a
  Proposed row, and the `LOGIC_COVERAGE` sheet has one row per form and category plus the helper items.
- Study Specification: a LOGIC COVERAGE section (PDF) and sheet (XLSX) with the totals per category.
- Standalone: `forms/*.xlsx` with the applied logic (apply mode), `<protocol>_DVS.xlsx` with the UAT cases of the
  existing generator, `<protocol>_Logic_Coverage.md`.

```
python skills/logic-coverage/scripts/standalone.py build.zip --out out/ --mode apply --protocol STUDY-1
python skills/logic-coverage/scripts/standalone.py export.xml --out out/ --mode report
```

## What it does not do

- It does not load data or talk to OpenClinica. UAT loading and read-back is `uat_loader.py`.
- It does not invent a range for a number whose meaning is unknown, hide an item whose meaning is unknown, or
  generate a visit-window check without windows.
- A rebuilt or read-only helper item is reported one by one; the customer's own derivation, where it existed in
  their original forms, is not recoverable from an ODM.
