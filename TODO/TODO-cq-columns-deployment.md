# TODO — Convention Questions Deployment (CQ_* columns)

**Status:** Pending team review
**Priority:** Medium
**Created:** 2026-05-18
**Origin:** During CRS-136 iteration we identified a set of upstream
"Convention Questions" the AI Hub should ask the requester to disambiguate
build choices before the pipeline runs. Six are deployed and working; the
remaining 40 are queued behind a team review of the question catalogue.

---

## Summary

- **Description:** Deploy remaining 40 CQ_* columns (46 total across
  9 categories) to the AI Hub Monday board (board id `18409146946`).
- **Current state:** 6 CQ_* columns already deployed and working.
- **Pipeline support:** Already built-in — the pipeline auto-detects any
  column whose id starts with `color_mm…` and whose title starts with
  `CQ `. Zero code changes needed to ship more.
- **Blockers:** Need team meeting to review the proposed question catalogue.

## Blockers

The proposed question catalogue lives in
`EDC_Build_Questions_For_Team_Review.xlsx` (referenced in this entry's
description). **Note:** this file is not currently checked into the repo —
either locate the working copy and check it in, or recreate it before the
review meeting.

## Next steps

1. Schedule team review meeting (30–60 min).
2. Review `EDC_Build_Questions_For_Team_Review.xlsx`.
3. Mark each question: ✅ Deploy / ⏸️ Maybe Later / ❌ Skip.
4. Create approved columns on the board (Monday GraphQL `create_column`
   mutation — direct API via `$MONDAY_API_TOKEN`, per repo memory rule).
5. Test on CRS-136 (item `11894915700`).
6. Document for users (optional tooltips / requester-facing guide).

## Benefits

- More accurate EDC builds (fewer reviewer back-and-forth cycles).
- Fewer pipeline iterations per study.
- Faster delivery.

## Related files

- `EDC_Build_Questions_For_Team_Review.xlsx` (not yet in repo — see Blockers)

## Related context

- Six CQ_* columns currently live on item `11894915700` (Pacira CRS-136),
  all with title `CQ ...` and type `status`. Examples:
  `color_mm31v560` (early termination), `color_mm311j7v` (DOV collected),
  `color_mm303mzg`, `color_mm30r2d8`, `color_mm30e0m0`, `color_mm30jxgj`.
  The board column-listing GraphQL query is the source of truth for the
  full current set.
