"""
test_skills_locally.py — Run all skill scripts against local JSON inputs
                         WITHOUT calling the Claude API.

Workflow:
  1. Obtain Study Spec JSON and Protocol Summary JSON (once — see below)
  2. Save them as fixtures/study_spec.json and fixtures/protocol_summary.json
  3. Run this script — generates every output file under test_outputs/
  4. Iterate on skill scripts; re-run (0 API cost each time)

How to get the input JSONs the first time:
  Option A: Download from monday.com manually (they're in the 'Study Spec JSON'
            and 'Protocol Summary' columns on the item)
  Option B: Run fetch_jsons_from_monday.py (requires MONDAY_API_TOKEN)
  Option C: Run extract_jsons_once.py (one Claude API call per JSON)

Usage:
    cd ~/oc-ai-pipeline
    python3 test_skills_locally.py
"""

import json, os, sys, traceback
from pathlib import Path

REPO = Path(__file__).parent
SKILLS = REPO / "skills"
FIXTURES = REPO / "fixtures"
OUT = REPO / "test_outputs"

# ── Load the input JSONs ──────────────────────────────────────────────────────

def load_json(path):
    if not path.exists():
        print(f"  ✗ MISSING: {path}")
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        print(f"  ✓ Loaded {path.name} ({path.stat().st_size} bytes)")
        return data
    except Exception as e:
        print(f"  ✗ FAILED to parse {path}: {e}")
        return None


def run_one(label, fn):
    """Run a generator function, report result."""
    print(f"\n── {label} ──")
    try:
        result = fn()
        if result and os.path.exists(result):
            size = os.path.getsize(result)
            print(f"  ✓ {result} ({size:,} bytes)")
        elif result:
            print(f"  ✓ {result} (path returned but file not found)")
        else:
            print(f"  ⚠ no output path returned")
    except Exception as e:
        print(f"  ✗ ERROR: {e}")
        traceback.print_exc()


