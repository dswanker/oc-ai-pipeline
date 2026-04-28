"""
Opt-in integration check — calls the REAL Anthropic API.

Purpose
-------
End-to-end sanity test: parse the PrTK05 ODM fixture, send the result
through Claude, print the resulting StudyFingerprint. This is the
"did this actually work?" smoke test for the fingerprint extractor.

Why opt-in
----------
Real API calls cost money (cents per run), require a working
``ANTHROPIC_API_KEY`` in ``.env``, and need the full dependency set
installed. Unit tests in ``test_fingerprint_extractor.py`` cover the
deterministic logic without the network round-trip.

How to run
----------
1. Make sure deps are installed::

     pip install anthropic structlog pydantic pydantic-settings python-dotenv

   …or just::

     pip install -e .

2. Put a real API key in ``.env``::

     ANTHROPIC_API_KEY=sk-ant-...
     ANTHROPIC_MODEL=claude-opus-4-7

3. Run::

     python tests/integration_fingerprint_real_api.py

The filename intentionally does NOT start with ``test_`` so pytest
won't auto-discover it.

Expected output
---------------
Something close to::

    Sponsor:           Candel Therapeutics, Inc.
    Intervention:      ['aglatimagene besadenovec', 'valacyclovir']
    Indication:        intermediate-risk prostate cancer
    Phase:             2
    Study type:        interventional
    Therapeutic area:  oncology
    Confidence:        ~0.9 to 0.95
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Best-effort .env load. dotenv isn't a hard dep; pydantic-settings
# already reads .env on import, so we may not need this. Keeping it for
# defensiveness in case someone runs this script outside a context that
# triggers settings-load.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from core.fingerprint import FingerprintExtractor
from core.form_parser.odm_xml import ODMXMLParser

FIXTURE = Path(__file__).parent / "fixtures" / "prtk05_sample.odm.xml"


async def main(use_overrides: bool = False) -> None:
    print(f"Reading fixture: {FIXTURE.name}")
    data = FIXTURE.read_bytes()

    print("Parsing ODM XML...")
    parsed = await ODMXMLParser().parse(data, filename=FIXTURE.name)
    print(f"  Found {len(parsed.forms)} forms; sponsor in XML = {parsed.sponsor!r}")

    print("Calling Claude...")
    extractor = FingerprintExtractor()

    overrides = (
        {"sponsor": "Candel Therapeutics, Inc."} if use_overrides else None
    )
    fp = await extractor.extract(parsed, overrides=overrides)

    print()
    print("─── StudyFingerprint ───────────────────────────────────────")
    print(f"  Sponsor:           {fp.sponsor}")
    print(f"  Intervention:      {fp.intervention}")
    print(f"  Indication:        {fp.indication}")
    print(f"  Phase:             {fp.phase}")
    print(f"  Study type:        {fp.study_type}")
    print(f"  Therapeutic area:  {fp.therapeutic_area}")
    print(f"  Confidence:        {fp.extraction_confidence}")
    if fp.notes:
        print(f"  Notes:             {fp.notes}")
    print("────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    use_overrides = "--with-overrides" in sys.argv
    asyncio.run(main(use_overrides=use_overrides))
