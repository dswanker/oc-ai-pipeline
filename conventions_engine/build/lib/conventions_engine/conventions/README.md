# Conventions Engine

A rule system that applies consistent patterns across Study Spec generation, EDC building, and ODM migration. Conventions encode OC compliance rules, CDASH conventions, customer-tenant preferences, vendor-source quirks, and study-specific overrides as versioned JSON records — replacing what used to be hard-coded branches in `prompts.py`, `pipeline.py`, and various skills.

This README is the day-to-day reference: directory layout, how the cascade resolves, how to add or override a convention, and where conflict reports surface for human review. For design history and decision records, see [Further Reading](#further-reading).

---

## Overview

**What it is.** A 4-scope cascade resolver (`global`, `customer`, `vendor`, `study`) backed by a small DSL — `applies_when` filters the entities a rule applies to, `effect` declares what the engine does. Records are JSON files validated against `schema/convention.schema.json` at load time.

**Why it exists.** Ensures consistent quality standards, OC compliance, and CDASH conventions without manual review per build. Before the cascade, adding a customer-specific exception meant a code change + redeploy. Conventions move that work into versioned data records that can be added, overridden, or archived without touching code.

**When it runs.** The engine fires inside `apply_conventions(spec, study_id, customer_subdomain, migration_source=None)` from `conventions_engine/__init__.py`. Pipeline.py calls it at three sites:

| Path | Trigger | Engine call |
|------|---------|-------------|
| Fresh build | new protocol PDF processed | `run_study_spec_files()` (Phase C.1) |
| Path X.1 | edited Study Spec XLSX uploaded | inline, with three-way conflict diff (Phase C.2 / C.4) |
| Path M | source EDC ODM uploaded (migration) | inline after ODM transform (Phase B.1b Patch 4) |

Downstream paths (edited Build ZIP, edited DVS, edited Quote XLSX) do not call the engine — they operate on build artifacts produced after the engine has already done its work. See [`_audit/update_prompts_audit.md`](_audit/update_prompts_audit.md) for the full wiring audit.

---

## Directory layout

```
conventions/
├── README.md                    ← this file
├── _audit/                      ← design docs, historical audits, decision records
│   ├── B0_inventory.md
│   ├── F2_resolution.md
│   ├── OC7_decomposition_plan.md
│   └── update_prompts_audit.md
├── schema/
│   ├── convention.schema.json   ← JSON Schema (Draft 2020-12) for one convention record
│   ├── dsl-operators.md         ← applies_when + effect operator reference
│   └── version.txt              ← engine schema version (bumped on breaking changes)
├── global/                      ← universal conventions — apply to every build
│   ├── form_placement.*.json
│   ├── validation.*.json
│   ├── clinical_patterns.*.json
│   └── ...
├── customers/                   ← per-OC-tenant overrides
│   └── <subdomain>/             ← e.g. acme/, customers' OC subdomain
│       └── *.json
├── vendors/                     ← per-source-EDC-vendor (migration builds only)
│   ├── castor/presence.json
│   ├── redcap/presence.json
│   └── ...                      ← 10 vendor slugs; presence stubs only (B.1b Patch 5a)
└── studies/                     ← per-study overrides
    └── <study_id>/              ← e.g. PRTK05/, matches protocol_number
        └── *.json
```

The loader (`conventions_engine/loader.py`) discovers conventions by scope-directory walk: `load_scope(repo_root, "global")` reads every `.json` in `conventions/global/`; `load_scope(repo_root, "study", "PRTK05")` reads `conventions/studies/PRTK05/`. Missing scope directories return empty lists silently — empty `customers/<sub>/`, `vendors/<slug>/`, and `studies/<id>/` are not pre-created; they spring into existence when their first convention lands.

---

## Cascade precedence

The cascade resolves multiple conventions touching the same topic (same `natural_key`) into a single winner. Precedence runs **study > {customer, vendor} > global** — the most specific scope wins.

```
        global
        /    \
     vendor   customer        (peer axis at scope_order=1)
        \    /
        study                  (highest, scope_order=2)
```

`customer` and `vendor` share `scope_order=1`. When both resolve the same `natural_key`, **customer wins** per [F2 sub-decision A](_audit/F2_resolution.md). The collision is recorded in `spec.study_meta.customer_vendor_conflicts` so reviewers can see what was overridden.

The resolver iterates **global → vendor → customer → study** so each subsequent scope overrides the previous; later wins on the same `natural_key`. The losing convention is preserved in the winner's `overrode[]` list (surfaced in `study_meta.conventions_engine_applied`) for full provenance.

**Worked example** — a build for customer `acme`, migration from REDCap, study `PRTK05`:

1. `global/validation.required_message_on_required_fields.json` (`natural_key`: `required_message_on_required_fields`)
2. `customers/acme/required_message_override.json` (same `natural_key`, different `id`)
3. `studies/PRTK05/required_message_relaxed.json` (same `natural_key` again)

The cascade applies the study record. Customer and global both end up in the study's `overrode[]` array — visible in the conventions_engine_applied audit trail.

Implementation: [`conventions_engine/cascade.py`](../conventions_engine/cascade.py).

---

## Creating a global convention

A convention is one JSON file in `conventions/global/`. The filename should match the convention's `id`, e.g. `id: "validation.dm_brthdat_required"` lives at `conventions/global/validation.dm_brthdat_required.json`.

**Required fields** (per `schema/convention.schema.json`):

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Dotted snake_case identifier. Must be globally unique. |
| `title` | string | Short human-readable title (3-120 chars). |
| `kind` | enum | One of `"structured"`, `"hybrid"`, `"advisory"` — see below. |
| `scope` | enum | For global: `"global"`. (Other scopes: `"customer"`, `"vendor"`, `"study"` — those require `scope_id`.) |
| `status` | enum | One of `"proposed"`, `"active"`, `"archived"`. Only `"active"` is applied at build time. |
| `natural_key` | string | Lowercase snake_case topic tag. **This is the cascade key** — same `natural_key` across scopes = same topic, study wins. |
| `description` | string | 1-3 sentence prose explanation. |
| `target` | enum | One of `"study"`, `"form"`, `"field"`, `"event"`, `"choice"`. |
| `created_at` | string | ISO 8601 timestamp. |
| `created_by` | string | `"system:<source>"` or `"human:<username>"`. |
| `source` | string | Provenance string, e.g. `"consolidation:skills/protocol-analysis/references/conventions.md:§7"`. |

Optional but commonly present: `rationale` (1-2 sentences explaining why), `tags` (array), `applies_when` (DSL filter — required for `structured`/`hybrid`), `effect` (DSL action — required for `structured`/`hybrid`), `history` (audit array).

**Convention kinds:**

- `"structured"` — has both `applies_when` and `effect`, no `soft:` markers. Engine applies deterministically.
- `"hybrid"` — has both `applies_when` and `effect`, with at least one `soft:` marker for Claude-judgment fallback.
- `"advisory"` — pure prose guidance. `applies_when`/`effect` are forbidden. Renders into Claude's prompt context only.

**Example — `conventions/global/validation.dm_brthdat_required.json`:**

```json
{
  "id": "validation.dm_brthdat_required",
  "title": "Birth date required on DM form per CDASH",
  "kind": "structured",
  "scope": "global",
  "status": "active",
  "natural_key": "dm_brthdat_required",
  "description": "DM form's BRTHDAT (date of birth) field must be required. CDASH treats birth date as mandatory data capture for any demographics collection.",
  "rationale": "CDASH IG §5.3 — Birth date is mandatory per the CDASH demographics specification.",
  "target": "field",
  "applies_when": {
    "form.form_id": "DM",
    "field.name": "BRTHDAT"
  },
  "effect": {
    "set": { "field.required": "yes" }
  },
  "created_at": "2026-05-18T00:00:00Z",
  "created_by": "human:dswanker",
  "source": "cdash:ig_5.3_birthdate_required"
}
```

The `applies_when` block uses entity-relative paths (`form.form_id`, `field.name`) — see [`schema/dsl-operators.md`](schema/dsl-operators.md) for the full operator reference. The `effect.set` value uses the same entity-relative path style — `field.required` resolves to "this field's required column" at apply time.

**Validating locally before committing:**

The pytest suite tests the loader + engine against fixture files, not against the live `conventions/global/`. For ad-hoc validation of a new file:

```bash
~/oc-ai-pipeline/.venv/bin/python3 - <<'PY'
import json, jsonschema
schema = json.load(open('conventions/schema/convention.schema.json'))
record = json.load(open('conventions/global/your_new_convention.json'))
jsonschema.validate(instance=record, schema=schema)
print("schema validation: PASS")
PY
```

Then run the engine suite to catch any broader regression:

```bash
~/oc-ai-pipeline/.venv/bin/python3 -m pytest tests/conventions/
```

If validation fails in production, the loader catches the exception and surfaces it as `spec.review_flags.convention_load_errors[*]` — the build continues without applying the bad convention, but reviewers see the error in the spec output.

**Lifecycle:** Conventions are never deleted, only `status`-archived. Git history is the audit trail. Setting `status: "archived"` makes the engine ignore the record; the file stays in the repo for posterity.

---

## Study-scope overrides

When a single study needs a different behavior than the global rule (sponsor's request, protocol-specific exception, regulatory carve-out), create an override in `conventions/studies/<study_id>/`. The `<study_id>` matches `study_meta.protocol_number`.

**The override mechanism is keyed on `natural_key`, not `id` or filename.** Two records with the same `natural_key` at different scopes are treated as "addressing the same topic"; the more-specific scope wins. The `id` and filename of the study record can be anything unique — what makes it an override is the matching `natural_key`.

**Example — `conventions/studies/PRTK05/form_placement_override.json`:**

```json
{
  "id": "study.prtk05.form_placement_ae_baseline",
  "title": "PRTK05 — Adverse Events captured at BASELINE, not Common Visit",
  "kind": "structured",
  "scope": "study",
  "scope_id": "PRTK05",
  "status": "active",
  "natural_key": "common_visit_safety_admin_placement",
  "description": "PRTK05's safety monitoring protocol requires AE capture at the BASELINE visit specifically, not the pooled SE_COMMON event. Overrides the global Common Visit safety/admin placement convention for this study only.",
  "rationale": "Sponsor request 2026-04-12; protocol §6.2.1 specifies AE collection at BASELINE for the safety-monitoring data flow.",
  "target": "form",
  "applies_when": {
    "form.form_id": "AE"
  },
  "effect": {
    "set": { "form.visits_assigned": ["SE_BASELINE"] }
  },
  "created_at": "2026-05-18T00:00:00Z",
  "created_by": "human:dswanker",
  "source": "sponsor_request:prtk05_2026-04-12"
}
```

`scope_id` is required for non-global scopes (`customer`, `vendor`, `study`) — it tells the loader which subdirectory the record belongs to.

The cascade resolver sees `validation.required_fields_enforcement` from `global/...` AND `study.prtk05.form_placement_ae_baseline` from `studies/PRTK05/...` both carrying `natural_key: "common_visit_safety_admin_placement"`. The study record wins; the global record goes into the study winner's `overrode[]` list.

---

## Conflict reports

Phase C.2 / C.4 added conflict detection to the edited-XLSX update path (Path X.1). When a human uploads a Study Spec XLSX they've edited, the pipeline diffs three snapshots and reports the fields where the engine modified a value the user also touched.

**Where conflicts surface:**

| Output | Field / location |
|--------|------------------|
| Spec JSON | `study_meta.convention_conflicts` (list of dicts) |
| Spec JSON | `study_meta.user_changes` (companion — all paths the user changed, regardless of conflict) |
| Study Spec PDF | Appendix page: `APPENDIX — CONVENTION CONFLICTS DETECTED` (omitted entirely when no conflicts) |
| Study Spec XLSX | Worksheet: `CONVENTION_CONFLICTS` (not created when no conflicts) |

**Schema — `convention_conflicts` row (Phase C.4, 5 keys):**

| Key | Meaning |
|-----|---------|
| `field_path` | Spec-absolute path, e.g. `"forms[3].survey[2].required"` |
| `baseline_value` | What the system originally generated |
| `user_value` | What the user uploaded (after their XLSX edits) |
| `engine_value` | What conventions produced after running on the user's edit |
| `convention_id` | Which convention caused the change (or `None` if attribution failed) |

**Three-way diff explanation:**

A "true" conflict requires three snapshots:

- `baseline` — the previous `spec_json` written to Monday (what the user downloaded and edited from)
- `user_edit` — the spec parsed from the uploaded XLSX (what the user wants)
- `post_convention` — the spec after `apply_conventions` runs on `user_edit`

A field is a true conflict only when `baseline ≠ user_edit` AND `user_edit ≠ post_convention` — meaning the user deliberately changed it AND the engine then changed it again. Fields where the user left the baseline untouched (engine routine work on untouched paths) don't appear. Fields where the user edited but the engine left alone don't appear either.

**Fallback (4-key schema):** On the first edit cycle for a study (no prior `spec_json` in Monday) or if the baseline download fails, the pipeline falls back to two-way diff — every engine mutation on the user's spec is reported, `baseline_value` is absent. Renderers handle both schemas gracefully; missing `baseline_value` shows as `—`.

For the full algebra and edge cases see [`conventions_engine/diff.py`](../conventions_engine/diff.py)'s module docstring.

---

## Further reading

**Historical / design docs:**

- [`_audit/F2_resolution.md`](_audit/F2_resolution.md) — Vendor cascade axis: how vendor became a peer of customer (sub-decision A: customer wins on tie); Patch 3 / Patch 5 implementation notes documenting where design met reality.
- [`_audit/B0_inventory.md`](_audit/B0_inventory.md) — Original Phase B.0 audit cataloguing every convention candidate from `prompts.py`, the conventions markdown, the trainer rulebook, and vendor markdowns; the source list that drove B.1a (global) and B.1b (vendor) authoring.
- [`_audit/OC7_decomposition_plan.md`](_audit/OC7_decomposition_plan.md) — Decomposition strategy for the umbrella OC-7 "universal clinical patterns" rule into ~16 focused sub-rules.
- [`_audit/update_prompts_audit.md`](_audit/update_prompts_audit.md) — Phase C.5 audit confirming engine wiring is complete for all spec-generating paths; identifies 4 dead prompts as cleanup candidates.

**Reference:**

- [`schema/convention.schema.json`](schema/convention.schema.json) — JSON Schema (Draft 2020-12) for convention records. Authoritative source for required/optional fields and value constraints.
- [`schema/dsl-operators.md`](schema/dsl-operators.md) — `applies_when` and `effect` operator reference, including `match`, `default_value`, `form.has_field`, `field.has_sibling`, and all comparison/logical operators.

**Source code:**

- [`../conventions_engine/`](../conventions_engine/) — The engine itself: loader, cascade, applies_when evaluator, effect applier, diff, attribution, record-keeping. Each module has a substantial docstring; start at `__init__.py` for the orchestrator.
- [`../tests/conventions/`](../tests/conventions/) — 190-test suite. Each module has a paired test file; `test_orchestrator.py` runs end-to-end scenarios.
