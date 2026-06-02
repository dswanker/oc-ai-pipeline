# CDASH Domain Library — Standard Field Definitions

**Purpose:** Baseline field definitions for every CDASH domain used in
OpenClinica study builds. Claude uses these as defaults when protocol detail
is insufficient. Protocol-specific requirements override these defaults.

**Source:** CDASH standards + patterns observed in PrTK05 builds.

---

## DM — Demographics

**form_id:** DM | **Repeating:** No | **Default tier:** Simple

| name | label | type | required | constraint | notes |
|------|-------|------|----------|------------|-------|
| AGE | Age: | integer | yes | `. >= 18 and . <= 100` | Adjust range per protocol. **NEVER generate AGE as a `calculate` field. AGE is always a directly entered integer with a constraint. If a protocol asks to derive age from birthdate, add a separate `AGE_CALC` calculate field with `readonly: yes` — but the primary AGE item remains a plain integer entry field.** |
| SEX | Sex: | select_one SEX | yes | | Default: M (readonly calculate) |
| ETHNIC | Ethnicity: | select_one ETHNIC | yes | | |
| RACE | Race: | select_multiple RACE | yes | not(selected AND others) | Cannot select Not Reported with others |
| RACEOTH | If other, specify: | text | yes | | relevant: selected(${RACE},'OTHER') |

**Standard group:** `begin group DM1` wrapping all fields.

---

## MH — Medical History

**form_id:** MH (or MEDH) | **Repeating:** Yes (1 group) | **Default tier:** Average

| name | label | type | required | notes |
|------|-------|------|----------|-------|
| MEDHYN | Does participant have any relevant medical history? | select_one NY | yes | relevant: ${MEDHID}=1 |
| MHTERM | Medical History Term | text | yes | In repeating group |
| MHSTDAT_YEAR/MON/DAY/UNK | Start date (partial) | partial date pattern | conditional | |
| MHCONT | Ongoing: | select_one NY | yes | |
| MHENDAT_YEAR/MON/DAY/UNK | End date (partial) | partial date pattern | conditional | relevant: ${MHCONT}='N' |

**Cross-form:** Pulls AGE_CF and BL_CF (baseline date) from clinical data.

---

## IE — Inclusion/Exclusion Criteria

**form_id:** IE | **Repeating:** No | **Default tier:** Simple-Average

| name | label | type | notes |
|------|-------|------|-------|
| ICYN | Did participant consent? | select_one YN | |
| ICDAT | Date of informed consent: | date | constraint: `. <= today()` |
| ICVER | ICF version: | text | |
| PROTOCOL | Protocol version: | select_one VERSION | |
| ARM | Study group assignment: | select_one ARM | relevant when protocol version ≥ 2 |
| IE001–IEn | Inclusion criteria 1–n | select_one YN or YNA | constraint: `.'Y'`; arm-specific relevant |
| EX001–EXn | Exclusion criteria 1–n | select_one YN or YNA | constraint: `.'N'`; arm-specific relevant |

**Note:** Each criterion gets its own row. Treatment-only criteria use
`relevant: ${ARM}='TREATMENT'`. Criteria not applicable to control use YNA.

---

## VS — Vital Signs

**form_id:** VS | **Repeating:** No | **Default tier:** Simple

