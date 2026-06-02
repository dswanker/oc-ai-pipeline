# OpenClinica EDC Build Conventions

Default build conventions for the `protocol-to-edc-structure` and `edc-builder`
skills. Apply these on every build unless the protocol or customer library
explicitly overrides them.

This file lives at `protocol-to-edc-structure/references/conventions.md` and
is loaded by the `protocol-to-edc-structure` skill before any protocol is
processed. It is referenced again at form-definition time and again at
spec-output generation time.

---

## §0 Foundational Rule: Protocol Data-Item Census + Form Definition Lookup Hierarchy

**This is the most important rule in this file.** Every other convention in
this document is downstream of it. Apply this rule first; everything else
is a fallback for when the rule's two phases haven't decided an outcome.

§0 has two complementary parts that work together:

- **§0.A — Data-Item Census** answers *which fields must exist*. The
  protocol is the authoritative source for this question.
- **§0.B — Lookup Hierarchy** answers *how each field is encoded*
  (its name, type, choice list, group structure, style). A three-level
  hierarchy provides the answer.
- **§0.C — Reconciliation** combines the two. The census is the master
  list. The hierarchy provides encoding for items it has matches for.
  Items the census names but no hierarchy level provides become
  **placeholders**, clearly tagged for human completion.

The principle: **protocol determines content. The hierarchy determines
form.** These are orthogonal questions and must not be conflated. Forms
that go through this rule honestly are auditable end-to-end.

---

### §0.A — Protocol Data-Item Census

The skill performs a complete pass over the protocol *before* defining any
form. The pass enumerates every data item the protocol implies must be
captured anywhere in the build, regardless of which form will host it.

**What to scan:**

- Sections explicitly about consent, eligibility, withdrawal, demographics,
  procedures, follow-up, adverse-event reporting, deviations, randomization
- The Schedule of Assessments table (which items are collected when)
- Endpoint definitions (each endpoint implies measurement fields)
- Investigator obligations sections (often imply documentation fields)
- Glossary / definitions (terms used elsewhere as required data)
- Document header metadata (protocol version, amendment number)

**How to extract:**

Look for sentences in the form *"subject must / will / shall provide /
sign / confirm / report / be evaluated for / undergo X"* and capture X
as an implied data item. Pattern signals include:

| Protocol prose pattern | Implied field type |
|------------------------|---------------------|
| *"approved by … IRB/EC"* | site IRB version |
| *"in a language that is understandable"* | language selector |
| *"sign and date … prior to"* | date + Y/N pre-procedure flag |
| *"Ver. X" / "Amendment X"* in header | protocol version field |
| *"recording / videotape / photograph"* | media-consent Y/N |
| *"HIPAA authorization"* | authorization Y/N |
| *"primary endpoint is X"* | direct measurement field for X |
| *"safety endpoint includes"* | event-capture fields |
| *"randomization stratified by"* | stratum capture fields |
| *"will be collected at each visit"* | per-visit capture |

**Output of §0.A:**

A `protocol_data_item_census` block, one row per implied item:

```json
{
  "item_id":         "<short_id>",
  "form_target":     "<form_id>",
  "description":     "<one-line description of the item>",
  "source_section":  "<protocol_section_number_or_heading>",
  "source_quote":    "<the verbatim sentence(s) that implied the item>",
  "type_hint":       "<date|select_one|select_multiple|text|integer|decimal>",
  "choice_hint":     ["<list of categories if mentioned>"]
}
```

---

### §0.B — Form Definition Lookup Hierarchy

For every form the protocol requires, the skill walks down this hierarchy
and stops at the first level that has a match:

1. **Customer OC4 XLSForm Standard(s)** — highest priority.
   Customer-wide reusable templates maintained at the customer / sponsor
   level (e.g., Abbott's institutional house standards). These exist
   because customers don't redefine the same forms (AE, ConMeds, vital
   signs, etc.) for every study.
   - Match key: form_id / CDASH domain / explicit form_name token.
   - When matched: form is sourced from `customer_oc4_standard`.
   - Location: configured per-customer (see "Customer standards
     location" in `references/customer_standards.md` if present;
     otherwise level 1 returns no matches and we fall through to level 2).

2. **Customer CRF Library** — middle priority.
   Study-specific CRF library files supplied with the protocol upload
   (e.g., `CIP-10601_*.xls` files for Agilis). Includes the CRF Case Book
   PDF and any per-study XLSForm files.
   - Match key: filename token / CDASH domain / explicit form_name token.
   - When matched: form is sourced from `customer_crf_library`.

3. **CDASH form definition** — lowest priority fallback.
   The CDASH-mapped default the skill knows from
   `references/cdash-domain-library.md`. Used only when neither customer
   source has a match.
   - When applied: form is sourced from `cdash_default`. Conventions
     §3, §4, §5, §13, §14 actively shape output here.

A match at *any* level is the success outcome. The output spec records
which level produced each form via the `definition_source` field.

---

### §0.C — Reconciliation: Census × Hierarchy → Placeholders

After §0.A produces the census and §0.B produces the form's base
structure, the skill reconciles the two:

**For each protocol-implied item in the census whose target is this form:**

1. Does the form's chosen source (level 1, 2, or 3) already include a
   field that matches this item? → use the source's encoding as-is.

2. Does another hierarchy level have an encoding for this item even though
   the chosen source lacks it? → carry the encoding forward as a
   protocol-extension field, tagged `library_source: PROTOCOL_EXTENSION`,
   `completion_status: FLAGGED`.

3. **Does no hierarchy level provide an encoding?** → **emit a
   placeholder field**. The form must include the item; how to encode it
   awaits human review or a future level-1 source contribution.

**Placeholder field requirements:**

Every placeholder must carry:

