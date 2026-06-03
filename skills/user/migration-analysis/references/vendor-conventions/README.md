# Vendor Conventions

Per-vendor knowledge files used by `odm_to_spec.transform_with_ai()` to enrich
the AI prompt with vendor-specific behaviour that the deterministic transform
does not — and should not — encode.

## Why this exists

`migration/odm_to_spec.py` produces an OC4 Study Spec JSON deterministically
from an ODM intermediate. That transform is intentionally vendor-agnostic: it
only encodes the small set of rules (CDASH domain mapping, OC-1..OC-9, OID
shape) that apply to every input. Everything else — vendor namespace
attributes, hierarchical OIDs, log-line repeating semantics, longitudinal-arm
quirks, UUID-form identifiers — varies by source EDC and changes between
export versions.

Encoding those vendor quirks as code branches would:
- bloat the deterministic transform with conditionals,
- make every new vendor or new export version a code change + redeploy,
- bury knowledge that operations engineers need to read, not parse code for.

The convention files are the single source of truth for **how each vendor
behaves and how to map it to OC4**. They are loaded as text and embedded in
the AI enrichment prompt so Claude applies the right rules during the
enrichment pass without us re-shipping code.

## How they integrate

`odm_to_spec.transform_with_ai(odm_study, claude_client, protocol_bytes,
source_system)` calls `load_vendor_conventions(source_system)`, which:

1. Looks `source_system` (e.g. `"Medidata Rave"`) up in
   `VENDOR_CONVENTION_FILES`.
2. Reads the matched `.md` file and returns its full text.
3. Falls back to `generic_odm.md` if no match.
4. Returns `""` if the file cannot be read (graceful degradation — the
   enrichment still runs without the vendor section).

The text is interpolated into the `AI_ASSIST_PROMPT` block:

```
VENDOR-SPECIFIC CONVENTIONS FOR {source_system}:
{vendor_conventions_text}
```

Rules in the convention file take precedence over generic ODM handling but
remain subordinate to OC Standards OC-1 through OC-9, which always win.

## Adding a new vendor

1. Create `migration/vendor_conventions/<vendor>.md` using the structure
   below. Keep the section order — tests assert on heading presence.
2. Add an entry to `VENDOR_CONVENTION_FILES` in
   `migration/odm_to_spec.py`. The key is the exact string
   `odm_reader._detect_vendor` returns for that vendor (case-sensitive).
3. If `_detect_vendor` does not yet recognise the vendor, add a branch
   there too — match on `Originator`, `SourceSystem`, or namespace URI.
4. Add a fixture exercising the vendor under
   `tests/migration/fixtures/<vendor>_synthetic.xml` and a test class that
   parses + transforms it (see existing `medidata_rave_synthetic.xml`).

## Updating an existing convention

When a new export version surfaces new attributes or breaks an old
assumption:

1. Update the `Overview` line — bump the known EDC version.
2. Add/update the affected section — most commonly `Namespace`,
   `OID Conventions`, or `Form Structure Quirks`.
3. Capture the change in a one-line entry under
   `Known Export Limitations` or `OC4 Transform Rules` so future readers
   know which version introduced the behaviour.
4. If the change alters the deterministic transform, update the code too —
   conventions describe behaviour, they do not substitute for code fixes.

## Standard file structure

Every convention file MUST contain these sections, in this order, as level-2
markdown headings (`## …`). The test suite enforces this.

1. **Overview** — vendor name, product name, known EDC version(s), ODM
   version exported.
2. **Detection** — how `odm_reader` detects this vendor (Originator string,
   `SourceSystem` attribute, namespace URI).
3. **Namespace** — xmlns URI and the vendor-specific attributes the parser
   captures into `vendor_specific`.
4. **ODM Structural Patterns** — how this vendor's study/event/form/group
   structure maps onto standard ODM elements.
5. **OID Conventions** — how the vendor formats OIDs and what normalisation
   the OC4 transform applies.
6. **Form Structure Quirks** — repeating forms, log-line patterns,
   multi-group forms, matrix/folder constructs.
7. **Event/Visit Mapping** — how the vendor's events map to OC4 `SE_…`
   events, including OC-9 pinning where applicable.
8. **Codelist Handling** — vendor-specific codelist patterns (external
   dictionaries, decode language tags, etc.).
9. **Clinical Data Patterns** — how subject/event/form/item data is
   structured in `ClinicalData` exports.
10. **Known Export Limitations** — fields the vendor does not export,
    documented gaps, anything we have observed missing.
11. **OC4 Transform Rules** — explicit decisions of the form
    *"X in source → Y in OC4"*. This is the operational core.
12. **Compliance Notes** — anything relevant to GDPR, 21 CFR Part 11,
    ICH E6, audit trail expectations, signature handling.

Conventions are markdown so they read well in PR review and in the AI
prompt. Keep prose tight; favour bullet lists and short rules over
narrative.
