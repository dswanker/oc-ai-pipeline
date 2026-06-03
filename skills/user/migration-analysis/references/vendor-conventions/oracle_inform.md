# Oracle InForm

## Overview

- Vendor: Oracle Health Sciences
- Product: Oracle InForm (formerly Phase Forward InForm)
- Known EDC versions: 6.x (6.1, 6.2, 6.3), Cloud-deployed builds 2023.x+
- ODM version exported: 1.3.2 with the InForm Adapter extension namespace

## Detection

- `ODM/@Originator` contains `"Oracle"` or `"InForm"` → `source_system = "Oracle InForm"`.
- `SourceSystemVersion` carries the InForm Adapter version (e.g. `"6.2.1"`).
- `xmlns:pf="http://www.phaseforward.com/InFormAdapter/ODM/Extensions/2.0"`
  declaration on the `ODM` element is a secondary signal.

## Namespace

- URI: `http://www.phaseforward.com/InFormAdapter/ODM/Extensions/2.0`
- Prefix: `pf`
- Vendor attributes captured into `vendor_specific`:
  - `pf:MappingVersion` — InForm Adapter mapping schema version
  - `pf:HierarchicalOIDs="Yes"` — toggles the long dotted OID format
  - `pf:InFormAdapterVersion` on `ODM` root
  - `pf:DBUID`, `pf:GUID` on `MeasurementUnit` and `ItemDef`
  - `pf:InFormItemRef` child elements inside `ItemGroupDef` for ordering
    metadata that complements `ItemRef/@OrderNumber`

## ODM Structural Patterns

- Adds a "section" layer between `FormDef` and `ItemGroupDef`. Sections
  flatten to one ItemGroup each when exported via the InForm Adapter.
- `pf:InFormItemRef` is parallel to `ItemRef`; consume `ItemRef` for the
  canonical list and use `pf:InFormItemRef` only for section context.

## OID Conventions

- Hierarchical dot-notation when `pf:HierarchicalOIDs="Yes"`:
  - Forms: `frmAE`
  - Sections: `frmAE.sctAE`
  - Items: `frmAE.sctAE.itmAEDIAG`
- OC4 normalisation:
  - `frm<UPPER>` → strip `frm` prefix → form_id = `<UPPER>`.
  - `frm<X>.sct<Y>.itm<Z>` → form_id = `<X>`, item name = `<Z>`
    (drop the middle section component).
- Non-hierarchical export mode (`pf:HierarchicalOIDs="No"`) emits standard
  short OIDs — no special handling.

## Form Structure Quirks

- InForm sections are presentation-only — they do not survive as separate
  ItemGroups after the OC4 transform.
- Repeating items use ItemGroup-level `Repeating="Yes"`; log-line forms are
  rarer than in Rave but follow the same OC-8 pattern.
- Itemset templates (commonly used for vital-signs grids) flatten into one
  ItemGroup per occurrence at export time.

## Event/Visit Mapping

- StudyEventDef OIDs typically prefixed `visit_` or full event names
  (`SCREEN`, `BASELINE`, `WEEK_4`). The transform normalises every event
  with the `SE_` prefix via `_oc_event_oid`.
- AE / CM forms pin to `SE_COMMON` per OC-9.

## Codelist Handling

- Standard ODM `CodeList` with `Decode/TranslatedText`.
- `pf:DBUID` on codelist items provides traceability back to the InForm
  database — preserve in `vendor_specific`, do not surface in the OC4
  XLSForm choices.

## Clinical Data Patterns

- Full `ClinicalData/SubjectData/StudyEventData/FormData/...` hierarchy
  with `@StudyEventRepeatKey` for repeating events and
  `@ItemGroupRepeatKey` for repeating sections.
- Subject identifiers in `SubjectData/@SubjectKey`; InForm site/subject
  composite keys are not emitted in the metadata-only export.

## Known Export Limitations

- The section layer (`sct…`) loses its semantic grouping in OC4 — if a
  form depends on visual section breaks the protocol PDF must drive that
  decision during enrichment.
- Edit-check rules expressed as InForm Java/EditScript expressions are not
  exported in the ODM stream — flag for protocol-driven re-implementation.
- Calculated items (`@OriginType="Derived"`) export the declaration but
  not the underlying formula.

## OC4 Transform Rules

- Hierarchical OID `frm<X>.sct<Y>.itm<Z>` → strip middle section,
  `form_id = X`, `item_name = Z`.
- `pf:HierarchicalOIDs="Yes"` → run the InForm OID normaliser; otherwise
  treat as generic ODM.
- Multiple sections collapsing to a single ItemGroup must dedupe item
  names within the resulting group (use the group-level
  `seen_names_in_group` mechanism in `odm_to_spec.transform`).

## Compliance Notes

- InForm is 21 CFR Part 11 validated. Signature events live in the InForm
  audit trail, not in the ODM metadata export.
- For GDPR transfers out of EU-hosted InForm tenants, ensure the protocol
  number and study OID do not leak subject-identifiable data — InForm's
  `SubjectKey` is typically the screening number, not the patient name,
  but verify per study.
