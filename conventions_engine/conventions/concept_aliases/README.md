# Customer concept aliases

Tell the pipeline which CDASH concept a customer's own field names represent, so CDISC edit checks and
terminology apply to non-CDASH forms. Read by `cdisc_concepts.py` before the conventions engine runs.
Highest precedence: an alias always beats a CDASH name match and a Claude-validated tag.

One file per customer, named like the customer's conventions folder (`conventions/customers/<name>`),
e.g. `bioIVT.json`:

```json
{
  "aliases": {
    "PMHYN": "MHYN",
    "SHORTFORM.COLLDT": "VISDAT",
    "IEINC01": "IEORRES:INCLUSION",
    "SMOKSTAT": "SUNCF:TOBACCO"
  }
}
```

Keys are `FIELD` (any form) or `FORM.FIELD`. Values are a CDASHIG variable, optionally `:QUALIFIER`
(test code, category or term). Unknown variables are ignored and counted in the build log.
This folder is not a conventions scope; files here are not loaded as conventions.