| name | label | type | required | constraint | notes |
|------|-------|------|----------|------------|-------|
| VSTPT | ** Timepoint: ** | text | | | calculation: ${TPTCALC}, readonly |
| VSPERF | Were vital signs performed? | select_one NY | yes | | |
| VSDAT | Date of measurements: | date | yes | visit window constraint | relevant: ${VSPERF}='Y' |
| HEIGHT_VSORRES | Height: | decimal | yes | `. >= 140 and . <= 210` | relevant: baseline only `${TPTCALC}='Eligibility / Baseline'` |
| HEIGHT_VSORRESU | Height unit: | text | | | calculation: 'cm', readonly |
| WEIGHT_VSORRES | Weight: | decimal | yes | `. >= 40 and . <= 200` | relevant: baseline only |
| WEIGHT_VSORRESU | Weight unit: | text | | | calculation: 'kg', readonly |
| SYSBP_VSORRES | Systolic blood pressure: | integer | yes | `. >= 90 and . <= 180` | |
| SYSBP_VSORRESU | SBP unit: | text | | | calculation: 'mmHg', readonly |
| DIABP_VSORRES | Diastolic blood pressure: | integer | yes | `. >= 50 and . <= 110` | |
| DIABP_VSORRESU | DBP unit: | text | | | calculation: 'mmHg', readonly |
| PULSE_VSORRES | Pulse: | integer | yes | `. >= 40 and . <= 120` | |
| PULSE_VSORRESU | Pulse unit: | text | | | calculation: 'beats/min', readonly |
| RESP_VSORRES | Respiratory rate: | decimal | yes | `. >= 10 and . <= 30` | |
| RESP_VSORRESU | RR unit: | text | | | calculation: 'breaths/min', readonly |
| TEMP_VSORRES | Temperature: | decimal | yes | `. >= 34.5 and . <= 38.5` | |
| TEMP_VSORRESU | Temp unit: | select_one TUNIT | | | calculation: 'C', readonly |

**Two groups:** VSNY (visit-level) and VSDETAILS (relevant: ${VSPERF}='Y').
Height/weight fields relevant only at baseline timepoint.

---

## PE — Physical Examination

**form_id:** PE | **Repeating:** No | **Default tier:** Simple-Average

Two versions typically needed:
- PE_FULL: Full examination at screening (all systems)
- PE_SD: Symptom-directed at follow-up visits

| name | label | type | notes |
|------|-------|------|-------|
| PEPERF | Was physical exam performed? | select_one NY | |
| PEDAT | Date of exam: | date | relevant: ${PEPERF}='Y' |
| PERES | Overall findings: | select_one NY | Normal/Abnormal |
| PECLSIG | Clinically significant? | select_one NY | relevant: abnormal |
| PEDESC | Description of findings: | text | relevant: abnormal |

---

## LB — Laboratory Test Results

**form_id:** LB | **Repeating:** Yes (1 group per analyte) | **Default tier:** Average-Complex

Standard header fields:
| name | label | type | notes |
|------|-------|------|-------|
| LBTPT | ** Timepoint: ** | text | calculation: ${TPTCALC}, readonly |
| LBPERF | Was lab assessment done? | select_one NY | required: yes |
| LBRSN | Reason not done: | text | relevant: ${LBPERF}='N' |
| LBNAM | Local lab name: | select_one LBNAM | relevant: ${LBPERF}='Y'; choice_filter on site |
| LBDAT | Collection date: | date | relevant: ${LBPERF}='Y'; visit window constraint |
| LBTIM | Collection time: | text | time regex constraint |

Per-analyte pattern (repeat for each lab test):
| name | label | type | notes |
|------|-------|------|-------|
| [TST]_ND | [blank] | select_multiple ND | Not Done flag |
| [TST]_LBCAT | Laboratory category: | calculate | e.g., 'Hematology' |
| [TST]_LBSCAT | Subcategory: | calculate | e.g., 'CBC' |
| [TST]_LBORRES | Result: | decimal | required: yes |
| [TST]_UNIT_CALC | | calculate | from labranges |
| [TST]_LBORNRLO_CALC | | calculate | from labranges |
| [TST]_LBORNRHI_CALC | | calculate | from labranges |
| [TST]_UNIT | Units: | text | calculation from _CALC, readonly |
| [TST]_LBORNRLO | Normal range lower: | text | calculation from _CALC, readonly |
| [TST]_LBORNRHI | Normal range upper: | text | calculation from _CALC, readonly |
| [TST]_CCSIG | Clinically significant? | select_one NYU | relevant: out of range |

**Standard analytes for hematology/chemistry panel (per PrTK05):**
WBC, HGB, NEUT (Neutrophils), LYMPH (Lymphocytes), PLT (Platelets),
AST, ALT, BILI (Total Bilirubin), ALKPH (Alkaline Phosphatase),
CREAT (Creatinine), CRCL (Creatinine Clearance — calculated field)

