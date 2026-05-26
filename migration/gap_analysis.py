"""
gap_analysis.py — ODM source ↔ OC4 target gap analysis engine.

Step 2 of the migration capability. Consumes the outputs of:
    odm_reader.parse_odm_metadata(xml)  → odm_metadata
    odm_to_spec.transform(odm_metadata) → spec_json
and emits a structured GapAnalysisReport that downstream surfaces (the
Syndeo UI, the pipeline's monday upload) render as per-field confidence
and risk signals.

Public entry point
──────────────────
    run_gap_analysis(odm_metadata, spec_json, source_system) -> dict

Linkage to source ODM items is via the `_source_oid` field stamped on
each survey row by odm_to_spec.transform(). Rows without a source
(group wrappers, auto-injected SUBJID) are skipped — gap analysis only
reports on fields that originated in the source export.

Schema (top-level)
──────────────────
    {
      "report_id":              str (uuid),
      "generated_at":           str (ISO 8601 UTC),
      "source_system":          str,
      "source_study_oid":       str,
      "target_study_oid":       str,
      "arm_analysis_available": bool,
      "warnings":               [str],
      "summary": {
          "total":          int,
          "clean":          int,
          "warning":        int,
          "data_loss_risk": int,
          "blocking":       int,
          "unmapped":       int,
      },
      "mappings": [Mapping…],
    }

Per-field Mapping
─────────────────
    {
      "sources": [FieldDescriptor],   # always ≥1 except "new" (not emitted today)
      "targets": [FieldDescriptor],   # empty list when mapping_type=="unmapped"
      "mapping_type":      "1:1" | "1:many" | "many:1" | "unmapped",
      "confidence":        "High" | "Medium" | "Low" | "Unmappable",
      "risk":              "Clean" | "Warning" | "Data Loss Risk" | "Blocking",
      "reason":            str,
      "reviewer_decision": None,      # populated by UI reviewer
      "reviewer_note":     None,      # populated by UI reviewer
      "override_mapping":  None,      # populated by UI reviewer
    }

FieldDescriptor
───────────────
    {
      "study":      str,              # ODM study OID (source) / derived (target)
      "site":       "*",              # not knowable from metadata
      "subject":    str | None,       # arm OID, "*", or None (Rave-style)
      "event":      "*",              # forms span multiple events; "*" is "all"
      "form":       str,              # form OID (source) / form_id (target)
      "oid":        str,
      "label":      str,
      "type":       str,              # ODM DataType / XLSForm type, verbatim
      "length":     int | None,
      "coded_list": [str] | None,     # CodeList coded_values
      "required":   bool,
    }
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any


# ── Type normalization ───────────────────────────────────────────────────────
#
# ODM DataType vocabulary and XLSForm type vocabulary differ. Normalize both
# onto a small canonical set so the classification rules don't have to repeat
# every synonym.

_NUMERIC_NORMAL  = {"integer", "decimal"}
_TEMPORAL_NORMAL = {"date", "time", "datetime"}
_SELECT_NORMAL   = {"select_one", "select_multiple"}


def _normalize_source_type(t: str) -> str:
    """ODM DataType → canonical type. Defaults to 'text' on unknowns."""
    t = (t or "").lower().strip()
    return {
        "text": "text", "string": "text",
        "integer": "integer", "int": "integer",
        "float": "decimal", "double": "decimal",
        "decimal": "decimal", "number": "decimal",
        "date": "date", "partialdate": "date",
        "time": "time", "partialtime": "time",
        "datetime": "datetime", "partialdatetime": "datetime",
        "boolean": "boolean",
        "uri": "text", "base64binary": "text", "hexbinary": "text",
    }.get(t, "text")


def _normalize_target_type(t: str) -> str:
    """XLSForm type → canonical type. select_* are bucketed by family."""
    t = (t or "").lower().strip()
    if t.startswith("select_one"):
        return "select_one"
    if t.startswith("select_multiple"):
        return "select_multiple"
    return {
        "text": "text",
        "integer": "integer",
        "decimal": "decimal",
        "date": "date",
        "time": "time",
        "datetime": "datetime",
        "boolean": "boolean",
        "calculate": "calculate",
    }.get(t, "text")


def _check_type(src: str, tgt: str) -> str:
    """Return 'same' | 'widening' | 'narrowing' | 'incompatible' for the
    canonical type pair. Widening = lossless; narrowing = lossy; same = exact
    match; incompatible = no defensible coercion."""
    if src == tgt:
        return "same"
    # boolean reasonably maps to select_one yn (and vice versa).
    if {src, tgt} == {"boolean", "select_one"}:
        return "same"
    # Numeric ladder.
    if src == "integer" and tgt == "decimal":
        return "widening"
    if src == "decimal" and tgt == "integer":
        return "narrowing"
    # Temporal ladder.
    if src == "date" and tgt == "datetime":
        return "widening"
    if src == "datetime" and tgt == "date":
        return "narrowing"
    if src == "time" and tgt == "datetime":
        return "widening"
    if src == "datetime" and tgt == "time":
        return "narrowing"
    # text absorbs anything; the inverse (text → non-text) is incompatible
    # because we'd need to parse arbitrary user-entered strings.
    if tgt == "text":
        return "widening"
    if src == "text":
        return "incompatible"
    # Within the select family: single → multi accommodates the source value
    # as a single-element selection (widening); multi → single loses any
    # multi-value selections (narrowing).
    if src == "select_one" and tgt == "select_multiple":
        return "widening"
    if src == "select_multiple" and tgt == "select_one":
        return "narrowing"
    # Selects: gaining an enumeration constraint is widening (lossless if
    # all source values fit). Losing it is narrowing (constraint dropped).
    if src not in _SELECT_NORMAL and tgt in _SELECT_NORMAL:
        return "widening"
    if src in _SELECT_NORMAL and tgt not in _SELECT_NORMAL:
        return "narrowing"
    # Anything reaching here (e.g. integer → date) is genuinely unmappable.
    return "incompatible"


# ── Arm / subject resolution ─────────────────────────────────────────────────

def _resolve_subject(odm_metadata: dict) -> tuple[Any, bool, str | None]:
    """Apply the arm rule.

    Returns:
        (subject_value, arm_analysis_available, warning_or_None)

      * arms in metadata     → (first arm OID, True, None)
      * arms not in metadata → (None,           False, warning)

    The "no arms at all = single-arm" case can't be distinguished from
    the "arms in ClinicalData only" case at metadata-parse time, so we
    emit the warning either way and let the reviewer override.
    """
    arms = (odm_metadata.get("protocol") or {}).get("arms") or []
    if arms:
        first = arms[0]
        return (first.get("oid") or first.get("name") or "ARM_1", True, None)
    return (
        None,
        False,
        "ODM metadata does not declare arms in <Protocol><Arm>. Arm "
        "assignment likely lives in ClinicalData (Rave-style) or the "
        "study is single-arm — reviewer must verify before migration.",
    )


# ── Study OID derivation (target side) ────────────────────────────────────────

def _derive_target_study_oid(spec_json: dict) -> str:
    """Best-effort OC4 study OID from study_meta. Falls back to a stub."""
    sm = spec_json.get("study_meta") or {}
    if sm.get("study_id"):
        return sm["study_id"]
    proto = (sm.get("protocol_number") or "").strip()
    if proto:
        clean = re.sub(r"[^A-Za-z0-9]", "_", proto).upper().strip("_")
        return clean if clean.startswith("S_") else f"S_{clean}"
    return "S_MIGRATED"


# ── Field descriptor builders ─────────────────────────────────────────────────

def _extract_target_coded_list(row: dict, form: dict) -> list[str] | None:
    """If row is a select_*, look up the form.choices entries whose
    list_name matches the type's second token. Returns None for non-selects
    or selects whose list isn't present in form.choices."""
    parts = (row.get("type") or "").split(None, 1)
    if len(parts) != 2 or not parts[0].startswith("select_"):
        return None
    list_name = parts[1]
    values = [c.get("name") for c in (form.get("choices") or [])
              if c.get("list_name") == list_name and c.get("name") is not None]
    return values if values else None


