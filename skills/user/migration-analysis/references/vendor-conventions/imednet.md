# iMedNet

## Overview

- Vendor: Mednet Solutions
- Product: iMedNet EDC
- Known versions: 5.x, 6.x
- ODM version exported: 1.3.2

## Detection

- `ODM/@Originator` contains `"iMedNet"` → `source_system = "iMedNet"`.
- `SourceSystemVersion` carries the iMedNet release (e.g. `"6.2"`).
- `xmlns:imn="http://www.imednet.com/ns/odm"` declaration is present in
  newer exports but is optional — many iMedNet studies export pure ODM
  with no extension namespace at all.

## Namespace

- URI: `http://www.imednet.com/ns/odm` (when present)
- Prefix: `imn`
- Vendor attributes captured into `vendor_specific` (when present):
  - `imn:FormSubtype` — informational form classification
  - `imn:VisitId`, `imn:FormId` — iMedNet internal numeric ids
  - `imn:RoleVisibility` — role-based display flags

When the namespace is absent, treat the export as plain ODM and capture
nothing vendor-specific beyond the `source_system` label.

## ODM Structural Patterns

- Largely clean ODM 1.3.2.
- Standard `Study → MetaDataVersion → Protocol → StudyEventDef → FormDef →
  ItemGroupDef → ItemDef` nesting.
- Visit-based scheduling — no folder/matrix layer.

## OID Conventions

- Standard short OIDs (`F_DM`, `IG_AE`, `SE_SCREEN`).
- iMedNet OIDs sometimes include a study-prefix segment
  (`SE_STUDY123_VISIT_1`) — the transform's `_oc_event_oid` collapses
  these via uppercasing and `SE_` normalisation.

## Form Structure Quirks

- Log-line forms (AE, CM) use the standard `FormDef/@Repeating="Yes"`
  pattern.
- iMedNet supports "iForms" (dynamic form variants) — only the resolved
  base form appears in the ODM export.
- No multi-section forms beyond standard ItemGroups.

## Event/Visit Mapping

- Standard `StudyEventDef` → `SE_<NAME>`.
- iMedNet "unscheduled visits" map to `SE_UNSCHEDULED` (synthesised if
  absent in the source).
- AE / CM / DV pin to `SE_COMMON` per OC-9.

## Codelist Handling

- Standard ODM `CodeList`/`CodeListItem` with English decodes.
- No vendor-specific codelist tagging.

## Clinical Data Patterns

- Standard `ClinicalData` hierarchy.
- `SubjectData/@SubjectKey` = iMedNet subject id.

## Known Export Limitations

- iMedNet dynamic-form logic is not captured in standard ODM exports.
- Edit-check rules expressed in iMedNet Designer are not in the export.
- Calculated items export the result/declaration but not the formula.

## OC4 Transform Rules

- Treat as generic ODM unless `imn:` namespace is detected — in which
  case preserve attributes in `vendor_specific`.
- `FormDef/@Repeating="Yes"` drives OC-8 repeating wrapper.
- Standard CDASH naming; no special form-id mapping required.

## Compliance Notes

- iMedNet is 21 CFR Part 11 validated.
- Subject identifiers are typically site-scoped sequence numbers.
- Signature events live in the iMedNet audit log, not the metadata
  export.
