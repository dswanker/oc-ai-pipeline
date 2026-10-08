# OC Standard Form Matching Plan

**Status (2026-10-09):** rules corrected and decided by Dan; implementation starting. Original 2026-07-28 design kept
below for history; where they differ, the "Agreed rules (2026-10-09)" section wins.

## Agreed rules (2026-10-09)

Input column: `file_mm2mafjc` (`COL["oc_standard"]`, "Customer OC4 XLSForm Standard(s)").

1. **Match** each protocol form to a customer standard form by **CDASH domain** (e.g. `AEGEN` matches AE).
2. **On a match, use the standard form exactly as provided:** every field, choice list, constraint, required flag,
   relevance (show-when) and calculation. All logic and relevance checks in the standard are kept.
3. **Add a field only when the protocol clearly and directly specifies that data point** and the standard form does
   not have it. The added field uses its CDASHIG definition plus any logic the protocol states for it.
4. **If the protocol does not specify the data points / Biomedical Concepts for that form, change nothing:** the
   standard is used as provided.
5. **Never remove** a field from the standard form, even if the protocol does not mention it.
6. **Provenance:** form-level "Customer standard" (`library_match.status = "CUSTOMER_STANDARD"`, matched form name in
   `customer_form_name`), field-level "Added from protocol" for added fields; shown in the Study Specification and the
   Protocol Summary.
7. **Rules-engine checks on matched standard forms are proposals only (decision b):** CDISC / global / customer
   conventions-engine checks that would add or change logic on a customer-standard form are NOT applied; they appear
   in the DVS (DVS_OC4) as Status "Proposed" with their source, for a DM to Approve (same path as AI-proposed checks).
   CDISC controlled terminology already never overrides these fields (protected choice lists).
8. Runs **immediately after protocol analysis, before anything is built** (same hook as `_ensure_required_forms`).

### Accepted inputs
- Deterministic matching: **XLSForm `.xlsx`** (single files and ZIPs of them) and **ODM XML** study metadata
  exports (OC3/OC4, including OpenClinica extensions for logic).
- PDF / Word / CSV form documents cannot be matched deterministically: they belong in the CRF Standards column
  (AI guidance).
- Fix: today a single `.xlsx` and a `.docx` are both misdetected as a ZIP of XLSForms (both are zip containers) and
  silently produce nothing; every file in the column must be either used or named in the monday log as not usable.
- Today the column's content is only pasted into the analysis prompt (150,000-character cap per file) and Claude
  decides what to use; the only deterministic use is protecting its choice lists from CDISC CT.

### Test cases
- **PrTK05** (item 11779964503, cust1, `S_PRTK05(TEST)`): its `oc_standard` is `PrTK05StudyMetadata.xml`, the ODM
  XML of its own existing build, so a correct match should reproduce those forms almost exactly.
- **CRS-135** (item 11894915699, cust1, `S_CRS135_7414(TEST)`): no `oc_standard`; the pure-protocol counterpart.
- Both get a full rerun with fresh protocol analysis after the matching is built (keep a copy of each saved spec JSON
  first for a before/after comparison). Publishing / UAT on cust1 needs a cust1 browser login (the saved one is for
  the BioIVT tenant).

---

# Original design (2026-07-28)


**Status:** Design confirmed with Dan (2026-07-28), ready for scoping into
implementation stages. Not yet started.
**Depends on:** the `oc_standard` content-attachment fix already shipped
(commit on `pipeline.py` ~4538 — customer's ODM XML / XLSForm ZIP now
actually reaches the Study Spec generation prompt, whereas before it was
silently never attached).

---

## 1. Problem

When a customer has an existing OpenClinica build (`file_mm2mafjc` /
`COL["oc_standard"]`) that already contains a form the current protocol
also needs (e.g. `VS`, `AE`), the pipeline should use that **exact existing
form definition, unchanged** — not regenerate something merely "inspired
by" it. Today, even with the content-attachment fix, there is no
deterministic mechanism enforcing this: the Study Spec extraction (Step 1)
is a single free-form Claude call that treats the standard as "Priority 1
guidance," which influences but does not guarantee identical output.

**Sequencing requirement (confirmed by Dan):** the match/reuse decision
must happen before the Study Specification documents and the actual XLSForm
build are produced — whatever the JSON says was reused verbatim, it must
be what actually got built. Since "which forms does the protocol need" is
itself an output of Step 1 (there's no earlier point where that's known),
the practical fix is: run a **deterministic step immediately after Step 1
produces its JSON, before that JSON is used for anything else** (before
Chain A builds the Study Spec PDF/XLSX, before Chain C builds the actual
XLSForms). That single intervention point guarantees every downstream
artifact — JSON, Study Spec doc, actual build, Protocol Summary — reflects
the same, corrected data.