def _build_source_field(
    item: dict,
    form_oid: str,
    mandatory: bool,
    study_oid: str,
    subject: Any,
    codelists_by_oid: dict,
) -> dict:
    coded_list = None
    if item.get("codelist_ref"):
        cl = codelists_by_oid.get(item["codelist_ref"])
        if cl:
            coded_list = [c["coded_value"] for c in (cl.get("items") or [])
                          if c.get("coded_value")]
    return {
        "study":      study_oid or "",
        "site":       "*",
        "subject":    subject,
        "event":      "*",
        "form":       form_oid or "",
        "oid":        item.get("oid", "") or "",
        "label":      item.get("label") or item.get("name") or "",
        "type":       item.get("data_type") or "text",
        "length":     item.get("length"),
        "coded_list": coded_list,
        "required":   bool(mandatory),
    }


def _build_target_field(
    row: dict,
    form: dict,
    study_id: str,
    subject: Any,
) -> dict:
    row_type = row.get("type") or ""
    coded_list = _extract_target_coded_list(row, form)
    # OC4 doesn't carry an explicit Length on text fields; the platform
    # default cap is 255 characters. Surface that so length comparisons
    # below have something to chew on; other types report None (length
    # isn't a meaningful concept for date/integer/select on the target).
    length: int | None = None
    if _normalize_target_type(row_type) == "text":
        length = 255
    return {
        "study":      study_id or "",
        "site":       "*",
        "subject":    subject,
        "event":      "*",
        "form":       form.get("form_id", "") or "",
        "oid":        row.get("name", "") or "",
        "label":      row.get("label") or row.get("name") or "",
        "type":       row_type,
        "length":     length,
        "coded_list": coded_list,
        "required":   bool(row.get("required")),
    }


