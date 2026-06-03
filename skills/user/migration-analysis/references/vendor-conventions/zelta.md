# Zelta (Merative)

## Overview

- Vendor: Merative (formerly IBM Watson Health → IBM Clinical
  Development → Merge eClinical)
- Product: Zelta EDC
- Known versions: 2023.x, 2024.x
- ODM version exported: 1.3.2

## Detection

- `ODM/@Originator` contains `"Merative"` or `"Zelta"` →
  `source_system = "Zelta (Merative)"`.
- Detection is Originator-only — no widely-documented public namespace.
- `SourceSystemVersion` carries the Zelta release (e.g. `"2024.5"`).

## Namespace

- No widely-published vendor namespace. When present in older exports
  these legacy prefixes may appear:
  - `xmlns:ibm="http://www.ibm.com/clinical/odm"` (legacy IBM Clinical
    Development)
  - `xmlns:merge="http://www.mergehealthcare.com/ns/odm"` (legacy Merge)
- Capture any unknown namespaces in `vendor_specific` and log a parse
  warning — do not fail on their presence.

## ODM Structural Patterns

- Standard ODM 1.3.2 with negligible vendor extension.
- Repeating event groups (treatment cycles, dosing periods) encoded as
  repeating `StudyEventDef` entries with `Repeating="Yes"`.
- Standard form/group/item nesting.

## OID Conventions

- Standard short OIDs in CDASH style (`F_DM`, `IG_AE`, `I_DM_SUBJID`).
- Event OIDs may use either `SE_<NAME>` or bare `<NAME>` — the transform
  normalises both via `_oc_event_oid`.

## Form Structure Quirks

- Log-line forms (AE, CM, MH, EX) use the standard
  `FormDef/@Repeating="Yes"` + repeating `ItemGroupDef` pattern.
- Zelta supports multi-period dosing schedules with one `FormDef` reused
  across periods — same handling as Veeva cycles: one OC4 form with
  multi-event `visits_assigned`.

## Event/Visit Mapping

- Standard `StudyEventDef` → `SE_<NAME>`.
- Repeating cycle/period events map directly to OC4 repeating events.
- AE / CM / DV / AESAE pin to `SE_COMMON` per OC-9.

## Codelist Handling

- Standard ODM `CodeList`/`CodeListItem` — no vendor-specific tagging.
- Decode language tags default to `xml:lang="en"`.

## Clinical Data Patterns

- Standard `ClinicalData` hierarchy.
- `SubjectData/@SubjectKey` = Zelta subject identifier.
- Repeating period data uses `StudyEventData/@StudyEventRepeatKey`.

## Known Export Limitations

- Edit-check rules expressed in Zelta Designer are not in the metadata
  export; protocol-driven validations during AI enrichment fill the gap.
- Lab unit conversions and reference ranges live in Zelta Lab Admin —
  `labranges_csv` rows are placeholders.
- Some 2023.x exports omit `MeasurementUnitRef` entries on items that
  clearly have units in the source study; flag for review.

## OC4 Transform Rules

- Treat Zelta as generic ODM with the standard OC-1..OC-9 rules unless a
  recognised legacy `ibm:` / `merge:` namespace is present (in which case
  preserve those attributes in `vendor_specific`).
- Repeating cycle structure → standard OC4 repeating events; no special
  cycle-naming pattern is enforced.
- Standard CDASH naming — minimal transform.

## Compliance Notes

- Zelta inherits the Merge eClinical / IBM Clinical Development Part 11
  validation lineage.
- Tenant hosting varies (AWS US, EU, APAC); confirm region for GDPR
  before migrating subject metadata.
- Signature events live in the Zelta audit log, not the metadata export.
