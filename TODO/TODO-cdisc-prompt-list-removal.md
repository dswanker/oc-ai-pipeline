# TODO: Stop Claude writing choice lists for CDISC-governed fields

**Priority:** Medium. **Added:** 2026-10-07. **Depends on:** CDISC CT layer (`ce45ae6`) proven in production.

## What
`EDC_STRUCTURE_PROMPT` in `prompts.py` still asks Claude to write choice lists for every select
field. Since `ce45ae6`, fields that default to CDASH get their list from CDISC deterministically
(`pipeline._apply_cdisc_ct` / `cdisc_ct.py`), so Claude's list for those fields is generated,
paid for, then replaced. Change the prompt so Claude emits the field with its CDASH variable name
and a placeholder list reference, and lets the pipeline fill the list.

## Why it was not done with the CT layer
1. It is the only part of the CDISC work that changes Claude's output. Everything in `ce45ae6`
   is deterministic and was verified offline (unit tests, EDC-builder QA before/after, idempotency).
   A prompt change can only be judged on real runs (learnings: 3+ runs, variance floor ±1.1pp,
   single-purpose prompt patches only).
2. Ordering risk. If Claude stops writing lists and the CDISC layer is skipped for a run
   (`CDISC_CT_APPLY=0`, standards unavailable, pinned CT version not fetchable), those fields
   reach `_fix_missing_choices` with no list and are deleted. The fallback must exist first.
3. Any prompt edit busts the prompt cache on the next run.

## Gate before starting
- Several production builds show the CDISC CT summary line in the monday log with no regressions.
- Decide the fallback: if the CDISC layer is skipped, either abort the build or re-request lists.

## Done when
- Prompt change shipped as a single-purpose patch, measured over 3+ runs.
- Token use per build measured before/after.
- A run with `CDISC_CT_APPLY=0` does not delete CDASH fields (fallback works).
