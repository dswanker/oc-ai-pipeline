# CDISC Phase 1: CDISC Controlled Terminology in the source hierarchy

Scope: the pipeline in general. No customer-, protocol- or form-specific rules anywhere.

## Hierarchy (per pick-list field, decided in code, no AI)
1. Customer standards (CHOICES.csv etc.): used as given, never changed.
2. OC standards input (OC4 XLSForm ZIP / ODM XML): used as given, never changed.
3. Otherwise the field defaults to CDASH and gets its list from CDISC (`cdisc_ct.apply_to_spec`).
4. No CDISC codelist for the field (sponsor-defined per CDASHIG): left as is.
Customer conventions (conventions_engine, customer-scoped) run later and still win.

Protected fields are found from the input files themselves (`pipeline._cdisc_protected_vars`),
never from Claude's source tags. Runs at all four spec-finalization points, before
`_fix_missing_choices`, so a bindable missing list is filled instead of the field being deleted.

## Sources (pinned in cdisc_standards/manifest.json; see cdisc_standards/README.md)
- CT: NCI EVS dated SDTM Terminology archive file (public). CDISC Library API is not used.
- Binding: CDASHIG metadata (member file, never committed), then COSMoS CRF specializations, then name rules.
- Values and labels: COSMoS CRF specializations (CDISC's recommended value list per variable).

## Value rules
- CDISC recommends values: use them (study subset kept only if every study value maps).
- No recommendation: study values that all map keep their selection with CT codes.
- Unmapped values: small non-extensible codelist -> full codelist; extensible -> mapped + sponsor
  extensions; a value still used by skip logic is kept and flagged rather than breaking the form.
- Choice names are XLSForm-safe (DOSE_NOT_CHANGED); exact submission value + C-code stored on the choice.
- Skip-logic expressions are rewritten when a choice name changes.
- A codelist CDISC names that the pinned release lacks is reported, never guessed.

## Versioning
Every build stamps `study_meta.cdisc_standards` (CT version, CRF specialization file, CDASHIG
version, summary). A study keeps its stamped CT version on reruns; bumping manifest.json is an
explicit upgrade for new studies.

## Switches
CDISC_CT_APPLY=0 disables the layer. On any error the spec is returned unchanged.

## Known gaps
- RACE (C74457) and ETHNIC (C66790) are not in CT 2026-09-25 (only RACEC/ETHNICC): left as is, flagged.
- CDASH subset codelists named by CDASHIG v2.3 (C78417-C78431) are retired; the general codelist is used.
- Claude still writes lists for fields that get CDISC lists (token saving is a later, separate change).
