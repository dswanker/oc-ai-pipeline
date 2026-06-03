# REDCap

## Overview

- Vendor: Vanderbilt University (open consortium)
- Product: REDCap (Research Electronic Data Capture)
- Known versions: 13.x, 14.x, 15.x
- ODM version exported: typically 1.3.1 (sometimes 1.3.0 on older builds)

## Detection

- `ODM/@SourceSystem` contains `"REDCap"` → `source_system = "REDCap"`.
  REDCap uses the `SourceSystem` attribute rather than `Originator`.
- `SourceSystemVersion` carries the REDCap build (e.g. `"14.5.36"`).
- `xmlns:redcap="https://projectredcap.org"` declaration is a secondary
  signal.

## Namespace

- URI: `https://projectredcap.org`
- Prefix: `redcap`
- Vendor attributes captured into `vendor_specific`:
  - `redcap:Variable` — the internal REDCap variable name (canonical id)
  - `redcap:FieldType` — `text`, `notes`, `radio`, `checkbox`, `dropdown`,
    `yesno`, `truefalse`, `slider`, `file`, `calc`, `descriptive`
  - `redcap:FieldNote` — inline field help
  - `redcap:SectionHeader` — section break before this item
  - `redcap:BranchingLogic` — REDCap-syntax conditional display

## ODM Structural Patterns

- REDCap exports "instruments" as `FormDef`. Each instrument is its own
  form; there is no matrix/folder layer.
- Longitudinal projects emit `StudyEventDef` per event with `Arm`-scoped
  schedules: arms are encoded as separate event sequences.
- Classic (non-longitudinal) projects have no event structure at all —
  every form floats; the transform synthesises a single
  `SE_STUDY` event plus `SE_COMMON` for OC-9 forms.

## OID Conventions

- Form OID = REDCap instrument unique name (snake_case, lower-cased).
- Item OID = REDCap variable name (snake_case, lower-cased), occasionally
  prefixed with the instrument name when the form has duplicate field
  patterns (uncommon in well-formed projects).
- ItemGroup OID = REDCap instrument name (one ItemGroup per form, unless
  section headers split it post-import).
- OC4 normalisation: uppercase to match CDASH style; preserve the original
  in `vendor_specific.redcap_variable` for traceability.

## Form Structure Quirks

- REDCap "repeating instruments" (RIF/RIE) → OC8 repeating ItemGroup with
  `begin repeat <FORM_ID>_LOG` wrapper.
- "Repeating events" → OC4 repeating `StudyEventDef`.
- `redcap:SectionHeader` is presentation-only — does not produce extra
  ItemGroups; surface as a `begin group` with the section text as label
  only when the protocol clearly relies on it.

## Event/Visit Mapping

- Longitudinal arm/event grids map directly:
  - Arm → OC4 arm
  - Event under arm → `SE_<EVENT>`
  - Visit number derived from event order within the arm
- Classic projects:
  - One synthetic `SE_STUDY` event holds non-OC-9 forms
  - `SE_COMMON` holds AE/CM/DV per OC-9
- Long-form-only studies (no events at all): use `SE_STUDY` and emit a
  `parse_warnings` entry noting the synthesis.

## Codelist Handling

- REDCap choice strings encode raw value + label as `value, label` pairs
  in `Choices`. ODM export converts them to standard
  `CodeList/CodeListItem` with `@CodedValue` and `Decode/TranslatedText`.
- Branching logic is REDCap-specific syntax (`[field] = "1"`); not consumed
  by the deterministic transform. AI enrichment can lift simple cases to
  XLSForm `relevant` expressions.
- Multi-language labels are uncommon in REDCap exports; assume `xml:lang="en"`.

## Clinical Data Patterns

- `ClinicalData` blocks per longitudinal event when exported with data;
  classic projects emit one `SubjectData` per record with all instrument
  data flattened under it.
- `SubjectData/@SubjectKey` = REDCap `record_id` field value.
- Repeating instruments emit `ItemGroupData` with
  `@ItemGroupRepeatKey="<instance_number>"`.

## Known Export Limitations

- File-upload fields export as text references rather than embedded
  binary data.
- Calculated fields (`@FieldType="calc"`) export the result but not the
  REDCap calculation syntax — flag for protocol re-implementation.
- Survey-only features (matrix questions, descriptive text, slider min/max
  labels) lose presentation detail in standard ODM.
- REDCap data dictionary import is not the same as ODM metadata export —
  always require the ODM XML, not a CSV dictionary.

## OC4 Transform Rules

- Classic (non-longitudinal) project → synthesise `SE_STUDY` and
  `SE_COMMON`; map all instruments to one of these per OC-9.
- Longitudinal project → arm/event grid drives `events` and `arms` in
  `study_meta`.
- `redcap:FieldType` → XLSForm type mapping:
  - `text`, `notes` → `text`
  - `radio`, `dropdown`, `yesno`, `truefalse` → `select_one`
  - `checkbox` → `select_multiple`
  - `calc` → `calculate`
  - `slider` → `integer`/`decimal` depending on step
  - `file` → `text` (with note for migration of attachments)
- Repeating instrument → OC-8 repeat wrapper.
- Always uppercase OIDs for OC4 even though REDCap stores them lowercase.

## Compliance Notes

- REDCap deployments vary by institution — Part 11 compliance depends on
  the host's validation status. Do not assume signed-record parity in
  OC4; if needed, surface in `review_flags.protocol_ambiguous`.
- For multi-site / international REDCap consortia, watch for institution-
  specific subject-id formats encoded as plain text.