## 2. Confirmed matching/merge rules (from conversation with Dan)

- **Match key:** CDASH domain, not just exact `form_id` string equality.
  Example: protocol needs an `AE` form; customer's standard has
  `AEGEN.xlsx` — different name, same CDASH AE domain — still a match.
- **On match:** use the existing form's fields **exactly as they are**, no
  modification.
- **On partial match** (existing form is missing a field the CDASH domain
  standard defines): **add** that field via the CDASH domain reference —
  never omit it, never flag for manual review. This is a per-field
  operation layered on top of a per-form match decision.
- Applies to **both** `oc_standard` file types (ODM XML and XLSForm ZIP) —
  a fix that only works for one file format isn't a real fix, since
  whether reuse works can't depend on which format a customer happened to
  upload.
- Provenance must show in **two** documents, not one: the Study
  Specification (what's being built) and the Protocol Summary (which
  currently has no working way to say "this form came from the customer's
  existing build vs. was generated from CDASH standard").

## 3. Major finding: the schema and doc-rendering side already exist

This is not new infrastructure to invent — it's an existing, proven
pattern that's underused. Confirmed by reading the actual code:

### Study Specification side
- Every form in the Step 1 JSON output already carries:
  - `form_category`: `"ADMINISTRATIVE" | "CDASH_CLINICAL" | "CDASH_SAFETY" | "INFRASTRUCTURE" | "CUSTOM"`
  - `cdash_domain`: e.g. `"DM"`, `"AE"` — **Step 1 already classifies each
    form's CDASH domain itself**, which is exactly the match key needed.
  - `library_match`: `{status, source_type, fields_from_library,
    fields_extended_from_protocol, fields_from_cdash_default}` — a
    form-level provenance object. Existing observed `status` values:
    `"CDASH_DEFAULT"`, `"PROTOCOL_ONLY"`.
  - Per-field `library_source`: `"CDASH_DEFAULT" | "CDASH_STANDARD" |
    "PROTOCOL_SPECIFIC" | "CUSTOM"` — `"CUSTOM"` already exists as a value
    and is the natural tag for a field copied verbatim from a customer's
    `oc_standard`.
- `skills/protocol-analysis/scripts/generate_study_spec_pdf.py` (lines
  ~638, 678, 699, 701) **already reads and renders** `library_match` and
  `form_category` in the actual PDF output. The rendering side is done;
  only the data populating it (for real, non-fixture runs) needs to be
  accurate.

### Protocol Summary side
- Same underlying schema (`prompts.py:~1224`, same Step 1 JSON — this is
  **not** a separate generation flow from Study Spec, despite living in a
  differently-named document/skill). Per-domain-row fields already
  include: `domain_name`, `cdash_code`, `source` (observed value in
  fixtures: `"CDASH_ESTIMATE"`), **`customer_form_name`** (present in the
  schema, always `None` in current fixture/example data — this field is
  *designed* to hold the matched customer form's name and is simply never
  populated by real matching logic today), `reuse_count`, `confidence`,
  `notes`.
- **One fix, both documents**: since both documents read from the same
  Step 1 JSON, populating `library_match`/`library_source`/`cdash_domain`
  correctly via the deterministic post-processing step (§4) automatically
  feeds both the Study Spec PDF and the Protocol Summary PDF. No separate
  work needed per-document.

### Precedent pattern already in production
`_ensure_required_forms()` (`pipeline.py:3684`) is called at 4 sites,
always immediately after Step 1's JSON is produced
(`struct_json = _ensure_required_forms(struct_json, protocol_num)`),
always before Chain A/Chain C consume it. It deterministically checks
`existing_ids = {f.get("form_id") for f in forms}` and, if a required
form (currently only `DOV`, hardcoded) is missing, **injects a complete,
literal form definition** — survey/choices/settings, with
`library_match: {"status": "CDASH_DEFAULT", ...}` and per-field
`library_source: "CDASH_DEFAULT"` already set correctly.

**This is exactly the mechanism needed for oc_standard matching** — same
hook point, same JSON-splicing approach, same provenance-tagging
convention. The new work is: instead of one hardcoded form (DOV), compare
dynamically against a parsed catalog of the customer's actual
`oc_standard` upload, and instead of only ever injecting missing
forms, also **overwrite** forms Step 1 already generated when a real
customer match exists.

## 4. What's actually new work (the genuine gaps)

1. **ODM XML → per-form parser.** `_read_zip_xlsforms()`
   (`pipeline.py:440`) already handles the ZIP case, returning
   `{filename: {survey, choices, settings}}`. No equivalent exists for
   ODM XML — needs a new function parsing `FormDef`/`ItemGroupDef`/
   `ItemDef`/`CodeListRef` into the same shape, keyed by form OID. This is
   the reverse direction of the already-planned `xls_to_odm.py` migration
   work (XLS→ODM) — worth checking whether any of that tooling's XML
   handling can be reused/mirrored rather than starting fresh.
2. **Domain matching function.** Given Step 1 already tags each generated
   form with `cdash_domain`, matching means: for each parsed
   `oc_standard` form, determine its own CDASH domain (via filename/OID
   heuristics against a domain-code table — extending the existing
   `_CDASH_LABELS` dict at `pipeline.py:3640` is a reasonable starting
   point — or a settings-sheet field if one reliably encodes it), then
   compare against Step 1's `forms[].cdash_domain`. Match → splice.
3. **The splice function itself** —
   `_apply_oc_standard_matches(spec: dict, oc_standard_catalog: dict) -> dict`,
   called at the same 4 sites as `_ensure_required_forms`, likely
   immediately before or after it:
   - For each form in `spec["forms"]` with a domain match in the catalog:
     replace its `survey`/`choices`/`settings` with the catalog's exact
     values (all fields tagged `library_source: "CUSTOM"`); set
     `form.library_match = {"status": "CUSTOMER_STANDARD", "source_type":
     "CUSTOMER_STANDARD", ...}` (new status value, following the existing
     enum pattern); set `form.form_category` appropriately; populate
     whatever field feeds the Protocol Summary's `customer_form_name`
     with the matched form's original name/filename from `oc_standard`.
   - Gap-fill: for any CDASH-standard field the domain defines that the
     matched existing form lacks, append it (tagged
     `library_source: "CDASH_STANDARD"` or `"CDASH_DEFAULT"`, matching
     existing convention) — same splicing pattern
     `_ensure_required_forms` already uses for DOV.
4. **CDASH domain field reference** (needed for gap-filling, item 3
   above). The literal `CDASH_DEFAULT` field definitions visible in
   `generate_study_spec_pdf.py` (~line 1216+) are test/example fixtures
   for the PDF renderer itself, **not** a genuine reusable reference
   table — this needs to be real, sourced content (which CDASH-standard
   fields exist per domain: DM, AE, VS, LB, CM, MH, DS, PE, IE, etc.),
   most likely built out incrementally per domain as real customer
   standards are processed, rather than attempting to pre-build a
   complete CDASH field library upfront.

## 5. Open design questions to settle before implementation

- **Domain classification for the customer's existing forms** — is there
  ever a reliable structural signal (a settings-sheet field, an OID
  convention) that already encodes CDASH domain in real customer
  `oc_standard` uploads, or does this always need a heuristic/LLM
  classification pass? Worth checking against a real customer ZIP/XML
  before assuming either way.
- **New `library_match.status` value** — proposing `"CUSTOMER_STANDARD"`
  to sit alongside the existing `"CDASH_DEFAULT"` / `"PROTOCOL_ONLY"`
  values. Confirm this is the right name/doesn't collide with anything
  downstream that pattern-matches on status strings.
- **Partial-domain-match edge case not yet discussed:** what if the
  customer's existing form for a domain covers *more* than the protocol
  needs (e.g. their `AE` form has 40 fields, protocol only implies ~15)?
  Current rule ("use existing form exactly as-is") suggests keeping all
  40 rather than trimming — worth explicitly confirming that's intended,
  since it differs from the "add missing CDASH fields" case which is
  additive-only in the other direction.

## 6. Suggested implementation order

1. Confirm/resolve §5's open questions.
2. Build the ZIP-side matching (`_read_zip_xlsforms` catalog already
   exists) end-to-end first — smaller surface area, validates the
   splice/provenance-tagging approach on real data before tackling XML.
3. Add the ODM XML parser, wire it into the same matching function so
   both file types share one splice implementation.
4. Validate against real customer data on both Study Spec PDF and
   Protocol Summary PDF outputs — confirm `customer_form_name` and
   `library_match` render correctly, not just that the JSON looks right.
