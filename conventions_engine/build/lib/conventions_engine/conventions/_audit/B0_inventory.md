# Conventions Inventory — Phase B.0 Audit

**Generated:** 2026-05-14
**Status:** Read-only catalog. No translation. Inputs to Phase B.1+.
**Source:** /tmp/B0_system{1,2,3_writer,4}.txt — see commit message for reproduction commands.

## Summary

| System | Source                                                                      | Rows |
|--------|-----------------------------------------------------------------------------|------|
| 1      | OC-N rules in `prompts.py`                                                  | 11   |
| 2      | §-numbered sections in `skills/protocol-analysis/references/conventions.md` | 29   |
| 3      | Trainer rulebook `/data/rulebook/conventions.json` (writer + runtime)       | 2 (writer-shape only; runtime file absent) |
| 4      | `migration/vendor_conventions/*.md`                                         | 73 (≈36 vendor-specific after dedup; remainder reaffirm globals) |
| **Total** |                                                                          | **115** |

## Findings (read first)

### F1. The trainer rulebook is a write-only sink
`grep` across the repo finds zero readers of `/data/rulebook/conventions.json`.
SSH into the production trainer container on 2026-05-14 confirms the file
has never been written (`cat: No such file or directory`). The entire
`convention_worker.py` pipeline has produced nothing that has ever been
consumed by anything. This validates the architecture doc's premise that
"the human classification work being done today literally has no effect
on future builds." (System 3 detail below.)

### F2. The vendor cascade axis is parallel to, not part of, the OC-tenant cascade
**Resolved 2026-05-14.** See `conventions/_audit/F2_resolution.md`.
The decision: vendor becomes a peer-axis to customer in the cascade,
with customer-wins tie-breaking and every conflict logged. Vendor
identifier comes from monday column `dropdown_mm382w7d`. The ~36
truly vendor-specific rows from System 4 translate into
`conventions/vendors/<slug>/*.json` during Phase B.1b; reaffirmations
are dropped (cascade resolves them from `global/`). Migration
`vendor_conventions/*.md` files stay during transition and get
deleted in Phase C.

### F3. System 3 + System 4 sources do not match `convention.schema.json`
The trainer writer emits `{id: "CONV-NNNN", layer, source_study, rule,
rationale, created}` — missing 8 required schema fields (title, kind,
status, natural_key, description, target, created_by, source) and using
an id format that violates the dotted-snake-case `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`
pattern. Vendor files are markdown prose, no schema-conformant structure
at all. Both require a translation pass before flowing through the engine.

### F4. OC-7 umbrella likely subsumes parts of §20–§28
OC-7 (`universal_clinical_data_patterns`, row 1.9) is an umbrella for
sub-rules 7A–7P. Several §20–§28 entries look like specific 7-letter
sub-rules expressed standalone — candidates listed in Cross-refs.
Unresolvable until OC-7 is decomposed in Phase B.1+.

### F5. §28 needs a DSL extension OR per-type decomposition
`decimal_precision_constraint` (row 2.28) carries an embedded lookup
table (`HEIGHT=2, WEIGHT=2, TEMP=1, BP=0`). The current DSL has no
inline-table operator. Two paths: extend the DSL with a `match` operator,
or split into per-measurement-type structured conventions.

### F6. Phase A rename note
The conventions_engine emits `study_meta.conventions_engine_applied`
to avoid collision with the legacy `conventions_applied` field written
by `compute_conventions.py`. Unification is Phase C work per the
architecture doc.

## System 1 — OC-N rules in `prompts.py`

