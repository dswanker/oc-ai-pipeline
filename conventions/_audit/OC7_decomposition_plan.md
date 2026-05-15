# OC-7 Decomposition Plan (B.1c-final completion record)

**Status:** Decomposition complete. 16 sub-rule conventions emitted in Phase B.1c-final.
**Source:** `prompts.py` lines 266–474 (RULE OC-7 — UNIVERSAL CLINICAL DATA PATTERNS).
**Umbrella convention retained:** `conventions/global/clinical_patterns.universal_clinical_data_patterns.json` from Phase B.1a (commit `4651d53`). It now points to the 16 sub-rule conventions for the engine-resolvable per-sub-rule details.

## What changed from the B.1a plan-doc draft

Phase B.1a's plan doc proposed a 16-sub-rule decomposition based on names, but several sub-letter assignments were incorrect (the B.1a author hadn't yet pulled the actual prose from `prompts.py`). The corrections:

| Sub-letter | B.1a plan-doc guess | Actual `prompts.py` text |
|---|---|---|
| 7K | "CF helpers" | duration-in-days calculation (optional) |
| 7L | "sex-dependent fields" | CF helper pattern (cross-form value fetch) |
| 7M | "repeat-key calculate + display pair" (duplicate of OC-5) | sex-dependent fields require SEX_CF |
| 7N | (placeholder) | consent date floor for event dates |
| 7O | (placeholder) | universal relevance patterns (umbrella with 7 sub-cases) |
| 7P | (placeholder) | universal conditional branching patterns (umbrella with 6 sub-cases) |

The B.1a plan's "skip 7M as duplicate of OC-5" recommendation is rescinded — 7M in actual `prompts.py` is sex-dependent fields, not a repeat-key duplicate. **No skip. 16 sub-rules emitted, none retired.**

## Sub-rule catalogue (final)

Each entry: sub-letter → natural_key, kind, target, DSL operators used. Detailed prose lives in the corresponding JSON file under `conventions/global/`.

| Sub | natural_key | kind | target | DSL ops used |
|---|---|---|---|---|
| 7A | `paired_start_end_date_constraint` | hybrid | field | `has_sibling` |
| 7B | `start_date_not_in_future` | hybrid | field | `matches`, `any_of`, `ensure`, `flag` |
| 7C | `endat_relevance_on_ongoing_status` | hybrid | field | `has_sibling` |
| 7D | `sequential_dates_cascade` | hybrid | form | soft-only |
| 7E | `bmi_calculation` | hybrid | form | `has_field` (×2, in `all_of`) |
| 7F | `ae_severity_serious_logic` | hybrid | field | `has_sibling`, `ensure`, `flag` |
| 7G | `eligibility_fixed_value_constraints` | hybrid | field | `matches`, `flag` |
| 7H | `physiological_range_sanity_check` | **structured** | field | `in`, **`match`** |
| 7I | `race_multiselect_exclusivity` | hybrid | field | `all_of`, `ensure`, `flag` |
| 7J | `dose_calculation_optional` | hybrid | form | soft-only |
| 7K | `duration_in_days_optional` | hybrid | form | soft-only |
| 7L | `cross_form_value_fetch_cf` | hybrid | field | `matches`, `ensure` |
| 7M | `sex_dependent_fields_via_sex_cf` | hybrid | form | `has_field` (regex), `any_of` |
| 7N | `consent_date_floor` | hybrid | field | `all_of`, `not_in` |
| 7O | `universal_relevance_patterns` | **advisory** | field | none |
| 7P | `universal_conditional_branching` | **advisory** | field | none |

Kind distribution: 1 structured, 13 hybrid, 2 advisory. Only 7H (physiological ranges) is fully structured today — the rest carry soft directives because their effects require capabilities the engine doesn't yet have (sibling-name interpolation, constraint-text append, row insertion into form.survey).

7O and 7P are advisory because they document XPath idiom libraries rather than fire-on-entity rules. They render into Claude's prompt context during builds without triggering any mechanical evaluation.

## DSL gaps surfaced during decomposition

The translation exercise surfaced four DSL gaps. Each one could promote one or more hybrid conventions to structured if implemented:

1. **Sibling-name string interpolation in effect expressions.** 7A, 7C, 7F all reference a sibling's actual field name in their constraint/relevant expressions (e.g. `${AESTDAT}` for the STDAT sibling of AEENDAT). Today the convention author has to write the expression in soft prose. A DSL primitive `${sibling.field.name}` resolved at effect-apply time would make these structured.

