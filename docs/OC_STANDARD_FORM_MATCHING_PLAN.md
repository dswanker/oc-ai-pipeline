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

## Implementation status (train of 2026-10-09)

| Item | What | Status |
|---|---|---|
| 0 | One test command (`pytest`) runs all of `tests/` | Done |
| 1 | Deterministic matching core for uploaded files (`standards_match.py`) | Done |
| 2 | AI logic for logic-free standards and gap-filling (`ai_standard_logic.py`) | Done |
| 3 | "Reference OC Studies (up to 5)" (referenced studies as a source, `reference_studies.py`) | Done |

### Item 1: what was built

- **Detection by content** (`standards_match.detect_kind`): workbook with a `survey` sheet = single XLSForm; ZIP with
  `.xlsx` (or ODM `.xml`) members = XLSForm ZIP; ODM XML; everything else (`.docx`, a workbook that is not an XLSForm,
  PDF, text) = unstructured and keeps the text path. Every file is used or named in the monday log
  ("Customer OC4 standards: ...; <file>: NOT usable for form matching (...)").
- **One normalised standard-form model** `{form_oid, title, domain, survey, choices, settings, extra_cols, has_logic,
  source, source_file, format}` from XLSForm (single + ZIP) and ODM XML (FormDef, ItemGroupRef/Def, ItemRef/Def with
  DataType, Question, Mandatory, CodeList, OpenClinica:MultiSelectList; item group names in `bind__oc_itemgroup`).
  ODM carries structure only: `has_logic` False.
- **Domain of a standard form**: form OID equal to a CDASH domain; else the CDASH variables its fields are named
  after (CDASHIG, the same deterministic resolution as `cdisc_concepts`), needing a majority and at least 3 fields or
  a quarter of the form; else the form title; else OID prefix backed by a field. Every basis is recorded.
- **Matching**: by CDASH domain, one standard form per protocol form. Source priority: uploaded XLSForm > referenced
  studies in listed order > uploaded ODM (the same form from a lower source is superseded). Several candidates in a
  domain: best name similarity (same form id, else titles), logged with the alternatives and marked AMBIGUOUS when
  close. Forms the domain pass leaves over are matched when the form id is identical (or the titles are, when the
  two do not both have a domain); that basis is logged as such. `STANDARDS_MATCH_BY_NAME=0` turns this second pass off.
