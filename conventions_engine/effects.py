"""Effect DSL applier. See conventions/schema/dsl-operators.md."""
from __future__ import annotations
import copy
import re
from typing import Any, Dict, List

from . import ApplyResult, Mutation, Flag, DSLEvaluationError, EntityContext
from .applies_when import _resolve_path, _SENTINEL_MISSING


# ──────────────────────────────────────────────────────────────────────
# Path-based writes
# ──────────────────────────────────────────────────────────────────────

def _set_path(path: str, value: Any, ctx: EntityContext) -> Any:
    """
    Write `value` into the entity context at `path`. Returns the old
    value (or _SENTINEL_MISSING). Only supports simple dotted paths
    rooted at the same place applies_when reads from; fan-outs and
    .length are not writable.
    """
    parts = path.split(".")
    head = parts[0]
    rest = parts[1:]

    if head == "study":
        current: Any = ctx.spec
    elif head == "form":
        if ctx.kind == "form":
            current = ctx.entity
        elif ctx.kind in ("field", "choice"):
            current = ctx.parent
        else:
            raise DSLEvaluationError(f"Cannot write to {path!r} for target {ctx.kind!r}")
    elif head == "field":
        if ctx.kind == "field":
            current = ctx.entity
        else:
            raise DSLEvaluationError(f"Cannot write to {path!r} for target {ctx.kind!r}")
    elif head == "event":
        if ctx.kind == "event":
            current = ctx.entity
        else:
            raise DSLEvaluationError(f"Cannot write to {path!r} for target {ctx.kind!r}")
    elif head == "choice":
        if ctx.kind == "choice":
            current = ctx.entity
        else:
            raise DSLEvaluationError(f"Cannot write to {path!r} for target {ctx.kind!r}")
    else:
        raise DSLEvaluationError(f"Unknown path root: {head!r}")

    if not rest:
        raise DSLEvaluationError(f"Cannot overwrite root context {path!r}")

    for step in rest[:-1]:
        if "[*]" in step or step == "length":
            raise DSLEvaluationError(f"Fan-out / length not writable: {path!r}")
        if not isinstance(current, dict):
            raise DSLEvaluationError(f"Cannot traverse non-dict at {step!r} in {path!r}")
        if step not in current:
            current[step] = {}
        current = current[step]

    final_key = rest[-1]
    if "[*]" in final_key or final_key == "length":
        raise DSLEvaluationError(f"Fan-out / length not writable: {path!r}")
    if not isinstance(current, dict):
        raise DSLEvaluationError(f"Cannot write to non-dict at {final_key!r} in {path!r}")
    old = current.get(final_key, _SENTINEL_MISSING)
    current[final_key] = value
    return old


# ──────────────────────────────────────────────────────────────────────
# Template variable substitution for flag messages
# ──────────────────────────────────────────────────────────────────────

_TEMPLATE_VAR = re.compile(r"\$\{([^}]+)\}")


def _interpolate(template: str, ctx: EntityContext) -> str:
    """Replace ${path} occurrences with resolved values."""
    def _sub(m: "re.Match[str]") -> str:
        path = m.group(1)
        val = _resolve_path(path, ctx)
        if val is _SENTINEL_MISSING:
            return f"<unresolved:{path}>"
        return str(val)
    return _TEMPLATE_VAR.sub(_sub, template)


# ──────────────────────────────────────────────────────────────────────
# Effect directives
# ──────────────────────────────────────────────────────────────────────