**CRCL Calculation:**
`((140 - ${AGE_CF}) * ${WEIGHT_CF}) div (72 * ${CREAT_LBORRES})`

---

## PSA — Prostate Specific Antigen

**form_id:** PSA | **Repeating:** No | **Default tier:** Simple
*(Study-specific LB subdomain — separate form in PrTK05)*

| name | label | type | notes |
|------|-------|------|-------|
| PSATPT | ** Timepoint: ** | text | readonly |
| LBPERF | Was PSA collected? | select_one NY | required: yes |
| LBDAT | Date of collection: | date | constraint: `. <= today()` |
| LBTM | Time of collection: | text | time regex |
| LBORRES | PSA results: | text | regex decimal constraint |
| LBORRESU | PSA units: | text | calculation: 'ng/mL', readonly |

---

## AE — Adverse Events

**form_id:** AE | **Repeating:** Yes (1 group) | **Default tier:** Average

Core AE fields (in repeating group):
| name | label | type | required | constraint | notes |
|------|-------|------|----------|------------|-------|
| AESPID | AE ID | text | | | calculation: ${AEID}, readonly |
| AETERM | Adverse event term: | text | yes | | |
| AESTDAT | Start date: | date | yes | `. <= today()` | |
| AEONGO | Ongoing: | select_one NY | yes | | |
| AEENDAT | End date: | date | yes | `. <= today() and . >= ${AESTDAT}` | relevant: ${AEONGO}='N' |
| AESEV | Severity: | select_one AESEV | yes | Grade 1-3 soft warning | NCI-CTCAE v5.0 |
| AESER | Was AE serious? | select_one NY | yes | Grade 5 must be Y | |
| AEREL1 | Relationship to [drug1]: | select_one REL | yes | | Study drug specific |
| AEACN1 | Action taken with [drug1]: | select_one AEACN_C | yes | | |
| AEREL2 | Relationship to [drug2]: | select_one NY | yes | | If prodrug |
| AEACN2 | Action taken with [drug2]: | select_one AEACN | yes | | |
| AEOUT | Outcome: | select_one OUT | yes | Consistent with ongoing | |

SAE section (begin group AE2, relevant: ${AESER}='Y'):
| name | label | type | notes |
|------|-------|------|-------|
| AESERSTDAT | Date event became serious: | date | |
| AESERONGO | Ongoing: | select_one NY | |
| AESERENDAT | Date became non-serious: | date | relevant: ${AESERONGO}='N' |
| AESERCRIT | Seriousness criteria: | select_multiple AESERCRIT | |
| AESERNARRTE | Event narrative: | text (multiline) | |

Reporter section (begin group AE2.03):
| name | label | type |
|------|-------|------|
| CASENUM | Case number: | text |
| REPNAM | Reporter name: | text |
| REPROLE | Reporter role: | text |
| SUBMTSAF | [Submit to safety checkbox] | select_multiple SUBMTSAF |
| SUBMTSAFDTC | Date/time of submission: | text (calculated) |

**Cross-form pulls:** AGE_CF, WEIGHT_VSORRES_CF, HEIGHT_VSORRES_CF from VS.

---

## CM — Prior and Concomitant Medications

**form_id:** CM | **Repeating:** Yes (1 group) | **Default tier:** Average

| name | label | type | notes |
|------|-------|------|-------|
| CMTRT | Medication/Therapy: | text | required |
| CMINDCAT | Indication: | select_multiple INDCAT | MH / AE / Other |
| CMDOS | Dose: | decimal | |
| CMDOSU | Unit: | select_one UNIT | |
| CMDOSFRM | Dose form: | select_one DOSE | |
| CMDOSFRQ | Frequency: | select_one FREQ | |
| CMROUTE | Route: | select_one ROUTE | |
| CMSTDAT_YEAR/MON/DAY/UNK | Start date (partial): | partial date pattern | |
| CMONGO | Ongoing: | select_one NY | |
| CMENDAT_YEAR/MON/DAY/UNK | End date (partial): | partial date pattern | relevant: ${CMONGO}='N' |

---

## EX — Exposure (Study Drug)

**form_id:** EX | **Repeating:** No | **Default tier:** Simple