# ── Classification rules ──────────────────────────────────────────────────────
#
# Returns (confidence, risk, reason) where the (confidence, risk) pair is
# one of the four ladder rungs:
#
#   High        / Clean           — same type, capacity ≥, all codes covered
#   Medium      / Warning         — lossless widening; required→optional
#   Low         / Data Loss Risk  — length shrink, lossy narrowing, partial
#                                   codelist coverage (≤50% missing)
#   Unmappable  / Blocking        — incompatible types, no target on a
#                                   required source, >50% missing codes
#
# Order matters: blocking conditions short-circuit before warning ones.

def _classify(src: dict, tgt: dict | None) -> tuple[str, str, str]:
    if tgt is None:
        if src.get("required"):
            return (
                "Unmappable", "Blocking",
                "Required source field has no target in the OC4 spec — "
                "migration cannot proceed without a manual mapping.",
            )
        return (
            "Unmappable", "Blocking",
            "Source field has no target in the OC4 spec — values will be "
            "dropped unless a reviewer adds a mapping.",
        )

    src_canon = _normalize_source_type(src.get("type", ""))
    tgt_canon = _normalize_target_type(tgt.get("type", ""))
    # ODM convention: codelist-constrained items declare DataType="text"
    # (or "integer") for their storage type while the <CodeListRef> carries
    # the enumeration. odm_to_spec correctly emits these as `select_one
    # CL_X`. Without this promotion we'd mis-classify every coded field as
    # `text → select_one = Blocking`. The coded-value coverage check below
    # is what actually verifies semantic compatibility for these cases.
    if src.get("coded_list"):
        src_canon = "select_one"
    type_relation = _check_type(src_canon, tgt_canon)

    # 1. Blocking: incompatible type.
    if type_relation == "incompatible":
        return (
            "Unmappable", "Blocking",
            f"Source type ({src_canon}) is fundamentally incompatible with "
            f"target type ({tgt_canon}). Manual transformation required.",
        )

    # 2. Codelist coverage — applies whenever the source declares one.
    src_codes = set(src.get("coded_list") or [])
    if src_codes:
        tgt_codes = set(tgt.get("coded_list") or [])
        missing = src_codes - tgt_codes
        if missing:
            ratio = len(missing) / max(len(src_codes), 1)
            preview = sorted(missing)[:5]
            preview_str = (
                ", ".join(preview)
                + (f", … (+{len(missing) - 5} more)" if len(missing) > 5 else "")
            )
            if ratio > 0.5:
                return (
                    "Unmappable", "Blocking",
                    f"Target codelist is missing {len(missing)} of "
                    f"{len(src_codes)} source values "
                    f"({int(ratio * 100)}%): {preview_str}.",
                )
            return (
                "Low", "Data Loss Risk",
                f"Target codelist is missing {len(missing)} of "
                f"{len(src_codes)} source values: {preview_str}.",
            )

    # 3. Length shrink (text only).
    if src_canon == "text" and tgt_canon == "text":
        src_len = src.get("length")
        tgt_len = tgt.get("length")
        if (isinstance(src_len, int) and isinstance(tgt_len, int)
                and tgt_len < src_len):
            return (
                "Low", "Data Loss Risk",
                f"Text field length shrinks from {src_len} → {tgt_len} — "
                f"source values exceeding {tgt_len} characters will be "
                f"truncated during migration.",
            )

    # 4. Lossy type narrowing.
    if type_relation == "narrowing":
        return (
            "Low", "Data Loss Risk",
            f"Type narrows from {src_canon} → {tgt_canon}. Values may "
            f"lose precision (e.g. decimals truncated, time component "
            f"dropped, free text constrained to enum).",
        )

    # 5. Lossless widening notes.
    if type_relation == "widening":
        return (
            "Medium", "Warning",
            f"Type widened from {src_canon} → {tgt_canon}. Lossless but "
            f"downstream consumers may see a different type signature.",
        )

    # 6. Required → optional regression.
    if src.get("required") and not tgt.get("required"):
        return (
            "Medium", "Warning",
            "Source field is required but target is optional — data "
            "quality may degrade if reviewers skip the field.",
        )

    # 7. Length expansion — Clean, with note.
    if src_canon == "text" and tgt_canon == "text":
        src_len = src.get("length")
        tgt_len = tgt.get("length")
        if (isinstance(src_len, int) and isinstance(tgt_len, int)
                and tgt_len > src_len):
            return (
                "High", "Clean",
                f"Text field, capacity expanded ({src_len}→{tgt_len}), "
                f"no data loss risk.",
            )

    return (
        "High", "Clean",
        "Same type, capacity matches or expands, no data loss risk.",
    )