| Field attribute | Value |
|-----------------|-------|
| `name` | Best-effort generated name (CDASH-style or sponsor-style depending on form's `definition_source`); flagged with `_TBD` suffix is acceptable |
| `type` | From `type_hint` in census (`text` is the default when uncertain) |
| `label` | Verbatim or close paraphrase of the protocol-implied wording |
| `library_source` | `PROTOCOL_INFERRED_PLACEHOLDER` |
| `completion_status` | `FLAGGED` |
| `bind::oc:briefdescription` | Short phrase describing what the placeholder is for |
| `relevant` / choice list | Empty unless `choice_hint` was populated |

**Surfacing in the spec:**

A new `review_flags` bucket: `placeholders_for_human_completion`. Each
entry references the form, the item, the protocol section that implied
it, and the protocol quote.

The `conventions_applied` block adds:

```json
"protocol_inferred_placeholders": {
  "applied":  true,
  "count":    <int>,
  "by_form":  { "<form_id>": <int> },
  "items":    [
    {"form": "<form_id>", "name": "<field_name>",
     "source_section": "<protocol_section>",
     "type": "<row_type>"}
  ]
}
```

---

### What this means in practice

- **Silent omission is forbidden.** The build must never silently exclude
  a protocol-implied data item. If no source covers it, it goes in as a
  placeholder.

- **Placeholders are first-class.** They show up in the form, in the
  spec, in the review flags, and in the conventions_applied counters.
  A reviewer can see exactly which fields the protocol asked for that
  no source provided.

- **Conventions §3, §4, §5, §13, §14 apply to placeholders**, since
  placeholders are level-3 generated content by definition (no hierarchy
  source had them). They follow CDASH naming and the rest of the
  convention defaults.

- **Customer-sourced fields stay verbatim.** When the form is sourced
  from level 1 or 2 and a census item *is* covered by that source, copy
  the source content as-authored. The conventions don't override.

### Worked example — Agilis ICF

Census (from protocol §5.2):

| Item ID | form | description | source |
|---------|------|-------------|--------|
| ICF_LANG | ICF | Consent language | §5.2 *"language … understandable to the patient"* |
| ICF_SITE | ICF | Site IRB/EC version | §5.2 *"approved by the center's IRB/EC"* |
| ICF_PRTV | ICF | Protocol version consented | header *"Ver. A"* |
| ICF_PRIOR | ICF | Signed prior to investigation procedures | §5.2 verbatim |
| ICF_DATE | ICF | Date of consent | §5.2 *"sign and date the Informed Consent form"* |

Hierarchy walk for ICF:
- Level 1 (Abbott OC4 standards): not configured → no match
- Level 2 (Case Book): no ICF section → no match
- Level 3 (CDASH default): provides `RFICDAT` only → covers ICF_DATE

Reconciliation:
- ICF_DATE → covered by level 3 → encoded as `RFICDAT` per CDASH
- ICF_LANG → no source → **placeholder** `RFICLANG_TBD`, `select_one` (empty list), FLAGGED
- ICF_SITE → no source → **placeholder** `RFICSITV_TBD`, `select_one` (empty list), FLAGGED
- ICF_PRTV → no source → **placeholder** `RFICPRTV_TBD`, `select_one` (empty list), FLAGGED
- ICF_PRIOR → no source → **placeholder** `RFICPRIOR_TBD`, `select_one` (NY), FLAGGED

Result: 5-field ICF form, with 4 placeholders flagged for human completion.
The 5th item the human authored (videotape consent) is not in the census
because the protocol doesn't mention recording — that genuinely needs a
level-1 source (Abbott OC4 standards) to surface.

### Why this is rule §0 and not §X

Conventions §1–§19 are rules about *what the skill produces*. §0 is the
rule about *how the skill decides what to produce*: protocol → census,
hierarchy → encoding, reconciliation → placeholders. It runs before all
other rules and determines which fields the conventions even apply to.
A new convention added later is downstream of §0; §0 itself is invariant.

---

## 1. Standalone ICF (Informed Consent) Form Always Present

Every study build includes a standalone `ICF` form, even when the customer
library does not contain one and the protocol does not list ICF as a CRF.

**Default fields (CDASH fallback layer only — see §0):**

The ICF form's content is determined by the §0 lookup hierarchy. Walk the
hierarchy first. Only use the table below as the CDASH-default content
when neither customer source has an ICF form.

| name      | type | required | notes |
|-----------|------|----------|-------|
| `RFICDAT` | date | yes      | Date subject signed the Informed Consent. Future-date constraint per §2 below. |

**Default settings (CDASH fallback layer only):**
- `form_id`: `ICF`
- `form_title`: `Informed Consent`
- Wrapped in `begin group` / `end group` per §3 below.

**Form classification:**
- `form_category`: `INFRASTRUCTURE`
- `cdash_domain`: `RF` (Reference / Trial Subjects domain)
- `visits_assigned`: Baseline / Screening event only
- Add to the standard infrastructure form list alongside `DOV`, `DV`, `SPELIG`.

**When the customer CRF library or OC4 standard provides ICF:**
Per §0, use that source's fields, choice lists, group structure, naming,
and required_message coverage *as-is*. Do not impose the CDASH default
fields above. Mark `definition_source = customer_crf_library` (or
`customer_oc4_standard`).

If the protocol requires an ICF field the customer source doesn't provide,
append it per §0 with `library_source: PROTOCOL_EXTENSION` and
`completion_status: FLAGGED`.

**When to skip the form entirely:**
Only skip the ICF form if the protocol or customer library *explicitly
states* that consent is captured outside the EDC (e.g., on a paper
enrollment log not managed in the system). Absence of an explicit
statement = include the form. The customer library having its own ICF
form is **not** a reason to skip — it is a reason to use the customer
version per §0.

**Reporting in `conventions_applied`:**

```json
"icf_form_added_by_default": {
  "applied": true,
  "definition_source": "customer_crf_library",   // or oc4_standard / cdash_default
  "form_id": "ICF",
  "fields": ["ICF_SITE_VER","ICF_LANGUAGE","PROTOCOL_VER",
             "WRITTEN_IC_PRIOR","WRITTEN_CONS_PROC","ICF_DATE"]
}
```

This must be derived dynamically from the actual built forms list — never
hard-coded. If no form with `form_id == "ICF"` (or `cdash_domain == "ICF"`,
or `form_category == "INFRASTRUCTURE"` with consent-related title) exists
in the build, the field reports `applied: false` and surfaces as a **red**
violation — not green or grey — because §1 mandates inclusion.

---

## 2. Future-Date Constraint on Every Date Field

Every survey row with `type: date` must include:

- `constraint: . <= today()`
- `constraint_message: "Future dates are not allowed."`

This applies to all forms, all events, all domains.

**Exceptions:**
A small set of date fields are intentionally future-dated — for example a
projected discharge date or a scheduled-but-not-yet-completed visit date.
For these, do not apply the future-date constraint and tag the row with
`flag_reason: "Future date allowed — scheduled/projected"` so the
reviewer sees the deviation.

---

## 3. begin / end group Wrapping on Every Form

> **Scope per §0:** This convention applies *only* to fields the skill generates from CDASH defaults (level 3 of the lookup hierarchy). Forms sourced from `customer_oc4_standard` or `customer_crf_library` are used as-authored; this convention does not override their content.

Every form's survey content must be wrapped in a `begin group` / `end group`
pair, including forms with no repeating objects and no semantic sections.

**Single-section forms:** Use group name `group0` for the outer container.

**Multi-section forms:** Use semantic group names per section
(e.g., `MH_ARRHY`, `MH_SURG`, `MH_DIS`).

**Appearance:** The `begin group` row should set `appearance: field-list`
for typical layouts.

---

## 4. CDASH Naming Convention for Field Names

> **Scope per §0:** This convention applies *only* to fields the skill generates from CDASH defaults (level 3 of the lookup hierarchy). Forms sourced from `customer_oc4_standard` or `customer_crf_library` are used as-authored; this convention does not override their content.

Use CDASH-aligned field names by default. Reference
`references/cdash-domain-library.md` for the standard name per domain.

| Domain | CDASH name | Description |
|--------|-----------|-------------|
| RF     | `RFICDAT` | Date of Informed Consent |
| IE     | `IETEST`, `IEORRES` | I/E criterion text and response |
| DM     | `BRTHDAT`, `AGE`, `SEX`, `RACE`, `ETHNIC` | Demographics |
| MH     | `MHTERM`, `MHSTDAT` | Medical history term and start date |
| AE     | `AETERM`, `AESTDAT`, `AESEV`, `AEREL`, `AEOUT` | Adverse events |
| VS     | `VSORRES`, `VSORRESU`, `VSDAT` | Vital signs |
| LB     | `LBORRES`, `LBORRESU`, `LBDAT`, `LBNAM` | Lab results |
| DS     | `DSDECOD`, `DSSTDAT` | Disposition |

Customer-preferred names that deviate from CDASH (e.g., descriptive English
names like `WRITTEN_IC_PRIOR`) are used only when the customer library
explicitly provides them. Tag those rows with `cdash_name_deviation: true`
and include the CDASH name in `cdash_standard_name`.

This default may be overridden per customer if a registered customer-specific
naming library is provided in a future build.

---

## 5. UPPERCASE Choice List Names

> **Scope per §0:** This convention applies *only* to fields the skill generates from CDASH defaults (level 3 of the lookup hierarchy). Forms sourced from `customer_oc4_standard` or `customer_crf_library` are used as-authored; this convention does not override their content.

All `list_name` values in the choices sheet must be uppercase short codes.

**Standard lists:** `NY`, `YN`, `NYU`, `YNA`, `SEX`, `RACE`, `ETHNIC`,
`AESEV`, `AEACN`, `AEOUT`, `AEREL`, `DSDECOD`, `DAY`, `MONTH`, `UNK`, `ND`.

**Study-specific lists:** Follow the same uppercase convention
(e.g., `LIK7_AGREE`, `MAPSYS`, `LAAO_DEV`, `NYHA`, `SHD`).

Lowercase variants like `yn`, `sex`, `aesev` are not used.

---

## 6. required_message on Every Required Field

When `required: yes` is set on a survey row, populate `required_message`
with a brief instruction telling the end user what to enter. OpenClinica
displays this inline when the user tries to submit without completing
the field.

**Format options:**
- `"Please indicate <field description>."`
- `"<Field name> is required."`
- `"Please enter the <field description>."`

**Apply universally** — to every required survey row regardless of type
(`date`, `select_one`, `select_multiple`, `integer`, `decimal`, `text`).

**Examples:**

| name        | required_message |
|-------------|------------------|
| `RFICDAT`   | Please indicate the date the subject signed the consent form. |
| `AESTDAT`   | Date of AE onset is required. |
| `AESAE`     | Please indicate whether this is a Serious Adverse Event. |
| `DSDECOD`   | Reason for disposition is required. |
| `MHTERM`    | Medical history term is required. |

---

## 7. Common Event with Reactive Safety/Admin Forms

OpenClinica supports two event types: **Visit-Based** (scheduled, with start/end
dates) and **Common** (unscheduled, triggered by event occurrence — see
`Minimal_board_json.md`). Forms reported reactively rather than at scheduled
visits belong in a Common event, not visit-scheduled.

**Default Common event:**

```
event_oid:   SE_COMMON
event_type:  Common
event_title: Common — Reported As Occurring
isRepeating: true
```

**Forms always placed in `SE_COMMON`:**

| form_id | reason |
|---------|--------|
| `AE`    | Adverse events arise at any time; reporting is event-triggered, not visit-scheduled. |
| `CM`    | Concomitant medications start/stop independent of study visits. |
| `DV`    | Protocol deviations occur at any time. |

**Forms placed in `SE_COMMON` *only if the protocol requires the form*:**

| form_id | trigger condition |
|---------|-------------------|
| `DD`    | Only when the protocol requires Device Deficiency reporting (medical-device studies). If the protocol has no DD requirement, omit the form entirely; do not create an empty placeholder. |

**Forms that stay visit-scheduled (do NOT place in `SE_COMMON`):**

- `PREGPART` — pregnancy testing/reporting follows the visits the protocol
  designates (typically screening + each treatment visit + EOS for FOCBP).
  Visit-based event placement matches the protocol-driven cadence.

**Why this matters.** Scheduling AE/CM at every protocol visit creates artificial
empty repeats, confuses sites about when to enter data, and misrepresents the
operational model. The Common event type exists for exactly this case.

**Override.** A customer library or override block can:
- Remove a form from `SE_COMMON` (`common_event_forms: []` or per-form removal)
- Move AE/CM to specific visits if a sponsor genuinely requires per-visit capture
- Add additional forms to `SE_COMMON` not in the default list

When override is applied, surface the deviation in `conventions_applied.common_event_applied.forms_excluded_by_override` with a one-line rationale.

---

## 8. Soft Edit Checks by Default

OpenClinica supports two strictness levels for required fields and constraints:
soft (default) and strict (hard-stop). Strict checks make data entry painful
and OC explicitly recommends them only when data quality justifies the cost.
OC docs: *"All checks that are not explicitly defined as hard edit checks are
soft edit checks in all forms"*; Participate forms auto-promote everything
to strict regardless.

**Default.** Skill never emits `bind::oc:required-type: strict` or
`bind::oc:constraint-type: strict` on any survey row. All required fields and
constraints are soft.

**Override.** Strict only when:
- Protocol explicitly mandates hard-stop validation (e.g., subject ID format,
  consent date must equal today)
- Customer library carries `strict` — carry forward as-is

When applied, count strict-typed rows in
`conventions_applied.soft_edit_checks_applied.strict_required_count` and
`.strict_constraint_count`.

---

## 9. PDate for Recall-Based Dates, Date for Definite Events

OC distinguishes two date types:
- **`Date`** accepts only DD-MMM-YYYY (full dates)
- **`PDate`** accepts DD-MMM-YYYY, MMM-YYYY, or YYYY (partial dates)

OC's blog warns: requiring a full date when only month/year is known is
*"a major hazard for analysis"* — users pick "1st" or "15th" placeholders
that look authoritative but are not.

**Default categorization:**

| Type | Use for |
|------|---------|
| `Date` | Informed consent date, randomization date, visit date, sample collection date, dose admin date, enrollment date — events the site staff records contemporaneously and knows in full |
| `PDate` | CMSTDAT, CMENDAT, AESTDAT, AEENDAT, MHSTDAT, prior procedure dates, date of diagnosis, date of birth (when partial-DOB is acceptable) |

**Caveats:**
- OC Rules don't support PDate operators in rule expressions. Cross-form
  calculations involving PDate fields need flagging with status `FLAGGED`
  and a brief description noting the limitation.
- The existing manual partial-date decomposition pattern in
  `references/xlsform-patterns.md` (`[prefix]DAT_YEAR` / `_MON` / `_DAY` /
  `_UNK` with calculate fields) is preserved as-is. Whether to migrate to
  native PDate is a separate decision — see "Open Items" below.

**Override.** Protocol mandates exact date with no recall-uncertainty wording.

When applied, surface in
`conventions_applied.pdate_for_recall_dates.{pdate_fields, date_fields, deviations}`.

---

## 10. Minimal/Autocomplete Appearance for Long Pick-Lists

OC4 docs recommend `appearance: minimal autocomplete` to filter dropdown
options as the user types. Two thresholds apply, depending on form purpose:

| Form purpose | Trigger | Rationale |
|--------------|---------|-----------|
| Participate / ePRO | `select_one` or `select_multiple` with **5+ choices** | Screen-space pressure on mobile (per OC internal best-practice doc) |
| Site-staff form | `select_one` or `select_multiple` with **20+ choices** | Performance optimization for long lists (per OC4 public docs) |

Below the trigger, default appearance (vertical radio buttons / checkboxes)
is fine.

**Default.** Skill applies the threshold per form's `is_epro` / Participate
flag. Each list crossing its threshold gets `appearance: minimal autocomplete`
unless the customer library explicitly specifies otherwise.

**Override.** Customer library specifies a different appearance.

When applied, surface in
`conventions_applied.autocomplete_appearance.{participate_lists_eligible,
participate_lists_with_minimal, site_lists_eligible, site_lists_with_minimal}`.

---

## 11. External CSV for Choice Lists Exceeding 3,500 Characters

OC4 hard limit: *"The choices worksheet of the form template restricts the
combination of labels and names to a maximum of 4,000 characters."* Forms
that exceed this fail to upload.

**Default.** Skill computes total `len(label) + len(name)` across all rows of
each choice list. If a single list exceeds 3,500 characters (safety margin
below OC's 4,000 hard ceiling), emit it as an external CSV file referenced
via `search()` from the survey row, with `appearance: minimal autocomplete`
applied automatically.

Common triggers: medication dictionaries, country/state lists, MedDRA terms,
ICD code lists, sponsor DVG term lists, lab test name catalogs.

**Output.** Each externalized list becomes its own CSV file alongside the
form xlsx, named `{study_id}_{list_name}.csv`. Add an entry to the build
package manifest.

When applied, surface in
`conventions_applied.external_csv_for_long_lists.{lists_exceeded_threshold,
external_csvs_created}`.

---

## 12. Item-Count Caps as Build-Time Warnings

OC4 docs: *"OpenClinica recommends having no more than 100-200 items on a
form and no more than 50 items on a Participate form, specifically when it is
likely to be accessed from a mobile device."*

This is a **build-time check**, not a default. Forms over the cap aren't
broken — they load slowly. No automatic remediation; this is human judgment
territory.

**Build-time check.** After all forms are defined, count survey rows
excluding `note`, `calculate`, and group-marker rows.

| Form purpose | Threshold | Action when exceeded |
|--------------|-----------|---------------------|
| Site-facing | 200 items | AMBER flag in Conventions Applied page; recommend splitting form or paginating with `Style: pages` |
| Participate / ePRO | 50 items | AMBER flag; recommend pagination with `Style: pages` |

When applied, surface form_id + count for each over-cap form in
`conventions_applied.item_count_caps.{site_forms_over_200,
participate_forms_over_50}`.

---

## 13. bind::oc:briefdescription on Every Survey Row

> **Scope per §0:** Applies universally — auto-fill briefdescription on every data row regardless of `definition_source` (per IE-6 walkthrough decision). Both customer-sourced and CDASH-default rows get the auto-fill. Where the customer source already provides a briefdescription, that authored value wins; the auto-fill only populates rows where the field is empty.

OC uses `bind::oc:briefdescription` in three places: annotated eCRF
generation, Participant Matrix custom column headers, and item brief
description in Insight. When unpopulated, OC falls back to the full label,
which is often too long for table headers and produces poor annotated CRFs.

**Default.** Every survey row of `type` `text`, `integer`, `decimal`,
`date`, `select_one`, `select_multiple`, or `calculate` populates
`bind::oc:briefdescription`. Excluded: `note`, `begin group`, `end group`,
`begin repeat`, `end repeat`.

**Generation rule:**
- For CDASH-mapped fields, use the standard CDASH label
  ("Subject ID", "AE Start Date", "AE Severity")
- For custom fields, derive from question text — first 3-5 words, no
  trailing punctuation, sentence case

**Override.** Customer library already populates it; carry forward.

When applied, surface
`conventions_applied.briefdescription_coverage.{applied_count,
total_data_rows, missing_count}` with green/amber based on coverage.

---

## 14. Form Style Declared Explicitly per Form Purpose

> **Scope per §0:** This convention applies *only* to fields the skill generates from CDASH defaults (level 3 of the lookup hierarchy). Forms sourced from `customer_oc4_standard` or `customer_crf_library` are used as-authored; this convention does not override their content.

OC4 supports four form styles. The recent OC4 default flipped to
"Simple-Single Page" (per release notes). Each form's `style` column on the
settings sheet should be populated deliberately, never left blank by default.

**Defaults by form purpose:**

| Form purpose | `style` value | Rationale |
|---|---|---|
| Site-staff form, ≤30 items | `(blank)` Simple-single | Default; clean for short forms |
| Site-staff form, dense tabular data (LB, VS panels) | `theme-grid` | Grid layout suits tabular data |
| Site-staff form, > 50 items | `pages` | Pagination helps cognitive load |
| Participate / ePRO form | `pages` | OC blog: "fewer questions on more pages is preferable" + portrait-phone optimization |

**Override.** Customer library specifies otherwise; carry forward.

When applied, surface counts in
`conventions_applied.form_style_explicit.{site_simple_single,
site_simple_pages, site_theme_grid, participate_simple_pages, missing_style}`.

---

## 15. crossform_references Auto-Populated on Settings Sheet

OC4 docs: *"Crossform References optimize loading forms by specifying the
events or forms that the system needs to reference for cross-checks. Instead
of the system loading all of a participant's information, it only loads the
information necessary for the cross-check."*

**Default.** Whenever a form contains any `calculate` row using
`bind::oc:external: clinicaldata` (cross-form reference), the skill
auto-populates `crossform_references` on the settings sheet with the list of
`(event_oid, form_oid)` pairs the calculations reference. The dependency
graph is already in `cross_form_dependencies` in the spec JSON — this
convention just propagates it.

**Format.** Comma-separated list of `EventOID/FormOID` pairs in the
`crossform_references` cell of the settings sheet.

**Override.** Manual entry in customer library; carry forward.

When applied, surface in
`conventions_applied.crossform_references_populated.{forms_with_cross_form_calc,
forms_with_crossform_references}`.

---

## 16. bind::oc:itemgroup Keep-Together for Repeating Logical Records

OC4 docs: *"In the bind::oc:itemgroup column, you are using repeating
groups, which must be kept separate, it is ideal to keep items in the same
group. These groups do not have to correspond to the begin/end groups.
Keeping items in the same group allows for better data visualization in
Insight because participant data is displayed on tables by item group, not
by form."*

The visual `begin group` / `end group` markers don't have to match the
`bind::oc:itemgroup` data-grouping value. All fields belonging to one
logical record (one AE, one CM, one MH event) should share a single
`bind::oc:itemgroup` value, even when split across multiple visual groups.

**Default.** Within a repeating logical record (one AE, one CM, one MH
entry), all data fields share a single `bind::oc:itemgroup` value matching
the CDASH domain code (`AE`, `CM`, `MH`, `DV`, `DD`, etc.) — regardless of
how many `begin group` / `end group` blocks exist for visual organization.

**Override.** Multiple genuinely distinct sub-records within one form (rare,
but legitimate — e.g., a form capturing both header-level and line-item-level
data with independent repeat semantics).

When applied, surface in
`conventions_applied.itemgroup_keep_together.{repeating_logical_records,
repeating_records_consistent, deviations}`.

---

## 17. Likert Appearance Only with ≤5 Short-Label Choices

OC's internal Participate Form Design best-practice doc: *"Use the Likert
Scale appearance with ~5 values or less. The length of the text for the
Choice Labels will also affect how well this renders on smaller screens."*

**Default.** Skill emits `appearance: likert` only when **both** conditions hold:
- Choice list has 5 or fewer options
- Every choice label is 20 characters or fewer

When either condition fails, fall back to:
- Vertical radio buttons (default appearance) — for site-staff forms
- `appearance: minimal` (dropdown) — for Participate forms

**Important.** This affects scales like the Agilis EXP form's 7-point
expectation/satisfaction scales. Since EXP is a physician-PRO form (not
patient ePRO), the rule is less critical there — but a 7-point Likert on a
mobile patient form would render poorly.

**Override.** Customer library explicitly specifies `likert` despite
exceeding the threshold; carry forward with `FLAGGED` status and a note.

When applied, surface in
`conventions_applied.likert_appearance_rule.{likert_fields, likert_compliant,
likert_non_compliant}`.

---

## 18. VAS Scales Rendered Vertically

OC's internal Participate Form Design best-practice doc: *"For VAS Scales,
use a vertical appearance instead of horizontal."*

**Default.** Any Visual Analog Scale field (typically a 0-100 numeric slider
for pain, fatigue, quality-of-life) uses a vertical appearance. The exact
appearance keyword is OC-version-specific — note as `appearance: vas vertical`
or `appearance: distress vertical` per the OC widget convention in use, and
flag for verification at first encounter.

**Trigger detection:** Field has `appearance: distress`, `appearance: vas`,
or any other VAS-style widget keyword.

**Override.** Customer library specifies horizontal explicitly.

This is a narrow rule that fires only when a VAS exists. Most CDASH-domain
protocols don't have one. When applied, surface in
`conventions_applied.vas_appearance_rule.{vas_fields, vas_vertical}`.

---

## 19. Table Appearance Only with Short Choice Labels

OC's internal Participate Form Design best-practice doc: *"Only use Table
appearance if the Choice Labels are short."*

**Default.** Skill emits `appearance: table-list` (or the OC table appearance
keyword in current use) only when every choice label in the referenced list
is 15 characters or fewer.

When labels exceed the threshold, fall back to:
- Vertical layout (default) — labels wrap naturally on small screens
- `appearance: minimal` — for very long lists

**Override.** Customer library specifies table appearance despite long labels;
carry forward with `FLAGGED` status.

When applied, surface in
`conventions_applied.table_appearance_rule.{table_fields, table_compliant}`.

---

## 20. Forms-Completion Safety-Net Group

> **Scope per §0:** Applies universally — both customer-sourced and CDASH-default forms.

Forms that anchor a clinical visit may include a final group whose purpose
is not data capture but operator reminder — Y/N flags asking whether other
forms (AE, DV, DS) need to be completed for the same visit, paired with
red-text reminder notes. The pattern is observed in customer authoring as
a forms-completion safety net.

**Default behaviour:** Apply to "anchor" forms (the longest or most
encounter-defining form per visit, typically MH for baseline, PROC for
procedure visit). Each safety-net group contains:

- One `select_one YN` row per related form (`{FORM}AE_YN`, `{FORM}DV_YN`,
  `{FORM}DS_YN` using IE-2a domain-prefix naming convention)
- One `note` row per Y/N, paired and gated per §22

**Reporting in `conventions_applied`:**
```json
"forms_completion_safety_net": {
  "applied_count": <int>,
  "forms_with_safety_net": ["MH", ...]
}
```

**Override.** When the customer library explicitly authors a different
group structure for forms-completion reminders, carry forward as-is.

---

## 21. Header Group Pattern

> **Scope per §0:** Applies universally.

Forms typically open with an unlabeled wrapper group (`group0` per IE-1a)
that contains date and identification fields (Date of Assessment / Date
of Procedure / Date of Informed Consent, Physician Name, etc.) before
section-numbered content begins.

**Default behaviour:**

- The first `begin group` row is named `group0` and has empty `label`.
- Header group contains only date and identification fields, not clinical
  content.
- Subsequent groups (`group1`, `group2`, ...) hold section-numbered
  Case Book content.

**Reporting in `conventions_applied`:**
```json
"header_group_pattern": {
  "forms_with_header": <int>,
  "forms_without_header": <int>
}
```

---

## 22. Reminder Notes Gated by Y/N Trigger

> **Scope per §0:** Applies universally.

Note rows that immediately follow a `select_one YN` field, where the note
text contains "If yes" or similar conditional language, gate themselves
with a `relevant` clause referencing the preceding YN field. The note
displays only when the YN answer is `'Y'`.

**Auto-detection rule.** The skill detects the pattern when:

- A `note` row immediately follows a `select_one YN` (or `YNU`) row
- The note label contains the literal text "If yes" (case-insensitive),
  "if applicable", "if so", or similar conditional preface
- The preceding YN row's `name` is captured as the trigger

**Auto-applied behaviour.** When detected, set:
```
relevant: ${preceding_yn_name}='Y'
```

This reduces visual clutter — the operator only sees the reminder when it
applies. Matches customer authoring pattern observed across forms.

**Reporting in `conventions_applied`:**
```json
"reminder_notes_gated": {
  "applied_count": <int>,
  "detected_patterns": [
    {"form": "MH", "trigger": "MHAE_YN", "note": "MHAE_NOTE"},
    ...
  ]
}
```

**Override.** Customer library leaves a paired note ungated; carry forward
with `FLAGGED` status and emit a `review_flags.note_gating_review` entry.

---

## 23. Source-Label Disambiguation for Hidden-Parent-Context Labels

> **Scope per §0:** Applies universally.

When a level-2 source (Case Book) provides a label that is meaningful only
in immediate proximity to its parent question (e.g., `"If yes"` next to a
parent `"Ventricular Tachycardia"` question), and the parent question is
not visually adjacent in the rendered form (because the gated field appears
later, after a `relevant` evaluates), the label is rewritten to be
self-explanatory.

**Detection rule.** The skill flags a label for rewrite when:

- The label is one of: `"If yes"`, `"If yes:"`, `"If yes, "`, `"If applicable"`,
  `"If other"` (with optional trailing punctuation)
- The field has a `relevant` clause referencing a parent field
- The parent field's label provides domain context (e.g.,
  `"Ventricular Tachycardia"`)

**Auto-applied behaviour.** Rewrite the label to incorporate parent context:

| Original label | Parent question | Rewritten label |
|----------------|-----------------|------------------|
| `"If yes"` | `"Ventricular Tachycardia"` | `"If yes, type of VT"` (with appropriate brevity) |
| `"If yes:"` | `"Atrial Flutter"` | `"If yes, type of AFL"` |

Section numbering from the Case Book is preserved (e.g., `"1.5.1. If yes, type of VT"`).

**Surfacing.** Every rewrite is recorded in
`review_flags.protocol_ambiguous`:
```json
{
  "form": "MH",
  "field": "MHARRVT_TYPE",
  "case_book_section": "1.5.1",
  "case_book_label": "If yes",
  "skill_rewrite": "If yes, type of VT",
  "rationale": "Hidden-parent-context disambiguation",
  "action_required": "Verify rewrite matches sponsor intent."
}
```

**Override.** Customer library uses the original label verbatim and the
form is otherwise sourced from level 2; carry forward as-authored without
flag. The §23 rewrite applies only when the skill is generating the label
itself.

---

## 24. Source Ambiguity → Clinical Reasoning + Auto-Flag

> **Scope per §0:** Applies universally.

When a level-2 source (e.g., Case Book PDF) renders a question in a way
that is ambiguous between two valid interpretations (e.g., `o` symbols
for a checklist could mean radio-button single-select OR checkbox
multi-select), the skill resolves the ambiguity using clinical reasoning
and emits a `review_flags.choice_list_review` entry.

**Detection rule.** The skill flags ambiguity when:

- A list of options appears in the source with markers (`o`, `□`, `(_)`)
  that are visually consistent with multiple input types
- The source does not specify input type via accompanying instruction
  (e.g., "select all that apply" vs "select one")

**Resolution principle.** The skill defaults to the interpretation that
**preserves more data**:

- For check-style lists where multiple values can plausibly co-occur
  (e.g., implanted devices, comorbidities, co-medications): default to
  `select_multiple`
- For mutually-exclusive categories (e.g., NYHA class, AFL type): default
  to `select_one`
- When clinical reasoning is unclear, default to `select_multiple` (lossless)

**Surfacing.** Every ambiguity-resolved field is recorded:
```json
{
  "form": "MH",
  "field": "MHSURGDEV_TYPE",
  "case_book_section": "2.5.1",
  "issue": "Source rendering is ambiguous between radio and checkbox.",
  "skill_decision": "Defaulted to multi-select on clinical reasoning…",
  "action_required": "Verify with sponsor."
}
```

**Override.** Sponsor confirms a different interpretation; update field
type and remove the flag.

---

## 25. Eligibility Verdict — 3-State Pattern

> **Scope per §0:** Applies universally to forms with all-conditions-met
> derivations (eligibility, completion checks, etc.).

When a form contains an eligibility verdict or all-conditions-met
derivation, the verdict field uses a 3-state result vocabulary instead of
a 2-state Yes/No.

**Default vocabulary** (override-able per study):
- `'Eligible'` — all inclusion criteria met AND all exclusion criteria absent
- `'Ineligible'` — at least one inclusion criterion missed OR at least one
  exclusion criterion present
- `'Not yet calculated'` — verdict cannot be determined yet (unanswered
  conditions remain)

**Default calc structure** (matches IE-9 conventions):

```
if(<all_eligible_clauses_joined_by_and>, 'Eligible',
if(<any_disqualifier_clauses_joined_by_or>, 'Ineligible', 'Not yet calculated'))
```

**Calc formatting per IE-9b/c:**
- No spaces around `=` in expressions (`${IEINC1}='Y'`, not `${IEINC1} = 'Y'`)
- Multi-line `\n` formatting between conditions when calc has more than ~3
  individual condition clauses

**Implementation pattern.** A pair of fields:
- `{FORM}ELIG_CALC` — type `calculate`, returns the verdict text
- `{FORM}ELIG` — type `text`, `readonly: yes`, `calculation: ${{FORM}ELIG_CALC}`

Both tagged `library_source: PROTOCOL_EXTENSION`, `completion_status:
FLAGGED` for human review.

**Why 3-state matters.** A 2-state pattern (Eligible / Not Eligible) would
display "Not Eligible" before any conditions are answered — clinically
misleading because eligibility hasn't been determined yet. 3-state cleanly
distinguishes "all conditions evaluated, verdict reached" from "evaluation
incomplete."

**Reporting in `conventions_applied`:**
```json
"eligibility_verdict_3state": {
  "applied_forms": ["IE", ...],
  "calc_field_pattern": "{FORM}ELIG_CALC + {FORM}ELIG"
}
```

---

## 26. Side-by-Side Layout for Value+Unit Pairs (`w2` Width Class)

> **Scope per §0:** Applies universally.

Numeric measurement fields paired with unit selectors (height + height
units, weight + weight units, temperature + temperature units, dose +
dose units, etc.) render side-by-side using OpenClinica's `w2` width-class
appearance modifier.

**Auto-detection rule (conservative).** The skill applies the pattern when:

- A `decimal` or `integer` field is immediately followed by a `select_one`
  field
- The `select_one` field's `list_name` matches `unit*`, `*_U`, `*_UNIT`,
  `*_UNITS`, OR
- The `select_one` field's `name` ends in `_U`, `_UNIT`, `_UNITS`

**Auto-applied behaviour.**

- The numeric field gets `appearance: w2`
- The unit field gets `appearance: horizontal w2` (preserves any existing
  `horizontal` from §17 / §18 layout rules)

**UX rationale.** A height value without its unit is ambiguous. Visual
pairing prevents data-entry errors where the operator enters a value and
forgets to set the unit (or vice versa). The pair reads as one composite
measurement (`72.5 inches`) instead of stacked rows.

**Reporting in `conventions_applied`:**
```json
"value_unit_pair_layout": {
  "pairs_detected": <int>,
  "pairs_with_w2": <int>,
  "pairs": [
    {"form": "DM_BL", "value_field": "HEIGHT", "unit_field": "HEIGHT_U"},
    ...
  ]
}
```

**Override.** Customer library uses different layout (e.g., stacked, or
`w3` width class); carry forward as-authored.

---

## 27. Sentinel-Value Exclusivity Constraint on Multi-Select Fields

> **Scope per §0:** Applies universally.

Multi-select fields whose choice list contains a sentinel value meaning
"no real value provided" (e.g., `DECLINED`, `UNKNOWN`, `NONE`, `N/A`,
`REFUSED`) get an auto-generated exclusivity constraint preventing the
sentinel from being co-selected with any real value.

**Auto-detection rule.** The skill applies the constraint when:

- Field type is `select_multiple`
- The field's choice list contains at least one of the sentinel values:
  `DECLINED`, `UNKNOWN`, `NONE`, `N_A`, `NA`, `REFUSED` (case-insensitive
  match on choice `name`)
- The field's choice list also contains at least one non-sentinel value

**`OTHER` is NOT a sentinel.** `OTHER` semantically means "a real value
that doesn't fit listed categories" — it can legitimately co-occur with
listed values (e.g., a subject with both "Atrial septal defect" AND
"Other: <specify>"). The §27 exclusivity rule does not apply to `OTHER`.

**Auto-applied constraint:**
```
not(selected(., '<SENTINEL>') and (selected(., '<other1>') or selected(., '<other2>') or ...))
```

**Auto-applied constraint message:**
```
"Cannot select '<sentinel_label>' and other options. Please correct or clarify."
```

The `<sentinel_label>` uses the choice's display label (e.g., "Declined",
not "DECLINED").

