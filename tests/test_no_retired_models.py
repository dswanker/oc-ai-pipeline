"""No retired Anthropic model ID remains in the codebase.

claude-sonnet-4-20250514 was retired on 2026-06-15; every call to it fails, and the email intake then files every email
as "needs review". The list below is every model Anthropic's model deprecations page showed as retired on 2026-10-02.
Add to it when a model is retired, and replace the model in the code in the same change.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RETIRED = (
    "claude-sonnet-4-20250514",      # retired 2026-06-15; replacement claude-sonnet-4-6
    "claude-opus-4-20250514",        # retired 2026-06-15
    "claude-opus-4-1-20250805",      # retired 2026-08-05
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
    "claude-3-5-haiku-20241022",
    "claude-3-haiku-20240307",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-2.1",
    "claude-2.0",
    "claude-instant-1.2",
)
SELF = Path(__file__).resolve()


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True)
    if out.returncode == 0 and out.stdout.strip():
        return [ROOT / f for f in out.stdout.splitlines()]
    return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts]


def test_no_retired_model_id_in_the_codebase():
    found = []
    for path in tracked_files():
        if path.resolve() == SELF or not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for model in RETIRED:
            if model in text:
                line = next(n for n, ln in enumerate(text.splitlines(), 1) if model in ln)
                found.append(f"{path.relative_to(ROOT)}:{line} uses {model}")
    assert not found, "Retired model IDs (calls to them fail):\n" + "\n".join(found)
