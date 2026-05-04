"""
Customer Conventions Client.

Reads CQ-prefixed columns from the monday item to extract customer-provided
convention answers, and merges them with the Convention Rulebook at build
time. Customer answers always supersede rulebook conventions on conflicts.

Two title formats are supported, both with prefix "CQ":
  * Preferred (new):  "CQ How would you like X organized?"  ← title IS the question
  * Legacy:           "CQ_How_Would_You_Like_X_Organized"   ← short identifier form

The new format is richer for the AI because the full question text becomes
part of the prompt context. Both formats remain valid so existing columns
keep working — rename at your leisure in the monday UI.

Extensibility contract: adding new questions to the monday board only requires
adding a new column with a CQ prefix. The pipeline picks them up automatically
— no code change, no deployment.

Storage: customer answers persist to /data/customer_conventions/<item_id>.json
on the Railway volume so they survive redeploys and re-triggers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import structlog
    logger = structlog.get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


CUSTOMER_CONVENTIONS_ROOT = Path(
    os.environ.get("CUSTOMER_CONVENTIONS_ROOT", "/data/customer_conventions")
)
RULEBOOK_PATH = Path(
    os.environ.get("RULEBOOK_PATH", "/data/rulebook/conventions.json")
)

CQ_PREFIX_NEW    = "CQ "   # human-readable: "CQ How do you want X?"
CQ_PREFIX_LEGACY = "CQ_"   # short identifier: "CQ_How_Do_You_Want_X"


def _strip_cq_prefix(title: str) -> str:
    """
    Remove the CQ prefix and normalize the question text for use as a dict key.
    Legacy underscore titles get converted to space-separated phrases so the
    prompt block reads naturally regardless of which format the column uses.
    """
    if title.startswith(CQ_PREFIX_NEW):
        return title[len(CQ_PREFIX_NEW):].strip()
    if title.startswith(CQ_PREFIX_LEGACY):
        return title[len(CQ_PREFIX_LEGACY):].replace("_", " ").strip()
    return title


def _is_convention_column(title: str, col_id: str) -> bool:
    """True if the column is a customer convention question."""
    return (
        title.startswith(CQ_PREFIX_NEW)
        or title.startswith(CQ_PREFIX_LEGACY)
        or col_id.startswith(CQ_PREFIX_LEGACY)
    )


def extract_customer_conventions(
    columns: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """
    Extract customer-provided convention answers from a monday item's columns.

    Returns:
        Dict of question_text -> answer_text. The CQ prefix is stripped from
        each title so the question itself becomes the key — giving Claude the
        full question as context when conventions are injected into prompts.
        Empty answers are skipped (absent → fall through to rulebook).
    """
    out: dict[str, str] = {}
    for col_id, col in columns.items():
        title = (col.get("title") or "").strip()
        if not _is_convention_column(title, col_id):
            continue
        value = (col.get("value") or "").strip()
        if not value:
            continue
        question = _strip_cq_prefix(title)
        out[question] = value
    return out


def save_customer_conventions(
    item_id: int,
    conventions: dict[str, str],
) -> Path:
    """Persist customer conventions to the Railway volume."""
    CUSTOMER_CONVENTIONS_ROOT.mkdir(parents=True, exist_ok=True)
    path = CUSTOMER_CONVENTIONS_ROOT / f"{item_id}.json"
    payload = {
        "item_id": item_id,
        "source": "customer_form_input",
        "supersedes_rulebook": True,
        "conventions": conventions,
    }
    path.write_text(json.dumps(payload, indent=2))
    logger.info("customer_conventions.saved",
                item_id=item_id, count=len(conventions), path=str(path))
    return path


def load_customer_conventions(item_id: int) -> dict[str, str]:
    """Load previously-saved customer conventions for this item, if any."""
    path = CUSTOMER_CONVENTIONS_ROOT / f"{item_id}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("conventions", {})
    except (json.JSONDecodeError, OSError):
        return {}


def load_rulebook_conventions() -> dict[str, Any]:
    """Load approved conventions from the Convention Rulebook (empty if missing)."""
    if not RULEBOOK_PATH.exists():
        return {}
    try:
        return json.loads(RULEBOOK_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("rulebook.load_failed",
                       path=str(RULEBOOK_PATH), error=str(exc))
        return {}


def merge_conventions(
    rulebook: dict[str, Any],
    customer: dict[str, str],
) -> dict[str, Any]:
    """Merge rulebook with customer answers — customer wins on conflicts."""
    effective: dict[str, Any] = {}
    effective.update(rulebook or {})
    effective.update(customer or {})
    return {
        "from_rulebook": rulebook or {},
        "from_customer": customer or {},
        "effective":     effective,
    }


def build_conventions_prompt_block(merged: dict[str, Any]) -> str:
    """Format merged conventions as a prompt-ready block for Claude calls."""
    eff = merged.get("effective", {})
    if not eff:
        return ""

    lines = ["## Customer + Rulebook Conventions",
             "Apply these conventions when generating output. "
             "Customer-provided answers take precedence over rulebook defaults."]
    for key, val in sorted(eff.items()):
        source = "customer" if key in merged.get("from_customer", {}) else "rulebook"
        lines.append(f"- **{key}** [{source}]: {val}")
    return "\n".join(lines) + "\n"