- **Splice**: the protocol form keeps its visit placement and scheduling; survey, choices, settings and extra columns
  are the standard's, exactly. The form takes the standard's form id (references in `schedule_of_events` and other
  forms' cross-form dependencies follow). The protocol-analysed form is kept in `standards_originals` so a changed set
  of sources can be re-matched. `study_meta.standards_match` holds the fingerprint of the sources, every decision,
  forms without a standard, standard forms not used, proposals and rejected ids.
- **Fields the protocol specifies (rule 3)**: candidates are fields of the analysed form that the standard does not
  have, compared by CDASH concept, then by data point (same numbered item, one name part of the other, near-identical
  label). One validated AI call: for each candidate the model must name the standard field that covers it, or return
  a verbatim protocol quote. A field is added only when nothing covers it and the quote is found in the protocol text
  (whitespace-normalised). Protocol text comes from `pypdf`; a scanned protocol yields no text and nothing is added.
  `STANDARDS_ADD_FIELDS_AI=0` skips the call. Added fields carry `provenance: "Added from protocol"` and the quote.
- **Nothing downstream changes a matched form**: CDISC CT / CDASH / QRS, missing-choices clean-up, title clean-up,
  CRF-standards injection and OMOP coding skip it; the form builder writes an XLSForm-sourced standard verbatim
  (layout rows, every column, settings; only the version stamp is ours) and never regenerates a standard form.
  `standards_match.integrity()` re-checks the content hash before the build; any difference is in the monday log.
- **Rules engine (rule 7)**: see `conventions_engine/customer_standard.py` and the DSL doc. Proposals are DVS_OC4
  rows with Status "Proposed" and Check Source "CDISC CORE (id)" / "CDISC Standard" / "Global Rule" / "Customer Rule";
  Approve applies them through `dvs_edits` (`standards_match.apply_proposal`). The standard's own logic shows Check
  Source "Customer Standard".
- **Analysis context**: structured standards are no longer pasted in full. A compact catalog (form, title, domain,
  field names) is injected instead; unstructured files keep the text path.
- **Kill switches**: `STANDARDS_MATCHING=0` (no matching; full-text paste as before), `STANDARDS_ADD_FIELDS_AI=0`,
  `STANDARDS_MATCH_BY_NAME=0`. On any exception the spec is returned unchanged.

### Item 2: AI-suggested logic (rule 8)

- `ai_standard_logic.py`, one call per run and per set of sources (`study_meta.standards_match.ai_logic` holds the
  fingerprint), after the rules engine and only when the DVS or the build is requested. Kill switch
  `AI_STANDARD_LOGIC=0`. The build continues if the call fails.
- Targets: matched forms with no constraint or show-when at all (every ODM-only standard), and fields with gaps:
  a date without a check, a number without a range, conditional wording ("if other, specify") without a show-when.
- Structured output only; the module builds the expressions. Kinds: `future_date`, `date_order` (two dates on the
  same form), `range` (accepted only with a verbatim protocol quote found in the protocol text), `relevant` (shown
  for one real choice code of a select field on the same form).
- Validation rejects: a form that is not a matched customer standard form, unknown fields, incompatible types, bad
  operators, choice codes that do not exist, a range without a verified quote, logic the field already has, logic
  the rules engine already proposes, anything a DM rejected before, duplicates, more than 80 suggestions.
- Accepted suggestions are DVS_OC4 rows, Status "Proposed", Check Source "AI-Suggested". Approve applies them
  through `dvs_edits` (`standards_match.apply_proposal`); Reject keeps them from being proposed again. The
  standard's own logic is never changed.

### Item 3: "Reference OC Studies (up to 5)"

- monday TEXT column "Reference OC Studies (up to 5)", id `text_mm7yy8kw`, `monday_client.COL["reference_studies"]`
  (created by Dan). Value: up to 5 study names / unique identifiers / OIDs, comma-separated; more than 5: the first
  5 are used and that is logged.
- Resolved on the ITEM's own subdomain (`text_mm3aa7cx`) with the service account (`OC_API_USERNAME` /
  `OC_API_PASSWORD`). READ-ONLY; only these calls (verified 2026-10-09 against cust1 / PrTK05):
  1. `POST https://{sub}.build.openclinica.io/user-service/api/oauth/token` json `{username, password}`; the token
     is the response text.
  2. `GET https://{sub}.build.openclinica.io/study-service/api/studies?page=0&size=1000` (Bearer); header
     `X-Total-Count`; paged when there are more. Matched on `uniqueIdentifier`, then `name`, then OID: exact
     (case-insensitive) first, then a unique contains-match. Ambiguous or not found is logged and skipped.
  3. Board id = the segment after `/b/` in `currentBoardUrl` (not the slug).
     `GET https://{sub}.design.openclinica.io/api/boards/{boardId}` -> `cards` (forms).
  4. Per distinct `formOcoid` among non-archived cards: the version whose `ocoid` equals the card's
     `selected_form_version_ocoid`, else the latest non-archived; `uploadedFileLinks[0]` and the encrypted key from
     `previewURL`. `GET https://{sub}.build.openclinica.io/form-service/api/encrypted-versions/{key}/artifacts/{file}`
     returns the original XLSForm with all its logic. Forms with no version are listed as "no uploaded form".
- The artifact URL works without sign-in: it is never logged or stored. Logs name studies, forms and files only.
- 6 downloads at a time, 30 s per request, one retry, timing per study in the log; the fetch never fails a build.
  Fetched once per run. Kill switch `REFERENCE_STUDIES=0`.
- Fetched forms are sources labelled "referenced study <identifier>", between an uploaded XLSForm and an uploaded
  ODM, in listed order. Two forms of one study whose ids differ only by the `F_` prefix count as the same form; the
  first on the board is used and the other is logged as superseded.
- cust1 findings (2026-10-08): 509 studies listed in about 2 s; PrTK05: 14 forms fetched in 2.5 to 3.9 s, 23 form
  ids on the board have no uploaded version. The study list has no OID field on cust1, so a reference by OID cannot
  resolve there. "CRS-135" is not an exact identifier on cust1 (49 studies contain it): it is reported as ambiguous
  and skipped; the exact identifier must be entered (for example the full "CRS-135 ..." identifier).

