# ODM → OC4 Field Mapping Reference

Authoritative mapping reference for the migration-analysis skill. All tables and rules below are derived from the actual code in `migration/odm_reader.py`, `migration/odm_to_spec.py`, and `migration/gap_analysis.py`. When the code and this document disagree, the code wins — file an issue to reconcile.

## 1. ODM DataType → XLSForm Type

From `DATATYPE_MAP` (`odm_reader.py:135`). The transform applies this verbatim for any item without a `codelist_ref`. Items with a resolvable codelist override this and become `select_one <safe_list>` or `select_multiple <safe_list>` (see §6 below).

| ODM DataType        | XLSForm type       | Notes |
|---------------------|--------------------|-------|
| `text`              | `text`             | Direct |
| `string`            | `text`             | ODM 1.3.x synonym |
| `integer`           | `integer`          | Direct |
| `float`             | `decimal`          | XLSForm has no `float` |
| `double`            | `decimal`          | XLSForm has no `double` |
| `decimal`           | `decimal`          | Direct |
| `date`              | `date`             | Direct |
| `time`              | `time`             | Direct |
| `datetime`          | `dateTime`         | Note: XLSForm `dateTime` (camelCase), not `datetime` |
| `partialDate`       | `date`             | Lossless widening to full date semantics; reviewer must populate partials |
| `partialTime`       | `time`             | Same — partial widened to full |
| `partialDatetime`   | `dateTime`         | Same |
| `boolean`           | `select_one yn`    | OC4 has no native boolean; uses canonical Yes/No codelist `yn` |
| `URI`               | `text`             | URI stored as text; no native XLSForm URI type |
| `base64Binary`      | `text`             | Binary not supported in metadata; encode as text for reviewer attention |
| `hexBinary`         | `text`             | Same |

Unknown / unmapped DataType → `text` (via `DATATYPE_MAP.get(dtype, "text")`).

## 2. Canonical Type Vocabulary (gap analysis)

`gap_analysis._normalize_source_type` and `_normalize_target_type` collapse both ODM and XLSForm vocabularies onto a small canonical set. The gap classification ladder operates on canonical types only.

**Source canonical (ODM):**

| ODM token (lowercased)                                     | Canonical |
|------------------------------------------------------------|-----------|
| `text`, `string`, `uri`, `base64binary`, `hexbinary`       | `text` |
| `integer`, `int`                                           | `integer` |
| `float`, `double`, `decimal`, `number`                     | `decimal` |
| `date`, `partialdate`                                      | `date` |
| `time`, `partialtime`                                      | `time` |
| `datetime`, `partialdatetime`                              | `datetime` |
| `boolean`                                                  | `boolean` |
| anything else                                              | `text` (default) |

**Target canonical (XLSForm):**

| XLSForm token                          | Canonical |
|----------------------------------------|-----------|
| starts with `select_one`               | `select_one` |
| starts with `select_multiple`          | `select_multiple` |
| `text`                                 | `text` |
| `integer`                              | `integer` |
| `decimal`                              | `decimal` |
| `date`                                 | `date` |
| `time`                                 | `time` |
| `datetime`                             | `datetime` |
| `boolean`                              | `boolean` |
| `calculate`                            | `calculate` |
| anything else                          | `text` (default) |

**Source codelist promotion.** A source field that declares `coded_list` (any non-empty list) has its canonical source type promoted to `select_one` regardless of its ODM DataType (`gap_analysis.py:324`). This is because ODM convention emits coded fields as `DataType="text"` with the enumeration in a `<CodeListRef>`. Without this promotion every coded field would mis-classify as `text → select_one = Blocking`.

## 3. Type Narrowing / Widening Table

From `gap_analysis._check_type` (lines 126–169). The function returns one of `same`, `widening`, `narrowing`, `incompatible` on the canonical pair.

**Same (lossless, identity):**

| Source canon       | Target canon       |
|--------------------|--------------------|
| any X              | X                  |
| `boolean`          | `select_one`       |
| `select_one`       | `boolean`          |

**Widening (lossless, type changes):**

| Source canon       | Target canon       | Note |
|--------------------|--------------------|------|
| `integer`          | `decimal`          | Numeric promotion |
| `date`             | `datetime`         | Temporal promotion |
| `time`             | `datetime`         | Temporal promotion |
| `select_one`       | `select_multiple`  | Source value carried as single-element selection |
| non-`select_*` X   | `select_one`       | Gaining enum constraint; lossless if all source values fit |
| non-`select_*` X   | `select_multiple`  | Same |
| any X              | `text`             | Text absorbs anything |

**Narrowing (lossy, may drop data):**

