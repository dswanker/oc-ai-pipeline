# Castor EDC

## Overview

- Vendor: Castor (formerly Castor EDC B.V.)
- Product: Castor EDC
- Known versions: 2023.x, 2024.x
- ODM version exported: 1.3.2

## Detection

- `ODM/@Originator` contains `"Castor"` → `source_system = "Castor EDC"`.
- `SourceSystemVersion` carries the Castor release (e.g. `"2024.3"`).
- `xmlns:castor="http://www.castoredc.com/ns/odm"` declaration is a
  secondary signal.

## Namespace

- URI: `http://www.castoredc.com/ns/odm`
- Prefix: `castor`
- Vendor attributes captured into `vendor_specific`:
  - `castor:FieldType` — Castor-specific widget hints
  - `castor:RepeatingDataName` — name of a Castor "Repeating Data" set
  - `castor:CrfId`, `castor:VisitId`, `castor:FieldId` — UUIDs that mirror
    the metadata OIDs

## ODM Structural Patterns

- Standard ODM hierarchy; Castor adds the "Repeating Data" construct
  for log-line-style data collection. Repeating Data records map to
  repeating ItemGroups.
- Castor "phases" map to `StudyEventDef` (study events).
- Castor "steps" within a CRF flatten to ItemGroups within a `FormDef`.

## OID Conventions

- All OIDs are UUIDs:
  `15E88A04-9CB8-4B30-9A3C-B1DBFC96CD88`.
- OC4 normalisation is mandatory — UUIDs must not propagate into OC4 OIDs:
  - Strip hyphens.
  - Prefer the human-readable `Name` attribute when present (e.g. CRF
    name `"Adverse Events"` → `AE` via CDASH mapping).
  - Fallback: truncate the de-hyphenated UUID to 20 chars and prefix with
    the entity type code (`F_`, `IG_`, `I_`, `SE_`).
- Preserve the original UUID in `vendor_specific` for downstream
  traceability and round-trip checks.

## Form Structure Quirks

- Castor "Repeating Data" sets are independent of the main CRF flow:
  they attach to a parent record (subject, visit, or other repeating
  data) and produce repeating ItemGroups in the ODM export. Apply the
  OC-8 repeating wrapper exactly as for log-line forms.
- Calculation fields export with `@OriginType="Derived"` but the formula
  is Castor-proprietary and is not included in the export.
- Conditional logic (Castor "Dependencies") is stored separately from the
  ODM in the Castor admin; not present in standard exports.

## Event/Visit Mapping

- Phase → `SE_<PHASE_NAME>` after normalisation.
- Castor visits within a phase map to event repeats or extra events
  depending on whether the phase is repeating.
- AE / CM / DV pin to `SE_COMMON` per OC-9.

## Codelist Handling

- Standard ODM `CodeList` with decoded values.
- Castor "option groups" reused across multiple fields share a single
  `CodeListOID`; preserve referential reuse during transform.

## Clinical Data Patterns

- `ClinicalData/SubjectData` keyed by Castor `participant_id` (mapped to
  `@SubjectKey`).
- Repeating Data records appear as `ItemGroupData` with
  `@ItemGroupRepeatKey` plus the `castor:RepeatingDataName` attribute
  identifying which repeating set produced the row.

## Known Export Limitations

Per Castor's own documentation the following are not present in the ODM
export:

- Grid / table fields (export as concatenated text where present at all).
- Measurement units on numeric items.
- Repeated-measure visit metadata beyond the basic `Repeating` flag.
- Field widths / display sizes.
- Help / info text below the field label.
- Data-validation expressions (range and consistency checks).
- File-upload fields export filenames only, not file bodies.

Treat all of the above as "must be re-derived from the protocol PDF" in
the AI enrichment pass.

## OC4 Transform Rules

- UUID OIDs → use Name attribute as the primary identifier; fall back to
  the sanitised UUID (de-hyphenated, truncated, prefixed).
- Preserve original UUID in `vendor_specific.castor_oid` per element.
- Castor Repeating Data → OC-8 repeating ItemGroup with
  `begin repeat <FORM_ID>_LOG`.
- Missing measurement units → numeric items default to `text` with a
  review flag (`choice_list_review` or `protocol_ambiguous` as
  appropriate).
- Missing range checks → constraints driven entirely by the protocol PDF
  during AI enrichment.

## Compliance Notes

- Castor is ISO 27001 / SOC 2 audited and supports Part 11 mode.
- Most Castor tenants are EU-hosted (Amsterdam). For GDPR-sensitive
  migrations, confirm the OC4 tenant region matches the data origin.
- Subject identifiers in Castor are pseudonymised participant numbers; do
  not assume they are direct identifiers.
