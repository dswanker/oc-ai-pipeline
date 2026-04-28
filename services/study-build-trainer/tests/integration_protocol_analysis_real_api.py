"""
Opt-in integration check — calls the REAL protocol-analysis skill via
the REAL Anthropic API.

Purpose
-------
End-to-end sanity check that the trainer's wrapper around the
protocol-analysis skill actually works on a real protocol PDF, end
to end. Different from ``test_protocol_analysis_client.py`` which
uses a stub client and a fake skill folder.

Why opt-in
----------
* Costs a few cents per run.
* Requires ``ANTHROPIC_API_KEY`` set in ``.env``.
* Requires a protocol PDF on disk somewhere.
* Requires the real skill folder to be present (it is, when the
  trainer is sitting inside ``oc-ai-pipeline/services/``).

How to run
----------
1. Ensure deps are installed (you already did this for the
   fingerprint integration check)::

     pip install anthropic structlog pydantic pydantic-settings python-dotenv

2. Make sure ``.env`` has a real ``ANTHROPIC_API_KEY``.

3. Point the script at a protocol PDF. By default it looks for one at
   ``../../../references/PrTK05_Protocol_v2_0_1.pdf``, the location it
   sits in the oc-ai-pipeline references folder. Override with the
   first CLI arg::

     python tests/integration_protocol_analysis_real_api.py
     # or
     python tests/integration_protocol_analysis_real_api.py /path/to/protocol.pdf

The filename intentionally does NOT start with ``test_`` so pytest
won't auto-discover it.

Expected output
---------------
The skill returns whatever its SKILL.md says it produces — typically
a structured-data block that includes sponsor, intervention, phase,
indication, and a list of forms / CRFs needed for the build. We just
print the response and let you eyeball it. If the skill returns JSON,
we attempt to pretty-print it; otherwise we print as-is.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env if python-dotenv is available; pydantic-settings normally
# handles this on import too.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from core.protocol_analysis_client import DEFAULT_SKILL_DIR, run_protocol_analysis

# Default protocol PDF location:
#   oc-ai-pipeline/services/study-build-trainer/tests/integration_*.py
#   ↑ parent[1]: study-build-trainer/
#   ↑ parent[2]: services/
#   ↑ parent[3]: oc-ai-pipeline/
#   + references/PrTK05_Protocol_v2_0_1.pdf
DEFAULT_PDF_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "references"
    / "PrTK05_Protocol_v2_0_1.pdf"
)


async def main(pdf_path: Path) -> None:
    if not pdf_path.is_file():
        print(f"ERROR: Protocol PDF not found at {pdf_path}")
        print(f"Pass a different path as the first CLI argument, e.g.:")
        print(f"  python {Path(__file__).name} /path/to/your/protocol.pdf")
        sys.exit(2)

    print(f"Reading protocol: {pdf_path}")
    pdf_bytes = pdf_path.read_bytes()
    print(f"  {len(pdf_bytes):,} bytes")

    print(f"Skill folder: {DEFAULT_SKILL_DIR}")
    if not (DEFAULT_SKILL_DIR / "SKILL.md").is_file():
        print(f"ERROR: SKILL.md not found at {DEFAULT_SKILL_DIR / 'SKILL.md'}")
        print("This usually means the trainer was extracted to its own repo.")
        print("Run from the in-tree location or pass skill_dir= explicitly.")
        sys.exit(2)

    print()
    print("Calling Claude (this may take 10–30 seconds)...")
    print()

    response_text = await run_protocol_analysis(pdf_bytes)

    print("─── RAW RESPONSE ───────────────────────────────────────────")
    print(response_text)
    print("────────────────────────────────────────────────────────────")
    print()

    # Try to extract a JSON block if one is present, just for nicer
    # display — totally optional.
    print("─── PARSED (best-effort JSON block) ────────────────────────")
    parsed = _try_parse_json_block(response_text)
    if parsed is None:
        print("No clean JSON block found in the response.")
    else:
        print(json.dumps(parsed, indent=2)[:4000])
        if len(json.dumps(parsed)) > 4000:
            print("... (truncated)")
    print("────────────────────────────────────────────────────────────")


def _try_parse_json_block(text: str) -> dict | list | None:
    """Best-effort: try to find and parse a JSON block in the response."""
    import re

    # 1) ```json ... ``` fenced block
    m = re.search(r"```json\s*\n(.+?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 2) Bare ``` ... ``` fenced block
    m = re.search(r"```\s*\n(\{.+?\})\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3) The whole response, if it happens to be pure JSON
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    return None


if __name__ == "__main__":
    pdf_path = (
        Path(sys.argv[1]).expanduser().resolve()
        if len(sys.argv) > 1
        else DEFAULT_PDF_PATH
    )
    asyncio.run(main(pdf_path))
