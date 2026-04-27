# TODO — Iterate Further on DVS UAT_Cases Input Data Quality

**Status:** Identified, deferred — not blocking current work.
**Created:** 26 April 2026
**Origin:** After commit `c72d307` (DVS placeholder replacement + cross-form
date continuity) shipped, a real-world re-run of CRS-138 confirmed the
Input Data column is significantly better but still not perfect.

---

## Background

In commit `c72d307` we replaced these placeholder patterns with concrete data:
- `<sample>` → protocol-tailored values (e.g. `AETERM=Injection site pain`)
- `<value that satisfies the rule>` / `<value that fails the rule>` → values
  parsed from the constraint expression
- `(choose any option)` → first valid choice
- `(value meeting/violating the constraint)` → typed concrete values
- `Set ICFDAT_CF to a base date first, then set this to ...` →
  `ICFDAT_CF=2026-01-20, then this date=2026-01-25` (with cross-form
  continuity so the same field gets the same value across all UAT cases
  that reference it)

A baseline timeline (`_BASELINE_TIMELINE` dict) was added with realistic
clinical-trial dates for ICFDAT, AESTDAT, CMSTDAT, etc.

## What still isn't right

When a real CRS-138 re-run was performed after deployment, the UAT Input
Data column was noticeably better but still has quality issues that need
another iteration to fully resolve.

**Specific examples to investigate** (collect during next pass):
- TBD — capture concrete bad cells with their UAT Case ID and field name
- TBD — note any patterns where placeholders remain or values are nonsensical
- TBD — note any cases where cross-form continuity broke

**Possible failure modes to look for:**
- Edge-case constraint expressions the gate evaluator doesn't parse
  cleanly (e.g. nested `or`, function calls like `regex()`, `count()`)
- Field names not covered by `_build_sample_context()` falling back to
  generic "Sample text" or "1"
- Numeric reference-bound checks where the `_BASELINE_TIMELINE` doesn't
  have an entry, allocating arbitrary `100` or `2026-03-15` defaults
- Calculation cases with > 4 referenced fields (current cap)
- Y/N / boolean fields that get numeric satisfies/fails values
- Date arithmetic edge cases (leap years, month boundaries, date types
  the parser doesn't recognize)
- Cross-form references where the referenced form hasn't been "visited"
  yet in the test sequence
- Choices with non-alphabetic codes (e.g. units like "mg/dL", multi-word
  labels)

## Approach when we return to this

1. **Take a fresh DVS output** from a real pipeline run — preferably 2-3
   different protocols (CRS-138 plus one with different therapeutic area).
2. **Manually review the entire UAT_Cases sheet** and flag every cell
   that still has placeholders, generic fallbacks, or nonsensical data.
3. **Categorize the failures** — which are extractor bugs, which are gaps
   in `_BASELINE_TIMELINE`, which are gaps in `_build_sample_context()`,
   which are unparseable constraint expressions.
4. **Patch in priority order:**
   - High-frequency patterns first (something appearing 50 times matters
     more than something appearing once)
   - Easy fixes second (obvious context additions)
   - Hard fixes (XPath parser improvements) only if frequency justifies
5. **Add unit tests** for each new pattern handled.
6. **Re-run the same protocols** and confirm the metrics improve.

## Why this is deferred

- Current output is already a major improvement over the previous version
  (`<sample>` everywhere)
- Pipeline is producing usable DVS files for the demo / current work
- Bigger priority right now: building the UAT Runner microservice (Phase 0
  scaffolding, then DVS parser, then OC API client, etc.) — see
  `FUTURE-PROJECT-uat-runner.md` for the full plan
- Iterating on input data quality is best done when we have real customer
  feedback or test failures pointing at specific issues, rather than
  speculatively trying to perfect every edge case

## Trigger conditions

Pick this back up when:
- Customer feedback flags specific bad cells
- UAT Runner Phase 1 (DVS parser) needs cleaner input to test against
- A new protocol with a different therapeutic area exposes
  context-coverage gaps in `_build_sample_context()`
- Time available for a focused 2-4 hour cleanup pass

## Files involved

- `skills/dvs-specification/scripts/extract_dvs_from_forms.py` — main
  extractor and helper functions
- `skills/dvs-specification/scripts/generate_dvs.py` — XLSX writer (probably
  no changes needed here)

## Related docs

- `TODO/FUTURE-PROJECT-uat-runner.md` — the UAT Runner microservice plan
  (which depends on high-quality DVS input but doesn't strictly require
  perfection — Runner can flag/skip cases with bad input data)
