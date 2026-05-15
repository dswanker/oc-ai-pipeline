"""Cascade resolver: study > {customer, vendor} > global, by natural_key.

Customer and vendor are peer axes at scope_order=1 per F2 sub-decision A.
When both scopes resolve the same natural_key, customer wins — OC house
conventions take precedence over vendor defaults. This is achieved by
iteration ORDER (vendor before customer in the build loop below), not
by an explicit dominance check, mirroring how customer-overrides-global
is achieved today. The losing vendor convention surfaces in the
winner's `overrode[]` list, the same way a customer convention would
surface in a study-scope winner's overrode. Non-migration builds
(loaded["vendor"] == []) are unaffected.
"""
from __future__ import annotations
from typing import Any, Dict, List

from . import ResolvedConvention, Overridden


def _summarize_effect(conv: Dict[str, Any]) -> str:
    """Human-readable one-liner for an Overridden record."""
    kind = conv.get("kind", "?")
    if kind == "advisory":
        desc = conv.get("description", "")
        return f"advisory: {desc[:80]}{'...' if len(desc) > 80 else ''}"
    effect = conv.get("effect") or {}
    parts: List[str] = []
    if "set" in effect:
        parts.append(f"set {list(effect['set'].keys())}")
    if "ensure" in effect:
        parts.append(f"ensure {list(effect['ensure'].keys())}")
    if "require" in effect:
        parts.append(f"require {effect['require']}")
    if "flag" in effect:
        parts.append("raise a review flag")
    if "append_to" in effect:
        parts.append(f"append to {list(effect['append_to'].keys())}")
    if "remove_from" in effect:
        parts.append(f"remove from {list(effect['remove_from'].keys())}")
    if "soft" in effect:
        parts.append(f"advise Claude: {effect['soft'][:60]}")
    return "; ".join(parts) if parts else "(no effect)"


def resolve(loaded: Dict[str, Any]) -> List[ResolvedConvention]:
    """
    Resolve the cascade. Returns the list of conventions that will
    actually be applied, each with metadata about any masked-out
    conventions at lower-precedence scopes.

    Precedence: study (highest) > customer > vendor > global (lowest).
    Customer and vendor are peers (both scope_order=1 in output sort).
    Iteration order below puts vendor before customer so customer
    overrides vendor on natural_key collision — per F2 sub-decision A.
    Same scope + same natural_key: undefined — emit both with no
    masking, since promotion-time conflict detection should have
    prevented this from happening.
    """
    by_key: Dict[str, Dict[str, Any]] = {}  # natural_key → {"winner": conv, "overrode": [Overridden]}

    # Build in reverse precedence so later inserts overwrite earlier.
    # global → vendor → customer (peers; customer wins per F2-A) → study.

    for scope_name in ("global", "vendor", "customer", "study"):
        for conv in loaded.get(scope_name) or []:
            nk = conv.get("natural_key")
            if not nk:
                continue
            if nk not in by_key:
                by_key[nk] = {"winner": conv, "overrode": []}
            else:
                prev = by_key[nk]["winner"]
                by_key[nk]["overrode"].append(Overridden(
                    convention_id=prev.get("id", "?"),
                    scope=prev.get("scope", "?"),
                    kind=prev.get("kind", "?"),
                    would_have_done=_summarize_effect(prev),
                    scope_id=prev.get("scope_id", ""),
                ))
                by_key[nk]["winner"] = conv

    # Conventions that lack a natural_key (shouldn't happen post-validation,
    # but be defensive) are passed through with no cascade interaction.
    pass_through: List[Dict[str, Any]] = []
    for scope_name in ("global", "vendor", "customer", "study"):
        for conv in loaded.get(scope_name) or []:
            if not conv.get("natural_key"):
                pass_through.append(conv)

    out: List[ResolvedConvention] = []
    for entry in by_key.values():
        out.append(ResolvedConvention(
            convention=entry["winner"],
            overrode=entry["overrode"],
        ))
    for conv in pass_through:
        out.append(ResolvedConvention(convention=conv, overrode=[]))

    # Sort for deterministic application order: by scope (global first
    # so study overrides are obvious in conventions_engine_applied output),
    # then by id alphabetically.
    # Peer axis per F2 sub-decision A; id-alphabetical tiebreak for ties at scope_order=1.
    scope_order = {"global": 0, "vendor": 1, "customer": 1, "study": 2}
    out.sort(key=lambda r: (
        scope_order.get(r.convention.get("scope", ""), 99),
        r.convention.get("id", ""),
    ))
    return out