Study drug injection fields:
| name | label | type | notes |
|------|-------|------|-------|
| EXYN | Was [drug] injected? | select_one NY | |
| EXRSN | Reason not done: | text | relevant: ${EXYN}='N' |
| EXDAT | Date of injection: | date | constraint: `. <= today()` |
| EXTM | Time of injection: | text | time regex |
| EXDOSE | Dose amount: | decimal | constraint: `.= [expected_dose]` |
| EXDOSU | Units: | text | calculation: 'mL', readonly |
| EXROUTE | Route: | select_one EXROUTECD | Transrectally/Transperineally |
| EXLOT | Vial number: | text | |
| INJCOMPLYN | Administered per pharmacy manual? | select_one NY | |
| INJCOMPLRSN | If no, specify: | text | |

---

## EC — Exposure (Concomitant/Prodrug)

**form_id:** EC | **Repeating:** Yes (repeat per dose day) | **Default tier:** Average

| name | label | type | notes |
|------|-------|------|-------|
| ECSTAT | Completed regimen as prescribed? | select_one NY | |
| ECOCCUR | Missed all doses? | select_one NY | relevant: ${ECSTAT}='N' |
| ECSTDAT | Dose start date: | date | constraint: = day after injection |
| ECENDAT | Dose end date: | date | constraint: = 14 days post injection |

Repeating section (14 rows, one per day, relevant: missed doses):
| name | label | type | notes |
|------|-------|------|-------|
| ECDAT | Dose date: | date | calculation: injection_date + day_number |
| ECYN | Administered as per protocol? | select_one NY | |
| ECRSN | If no, reason: | text | |
| ECDOSE | Dose: | select_one ECDOSE | 2g / 1.5g / Other |
| ECDOSU | Unit: | text | calculation: 'grams' |
| ECFREQ | Frequency: | select_one ECFREQ | TID / BID / Other |

---

## BE/BS — Biospecimen Collection

**form_id:** BE | **Repeating:** No (table layout per timepoint) | **Default tier:** Average-Complex

Per timepoint, per specimen type:
| name pattern | label | type | notes |
|-------------|-------|------|-------|
| T[nn]DT | Date of collection: | date | |
| T[nn]KITID | Kit ID: | text | |
| T[nn][SP]STAT | Not Collected: | select_multiple SYN | |
| T[nn][SP]COLTM | Collection Time: | text | time constraint |
| T[nn][SP]COLL | Collected via catheter? | select_multiple YES | urine only |
| T[nn][SP]PROCTM | Processing Time: | text | |
| T[nn][SP]STM | Storage Time: | text | |
| T[nn][SP]OPER | Operator Initials: | text | |
| T[nn][SP]CRYOVN | # cryovials: | integer | |

Specimen types: URINE (UR), PLASMA (PL), SERUM (S), WHOLE BLOOD (WB)
**Note:** This form is highly protocol-specific and timepoint-driven.
The full form structure must be derived from the biospecimen collection schedule.

---

## DS — Disposition

**form_id:** DS | **Repeating:** No | **Default tier:** Simple

| name | label | type | notes |
|------|-------|------|-------|
| DSTPT | ** Timepoint: ** | text | readonly |
| DSDAT | Date of completion/discontinuation: | date | `. <= today()` |
| DSDECOD | Participant status: | select_one DSDECOD | choice_filter on timepoint |
| DSDECODO | Other, specify: | text | relevant: OTHER |
| DSAEREF | Relevant AE ID: | text | relevant: ADVERSE_EVENT |

Death detail section (begin group DS2, relevant: ${DSDECOD}='DEATH'):
| name | label | type |
|------|-------|------|
| DDYN | Death details collected? | select_one NY |
| DDDAT | Collection date: | date |
| DTHDAT | Death date: | date |
| PRCDTH_DDORRES | Primary cause of death: | text |
| AUTOPIND_DDORRES | Autopsy performed? | select_one NY |

**DSDECOD choice filter:** uses `timepoint` column in choices sheet
matching event OIDs to show only relevant status options per visit.

---

## PR — Procedures

Two separate PR forms common in oncology studies:

