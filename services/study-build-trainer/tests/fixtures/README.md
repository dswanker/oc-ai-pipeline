# Test fixtures

Sample form designs and protocols used by the test suite.

Each fixture should be small (< 1 MB ideally) and represent a realistic
but de-identified clinical trial. Keep enough variety here to exercise
each parser:

- `*.odm.xml` — CDISC ODM exports
- `*.xlsx` — XLSForm files (single-form and multi-form packs)
- `*.pdf` — annotated CRFs, study spec PDFs

Fixtures are committed to the repo (small, public-safe data only).
Do NOT commit anything customer-specific or under NDA.
