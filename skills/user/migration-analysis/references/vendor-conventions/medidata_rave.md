# Medidata Rave

## Overview

- Vendor: Medidata Solutions
- Product: Medidata Rave EDC
- Known EDC versions: 5.x (5.6, 5.7), 2024.x
- ODM version exported: 1.3.2 (with `mdsol` extension namespace)

## Detection

- `ODM/@Originator` contains `"Medidata"` or `"Rave"` → `source_system = "Medidata Rave"`.
- `SourceSystemVersion` (when present) carries the Rave build (e.g. `"5.6"`).
- Namespace declaration `xmlns:mdsol="http://www.mdsol.com/ns/odm/metadata"`
  on the `ODM` element is a secondary signal.

## Namespace

- URI: `http://www.mdsol.com/ns/odm/metadata`
- Prefix: `mdsol`
- Vendor attributes the parser captures into `vendor_specific`:
  - `mdsol:Active` — boolean on most metadata elements
  - `mdsol:IsLog` — `"Yes"` marks a log-line repeating ItemGroup/Form
  - `mdsol:LogDirection` — `"Horizontal"` / `"Vertical"`
  - `mdsol:RecordsPerRow` on `ItemGroupDef`
  - `mdsol:StudyEventDefType`, `mdsol:Repeating`, `mdsol:Name` on
    `StudyEventRef` in `Protocol`
  - `mdsol:PrimaryFormOID`, `mdsol:DefaultMatrixOID`,
    `mdsol:SignaturePrompt` on `MetaDataVersion`

## ODM Structural Patterns

- Standard ODM hierarchy: `Study → MetaDataVersion → Protocol →
  StudyEventDef → FormDef → ItemGroupDef → ItemDef`.
- Rave adds a matrix/folder layer above events; the export flattens it and
  references the primary matrix via `mdsol:DefaultMatrixOID`.
- `Protocol/StudyEventRef` carries Rave-specific `mdsol:` attributes that
  describe scheduling and repetition independent of ODM `Repeating`.

## OID Conventions

- Standard ODM short OIDs: `F_AE`, `IG.AE`, `I.AE.AETERM`, `SE_SCREEN`,
  `CL.AESEV`.
- Form OIDs almost always prefixed `F_`; item OIDs typically `I.<FORM>.<NAME>`.
- No normalisation required for OC4 beyond the standard `_oc_form_id` /
  `_oc_item_name` rules.

## Form Structure Quirks

- Log-line repeating forms (AE, CM, MH, DV, EX, PC, etc.) carry
  `mdsol:IsLog="Yes"` on the `ItemGroupDef` and `Repeating="Yes"` on the
  `FormDef`. Both must be detected — log-line semantics drive the OC-8
  repeating structure.
- Repeating forms emit `begin repeat <FORM_ID>_LOG` + `end repeat` at the
  end of the form's survey rows. The inner item-group `begin group` /
  `end group` are emitted by the standard ItemGroup loop above.
- `mdsol:LogDirection="Horizontal"` is informational — OC4 renders all log
  lines vertically.

## Event/Visit Mapping

- `StudyEventDef OID="SE_<NAME>"` maps directly to OC4 events.
- AE / CM / DV / AESAE forms pin to `SE_COMMON` per OC-9 regardless of the
  `FormRef` list in source events.
- Rave's "Common Events" pseudo-event is folded into `SE_COMMON` by the
  deterministic transform.

## Codelist Handling

- Standard ODM `CodeList`/`CodeListItem` with `Decode/TranslatedText`.
- `mdsol:Active` on `CodeListItem` may be `"No"` for retired choices — the
  transform currently keeps them; flag for review if a retired-choice value
  appears in `ClinicalData`.
- AE severity / outcome lists reuse the canonical short codes
  (`MILD`/`MODERATE`/`SEVERE`, etc.).

## Clinical Data Patterns

- `ClinicalData/SubjectData/StudyEventData/FormData/ItemGroupData/ItemData`.
- `ItemGroupData/@mdsol:Submission="SpecifiedItemsOnly"` indicates Rave
  only emitted dirty items rather than the full record — treat absent items
  as "not modified", not "missing".
- `SubjectData/@SubjectKey` is the Rave subject id; map to OC4 SUBJID.

## Known Export Limitations

- Rave exports may include matrix/folder structure not present in the
  standard ODM grammar — treat as flat after applying `DefaultMatrixOID`.
- Edit-check expressions (`mdsol:RangeCheck`, `mdsol:CheckStep`) are
  proprietary and not consumed by the deterministic transform; protocol-
  driven validations are added during AI enrichment.
- Lab unit conversions and reference ranges live outside the ODM in
  Rave Lab Admin — `labranges_csv` rows are placeholders until protocol
  data is loaded.

## OC4 Transform Rules

- `mdsol:IsLog="Yes"` OR `FormDef/@Repeating="Yes"` → set
  `has_repeating_group=True`, apply OC-8 repeating structure with
  `begin repeat <FORM_ID>_LOG` / `end repeat` wrapper.
- AE / CM / DV / AESAE `FormDef` → `visits_assigned = ["SE_COMMON"]`
  (OC-9), independent of the source event list.
- `mdsol:DefaultMatrixOID` is informational — do not propagate to OC4.
- Codelist OIDs `CL.<NAME>` are renamed via `_safe_list_name` for
  XLSForm; preserve the original in `vendor_specific` for traceability.

## Compliance Notes

- Rave is 21 CFR Part 11 validated; signatures captured via
  `mdsol:SignaturePrompt` are not part of the metadata export. If the
  protocol requires signed-form replication in OC4, surface in
  `review_flags.protocol_ambiguous`.
- Audit trail data is exported separately from the metadata ODM and is
  out of scope for the migration metadata transform.