| Source canon       | Target canon       | Note |
|--------------------|--------------------|------|
| `decimal`          | `integer`          | Fractional precision dropped |
| `datetime`         | `date`             | Time component dropped |
| `datetime`         | `time`             | Date component dropped |
| `select_multiple`  | `select_one`       | Any multi-value selection dropped |
| `select_*`         | non-`select_*`     | Constraint dropped; free-text now accepted |

**Incompatible (no defensible coercion):**

| Source canon       | Target canon       | Note |
|--------------------|--------------------|------|
| `text`             | any non-`text`     | Cannot parse arbitrary user-entered strings |
| anything else without an entry above | | e.g. `integer` → `date` |

## 4. Gap Classification Ladder

From `gap_analysis._classify` (lines 302+). Order matters: the first matching rule wins. Reason text format is fixed by the function — match it exactly when writing alternate classifications so the UI rendering stays consistent.

The ladder rungs:

| Confidence    | Risk             | When it fires |
|---------------|------------------|---------------|
| High          | Clean            | Same canonical type; capacity equal or expanded; all source codes covered |
| Medium        | Warning          | Lossless widening; OR source required → target optional |
| Low           | Data Loss Risk   | Text length shrink; lossy narrowing; partial codelist coverage (≤ 50% missing) |
| Unmappable    | Blocking         | Incompatible canonical types; no target row for a required source; > 50% codelist values missing |

Rule order in `_classify`:

1. **No target row:** Unmappable / Blocking. Required source → "migration cannot proceed without a manual mapping." Optional source → "values will be dropped unless a reviewer adds a mapping."
2. **Incompatible type:** Unmappable / Blocking. "Source type (X) is fundamentally incompatible with target type (Y). Manual transformation required."
3. **Codelist coverage check:**
   - Missing values > 50% of source codes → Unmappable / Blocking with preview of 5 missing values.
   - Missing values ≤ 50% (and > 0) → Low / Data Loss Risk with preview.
4. **Text length shrink:** `text → text` with `target_length < source_length` → Low / Data Loss Risk with concrete shrink numbers.
5. **Lossy narrowing:** type_relation == "narrowing" → Low / Data Loss Risk.
6. **Lossless widening:** type_relation == "widening" → Medium / Warning.
7. **Required regression:** source required & target optional → Medium / Warning.
8. **Length expansion:** text capacity expanded → High / Clean.
9. **Default (clean baseline):** High / Clean — "Same type, capacity matches or expands, no data loss risk."

## 5. OID Normalisation Rules

From `odm_to_spec.py:225-288`.

### Event OIDs (`_oc_event_oid`)

```
oid = re.sub(r"[^A-Za-z0-9_]", "_", raw_oid).upper()
if not oid.startswith("SE_"):
    oid = "SE_" + oid
```

Examples:

| Source ODM event OID | Normalised |
|----------------------|------------|
| `SCREEN`             | `SE_SCREEN` |
| `Week-1`             | `SE_WEEK_1` |
| `SE_BASELINE`        | `SE_BASELINE` (unchanged) |
| `unsch visit`        | `SE_UNSCH_VISIT` |
| `c1d1`               | `SE_C1D1` |

### Form IDs (`_oc_form_id`)

Order of resolution:

1. **CDASH match first.** If the uppercased+stripped name or OID matches a `CDASH_DOMAIN_MAP` key, return the canonical CDASH domain (`AE`, `CM`, `DM`, `DV`, `MH`, etc.).
2. **Strip known prefixes.** Remove leading `F_`, `F.`, `CRF_`, `FORM_` (case-insensitive).
3. **Sanitise & uppercase.** Replace non-alphanumeric with `_`, uppercase, strip leading/trailing `_`.
4. **CDASH match again** on the cleaned token.
5. **Length cap.** If > 20 chars, truncate at last `_` boundary within 20 chars (only if the truncated stem is ≥ 4 chars); else hard cap at 20.

Examples:

| Source (oid, name)             | Normalised form_id |
|--------------------------------|---------------------|
| (`F_AE`, `Adverse Events`)     | `AE` |
| (`F_DEM`, `Demographics`)      | `DM` |
| (`F_CRF_VS_001`, `Vital Signs`)| `VS` |
| (`F_SLEEP`, `Sleep Quality (NRS + PROMIS 8A)`) | `SLEEP` |
| (`CUSTOM_PROTOCOL_FORM_NAME_THAT_IS_VERY_LONG`, ``) | (truncated to `≤ 20` chars on `_` boundary) |

### Item names (`_oc_item_name`)

Order:

1. If `cdash_alias` non-empty: return `cdash_alias.upper()`.
2. Else if `raw_name` non-empty: sanitise with `re.sub(r"[^A-Za-z0-9]", "_", name).upper().strip("_")`.
3. Else strip leading `I_<FORM>_` prefix from `raw_oid` and uppercase.

