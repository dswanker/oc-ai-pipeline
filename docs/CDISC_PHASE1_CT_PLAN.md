# CDISC Phase 1: Controlled Terminology for every build

Scope: the pipeline in general. Every study, every form, every select field. No customer- or
protocol-specific rules, form IDs or field lists anywhere in the code.

## Problem
Choice lists today come from Claude's generation plus customer CHOICES.csv. Nothing checks them
against CDISC CT. Failure modes: invented or misspelled codes, missing lists (select field references
a list that was never defined), and inconsistent lists for the same concept across forms and studies.
`_fix_missing_choices` handles the missing-list case by deleting the field.

## Design
1. CT source module (`cdisc_ct.py`), one interface, swappable backend:
   - Now: NCI EVS quarterly files (SDTM + CDASH terminology). No key needed. EVS is CDISC's CT publisher.
   - Later: CDISC Library API, same interface.
   - Cached on the Railway volume; pinned CT package date; refresh is explicit, not per run.
2. Field-to-codelist binding (general rule, applied to every select_one/select_multiple):
   - Now: CDISC variable name -> codelist short name (e.g. SEX -> SEX, AESEV -> AESEV), then
     domain-prefix removal (AEACN -> ACN, CMROUTE -> ROUTE), then Y/N-typed fields -> NY.
     Fields that do not bind are left alone (sponsor-defined lists like AE causality).
   - Later: exact binding from CDASHIG variable metadata (Library).
3. Apply after spec extraction, before build, in all build paths:
   - Non-extensible codelist: submission values must be CT values; a subset is allowed.
   - Extensible codelist: CT values plus customer additions allowed.
   - Customer CHOICES.csv / conventions still win on labels and on which values are offered.
   - Non-conformant values are flagged in QA, never silently dropped. Fields are never deleted.
4. `_fix_missing_choices`: when a field binds to a codelist, fill the list from CT instead of
   deleting the field. Unbound + undefined keeps today's behaviour, logged.
5. Prompting: give Claude the relevant CT codelists as reference so generation starts correct.
6. Traceability: record CT package date and per-field codelist code (NCI C-code) in the Study Spec
   JSON; show CT version on the Study Spec PDF and in the DVS.

## Verification
- Unit tests on the binding rule and the conformance check (synthetic forms).
- Run the post-processor over existing specs in the repo and report bound / conformant /
  extended / non-conformant counts, without changing output first (report-only mode).
- Then enable enforcement behind an env flag; default on once the report looks right.

## What changes when the Library API key works
- Backend swap from EVS to Library behind the same interface. CT content is the same data.
- Binding rule replaced by exact CDASHIG variable -> codelist metadata. Fewer unbound fields.
- Unlocks Phase 2 (CDASH field metadata), Phase 3 (Biomedical Concepts), and CORE rule metadata.
- Phase 1 design, enforcement rules and QA output do not change.

## Library API status (2026-10-07)
- Portal subscription "CDISC Library API" is Active under dswanker@mac.com.
- Key in Railway is valid (gateway accepts it) but every content endpoint returns
  401 "Members-only content". Header `api-key`, host https://api.library.cdisc.org/api are correct.
- Next checks: API Tester on the portal with the same key; confirm Railway key matches the portal
  primary key; ask CDISC support to link the account to the member organization.
