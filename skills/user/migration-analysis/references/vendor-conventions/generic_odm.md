# Generic ODM

## Overview

- Vendor: Unknown / unrecognised
- Product: Any EDC emitting standard CDISC ODM
- Known versions: ODM 1.3.0, 1.3.1, 1.3.2
- ODM version exported: 1.3.x as declared by the source

Used as the fallback when `odm_to_spec.load_vendor_conventions(...)` cannot
match `source_system` to a known vendor file. Encodes only the rules that
apply to every conformant ODM export.

## Detection

- Selected when `odm_reader._detect_vendor` returns `"UNKNOWN"`, or when
  `source_system` does not match any key in `VENDOR_CONVENTION_FILES`.
- Also selected when `Originator`/`SourceSystem` is missing or the value
  is not in our recognised vendor list.

## Namespace

- None required.
- Any non-standard XML namespace observed on the input is captured into
  `vendor_specific.<prefix>` per element. A parse warning is logged so the
  unknown extension is visible without failing the parse.

## ODM Structural Patterns

- Conformant CDISC ODM:
  `Study → MetaDataVersion → Protocol → StudyEventDef → FormDef →
  ItemGroupDef → ItemDef`.
- Codelists via `CodeList`/`CodeListItem`.
- Clinical data via `ClinicalData/SubjectData/StudyEventData/FormData/
  ItemGroupData/ItemData`.

## OID Conventions

- Standard short OIDs are assumed. The transform applies the generic
  normalisers:
  - Events: `_oc_event_oid` → ensure `SE_` prefix, uppercase.
  - Forms: `_oc_form_id` → strip `F_` / `CRF_` / `FORM_` prefixes,
    uppercase, CDASH-map when recognised, cap length at 20 chars at a
    natural underscore boundary.
  - Items: `_oc_item_name` → uppercase, prefer CDASH alias when present.
  - ItemGroups: `_oc_itemgroup` → strip `IG_` / form-prefix, uppercase.

## Form Structure Quirks

- Repeating detection: `FormDef/@Repeating="Yes"` OR
  `ItemGroupDef/@Repeating="Yes"` triggers the OC-8 wrapper.
- Multi-group forms emit one `begin group` / `end group` pair per
  ItemGroup. Field names are deduped within each group to avoid pyxform
  unique-name collisions.

## Event/Visit Mapping

- One `StudyEventDef` → one `SE_<NAME>` event in `timepoint_csv`.
- `SE_COMMON` is synthesised if absent.
- AE / CM / DV / AESAE pin to `SE_COMMON` per OC-9.

## Codelist Handling

- `CodeList/CodeListItem` consumed as-is.
- `Decode/TranslatedText[@xml:lang="en"]` is the preferred decode; fall
  back to the first translation when English is absent.
- Codelist OIDs are sanitised for XLSForm via `_safe_list_name`.

## Clinical Data Patterns

- Standard `ClinicalData` hierarchy.
- `SubjectData/@SubjectKey` → OC4 SUBJID.
- `StudyEventData/@StudyEventRepeatKey` and
  `ItemGroupData/@ItemGroupRepeatKey` carry repeat instance ids.

## Known Export Limitations

- Edit-check expressions vary by vendor and are commonly omitted from
  pure-ODM exports — most validations must be re-derived from the
  protocol PDF during AI enrichment.
- Calculated items typically export the declaration but not the formula.
- Lab reference ranges are vendor-specific extensions and are not part of
  standard ODM — `labranges_csv` rows are placeholders.

## OC4 Transform Rules

- Apply OC-1 through OC-9 unconditionally; they take precedence over any
  rule below.
- Apply the deterministic transform in `odm_to_spec.transform`:
  - CDASH domain mapping by form name / OID fragment.
  - OC-9 pinning for AE/CM/DV/AESAE.
  - OC-8 repeating wrapper for repeating forms.
  - SUBJID injection on forms that lack it.
  - Per-group field-name deduplication.
- Capture any unrecognised vendor namespace attributes in
  `vendor_specific` rather than failing.
- During AI enrichment, ask Claude to lean on the protocol PDF for
  constraint/relevant expressions and study metadata gaps; do not let it
  invent structural elements (events, forms, items) that the ODM does
  not contain.

## Compliance Notes

- Compliance posture is unknown — do not assume Part 11 / GDPR / ICH
  conformance. If the destination OC4 tenant carries those obligations,
  add review-flag entries asking the migration owner to confirm.
- Treat subject identifiers as potentially identifying until shown
  otherwise; preserve them verbatim but do not synthesise additional
  identifying fields.
