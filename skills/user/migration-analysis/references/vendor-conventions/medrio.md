# Medrio

## Overview

- Vendor: Medrio, Inc.
- Product: Medrio EDC
- Known versions: 2023.x, 2024.x
- ODM version exported: 1.3.2

## Detection

- `ODM/@Originator` contains `"Medrio"` → `source_system = "Medrio"`.
- `SourceSystemVersion` carries the Medrio release (e.g. `"2024.4"`).
- No widely-published vendor namespace — Medrio exports tend to be
  pure ODM 1.3.2.

## Namespace

- No publicly-documented vendor namespace URI as of writing.
- If a `medrio:` or `mr:` prefixed namespace ever appears, capture all
  attributes into `vendor_specific.medrio` and log a parse warning so we
  notice and document it.

## ODM Structural Patterns

- Standard ODM 1.3.2 with no vendor extension layer.
- `Study → MetaDataVersion → Protocol → StudyEventDef → FormDef →
  ItemGroupDef → ItemDef` nesting.
- Visit-based scheduling — no folder/matrix construct.

## OID Conventions

- Standard short OIDs (`F_DM`, `IG_AE`, `I_AE_AETERM`, `SE_BASELINE`).
- No special normalisation required beyond the generic
  `_oc_form_id` / `_oc_item_name` rules.

## Form Structure Quirks

- Medrio supports relational / repeating ItemGroups for log-line data
  (AE, CM, MH). Detection driven entirely by
  `ItemGroupDef/@Repeating="Yes"` and `FormDef/@Repeating="Yes"`.
- The transform applies the standard OC-8 repeating wrapper
  (`begin repeat <FORM_ID>_LOG` / `end repeat`) for any form flagged
  repeating.

## Event/Visit Mapping

- Standard `StudyEventDef` → `SE_<NAME>`.
- Unscheduled visits → `SE_UNSCHEDULED` (synthesised if absent).
- AE / CM / DV pin to `SE_COMMON` per OC-9.

## Codelist Handling

- Standard ODM `CodeList`/`CodeListItem` with English decodes.
- No vendor-specific codelist tagging.

## Clinical Data Patterns

- Standard `ClinicalData` hierarchy.
- `SubjectData/@SubjectKey` = Medrio subject identifier.
- Repeating data uses `ItemGroupData/@ItemGroupRepeatKey`.

## Known Export Limitations

- Edit-check rules expressed in the Medrio rule engine are not in the
  ODM export — protocol-driven validations during AI enrichment fill
  the gap.
- Calculated items export the result but not the formula.
- Medrio supports e-source attachments and signed e-consents that are not
  part of the ODM metadata stream.

## OC4 Transform Rules

- Treat as generic ODM with the standard OC-1..OC-9 rules.
- `FormDef/@Repeating="Yes"` drives OC-8 repeating wrapper.
- Standard CDASH naming — no special form-id mapping required.
- If an unrecognised namespace appears, preserve in `vendor_specific`
  rather than failing the parse.

## Compliance Notes

- Medrio is 21 CFR Part 11 validated and ICH E6 / GCP aligned.
- Tenant hosting is region-pinned (US-East, EU-West) — confirm region
  for GDPR-sensitive migrations.
- Signature events live in the Medrio audit log, not the metadata
  export.
