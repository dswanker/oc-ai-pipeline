# Viedoc

## Overview

- Vendor: Viedoc Technologies
- Product: Viedoc Clinical
- Known versions: 4.x, 2024.x
- ODM version exported: 1.3.2

## Detection

- `ODM/@Originator` contains `"Viedoc"` → `source_system = "Viedoc"`.
- `SourceSystemVersion` carries the Viedoc release (e.g. `"4.78"`).
- `xmlns:viedoc="http://www.viedoc.net/ns/odm"` declaration is a secondary
  signal.

## Namespace

- URI: `http://www.viedoc.net/ns/odm`
- Prefix: `viedoc`
- Vendor attributes captured into `vendor_specific`:
  - `viedoc:DisplayName` — UI label distinct from the ODM `@Name`
  - `viedoc:FormType` — `"Visit"`, `"Common"`, `"Event"`, etc.
  - `viedoc:RowLayout` — display hint for multi-column fields
  - `viedoc:ItemFormat` — input mask / display format
  - `viedoc:Required` — duplicates `ItemRef/@Mandatory`

## ODM Structural Patterns

- Largely clean, standard ODM. Viedoc was designed around the CDASH model
  and its exports closely mirror it.
- Visit-bound forms map 1:1 to `StudyEventDef`/`FormRef` pairs.
- Common forms (Viedoc "Common Events") flatten under a single common
  event in the export.

## OID Conventions

- Standard short OIDs in CDASH-like style (`F_DM`, `IG_DM`, `I_DM_SUBJID`,
  `SE_VISIT_1`).
- No special normalisation required beyond the generic
  `_oc_form_id` / `_oc_item_name` rules.

## Form Structure Quirks

- Viedoc supports log-line forms via `FormDef/@Repeating="Yes"` —
  standard OC-8 repeating wrapper applies.
- `viedoc:RowLayout` describes multi-column input rows; map to XLSForm
  `appearance` where there is a clean equivalent (`horizontal`, `w2`,
  `w3 horizontal`).
- Viedoc "Common Forms" (AE, CM, etc.) pin to `SE_COMMON` per OC-9 even
  if the Viedoc export lists them under a `viedoc:FormType="Common"`
  pseudo-event.

## Event/Visit Mapping

- `StudyEventDef OID="SE_VISIT_<N>"` is the typical pattern; the
  transform preserves it.
- Repeating visits (treatment cycles) carry `Repeating="Yes"` on the
  `StudyEventDef` — propagate to OC4 repeating events.
- Common events → `SE_COMMON`.

## Codelist Handling

- Standard ODM `CodeList` / `CodeListItem`.
- Viedoc supports multi-language decodes; export uses `xml:lang` on each
  `TranslatedText`. The transform takes the English decode and preserves
  the multilingual entries in `vendor_specific.codelist_translations`.

## Clinical Data Patterns

- Standard `ClinicalData` hierarchy.
- `SubjectData/@SubjectKey` = Viedoc subject id (typically site + sequence).
- Repeating visit data uses `StudyEventData/@StudyEventRepeatKey`.

## Known Export Limitations

- Viedoc dynamic edit checks (`viedoc:Validation`) are not always
  expressed as `RangeCheck` — some live in a separate metadata layer that
  is not part of the standard ODM export.
- Calculated items use Viedoc-proprietary expression syntax; not consumed
  by the deterministic transform.

## OC4 Transform Rules

- `viedoc:DisplayName` → use as XLSForm `label` when richer than the ODM
  `Question/TranslatedText`.
- `viedoc:RowLayout` → map to XLSForm `appearance`:
  - `Single` → omit (default)
  - `Inline2` → `w3 horizontal`
  - `Inline3+` → `w2`
- Repeating `StudyEventDef` → emit OC4 repeating event entries in
  `timepoint_csv`.
- All common forms pin to `SE_COMMON` per OC-9.

## Compliance Notes

- Viedoc is 21 CFR Part 11 validated and ICH E6 / GCP compliant.
- Signature events are stored in Viedoc audit logs, not in the metadata
  ODM. If the source study used eSignatures, surface in
  `review_flags.protocol_ambiguous` for OC4 signing parity.
- Viedoc tenants are commonly EU-hosted (Uppsala). Confirm tenant
  region for GDPR compliance.