**Reporting in `conventions_applied`:**
```json
"sentinel_exclusivity": {
  "applied_count": <int>,
  "fields": [
    {"form": "DM_BL", "field": "RACE", "sentinel": "DECLINED"},
    ...
  ]
}
```

**Override.** Customer library specifies a different exclusivity rule (or
no constraint); carry forward as-authored with `FLAGGED` status.

---

## 28. Decimal Measurement Precision Constraint

> **Scope per §0:** Applies universally.

Decimal fields capturing physical measurements (height, weight,
temperature, blood pressure, etc.) get an auto-generated precision
constraint based on the measurement's typical clinical precision.

**Default precision table:**

| Measurement type | Decimal places | Detection trigger |
|------------------|------------------|---------------------|
| Height | 2 | field name contains `HEIGHT`, `HT` |
| Weight | 2 | field name contains `WEIGHT`, `WT` |
| Temperature | 1 | field name contains `TEMP` |
| Blood pressure | 0 (integer) | field name contains `BP`, `SBP`, `DBP` |
| Heart rate / pulse | 0 (integer) | field name contains `HR`, `PULSE` |
| Other decimal | 2 (default) | any unmatched `decimal` field |

**Auto-applied constraint:**
```
. >0 and . =round(${field_name}, <decimals>)
```

