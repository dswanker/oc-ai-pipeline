# CRF Categorization Examples — Human-Corrected Learning Log

**Purpose:** This file stores corrections made by human reviewers to Claude's CRF complexity classifications. Claude reads this file before classifying CRFs in any new protocol. Entries here take precedence over general rules when a similar situation is encountered.

**How to add an entry:** After reviewing Claude's output, if a classification is incorrect, add an entry below using the template provided. Include enough context that Claude can recognize a similar situation in a future protocol.

**How to update an entry:** Add a follow-up note below the original entry rather than deleting it. This preserves the learning history.

---

## Entry Template

```
### [Domain Name] — [Protocol ID] — [Date]
**CRF Description:** Brief description of what this form collects
**Claude's Classification:** Simple / Average / Complex
**Human Correction:** Simple / Average / Complex (or "Confirmed Correct")
**Reason:** Explanation of why the classification was wrong and what rule or nuance applies
**Lesson for future:** What Claude should look for in similar situations
```

---

## Confirmed Correct Examples

### Inclusion/Exclusion Criteria (2 versions) — PrTK05 — April 2026
**CRF Description:** I/E checklist where some criteria apply to one arm only
**Claude's Classification:** Simple (1 CRF, flagged for review)
**Human Correction:** Confirmed correct reasoning — should be 2 unique CRFs
**Reason:** When treatment and control arms have different I/E criteria
(e.g., certain criteria marked "not applicable to concurrent control group"),
this creates 2 distinct form designs, not 1.
**Lesson for future:** Whenever I/E criteria differ between arms — even by
a single criterion — classify as 2 unique CRFs without asking for confirmation.

### Physical Examination (2 versions) — PrTK05 — April 2026
**CRF Description:** Full PE at screening; symptom-directed PE at follow-up visits
**Claude's Classification:** Average (1 CRF, flagged possible 2nd version)
**Human Correction:** Should be 2 unique CRFs
**Reason:** Full PE and symptom-directed PE collect different fields and
represent distinct form designs.
**Lesson for future:** Whenever a protocol specifies a full assessment at
screening/baseline and a modified or targeted version at follow-up visits,
classify as 2 unique CRFs. Do not wait for confirmation.

---

## Corrected Examples

### Demographics + Medical History — PrTK05 — April 2026
**CRF Description:** Demographics and Medical History assessed at screening
**Claude's Classification:** Average (combined DM/MH as 1 CRF)
**Human Correction:** Should be 2 unique CRFs (1 Simple DM + 1 Average MH)
**Reason:** DM and MH are always separate CDASH domains and separate forms
in OpenClinica unless the protocol explicitly states they are combined on
a single page.
**Lesson for future:** Always treat DM and MH as 2 separate unique CRFs.
Only combine them if the protocol explicitly states they are on one form.

### Valacyclovir Patient Diary — PrTK05 — April 2026
**CRF Description:** Patient-recorded prodrug compliance diary (dose taken
per day for 14-day course, captured 3 times across 3 injection courses)
**Claude's Classification:** Captured within EX (Prodrug Exposure) CRF;
diary flagged as possible paper form outside EDC
**Human Correction:** Patient diaries and ePRO instruments are unique CRFs
to be built within OpenClinica's built-in ePRO capability
**Reason:** OpenClinica has native ePRO functionality. Patient-reported
outcomes, diaries, and any patient-entered data should always be treated
as unique CRFs built within the EDC system, not assumed to be paper.
**Lesson for future:** Whenever a protocol mentions patient diaries,
patient-reported outcomes (PRO), ePRO instruments, symptom questionnaires,
or any data the patient records themselves — classify each as a unique CRF
to be built in OpenClinica's ePRO module. Do not flag as paper or external.

---