### Item-group code (`_oc_itemgroup`)

Returns just the short group code (no dots, no spaces), used as the value of `bind::oc:itemgroup`. Order:

1. If `group_name`: sanitise with non-`[A-Za-z0-9_]` → `_`, uppercase, strip `_`.
2. Else if `group_oid`: strip `IG_` or `<FORM>_` prefix, then sanitise as above.
3. Else: use `form_id` itself.

### CodeList safe name (`_safe_list_name`)

```
return re.sub(r"[^A-Za-z0-9_]", "_", oid)
```

Applied to the codelist OID when forming the `select_one <name>` / `select_multiple <name>` token. No truncation, no uppercase.

## 6. Codelist → select_* Decision

In `_build_survey_row` (`odm_to_spec.py:319-328`):

| Condition                                                                        | XLSForm type                              |
|----------------------------------------------------------------------------------|-------------------------------------------|
| No `codelist_ref`, OR codelist not in `codelist_lookup`                          | `DATATYPE_MAP.get(dtype, "text")` (see §1) |
| Has codelist AND `"multiple" in item.name.lower()`                               | `select_multiple <safe_list_name>` |
| Has codelist AND codelist has > 20 items                                         | `select_multiple <safe_list_name>` |
| Otherwise (codelist present, ≤ 20 items, name lacks "multiple")                  | `select_one <safe_list_name>` |

The 20-item threshold is empirical — beyond it, single-select dropdowns become hard to scan. Adjust the threshold only if the source EDC clearly intends single-select.

## 7. Form Title Sanitisation

Form titles render on OC4 board cards and feed into the Designer's `getForm` Meteor call. Apply BEFORE assigning `form_title` in step 3 of the skill.

| Char | Action | Reason |
|------|--------|--------|
| `+`  | Replace with ` and ` (space-and-space, three chars total) | Empirically confirmed to deadlock OC4 form-service in CRS-135 on 2026-06-02. The deadlock manifests as an OC4 form-import that never returns. |
| `&`  | Strip | XML/XPath ampersand; downstream XPath expressions break. |
| `%`  | Strip | Format-string char in some OC4 internal renderers; observed to produce garbled titles. |
| `#`  | Strip | URL fragment character; breaks deep-links to Designer cards. |
| `@`  | Strip | Reserved in OC4 internal indexing. |

Preserve everything else: letters, digits, spaces, parentheses `()`, slashes `/`, hyphens `-`, degree signs `°`, all other Unicode characters. Collapse any resulting double-spaces to single spaces.

Worked examples:

| Source title                                    | Sanitised title |
|-------------------------------------------------|-----------------|
| `Sleep Quality (NRS + PROMIS 8A)`               | `Sleep Quality (NRS and PROMIS 8A)` |
| `Inclusion/Exclusion Criteria`                  | `Inclusion/Exclusion Criteria` (unchanged) |
| `Pain NRS (Current and Daily)`                  | `Pain NRS (Current and Daily)` (unchanged) |
| `iovera° Treatment Administration`              | `iovera° Treatment Administration` (`°` preserved) |
| `Lipid Panel & HbA1c (Q1 2026 #1) @ Day 30`     | `Lipid Panel HbA1c (Q1 2026 1)  Day 30` → collapse spaces → `Lipid Panel HbA1c (Q1 2026 1) Day 30` |

## 8. Vendor Detection

From `VENDOR_CONVENTION_FILES` (`odm_to_spec.py:56-69`). The `source_system` string in `OdmStudy` (emitted by `odm_reader._detect_vendor` from the ODM `Originator` attribute and vendor-extension namespaces) keys into this table. Fall back to `generic_odm.md` if the vendor is not listed.

| `source_system` string  | Convention file              |
|-------------------------|------------------------------|
| `Medidata Rave`         | `medidata_rave.md`           |
| `Oracle InForm`         | `oracle_inform.md`           |
| `REDCap`                | `redcap.md`                  |
| `Castor EDC`            | `castor.md`                  |
| `Viedoc`                | `viedoc.md`                  |
| `Veeva Vault CDMS`      | `veeva.md`                   |
| `Zelta (Merative)`      | `zelta.md`                   |
| `iMedNet`               | `imednet.md`                 |
| `Medrio`                | `medrio.md`                  |
| `OpenClinica`           | `generic_odm.md`             |
| `OpenClinica 4`         | `generic_odm.md`             |
| anything else / blank   | `generic_odm.md` (default)   |

Vendor-extension namespace URIs used by `_detect_vendor` (for namespace-based fallback when `Originator` is absent):

