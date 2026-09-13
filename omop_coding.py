"""
omop_coding.py — the edc_design_standard=OMOP_CDM coding pass.

Inserted into pipeline.py's existing transform chain, right after the
current four calls:
    struct_json = _enforce_common_visit(struct_json)
    struct_json = _backfill_migration_fields(struct_json)
    struct_json = _sanitize_form_titles(struct_json)
    struct_json = _ensure_required_forms(struct_json, protocol_num)

as a fifth line:
    struct_json = _apply_omop_coding(struct_json, edc_design_standard)

When edc_design_standard == "STANDARD" (the default, and the only value
every other customer's studies will ever pass), this is a no-op —
struct_json comes back byte-for-byte identical. It only does anything
when edc_design_standard == "OMOP_CDM".

Vocabulary source: BioIVT_Data_Mapping_Workbook_v3.xlsx, Medications
sheet, rows with Status == "MAPPED" and Vocabulary == "RxNorm". 26 of
64 reviewed CMTRT values are clean and mapped; the rest are excluded
from this pass on purpose (see EXCLUDED_FROM_THIS_PASS below) rather
than guessed at.
"""

import csv
import io

# ── The only thing that changes per target field ────────────────────────
# Each entry: which form/field to recode, which vocab CSV to reference,
# and the appearance to use (OC4's documented pattern for long lists:
# see "Referencing Long Lists" / "Select_One From File" in the OC4
# Reference Guide — appearance=minimal gives type-ahead filtering).
OMOP_CODED_FIELDS = [
    {
        "form_id": "CM",
        "field_name": "CMTRT",
        "vocab_csv_filename": "rxnorm_cm.csv",
        "appearance": "minimal",
    },
    {
        "form_id": "MH",
        "field_name": "DONDIAG",
        "vocab_csv_filename": "snomed_diagnoses.csv",
        "appearance": "minimal",
    },
]

# snomed_diagnoses.csv: 7 verified SNOMED-mapped DONDIAG (Detroit) values,
# sourced from BioIVT_Data_Mapping_Workbook_v3.xlsx (Diagnoses sheet,
# Status=MAPPED, Vocabulary=SNOMED, real Concept/Code value present).
# This is a SMALL, HONEST PARTIAL vocabulary, not a general diagnosis
# coding solution.
#
# Of 103 substantive Diagnoses-sheet rows: 24 are Status=MAPPED, but 7 of
# those have no actual Concept/Code value (vocabulary named, code blank).
# Of the 17 usable rows, 10 are Carlsbad diagnosis_*/type fields (not
# wired here — Carlsbad has no single free-text diagnosis field today).
# The remaining 70 rows are Status="NLP NEEDED" — real NLP/clinical
# review work that has not been done. Nothing here fabricates a code for
# those.
#
# DONDIAG becomes a searchable picklist of 7 real terms. Anything a
# donor's actual diagnosis doesn't match has nowhere to go — there's no
# "Other / Not Listed" escape hatch in this pass (same limitation as
# CMTRT). Worth deciding before go-live whether to add one.
DIAGNOSES_EXCLUDED_FROM_THIS_PASS = {
    "mapped_but_no_code_value": [
        "Donor diagnosis status (Ongoing/New/Past/Recurrent)",
        "Histology grade (Well/Mod/Poorly diff.)",
        "Nottingham grade (I/II/III)",
    ],
    "mapped_but_not_detroit_dondiag": [
        "Alzheimer disease", "Multiple sclerosis", "ALS",
        "Parkinsons disease", "Progressive supranuclear palsy",
        "Frontotemporal dementia", "Mild cognitive impairment",
        "Cancer type Breast", "Cancer type Prostate", "Cancer type Lung/NSCLC",
    ],
    "needs_nlp_not_done": (
        "70 of 103 Diagnoses-sheet rows, marked 'NLP NEEDED'. Free-text "
        "DONDIAG values across both studies are varied enough that "
        "mapping to SNOMED requires real NLP/clinical review, not a "
        "lookup. Don't assume coverage beyond the 7 in "
        "snomed_diagnoses.csv."
    ),
}