### EC_DIARY not counted as unique CRF — PrTK05 — April 2026
**CRF Description:** Valacyclovir patient diary — daily compliance record
across 3 × 14-day prodrug courses. Protocol states patients record each
dose taken in a diary.
**Claude's Classification:** Flagged as ambiguous (paper or EDC unclear);
not counted as a unique CRF in Section 4
**Human Correction:** Must be counted as a unique CRF. Total unique CRF
count was 21 in pricing summary but should be 26 to match EDC structure.
**Reason:** The pricing summary was grouping the diary conceptually with
the EC (Prodrug Administration) CRF rather than treating it as a separate
buildable artifact. The EDC structure skill correctly identified it as a
distinct ePRO CRF. Any patient-entered data is always a separate CRF.
**Lesson for future:** Never fold a patient diary or ePRO instrument into
another CRF for counting purposes, even if it relates to the same domain.
Count it separately. The EC form is site-entered; the EC_DIARY is
patient-entered. Different entry modes = different CRFs.

### Disease Characteristics not counted separately — PrTK05 — April 2026
**CRF Description:** Disease assessment at screening — PSA, biopsy result,
T-staging, NCCN risk group classification, ECOG performance status.
**Claude's Classification:** Grouped with IE/SC as "Disease Assessment"
in the screening visit — not given its own CRF row in Section 4
**Human Correction:** Must be counted as a separate unique CRF (DC form).
**Reason:** Disease characteristics is a distinct buildable XLSForm in
OpenClinica with its own form_id (DC). The pricing summary was treating
it as a sub-assessment of the eligibility visit rather than a standalone
CRF. The EDC structure skill correctly separated it.
**Lesson for future:** Disease assessment / disease characteristics data
(PSA, staging, biopsy, risk classification, ECOG) always constitutes a
separate CRF domain (CDASH TU/RS), not a sub-section of the IE form.
Count it as a unique CRF whenever it appears in the SoA.

### Concomitant Procedures not counted separately from CM — PrTK05 — April 2026
**CRF Description:** Concomitant Procedures (PR_CONCOM) — procedures
reported by participants across all visits, both arms.
**Claude's Classification:** Possibly merged with CM or PR_EBRT; not
given a separate CRF row
**Human Correction:** Must be counted as a separate unique CRF.
**Reason:** PR_CONCOM and PR_EBRT are two completely different XLSForms.
PR_CONCOM is a repeating log of any procedures the participant undergoes.
PR_EBRT is a specific radiation therapy documentation form. The CDASH
domain code (PR) is the same but the form structures, visit assignments,
and field sets are entirely different. Always split by distinct form
design, not by CDASH domain code alone.
**Lesson for future:** When the same CDASH domain appears in two
different contexts with different field sets and visit assignments,
count each as a separate unique CRF. Do not merge CRFs just because
they share a CDASH domain code.

---

## General Lessons Learned

1. **DM and MH are always separate CRFs** unless the protocol explicitly
   states otherwise. Default = 2 unique CRFs.

2. **I/E criteria with arm-specific differences = 2 unique CRFs.** Any time
   criteria are marked as "not applicable" to one arm, create a separate
   form for each arm.

3. **Full assessment at screening + modified assessment at follow-up = 2
   unique CRFs.** This applies to PE, VS (when fields change), and any
   other domain where the field set changes between baseline and follow-up.

4. **Patient diaries and PRO instruments = unique CRFs in OpenClinica ePRO.**
   Never assume paper. Always count as unique CRF builds. Never fold into
   the related site-entered CRF — different entry mode = different CRF.

5. **Disease Characteristics / Disease Assessment = always a separate CRF.**
   PSA, staging, biopsy, NCCN risk group, ECOG — these constitute a
   distinct DC form, never a sub-section of the IE eligibility form.

6. **Same CDASH domain ≠ same CRF.** When a domain (e.g., PR, LB, VS)
   appears in two different contexts with different field sets, visit
   assignments, or arm applicability, count each as a separate unique CRF.
   Split by distinct form design, not by CDASH domain code alone.

7. **CRF count must match EDC structure skill count.** The pricing summary
   and EDC structure skills must identify the same number of unique CRFs.
   If they diverge, the pricing summary is likely under-counting due to
   conceptual grouping. Always split by distinct buildable XLSForm artifact.

---

*This file is maintained by the OpenClinica Service Delivery team. Last updated: April 2026*
