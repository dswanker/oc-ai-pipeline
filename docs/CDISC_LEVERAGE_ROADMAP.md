# CDISC: what the OC AI Pipeline can leverage

Research date: 2026-10-07. Sources: all active repos in https://github.com/cdisc-org (cloned and read),
CDISC published standards, and the CDASHIG v2.3 / SDTMIG v3.4 member metadata.
Companion docs: `CDISC_PHASE1_CT_PLAN.md` (shipped CT layer), `CDISC_DDF_RA_REFERENCE.md` (USDM).

Rule for all of this: improve the pipeline generally. Nothing customer-, protocol- or form-specific.
Hierarchy stays: customer standards -> OC standards -> CDISC defaults.

## 0. Offline copy of the CDISC Library (unblocks the API problem)
`cdisc-org/cdisc-rules-engine` (MIT) ships `resources/cache/`:
- Every CT release back to 2014: sdtmct (43), sendct, cdashct, adamct, protocolct, qrsct, define-xmlct, ddfct, glossaryct.
- `standards_details.pkl`: full Library responses for SDTMIG 3.1.2 to 3.4, SENDIG, CDASHIG 1.1.1 to 2.3, ADaM IGs.
  CDASHIG 2.3 = 42 domains, every field with its SDTMIG 3.4 mapping target and prior version.
- `standards_models.pkl` (SDTM 1.2 to 2.1, CDASH 1.0 to 1.3), `variable_codelist_maps.pkl`, compiled rules.
Use: drop-in source for anything we wanted from the Library API, no key needed.

## 1. CDASH field definitions (beyond pick lists)
CDASHIG v2.3: question text (1,178 fields), prompts, CRF completion instructions (998), Core per field
(HR 270 / R/C 336 / O 647; Core = whether the field belongs on the CRF, not whether it must be answered),
data type, SDTMIG target, mapping instructions, implementation notes.
Use: deterministic question text / help text / SDTM annotation for CDASH-default fields; completeness
check of standard domain forms against HR fields.

## 2. Data-cleaning (edit check) library
- `cdisc-org/cdisc-open-rules`: 823 published CORE rules (686 record-level) + unpublished SDTMIG (128),
  FDA Business Rules (60), deprecated SDTM-only (163). Every rule has positive and negative test data.
- These validate submission datasets (SDTM/SEND/USDM), not EDC forms, but many encode logic that belongs
  at entry, e.g. CORE-000022 AESER vs seriousness criteria, CORE-000007 death date implies DTHFL=Y,
  CORE-000034 DS death date = DM death date, CORE-000440 NOT DONE needs reason, CORE-000004/137 dose vs
  occurrence, CORE-000001/011 inclusion/exclusion responses, FDA rules on exposure date windows.
- CDASHIG implementation notes state checks in prose, e.g. CMONGO: end date or ongoing, not both.
Use: curated, reviewed library of standard edit checks keyed to CDASH variables, added when a form has the
fields; rule test data reused as UAT cases.

## 3. Questionnaires, ratings and scales (QRS / ePRO)
SDTM CT (already loaded by cdisc_ct.py) has 307 QRS instrument test-code codelists plus ORRES/STRESC
response codelists (PHQ-9, ECOG, Karnofsky, PROMIS, ADAS-Cog, AUDIT ...). CDISC QRS supplements add
sample CRFs and scoring guidance.
Use: standard item codes, response codes and numeric scores for ePRO and clinician-scale forms.
Caveat: CT gives codes, not copyrighted item wording for licensed instruments.

## 4. Biomedical Concepts and CRF specializations
`cdisc-org/COSMoS` (MIT): BCs, SDTM dataset specializations (dated releases), CRF specializations
(already vendored in cdisc_standards/cosmos). `lexjansen/cdisc360i-pocs` bc_dss2crf generates ODM 1.3.2 /
2.0 forms, HTML CRFs and SDTM-annotated CRFs from them.
Use: standard form templates (VS, LB, ...), domain recognition, annotated CRF generation.

## 5. Annotated CRF (aCRF), new deliverable
Same POC produces SDTM-annotated CRFs. Pipeline already knows each CDASH field's SDTM target (section 1).

## 6. Validate pipeline outputs with CORE (offline)
CORE engine runs from its local cache (`-lr` local rules, `--cache-path`). USDM rules, SDTMIG rules for
trial design and mapped UAT data.

## 7. USDM input
`cdisc-org/usdm` (pip `usdm`): USDM model classes + Excel importer. CORE has USDM v3/v4 rules;
`cdisc-jsonata-rules` has USDM test data (clean and dirty). See CDISC_DDF_RA_REFERENCE.md.

## 8. Downstream submission artifacts
`data-definition-engine` (Define-XML from USDM), `DataExchange-DDS` (data definition spec, LinkML),
cdisc-usdm-utils (trial design TA/TE/TV/TI/TS + XPT), `DataExchange-DatasetJson` (Dataset-JSON v1.1),
`define-xml-2.x-stylesheets`, sdtm.oak (pharmaverse, raw to SDTM).

## 9. Test data for UAT
`sdtm-adam-pilot-project`: 33 SDTM/ADaM XPT datasets + lab reference ranges. `360i/data/source`: DILI labs,
DHT (CGM glucose, step count), QRS EQ-5D-5L example.

## 10. Lower relevance
`DataExchange-RWD-Lineage` (lineage metadata for RWD-derived SDTM; possibly BioIVT), ODM v2.0
(`DataExchange-ODM`, OC4 is ODM 1.3 based), analysis standards (ARS, AC/DC).

## The reference architecture
CDISC 360i (`cdisc-org/360i`, `cdisc-360i-notebooks`) demonstrates: USDM -> CORE validation ->
eCRF + aCRF from BCs -> Define-XML -> trial design domains -> CORE -> SDTM (sdtm.oak) -> Dataset-JSON.
This is the open-source counterpart of the OC AI Pipeline; reuse its parts instead of AI generation.

## Build order
1. CDASH field definitions layer (section 1). SHIPPED: `cdisc_cdash.py` adds row["cdash"]
   (domain, variable, Core, SDTM target) to every CDASH field and CDASHIG completion instructions as hints
   on CDASH-default fields with no hint (designer notes, raw-code text, placeholders and identifiers
   filtered). Not shipped: HR-completeness check (CDASHIG findings are normalized, CRFs are horizontal;
   needs scenario-aware logic) and writing OC4 briefdescription/description (Participant Matrix headings,
   limits unverified).
2. Standard edit-check library (section 2).
3. QRS instruments for ePRO forms (section 3).
Then aCRF, CORE output validation, USDM input, submission artifacts.