# Rows from the mapping workbook that are NOT included in the CSV this
# pass ships, and why — kept here so nobody re-derives this later without
# knowing it was already looked at once:
EXCLUDED_FROM_THIS_PASS = {
    "reclassify_as_procedure_not_medication": [
        # These 7 values were entered into the medication field but are
        # actually procedures (radiation, surgery). Recoding CMTRT to
        # RxNorm doesn't fix this — it's a CRF data-quality issue, not a
        # vocabulary gap. Left as free text for now so these records
        # aren't silently hidden or forced into a wrong RxNorm code.
        "Radiation", "IMRT", "HDR", "Cyberknife",
        "Cholecystectomy", "Sigmoidectomy", "Resection",
    ],
    "needs_a_clinical_call_before_coding": [
        # Ambiguous or multi-component — coding any of these requires a
        # decision only a clinical reviewer should make, not something
        # to default silently:
        "Vitamin D",        # D2 vs D3 not specified in source data
        "Percocet",         # combination drug — keep as one code or explode to components?
        "Multivitamin",     # no single RxNorm concept exists
        "Trelegy Ellipta",  # no single CUI — must explode to 3 components
        "Folfox",           # named regimen, not a single drug
        "Folfirinox",       # named regimen, not a single drug
        "Abraxane",         # different formulation from standard Paclitaxel
        "MVASI",            # biosimilar — which CUI: reference product or biosimilar-specific?
        "Kanjinti",         # biosimilar — same question
    ],
    "needs_nlp_or_more_source_data": [
        "indication free text (Hypertension/Pain/Anxiety etc.)",
        "THERTYP Chemotherapy — category only, specific drug elsewhere",
    ],
}


def load_vocab_csv(path):
    """Read a vocab CSV (name,label columns) into a list of dicts, for
    embedding into the struct_json so the build step can write it out
    alongside the form."""
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _vocab_csv_to_string(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["name", "label"])
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def _apply_omop_coding(struct_json, edc_design_standard, vocab_dir=None):
    """
    struct_json: the Study Specification JSON dict (same shape used
        everywhere else in pipeline.py — has struct_json["forms"], a
        list of form dicts with "form_id" and "survey" list of rows).
    edc_design_standard: "STANDARD" or "OMOP_CDM" — read from the new
        Monday column (COL["edc_design_standard"]) by the caller.
    vocab_dir: directory containing the vocab CSVs (rxnorm_cm.csv etc).
        Defaults to a bundled directory shipped alongside this module.

    Returns struct_json, mutated in place (and also returned, matching
    the calling convention of _enforce_common_visit and friends).
    """
    if edc_design_standard != "OMOP_CDM":
        return struct_json  # no-op — this is the STANDARD/default path

    import os
    if vocab_dir is None:
        vocab_dir = os.path.join(os.path.dirname(__file__), "omop_vocab")

    struct_json.setdefault("_omop_vocab_files", {})

    # TEST MODE: pick the large sample vocab set for the study currently
    # being processed, based on study_id/protocol_number. These are NOT
    # verified real RxNorm/SNOMED codes -- see build_sample_vocabs.py.
    # They exist purely so BioIVT can see the search()/select_one_from_file
    # UX at realistic scale before real vocabulary review happens. Falls
    # back to the small verified sets (rxnorm_cm.csv, snomed_diagnoses.csv)
    # for any study_id that doesn't match either sample set.
    _study_id = str(
        (struct_json.get("study_meta") or {}).get("study_id")
        or (struct_json.get("study_meta") or {}).get("protocol_number")
        or ""
    ).upper()
    if "PRECISIO" in _study_id or "CARLSBAD" in _study_id:
        _sample_suffix = "_carlsbad_sample"
    elif "DETROIT" in _study_id:
        _sample_suffix = "_detroit_sample"
    else:
        _sample_suffix = None

    active_fields = OMOP_CODED_FIELDS
    if _sample_suffix:
        active_fields = []
        for target in OMOP_CODED_FIELDS:
            base, ext = target["vocab_csv_filename"].rsplit(".", 1)
            sample_name = f"{base}{_sample_suffix}.{ext}"
            if os.path.exists(os.path.join(vocab_dir, sample_name)):
                active_fields.append({**target, "vocab_csv_filename": sample_name})
            else:
                active_fields.append(target)

    for target in active_fields:
        vocab_path = os.path.join(vocab_dir, target["vocab_csv_filename"])
        if not os.path.exists(vocab_path):
            print(f"[omop-coding] WARNING: {vocab_path} not found — "
                  f"skipping {target['form_id']}.{target['field_name']}",
                  flush=True)
            continue

        rows = load_vocab_csv(vocab_path)
        struct_json["_omop_vocab_files"][target["vocab_csv_filename"]] = \
            _vocab_csv_to_string(rows)

        recoded = 0
        for form in struct_json.get("forms", []):
            if form.get("form_id") != target["form_id"]:
                continue
            for row in form.get("survey", []):
                if row.get("name") == target["field_name"]:
                    row["type"] = f"select_one_from_file {target['vocab_csv_filename']}"
                    row["appearance"] = target["appearance"]
                    recoded += 1
        print(f"[omop-coding] {target['form_id']}.{target['field_name']} "
              f"→ select_one_from_file {target['vocab_csv_filename']} "
              f"({len(rows)} concepts, {recoded} row(s) recoded)", flush=True)

    return struct_json