The `>0` clause provides positive-value sanity; the `round()` clause
enforces precision. Combined with `and`.

**Auto-applied constraint message:**
```
"Value must be positive, with no more than <N> decimal place(s)."
```

**Reporting in `conventions_applied`:**
```json
"decimal_precision_constraint": {
  "applied_count": <int>,
  "fields": [
    {"form": "DM_BL", "field": "HEIGHT", "decimals": 2},
    {"form": "DM_BL", "field": "WEIGHT", "decimals": 2},
    ...
  ]
}
```

**Override.** Customer library specifies different precision (or no
constraint); carry forward as-authored.

---

## §29. calculate Fields Must Always Have readonly: yes

**Rule.** Every survey row with type `calculate` — whether it computes a
derived value, pulls a cross-form reference, or assembles a display string —
**must** have `readonly: yes` in the survey row. OpenClinica's form validator
rejects any `calculate` field that does not carry `readonly: yes`, producing
the error:

> *"Element '[name]' cannot have a value in column 'calculation' unless it is
> read-only."*

This failure is silent at build time but fatal at upload time — the form
upload returns success but no version object is created, causing the
publisher to retry indefinitely.

**Apply to every calculate row without exception:**

```
type:     calculate
name:     [field_name]
readonly: yes
calculation: [expression]
```