# ── Summary aggregation ───────────────────────────────────────────────────────

def _aggregate(mappings: list[dict]) -> dict:
    s = {
        "total": 0, "clean": 0, "warning": 0,
        "data_loss_risk": 0, "blocking": 0, "unmapped": 0,
    }
    for m in mappings:
        s["total"] += 1
        if m["mapping_type"] == "unmapped":
            s["unmapped"] += 1
            continue
        c = m["confidence"]
        if c == "High":
            s["clean"] += 1
        elif c == "Medium":
            s["warning"] += 1
        elif c == "Low":
            s["data_loss_risk"] += 1
        elif c == "Unmappable":
            s["blocking"] += 1
    return s


# ── Public entry point ────────────────────────────────────────────────────────

def run_gap_analysis(
    odm_metadata: dict,
    spec_json: dict,
    source_system: str,
) -> dict:
    """Compare ODM source metadata against the generated OC4 spec.

    Args:
        odm_metadata: Output of odm_reader.parse_odm_metadata().
        spec_json:    Output of odm_to_spec.transform() (or the AI-assist
                      variant). Each survey row must carry _source_oid
                      when it originated from an ODM ItemDef.
        source_system: Vendor name (e.g. "Medidata Rave"). Used only for
                       the report header — classification logic is
                       vendor-neutral.

    Returns:
        A GapAnalysisReport dict (see module docstring for shape).
    """
    warnings: list[str] = []

    # 1. Arm / subject resolution.
    subject, arm_available, arm_warning = _resolve_subject(odm_metadata)
    if arm_warning:
        warnings.append(arm_warning)

    # 2. Lookups.
    codelists_by_oid   = {cl["oid"]: cl for cl in odm_metadata.get("codelists", [])}
    item_groups_by_oid = {ig["oid"]: ig for ig in odm_metadata.get("item_groups", [])}

    # parent_form_by_item[item_oid] = (form_oid, group_oid, mandatory).
    # An item can technically appear in multiple groups across multiple forms
    # — last-wins here, which mirrors odm_to_spec's behavior (each item gets
    # emitted once per group reference). Good-enough for the first pass.
    parent_form_by_item: dict[str, tuple[str, str, bool]] = {}
    for form in odm_metadata.get("forms", []):
        for ig_oid in form.get("item_group_refs", []):
            ig = item_groups_by_oid.get(ig_oid)
            if not ig:
                continue
            for ir in ig.get("item_refs", []):
                parent_form_by_item[ir["oid"]] = (
                    form["oid"], ig_oid, bool(ir.get("mandatory")),
                )

    # target_index[source_oid] = (form_dict, row_dict).
    target_index: dict[str, tuple[dict, dict]] = {}
    for form in spec_json.get("forms", []):
        for row in form.get("survey", []):
            soid = (row.get("_source_oid") or "").strip()
            if soid:
                target_index[soid] = (form, row)

    # 3. Iterate ODM items and emit mappings.
    source_study_oid = (odm_metadata.get("study") or {}).get("oid", "") or ""
    target_study_oid = _derive_target_study_oid(spec_json)

    mappings: list[dict] = []
    for item in odm_metadata.get("items", []):
        parent = parent_form_by_item.get(item.get("oid", ""))
        if not parent:
            # Orphan item — defined but never referenced by any item group.
            # Skip silently; odm_to_spec doesn't emit a row for it either.
            continue
        src_form_oid, _src_ig_oid, mandatory = parent

        src_field = _build_source_field(
            item, src_form_oid, mandatory,
            source_study_oid, subject,
            codelists_by_oid,
        )

        target_pair = target_index.get(item["oid"])
        if target_pair is not None:
            tgt_form, tgt_row = target_pair
            tgt_field = _build_target_field(
                tgt_row, tgt_form, target_study_oid, subject,
            )
            confidence, risk, reason = _classify(src_field, tgt_field)
            mappings.append({
                "sources":           [src_field],
                "targets":           [tgt_field],
                "mapping_type":      "1:1",
                "confidence":        confidence,
                "risk":              risk,
                "reason":            reason,
                "reviewer_decision": None,
                "reviewer_note":     None,
                "override_mapping":  None,
            })
        else:
            confidence, risk, reason = _classify(src_field, None)
            mappings.append({
                "sources":           [src_field],
                "targets":           [],
                "mapping_type":      "unmapped",
                "confidence":        confidence,
                "risk":              risk,
                "reason":            reason,
                "reviewer_decision": None,
                "reviewer_note":     None,
                "override_mapping":  None,
            })

    summary = _aggregate(mappings)

    return {
        "report_id":              str(uuid.uuid4()),
        "generated_at":           datetime.now(timezone.utc)
                                     .isoformat(timespec="seconds")
                                     .replace("+00:00", "Z"),
        "source_system":          source_system or "UNKNOWN",
        "source_study_oid":       source_study_oid,
        "target_study_oid":       target_study_oid,
        "arm_analysis_available": arm_available,
        "warnings":               warnings,
        "summary":                summary,
        "mappings":               mappings,
    }


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys
    from odm_reader import parse_odm_metadata
    from odm_to_spec import transform

    if len(sys.argv) < 2:
        print("Usage: python gap_analysis.py <odm_file.xml> [--json] [--out report.json]")
        sys.exit(1)

    path = sys.argv[1]
    as_json = "--json" in sys.argv
    out_path = None
    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        out_path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

    with open(path, "rb") as f:
        xml_bytes = f.read()

    odm = parse_odm_metadata(xml_bytes)
    spec = transform(odm)
    report = run_gap_analysis(odm, spec, odm.get("source_system", "UNKNOWN"))

    if out_path:
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Gap analysis report written to {out_path}", file=sys.stderr)
    elif as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        s = report["summary"]
        print(f"Source system:          {report['source_system']}")
        print(f"Source study OID:       {report['source_study_oid']}")
        print(f"Target study OID:       {report['target_study_oid']}")
        print(f"Arm analysis available: {report['arm_analysis_available']}")
        for w in report["warnings"]:
            print(f"  ⚠  {w}")
        print()
        print(f"  Total mappings:   {s['total']}")
        print(f"  Clean:            {s['clean']}")
        print(f"  Warning:          {s['warning']}")
        print(f"  Data Loss Risk:   {s['data_loss_risk']}")
        print(f"  Blocking:         {s['blocking']}")
        print(f"  Unmapped:         {s['unmapped']}")