## Train of 2026-10-08: forms are protocol-driven, Study Configuration

### Item 1: forms are protocol-driven (`protocol_forms.py`)

Bug (PrTK05, run of 2026-10-08): the compact standards catalog in the analysis context made the analysis mirror the
customer standard's form list. Physical Exam was dropped (protocol 10.5.2 requires it) and Concomitant Procedures
was omitted (protocol 10.4), while the standard's `PR` "Concomitant Procedures" form was left "not used".

Rule (Dan): the PROTOCOL defines which forms exist. For each protocol-required form the content comes from the first
match in: OC4 standard (uploaded file / Reference OC Studies) -> Customer CRF standards column -> CDASHIG. Standards
never add or remove forms on their own.

- **The catalog is no longer in the analysis context.** Structured standards are not shown to the analysis at all;
  they are used only after the forms are decided. `prompts.py` is unchanged. (`standards_match.catalog_text` remains
  as a helper; unstructured files keep their text path, which already says the protocol decides the forms.)
- **Completeness check** (`pipeline._protocol_forms_step`, before `_enforce_common_visit` at the same four sites as
  the matching step, once per protocol text and set of standards; state in `study_meta.protocol_forms`):
  1. One validated AI call lists the assessments the site records (Schedule of Activities rows, study-procedures
     sections), each with section, event OIDs, log yes/no and a **verbatim quote**. The protocol PDF is passed to
     the call; the quote is verified against the `pypdf` text (letters and digits only, so line breaks and table
     spacing do not matter). An entry without a verified quote is discarded. A combined row ("medications and
     procedures", "history and physical") gives one assessment each.
  2. Deterministic mapping: the CDASH domain comes from the assessment wording (the model's own domain only for
     wording we do not know); a form covers the assessment by domain and title, as the domain's generic form, as a
     log form for a log, by naming it on the form, by carrying at least two fields of the domain, or by a field or
     choice that names it. A running log ("Concomitant Procedures") is never covered by a visit form for one
     specific assessment of the same domain ("Radiation").
  3. An assessment no form covers gets a form: created with the id and title of the unused customer standard form
     that collects it (so `standards_match` splices the standard exactly), else from the CRF standards form, else
     from CDASHIG (Highly Recommended and Recommended/Conditional variables), else a two-field placeholder when
     there is no CDASH domain. Visits come from the verified assessment; a log goes to the common event. Every added
     form is flagged for review (`review_flags.protocol_required_form_added`, fields `FLAGGED`) and logged in
     monday with the protocol section.
- **Matcher guard**: sharing a CDASH domain is not enough when only one of the two forms is a prior / concomitant
  log and the names have nothing in common (a radiation form no longer takes a "Concomitant Procedures" standard).
- Kill switch `PROTOCOL_FORMS_CHECK=0`. On any error the spec is unchanged and the build continues.
- PrTK05 offline (protocol v2.0, spec of 2026-10-08, ODM standard): 22 assessments with verified quotes, 20 already
  had a form, 2 added: `PE` Physical examination (10.5.2, CDASHIG, 6 visits) and `PR` Concomitant Procedures (10.4,
  the customer's standard form, common event). ECOG stays an eligibility item. Only `DOV` is left unused.

### Decisions taken during the build (for Dan to confirm)

1. A matched form takes the standard's **form id** (needed to reproduce the customer's form and for cross-form
   references between standard forms). If that id is already used by another form, the protocol form id is kept.
2. Forms with **no CDASH domain** are matched only on an identical form id or title, and that is logged.
3. An **ODM-only** standard has no layout or logic, so the form builder lays the form out as usual. An ODM item
   without Question text never gets an empty label (Dan, 2026-10-08): the label falls back to the ODM item
   Description, then the OpenClinica item details (left item text, header, brief description, comment), then the
   item name. A label taken from the name is flagged on the field (`completion_status` FLAGGED, `flag_reason`
   "label from ODM name, no question text") and listed in `review_flags.customer_standard_label_from_name`.
4. The **version** in settings is stamped per build (OpenClinica needs a new version); all other settings are the
   customer's.

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