**Common violation to prevent — derived integers or dates.** If a protocol
asks to compute a value (e.g. age from birthdate, BMI from height/weight,
a date arithmetic result), the derived field must be:
- type `calculate` with `readonly: yes`, OR
- type `text` with `readonly: yes` and `calculation: [expression]`

Never leave `readonly` blank on a calculate row. Never generate AGE or
similar clinically meaningful items as `calculate` without `readonly: yes` —
instead keep AGE as a directly entered `integer` (with constraint) and add a
separate `AGE_CALC calculate readonly: yes` row only if derivation is also
needed.

**Scope.** Applies to all calculate rows in all survey sheets across all
forms: infrastructure forms (ICF, DOV), CDASH clinical forms, and custom
forms.

**Override.** None — this is an OC platform requirement, not a stylistic
default. It cannot be overridden by customer library or protocol instruction.

When applied, surface in `conventions_applied` as:
```json
{ "convention": "29", "calculate_readonly_enforced": true,
  "calculate_rows_audited": N, "violations_corrected": N }
```

---

## Open Items (Tracked for Future Convention Decisions)

These are deferred decisions noted but not yet codified as defaults:

- **PDate vs manual partial-date decomposition.** §9 preserves the existing
  `[prefix]DAT_YEAR` / `_MON` / `_DAY` / `_UNK` decomposition pattern from
  `xlsform-patterns.md`. Whether to migrate forms with partial-date fields to
  OC's native `PDate` type (collapsing the decomposition to a single field) is
  an open architectural decision. Native PDate gives cleaner forms but loses
  the explicit "unknown" capture; manual decomposition gives cleaner data
  extracts but adds field-count overhead.