| #    | location | rule_text | proposed_kind | proposed_natural_key | proposed_target | scope | notes |
|------|----------|-----------|---------------|----------------------|-----------------|-------|-------|
| 1.1  | prompts.py:116 | Expressions in `relevant`, `constraint`, `calculation`, and `default` must use functions from the OpenClinica Validated Functions Index (OC4 docs §2.4.6.1); avoid non-validated XPath functions (e.g. fn:… namespace, custom Enketo-only functions). | hybrid | validated_xpath_functions_only | field | global | OC-1. Allowlist enforceable with a function-name regex; full expression-grammar check is judgment-y. Common safe functions listed in the prompt (today, now, selected, count-selected, string-length, regex, coalesce, substr, date, decimal-date-time, format-date, int, number, round, if, once, position, concat, upper-case, lower-case). |
| 1.2  | prompts.py:130 | Every survey row whose `type` is a data type (text, integer, decimal, date, time, dateTime, select_one, select_multiple, note, calculate) MUST have `bind__oc_itemgroup` populated with a short group code (letters/digits/underscores only, must not start with a digit, MUST NOT contain a period); begin/end group and begin/end repeat rows do NOT need the field. | structured | itemgroup_mandatory_on_data_rows | field | global | OC-2. Itemgroup format constraint is mechanical. Cross-references OC-5a EXCEPTION: calculate rows with bind::oc:external='clinicaldata' MUST NOT have an itemgroup. Note rows also forbid it (per OC-5a). Invalid itemgroups cause silent OC rejection. |
| 1.3  | prompts.py:147 | The settings sheet must populate six cells: form_title (human-readable name), form_id (starts with F_), version (integer, start at 1), style ('theme-grid'), crossform_references (blank, comma-separated event OIDs, or 'current_event'), and namespaces (exactly oc="http://openclinica.org/xforms" , OpenClinica="http://openclinica.com/odm"). | structured | settings_sheet_required_fields | form | global | OC-3. namespaces value is a literal string with exact whitespace/separator. style is always "theme-grid". Form-level rule (one per form). |
| 1.4  | prompts.py:159 | Cross-form / cross-event field references use four exact XPath shapes (same-event-same-form, different-event-by-OID, current-event-OID lookup, and pulldata from `<study_id>_tpt`) plus `bind::oc:external='clinicaldata'` (or the CSV name for pulldata); referenced event OIDs go into `settings.crossform_references` (comma-separated). | structured | cross_form_xpath_patterns | field | global | OC-4. Four canonical patterns spelled out as full XPath strings in the prompt; pipeline validation would check that a field's calculation matches one of those shapes when it crosses forms. |
| 1.5  | prompts.py:174 | Forms with repeating groups (AE, CM, MH, DV, PR, and any custom repeating form) must include a `calculate` field at the top of the repeating group whose calculation uses `once(...@ItemGroupRepeatKey)` to prevent the repeat key from being overwritten on edit; pair with a separate display-only field using `if(${ID}!='', ${ID}, 'Scheduled')`. | structured | repeating_group_repeat_key_anchor | form | global | OC-5. Two-row pattern: (a) once()-anchored calculate, (b) display-only text field showing repeat number. Form-level (decides whether a form has the repeat trailer) but the rows themselves are field-level emissions. |
| 1.6  | prompts.py:184 | Element-type column matrix\: data types accept all columns; local `calculate` rows forbid readonly/constraint/required; external-lookup `calculate` rows (bind::oc:external='clinicaldata') additionally forbid bind::oc:itemgroup and (usually) label; `note` rows forbid bind::oc:itemgroup/required/constraint; begin/end group + begin/end repeat rows accept only type/name/appearance/relevant/bind::oc:itemgroup. | structured | element_type_column_matrix | field | global | OC-5a. Sub-rule of OC-5. Six element-type categories with explicit allowed/forbidden column sets. OC silently hides fields that violate this and emits diagnostic errors quoted in the prompt ("cannot be defined as type = calculate and readonly..." etc.). Most precise rule in the file — directly maps to validation logic. |
| 1.7  | prompts.py:236 | Computed values that must be visible to data entry use `type: text` + `calculation` + `readonly: yes` (NOT `type: calculate`, which OC hides from the data-entry view); use `type: calculate` ONLY for values consumed by other expressions (relevant/calculation/constraint). | structured | visible_calculated_uses_text_type | field | global | OC-5b. Sub-rule of OC-5/OC-5a. Mechanical: detect any type=calculate row whose name is referenced by a label-bearing display or which is meant to be visible to the user, and convert. |
| 1.8  | prompts.py:259 | By default constraints are soft (warning-only); to make a constraint hard-rejecting add `bind::oc:constraint-type='hard'` (JSON\: `bind__oc_constraint_type`); same for hard-required fields via `bind::oc:required-type='hard'` (JSON\: `bind__oc_required_type`). | advisory | hard_edit_checks_opt_in | field | global | OC-6. Documents the opt-in mechanism for hard validation. No automatic enforcement implied; convention would surface as an advisory hint or as a structured rule that flags rows where required=yes but no required-type set. (kind revised from structured during B.0 review — see Findings F4-adjacent note below) |
| 1.9  | prompts.py:266 | Apply universal clinical data patterns whenever the form contains the relevant fields\: paired *STDAT/*ENDAT cross-date constraints, future-date blocks on start dates, *ENDAT relevance on *ONGO status, sequential dates cascade, BMI calc, AE severity/serious logic, eligibility fixed-value constraints, physiological range sanity checks, RACE multi-select exclusion, optional dose/duration calcs, cross-form value fetch (CF), sex-dependent fields via SEX_CF, and other patterns 7A-7P. | hybrid | universal_clinical_data_patterns | field | global | OC-7. Umbrella rule containing sub-rules 7A through 7P. Each sub-rule is independently structured-or-hybrid; will almost certainly be decomposed into one convention per sub-letter in Phase B/C. Duplicated in skills/edc-builder/references/xlsform-build-rules.md and skills/protocol-analysis/references/xlsform-patterns.md. |
| 1.10 | prompts.py:475 | Repeating forms use OC's non-standard XLSForm structure\: data fields wrapped in a `begin group / end group` block (relevant-gated on the YN first-entry flag), followed by a trailer of `begin repeat <form_id>` + phantom `end group` (no matching begin_group) + `end repeat`; `end group` and `end repeat` rows MUST have a BLANK `name`; do NOT include a top-level SUBJID row on repeating forms; the first-entry YN gate uses `relevant: ${REPKEY_ID}=1`. | structured | repeating_form_structural_pattern | form | global | OC-8. Multi-part rule: trailer shape, blank-name requirement, no-SUBJID-on-repeating, YN-gate convention. Currently enforced in build_preview/sanitize.py and skills/edc-builder/scripts/validate_form.py (the phantom end-group stripper). Without the phantom end-group the OC upload silently fails to activate. |
| 1.11 | prompts.py:527 | Every study MUST include a Common Visit event with OID `SE_COMMON` (repeating, non-scheduled, available after enrollment); the forms AE, CM, DV, and AESAE MUST live ONLY on SE_COMMON (visits_assigned=["SE_COMMON"], exactly that and nothing else); skip SE_COMMON entirely only if none of those four forms are in scope. | structured | common_visit_safety_admin_placement | form | global | OC-9. Two-part: (a) ensure event SE_COMMON exists when AE/CM/DV/AESAE in scope, (b) pin those forms to SE_COMMON. Already enforced post-build by pipeline.py:_enforce_common_visit. Asserted in tests/migration/test_migration.py. Most-cited rule in the audit. |

### System 1 review note (OC-6)
Row 1.8 (OC-6) was reclassified from `structured` to `advisory` during
B.0 review. The rule as written in `prompts.py` is informational — it
documents the OC opt-in mechanism for hard constraints
(`bind::oc:constraint-type='hard'`) rather than asserting a policy. A
separate structured policy convention (e.g. "default to soft; flag rows
where required=yes but no required-type set") could be proposed in
Phase B.1+ but would be a new convention, not a translation of OC-6.

## System 2 — §-numbered conventions in `conventions.md`

| #    | location | description | proposed_kind | proposed_natural_key | proposed_target | scope | instrumented_in_compute | compute_function | notes |
|------|----------|-------------|---------------|----------------------|-----------------|-------|-------------------------|------------------|-------|
| 2.0  | conventions.md:14 | Foundational rule: the protocol's data-item census decides WHICH fields must exist; a 3-level lookup hierarchy (customer_oc4_standard → customer_crf_library → cdash_default) decides HOW each field is encoded; items the census names but no level matches become tagged placeholders. | hybrid | protocol_census_lookup_hierarchy | study | global | yes | compute_and_apply | §0. Governs the scope qualifier on every downstream convention (most §3-§14 sections carry "Scope per §0: applies only to CDASH-default fields"). Compute exposes definition_source_distribution and protocol_inferred_placeholders. |
| 2.1  | conventions.md:241 | Every study build includes a standalone ICF (Informed Consent) form even when the customer library does not contain one and the protocol does not list ICF as a CRF. | structured | standalone_icf_form_default | form | global | yes | compute_and_apply (icf_form_added_by_default block) | §1. icf_present / icf_form / icf_fields / icf_source computed by compute_and_apply; structured violation flagged when the form is missing. |
| 2.2  | conventions.md:305 | Every survey row with `type: date` must include `constraint: . <= today()` and `constraint_message: "Future dates are not allowed."`. | structured | future_date_constraint_on_dates | field | global | yes | _compute_legacy (fdc_const/fdc_exempt) | §2. Compute_and_apply emits future_date_constraint_applied with constrained/exempted counts. Exemptions list left as []. |
| 2.3  | conventions.md:323 | Every form's survey content must be wrapped in a `begin group` / `end group` pair, including forms with no repeating objects and no semantic sections; default group name `group0`, no label. | structured | form_begin_end_group_wrapper | form | global | yes | _compute_legacy (grp_wrapped) | §3. Scope-tagged: applies only to CDASH-default fields (level 3). Compute emits group_wrapping_applied with forms_wrapped count and the single_section_group_name="group0". |
| 2.4  | conventions.md:340 | Use CDASH-aligned field names by default; reference references/cdash-domain-library.md for the standard name per domain. | structured | cdash_field_naming | field | global | partial | _compute_legacy (cdash_using/cdash_dev) | §4. Scope-tagged: applies only to CDASH-default fields. Compute counts fields_using_cdash and name_deviations but the deviations_list is left []; full naming-deviation detection appears to be future work. |
| 2.5  | conventions.md:368 | All `list_name` values in the choices sheet must be uppercase short codes. | structured | uppercase_choice_list_names | choice | global | yes | _compute_legacy (upper_ok) | §5. Scope-tagged: applies only to CDASH-default fields. Compute emits uppercase_choice_lists.applied as a per-form pass/fail. |
| 2.6  | conventions.md:384 | When `required: yes` is set on a survey row, populate `required_message` with a brief instruction telling the end user what to enter. | structured | required_message_per_required_field | field | global | yes | _compute_legacy (rm_required/rm_with) | §6. Compute emits required_message_coverage with required_fields and fields_with_message counts; no message-quality check. |
| 2.7  | conventions.md:411 | Forms reported reactively rather than at scheduled visits belong in a Common event (SE_COMMON, repeating, non-scheduled) — typically AE, CM, DV, AESAE. | structured | common_visit_safety_admin_placement | form | global | yes | compute_and_apply (common_event_applied block) | §7. SAME natural_key as System 1 OC-9 — clear cross-system duplicate. Compute lists forms_in_common_event and supports conditional_forms_added/skipped (e.g., DD for device studies, CM omission for Agilis). |
| 2.8  | conventions.md:460 | All checks default to soft (warning-only); strict (hard-stop) is opt-in only when data quality justifies the cost. | structured | hard_edit_checks_opt_in | field | global | yes | compute_and_apply (soft_edit_checks_applied block) | §8. SAME natural_key as System 1 OC-6 — clear cross-system duplicate. §8 frames the default as soft and warns against strict; OC-6 documents the opt-in mechanism. Compute counts strict_required_count and strict_constraint_count. |
| 2.9  | conventions.md:484 | Use `PDate` for recall-based dates (accepts DD-MMM-YYYY, MMM-YYYY, or YYYY for partial dates) and `Date` for definite events (full dates only). | hybrid | pdate_for_recall_dates | field | global | yes | compute_and_apply (pdate_for_recall_dates block) | §9. Compute counts pdate_fields / date_fields and tracks rule_flagged_crossform_uses. Deciding "recall vs definite" per field requires Claude judgment on the field semantic — hybrid. |
| 2.10 | conventions.md:517 | Apply `appearance: minimal autocomplete` to long pick-lists; thresholds differ by form purpose (Site vs Participate). | structured | autocomplete_for_long_lists | field | global | yes | compute_and_apply (autocomplete_appearance block) | §10. Compute tracks participate_lists_eligible/with_minimal and site_lists_eligible/with_minimal. Two thresholds keyed off form purpose. |
| 2.11 | conventions.md:542 | Choice lists exceeding OC's 3,500-char (labels + names combined) threshold must be externalized to a CSV referenced via `bind::oc:external` — otherwise the form fails to upload. | structured | external_csv_long_choice_lists | choice | global | yes | compute_and_apply (external_csv_for_long_lists block) | §11. Compute counts lists_exceeded_threshold and external_csvs_created. Hard OC limit (4,000-char) cited; rule uses 3,500 as the trigger. |
| 2.12 | conventions.md:567 | Item-count caps: Site forms warn at 200+ items, Participate forms warn at 50+ items. | advisory | form_item_count_caps | form | global | yes | compute_and_apply (item_count_caps block) | §12. Build-time WARNINGS only — no automatic remediation. Compute emits site_forms_over_200 and participate_forms_over_50. |
| 2.13 | conventions.md:591 | Every survey row gets a `bind::oc:briefdescription` value auto-filled when the row's source layer did not provide one. | structured | briefdescription_on_every_data_row | field | global | yes | compute_and_apply (briefdescription_coverage block) | §13. Scope-tagged: applies UNIVERSALLY (not just CDASH-default). Customer-authored briefdescription wins; auto-fill targets empty cells. Compute reports applied_count/total/missing_count/missing_list[:20]. |
| 2.14 | conventions.md:619 | Each form's `style` cell on the settings sheet is set explicitly per form purpose (Site simple-single / Site simple-pages / Site theme-grid / Participate simple-pages). | structured | form_style_per_purpose | form | global | yes | compute_and_apply (form_style_explicit block) | §14. Scope-tagged: CDASH-default only. Compute counts per-style buckets (fse_simple, fse_pages, fse_grid, fse_partic, fse_missing). |
| 2.15 | conventions.md:644 | Auto-populate `settings.crossform_references` with the event/form OIDs referenced by cross-form calcs, so OC loads only required participant data at form display time. | structured | crossform_references_settings_populated | form | global | yes | compute_and_apply (crossform_references_populated block) | §15. Related to System 1 OC-3 (settings sheet REQUIRED fields) and OC-4 (cross-form XPath patterns) but distinct: this section is specifically about auto-populating that one settings cell. Compute counts forms_with_cross_form_calc vs forms_with_crossform_references. |
| 2.16 | conventions.md:669 | Items belonging to the same repeating logical record (e.g., AE start/end dates + severity for one event row) share a single `bind::oc:itemgroup` value, independent of the `begin/end group` wrapping. | structured | itemgroup_keep_together_for_repeating_records | field | global | yes | compute_and_apply (itemgroup_keep_together block) | §16. Cross-reference to System 1 OC-2 (itemgroup mandatory on data rows). OC-2 says itemgroup MUST exist; §16 says rows in a repeating record share the SAME itemgroup value. Compute counts repeating_logical_records, repeating_records_consistent, deviations. |
| 2.17 | conventions.md:698 | Use `appearance: likert` only when the field has ≤5 choice values AND the choice labels are short. | structured | likert_appearance_short_lists | field | global | yes | compute_and_apply (likert_appearance_rule block) | §17. Compute tracks likert_fields total + likert_compliant + likert_non_compliant. |
| 2.18 | conventions.md:726 | VAS (Visual Analog Scale) fields use vertical appearance instead of horizontal. | structured | vas_appearance_vertical | field | global | yes | compute_and_apply (vas_appearance_rule block) | §18. Compute counts vas_fields and vas_vertical. |
| 2.19 | conventions.md:748 | Use `appearance: table-list` (or equivalent table appearance) only when the choice labels are short. | structured | table_appearance_short_labels | field | global | yes | compute_and_apply (table_appearance_rule block) | §19. Compute tracks table_fields and table_compliant. Mirrors §17 and §18 — three Participate-form appearance rules. |
| 2.20 | conventions.md:769 | Forms that anchor a clinical visit may include a final group whose purpose is operator reminder — Y/N flags asking whether AE/DV/discontinuation events occurred during the visit. |  | forms_completion_safety_net | form | global | yes | _apply_pattern_conventions (forms_completion_safety_net) | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. (Compute does emit metrics via _apply_pattern_conventions but the section is not yet expressed as a proper convention record.) |
| 2.21 | conventions.md:800 | Forms typically open with an unlabeled wrapper group (`group0` per IE-1a) containing date and identification fields before the body of the form. |  | header_group_pattern | form | global | yes | _apply_pattern_conventions (header_group_pattern) | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. Detection: first begin_group has name="group0" and empty label. |
| 2.22 | conventions.md:827 | A `note` row immediately following a `select_one YN` field, with label containing "If yes" or similar conditional language, must gate itself with `relevant: ${YN_FIELD}='Y'` (or equivalent). |  | reminder_notes_yn_gated | field | global | yes | _apply_pattern_conventions (reminder_notes_gated) | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. Detects YN→note adjacency + relevant expression. |
| 2.23 | conventions.md:867 | When a level-2 (Case Book) source provides a label that is meaningful only adjacent to its parent question (e.g., "If yes"), rewrite the label to include enough context to stand alone in OC's flat XLSForm structure (e.g., "If yes, when did it occur?"). |  | source_label_disambiguation | field | global | yes | _apply_pattern_conventions (source_label_disambiguation) | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. Detection: relevant-gated row whose label starts with "if yes," (with content after the comma). |
| 2.24 | conventions.md:916 | When a level-2 source renders a question ambiguously (e.g., `o` symbols that could be either radio buttons or checkboxes), apply clinical reasoning to pick the most likely interpretation AND auto-flag the choice in review_flags. |  | source_ambiguity_clinical_reasoning | field | global | partial | _apply_pattern_conventions (source_ambiguity_resolved) | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. Compute emits a placeholder applied_count=0 — the actual count is supposed to come from review_flags.choice_list_review entries written by upstream form regeneration. |
| 2.25 | conventions.md:960 | Eligibility-verdict (and other all-conditions-met) calculations use a 3-state pattern: 'Eligible' / 'Ineligible' / 'Not yet calculated' inside the calculation expression. |  | eligibility_verdict_3state | field | global | yes | _apply_pattern_conventions (eligibility_verdict_3state) | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. Detection: calculation string contains all three quoted literals. |
| 2.26 | conventions.md:1011 | Numeric measurement fields paired with unit selectors (height + height units, weight + weight units, etc.) use `appearance: w2` on both so they render side-by-side. |  | value_unit_pair_side_by_side_layout | field | global | yes | _apply_pattern_conventions (value_unit_pair_layout) | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. Detection: decimal/integer field immediately followed by a recognised unit field; both must carry "w2" appearance. |
| 2.27 | conventions.md:1056 | Multi-select fields whose choice list contains a sentinel ("DECLINED", "UNKNOWN", "NONE", "N/A", "REFUSED") get an exclusivity constraint: when the sentinel is selected, nothing else may be selected. |  | sentinel_exclusivity_constraint | field | global | yes | _apply_pattern_conventions (sentinel_exclusivity) | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. Detection: select_multiple field with sentinel in choices + constraint containing selected(., 'SENTINEL') and not(selected …). |
| 2.28 | conventions.md:1107 | Decimal fields capturing physical measurements (height, weight, temperature, BP, etc.) carry an auto-generated precision constraint of the form `round(${FIELD}, N) = .` where N is the per-measurement-type precision (HEIGHT/WEIGHT=2, TEMP=1, BP=0). |  | decimal_precision_constraint | field | global | yes | _apply_pattern_conventions (decimal_precision_constraint) + PRECISION_TABLE | §20–§28 uninstrumented; re-scope per arch doc §11 Phase B. Per-name precision table built into compute_conventions.py (HEIGHT/HT=2, WEIGHT/WT=2, TEMP=1, BP/SBP/DBP=0). |

## System 3 — Trainer rulebook (write-only sink)

### 3A. Read-site check
- `grep -r "/data/rulebook/conventions.json" ~/oc-ai-pipeline` → 2 hits,
  both inside `services/study-build-trainer/workers/convention_worker.py`
  (the writer itself).
- `grep -r "rulebook/conventions" ~/oc-ai-pipeline` → same 2 hits.
- **Conclusion:** zero read sites repo-wide. Write-only sink.

### 3B. Runtime file inspection
- 2026-05-14, production trainer container `65a0dc84e590`:
  `cat /data/rulebook/conventions.json` → `No such file or directory`.
- **Conclusion:** file has never been written. The writer pipeline has
  produced no content to date.

### 3C. Writer-shape rows

| #    | location | shape_summary | proposed_kind | proposed_natural_key | proposed_target | scope | notes |
|------|----------|---------------|---------------|----------------------|-----------------|-------|-------|
| 3W.1 | services/study-build-trainer/workers/convention_worker.py:211 + :220 | Root container created by load_conventions() on first run and rewritten in full by save_conventions() on every update\: {version, last_updated, global\: [entry, …], customer_specific\: {<customer_uuid>\: [entry, …], …}} — written as a full-file replacement, not a per-record append. |  |  |  | global | File-level container schema. Two distinct scope buckets (global vs customer_specific keyed by customer_uuid). No "study" scope. No status field anywhere. No natural_key field anywhere. No JSON-Schema validation against conventions/schema/convention.schema.json. version="1.0" is a separate version-string from conventions/schema/version.txt=1. |
| 3W.2 | services/study-build-trainer/workers/convention_worker.py:235-249 (entry built at 235, appended to global at 244, or to customer_specific[uuid] at 249) | Per-record entry appended by apply_conventions()\: {id (e.g. "CONV-0042"), layer (Study\|Events\|Form Placement\|Forms\|Items\|Choices\|Logic), source_study (protocol #), rule (Claude-authored sentence), rationale (one-sentence reason), created (ISO date)}. | structured |  |  | global | Auto-promotes — written directly to the live `global` / `customer_specific` lists with NO proposed→active gate. NO conflict check (natural-key or otherwise) before append. Six conventions/schema/convention.schema.json required fields are MISSING in this shape: title, kind, status, natural_key, description, target, created_by, source. id format ("CONV-NNNN") VIOLATES the engine schema's dotted-snake-case pattern (^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$). scope values "global"/"customer" don't include "study" (engine has all three). customer-bucket key is customer_uuid (Monday item id?) while the engine cascade keys customers by subdomain string. layer enum (Study/Events/Form Placement/Forms/Items/Choices/Logic) does NOT map to the engine's target enum (study/form/field/event/choice). proposed_natural_key/proposed_target left blank because the topic and target are content-driven per Claude extraction, not declared by the writer. |

## System 4 — Vendor conventions (`migration/vendor_conventions/`)

### 4A. Encoding scheme

| filename            | encoding_scheme | customer_identifier |
|---------------------|-----------------|---------------------|
| castor.md           | filename        | castor              |
| generic_odm.md      | filename        | generic_odm  (fallback when no vendor match) |
| imednet.md          | filename        | imednet             |
| medidata_rave.md    | filename        | medidata_rave       |
| medrio.md           | filename        | medrio              |
| oracle_inform.md    | filename        | oracle_inform       |
| redcap.md           | filename        | redcap              |
| veeva.md            | filename        | veeva               |
| viedoc.md           | filename        | viedoc              |
| zelta.md            | filename        | zelta               |

Encoding-scheme observations:
- Primary encoding: (a) filename. The filename slug is the cache key used
  by `VENDOR_CONVENTION_FILES` in migration/odm_to_spec.py.
- Secondary encoding also present in every file: (c) header_line — every
  file begins `# <Display Name>` (e.g. `# Castor EDC`, `# Medidata Rave`,
  `# REDCap`) which is the human-readable vendor name. Display name and
  code-side `source_system` value (e.g. "Castor EDC", "Medidata Rave")
  are spelled out in each file's "## Detection" section.
- No file has yaml frontmatter (b).
- No file has per-rule customer annotation (d) — every rule in a given
  file applies to that one vendor; scope is whole-file.
- generic_odm.md is the explicit fallback for unrecognised vendors. Its
  directives are effectively "default ODM behaviour", not a customer-
  specific override; in cascade terms it would be a vendor-layer default,
  not a vendor-specific entry.

### 4B. Row catalog

| #    | location | customer | scope_id | rule_text | proposed_kind | proposed_natural_key | proposed_target | notes |
|------|----------|----------|----------|-----------|---------------|----------------------|-----------------|-------|
| 4.1  | migration/vendor_conventions/generic_odm.md:42-46 | generic_odm | generic_odm | Apply generic OID normalisers: events `_oc_event_oid` ensure `SE_` prefix uppercased; forms `_oc_form_id` strip `F_`/`CRF_`/`FORM_` prefix, uppercase, CDASH-map, cap at 20 chars at underscore boundary; items `_oc_item_name` uppercase + prefer CDASH alias; item groups `_oc_itemgroup` strip `IG_`/form-prefix and uppercase. | structured | oid_normalisation_oc4 | field | [VENDOR-DEFAULT] vendor-layer default; customer-specific addition (no System 1+2 equivalent). |
| 4.2  | migration/vendor_conventions/generic_odm.md:50-51 | generic_odm | generic_odm | Repeating detection\: `FormDef/@Repeating="Yes"` OR `ItemGroupDef/@Repeating="Yes"` triggers the OC-8 repeating wrapper. | structured | repeating_form_structural_pattern | form | [VENDOR-DEFAULT] vendor-layer reaffirmation of OC-8 (1.10 / OC-8). |
| 4.3  | migration/vendor_conventions/generic_odm.md:52-54 | generic_odm | generic_odm | Multi-group forms emit one `begin group` / `end group` pair per ItemGroup; dedupe field names within each group to avoid pyxform unique-name collisions. | structured | per_group_field_name_dedup | field | [VENDOR-DEFAULT] customer-specific addition (no System 1+2 equivalent); guards against pyxform collisions. |
| 4.4  | migration/vendor_conventions/generic_odm.md:58-60 | generic_odm | generic_odm | One `StudyEventDef` → one `SE_<NAME>` event in `timepoint_csv`; `SE_COMMON` is synthesised when absent; AE/CM/DV/AESAE pin to `SE_COMMON` per OC-9. | structured | common_visit_safety_admin_placement | event | [VENDOR-DEFAULT] vendor-layer reaffirmation of OC-9 (1.11). The `SE_COMMON` synthesise-if-absent fragment is an addition. |
| 4.5  | migration/vendor_conventions/generic_odm.md:64-67 | generic_odm | generic_odm | Codelists consumed as-is from `CodeList`/`CodeListItem`; preferred decode is `Decode/TranslatedText[@xml:lang="en"]`, fall back to first translation when English absent; sanitise codelist OIDs for XLSForm via `_safe_list_name`. | structured | codelist_decode_english_preferred | choice | [VENDOR-DEFAULT] customer-specific addition. |
| 4.6  | migration/vendor_conventions/generic_odm.md:87-88 | generic_odm | generic_odm | Apply OC-1 through OC-9 unconditionally; they take precedence over any rule below. | hybrid | vendor_rules_subordinate_to_oc_standards | study | [VENDOR-DEFAULT] customer-specific addition expressing precedence order; meta-rule, not a content rule. |
| 4.7  | migration/vendor_conventions/generic_odm.md:89-94 | generic_odm | generic_odm | Apply the deterministic transform in `odm_to_spec.transform`\: CDASH domain mapping by form name / OID fragment; OC-9 pinning for AE/CM/DV/AESAE; OC-8 repeating wrapper for repeating forms; SUBJID injection on forms that lack it; per-group field-name dedup. | structured | deterministic_transform_pipeline | study | [VENDOR-DEFAULT] meta-rule; bundles common_visit_safety_admin_placement + repeating_form_structural_pattern + cdash_field_naming + a new subjid_injection rule. |
| 4.8  | migration/vendor_conventions/generic_odm.md:95-96 | generic_odm | generic_odm | Capture any unrecognised vendor namespace attributes in `vendor_specific` rather than failing the parse. | structured | unknown_namespace_capture_in_vendor_specific | study | [VENDOR-DEFAULT] customer-specific addition; vendor-layer default. |
| 4.9  | migration/vendor_conventions/generic_odm.md:97-100 | generic_odm | generic_odm | During AI enrichment, lean on the protocol PDF for constraint/relevant expressions and study-metadata gaps; do NOT let the model invent structural elements (events, forms, items) that the ODM does not contain. | advisory | enrichment_grounded_in_protocol_no_invent_structure | study | [VENDOR-DEFAULT] customer-specific addition; constrains AI enrichment behaviour. |
| 4.10 | migration/vendor_conventions/generic_odm.md:104-106 | generic_odm | generic_odm | Compliance posture unknown for generic ODM; do not assume Part 11 / GDPR / ICH conformance — surface review-flag entries asking the migration owner to confirm. | advisory | compliance_posture_unknown_review_required | study | [VENDOR-DEFAULT] customer-specific addition; advisory. |
| 4.11 | migration/vendor_conventions/castor.md:36-46 | castor | castor | UUID OIDs (e.g. `15E88A04-9CB8-…`) must not propagate into OC4 OIDs\: strip hyphens, prefer `Name` attribute when present (CDASH-map where possible), fallback to de-hyphenated UUID truncated to 20 chars and prefixed with entity code (`F_`, `IG_`, `I_`, `SE_`); preserve original UUID in `vendor_specific`. | structured | vendor_uuid_oid_normalisation | field | customer-specific addition (Castor-only); preserves UUID for round-trip. |
| 4.12 | migration/vendor_conventions/castor.md:50-53 | castor | castor | Castor "Repeating Data" sets → repeating ItemGroup; apply OC-8 repeating wrapper exactly as for log-line forms. | structured | repeating_form_structural_pattern | form | customer-specific override of repeating_form_structural_pattern (1.10 / OC-8) — extends the trigger to Castor "Repeating Data" rather than `FormDef/@Repeating`. |
| 4.13 | migration/vendor_conventions/castor.md:61-64 | castor | castor | Castor phase → `SE_<PHASE_NAME>` after normalisation; Castor visits within a phase map to event repeats or extra events depending on phase repetition; AE/CM/DV pin to `SE_COMMON`. | structured | common_visit_safety_admin_placement | event | mixes a new key (vendor_phase_to_event) with reaffirmation of common_visit_safety_admin_placement (1.11 / OC-9). |
| 4.14 | migration/vendor_conventions/castor.md:69-71 | castor | castor | Castor "option groups" reused across multiple fields share a single `CodeListOID`; preserve referential reuse during transform. | structured | shared_codelist_oid_referential_reuse | choice | customer-specific addition. |
| 4.15 | migration/vendor_conventions/castor.md:96-100 | castor | castor | UUID OIDs → use `Name` as primary identifier; fall back to sanitised UUID (de-hyphenated, truncated, prefixed); preserve original UUID in `vendor_specific.castor_oid` per element. | structured | vendor_uuid_oid_normalisation | field | duplicates 4.11 (OID Conventions vs OC4 Transform Rules sections cover the same rule). One of these will collapse in a consolidated catalog. |
| 4.16 | migration/vendor_conventions/castor.md:101-102 | castor | castor | Castor "Repeating Data" → OC-8 repeating ItemGroup with `begin repeat <FORM_ID>_LOG`. | structured | repeating_form_structural_pattern | form | duplicates 4.12. |
| 4.17 | migration/vendor_conventions/castor.md:103-105 | castor | castor | Missing measurement units → numeric items default to `type: text` with a review flag (`choice_list_review` or `protocol_ambiguous` as appropriate). | hybrid | numeric_with_missing_unit_defaults_to_text | field | customer-specific addition; review-flag driven. |
| 4.18 | migration/vendor_conventions/castor.md:106-107 | castor | castor | Missing range checks → constraints driven entirely by the protocol PDF during AI enrichment. | advisory | missing_range_checks_via_protocol_enrichment | field | customer-specific addition; advisory. |
| 4.19 | migration/vendor_conventions/medidata_rave.md:50-58 | medidata_rave | medidata_rave | Log-line repeating forms (AE, CM, MH, DV, EX, PC, etc.) carry `mdsol:IsLog="Yes"` on the `ItemGroupDef` and `Repeating="Yes"` on the `FormDef`. Both must be detected — log-line semantics drive the OC-8 repeating structure with `begin repeat <FORM_ID>_LOG` + `end repeat` at the end of the form's survey rows. | structured | repeating_form_structural_pattern | form | customer-specific override of repeating_form_structural_pattern (1.10 / OC-8) — extends the trigger to mdsol\:IsLog. |
| 4.20 | migration/vendor_conventions/medidata_rave.md:61-66 | medidata_rave | medidata_rave | `StudyEventDef OID="SE_<NAME>"` maps directly to OC4 events; AE/CM/DV/AESAE forms pin to `SE_COMMON` per OC-9 regardless of the `FormRef` list in source events; Rave's "Common Events" pseudo-event is folded into `SE_COMMON`. | structured | common_visit_safety_admin_placement | event | customer-specific override of common_visit_safety_admin_placement (1.11 / OC-9); adds Common-Events folding. |
| 4.21 | migration/vendor_conventions/medidata_rave.md:71-74 | medidata_rave | medidata_rave | `mdsol:Active="No"` on `CodeListItem` marks a retired choice — current transform keeps it; flag for review if the retired value appears in `ClinicalData`. | advisory | retired_codelist_item_kept_review_if_used | choice | customer-specific addition; advisory review. |
| 4.22 | migration/vendor_conventions/medidata_rave.md:98-100 | medidata_rave | medidata_rave | `mdsol:IsLog="Yes"` OR `FormDef/@Repeating="Yes"` → set `has_repeating_group=True`; apply OC-8 repeating structure with `begin repeat <FORM_ID>_LOG` / `end repeat` wrapper. | structured | repeating_form_structural_pattern | form | duplicates 4.19 (Form Structure Quirks vs OC4 Transform Rules sections). |
| 4.23 | migration/vendor_conventions/medidata_rave.md:101-102 | medidata_rave | medidata_rave | AE / CM / DV / AESAE `FormDef` → `visits_assigned = ["SE_COMMON"]` (OC-9), independent of the source event list. | structured | common_visit_safety_admin_placement | form | reaffirms common_visit_safety_admin_placement (1.11 / OC-9). Duplicates the form-pinning half of 4.20. |
| 4.24 | migration/vendor_conventions/medidata_rave.md:103 | medidata_rave | medidata_rave | `mdsol:DefaultMatrixOID` is informational — do not propagate to OC4. | structured | informational_attribute_not_propagated | study | customer-specific addition; suppression rule. |
| 4.25 | migration/vendor_conventions/medidata_rave.md:104-105 | medidata_rave | medidata_rave | Codelist OIDs `CL.<NAME>` are renamed via `_safe_list_name` for XLSForm; preserve the original in `vendor_specific` for traceability. | structured | safe_list_name_codelist_oid_preserve_original | choice | customer-specific addition (mirrors generic_odm 4.5's `_safe_list_name`). |
| 4.26 | migration/vendor_conventions/imednet.md:41-43 | imednet | imednet | iMedNet OIDs sometimes include a study-prefix segment (`SE_STUDY123_VISIT_1`) — the transform's `_oc_event_oid` collapses these via uppercasing and `SE_` normalisation. | structured | vendor_study_prefix_event_oid_collapse | event | customer-specific addition. |
| 4.27 | migration/vendor_conventions/imednet.md:45-47 | imednet | imednet | Log-line forms (AE, CM) use the standard `FormDef/@Repeating="Yes"` pattern; only the resolved base form (not iForm variants) appears in the export. | structured | repeating_form_structural_pattern | form | reaffirms repeating_form_structural_pattern (1.10 / OC-8). Note about iForm resolution is informational only. |
| 4.28 | migration/vendor_conventions/imednet.md:53-57 | imednet | imednet | Standard `StudyEventDef` → `SE_<NAME>`; iMedNet "unscheduled visits" → `SE_UNSCHEDULED` (synthesised if absent in source); AE/CM/DV pin to `SE_COMMON` per OC-9. | structured | common_visit_safety_admin_placement | event | mixes vendor_unscheduled_visit_synthesised (new) with reaffirmation of common_visit_safety_admin_placement. |
| 4.29 | migration/vendor_conventions/imednet.md:77-78 | imednet | imednet | Treat as generic ODM unless `imn:` namespace is detected — in which case preserve attributes in `vendor_specific`. | structured | unknown_namespace_capture_in_vendor_specific | study | reaffirms unknown_namespace_capture_in_vendor_specific (4.8); meta-rule "fall through to generic". |
| 4.30 | migration/vendor_conventions/imednet.md:79 | imednet | imednet | `FormDef/@Repeating="Yes"` drives OC-8 repeating wrapper. | structured | repeating_form_structural_pattern | form | reaffirms repeating_form_structural_pattern (1.10 / OC-8). |
| 4.31 | migration/vendor_conventions/imednet.md:80 | imednet | imednet | Standard CDASH naming; no special form-id mapping required. | structured | cdash_field_naming | field | reaffirms System 2 cdash_field_naming. |
| 4.32 | migration/vendor_conventions/medrio.md:38-44 | medrio | medrio | Medrio supports relational / repeating ItemGroups for log-line data (AE, CM, MH). Detection driven entirely by `ItemGroupDef/@Repeating="Yes"` and `FormDef/@Repeating="Yes"`. Apply standard OC-8 repeating wrapper (`begin repeat <FORM_ID>_LOG` / `end repeat`). | structured | repeating_form_structural_pattern | form | reaffirms repeating_form_structural_pattern (1.10 / OC-8). |
| 4.33 | migration/vendor_conventions/medrio.md:46-50 | medrio | medrio | Standard `StudyEventDef` → `SE_<NAME>`; unscheduled visits → `SE_UNSCHEDULED` (synthesised if absent); AE/CM/DV pin to `SE_COMMON` per OC-9. | structured | common_visit_safety_admin_placement | event | reaffirms common_visit_safety_admin_placement (1.11 / OC-9) + vendor_unscheduled_visit_synthesised. |
| 4.34 | migration/vendor_conventions/medrio.md:74-76 | medrio | medrio | Treat as generic ODM with the standard OC-1..OC-9 rules; `FormDef/@Repeating="Yes"` drives OC-8 repeating wrapper; standard CDASH naming — no special form-id mapping required. | structured | cdash_field_naming | field | meta-rule "fall through to generic" + reaffirms repeating_form_structural_pattern + cdash_field_naming. |
| 4.35 | migration/vendor_conventions/medrio.md:77-78 | medrio | medrio | If an unrecognised namespace appears, preserve in `vendor_specific` rather than failing the parse. | structured | unknown_namespace_capture_in_vendor_specific | study | reaffirms unknown_namespace_capture_in_vendor_specific (4.8). |
| 4.36 | migration/vendor_conventions/oracle_inform.md:36-47 | oracle_inform | oracle_inform | Hierarchical dot-notation OIDs (when `pf:HierarchicalOIDs="Yes"`)\: forms `frm<UPPER>` → strip `frm` prefix → form_id = `<UPPER>`; items `frm<X>.sct<Y>.itm<Z>` → form_id = `<X>`, item name = `<Z>` (drop middle section component). Non-hierarchical mode emits standard short OIDs — no special handling. | structured | vendor_hierarchical_oid_strip_section | field | customer-specific addition (Oracle InForm-only). |
| 4.37 | migration/vendor_conventions/oracle_inform.md:50-56 | oracle_inform | oracle_inform | InForm sections are presentation-only — they do NOT survive as separate ItemGroups after the OC4 transform; itemset templates (vital-signs grids) flatten into one ItemGroup per occurrence at export time. | structured | vendor_section_collapse_to_single_itemgroup | form | customer-specific addition. |
| 4.38 | migration/vendor_conventions/oracle_inform.md:69-70 | oracle_inform | oracle_inform | `pf:DBUID` on codelist items provides traceability back to the InForm database — preserve in `vendor_specific`, do NOT surface in the OC4 XLSForm choices. | structured | vendor_dbuid_preserve_not_surface | choice | customer-specific addition; suppression rule. |
| 4.39 | migration/vendor_conventions/oracle_inform.md:92-93 | oracle_inform | oracle_inform | Hierarchical OID `frm<X>.sct<Y>.itm<Z>` → strip middle section, `form_id = X`, `item_name = Z`. | structured | vendor_hierarchical_oid_strip_section | field | duplicates the item-OID half of 4.36. |
| 4.40 | migration/vendor_conventions/oracle_inform.md:94-95 | oracle_inform | oracle_inform | `pf:HierarchicalOIDs="Yes"` → run the InForm OID normaliser; otherwise treat as generic ODM. | structured | vendor_hierarchical_oids_toggle | study | customer-specific addition; gate for 4.36/4.39. |
| 4.41 | migration/vendor_conventions/oracle_inform.md:96-98 | oracle_inform | oracle_inform | Multiple sections collapsing to a single ItemGroup must dedupe item names within the resulting group (use the group-level `seen_names_in_group` mechanism in `odm_to_spec.transform`). | structured | per_group_field_name_dedup | field | reaffirms per_group_field_name_dedup (4.3); specialisation for InForm section collapse. |
| 4.42 | migration/vendor_conventions/redcap.md:30-39 | redcap | redcap | REDCap exports "instruments" as `FormDef`; longitudinal projects emit `StudyEventDef` per event with `Arm`-scoped schedules (arms = separate event sequences); classic (non-longitudinal) projects have no event structure — every form floats, the transform synthesises a single `SE_STUDY` event plus `SE_COMMON` for OC-9 forms. | structured | vendor_redcap_classic_vs_longitudinal_event_structure | study | customer-specific addition; foundational REDCap layout rule. |
| 4.43 | migration/vendor_conventions/redcap.md:41-49 | redcap | redcap | Form OID = REDCap instrument unique name (snake_case, lower-cased); item OID = REDCap variable name (snake_case, lower-cased); ItemGroup OID = REDCap instrument name (one ItemGroup per form unless section headers split it post-import); OC4 normalisation = uppercase to match CDASH; preserve original in `vendor_specific.redcap_variable` for traceability. | structured | vendor_redcap_oid_lowercase_then_uppercase | field | customer-specific addition. |
| 4.44 | migration/vendor_conventions/redcap.md:51-54 | redcap | redcap | REDCap "repeating instruments" (RIF/RIE) → OC-8 repeating ItemGroup with `begin repeat <FORM_ID>_LOG` wrapper; "repeating events" → OC4 repeating `StudyEventDef`. | structured | repeating_form_structural_pattern | form | customer-specific override of repeating_form_structural_pattern (1.10 / OC-8) — trigger is REDCap repeating-instrument flag, not `FormDef/@Repeating`. |
| 4.45 | migration/vendor_conventions/redcap.md:56-58 | redcap | redcap | `redcap:SectionHeader` is presentation-only — does NOT produce extra ItemGroups; surface as a `begin group` with the section text as label only when the protocol clearly relies on it. | advisory | vendor_redcap_section_header_presentation_only | form | customer-specific addition. |
| 4.46 | migration/vendor_conventions/redcap.md:61-70 | redcap | redcap | Longitudinal arm/event grid maps directly\: arm → OC4 arm, event under arm → `SE_<EVENT>`, visit number derived from event order within arm. Classic projects\: one synthetic `SE_STUDY` event holds non-OC-9 forms, `SE_COMMON` holds AE/CM/DV per OC-9. Long-form-only studies (no events): use `SE_STUDY` and emit a `parse_warnings` entry noting the synthesis. | structured | common_visit_safety_admin_placement | event | mixes vendor_redcap_classic_vs_longitudinal_event_structure with common_visit_safety_admin_placement (1.11 / OC-9). |
| 4.47 | migration/vendor_conventions/redcap.md:72-80 | redcap | redcap | REDCap choice strings encode raw value + label as `value, label` pairs in `Choices`; ODM export converts to standard `CodeList/CodeListItem` with `@CodedValue` and `Decode/TranslatedText`; branching logic is REDCap-specific syntax (`[field] = "1"`) — not consumed deterministically, AI enrichment can lift simple cases to XLSForm `relevant`. Assume `xml:lang="en"` since multi-language labels are uncommon in REDCap exports. | hybrid | vendor_redcap_branching_logic_via_ai_enrichment | choice | customer-specific addition. |
| 4.48 | migration/vendor_conventions/redcap.md:104-105 | redcap | redcap | Classic (non-longitudinal) project → synthesise `SE_STUDY` and `SE_COMMON`; map all instruments to one of these per OC-9. | structured | vendor_redcap_classic_vs_longitudinal_event_structure | event | duplicates the classic-project half of 4.42 / 4.46. |
| 4.49 | migration/vendor_conventions/redcap.md:106-107 | redcap | redcap | Longitudinal project → arm/event grid drives `events` and `arms` in `study_meta`. | structured | vendor_redcap_classic_vs_longitudinal_event_structure | study | duplicates the longitudinal half of 4.42 / 4.46. |
| 4.50 | migration/vendor_conventions/redcap.md:108-114 | redcap | redcap | `redcap:FieldType` → XLSForm type mapping\: `text`/`notes`→`text`; `radio`/`dropdown`/`yesno`/`truefalse`→`select_one`; `checkbox`→`select_multiple`; `calc`→`calculate`; `slider`→`integer`/`decimal` depending on step; `file`→`text` (with attachment-migration note). | structured | vendor_redcap_fieldtype_to_xlsform_type | field | customer-specific addition. |
| 4.51 | migration/vendor_conventions/redcap.md:115 | redcap | redcap | Repeating instrument → OC-8 repeat wrapper. | structured | repeating_form_structural_pattern | form | duplicates 4.44 (Form Structure Quirks vs OC4 Transform Rules). |
| 4.52 | migration/vendor_conventions/redcap.md:116 | redcap | redcap | Always uppercase OIDs for OC4 even though REDCap stores them lowercase. | structured | vendor_redcap_oid_lowercase_then_uppercase | field | duplicates the normalisation half of 4.43. |
| 4.53 | migration/vendor_conventions/veeva.md:31-36 | veeva | veeva | Treatment cycles encoded as repeating `StudyEventDef` entries named `CYCLE1`, `CYCLE2`, `CYCLE3`, … → propagate as OC4 repeating events. | structured | vendor_veeva_cycle_event_naming | event | customer-specific addition. |
| 4.54 | migration/vendor_conventions/veeva.md:38-42 | veeva | veeva | Cycle event OIDs\: `CYCLE<N>` → `SE_CYCLE_<N>` after normalisation; form OIDs follow CDASH (`F_DM`, `F_AE`, `F_VS`, etc.). | structured | vendor_veeva_cycle_event_naming | event | customer-specific addition; specialisation of cdash_field_naming for cycle events. |
| 4.55 | migration/vendor_conventions/veeva.md:46-50 | veeva | veeva | Multi-cycle dosing forms reuse the same `FormDef` across multiple `CYCLE` events; the OC4 transform emits one form per CDASH domain and relies on `visits_assigned` to encode reuse. | structured | vendor_treatment_cycle_form_reuse | form | customer-specific addition. |
| 4.56 | migration/vendor_conventions/veeva.md:52-57 | veeva | veeva | Standard `StudyEventDef` → `SE_<NAME>`; cycle events (`CYCLE1`..`CYCLE3`) → `SE_CYCLE_1`..`SE_CYCLE_3`, each marked `Repeating="Yes"` when applicable; AE/CM/DV/AESAE pin to `SE_COMMON` per OC-9. | structured | common_visit_safety_admin_placement | event | mixes vendor_veeva_cycle_event_naming with reaffirmation of common_visit_safety_admin_placement. |
| 4.57 | migration/vendor_conventions/veeva.md:82 | veeva | veeva | Veeva cycle events `CYCLE<N>` → `SE_CYCLE_<N>` in OC4. | structured | vendor_veeva_cycle_event_naming | event | duplicates 4.54 (Event/Visit Mapping vs OC4 Transform Rules). |
| 4.58 | migration/vendor_conventions/veeva.md:83-84 | veeva | veeva | Treatment cycle reuse → emit one OC4 form with multi-event `visits_assigned`, never duplicate the form per cycle. | structured | vendor_treatment_cycle_form_reuse | form | duplicates 4.55. |
| 4.59 | migration/vendor_conventions/veeva.md:85-86 | veeva | veeva | Standard CDASH naming — minimal transform; rely on the generic `_oc_form_id` / `_oc_item_name` paths. | structured | cdash_field_naming | field | reaffirms System 2 cdash_field_naming. |
| 4.60 | migration/vendor_conventions/veeva.md:87 | veeva | veeva | `FormDef/@Repeating="Yes"` → OC-8 repeating wrapper. | structured | repeating_form_structural_pattern | form | reaffirms repeating_form_structural_pattern (1.10 / OC-8). |
| 4.61 | migration/vendor_conventions/viedoc.md:44-52 | viedoc | viedoc | `viedoc:RowLayout` describes multi-column input rows; map to XLSForm `appearance` where there is a clean equivalent (`horizontal`, `w2`, `w3 horizontal`). Common forms pin to `SE_COMMON` per OC-9 even if listed under `viedoc:FormType="Common"` pseudo-event. | hybrid | vendor_viedoc_rowlayout_to_appearance | field | customer-specific addition + reaffirmation of common_visit_safety_admin_placement. |
| 4.62 | migration/vendor_conventions/viedoc.md:62-67 | viedoc | viedoc | Viedoc supports multi-language decodes; export uses `xml:lang` on each `TranslatedText`. The transform takes the English decode and preserves multilingual entries in `vendor_specific.codelist_translations`. | structured | vendor_viedoc_multilang_decode_preserved | choice | customer-specific addition. |
| 4.63 | migration/vendor_conventions/viedoc.md:85-86 | viedoc | viedoc | `viedoc:DisplayName` → use as XLSForm `label` when richer than the ODM `Question/TranslatedText`. | structured | vendor_viedoc_displayname_as_label | field | customer-specific addition. |
| 4.64 | migration/vendor_conventions/viedoc.md:87-90 | viedoc | viedoc | `viedoc:RowLayout` → map to XLSForm `appearance`\: `Single` → omit (default); `Inline2` → `w3 horizontal`; `Inline3+` → `w2`. | structured | vendor_viedoc_rowlayout_to_appearance | field | duplicates 4.61 (Form Structure Quirks vs OC4 Transform Rules). |
| 4.65 | migration/vendor_conventions/viedoc.md:91-92 | viedoc | viedoc | Repeating `StudyEventDef` → emit OC4 repeating event entries in `timepoint_csv`. | structured | repeating_event_to_timepoint_repeats | event | customer-specific addition. |
| 4.66 | migration/vendor_conventions/viedoc.md:93 | viedoc | viedoc | All common forms pin to `SE_COMMON` per OC-9. | structured | common_visit_safety_admin_placement | form | reaffirms common_visit_safety_admin_placement (1.11 / OC-9). |
| 4.67 | migration/vendor_conventions/zelta.md:30-34 | zelta | zelta | Repeating event groups (treatment cycles, dosing periods) encoded as repeating `StudyEventDef` entries with `Repeating="Yes"`. | structured | repeating_event_to_timepoint_repeats | event | reaffirms 4.65 (Viedoc same pattern). |
| 4.68 | migration/vendor_conventions/zelta.md:35-40 | zelta | zelta | Event OIDs may use either `SE_<NAME>` or bare `<NAME>` — the transform normalises both via `_oc_event_oid`. | structured | oid_normalisation_oc4 | event | reaffirms 4.1 oid_normalisation_oc4 (event-half). |
| 4.69 | migration/vendor_conventions/zelta.md:42-47 | zelta | zelta | Log-line forms (AE, CM, MH, EX) use the standard `FormDef/@Repeating="Yes"` + repeating `ItemGroupDef` pattern; multi-period dosing schedules with one `FormDef` reused across periods → one OC4 form with multi-event `visits_assigned`. | structured | vendor_treatment_cycle_form_reuse | form | mixes reaffirmation of repeating_form_structural_pattern with vendor_treatment_cycle_form_reuse (4.55). |
| 4.70 | migration/vendor_conventions/zelta.md:49-53 | zelta | zelta | Standard `StudyEventDef` → `SE_<NAME>`; repeating cycle/period events → OC4 repeating events; AE/CM/DV/AESAE pin to `SE_COMMON` per OC-9. | structured | common_visit_safety_admin_placement | event | reaffirms common_visit_safety_admin_placement + repeating_event_to_timepoint_repeats. |
| 4.71 | migration/vendor_conventions/zelta.md:75-79 | zelta | zelta | Treat Zelta as generic ODM with standard OC-1..OC-9 rules unless a recognised legacy `ibm:` / `merge:` namespace is present (in which case preserve those attributes in `vendor_specific`). | structured | unknown_namespace_capture_in_vendor_specific | study | reaffirms 4.8 + meta-rule "fall through to generic". |
| 4.72 | migration/vendor_conventions/zelta.md:80-81 | zelta | zelta | Repeating cycle structure → standard OC4 repeating events; no special cycle-naming pattern is enforced. | structured | repeating_event_to_timepoint_repeats | event | reaffirms repeating_event_to_timepoint_repeats. |
| 4.73 | migration/vendor_conventions/zelta.md:82 | zelta | zelta | Standard CDASH naming — minimal transform. | structured | cdash_field_naming | field | reaffirms System 2 cdash_field_naming. |

### 4C. Cross-cutting observations

The vendor files share four heavily-reaffirmed global rules:

- `repeating_form_structural_pattern` (OC-8 / 1.10): rows 4.2, 4.12,
  4.16, 4.19, 4.22, 4.27, 4.30, 4.32, 4.44, 4.51, 4.60, (4.69)
- `common_visit_safety_admin_placement` (OC-9 / 1.11): rows 4.4, 4.13,
  4.20, 4.23, 4.28, 4.33, 4.46, 4.56, 4.61, 4.66, 4.70
- `cdash_field_naming` (System 2): rows 4.31, 4.34, 4.59, 4.73
- `oid_normalisation_oc4` (new): rows 4.1, 4.68 — Castor 4.11/4.15
  specialise it for UUIDs.

Most of these are "reaffirmation" rather than override, suggesting the
vendor layer should INHERIT from a single canonical global definition
rather than restating it per vendor file.

Truly vendor-specific additions (no global equivalent) cluster around:

- OID quirks (UUIDs → Castor; hierarchical OIDs → Oracle InForm; lowercase
  OIDs → REDCap; cycle naming → Veeva)
- Vendor-namespace attribute handling (`mdsol:IsLog`, `mdsol:DefaultMatrixOID`,
  `pf:HierarchicalOIDs`, `pf:DBUID`, `viedoc:DisplayName`, `viedoc:RowLayout`,
  `redcap:FieldType`, `redcap:SectionHeader`)
- Event-structure synthesis (`SE_UNSCHEDULED`, `SE_STUDY` for REDCap classic)
- Treatment-cycle form-reuse (Veeva, Zelta share `vendor_treatment_cycle_form_reuse`)

Internal duplicates within a single file are common: most files restate
the same rule in both "Form Structure Quirks" / "OID Conventions" /
"Event/Visit Mapping" AND again in "OC4 Transform Rules". Row pairs that
duplicate within one file: 4.11/4.15, 4.12/4.16 (Castor); 4.19/4.22,
4.20/4.23 (Rave); 4.36/4.39 (Oracle InForm); 4.42/4.48, 4.43/4.52,
4.44/4.51 (REDCap); 4.54/4.57, 4.55/4.58 (Veeva); 4.61/4.64 (Viedoc).
A consolidated catalog should keep one canonical row per
(vendor, natural_key) pair.

Schema-fit issue (same as System 3W audit): nothing in this directory
follows the `conventions/schema/convention.schema.json` shape. Files
are markdown prose, no id/kind/status/natural_key/target fields, no
`applies_when`/`effect` DSL. They are AI-prompt fragments, not
engine-loadable records — Phase B will need a markdown→JSON extractor
(or a hand-translation pass) before these can flow through the cascade.

## Cross-system references

### Confirmed duplicates (same rule, multiple source systems)

| Topic / natural_key                  | System 1 row | System 2 row | System 4 rows                                               |
|--------------------------------------|--------------|--------------|-------------------------------------------------------------|
| common_visit_safety_admin_placement  | 1.11 (OC-9)  | 2.7 (§7)     | 4.4, 4.13, 4.20, 4.23, 4.28, 4.33, 4.46, 4.56, 4.61, 4.66, 4.70 |
| repeating_form_structural_pattern    | 1.10 (OC-8)  | —            | 4.2, 4.12, 4.16, 4.19, 4.22, 4.27, 4.30, 4.32, 4.44, 4.51, 4.60, 4.69 |
| cdash_field_naming                   | —            | 2.4 (§4)     | 4.31, 4.34, 4.59, 4.73                                      |
| oid_normalisation_oc4                | —            | —            | 4.1, 4.68 (Castor 4.11/4.15 specialise for UUIDs)           |

### Complementary (related, not strict duplicates)

| Pair                              | Relation                                                                                          |
|-----------------------------------|---------------------------------------------------------------------------------------------------|
| 2.8 (§8) ↔ 1.8 (OC-6)             | §8 policy ("default to soft") + OC-6 mechanism doc ("here's how to opt in"). Should merge in B.1. |
| 2.3 (§3) ↔ 1.10 (OC-8)            | Both touch begin/end_group, but §3 = all forms, OC-8 = repeating only.                            |
| 2.13–2.14 (§13–§14) ↔ 1.3 (OC-3)  | §13/§14 fill specific cells in the same settings sheet OC-3 mandates.                             |
| 2.15 (§15) ↔ 1.3/1.4 (OC-3/OC-4)  | §15 auto-populates the `crossform_references` cell using OC-4's cross-form patterns.              |
| 2.16 (§16) ↔ 1.2 (OC-2)           | OC-2 = itemgroup mandatory; §16 = same value for repeating-record peers.                          |

### Candidate decomposition (Phase B.1+)

| §-row | natural_key                          | Likely OC-7 sub-rule (1.9)                                |
|-------|--------------------------------------|-----------------------------------------------------------|
| 2.25  | eligibility_verdict_3state            | OC-7 "eligibility fixed-value constraints"                |
| 2.27  | sentinel_exclusivity_constraint       | OC-7 "RACE multi-select exclusion"                        |
| 2.28  | decimal_precision_constraint          | OC-7 "physiological range sanity checks"                  |
| 2.26  | value_unit_pair_side_by_side_layout   | OC-7 BMI / dose-duration patterns area                    |

Resolution deferred until OC-7 is decomposed into 7A–7P.

### Stale duplicates (delete during Phase B/C, per architecture doc §11)
- OC-7 sub-rules 7A–7P also copied in `skills/edc-builder/references/xlsform-build-rules.md`
  and `skills/protocol-analysis/references/xlsform-patterns.md`. Single
  canonical home will be `conventions/global/`; the markdown copies get
  replaced with references.

## Effort sizing for Phase B.1+

| Bucket                                                  | Approx row count |
|---------------------------------------------------------|------------------|
| System 1 OC-N translations (1:1)                        | 11               |
| System 2 §0–§19 translations (instrumented, 1:1)        | 20               |
| System 2 §20–§28 re-scope-then-translate                | 9                |
| OC-7 → 7A–7P decomposition                              | ~16 sub-rows     |
| System 3 schema migration                               | 0 content; writer rewrite only |
| System 4 vendor-specific translations (post-dedup)      | ~36              |
| System 4 reaffirmations (drop or auto-inherit)          | ~37              |
| **Translation total**                                   | **~92 unique convention files** |

End of inventory.
