# F2 Resolution — Vendor Cascade Axis

**Status:** Decided 2026-05-14. Implementation deferred to Phase B.1/C
per scoping below.
**Resolves:** Finding F2 in `conventions/_audit/B0_inventory.md`.
**Prior decision context:** B.0 audit catalog at commit `6f2b41a`.

## The problem

A pipeline build has two independent provenance facts:

- **Source vendor** — the EDC system the protocol/ODM was exported from
  (REDCap, Castor, Medidata Rave, …). Determines how input is parsed
  and normalized.
- **OC tenant** — the OpenClinica customer subdomain the forms will be
  uploaded to. Determines what house conventions the output must
  conform to.

The cascade as originally designed (`global > customer > study`)
assumes one specificity axis with customer = OC tenant. The B.0 audit
of `migration/vendor_conventions/*.md` revealed these files use
"customer" to mean source vendor, and a build is sourced from one
vendor and targeted at one tenant — two independent dimensions.

## Decision

**Add vendor as a peer-axis to customer in the cascade.** A new scope
kind `vendor` joins `global`, `customer`, `study`. The resolver walks
both `customer` and `vendor` scopes for any given build. Net cascade
shape:

```
       global
       /    \
    vendor   customer
       \    /
       study
```

Resolution order (most → least specific): `study > {customer, vendor} > global`.
Conflict between `customer` and `vendor` at the same `natural_key`
follows sub-decision A below.

## Sub-decisions

### A. Conflict tie-breaker (customer ↔ vendor)

**Customer always wins.** OC house conventions are the system's
identity; vendor rules adapt to them. Every customer-vendor conflict
auto-resolved this way is recorded in
`study_meta.conventions_engine_applied` so reviewers can see what was
overridden and write an explicit study-scoped convention if the
default policy is wrong for a specific build.

Rationale for not making it per-rule overridable: per-rule precedence
policies grow into unmaintained lookup tables (same anti-pattern as
the trainer rulebook). The escape hatch is the study-scoped layer,
which is already in the cascade and already wins over both.

### B. Vendor-source plumbing

The vendor identifier comes from the monday board column
`dropdown_mm382w7d` (already plumbed as `source_edc_system` in
`monday_client.py:50`). Column value is a display name like "REDCap"
or "Castor EDC".

The engine's resolver needs a slug (`redcap`, `castor`) for filesystem
lookup. Translation reuses the existing `VENDOR_CONVENTION_FILES` dict
in `migration/odm_to_spec.py:56-69` (display name → filename), strips
the `.md` suffix to produce the slug. No new mapping table.

`apply_conventions(...)` gains an optional kwarg:

    def apply_conventions(
        spec,
        study_id,
        customer_subdomain,
        migration_source=None,    # NEW: vendor slug, e.g. "redcap"
        repo_root=None,
    ): ...

`None` means non-migration build (fresh-protocol path). The resolver
skips the `vendor` bucket entirely in that case — symmetric with how
`customer_subdomain=None` behaves today for missing customers.

### C. Disposition of `migration/vendor_conventions/*.md`

**Transition window:** keep the existing markdown files in place.
`migration/odm_to_spec.py:860` (call to `load_vendor_conventions()`)
continues feeding them into the AI enrichment prompt unchanged.

**Phase B.1:** ~36 truly vendor-specific rules get translated into
schema-conformant JSON files at `conventions/vendors/<slug>/*.json`.
The reaffirmations of OC-8/OC-9/cdash_field_naming/oid_normalisation
(~37 rows in B.0 audit) are *not* translated — the cascade resolves
them from `global/` automatically.

**Phase C:** `odm_to_spec.py`'s `load_vendor_conventions()` is
rewritten to assemble its prompt fragment from the engine's hybrid
bucket for the current vendor scope. The markdown files in
`migration/vendor_conventions/` are deleted in the same commit. Single
source of truth, no drift.

Belt-and-suspenders during transition. No permanent duplication.

## What this excludes

**CQ_* columns on the monday board stay where they are.** They have a
working extensibility surface (`_want()`-style discovery in
`pipeline.py`; add a column, pipeline picks it up), and the data model
is "values per study run" not "rules across study runs." They do not
benefit from the cascade. Phase B/C may revisit *whether the question
definitions themselves* belong in the cascade as advisory conventions,
but the per-study answers remain on monday rows.

## Storage layout after Phase B.1

```
conventions/
├── README.md
├── _audit/
│   ├── B0_inventory.md
│   └── F2_resolution.md         ← this file
├── schema/
│   ├── convention.schema.json
│   ├── dsl-operators.md
│   └── version.txt
├── global/
│   └── *.json
├── vendors/                     ← NEW (Phase B.1)
│   ├── castor/
│   │   └── *.json
│   ├── redcap/
│   │   └── *.json
│   └── ...
├── customers/
│   └── <subdomain>/
│       └── *.json
└── studies/
    └── <study_id>/
        └── *.json
```

`vendors/<slug>/` mirrors `customers/<subdomain>/` — folder per scope
entity, JSON file per convention, README.md per folder.

## Engine changes required (Phase B.1)

1. `conventions_engine/loader.py` — add `load_vendor_set(repo_root, slug)`
   mirroring the existing `load_customer_set`.
2. `conventions_engine/cascade.py` — extend `resolve()` to gather from
   `vendors/<slug>/` when `migration_source` is non-None; merge into
   the resolved set with customer-wins tie-breaking.
3. `conventions_engine/intersection.py` — extend conflict detection to
   surface customer-vs-vendor conflicts in a separate record from
   intra-scope conflicts (so the spec output can distinguish them).
4. `conventions_engine/record.py` — extend
   `study_meta.conventions_engine_applied` shape to record
   `customer_vendor_conflicts: [{natural_key, customer_id, vendor_slug,
   winner: "customer", losing_effect_summary}]`.
5. `pipeline.py` — three call sites pass `migration_source=<slug>` when
   `source_edc_system` is present on the monday item; reuse the
   `VENDOR_CONVENTION_FILES` dict for the slug lookup.

**Patch 3 implementation note (2026-05-15):** The
`customer_vendor_conflicts` bucket landed in
`conventions_engine/record.py`, not `intersection.py` as originally
stipulated in item 3 above. The cascade resolver (Patch 2) already
detects the customer-over-vendor collision and surfaces the losing
convention in the winner's `overrode[]` list; record.py runs at the
right point in the build pipeline to elevate that into a top-level
report bucket. intersection.py remains for promotion-time
peer-convention conflict detection — a different concern.

Tests follow the same coverage pattern as the existing 110 unit tests
in `tests/conventions/`.

## Sequencing

Phase B.1 splits into two sub-phases:

- **B.1a — Translate System 1 (the 11 OC-N rules) first.** Global
  scope only; doesn't touch the vendor axis. Builds the
  prompts.py-rule → conventions/global/*.json translation pattern
  that B.1b reuses for vendor.
- **B.1b — Vendor-axis engine work + System 4 vendor translation.**
  The 5 engine changes above, then translate the ~36 vendor-specific
  rows from B0_inventory.md System 4 row catalog into
  `conventions/vendors/<slug>/*.json`.

## What this defers

- Actual translation of the ~36 vendor-specific rules into JSON
  records (Phase B.1b work).
- The Phase C cutover that deletes `migration/vendor_conventions/*.md`
  and rewires `odm_to_spec.py`.
- Any per-rule precedence policy (explicitly excluded; not coming back).