### PR (Concomitant Procedures)
**form_id:** PR | **Repeating:** Yes (1 group)

| name | label | type |
|------|-------|------|
| PRTRT | Procedure Name | text |
| PRINDCAT | Indication: | select_multiple INDCAT |
| PRSTDAT | Start Date: | date |
| PRONGO | Ongoing: | select_one NY |
| PRENDAT | End Date: | date |

### PR (EBRT/Radiation) — Study-Specific Override
**form_id:** PR (same ID, different form for different event)

| name | label | type | notes |
|------|-------|------|-------|
| PRYN | Was radiation administered? | select_one NY | |
| PRTRT | Treatment: | text | calculation: 'Radiation (EBRT)' |
| RADTYPE | Fractionation type: | select_one RADTYPE | Standard/Moderate Hypo |
| RADFRNUM | Number of fractions: | integer | |
| RADDOSFR | Dose per fraction: | decimal | |
| PRDOSU | Unit: | text | calculation: 'Gy' |
| PRDSTXT | Total dose received: | decimal | calculation: ${RADFRNUM}*${RADDOSFR} |
| PRLOC | Anatomical location: | text | calculation: 'Prostate Gland' |
| PRLOCO | Outside prostate? | select_one LOCO | |
| PRSTDAT | Start date: | date | constraint: within 3 days of C2 injection |
| PRONGO | Ongoing: | select_one NY | |
| PRENDAT | End date: | date | |

---

## DV — Protocol Deviation Log

**form_id:** DV | **Repeating:** Yes (1 group) | **Category:** INFRASTRUCTURE

| name | label | type | notes |
|------|-------|------|-------|
| DVSEQ | Deviation number: | text | readonly, calculated |
| DVDESC | Description: | text | required |
| DVSTDAT | Date occurred: | date | `. <= today()` |
| DVAWDAT | Date of site awareness: | date | `. >= ${DVSTDAT}` |
| DVREPDAT | Date reported: | date | `. >= ${DVAWDAT}` |
| DVCLAS | Important/Not Important: | select_one DVCLASCD | |
| DVCOD | Category: | select_one DVCAT | |
| DVOTH | Other, specify: | text | |
| DVELIG | Inclusion/Exclusion: | select_one ELIG | relevant: eligibility violation |
| DVIRB | Reportable to IRB?: | select_one NY | |
| DVIRBDAT | Date reported to IRB: | date | |
| DVAESAE | Resulted in SAE?: | select_one NY | |
| DVACT | Actions taken: | text | |
| DVRES | Resolution: | text | |
| DVCOVAL | Comments: | text | |

---

## DOV — Date of Visit

**form_id:** DOV | **Repeating:** No | **Category:** INFRASTRUCTURE

| name | label | type | notes |
|------|-------|------|-------|
| DOVTPT | ** Timepoint: ** | text | readonly |
| VISYN | Was visit done?: | select_one NY | NOT relevant at baseline |
| VISDT | Date of visit: | date | `. <= today()` |
| VISNDRSN | Reason not done: | text | relevant: ${VISYN}='N' |

---

## PREGPART — Pregnant Partner Report

**form_id:** PREGPART | **Repeating:** No (nested repeat for neonates) | **Default tier:** Average

Sections: Pregnancy Information, Obstetrical History, Pregnancy Outcome,
Neonate Information (begin_repeat NEONATES for multiple births).

Key fields: LMPDT, CONDT, DLVRYDT, PREGDT, DIAGMETH, PREGNUM,
FULLTERM, PRETERM, SPNTABT, PREGOUT, OUTDT, BIRTHTYP, NEONUM.

Neonate repeat fields: BIRTHORD, SEX, WEIGHTKG, HEIGHTCM, HEADCMCM,
DELMODE, DELCOMP, RESUSREQ, NEOSTAT.

---

## SPELIG — Sponsor Eligibility Review

**form_id:** SPELIG | **Repeating:** No | **Category:** INFRASTRUCTURE

| name | label | type |
|------|-------|------|
| IEDTC | Date of review: | date |
| IEORRES | Patient eligible for study? | select_one YN |
