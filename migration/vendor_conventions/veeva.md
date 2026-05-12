# Veeva Vault CDMS

## Overview

- Vendor: Veeva Systems
- Product: Veeva Vault CDMS (Clinical Data Management System)
- Known versions: 23.x, 24.x
- ODM version exported: 1.3.2

## Detection

- `ODM/@Originator` contains `"Veeva"` or `"Vault"` →
  `source_system = "Veeva Vault CDMS"`.
- `SourceSystemVersion` carries the Vault release (e.g. `"24R2"`).
- `xmlns:v="http://www.veevavault.com/ns/odm"` declaration when used.

## Namespace

- URI: `http://www.veevavault.com/ns/odm`
- Prefix: `v` (sometimes `veeva`)
- Vendor attributes captured into `vendor_specific`:
  - `v:LifecycleState` — Veeva Vault lifecycle for the metadata object
  - `v:ObjectName` — internal Vault object reference
  - `v:VersionId` — Vault version id of the source metadata record

Note: Veeva exports use vendor extensions sparingly — most of the
metadata is plain ODM 1.3.2 with only occasional `v:` attribution.

## ODM Structural Patterns

- Largely standard ODM. Veeva models the study as
  `Study → MetaDataVersion → Protocol → StudyEventDef → FormDef → ItemGroupDef → ItemDef`.
- Treatment cycles encoded as repeating `StudyEventDef` entries named
  `CYCLE1`, `CYCLE2`, `CYCLE3`, ... — propagate as OC4 repeating events.
- No matrix/folder construct above events.

## OID Conventions

- Standard short OIDs in CDASH style.
- Cycle event OIDs: `SE_CYCLE_<N>` after normalisation (`CYCLE1` →
  `SE_CYCLE_1`).
- Form OIDs follow CDASH (`F_DM`, `F_AE`, `F_VS`, etc.).

## Form Structure Quirks

- Veeva log-line forms (AE, CM) use `FormDef/@Repeating="Yes"` plus a
  single repeating `ItemGroupDef` — standard OC-8 wrapper.
- Multi-cycle dosing forms reuse the same `FormDef` across multiple
  `CYCLE` events; the OC4 transform emits one form per CDASH domain and
  relies on `visits_assigned` to encode reuse.

## Event/Visit Mapping

- Standard `StudyEventDef` → `SE_<NAME>` normalisation.
- Cycle events (`CYCLE1` ... `CYCLE3`) → `SE_CYCLE_1` ... `SE_CYCLE_3`,
  each marked `Repeating="Yes"` when applicable.
- AE / CM / DV / AESAE pin to `SE_COMMON` per OC-9.

## Codelist Handling

- Standard ODM `CodeList`/`CodeListItem`.
- Codelist OIDs typically `CL.<DOMAIN>.<CONCEPT>` (e.g. `CL.AE.SEVERITY`).
- No vendor-specific decode tagging observed.

## Clinical Data Patterns

- Standard `ClinicalData` hierarchy.
- `SubjectData/@SubjectKey` = Veeva subject identifier.
- Repeating event data uses `StudyEventData/@StudyEventRepeatKey` with
  integer keys matching the cycle number.

## Known Export Limitations

- Veeva uses configurable picklists (`v:LifecycleState`-driven) — those
  do not always export with their full code/decode mapping.
- Vault notebook / e-source attachments are not part of the standard ODM
  export.
- Edit-check rules expressed as Veeva Studio rules are not in the export.

## OC4 Transform Rules

- Veeva cycle events `CYCLE<N>` → `SE_CYCLE_<N>` in OC4.
- Treatment cycle reuse → emit one OC4 form with multi-event
  `visits_assigned`, never duplicate the form per cycle.
- Standard CDASH naming — minimal transform; rely on the generic
  `_oc_form_id` / `_oc_item_name` paths.
- `FormDef/@Repeating="Yes"` → OC-8 repeating wrapper.

## Compliance Notes

- Veeva Vault CDMS is 21 CFR Part 11 validated; signatures captured via
  Vault eSignature events live outside the ODM metadata export.
- Vault tenants are region-pinned for GDPR; confirm origin before
  transferring metadata into a US-hosted OC4 tenant.