| Vendor      | URI |
|-------------|-----|
| Medidata    | `http://www.mdsol.com/ns/odm/metadata` |
| Viedoc      | `http://www.viedoc.net/ns/odm` |
| Oracle      | `http://www.oracle.com/ns/odm` |
| Castor      | `http://www.castoredc.com/ns/odm` |
| REDCap      | `https://projectredcap.org` |
| OC3         | `http://www.openclinica.org/ns/odm_ext_v130/v3.1` |
| OC4         | `http://openclinica.org/xforms` |

The convention file is already injected into the AI prompt by `_render_ai_assist_prompt` via `load_vendor_conventions(source_system)`. Read the loaded convention before applying any vendor-specific rule (e.g. Medidata Rave's `mdsol:IsLog="Yes"` → repeating semantics).

## 9. Auto-Injected Rows (excluded from Gap Appendix)

These rows are emitted for OC4 plumbing reasons and have no ODM source counterpart. All carry `_source_oid = ""` and are excluded from the per-field gap table (they appear in the appendix's "Auto-injected rows" section with a one-line reason instead).

| Row                              | Trigger                                                          | Type            | Notes |
|----------------------------------|------------------------------------------------------------------|-----------------|-------|
| `SUBJID` calculate                | Every form lacking `SUBJID` in its source items                  | `calculate`     | `calculation = instance('clinicaldata')/ODM/ClinicalData/SubjectData/@SubjectKey`. Injected at top of survey. (`odm_to_spec.py:648-670`) |
| DOV (Date of Visit) `date`        | Forms where AI determines a visit-date stamp is needed           | `date`          | Inject with `_source_oid = ""` and append a Gap Appendix auto-injected entry stating the date binding. |
| `begin group` / `end group`       | Every ItemGroupDef in the source form                            | `begin group` / `end group` | Wrappers around real fields. (`odm_to_spec.py:685-697, 728-736`) |
| `begin repeat` / `end repeat`     | Currently NEVER emitted by deterministic `transform()`           | (n/a)           | Per CRS-135: OC4 derives repetition from `bind::oc:itemgroup` on data fields; XLSForm repeat trailers cause "Unmatched end statement" on form import. (`odm_to_spec.py:738-743`) |
| Cross-form `calculate` rows       | Added by AI for dependencies (SUBJID lookup from DM, ICFDAT from ICF, etc.) | `calculate` | When AI adds a cross-form ref, `_source_oid = ""` and the appendix gets an entry stating the source-form / source-field. |

## 10. OC-9: SE_COMMON Assignment

From `_build_visit_assignment` (`odm_to_spec.py:467-481`) and `COMMON_VISIT_FORMS` (line 223).

```
COMMON_VISIT_FORMS = {"AE", "CM", "DV", "AESAE"}

def _build_visit_assignment(form_oid, form_id, odm_study):
    if form_id in COMMON_VISIT_FORMS:
        return ["SE_COMMON"]   # OC-9 override
    ...
```

A form's `visits_assigned` is forced to `["SE_COMMON"]` if its computed `form_id` is in the set above, regardless of the source ODM's StudyEventDef → FormRef placements. This is unconditional.

`SE_COMMON` is always present in `timepoint_csv.rows`. If no source event normalises to it, `_build_timepoint_rows` appends a synthetic `SE_COMMON` row (`odm_to_spec.py:506-510`).

Reviewer override: the spec JSON can be hand-edited post-generation to restore per-visit AE collection, but the skill itself NEVER produces non-OC-9-compliant output.

## 11. Repeating Domains

From `REPEATING_DOMAINS = {"AE", "CM", "MH", "DV", "PC", "PR", "EX"}` (`odm_to_spec.py:220`).

`has_repeating_group = form.repeating OR form_id in REPEATING_DOMAINS`

So even if a vendor exports `AE` as non-repeating, the skill records it as a repeating form because AE-by-CDASH-convention is log-line. This drives downstream complexity scoring and (in OC-8 layouts) repeating-group rendering, but does NOT emit `begin_repeat`/`end_repeat` trailers — see §9.

## 12. Cross-References

- Code authority: `migration/odm_reader.py` (DATATYPE_MAP, OdmStudy schema, `_detect_vendor`).
- Classification authority: `migration/gap_analysis.py` (`_normalize_*_type`, `_check_type`, `_classify`).
- Transform authority: `migration/odm_to_spec.py` (`_oc_*` OID helpers, `_build_visit_assignment`, `_build_survey_row`, `COMMON_VISIT_FORMS`, `REPEATING_DOMAINS`, `VENDOR_CONVENTION_FILES`).
- Vendor specifics: `references/vendor-conventions/<vendor>.md`.
- Skill flow: `SKILL.md` in this directory.
