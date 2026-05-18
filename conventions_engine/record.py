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


def _to_absolute_path(dsl_path: str, applied_to: str, target: str) -> str:
    """Translate an effect-DSL path to a spec-absolute (JSONPath-style) path.

    Effect-DSL paths are entity-relative: `form.visits_assigned`,
    `field.constraint`, `study.review_flags`. Spec-absolute paths are rooted
    at the spec dict: `forms[3].visits_assigned`, `forms[3].survey[2].constraint`.

    Phase C.2's diff.deep_diff() emits spec-absolute paths; attribution
    matches by string equality, so we translate at recording time.

    Translation cases (when prefix == DSL entity name):
      study.X            → X                              (writes to spec root)
      <target>.X         → <applied_to>.X                 (same-entity write)
      form.X     (field) → <applied_to with last segment stripped>.X
                                                          (field convention
                                                           reaches into parent form)
      form.X     (choice)→ <applied_to with last segment stripped>.X
                                                          (choice → parent form)

    If the dsl_path has no entity prefix (no dot), it's returned as-is —
    matches the effect DSL's "single segment with no recognised entity"
    edge case which _set_path would reject anyway.
    """
    if "." not in dsl_path:
        return dsl_path
    prefix, suffix = dsl_path.split(".", 1)

    if prefix == "study":
        return suffix

    if prefix == target:
        return f"{applied_to}.{suffix}" if applied_to else suffix

    # Parent-traversal: field/choice convention writes to its parent form.
    if target in ("field", "choice") and prefix == "form":
        if "." in applied_to:
            parent = applied_to.rsplit(".", 1)[0]
        else:
            parent = applied_to
        return f"{parent}.{suffix}" if parent else suffix

    # Unrecognised combination — concatenate and hope for the best. Attribution
    # will silently no-op on a path that doesn't match the diff; that's a
    # degraded-but-non-broken outcome (loss is a False None in convention_id,
    # not a crash).
    return f"{applied_to}.{suffix}" if applied_to else dsl_path


def _extract_mutations(effects_done: ApplyResult, applied_to: str,
                       target: str) -> List[Dict[str, Any]]:
    """Serialize the per-mutation paths from an ApplyResult to the
    machine-readable shape attribution.attribute_changes() consumes.

    Each mutation row:
      {"path": <spec-absolute>, "directive": str, "old": Any, "new": Any}

    Flags and soft directives are NOT mutations (they don't change leaf
    values on the spec tree), so they're excluded — they wouldn't show up
    in a pre/post diff regardless. _summarize_effects covers them for the
    human-readable summary.
    """
    out: List[Dict[str, Any]] = []
    for m in effects_done.mutations_made:
        abs_path = _to_absolute_path(m.path, applied_to, target)
        out.append({
            "path":      abs_path,
            "directive": m.directive,
            "old":       m.old_value,
            "new":       m.new_value,
        })
    return out


def record_application(spec: Dict[str, Any], convention: Dict[str, Any],
                       applied_to: str, effects_done: ApplyResult,
                       overrode: List[Overridden]) -> None:
    """Append one conventions_engine_applied entry to study_meta.

    Entry shape:
      convention_id  — the convention's id (dotted-snake)
      scope          — global / customer / vendor / study
      kind           — structured / hybrid / advisory
      applied_to     — entity path string from EntityContext.path
      effect_summary — human-readable one-line summary (existing, unchanged)
      mutations      — machine-readable per-mutation list (Phase C.2 addition):
                       each is {path, directive, old, new}, path is
                       spec-absolute for attribution matching against
                       diff.deep_diff() output
      overrode       — only present when this winner masked lower-scope
                       conventions (cascade overrides)
    """
    ensure_section(spec)

    target = convention.get("target", "")
    entry: Dict[str, Any] = {
        "convention_id": convention.get("id"),
        "scope":         convention.get("scope"),
        "kind":          convention.get("kind"),
        "applied_to":    applied_to,
        "effect_summary": _summarize_effects(effects_done),
        "mutations":     _extract_mutations(effects_done, applied_to, target),
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