- **ePRO first-page question count, progressive disclosure, label-placement
  rules.** Tier 4 candidates from the OpenClinica blog are too situational
  for automation. Revisit when an actual ePRO build is in scope.

---

## What This Skill Does NOT Default

The following ICF-form fields, observed in some customer-built ICF forms,
are sponsor- or study-specific and must come from explicit input
(protocol section, customer library, or sponsor configuration document).
The skill does **not** invent or default these:

- ICF site version code (e.g., A/B/C/D)
- ICF language picklist (e.g., English / French / Chinese)
- Protocol version under which subject was consented
- Procedure videotaping / recording consent indicator

If the customer library includes these, carry them forward as-is and tag
with `library_source: CUSTOMER_PDF` or `CUSTOMER_XLSX_*` per the standard
mode rules.

---

## Surfacing in the Study Specification

Every convention applied (or intentionally skipped) must be visible to the
human reviewer of the EDC Structure specification — not just baked silently
into the survey rows. Surface them in **all four** spec outputs.

### A. Output A — Human-Readable Text Summary

Insert a new section immediately after the header block, before
`SECTION 1 — STUDY EVENT SCHEDULE`:

```
SECTION 0 — BUILD CONVENTIONS APPLIED
─────────────────────────────────────────────────────
The following defaults from references/conventions.md were applied:

  ✓ Standalone ICF form added (default behavior)
       form_id: ICF, fields: RFICDAT
  ✓ Future-date constraint applied to N date fields across M forms
       (X date fields exempted — see review_flags.constraint_review)
  ✓ begin/end group wrapping applied to all N forms
  ✓ CDASH naming convention applied to N fields
       (X customer-name deviations carried forward — see name_deviations)
  ✓ UPPERCASE choice list naming applied to N choice lists
  ✓ required_message populated for all N required survey rows
  ✓ Common event SE_COMMON added with N reactive forms: AE, CM, DV
       (X conditional forms added: DD)
       (Y conditional forms skipped: DD — protocol does not require)
  ✓ Soft edit checks default applied (X strict required, Y strict constraint overrides)
  ✓ PDate / Date type categorization applied (N PDate, M Date)
       (X review flags for PDate fields used in cross-form calculations)
  ✓ Minimal/autocomplete appearance applied where threshold met
       (Participate ≥5: A applied / B eligible | Site ≥20: C applied / D eligible)
  ✓ External CSV emitted for N choice list(s) exceeding 3,500-character threshold
  ✓ Item-count check completed: X site form(s) over 200, Y Participate form(s) over 50
  ✓ bind::oc:briefdescription populated: X / Y data rows (Z missing)
  ✓ Form style declared explicitly on all N forms
       (Simple-single A, Simple-pages B, theme-grid C, Participate-pages D)
  ✓ crossform_references auto-populated on N forms with cross-form calc rows
  ✓ bind::oc:itemgroup keep-together rule applied to N repeating logical records
  ✓ Likert appearance rule applied: A compliant / B total Likert fields
       (C non-compliant override(s) carried from customer library)
  ✓ VAS appearance rule applied: A vertical / B total VAS fields
  ✓ Table appearance rule applied: A compliant / B total table-appearance fields

Override or remove these defaults by editing the EDC Structure XLSX
and re-running the skill, OR by adding a study-specific override block
to the customer library.
```

