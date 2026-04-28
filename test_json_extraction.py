"""
test_json_extraction.py — Call Claude just for the JSON extraction steps,
bypassing the Skills API calls and monday uploads. Saves the extracted
JSON to fixtures/ so you can iterate on prompts without spending API
tokens on the full pipeline.

Two modes:
  --spec     Extract Study Spec JSON from the protocol PDF (Step 1)
  --summary  Extract Protocol Summary JSON from an existing Study Spec
             fixture (Chain B's first call)

Usage:
    cd ~/oc-ai-pipeline
    export ANTHROPIC_API_KEY=<your key>

    # Full cycle: extract both JSONs from scratch (1-2 API calls)
    python3 test_json_extraction.py --spec --summary

    # Just re-extract Study Spec (1 API call, ~3 min)
    python3 test_json_extraction.py --spec

    # Just re-extract Protocol Summary from existing spec (1 API call, ~1 min)
    python3 test_json_extraction.py --summary

Requires:
  - ~/oc-ai-pipeline/fixtures/PrTK05_Protocol_v2.0.pdf
    (copy from monday download or from your original test PDF)
"""

import argparse, asyncio, json, sys
from pathlib import Path

REPO     = Path(__file__).parent
FIXTURES = REPO / "fixtures"

sys.path.insert(0, str(REPO))
from claude_client import call_claude, extract_json
from prompts       import EDC_STRUCTURE_PROMPT, PRICING_SUMMARY_PROMPT


async def extract_study_spec():
    """Run Step 1: protocol PDF → Study Spec JSON."""
    pdf_path = FIXTURES / "PrTK05_Protocol_v2.0.pdf"
    if not pdf_path.exists():
        print(f"\n✗ Missing {pdf_path}")
        print(f"  Please copy the protocol PDF there first, e.g.:")
        print(f"  cp ~/Downloads/PrTK05_Protocol_v2.0.pdf {FIXTURES}/")
        return None

    pdf_bytes = pdf_path.read_bytes()
    print(f"Protocol PDF: {pdf_path.name} ({len(pdf_bytes):,} bytes)")
    print("Calling Claude for Step 1 — Study Spec JSON (~2-5 min)...")

    raw = await call_claude(EDC_STRUCTURE_PROMPT, pdf_bytes=pdf_bytes)

    # Save the raw response for debugging, always
    raw_path = FIXTURES / "_debug_study_spec_raw.txt"
    raw_path.write_text(raw)
    print(f"Raw response saved to {raw_path} ({len(raw):,} chars)")

    try:
        parsed = extract_json(raw, expected_keys=["study_meta", "forms"])
    except ValueError as e:
        print(f"\n✗ extract_json failed: {e}")
        return None

    if not isinstance(parsed, dict):
        print(f"\n✗ Expected dict, got {type(parsed).__name__}")
        return None

    out = FIXTURES / "study_spec.json"
    out.write_text(json.dumps(parsed, indent=2))
    print(f"\n✓ Saved {out}")
    print(f"  keys:  {list(parsed.keys())}")
    print(f"  forms: {len(parsed.get('forms', []))}")
    print(f"  tpts:  {len(parsed.get('timepoint_csv', {}).get('rows', []))}")
    print(f"  labs:  {len(parsed.get('labranges_csv', {}).get('rows', []))}")
    print(f"  flags: {sum(len(v) for v in parsed.get('review_flags', {}).values()) if isinstance(parsed.get('review_flags'), dict) else 0}")

    # Surface a few quality signals
    surveys = 0
    statuses = {"COMPLETE": 0, "FLAGGED": 0, "PLACEHOLDER": 0, "OTHER": 0}
    xdeps = 0
    for form in parsed.get("forms", []):
        for row in form.get("survey", []):
            surveys += 1
            st = row.get("completion_status", "")
            if st in statuses: statuses[st] += 1
            else: statuses["OTHER"] += 1
        xdeps += len(form.get("cross_form_dependencies", []) or [])
    print(f"  survey rows: {surveys} "
          f"(complete={statuses['COMPLETE']}, flagged={statuses['FLAGGED']}, "
          f"placeholder={statuses['PLACEHOLDER']}, other={statuses['OTHER']})")
    print(f"  cross_form_deps: {xdeps}")
    return parsed


async def extract_protocol_summary(spec):
    """Run Chain B Step 1: Study Spec → Protocol Summary JSON."""
    print("\nCalling Claude for Protocol Summary JSON (~30-60s)...")

    # Match pipeline.py's behaviour — pass a slim Study Spec (forms list
    # is abbreviated to form_id + title + domain only)
    struct_slim = {
        "study_meta": spec.get("study_meta", {}),
        "forms": [
            {
                "form_id": f.get("form_id"),
                "form_title": f.get("form_title"),
                "cdash_domain": f.get("cdash_domain"),
                "visits_assigned": f.get("visits_assigned", []),
                "complexity": f.get("complexity"),
            }
            for f in spec.get("forms", [])
        ],
        "timepoint_csv": spec.get("timepoint_csv", {}),
        "review_flags": spec.get("review_flags", {}),
    }

    raw = await call_claude(
        PRICING_SUMMARY_PROMPT,
        extra_text="Study Specification JSON:\n" + json.dumps(struct_slim),
    )

    raw_path = FIXTURES / "_debug_protocol_summary_raw.txt"
    raw_path.write_text(raw)
    print(f"Raw response saved to {raw_path} ({len(raw):,} chars)")

    try:
        parsed = extract_json(raw, expected_keys=[
            "study_meta", "patient_population", "visit_summary", "crf_summary"
        ])
    except ValueError as e:
        print(f"\n✗ extract_json failed: {e}")
        return None

    out = FIXTURES / "protocol_summary.json"
    out.write_text(json.dumps(parsed, indent=2))
    print(f"\n✓ Saved {out}")
    print(f"  keys: {list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__}")
    return parsed


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec",    action="store_true",
                    help="Extract Study Spec JSON from protocol PDF")
    ap.add_argument("--summary", action="store_true",
                    help="Extract Protocol Summary JSON from current spec fixture")
    args = ap.parse_args()

    if not (args.spec or args.summary):
        ap.print_help()
        sys.exit(1)

    FIXTURES.mkdir(exist_ok=True)

    spec = None
    if args.spec:
        spec = await extract_study_spec()
        if spec is None:
            sys.exit(1)
    elif args.summary:
        # Load existing fixture
        p = FIXTURES / "study_spec.json"
        if not p.exists():
            print(f"✗ No {p} — run --spec first")
            sys.exit(1)
        spec = json.loads(p.read_text())
        print(f"Loaded existing {p} ({len(spec.get('forms', []))} forms)")

    if args.summary and spec:
        await extract_protocol_summary(spec)

    print("\nDone. Now run: python3 test_skills_locally.py")


if __name__ == "__main__":
    asyncio.run(main())
