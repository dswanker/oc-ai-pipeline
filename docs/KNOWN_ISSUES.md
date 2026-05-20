
## Trainer Board Duplicate Rows (NOT YET FIXED)

**Issue:** The trainer board (`18410424473`) is creating duplicate rows for the same protocol when a study is run multiple times through the AI Hub. There should be deduplication logic to prevent this.

**Expected behavior:** Each unique protocol should have only ONE row on the trainer board, updated on each run rather than creating new rows.

**Current behavior:** Multiple rows are created for the same protocol (e.g., two PrTK05 rows observed on 2026-05-20).

**Deduplication logic location:** Likely in `pipeline.py` around the trainer row creation code (search for `create_pending_row` or trainer board item creation).

**Priority:** Medium - clutters the trainer board but doesn't break functionality.

**Status:** Deferred - will address after resolving current form upload workflow issues.

**Date reported:** 2026-05-20
