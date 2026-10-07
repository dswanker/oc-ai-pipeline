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

## USDM to pipeline mapping (draft)
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
Pin to usdmVersion 4.0.0 and check it on every input.
