# System 2 Translation Plan (Phase B.1d)

**Status:** Completed 2026-05-15.
**Scope:** Translate the 29 §-numbered rules in `skills/protocol-analysis/references/conventions.md` (lines 1-1155) into individual JSON conventions in `conventions/global/`.

## Source

Authoritative file: `skills/protocol-analysis/references/conventions.md` (1402 lines).
Mirror copy: `services/study-build-trainer/skills/protocol-analysis/references/conventions.md` (identical content).

The 29 §-numbered rules occupy lines 14-1155:
- `## §0 Foundational Rule` at line 14, with sub-sections `### §0.A`, `### §0.B`, `### §0.C`
- `## 1.` through `## 28.` at known line offsets

Mirror approach: keep `services/study-build-trainer/skills/...` mirror as belt-and-suspenders documentation until Phase C cutover, per the established pattern from F2 vendor-axis resolution.

## Output

**31 new conventions** in `conventions/global/`:

| § | Convention id | Category | Kind | Target |
|---|---|---|---|---|
| §0.A | process.protocol_data_item_census | process | advisory | study |
| §0.B | process.form_definition_lookup_hierarchy | process | advisory | study |
| §0.C | process.census_hierarchy_reconciliation | process | advisory | study |
| §1 | form_placement.standalone_icf_always_present | form_placement | hybrid | study |
| §2 | validation.future_date_constraint_on_dates | validation | hybrid | field |
| §3 | field_metadata.begin_end_group_wrapping | field_metadata | structured | form |
| §4 | field_metadata.cdash_field_naming | field_metadata | hybrid | field |
| §5 | field_metadata.uppercase_choice_list_names | field_metadata | hybrid | choice |
| §6 | validation.required_message_on_required_fields | validation | structured | field |
| §7 | form_placement.common_event_safety_admin_placement | form_placement | hybrid | form |
| §8 | validation.soft_edit_checks_by_default | validation | advisory | field |
| §9 | field_types.pdate_for_recall_dates | field_types | hybrid | field |
| §10 | appearance.minimal_autocomplete_for_long_picklists | appearance | hybrid | field |
| §11 | build_artifacts.external_csv_for_long_choice_lists | build_artifacts | hybrid | choice |
| §12 | build_artifacts.item_count_caps_as_warnings | build_artifacts | hybrid | form |
| §13 | field_metadata.briefdescription_on_data_rows | field_metadata | structured | field |
| §14 | form_metadata.form_style_per_purpose | form_metadata | hybrid | form |
| §15 | form_metadata.crossform_references_auto_populate | form_metadata | structured | form |
| §16 | field_metadata.itemgroup_keep_together_repeating | field_metadata | hybrid | field |
| §17 | appearance.likert_appearance_threshold | appearance | hybrid | field |
| §18 | appearance.vas_vertical_appearance | appearance | hybrid | field |
| §19 | appearance.table_appearance_short_labels | appearance | hybrid | field |
| §20 | form_placement.forms_completion_safety_net | form_placement | hybrid | form |
| §21 | form_metadata.header_group_pattern | form_metadata | hybrid | form |
| §22 | clinical_patterns.reminder_notes_gated_by_yn | clinical_patterns | hybrid | field |
| §23 | clinical_patterns.source_label_hidden_parent_disambiguation | clinical_patterns | hybrid | field |
| §24 | clinical_patterns.source_ambiguity_clinical_reasoning | clinical_patterns | advisory | field |
| §25 | clinical_patterns.eligibility_verdict_3state_pattern | clinical_patterns | hybrid | form |
| §26 | appearance.value_unit_pair_side_by_side_layout | appearance | hybrid | field |
| §27 | validation.sentinel_value_exclusivity_constraint | validation | hybrid | field |
| §28 | validation.decimal_precision_constraint | validation | hybrid | field |

**Kind distribution:** 4 structured, 22 hybrid, 5 advisory. (Initial draft was 8/18/5; §22, §26, §27, §28 moved structured→hybrid during validation-pass corrections — schema forbids structured directives coexisting with soft in the same effect block, and these four had soft components that were the right call to keep over the structured directives that would have required not-yet-implemented DSL primitives.)

## Category additions

Three new top-level categories introduced:

- **`process.*`** — rules about how the build pipeline operates, distinct from rules about what it produces. Houses §0's three sub-rules.
- **`appearance.*`** — rules about UI presentation (Likert, VAS, table-list, autocomplete, w2 width class). Distinct from `field_metadata.*` (bind:: metadata) and `field_types.*` (type semantics). Houses §10, §17, §18, §19, §26.
- **`build_artifacts.*`** — rules about build-output artifacts beyond the form xlsx itself (CSV externalizations, item-count warnings). Houses §11, §12.

