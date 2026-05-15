"""
Conventions engine — applies OpenClinica build conventions to a study spec.

Public API:
    apply_conventions(spec, study_id, customer_subdomain, repo_root=None)
    detect_conflict(new_convention, existing_conventions)

See conventions/README.md for the data model and
conventions/schema/dsl-operators.md for the DSL reference.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────

class ConventionsError(Exception):
    """Base exception for the conventions engine."""

class SchemaValidationError(ConventionsError):
    """A convention file failed JSON-Schema validation."""

class DSLEvaluationError(ConventionsError):
    """An applies_when or effect block could not be evaluated."""


# ──────────────────────────────────────────────────────────────────────
# Shared dataclasses
# ──────────────────────────────────────────────────────────────────────

@dataclass
class EntityContext:
    kind: str                 # "study" | "form" | "field" | "event" | "choice"
    entity: Dict[str, Any]    # the dict being filtered/mutated
    parent: Optional[Dict[str, Any]]  # parent entity (form for fields, etc.)
    spec: Dict[str, Any]      # full spec
    path: str                 # human-readable path e.g. "forms[2].survey[5]"

@dataclass
class EvaluateResult:
    matched: bool
    soft_hints: List[str] = field(default_factory=list)

@dataclass
class Mutation:
    directive: str            # "set" | "ensure" | "append_to" | "remove_from"
    path: str
    old_value: Any
    new_value: Any

@dataclass
class Flag:
    category: str             # e.g. "review_flags.constraint_review"
    message: str

@dataclass
class ApplyResult:
    mutations_made: List[Mutation] = field(default_factory=list)
    flags_raised: List[Flag] = field(default_factory=list)
    soft_directives: List[str] = field(default_factory=list)

@dataclass
class Overridden:
    convention_id: str
    scope: str                # "global" | "customer" | "vendor" | "study"
    kind: str
    would_have_done: str      # human-readable summary
    scope_id: str = ""        # customer subdomain / vendor slug / study_id; "" for global

@dataclass
class ResolvedConvention:
    convention: Dict[str, Any]
    overrode: List[Overridden] = field(default_factory=list)

@dataclass
class LoadError:
    path: str
    reason: str

@dataclass
class ConflictReport:
    has_conflict: bool
    natural_key_conflicts: List[Dict[str, Any]] = field(default_factory=list)
    semantic_conflicts: List[Dict[str, Any]] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Internal helper: iterate over entities matching a convention's target
# ──────────────────────────────────────────────────────────────────────

def iterate_targets(spec: Dict[str, Any], target_kind: str) -> Iterator[EntityContext]:
    """
    Yield EntityContext for each entity in `spec` matching `target_kind`.

    target_kind values and what they yield:
      "study"  → exactly one context wrapping the spec root
      "form"   → one context per form in spec["forms"]
      "field"  → one context per field of each form in spec["forms"][i]["survey"]
      "event"  → one context per visit in spec["timepoint_csv"]["rows"]
      "choice" → one context per choice in each form's spec["forms"][i]["choices"]
    """
    if target_kind == "study":
        yield EntityContext(
            kind="study",
            entity=spec,
            parent=None,
            spec=spec,
            path="",
        )
        return

    if target_kind == "form":
        for i, form in enumerate(spec.get("forms") or []):
            yield EntityContext(
                kind="form",
                entity=form,
                parent=spec,
                spec=spec,
                path=f"forms[{i}]",
            )
        return

    if target_kind == "field":
        for i, form in enumerate(spec.get("forms") or []):
            for j, field_item in enumerate(form.get("survey") or []):
                yield EntityContext(
                    kind="field",
                    entity=field_item,
                    parent=form,
                    spec=spec,
                    path=f"forms[{i}].survey[{j}]",
                )
        return

    if target_kind == "event":
        for k, row in enumerate((spec.get("timepoint_csv") or {}).get("rows") or []):
            yield EntityContext(
                kind="event",
                entity=row,
                parent=spec,
                spec=spec,
                path=f"timepoint_csv.rows[{k}]",
            )
        return

    if target_kind == "choice":
        for i, form in enumerate(spec.get("forms") or []):
            for j, choice in enumerate(form.get("choices") or []):
                yield EntityContext(
                    kind="choice",
                    entity=choice,
                    parent=form,
                    spec=spec,
                    path=f"forms[{i}].choices[{j}]",
                )
        return

    raise ConventionsError(f"Unknown target kind: {target_kind!r}")


# ──────────────────────────────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────────────────────────────

def _default_repo_root() -> Path:
    """Conventions store lives at <repo>/conventions/. The engine package
    is at <repo>/conventions_engine/, so the repo root is one level up."""
    return Path(__file__).resolve().parent.parent


def apply_conventions(
    spec: Dict[str, Any],
    study_id: str,
    customer_subdomain: str,
    migration_source: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Load active conventions, resolve cascade, apply to spec.

    `migration_source` is the vendor slug (e.g. "redcap", "castor") for
    migration builds, drawn from monday's source_edc_system column via
    `_vendor_slug_from_display_name` in pipeline.py. None or "" skips
    the vendor cascade bucket — correct for non-migration builds
    (fresh-protocol path).

    Mutates spec in place AND returns it. Idempotent (re-running on the
    same spec produces the same result) modulo timestamps in
    conventions_engine_applied entries.

    On a build with an empty conventions/ store, this function is a
    no-op aside from ensuring spec["study_meta"]["conventions_engine_applied"]
    exists as an empty list.
    """
    # Local imports keep this module's import graph clean from
    # circular reference between __init__ and the submodules below.
    from . import loader, cascade, applies_when, effects, render, record

    if repo_root is None:
        repo_root = _default_repo_root()

    record.ensure_section(spec)

    loaded = loader.load_all(repo_root, customer_subdomain, study_id,
                             migration_source=migration_source or "")

    # Surface load errors as review_flags entries so humans see them
    # — don't crash the whole pipeline on a single malformed file.
    review_flags = spec.setdefault("review_flags", {})
    rf_loaderrors = review_flags.setdefault("convention_load_errors", [])
    for err in loaded.get("errors", []):
        rf_loaderrors.append({"path": err.path, "reason": err.reason})

    resolved_list: List[ResolvedConvention] = cascade.resolve(loaded)

    prompt_blocks: List[str] = []

    for resolved in resolved_list:
        conv = resolved.convention
        target_kind = conv.get("target")
        if not target_kind:
            continue

        for entity_ctx in iterate_targets(spec, target_kind):
            apply_eval = applies_when.evaluate(
                conv.get("applies_when", {}),
                entity_ctx,
            )
            if not apply_eval.matched:
                continue

            applied = effects.apply_effect(
                conv.get("effect", {}),
                entity_ctx,
                spec,
                conv["id"],
            )

            record.record_application(
                spec,
                conv,
                applied_to=entity_ctx.path,
                effects_done=applied,
                overrode=resolved.overrode,
            )

            prompt_blocks.append(
                render.render_one(conv, apply_eval.soft_hints, applied.soft_directives)
            )

    # Park prompt-injection text under study_meta so prompts.py can
    # pluck it out later. Phase C wires the actual injection.
    spec.setdefault("study_meta", {})["conventions_prompt_block"] = (
        render.render_prompt_block(prompt_blocks)
    )

    return spec


def detect_conflict(
    new_convention: Dict[str, Any],
    existing_conventions: List[Dict[str, Any]],
) -> ConflictReport:
    """Used at promotion time. See conventions_engine.intersection."""
    from . import intersection
    return intersection.detect_conflict(new_convention, existing_conventions)


__all__ = [
    "apply_conventions",
    "detect_conflict",
    "ConventionsError",
    "SchemaValidationError",
    "DSLEvaluationError",
    "EntityContext",
    "EvaluateResult",
    "ApplyResult",
    "Mutation",
    "Flag",
    "Overridden",
    "ResolvedConvention",
    "LoadError",
    "ConflictReport",
]
