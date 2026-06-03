# Migration Analysis Skill

## Purpose

Convert an ODM XML export from a competitor EDC (Medidata Rave, Oracle InForm, REDCap, Castor, Viedoc, Veeva, Zelta/Merative, iMedNet, Medrio, OpenClinica 3) into the **same Study Spec JSON schema** that `protocol-analysis` produces from a protocol PDF. The migration path joins the downstream pipeline at exactly the same point — `run_study_spec_files`, `run_edc_build`, `create_oc_study` all consume an identical schema.

This skill is invoked by `migration_pipeline.run_migration` via `odm_to_spec.transform_with_ai`. Its content is prepended to the Claude prompt so the AI uses the same rules as the documented skill.

```
ODM XML  →  odm_reader.parse_odm_metadata  →  OdmStudy
         →  odm_to_spec.transform_with_ai(skill_content=this_file)
         →  Study Spec JSON  →  same downstream pipeline as protocol-analysis
```

## Preserve-First Design Principle

The migrating customer already trained sites on the source EDC's field names, codelist values, form titles, and event labels. Renaming any of these forces site-level retraining and breaks audit-trail continuity. The skill MUST preserve the customer's source naming and structure UNLESS there is a hard OC4 technical reason to change it.

Hard reasons to change (must override preservation):

- **OC OID syntax constraints.** Event OIDs must start with `SE_` and contain only `[A-Z0-9_]`; form OIDs must be plain short uppercase tokens; item names must be `[A-Z0-9_]`. Non-conforming characters are replaced with `_` and uppercased (see `_oc_event_oid`, `_oc_form_id`, `_oc_item_name` in `odm_to_spec.py`).
- **OC-9 (SE_COMMON pin).** AE / CM / DV / AESAE forms must be assigned only to `SE_COMMON`, regardless of the source event list. This overrides whatever events the source ODM lists for those forms.
- **Form title characters that deadlock OC4.** Strip `+`, `&`, `%`, `#`, `@` from form titles. Replace `+` with the literal three-character sequence ` and ` (space-and-space). The `+` failure is empirically confirmed (CRS-135, 2026-06-02): OC4's form-service deadlocks on `+` in titles.
- **Type narrowing that causes data loss.** If a source DataType cannot be represented in any XLSForm type without loss, fall back to `text` (Data Loss Risk) and log it for the Gap Appendix.

Everything else (codelist coded_values, item labels/questions, group names, event labels, form titles after sanitisation, item OIDs that already meet OC4 syntax) is preserved verbatim from the ODM source.

## The 7 Steps

The skill operates as a 7-step pipeline. Steps 1–6 build the Study Spec JSON; step 7 produces the Gap Appendix that ships embedded in the Study Spec PDF / XLSX.

### Step 1 — Detect vendor

The vendor string in `odm_study["source_system"]` is set by `odm_reader._detect_vendor` from the ODM `Originator` attribute and vendor-extension namespaces. Use the value as the key into `VENDOR_CONVENTION_FILES` (`odm_to_spec.py:56`) to load the matching `vendor_conventions/<vendor>.md` reference. The convention file is already injected into the prompt by `_render_ai_assist_prompt` — read it before applying mappings. Fall back to `generic_odm.md` if the vendor is not in the table.

The 11 supported vendor entries are listed in `references/odm-to-oc4-field-mapping.md` (Vendor Detection section).

### Step 2 — Map events

Walk `odm_study["events"]` (StudyEventDef rows). For each event:

1. Normalise the OID with `_oc_event_oid`: `re.sub(r"[^A-Za-z0-9_]", "_", oid).upper()`, then prepend `SE_` if missing.
2. Carry `name` through as the human `timepoint` label.
3. Carry `repeating` through unchanged.
4. Carry the event's `form_refs` list (FormDef OIDs) — used in step 3 for visit assignment.

Emit one `timepoint_csv.rows` entry per unique normalised event OID (preserve original order; assign `visit_number` 1-based by appearance). Always ensure `SE_COMMON` is present in the timepoint rows (OC-9); if no source event maps to it, append a synthetic `SE_COMMON` row.

### Step 3 — Map forms

