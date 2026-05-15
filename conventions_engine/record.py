"""
Accumulate conventions_engine_applied entries into study_meta.

Each application of a convention to a single entity produces one
entry. The list lives at spec.study_meta.conventions_engine_applied and is
rendered into the spec PDF/XLSX output by the spec builder.
"""
from __future__ import annotations
from typing import Any, Dict, List

from . import ApplyResult, Overridden


def ensure_section(spec: Dict[str, Any]) -> None:
    """Guarantee study_meta.conventions_engine_applied and
    study_meta.customer_vendor_conflicts exist as lists."""
    sm = spec.setdefault("study_meta", {})
    if not isinstance(sm.get("conventions_engine_applied"), list):
        sm["conventions_engine_applied"] = []
    if not isinstance(sm.get("customer_vendor_conflicts"), list):
        sm["customer_vendor_conflicts"] = []


def _maybe_record_customer_vendor_conflict(
    spec: Dict[str, Any],
    convention: Dict[str, Any],
    overrode: List[Overridden],
) -> None:
    """If this application is a customer winner over one or more vendor
    losers, append entries to study_meta.customer_vendor_conflicts.
    Per F2 sub-decision A — elevates implicit cascade overrides into a
    discoverable top-level list so reviewers can see customer/vendor
    rule clashes without grep-walking conventions_engine_applied."""
    if convention.get("scope") != "customer":
        return
    customer_id = convention.get("scope_id", "")
    nk = convention.get("natural_key")
    bucket = spec["study_meta"]["customer_vendor_conflicts"]
    for ov in overrode:
        if ov.scope != "vendor":
            continue
        bucket.append({
            "natural_key": nk,
            "customer_id": customer_id,
            "vendor_slug": ov.scope_id,
            "winner": "customer",
            "losing_effect_summary": ov.would_have_done,
        })


def _summarize_effects(effects_done: ApplyResult) -> str:
    """Human-readable summary of what an effect actually did."""
    parts: List[str] = []
    for m in effects_done.mutations_made:
        parts.append(f"{m.directive} {m.path}")
    for f in effects_done.flags_raised:
        parts.append(f"flag {f.category}")
    for s in effects_done.soft_directives:
        parts.append(f"advise: {s[:40]}{'...' if len(s) > 40 else ''}")
    return "; ".join(parts) if parts else "(no-op for this entity)"


def record_application(spec: Dict[str, Any], convention: Dict[str, Any],
                       applied_to: str, effects_done: ApplyResult,
                       overrode: List[Overridden]) -> None:
    """Append one conventions_engine_applied entry to study_meta."""
    ensure_section(spec)

    entry: Dict[str, Any] = {
        "convention_id": convention.get("id"),
        "scope":         convention.get("scope"),
        "kind":          convention.get("kind"),
        "applied_to":    applied_to,
        "effect_summary": _summarize_effects(effects_done),
    }

    if overrode:
        entry["overrode"] = [
            {
                "convention_id": ov.convention_id,
                "scope":         ov.scope,
                "scope_id":      ov.scope_id,
                "kind":          ov.kind,
                "would_have_done": ov.would_have_done,
            }
            for ov in overrode
        ]

    spec["study_meta"]["conventions_engine_applied"].append(entry)
    _maybe_record_customer_vendor_conflict(spec, convention, overrode)