2. **Constraint-text append (extend, don't overwrite).** 7N's consent-date-floor extends an existing constraint with ` and . >= ${ICFDAT_CF}` rather than overwriting. `effects.set` overwrites; `effects.ensure` fires only when empty. A new `effects.extend` directive (read current, AND-join with addition, write result) would close this gap.

3. **Form-survey row insertion at form scope.** 7E (BMI), 7J (dose calc), 7K (duration), 7L (CF helpers), 7M (sex-dependent helpers), 7N (consent helper) all need to emit a calculate row into a form's survey. Today this is soft-only. An `effects.emit_row` directive at form scope, taking a row dict and an insertion position (head/tail/after-field-X), would make these structured.

4. **Per-field structural metadata beyond bind__oc_itemgroup.** Several conventions need to read or write XLSForm columns we don't currently surface in the spec model (constraint_message, readonly, bind__oc_external). These columns exist in the underlying XLSForm files; the engine's spec representation needs to surface them as first-class field attributes. Today they're accessed via `_set_path("field.constraint_message", ...)` which writes to the field dict but isn't read back by anything in the engine.

These gaps are independent of B.1c. They'd land in a future B.1d-or-later DSL-extension pass.

## Overlaps with System 2 (B.1d candidates)

The B.0 inventory's System 2 (29 §-numbered rules, not yet translated) contains several rules that look like specific OC-7 sub-rule applications. These overlaps need resolution when System 2 lands:

- **§2 (general future-date constraint) ↔ 7B.** Likely merge: §2 is the general rule, 7B becomes a specific instance for *STDAT fields. Or §2 retires and 7B absorbs.
- **§27 (sentinel exclusivity) ↔ 7I.** Same shape: §27 is the general pattern, 7I is the RACE-specific application. Likely keep both — §27 as cascade-resolved general rule, 7I as canonical reaffirmation for the RACE field.
- **§22 (note-after-YN) ↔ 7O.a (YES-BRANCH).** Same idiom written in two places. §22 retires when 7O.a covers it.
- **§23 (hidden-parent-context label) ↔ 7O.b, 7O.c, 7O.f.** §23 collects several relevance idioms into one rule; 7O distributes them. §23 retires when System 2 lands.
- **§25 / §26 / §28.** Unknown overlap shape until System 2's text is read. Plan accordingly.

## What is NOT decomposed in B.1c-final

- **OC-7's umbrella convention** (`clinical_patterns.universal_clinical_data_patterns.json` from B.1a) is **retained**, not removed. It serves as the catalog pointer and the prompt-rendering anchor. The 16 sub-rules are addressable independently for cascade resolution; the umbrella is for orchestrator-level discovery.
- **Stale duplicates of 7A–7P in skills/edc-builder/references/xlsform-build-rules.md and skills/protocol-analysis/references/xlsform-patterns.md** — deletion deferred to Phase C, per architecture doc §11.
- **Per-helper CF convention bundle.** 7L's seven pre-blessed helpers (SEX_CF, AGE_CF, WEIGHT_CF, ICFDAT_CF, ARMCD_CF, ENRLDAT_CF, BRTHDAT_CF) could each get their own structured convention with the source OID coordinates hard-coded. Deferred to B.1d.

## DSL operators used by B.1c-final conventions

- `has_field` (B.1c-1, commit `c9ef634`): used by 7E, 7M.
- `has_sibling` (B.1c-1): used by 7A, 7C, 7F.
- `match` (B.1c-2, commit `46ad938`): used by 7H.
- `default_value` (B.1c-3, commit `a161961`): **not used in B.1c-final.** 7F was originally planned to compose `match` + `default_value`, but the actual `prompts.py` rule uses a hard constraint (Grade-5 must equal Y), not a runtime default. `default_value` remains the operator-of-record for runtime-default rules; no current sub-rule needs it. Worth noting that all four B.1c DSL extensions were correctly motivated by OC-7's needs, even though `default_value` ended up being used by zero OC-7 sub-rules — its existence enables future runtime-default rules that didn't make it into this phase.

---

*End of plan. B.1c-final closes Phase B.1c. Phase B.1d (System 2 translation) is the next-but-not-imminent sub-phase.*