These earn their place at the projected hundreds-of-conventions scale. `process.*` in particular is positioned for future rules about protocol parsing, vendor-term translation, ODM canonicalization, etc.

## Archives

Three existing conventions superseded by richer System 2 rules:

| Existing convention (from B.1a / B.1c-final) | Superseded by | Status change |
|---|---|---|
| `form_placement.common_visit_safety_admin.json` (OC-9) | §7 | `active` → `archived` |
| `validation.hard_edit_checks_opt_in.json` (OC-6) | §8 | `active` → `archived` |
| `clinical_patterns.race_multiselect_exclusivity.json` (7I) | §27 | `active` → `archived` |

The cascade resolver (`conventions_engine/intersection.py`) already skips conventions with `status: "archived"`. Each archived file gets a history entry pointing at the superseding rule.

## Overlap resolutions

| §-rule | Overlap | Resolution |
|---|---|---|
| §2 | OC-7 7B (start_date_not_in_future) | Coexist. §2 fires universally on type=date; 7B fires on *STDAT-named fields with stricter constraint-message. Ensure directives are idempotent (same value); 7B's flag adds start-date-specific review note. |
| §7 | OC-9 (form_placement.common_visit_safety_admin) | §7 supersedes. OC-9 archived in this commit. |
| §8 | OC-6 (validation.hard_edit_checks_opt_in) | §8 supersedes. OC-6 archived in this commit. |
| §15 | OC-4 (expressions.cross_form_xpath_patterns) | Complementary. OC-4 validates cross-form expression syntax; §15 propagates the resulting dependency metadata to settings sheet. No conflict; both apply. |
| §16 | OC-2 (field_metadata.itemgroup_mandatory_on_data_rows) | Complementary. OC-2: every data row must have itemgroup populated. §16: all rows in one repeating logical record share the SAME itemgroup value. Different rules; both apply. |
| §22 | OC-7 7O.a (YES-BRANCH idiom in clinical_patterns.universal_relevance_patterns) | Coexist. §22 is the mechanical convention for note-after-YN auto-gating with structured detection; 7O.a is the general idiom library entry. §22's history points at 7O.a as the parent idiom. |
| §23 | OC-7 7O.b/c/f (general relevance idioms) | Coexist. §23 is label-text-specific auto-detection-and-rewrite; 7O.b/c/f document the general relevance idiom catalog. Both apply. |
| §25 | OC-7 7G (eligibility_fixed_value_constraints) | Complementary. 7G handles per-criterion constraints (.='Y'); §25 handles the DERIVED OVERALL VERDICT field. Different fields, different concerns; both apply. |
| §27 | OC-7 7I (race_multiselect_exclusivity) | §27 supersedes. 7I archived in this commit. RACE field still gets the constraint under §27 (DECLINED is in its choice list). |

## DSL operators in use

The B.1c extensions (`has_field`, `has_sibling`, `match`, `default_value`) are exercised across this set:

- **has_sibling** (B.1c-1): §22 (preceding_in_form), §26 (next_in_form)
- **match** (B.1c-2): §28 (10-case dispatch on field name → precision)
- **default_value** (B.1c-3): not used in System 2 (consumers were OC-7-specific; will apply on future translations)
- **has_field**: not used in System 2 (form-level existential quantification; consumers were OC-7 specific)

## DSL gaps surfaced

Same gaps as B.1c-final, encountered again:

1. **Sibling-name string interpolation in effect expressions** — §22, §28 need `${<sibling_name>}` resolved inside `field.constraint` / `field.relevant` expression strings.
2. **Form-survey row insertion** — §1, §3, §20, §25 need to emit calculate / Y/N / note rows into form.survey.
3. **Effect on sibling/related entity** — §26 needs to apply an appearance update to the immediately-following field, not the matched field.
4. **Choice-list resolution from field-target context** — §10, §11, §17, §19, §27 all need `field.choice_list_size`, `field.choice_max_label_length`, or `field.choices_contain_sentinel` computed attributes.

Closing any of these would promote multiple hybrid conventions to structured. Tracked for a future DSL-extension pass; not blockers for B.1d landing.

## Trainer-service mirror

`services/study-build-trainer/skills/protocol-analysis/references/conventions.md` is identical to the primary file at translation time. Per F2 resolution's belt-and-suspenders pattern, the mirror is kept as-is in this commit. Delete or symlink decisions deferred to Phase C cutover.

## File count

- 31 new convention JSON files in `conventions/global/`
- 3 modified convention JSON files in `conventions/global/` (archive flips)
- 1 new plan doc in `conventions/_audit/`

Total: 35 files touched. Single commit.
