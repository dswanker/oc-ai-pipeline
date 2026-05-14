"""
Prompt-text and spec-output renderers.

The actual injection of `render_prompt_block` output into prompts.py
happens in Phase C. This module just produces the text.
"""
from __future__ import annotations
from typing import Any, Dict, List


def render_one(conv: Dict[str, Any], applies_when_soft: List[str],
               effect_soft: List[str]) -> str:
    """Render one resolved convention as a prompt-injection block."""
    parts: List[str] = []
    parts.append(f"### {conv.get('title', conv.get('id', '<unnamed>'))} "
                 f"[{conv.get('kind', '?')}, {conv.get('scope', '?')}]")

    desc = conv.get("description", "").strip()
    if desc:
        parts.append(desc)

    if applies_when_soft:
        parts.append("Apply when (Claude judgment): " + "; ".join(applies_when_soft))

    if effect_soft:
        parts.append("Then (Claude judgment): " + "; ".join(effect_soft))

    return "\n".join(parts)


def render_prompt_block(blocks: List[str]) -> str:
    """Combine per-convention blocks into one prompt-section payload."""
    if not blocks:
        return ""
    header = "## Active Conventions\n\nThe following conventions apply to this build:\n"
    return header + "\n\n".join(blocks) + "\n"


def render_overrides_for_spec(resolved) -> List[Dict[str, Any]]:
    """
    Format any override records (a study-scope convention masking a
    global/customer one) for the spec PDF/XLSX output. Returns a list
    of dicts ready for the spec builder to render in a table.
    """
    out: List[Dict[str, Any]] = []
    for res in resolved:
        for ov in res.overrode:
            out.append({
                "winning_id": res.convention.get("id"),
                "winning_scope": res.convention.get("scope"),
                "overridden_id": ov.convention_id,
                "overridden_scope": ov.scope,
                "overridden_kind": ov.kind,
                "would_have_done": ov.would_have_done,
            })
    return out
