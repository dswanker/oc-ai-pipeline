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

SHIPPED: `cdisc_qrs.py` (+ `cdisc_concepts.py`, `sdtm_mapping.py`, `edit_check_meta.py`, `pipeline._apply_qrs`).
- Instrument index built from the pinned CT: 362 instruments (QS 252, RS 83, FT 27) with their test codes and
  names; 87 response codelists; numeric scores for the responses whose STRESC definition quotes the original
  response (the pairing CT itself publishes; nothing is inferred when CT paraphrases).
- Tagging: concept QSORRES / FTORRES / RSORRES + qualifier = test code. The validated AI call may use only
  test codes of instruments the form names (title, id, notes); deterministic matching by instrument and item
  order fills the rest (concept_source `qrs_instrument`) and never guesses when counts or responses disagree.
- CDASH-default select_one items get the instrument's response codelist (codes, CT labels, skip logic
  rewritten); the score per choice is in `row["qrs"]["scores"]` (spec metadata for --STRESN). Customer and OC
  standard fields get metadata only. Question labels are never changed.
- SDTM Mapping Specification: `QS.QSORRES`, `QSTESTCD = <code>; QSCAT = <instrument>`. DVS_OC4 Notes show the
  instrument, item and response codelist. Switch: `CDISC_QRS=0`.
- Not shipped: instruments whose responses CT does not publish get test codes only; "the Same as" response
  codelists are bound to the named item only.

## 4. Biomedical Concepts and CRF specializations
`cdisc-org/COSMoS` (MIT): BCs, SDTM dataset specializations (dated releases), CRF specializations
(already vendored in cdisc_standards/cosmos). `lexjansen/cdisc360i-pocs` bc_dss2crf generates ODM 1.3.2 /
2.0 forms, HTML CRFs and SDTM-annotated CRFs from them.
Use: standard form templates (VS, LB, ...), domain recognition, annotated CRF generation.

## 4b. SDTM Mapping Specification (SHIPPED)
`sdtm_mapping.py`, Chain A: `{protocol}_SDTM_Mapping_Specification_{version}.xlsx/.pdf` to monday columns
`SDTM Mapping Spec (XLSX)` (file_mm7xk7mw) and `SDTM Mapping Spec (PDF)` (file_mm7xnxj6). One row per data field:
customer field -> CDASH concept (+qualifier) -> SDTM domain.variable, value-level detail, CT, Confidence + Basis
(High: customer alias / CDASH name; Medium: Claude validated; None: not mapped). Proposed mapping for review.
Produced when "SDTM Mapping Specification" (dropdown label id 9) is selected in "What outputs would you like?"
(dropdown_mm2nc7d4), or when no output is selected (run all). Gate: built only if a Study Specification JSON
already exists on the item OR "Protocol specification" is also selected (first run); it never triggers a protocol
analysis on its own. Mapping-only runs reuse the saved spec (Path R); otherwise the run fails fast with a log message.

## 5. Annotated CRF (aCRF), new deliverable
Same POC produces SDTM-annotated CRFs. Pipeline already knows each CDASH field's SDTM target (section 1).

SHIPPED: `acrf.py`, Chain A: `{protocol}_Annotated_CRF_{version}.pdf` to the monday file column
`Annotated CRF (PDF)` (file_mm7y8tr7, `monday_client.COL["acrf_pdf"]`; outputs dropdown label id 10). Landscape, Study Specification palette. One section per
built form: question, response options with stored codes, and an annotation box per field taken from the SDTM
Mapping Specification rows (`sdtm_mapping.build_rows` is the single source of truth): `DOMAIN.VARIABLE`,
value-level lines ("when VSTESTCD = SYSBP"), `NOT SUBMITTED`, `SUPP<DOMAIN>.QVAL candidate`; AI-validated and
questionnaire item-order mappings are marked for review. Original reportlab code (the 360i POC was only the idea).
Produced when "Annotated CRF" is selected in "What outputs would you like?" or when no output is selected. Same
gate as the mapping specification: needs a Study Specification JSON on the item or "Protocol specification"
selected in the run; never triggers a protocol analysis alone. Built on a copy of the spec. Switch: `ACRF_OUTPUT=0`.

