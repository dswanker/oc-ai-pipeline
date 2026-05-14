# Conventions Store

This directory is the canonical home for OpenClinica build conventions
— the rules that shape how Claude generates Study Specifications and
EDC Builds.

## Directory layout

```
conventions/
├── README.md                  # this file
├── schema/
│   ├── version.txt            # store schema version (integer)
│   ├── convention.schema.json # JSON Schema for one convention record
│   └── dsl-operators.md       # human reference for applies_when / effect
├── global/                    # conventions that apply to every study
│   └── *.json                 # one file per convention
├── customers/
│   └── <subdomain>/           # one folder per customer (matches Monday OC Subdomain)
│       └── *.json             # customer-specific conventions
└── studies/
    └── <study_id>/            # one folder per study (matches study_meta.protocol_id)
        └── *.json             # study-specific conventions
```

Empty `global/`, `customers/`, and `studies/` directories are not
tracked in git — they spring into existence when their first
convention lands.

## Resolution cascade

When a build runs, the engine resolves conventions in this order:

1. Study-specific  (`studies/<study_id>/*.json`)
2. Customer-specific  (`customers/<subdomain>/*.json`)
3. Global  (`global/*.json`)

For each unique `natural_key` across all scopes, the most-specific
active convention wins. See `schema/dsl-operators.md` for the full
spec.

## Convention kinds

Three kinds, distinguished by the `kind` field:

- **structured**  — has `applies_when` and `effect`. Engine applies it deterministically.
- **hybrid**      — has `applies_when` and/or `effect` with `soft:` markers. Engine does the hard parts; Claude does the rest via prompt guidance.
- **advisory**    — no `applies_when` or `effect`. Pure prose guidance injected into Claude's prompt.

## Lifecycle

A convention's `status` field is one of:

- **proposed**  — drafted but not yet active. Engine ignores.
- **active**    — applied at build time.
- **archived**  — formerly active, retired. Engine ignores. File preserved for audit history.

Conventions are never deleted — only archived. Git history is the
audit trail.

## Adding a convention

1. Create a JSON file under the appropriate scope folder.
2. Validate against `schema/convention.schema.json`.
3. Open a PR. Reviewers check natural-key conflicts at promotion time.
4. After approval, flip `status` from `proposed` to `active`.

## Versioning

`schema/version.txt` holds an integer schema version. Breaking changes
to the convention record shape or DSL operators bump the version and
require a migration script. The loader rejects files with mismatched
versions until they're migrated.
