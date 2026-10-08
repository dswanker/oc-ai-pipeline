# CDISC DDF-RA (USDM) Reference for OC AI Pipeline

Source: https://github.com/cdisc-org/DDF-RA (v4.0 final, Jun 2025). Code MIT, content CC-BY-4.0.
Attribution required if vendored: "Content based on DDF-RA (GitHub) used under the CC-BY-4.0 license."

## What it is
CDISC's Digital Data Flow Reference Architecture: the USDM logical model, API definition,
Implementation Guide and USDM Controlled Terminology. A specification repo, not a runtime library.

## Useful files
| Path | Use for the pipeline |
|---|---|
| Deliverables/UML/dataStructure.yml | 86 USDM classes with attributes, cardinality, NCI C-codes. Parse target for USDM input. |
| Deliverables/API/USDM_API.json | OpenAPI schema. Validate incoming USDM JSON. |
| Deliverables/CT/USDM_CT.xlsx | Codelists for USDM attributes (encounter type, epoch type, etc.). |
| Deliverables/RULES/USDM_CORE_Rules.xlsx | Conformance rules for USDM documents. |
| Deliverables/IG/USDM-IG.pdf | Implementation guide. |
| Documents/Examples/* | Protocol PDF + USDM 4.0 JSON pairs (CDISC_Pilot, EliLilly_NCT03421379, Alexion_NCT04573309, Devices, Observational). General regression benchmark, not tuning targets. |
| Documents/Mappings/sdtm_mapping.xlsx | USDM to SDTM trial design domains TA, TE, TV, TI, TS. |
| Documents/Mappings/m11_mapping.xlsx | ICH M11 structured protocol to USDM. |

## USDM to pipeline mapping (implemented in `usdm_input.py`)
| USDM | Pipeline / OC4 |
|---|---|
| StudyDesign.encounters (Encounter) | Study events (SE_*) |
| ScheduleTimeline.instances (ScheduledActivityInstance: encounterId + activityIds) | Form placements per event |
| Activity.biomedicalConceptIds -> BiomedicalConcept.code | Form/domain recognition |
| Timing (value, windowLower, windowUpper, relativeTo/From) | Visit windows, calendaring-rules skill |
| StudyArm, StudyEpoch, StudyCell | Arms, epochs, arm-specific event lists |
| EligibilityCriterion (+ EligibilityCriterionItem) | IE criteria rows |
| ScheduleTimeline (non-main) | Conditional / unscheduled timelines, common events |

Caveat: USDM Activities are protocol assessments, not CRFs. Activity to form is many-to-many and
needs a general rule layer (BC codes + FORMS.csv conventions), never study-specific hardcoding.
Pinned to usdmVersion 4.0.x, checked on every input (`usdm_input.check_version`).

## What is vendored
- `cdisc_standards/usdm/usdm_4_0_classes.json`: class -> attribute -> cardinality and types, derived from
  `Deliverables/UML/dataStructure.yml` by `scripts/build_usdm_class_schema.py` (commit aa303cb, 2025-06-03).
- `tests/usdm/examples/`: CDISC_Pilot, EliLilly_NCT03421379, Alexion_NCT04573309 example JSONs, unmodified.
Content based on DDF-RA (GitHub) used under the CC-BY-4.0 license.

## Details worth knowing
- Event OID = `SE_` + the encounter label; a leading minus is kept ("Day -1" -> `SE_DAY_MINUS_1`).
- Days: the timeline's "Fixed Reference" instance is day 0; Before / After timings are followed to every
  instance. `spec["scheduling"]` re-anchors on the first encounter (the pipeline's index event).
  Windows are rounded outward to whole days. Months count 30 days, years 365.
- An activity that runs a sub-timeline, or has child activities, places those activities at its own encounters.
- Timelines not tied to encounters (adverse event, early termination, unscheduled) are reported as conditional
  timelines; their forms keep the pipeline's own common / unscheduled events.