## 6. Validate pipeline outputs with CORE (offline)
CORE engine runs from its local cache (`-lr` local rules, `--cache-path`). USDM rules, SDTMIG rules for
trial design and mapped UAT data.

SHIPPED (USDM inputs): `core_validation.py` + `pipeline._load_usdm_input`. Verified offline (network blocked,
no Library API key): cdisc-rules-engine 0.17.1 with its shipped `resources/cache` runs 207 USDM 4.0 rules in
about 20 s per file (DDF-RA examples: 617 / 355 / 216 findings). The engine is an external command, not a
pipeline dependency (it needs Python 3.12; the pipeline image is 3.11): active only when `CORE_ENGINE_CMD` is set
(`CORE_ENGINE_DIR`, `CORE_CACHE_DIR` optional; `CORE_VALIDATE=0` disables). Findings go to the monday log and
never block a build. Not shipped: installing the engine and its 462 MB cache in the Railway image; SDTMIG rules
on trial design / mapped UAT data.

## 7. USDM input
`cdisc-org/usdm` (pip `usdm`): USDM model classes + Excel importer. CORE has USDM v3/v4 rules;
`cdisc-jsonata-rules` has USDM test data (clean and dirty). See CDISC_DDF_RA_REFERENCE.md.

SHIPPED: `usdm_input.py` (+ `pipeline._load_usdm_input`, `_apply_usdm`). A sponsor's USDM 4.0.x JSON in the
monday file column `USDM JSON (input)` (file_mm7yx5qb, `monday_client.COL["usdm_input"]`) makes the visit structure
deterministic: encounters -> study events, scheduled activity instances (with sub-timelines and child activities)
-> form placements, timings -> `spec["scheduling"]` (offsets and windows in days; the AI scheduling pass is
skipped), arms / epochs / cells -> arms and arm applicability, eligibility criteria -> criteria rows. The
structure is passed to the protocol analysis as context (prompts.py unchanged) and enforced on the result before
the customer-convention steps, which still win. Other versions are rejected with a clear log line and the run
continues without the file. Schema check from DDF-RA `dataStructure.yml` (findings logged, not blocking).
Activity -> form: general rule layer (Biomedical Concept code, customer FORMS convention, CDISC domain name,
form name, QRS instrument, CDISC test name, domain-name prefix); unresolved activities are listed for review
(`study_meta.usdm`, `review_flags.usdm_review`). Switch: `USDM_INPUT=0`.
Benchmark (`tests/usdm/benchmark.py`, DDF-RA examples vendored under CC-BY-4.0): events 100%, placement recall
100%, placement accuracy 100% on CDISC_Pilot (12 events / 118 placements), EliLilly_NCT03421379 (7 / 49) and
Alexion_NCT04573309 (50 / 376). Activity -> form against a generic one-form-per-domain library: 44% / 35% / 22%
(the rest are protocol-specific activity names, listed for review; a real build matches on its own form titles).

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
2. Standard edit-check library (section 2). Foundations SHIPPED: conventions engine bindings, template
   filters, study.has_field (cross-form), add_constraint; concept tagging (`cdisc_concepts.py`: row concept +
   qualifier from customer aliases, CDASH names, validated Claude call) so checks match non-CDASH forms.
3. QRS instruments for ePRO forms (section 3). SHIPPED: `cdisc_qrs.py`.
4. Annotated CRF (section 5). SHIPPED: `acrf.py`.
5. USDM 4.0 input (section 7). SHIPPED: `usdm_input.py`.
6. CORE validation of USDM inputs (section 6). SHIPPED: `core_validation.py` (engine install is a deploy step).
Then CORE validation of outputs (trial design, mapped UAT data), submission artifacts.
