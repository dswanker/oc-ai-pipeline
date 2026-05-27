# TODO — study-build-trainer skill duplication / drift

## Status
OPEN — high priority. Identified during the B-report (SOE arm) work, May 2026.

## The finding
`services/study-build-trainer/skills/protocol-analysis/` contains a full, independently-bundled copy of the `protocol-analysis` skill. That copy has drifted behind the canonical `skills/protocol-analysis/`. Confirmed example: the dead `"CTL"`-string arm heuristic was removed from `skills/protocol-analysis/scripts/generate_study_spec_pdf.py` (lines 244–246 in the canonical) but still exists in the trainer's copy at `services/study-build-trainer/skills/protocol-analysis/scripts/generate_study_spec_pdf.py:505–506`, and the trainer's `generate_protocol_summary_pdf.py:449` still has the related heuristic.

## Why this matters (the real concern)
The design intent was that the trainer consumes the main pipeline's outputs so its accuracy scoring measures the live builder. The repo shows the opposite — the trainer bundles and (likely) runs its own frozen copy. If true, the trainer is scoring against an outdated version of the pipeline, which silently undermines the accuracy work. This reframes the issue from "sync a heuristic" to "verify the trainer's architecture matches its design intent."

## First diagnostic step
From the repo root, list every file that differs between the canonical and trainer copies:
diff -rq skills/protocol-analysis services/study-build-trainer/skills/protocol-analysis

Then `diff -u <a> <b>` per file of interest to see the actual drift. This scopes the work.

## Open questions to answer next
1. Does `study-build-trainer` actually invoke the bundled skill (spec / build / summary generation), or does it only consume outputs the main pipeline already produced?
2. If it invokes the bundled skill, why a separate copy instead of importing/sharing the canonical one?
3. How much accumulated drift is there beyond the arm heuristic (the diff above answers this)?

## Possible resolutions (decide after the diagnostic)
- Trainer reads main-pipeline outputs only → delete the duplicate skill tree.
- Trainer needs to invoke skill code → make `skills/` the single source of truth (symlink, package import, or build-time copy step) so drift is impossible by construction.
- Trainer intentionally needs a pinned snapshot → document that explicitly and add a sync mechanism with a visible version.

## Related
- Scoping doc: `docs/calendaring-integration-scope.md`
- B-report fix that exposed this: `skills/protocol-analysis/scripts/generate_study_spec_pdf.py` (uncommitted at the time of this TODO)