def _do_set(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    for path, value in payload.items():
        old = _set_path(path, value, ctx)
        old_repr = None if old is _SENTINEL_MISSING else old
        result.mutations_made.append(Mutation(
            directive="set", path=path, old_value=old_repr, new_value=value,
        ))


def _do_ensure(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    for path, value in payload.items():
        current = _resolve_path(path, ctx)
        if current is _SENTINEL_MISSING or current is None or current == "" or current == [] or current == {}:
            _set_path(path, value, ctx)
            result.mutations_made.append(Mutation(
                directive="ensure", path=path, old_value=None, new_value=value,
            ))


def _do_require(payload: Any, ctx: EntityContext, result: ApplyResult) -> None:
    paths = payload if isinstance(payload, list) else [payload]
    for path in paths:
        val = _resolve_path(path, ctx)
        # For fan-out paths, val is a list — require each non-empty
        if isinstance(val, list):
            empties = [i for i, v in enumerate(val) if v in (None, "", [], {}, _SENTINEL_MISSING)]
            if empties:
                result.flags_raised.append(Flag(
                    category="review_flags.constraint_review",
                    message=f"Required path {path} has empty values at indexes {empties}",
                ))
        elif val is _SENTINEL_MISSING or val in (None, "", [], {}):
            result.flags_raised.append(Flag(
                category="review_flags.constraint_review",
                message=f"Required path {path} is empty",
            ))


def _do_flag(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    category = payload.get("category", "review_flags.constraint_review")
    message_template = payload.get("message", "")
    result.flags_raised.append(Flag(
        category=category,
        message=_interpolate(message_template, ctx),
    ))


def _do_append_to(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    for path, value in payload.items():
        current = _resolve_path(path, ctx)
        if current is _SENTINEL_MISSING:
            _set_path(path, [value], ctx)
            result.mutations_made.append(Mutation(
                directive="append_to", path=path, old_value=None, new_value=[value],
            ))
            continue
        if not isinstance(current, list):
            raise DSLEvaluationError(f"append_to target {path!r} is not a list")
        if value in current:
            continue  # idempotent
        new = current + [value]
        _set_path(path, new, ctx)
        result.mutations_made.append(Mutation(
            directive="append_to", path=path, old_value=current, new_value=new,
        ))


def _do_remove_from(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    for path, value in payload.items():
        current = _resolve_path(path, ctx)
        if current is _SENTINEL_MISSING or not isinstance(current, list):
            continue
        if value not in current:
            continue
        new = [x for x in current if x != value]
        _set_path(path, new, ctx)
        result.mutations_made.append(Mutation(
            directive="remove_from", path=path, old_value=current, new_value=new,
        ))


def _do_match(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    """
    Conditional dispatch on a resolved path value.

    Shape:
      {
        "on": "<dotted-path>",
        "cases": { "<case-value>": { <sub-effect-block> }, ... },
        "default": { <sub-effect-block> }   # optional
      }

    Resolves `on` against the current context. If the resolved value
    is a key in `cases`, dispatches that case's sub-effect-block via
    the existing DIRECTIVES table. If no case matches and `default`
    is provided, dispatches `default`. Otherwise silent no-op
    (including when `on` resolves to _SENTINEL_MISSING).

    Case keys are exact-match, case-sensitive (Python dict semantics).
    Sub-effect-blocks may contain any directive including nested match;
    they may NOT contain `soft` (parent excludes soft for non-advisory
    conventions). Flag-flushing into spec.review_flags happens once in
    the outer apply_effect call, so this helper does not flush.
    """
    if not isinstance(payload, dict):
        raise DSLEvaluationError(f"match payload must be a dict, got {type(payload).__name__}")
    if "on" not in payload or "cases" not in payload:
        raise DSLEvaluationError("match requires both 'on' and 'cases' keys")
    on_path = payload["on"]
    cases = payload["cases"]
    if not isinstance(cases, dict):
        raise DSLEvaluationError("match 'cases' must be a dict of case-value -> sub-effect-block")

    actual = _resolve_path(on_path, ctx)
    selected: Any = _SENTINEL_MISSING
    if actual is not _SENTINEL_MISSING and actual in cases:
        selected = cases[actual]
    elif "default" in payload:
        selected = payload["default"]

    if selected is _SENTINEL_MISSING:
        return  # no case matched, no default; silent no-op
    if not isinstance(selected, dict):
        raise DSLEvaluationError("match case / default must be a sub-effect-block (dict)")

    for sub_key, sub_payload in selected.items():
        if sub_key == "soft":
            raise DSLEvaluationError("match sub-effect-blocks may not contain 'soft'")
        if sub_key not in DIRECTIVES:
            raise DSLEvaluationError(f"Unknown effect directive {sub_key!r} inside match case")
        DIRECTIVES[sub_key](sub_payload, ctx, result)


def _do_default_value(payload, ctx: "EntityContext", result: "ApplyResult") -> None:
    """
    Write a default value to the XLSForm `default` column on a field.

    Shape:
      "default_value": "Y"           # bare value, the value to write

    Field-scoped directive ONLY. Writes to `field.default`. Only-if-empty
    semantics, matching `ensure`: if `field.default` is already populated,
    this directive is a no-op (a higher-precedence convention or upstream
    process already set it; we don't overwrite).

    Use this directive when the rule is "pre-populate the cell with X but
    let the user change it" — different from `set` (overwrite, even if
    populated) and from `ensure` (write to any field column, not just
    the XLSForm default column).

    OC-7 7F's AESEV → AESER cascade lands as a `match` with `default_value`
    inside the AESER case.

    Errors:
      - non-field context → DSLEvaluationError
      - empty / None payload → DSLEvaluationError (clearing a default is
        almost certainly a bug; use `set` with explicit null if intended)
    """
    if ctx.kind != "field":
        raise DSLEvaluationError(
            f"default_value requires field-scoped context, got {ctx.kind!r}"
        )
    if payload is None or payload == "":
        raise DSLEvaluationError(
            "default_value payload must be a non-empty value"
        )

    current = _resolve_path("field.default", ctx)
    if current is _SENTINEL_MISSING or current is None or current == "":
        _set_path("field.default", payload, ctx)
        result.mutations_made.append(Mutation(
            directive="default_value",
            path="field.default",
            old_value=None,
            new_value=payload,
        ))


def _do_use_canonical_list(payload: Any, ctx: EntityContext, result: ApplyResult,
                           canonical_lists: Dict[str, Any]) -> None:
    """
    Point a field at a shared canonical choice list.

    Shape: "use_canonical_list": "<name>"   — <name> is a filename
    stem under conventions/canonical_lists/, e.g. "race".

    Sets field.type to "select_one <canonical list_name>" (or
    select_multiple, per the canonical file's multi_select flag),
    and merges the canonical file's choices into the parent form's
    choices array — additive and idempotent: existing choices under
    the same list_name are left alone, only missing ones are appended,
    so re-running on an already-migrated field is a no-op.

    Field-scoped only, mirroring default_value. Takes canonical_lists
    as an explicit parameter (not via ctx) since it's loaded once per
    build in apply_conventions and threaded through — not part of the
    per-entity context the other directives read from.
    """
    if ctx.kind != "field":
        raise DSLEvaluationError(
            f"use_canonical_list requires field-scoped context, got {ctx.kind!r}"
        )
    if not isinstance(payload, str) or not payload:
        raise DSLEvaluationError(
            "use_canonical_list payload must be a non-empty canonical list name"
        )
    if payload not in canonical_lists:
        raise DSLEvaluationError(
            f"Unknown canonical list {payload!r} — no file at "
            f"conventions/canonical_lists/{payload}.json"
        )

    canonical = canonical_lists[payload]
    list_name = canonical["list_name"]
    is_multi = bool(canonical.get("multi_select", False))
    new_type = f"{'select_multiple' if is_multi else 'select_one'} {list_name}"

    old_type = ctx.entity.get("type")
    if old_type != new_type:
        ctx.entity["type"] = new_type
        result.mutations_made.append(Mutation(
            directive="use_canonical_list", path="field.type",
            old_value=old_type, new_value=new_type,
        ))

    form = ctx.parent
    if not isinstance(form, dict):
        raise DSLEvaluationError("use_canonical_list: field has no parent form to hold choices")
    form_choices = form.setdefault("choices", [])
    existing_names = {
        c.get("name") for c in form_choices if c.get("list_name") == list_name
    }
    added = 0
    for choice in canonical["choices"]:
        if choice.get("name") in existing_names:
            continue
        merged = dict(choice)
        merged["list_name"] = list_name
        form_choices.append(merged)
        existing_names.add(choice.get("name"))
        added += 1

    if added:
        result.mutations_made.append(Mutation(
            directive="use_canonical_list", path="form.choices",
            old_value=None, new_value=f"+{added} choice(s) from canonical list {payload!r}",
        ))


# Internal marker used by _do_move_to_form / sweep_pending_removals. Not a
# real spec field -- stripped from every row before it's actually written
# anywhere a field is read for output (XLSForm build, board.json, etc.
# never see it, since the sweep removes tombstoned rows from their old
# form before those consumers run).
_PENDING_REMOVAL_KEY = "__pending_removal__"


def _do_move_to_form(payload: Any, ctx: EntityContext, result: ApplyResult) -> None:
    """
    Move a field's survey row to a different form in the same study.

    Shape: "move_to_form": "<form_id>"

    Field-scoped only. Appends the field to the target form's survey
    list immediately (a different list object than whatever is
    currently being iterated, so this part is always safe), copies
    across any choices it references that the target form doesn't
    already have, and repoints bind__oc_itemgroup to the target
    form_id.

    The SOURCE row is deliberately NOT removed from its old form's
    survey list here. Deleting a row mid-iteration of the same list a
    field-targeted convention is walking risks silently skipping the
    next matching row in that same form for that convention -- a live
    Python list mutated during enumerate(). Instead the source row is
    tombstoned with an internal "__pending_removal__" marker, and
    apply_conventions() sweeps every form's survey list for that
    marker once, right after each convention's own iteration fully
    completes (see sweep_pending_removals, called from __init__.py).
    This keeps the directive correct regardless of how many fields a
    single move_to_form convention's applies_when happens to match
    within one form, not just for today's single-named-field case.

    Idempotent: uses ctx.spec (already on every EntityContext) to find
    the target form, so it needs no extra threading the way
    canonical_lists does for use_canonical_list. If the field is
    already tombstoned (this convention already ran in this build
    pass) or is already in the target form, this is a no-op.
    """
    if ctx.kind != "field":
        raise DSLEvaluationError(
            f"move_to_form requires field-scoped context, got {ctx.kind!r}"
        )
    if not isinstance(payload, str) or not payload:
        raise DSLEvaluationError(
            "move_to_form payload must be a non-empty target form_id"
        )

    source_form = ctx.parent
    if not isinstance(source_form, dict):
        raise DSLEvaluationError("move_to_form: field has no parent form")

    field_row = ctx.entity
    if field_row.get(_PENDING_REMOVAL_KEY):
        return  # already tombstoned this build pass

    source_form_id = source_form.get("form_id")
    target_form_id = payload

    if source_form_id == target_form_id:
        return  # already home

    target_form = None
    for f in ctx.spec.get("forms", []):
        if f.get("form_id") == target_form_id:
            target_form = f
            break
    if target_form is None:
        raise DSLEvaluationError(
            f"move_to_form: no form with form_id {target_form_id!r} in this study"
        )

    moved_row = dict(field_row)
    moved_row.pop(_PENDING_REMOVAL_KEY, None)
    old_itemgroup = moved_row.get("bind__oc_itemgroup")
    moved_row["bind__oc_itemgroup"] = target_form_id

    target_survey = target_form.setdefault("survey", [])
    target_survey.append(moved_row)

    field_type = moved_row.get("type", "")
    copied_choices = 0
    if field_type.startswith("select_one ") or field_type.startswith("select_multiple "):
        list_name = field_type.split(" ", 1)[1]
        target_choices = target_form.setdefault("choices", [])
        existing = {(c.get("list_name"), c.get("name")) for c in target_choices}
        for c in source_form.get("choices", []):
            if c.get("list_name") == list_name and (c.get("list_name"), c.get("name")) not in existing:
                target_choices.append(dict(c))
                copied_choices += 1

    field_row[_PENDING_REMOVAL_KEY] = source_form_id

    result.mutations_made.append(Mutation(
        directive="move_to_form", path="field.parent_form",
        old_value=source_form_id, new_value=target_form_id,
    ))
    if old_itemgroup != target_form_id:
        result.mutations_made.append(Mutation(
            directive="move_to_form", path="field.bind__oc_itemgroup",
            old_value=old_itemgroup, new_value=target_form_id,
        ))
    if copied_choices:
        result.mutations_made.append(Mutation(
            directive="move_to_form", path="form.choices",
            old_value=None, new_value=f"+{copied_choices} choice(s) copied to {target_form_id!r}",
        ))


def sweep_pending_removals(spec: Dict[str, Any]) -> int:
    """
    Strip every survey row tombstoned by move_to_form from its OLD
    form. Called by apply_conventions() right after each convention's
    own iteration completes -- see __init__.py. Returns the number of
    rows removed (0 is the normal case; most conventions never touch
    move_to_form at all).
    """
    removed = 0
    for form in spec.get("forms") or []:
        survey = form.get("survey")
        if not survey:
            continue
        kept = [r for r in survey if not r.get(_PENDING_REMOVAL_KEY)]
        if len(kept) != len(survey):
            removed += len(survey) - len(kept)
            form["survey"] = kept
    return removed


def _form_by_id(spec: Dict[str, Any], form_id: Any):
    for f in spec.get("forms") or []:
        if f.get("form_id") == form_id:
            return f
    return None


def _do_assemble_form(payload: Any, ctx: EntityContext, result: ApplyResult) -> None:
    """
    Create a new form by pulling questions out of existing forms.

    Shape (study-scoped):
      "assemble_form": {
        "form_id": "SPECCOL", "form_title": "Specimen Collection",
        "clone_from": "SHORTFORM",            # template form: supplies the form-level settings and group layout
        "cdash_domain": null,
        "visits": {"like": "SHORTFORM"} | {"events": ["SE_COMMON"]},
        "item_group": "SPECCOL", "group_name": "SPECCOL_GRP", "required": true,
        "helpers": [ {calculate row}, ... ],  # optional hidden helper rows placed before the group
        "fields": [
          {"from": "SHORTFORM.KIT_NO", "relevant": "..." | null},
          {"from": "SHORTFORM.DOD", "as": "DTHDAT", "label": "...", "also_from": ["FOLLOWUP.DTDTH"], "relevant": null}
        ]
      }

    Nothing is deleted without a home: every source row moves into the new form (the first source is the
    row that is copied; any "also_from" rows must have the same answer type and are merged into it), the
    old row is removed from its form, and an old -> new entry is written to study_meta.field_lineage.
    A "relevant" key replaces that row's show/hide rule (null/"" clears it); no key keeps the copied rule.

    Applicability: if the template form (clone_from) is not in this study, the convention does not apply and
    nothing happens, with no flag (e.g. a BioIVT study that has no Short Form).
    All-or-nothing: if the template exists but a source question, an event, or a merge type is missing, the
    form is NOT created, nothing is moved, and a review flag review_flags.assemble_form_skipped explains why.
    Idempotent: if the form already exists the directive does nothing, because conventions run more than
    once per build.
    """
    if ctx.kind != "study":
        raise DSLEvaluationError(f"assemble_form requires study-scoped context, got {ctx.kind!r}")
    if not isinstance(payload, dict) or not payload.get("form_id") or not payload.get("clone_from"):
        raise DSLEvaluationError("assemble_form payload needs form_id and clone_from")
    spec = ctx.spec
    form_id = payload["form_id"]
    if _form_by_id(spec, form_id) is not None:
        return

    template = _form_by_id(spec, payload["clone_from"])
    if template is None:
        return  # the study does not have this structure at all, so the convention simply does not apply (no flag)
    problems: List[str] = []

    visits = payload.get("visits") or {}
    if "like" in visits:
        like = _form_by_id(spec, visits["like"])
        if like is None:
            problems.append(f"form {visits['like']!r} (used for its events) is not in this study")
        events = list(dict.fromkeys((like or {}).get("visits_assigned") or []))
    else:
        events = list(visits.get("events") or [])
    known = {r.get("event") for r in ((spec.get("timepoint_csv") or {}).get("rows") or [])}
    if not events:
        problems.append("there are no events to place the form on")
    elif [e for e in events if e not in known]:
        problems.append(f"event(s) {[e for e in events if e not in known]} are not defined in this study")

    plan = []
    for spec_f in payload.get("fields") or []:
        found = []
        for ref in [spec_f.get("from")] + list(spec_f.get("also_from") or []):
            src_id, _, src_name = str(ref).partition(".")
            src_form = _form_by_id(spec, src_id)
            row = next((r for r in (src_form or {}).get("survey") or [] if r.get("name") == src_name), None)
            if row is None:
                problems.append(f"{ref} is not in this study")
            else:
                found.append((src_form, row))
        if len({str(r.get("type")) for _, r in found}) > 1:
            problems.append(f"{spec_f.get('from')} and the questions merged into it have different answer types")
        if found:
            plan.append((spec_f, found))

    if problems:
        result.flags_raised.append(Flag(
            category="review_flags.assemble_form_skipped",
            message=f"Form {form_id} was not created and nothing was moved: " + "; ".join(problems)))
        return

    new = copy.deepcopy({k: v for k, v in template.items() if k not in ("survey", "choices")})
    new.update({
        "form_id": form_id, "form_title": payload.get("form_title", form_id),
        "cdash_domain": payload.get("cdash_domain"), "visits_assigned": events, "reuse_count": len(events),
        "cross_form_dependencies": copy.deepcopy(payload.get("cross_form_dependencies") or []),
    })
    settings = copy.deepcopy(template.get("settings") or {})
    settings.update({"form_title": new["form_title"], "form_id": form_id})
    settings["crossform_references"] = ",".join(events) if payload.get("helpers") else ""
    new["settings"] = settings

    begin = next((copy.deepcopy(r) for r in template["survey"] if r.get("type") == "begin group"),
                 {"type": "begin group", "appearance": "field-list", "label": ""})
    begin["name"] = payload.get("group_name") or f"{form_id}_GRP"
    end = next((copy.deepcopy(r) for r in reversed(template["survey"]) if r.get("type") == "end group"),
               {"type": "end group", "name": ""})
    item_group = payload.get("item_group") or form_id
    survey = [copy.deepcopy(h) for h in payload.get("helpers") or []] + [begin]
    choices, seen, lineage = [], set(), []
    for spec_f, found in plan:
        src_form, src_row = found[0]
        row = copy.deepcopy(src_row)
        row["name"] = spec_f.get("as") or src_row["name"]
        if spec_f.get("label"):
            row["label"] = spec_f["label"]
        if "relevant" in spec_f:
            if spec_f["relevant"]:
                row["relevant"] = spec_f["relevant"]
            else:
                row.pop("relevant", None)
        row["bind__oc_itemgroup"] = item_group
        survey.append(row)
        t = str(row.get("type", ""))
        if t.startswith("select") and " " in t:
            ln = t.split(" ", 1)[1]
            for c in src_form.get("choices") or []:
                if c.get("list_name") == ln and (ln, c.get("name")) not in seen:
                    seen.add((ln, c.get("name")))
                    choices.append(copy.deepcopy(c))
        for sf, sr in found:
            lineage.append({"old": f"{sf['form_id']}.{sr['name']}", "new": f"{form_id}.{row['name']}",
                            "how": "merged" if len(found) > 1 else "moved"})
    survey.append(end)
    new["survey"], new["choices"] = survey, choices

    for _, found in plan:
        for sf, sr in found:
            sf["survey"] = [r for r in sf["survey"] if r is not sr]

    anchor = _form_by_id(spec, payload.get("insert_after") or payload["clone_from"])
    forms = spec["forms"]
    idx = next((i for i, f in enumerate(forms) if f is anchor), len(forms) - 1)
    forms.insert(idx + 1, new)

    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict) and isinstance(soe.get("form_placements"), list):
        for e in events:
            soe["form_placements"].append({"target_visit_oid": e, "form_id": form_id,
                                           "required": bool(payload.get("required", False)),
                                           "repeating": False, "notes": ""})
    spec.setdefault("study_meta", {}).setdefault("field_lineage", []).extend(lineage)
    result.mutations_made.append(Mutation(
        directive="assemble_form", path="forms", old_value=None,
        new_value=f"created {form_id}: {len(plan)} question(s) from "
                  f"{sorted({l['old'].split('.')[0] for l in lineage})}, events {events}"))


def _do_insert_after(payload: Any, ctx: EntityContext, result: ApplyResult) -> None:
    """
    Insert a new question directly after the current field.

    Shape (field-scoped): "insert_after": {"name": "{self}_OTH", "type": "text", "label": "...",
                                           "relevant": "${{self}}='other'"}
    "{self}" in any string value is replaced by the current field's name. The new row inherits the current
    field's item group and width unless it sets its own. Idempotent: if a question with that name already
    exists on the form, nothing happens. Safe during the engine's field walk: the new row is inserted after
    the current position and has a different name, so it is visited once and does not match again.
    """
    if ctx.kind != "field":
        raise DSLEvaluationError(f"insert_after requires field-scoped context, got {ctx.kind!r}")
    if not isinstance(payload, dict) or not payload.get("name") or not payload.get("type"):
        raise DSLEvaluationError("insert_after payload needs at least name and type")
    form, field = ctx.parent, ctx.entity
    me = field.get("name", "")
    row = {k: (v.replace("{self}", me) if isinstance(v, str) else copy.deepcopy(v)) for k, v in payload.items()}
    if any(r.get("name") == row["name"] for r in form.get("survey") or []):
        return
    row.setdefault("bind__oc_itemgroup", field.get("bind__oc_itemgroup"))
    if "appearance" not in row and field.get("appearance"):
        row["appearance"] = field["appearance"]
    row.setdefault("completion_status", "COMPLETE")
    row.setdefault("library_source", "PROTOCOL_SPECIFIC")
    survey = form["survey"]
    idx = next(i for i, r in enumerate(survey) if r is field)
    survey.insert(idx + 1, row)
    result.mutations_made.append(Mutation(
        directive="insert_after", path="form.survey", old_value=None,
        new_value=f"+{row['name']} after {me}"))


DIRECTIVES = {
    "set":         _do_set,
    "ensure":      _do_ensure,
    "require":     _do_require,
    "flag":        _do_flag,
    "append_to":   _do_append_to,
    "remove_from": _do_remove_from,
    "match":       _do_match,
    "default_value": _do_default_value,
    "move_to_form": _do_move_to_form,
    "assemble_form": _do_assemble_form,
    "insert_after": _do_insert_after,
}

# use_canonical_list is intentionally NOT in DIRECTIVES: every other
# directive has signature (payload, ctx, result) and _do_match dispatches
# into DIRECTIVES for its sub-effect-blocks, so adding a 4th required
# parameter (canonical_lists) here would force it onto directives that
# don't need it. Handled as an explicit branch in apply_effect instead —
# see there. It is also deliberately excluded from what _do_match may
# dispatch to (checked there via DIRECTIVES membership), since a
# canonical-list swap nested inside a conditional is not a supported
# pattern yet.


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def apply_effect(effect: Dict[str, Any], ctx: EntityContext,
                 spec: Dict[str, Any], convention_id: str,
                 canonical_lists: Dict[str, Any] | None = None) -> ApplyResult:
    """
    Apply an effect block. Mutates spec / ctx.entity in place. Soft
    directives are accumulated, not applied.

    Effects within one block execute in source order.

    `canonical_lists` is optional and defaults to None — every
    existing caller (including all of tests/conventions/) is
    unaffected. It's only consulted for the `use_canonical_list`
    directive; a convention that uses it while canonical_lists is
    None (or lacks the referenced name) raises DSLEvaluationError,
    same failure mode as any other malformed convention.
    """
    result = ApplyResult()
    if not effect:
        return result

    for key, payload in effect.items():
        if key == "soft":
            if isinstance(payload, str):
                result.soft_directives.append(payload)
            continue
        if key == "use_canonical_list":
            _do_use_canonical_list(payload, ctx, result, canonical_lists or {})
            continue
        if key in DIRECTIVES:
            DIRECTIVES[key](payload, ctx, result)
            continue
        raise DSLEvaluationError(f"Unknown effect directive {key!r} in {convention_id!r}")

    # Flags raised need to land in spec.review_flags.<category>
    for flag in result.flags_raised:
        review_flags = spec.setdefault("review_flags", {})
        # category is dotted like "review_flags.constraint_review" — we strip prefix
        cat = flag.category
        if cat.startswith("review_flags."):
            cat = cat[len("review_flags."):]
        bucket = review_flags.setdefault(cat, [])
        if flag.message not in bucket:
            bucket.append(flag.message)

    return result