### B. Output B — JSON Spec

Add a `conventions_applied` block under `study_meta`:

```json
{
  "study_meta": {
    "...": "...",
    "conventions_applied": {
      "version": "1",
      "source": "references/conventions.md",
      "icf_form_added_by_default": true,
      "future_date_constraint_applied": {
        "fields_constrained": 42,
        "fields_exempted": 0,
        "exemptions": []
      },
      "group_wrapping_applied": {
        "forms_wrapped": 12,
        "single_section_group_name": "group0"
      },
      "cdash_naming_applied": {
        "fields_using_cdash": 218,
        "name_deviations": 6,
        "deviations_list": [
          {"form": "DM_BL", "field": "BMI",
           "cdash_standard_name": "VSORRES (BMI)"}
        ]
      },
      "uppercase_choice_lists": true,
      "required_message_coverage": {
        "required_fields": 95,
        "fields_with_message": 95
      },
      "common_event_applied": {
        "event_oid": "SE_COMMON",
        "event_type": "Common",
        "event_title": "Common — Reported As Occurring",
        "forms_in_common_event": ["AE", "CM", "DV"],
        "forms_excluded_by_override": [],
        "conditional_forms_added": [],
        "conditional_forms_skipped": [
          {"form": "DD", "reason": "Protocol does not require Device Deficiency reporting"}
        ]
      },
      "soft_edit_checks_applied": {
        "applied": true,
        "strict_required_count": 0,
        "strict_constraint_count": 0,
        "overrides": []
      },
      "pdate_for_recall_dates": {
        "applied": true,
        "pdate_fields": 8,
        "date_fields": 24,
        "rule_flagged_crossform_uses": [],
        "deviations": []
      },
      "autocomplete_appearance": {
        "applied": true,
        "participate_lists_eligible": 0,
        "participate_lists_with_minimal": 0,
        "site_lists_eligible": 4,
        "site_lists_with_minimal": 4
      },
      "external_csv_for_long_lists": {
        "applied": true,
        "lists_exceeded_threshold": 0,
        "external_csvs_created": []
      },
      "item_count_caps": {
        "checked": true,
        "site_forms_over_200": [],
        "participate_forms_over_50": []
      },
      "briefdescription_coverage": {
        "applied_count": 282,
        "total_data_rows": 282,
        "missing_count": 0,
        "missing_list": []
      },
      "form_style_explicit": {
        "applied": true,
        "site_simple_single": 8,
        "site_simple_pages": 0,
        "site_theme_grid": 2,
        "participate_simple_pages": 0,
        "missing_style": 0
      },
      "crossform_references_populated": {
        "applied": true,
        "forms_with_cross_form_calc": 4,
        "forms_with_crossform_references": 4
      },
      "itemgroup_keep_together": {
        "applied": true,
        "repeating_logical_records": 6,
        "repeating_records_consistent": 6,
        "deviations": []
      },
      "likert_appearance_rule": {
        "applied": true,
        "likert_fields": 0,
        "likert_compliant": 0,
        "likert_non_compliant": []
      },
      "vas_appearance_rule": {
        "applied": true,
        "vas_fields": 0,
        "vas_vertical": 0
      },
      "table_appearance_rule": {
        "applied": true,
        "table_fields": 0,
        "table_compliant": 0
      }
    }
  }
}
```