def main():
    print(f"Repo root:  {REPO}")
    print(f"Fixtures:   {FIXTURES}")
    print(f"Outputs:    {OUT}")
    OUT.mkdir(exist_ok=True)

    # ── Load inputs ─────────────────────────────────────────────────────────
    print("\n═══ Loading fixtures ═══")
    spec    = load_json(FIXTURES / "study_spec.json")
    summary = load_json(FIXTURES / "protocol_summary.json")
    if not spec:
        print("\nCannot proceed without study_spec.json fixture.")
        print("See docstring for how to obtain it.")
        sys.exit(1)

    protocol = spec.get("study_meta", {}).get("protocol_number", "TEST")
    print(f"Protocol number: {protocol}")
    print(f"Forms in spec:   {len(spec.get('forms', []))}")
    print(f"Timepoints:      {len(spec.get('timepoint_csv', {}).get('rows', []))}")

    # ── Put skill scripts on the path ───────────────────────────────────────
    sys.path.insert(0, str(SKILLS / "protocol-analysis" / "scripts"))
    sys.path.insert(0, str(SKILLS / "pricing-quote"     / "scripts"))
    sys.path.insert(0, str(SKILLS / "edc-builder"       / "scripts"))
    sys.path.insert(0, str(SKILLS / "dvs-specification" / "scripts"))

    # ── Test each generator ─────────────────────────────────────────────────
    print("\n═══ protocol-analysis — Study Spec PDF + XLSX ═══")

    def _spec_pdf():
        from generate_study_spec_pdf import build_study_spec_pdf
        path = str(OUT / f"{protocol}_Study_Specification.pdf")
        build_study_spec_pdf(spec, path)
        return path
    run_one("Study Specification PDF", _spec_pdf)

    def _spec_xlsx():
        from generate_study_spec_xlsx import build_study_spec_xlsx
        path = str(OUT / f"{protocol}_Study_Specification.xlsx")
        build_study_spec_xlsx(spec, path)
        return path
    run_one("Study Specification XLSX", _spec_xlsx)

    # ── Protocol Summary PDF ────────────────────────────────────────────────
    if summary:
        print("\n═══ protocol-analysis — Protocol Summary PDF ═══")
        def _ps_pdf():
            from generate_protocol_summary_pdf import build_protocol_summary_pdf
            path = str(OUT / f"{protocol}_Protocol_Summary.pdf")
            build_protocol_summary_pdf(summary, path)
            return path
        run_one("Protocol Summary PDF", _ps_pdf)

        # ── Pricing Quote ────────────────────────────────────────────────────
        print("\n═══ pricing-quote — 4 quote files ═══")
        def _quote():
            from pricing_engine      import calculate_quote, merge_edc_flags
            from generate_quote_pdf  import build_quote_pdfs
            from generate_quote_xlsx import build_quote_xlsx
            # Merge Study Spec review_flags into Protocol Summary (mirrors
            # pipeline.py behavior). This gives the pricing engine the
            # list-typed, per-item flags from the Study Spec rather than
            # only the int counts in the Protocol Summary.
            enriched = merge_edc_flags(summary, spec)
            quote = calculate_quote(enriched)
            pdf_int = str(OUT / f"{protocol}_Quote_Internal.pdf")
            pdf_cli = str(OUT / f"{protocol}_Quote_Client.pdf")
            xls_int = str(OUT / f"{protocol}_Quote_Internal.xlsx")
            xls_cli = str(OUT / f"{protocol}_Quote_Client.xlsx")
            build_quote_pdfs(quote, pdf_int, pdf_cli)
            build_quote_xlsx(quote, xls_int, xls_cli)
            # Diagnostic: show what the engine counted
            fd = quote.get('flag_analysis', {})
            print(f"  flag_analysis: counted={fd.get('flagged_items')}, "
                  f"excluded={fd.get('excluded_items')}, "
                  f"category_counts={fd.get('category_counts')}")
            return pdf_int
        run_one("Price Quote (4 files)", _quote)
    else:
        print("\n⚠ Skipping Protocol Summary PDF and Price Quote "
              "(no protocol_summary.json fixture)")

    # ── EDC Build ZIP ───────────────────────────────────────────────────────
    print("\n═══ edc-builder — Study build ZIP ═══")
    def _edc():
        import tempfile, zipfile, shutil
        from build_xlsforms  import build_all_xlsforms, write_timepoint_csv, write_labranges_csv
        from build_checklist import build_checklist_pdf, build_checklist_xlsx
        from build_package   import build_package
        # build_log is a dict with list-valued buckets — match the shape
        # that build_xlsforms expects.
        build_log = {
            'forms_built':        [],
            'forms_skipped':      [],
            'build_errors':       [],
            'build_warnings':     [],
            'placeholder_applied': [],
            'oid_placeholders':   [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            forms_dir     = os.path.join(tmp, 'forms')
            csv_dir       = os.path.join(tmp, 'csv')
            checklist_dir = os.path.join(tmp, 'checklist')
            package_dir   = os.path.join(tmp, 'package')
            for d in (forms_dir, csv_dir, checklist_dir, package_dir):
                os.makedirs(d, exist_ok=True)

            build_all_xlsforms(spec, forms_dir, build_log)
            write_timepoint_csv(spec.get('timepoint_csv', {}),
                                os.path.join(csv_dir, f'{protocol}_tpt.csv'),
                                build_log)
            write_labranges_csv(spec.get('labranges_csv', {}),
                                os.path.join(csv_dir, f'{protocol}_labranges.csv'),
                                build_log)
            build_checklist_pdf(spec, build_log,
                                os.path.join(checklist_dir,
                                             f'{protocol}_Build_Checklist.pdf'))
            build_checklist_xlsx(spec, build_log,
                                 os.path.join(checklist_dir,
                                              f'{protocol}_Build_Checklist.xlsx'))

            # build_package writes a zip inside the output_dir (date-stamped name).
            # It returns the full zip path.
            produced_zip = build_package(spec, build_log,
                                         forms_dir, csv_dir,
                                         checklist_dir, package_dir)
            # Copy the produced zip to our test_outputs with a stable name.
            # Clean up any leftover file/dir at the target path first so
            # shutil.copy doesn't accidentally copy INTO a directory.
            final_zip = str(OUT / f'{protocol}_EDC_Build.zip')
            if os.path.isdir(final_zip):
                shutil.rmtree(final_zip)
            elif os.path.exists(final_zip):
                os.remove(final_zip)
            shutil.copy(produced_zip, final_zip)
            print(f"  build_log summary: "
                  f"built={len(build_log.get('forms_built', []))}, "
                  f"skipped={len(build_log.get('forms_skipped', []))}, "
                  f"errors={len(build_log.get('build_errors', []))}")
            for err in build_log.get('build_errors', [])[:10]:
                print(f"    skipped: {err.get('form_id')}: {err.get('error')}")
            return final_zip
    run_one("EDC Build ZIP", _edc)

    # ── DVS XLSX ────────────────────────────────────────────────────────────
    # Skipped for now — dvs-specification's build_dvs needs a pre-built dvs_data
    # structure that normally comes from Claude's reasoning. For offline testing
    # a future version would need a fixture of dvs_data too.
    print("\n⚠ Skipping DVS XLSX (requires Claude-derived dvs_data structure)")

    print(f"\n═══ Done. Outputs in {OUT}/ ═══")
    for f in sorted(OUT.iterdir()):
        if f.is_file():
            print(f"  {f.name}  ({f.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
