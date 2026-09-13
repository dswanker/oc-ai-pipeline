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
]

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

    for target in OMOP_CODED_FIELDS:
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
