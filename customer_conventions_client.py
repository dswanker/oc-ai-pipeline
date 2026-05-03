"""
Customer Conventions Client.

Reads `CQ_*` columns from the monday item to extract customer-provided
convention answers, and merges them with the Convention Rulebook at
build time. Customer answers always supersede rulebook conventions.

Extensibility: adding new questions to the monday board only requires
adding a new column with a `CQ_` prefix. The pipeline picks them up
automatically — no code change, no deployment needed.

Storage: customer conventions are persisted to
  /data/customer_conventions/<item_id>.json
on the Railway volume so they survive redeploys and are available on
re-triggers without re-fetching from monday.
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


# Storage root on the Railway persistent volume
CUSTOMER_CONVENTIONS_ROOT = Path(
    os.environ.get("CUSTOMER_CONVENTIONS_ROOT", "/data/customer_conventions")
)

# Rulebook conventions location (where the trainer writes approved conventions)
RULEBOOK_PATH = Path(
    os.environ.get("RULEBOOK_PATH", "/data/rulebook/conventions.json")
)

# Prefix that identifies customer-provided convention question columns.
# This is the entire extensibility contract: any column with this prefix is a
# convention question. Add a new column with this prefix in monday → pipeline
# picks it up on the next run, no code change required.
CONVENTION_QUESTION_PREFIX = "CQ_"


def extract_customer_conventions(
    columns: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """
    Extract customer-provided convention answers from a monday item's columns.

    Args:
        columns: Mapping of column_id -> {"title": str, "value": str|None, ...}
                 as returned by monday's items_page query.

    Returns:
        Dict of question_title -> answer_text. Empty answers are skipped so
        absent answers fall through to the rulebook defaults at merge time.

    Recognition rule: any column whose title OR id starts with `CQ_`.
    """
    out: dict[str, str] = {}
    for col_id, col in columns.items():
        title = (col.get("title") or "").strip()
        is_question = (
            title.startswith(CONVENTION_QUESTION_PREFIX)
            or col_id.startswith(CONVENTION_QUESTION_PREFIX)
        )
        if not is_question:
            continue
        value = (col.get("value") or "").strip()
        if not value:
            continue  # absent answer — let rulebook fill in
        # Use the title as the canonical key. This keeps the convention dict
        # readable and stable across column-id changes.
        out[title] = value
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
    logger.info(
        "customer_conventions.saved",
        item_id=item_id, count=len(conventions), path=str(path),
    )
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
    """
    Load approved conventions from the Convention Rulebook.

    Returns an empty dict if the rulebook file doesn't exist yet (e.g. before
    any conventions have been submitted via the trainer).
    """
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
    """
    Merge rulebook conventions with customer-provided answers.

    Customer answers always supersede rulebook entries on key conflicts.
    Returns a flat dict suitable for injecting into prompts at build time.

    Output structure:
        {
            "from_rulebook": {...},     # baseline conventions
            "from_customer": {...},     # customer answers (override layer)
            "effective":     {...},     # merged result, customer wins
        }
    """
    effective: dict[str, Any] = {}
    effective.update(rulebook or {})
    effective.update(customer or {})  # customer wins
    return {
        "from_rulebook": rulebook or {},
        "from_customer": customer or {},
        "effective":     effective,
    }


def build_conventions_prompt_block(merged: dict[str, Any]) -> str:
    """
    Format merged conventions as a prompt-ready block for injection into
    Claude calls (form spec generation, EDC structure, etc.).

    Returns empty string if there are no effective conventions, so callers
    can safely concatenate.
    """
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