### C. Spec PDF (`generate_pdf.py`)

Add a "Build Conventions Applied" page immediately after the cover page.
Render as a single-page summary table mirroring the Section 0 text above,
with check-marks for applied conventions and counts for coverage.

### D. Spec XLSX (`generate_xlsx.py`)

Either:
- **Option 1 (preferred):** Add a new sheet `CONVENTIONS` immediately after
  the `INDEX` sheet, listing each convention with: rule name, status (applied
  / partial / overridden), counts, and exemptions list.
- **Option 2:** Add a "Build Conventions" section block at the top of the
  existing `INDEX` sheet, above the form inventory.

Use the same colour coding as the rest of the workbook (green = applied,
amber = partial / has exemptions, red = override active).

### Why this matters

Without this surfacing, the human reviewer can't tell whether a form's
behaviour came from the protocol, the customer library, or a skill default.
The `conventions_applied` block makes every default explicit, auditable,
and easy to override deliberately.

---

## Integration Notes

This file is referenced from the `protocol-to-edc-structure` skill's
"Before You Begin — Read Reference Files" section. It is loaded once per
protocol run and applied throughout Steps 1–8 of the skill.

The `edc-builder` skill consumes the JSON output of `protocol-to-edc-structure`
and does not need to re-apply these conventions — they are baked into the
spec by the time the builder runs.

If a future customer library or build experience reveals additional default
conventions, append them as new numbered sections in this file and update
the `version` field in `conventions_applied`.