For each FormDef in `odm_study["forms"]`:

1. Compute `form_id` via `_oc_form_id(form.oid, form.name)`. This strips known prefixes (`F_`, `F.`, `CRF_`, `FORM_`), uppercases, and prefers a CDASH-domain match (`CDASH_DOMAIN_MAP`) when the cleaned name matches a CDASH 2-letter or short domain. Caps at 20 chars on natural underscore boundaries.
2. Compute `form_title` from the source `name` and apply form-title sanitisation:
   - Replace `+` with ` and ` (space-and-space — the `+` character has been confirmed to deadlock OC4's form-service in CRS-135 on 2026-06-02).
   - Strip `&`, `%`, `#`, `@`.
   - Leave `()`, `/`, `-`, `°`, letters, digits, and spaces untouched.
   - Collapse any resulting double-spaces to single.
3. Compute `visits_assigned` via `_build_visit_assignment`:
   - **OC-9 override:** if `form_id` is in `COMMON_VISIT_FORMS = {"AE", "CM", "DV", "AESAE"}`, return `["SE_COMMON"]` regardless of source.
   - Otherwise, return every event whose `form_refs` includes this form's OID, normalised via `_oc_event_oid`.
   - If the form is referenced nowhere, return `["SE_UNSCHEDULED"]`.
4. `has_repeating_group` = `form.repeating OR form_id in REPEATING_DOMAINS` (`{AE, CM, MH, DV, PC, PR, EX}`).
5. `complexity` = `_form_complexity` score (Simple / Average / Complex from item count + repeating + constraints + codelists).
6. `cdash_alignment`: `FULL` if `form_id` is in `CDASH_DOMAIN_MAP`, `PARTIAL` if any item carries a `cdash_alias`, else `NONE`.

### Step 4 — Map fields

For each ItemDef referenced by the form's item-groups, emit a survey row built via `_build_survey_row`:

1. `name` = `_oc_item_name(item.oid, item.name, item.cdash_alias)` — prefers CDASH alias if present, then sanitised name, then OID with `I_<FORM>_` prefix stripped.
2. `type` derivation:
   - If item has a `codelist_ref` resolvable in `codelist_lookup`:
     - `select_multiple <safe_list>` when the item name contains "multiple" or the codelist has > 20 items.
     - Otherwise `select_one <safe_list>`.
   - Else: `DATATYPE_MAP.get(odm_data_type.lower(), "text")` — the full mapping table is in `references/odm-to-oc4-field-mapping.md`.
3. `label` = ODM Question text → `description` → `name` (first non-empty).
4. `bind::oc:itemgroup` = `_oc_itemgroup(form_id, ig_oid, ig_name)` — group code only (no dots, no spaces).
5. `constraint` / `constraint_message`: built from ODM `RangeCheck` rows using the `LT/LE/GT/GE/EQ/NE` → `< <= > >= = !=` mapping.
6. `required` = `"yes"` when the corresponding `ItemRef.mandatory` is true; else empty.
7. `_source_oid` = the original ODM ItemDef OID (verbatim). **This is the gap-analysis link** — see "Source OID Stamping" below.

Within a single `begin group` ... `end group` window, item-name collisions (e.g. two CDASH-aliased fields both becoming `IETESTCD`) are deduped via `_<odm_name>` or `_2`, `_3`, … suffix to satisfy pyxform's local-uniqueness rule.

### Step 5 — Classify mappings

For every emitted survey row whose `_source_oid` is non-empty, gap_analysis runs `_classify(src, tgt)` and assigns one of:

- **High / Clean** — same canonical type, capacity equal or expanded, all source codes covered.
- **Medium / Warning** — lossless widening (integer→decimal, date→datetime, type→text); required→optional regression.
- **Low / Data Loss Risk** — text length shrink, lossy narrowing (decimal→integer, datetime→date, multi→single), partial codelist coverage with ≤50% missing.
- **Unmappable / Blocking** — incompatible types (no defensible coercion), no target row for a required source, or > 50% codelist values missing.

The full ladder (with the seven ordered rules in `_classify`) is in `references/odm-to-oc4-field-mapping.md` — follow it exactly when writing reason text so the downstream UI rendering stays consistent.

### Step 6 — Produce the Study Spec JSON

The output is byte-identical in schema to what `EDC_STRUCTURE_PROMPT` emits from a protocol PDF. Required top-level keys:

```
study_meta, forms, timepoint_csv, labranges_csv, scheduling, study_calendars,
review_flags, migration_meta
```

Plus the deterministic `transform()` baseline already produces `forms[].settings`, `forms[].choices`, `forms[].survey` in the exact shape the downstream `run_edc_build` consumes. The skill's job in `transform_with_ai` is to **enrich** this baseline: fill `study_meta` from protocol PDF when available, add cross-form `relevant` expressions, populate constraint expressions absent from raw ODM, and resolve protocol-driven labranges.

`migration_meta` block (migration-specific, ignored by non-migration code paths) records:

```json
"migration_meta": {
  "source_system":         "...",
  "source_system_version": "...",
  "odm_version":           "1.3.x",
  "source_study_oid":      "...",
  "source_file_oid":       "...",
  "vendor_conventions_applied": "<vendor>.md",
  "ai_enrichment_used":    true|false,
  "skill_content_loaded":  true|false
}
```

### Step 7 — Produce the Gap Appendix (embedded)

The Gap Appendix is **NOT** a separate file. It is rendered into the Study Spec PDF (last appendix section) and the Study Spec XLSX (`GAP_ANALYSIS` sheet) by `run_study_spec_files`. The skill emits the structured data; the PDF/XLSX builders consume it.

Inputs: the gap-analysis report from `run_gap_analysis(odm_metadata, spec_json, source_system)` — schema documented at the top of `migration/gap_analysis.py`.

Sections in the appendix:

1. **Summary counts** — total / clean / warning / data_loss_risk / blocking / unmapped from the gap report's `summary` block.
2. **Per-form gap table** — one row per mapping, columns: source OID • source label • source type • target name • target type • confidence • risk • reason. Rows are sorted by (form, source order), then grouped under form headers.
3. **Vendor convention applied** — name of the `vendor_conventions/<vendor>.md` file used and one-line summary.
4. **Auto-injected rows** — list every row with `_source_oid == ""` (see "Structural Injections" below), grouped by form, with the injection reason. These rows are **excluded** from the gap table because they have no source counterpart.

## Source OID Stamping

Every survey row carries a `_source_oid` field. This is the only link gap_analysis uses to pair ODM source items with their generated target rows — it never replays `_oc_item_name` (that would drift against any AI-enriched name changes).

Rules:

- **ODM-derived rows** (real item mappings): `_source_oid = item.oid` (the original ODM ItemDef OID, verbatim).
- **Structural injections** (SUBJID calculate, DOV date when injected, group wrappers, repeat wrappers, any helper calc the skill adds for OC4 plumbing): `_source_oid = ""` (empty string). These rows are excluded from gap analysis and from the Gap Appendix's per-field table.
- **AI-enriched rows** (Claude added a new field absent from source, e.g. a cross-form dependency or protocol-derived calculation): `_source_oid = ""`. They are excluded from gap analysis (no source to compare) but should appear in the appendix's "Auto-injected rows" section with an explanation.

The deterministic `transform()` does this stamping correctly for SUBJID (line 669) and begin/end group wrappers (lines 696, 735). When you add or modify rows in `transform_with_ai`, you MUST set `_source_oid` according to these rules. A missing `_source_oid` defaults to `""` inside gap_analysis (treated as "not from source"), so the failure mode is silent.

## Structural Injections (excluded from Gap Appendix)

The following rows are injected for OC4 plumbing reasons, not because they correspond to any ODM source item. All carry `_source_oid = ""`:

- **SUBJID calculate row** — every OC4 form needs `SUBJID` to participate in cross-form lookups. Injected at the top of every form's survey if not already present in the source (`odm_to_spec.py:648`). Type: `calculate`, calculation: `instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey`.
- **DOV (Date of Visit) date row** — for visit-anchored events that need a date stamp. When you inject one, mark `_source_oid = ""` and add a Gap Appendix "Auto-injected rows" entry stating the date binding.
- **`begin group` / `end group` wrappers** around every item-group. The wrappers structure the form per OC4's XLSForm grammar; they have no source counterpart.
- **`begin repeat` / `end repeat` wrappers**, when emitted. Per CRS-135 manual testing, the deterministic `transform()` no longer emits XLSForm `begin_repeat`/`end_repeat` trailers because OC4 derives repetition from `bind::oc:itemgroup` on the data fields and rejects XLSForm repeat syntax with "Unmatched end statement" (`odm_to_spec.py:738-743`). If AI enrichment adds them anyway (Phase 2), they must carry `_source_oid = ""`.

## OC-9: SE_COMMON Pinning

AE, CM, DV, and AESAE forms are always assigned `visits_assigned = ["SE_COMMON"]`, regardless of what the source ODM's StudyEventDef → FormRef structure says. This is hard-coded in `_build_visit_assignment` (line 471) and the `COMMON_VISIT_FORMS` set (line 223).

Rationale: in OC4, adverse-event-related forms are collected on the SE_COMMON event because they are not tied to a specific scheduled visit. Source EDCs often replicate the same AE form across every event; mapping that literally would create duplicate cards on every visit. OC-9 collapses this to a single SE_COMMON placement.

Reviewer override: a reviewer can edit the spec JSON post-generation to restore per-visit AE collection. The skill itself never produces non-OC-9-compliant output.

## Form Title Sanitisation

Form titles are display strings rendered on OC4 board cards and in the Designer UI. They must be cleaned before they reach the board JSON or the Designer's `getForm` Meteor call.

Strip these characters:

| Char | Action | Reason |
|------|--------|--------|
| `+`  | Replace with ` and ` (space-and-space) | Empirically confirmed to deadlock OC4's form-service in CRS-135 on 2026-06-02. Replacing with ` and ` reads naturally in display titles like "Sleep Quality (NRS + PROMIS 8A)" → "Sleep Quality (NRS and PROMIS 8A)". |
| `&`  | Strip | Special XML/XPath character; causes parse failures downstream. |
| `%`  | Strip | Format-string character in some OC4 internal renderers. |
| `#`  | Strip | URL-fragment character; breaks deep-links to Designer cards. |
| `@`  | Strip | Reserved in OC4 internal indexing. |

Preserve everything else: letters, digits, spaces, parentheses, slashes, hyphens, degree signs (`°`), and any other Unicode character not in the strip list. Collapse any resulting double-spaces to single.

Apply this to the form `name` BEFORE assigning `form_title` in step 3 and BEFORE the title is passed to the board-JSON builder downstream.

## Output Schema Identity

The spec JSON produced by this skill is consumed unchanged by:

- `run_study_spec_files(struct_json)` → Study Spec PDF + XLSX.
- `run_edc_build(struct_json)` → EDC Build ZIP (xlsforms + timepoint CSV + labranges CSV).
- `create_oc_study(subdomain, struct_json, ...)` → OC study + board import.
- `run_calendaring_rules(struct_json, forms_json)` → Calendaring Rules ZIP.
- `run_dvs_xlsx(struct_json, forms_json)` → DVS XLSX.

There must be **no migration-specific keys** at the top level of `study_meta` or inside `forms[]`. The only migration-specific addition is the top-level `migration_meta` block (read above), which downstream code ignores. Any drift from the protocol-analysis schema breaks the downstream pipeline.

## References

- `references/odm-to-oc4-field-mapping.md` — authoritative DataType table, classification ladder, OID rules, vendor table, sanitisation rules.
- `references/vendor-conventions/<vendor>.md` — per-vendor convention files (Medidata, Oracle InForm, REDCap, Castor, Viedoc, Veeva, Zelta, iMedNet, Medrio, generic_odm, OC3).

## Fallback Behaviour

If this SKILL.md file cannot be loaded at runtime (file missing, IO error), `migration_pipeline._load_migration_skill()` returns the empty string and `transform_with_ai` falls back to `_render_ai_assist_prompt`'s built-in prompts. The pipeline still produces a valid spec — it just lacks the explicit preserve-first + OC-9 + sanitisation reinforcement that this skill adds. Loader behaviour is silent on miss.
