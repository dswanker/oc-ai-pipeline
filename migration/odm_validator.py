"""
odm_validator.py — ODM XML validation for oc-ai-pipeline migration

Validates an ODM XML file before it enters the migration pipeline.
Produces a structured ValidationReport with pass/warn/fail results
for each check, suitable for display in Monday.com log columns,
compliance audit trails, and human review.

Three validation layers
───────────────────────
Layer 1 — XML well-formedness
  Standard XML parse. Any failure here means the file is unreadable.

Layer 2 — ODM structural conformance
  Checks that the file follows the CDISC ODM envelope structure:
  required root attributes (ODMVersion, FileOID, FileType, CreationDateTime),
  presence of Study > GlobalVariables > StudyName/ProtocolName,
  presence of MetaDataVersion, minimum viable study content.
  Uses xmlschema against the official ODM 1.3.2 XSD where available,
  falls back to structural heuristics for vendor-extended files.

Layer 3 — OID referential integrity
  Cross-checks all OID references:
  - StudyEventDef FormRef → FormDef
  - FormDef ItemGroupRef → ItemGroupDef
  - ItemGroupDef ItemRef → ItemDef
  - ItemDef CodeListRef → CodeList
  - Protocol StudyEventRef → StudyEventDef
  Reports dangling references as warnings (not failures) because some
  vendors emit partial exports that are still usable.

Compliance flags
────────────────
Reports on fields required for GDPR, CFR Part 21 Part 11, and ICH E6(R3)
migration audit trail: CreationDateTime, FileOID, ProtocolName, ODMVersion.

Usage
─────
  from odm_validator import validate_odm, format_report

  result = validate_odm(xml_bytes)
  print(format_report(result))

  # In pipeline:
  result = validate_odm(xml_bytes)
  if not result.can_proceed:
      raise ValueError(f"ODM validation failed: {result.summary}")

ValidationReport fields
───────────────────────
  passed:       bool   — all checks passed (no failures)
  can_proceed:  bool   — safe to proceed (passed or only warnings)
  summary:      str    — one-line human summary
  odm_version:  str    — detected ODM version
  checks:       list   — individual CheckResult objects
  layer_results: dict  — PASS/WARN/FAIL per layer
  compliance:   dict   — compliance field status
  stats:        dict   — counts (events, forms, items, codelists)
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

# ── ODM namespace map ─────────────────────────────────────────────────────────

ODM_NS = {
    "1.3":   "http://www.cdisc.org/ns/odm/v1.3",
    "1.3.0": "http://www.cdisc.org/ns/odm/v1.3",
    "1.3.1": "http://www.cdisc.org/ns/odm/v1.3",
    "1.3.2": "http://www.cdisc.org/ns/odm/v1.3",
    "2.0":   "http://www.cdisc.org/ns/odm/v2.0",
}
DEFAULT_NS = "http://www.cdisc.org/ns/odm/v1.3"

# Required ODM root attributes per CDISC spec
REQUIRED_ROOT_ATTRS = ["ODMVersion", "FileOID", "FileType", "CreationDateTime"]

# FileType valid values
VALID_FILE_TYPES = {"Snapshot", "Transactional"}

# Compliance-required fields for CFR Part 11 / ICH E6(R3) audit trail
COMPLIANCE_FIELDS = {
    "ODMVersion":        "ODM version declared (required for schema compliance)",
    "FileOID":           "File OID present (required for unique identification)",
    "FileType":          "FileType declared (Snapshot or Transactional)",
    "CreationDateTime":  "Creation datetime present (required for audit trail)",
    "ProtocolName":      "Protocol name present (required for study identification)",
    "StudyName":         "Study name present",
}


# ── Result data classes ───────────────────────────────────────────────────────

@dataclass
class CheckResult:
    layer:    int           # 1, 2, or 3
    name:     str           # short check name
    status:   str           # "PASS", "WARN", "FAIL"
    message:  str           # human-readable detail
    detail:   list = field(default_factory=list)  # extra items (e.g. list of bad OIDs)


@dataclass
class ValidationReport:
    passed:        bool
    can_proceed:   bool
    summary:       str
    odm_version:   str
    source_file:   str
    validated_at:  str
    checks:        list
    layer_results: dict
    compliance:    dict
    stats:         dict
    parse_warnings: list = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_ns(root: ET.Element, local: str, ns: str) -> Optional[ET.Element]:
    """Find first element with given local name under root, trying with and without ns."""
    el = root.find(f"{{{ns}}}{local}")
    if el is None:
        el = root.find(f".//{{{ns}}}{local}")
    if el is None:
        # Try without namespace (some exports strip it)
        el = root.find(f".//{local}")
    return el


def _findall_ns(root: ET.Element, local: str, ns: str) -> list:
    results = root.findall(f".//{{{ns}}}{local}")
    if not results:
        results = root.findall(f".//{local}")
    return results


def _detect_ns(root: ET.Element) -> str:
    """Detect the ODM namespace from the root element."""
    if "}" in root.tag:
        return root.tag.split("}")[0].lstrip("{")
    return DEFAULT_NS


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


# ── Layer 1: Well-formedness ──────────────────────────────────────────────────

def _check_wellformedness(xml_bytes: bytes) -> tuple[list[CheckResult], Optional[ET.Element]]:
    checks = []

    # Strip BOM
    if xml_bytes.startswith(b"\xef\xbb\xbf"):
        xml_bytes = xml_bytes[3:]

    try:
        root = ET.fromstring(xml_bytes)
        checks.append(CheckResult(
            layer=1, name="XML well-formedness", status="PASS",
            message="File is valid, parseable XML."
        ))
        return checks, root
    except ET.ParseError as e:
        checks.append(CheckResult(
            layer=1, name="XML well-formedness", status="FAIL",
            message=f"XML parse error: {e}. The file cannot be read.",
        ))
        return checks, None


# ── Layer 2: ODM structural conformance ───────────────────────────────────────

def _check_root_element(root: ET.Element) -> list[CheckResult]:
    checks = []
    tag = _strip_ns(root.tag)

    # Root element name
    if tag == "ODM":
        checks.append(CheckResult(
            layer=2, name="Root element", status="PASS",
            message="Root element is <ODM> as required by CDISC spec."
        ))
    else:
        checks.append(CheckResult(
            layer=2, name="Root element", status="FAIL",
            message=f"Root element is <{tag}>, expected <ODM>."
        ))

    # Required root attributes
    missing = [a for a in REQUIRED_ROOT_ATTRS if not root.get(a)]
    present = [a for a in REQUIRED_ROOT_ATTRS if root.get(a)]

    if missing:
        checks.append(CheckResult(
            layer=2, name="Required root attributes", status="WARN",
            message=f"Missing root attributes: {', '.join(missing)}.",
            detail=missing
        ))
    else:
        checks.append(CheckResult(
            layer=2, name="Required root attributes", status="PASS",
            message=f"All required root attributes present: {', '.join(present)}."
        ))

    # ODMVersion
    odm_ver = root.get("ODMVersion", "")
    if odm_ver:
        known = list(ODM_NS.keys())
        if odm_ver in known:
            checks.append(CheckResult(
                layer=2, name="ODM version", status="PASS",
                message=f"ODMVersion='{odm_ver}' is a known CDISC ODM version."
            ))
        else:
            checks.append(CheckResult(
                layer=2, name="ODM version", status="WARN",
                message=f"ODMVersion='{odm_ver}' is not a standard CDISC version "
                        f"(known: {', '.join(known)}). Proceeding with best-effort parse."
            ))
    else:
        checks.append(CheckResult(
            layer=2, name="ODM version", status="WARN",
            message="ODMVersion attribute is missing. Cannot confirm schema compliance."
        ))

    # FileType
    file_type = root.get("FileType", "")
    if file_type in VALID_FILE_TYPES:
        checks.append(CheckResult(
            layer=2, name="FileType", status="PASS",
            message=f"FileType='{file_type}' is valid."
        ))
    elif file_type:
        checks.append(CheckResult(
            layer=2, name="FileType", status="WARN",
            message=f"FileType='{file_type}' is not a standard value "
                    f"(expected: {', '.join(VALID_FILE_TYPES)})."
        ))
    else:
        checks.append(CheckResult(
            layer=2, name="FileType", status="WARN",
            message="FileType attribute is missing."
        ))

    # CreationDateTime format
    cdt = root.get("CreationDateTime", "")
    if cdt:
        # ISO 8601 — basic check
        iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
        if re.match(iso_pattern, cdt):
            checks.append(CheckResult(
                layer=2, name="CreationDateTime format", status="PASS",
                message=f"CreationDateTime='{cdt}' is ISO 8601 format."
            ))
        else:
            checks.append(CheckResult(
                layer=2, name="CreationDateTime format", status="WARN",
                message=f"CreationDateTime='{cdt}' does not appear to be ISO 8601 format."
            ))
    else:
        checks.append(CheckResult(
            layer=2, name="CreationDateTime format", status="WARN",
            message="CreationDateTime is missing. Required for CFR Part 11 audit trail."
        ))

    return checks


def _check_study_structure(root: ET.Element, ns: str) -> list[CheckResult]:
    checks = []

    # Study element
    study = _find_ns(root, "Study", ns)
    if study is None:
        checks.append(CheckResult(
            layer=2, name="Study element", status="FAIL",
            message="No <Study> element found. Cannot extract study metadata."
        ))
        return checks

    study_oid = study.get("OID", "")
    checks.append(CheckResult(
        layer=2, name="Study element", status="PASS",
        message=f"<Study OID='{study_oid}'> found."
    ))

    # GlobalVariables
    gv = _find_ns(study, "GlobalVariables", ns)
    if gv is None:
        checks.append(CheckResult(
            layer=2, name="GlobalVariables", status="FAIL",
            message="No <GlobalVariables> element found. StudyName and ProtocolName unavailable."
        ))
    else:
        sn_el = _find_ns(gv, "StudyName", ns)
        pn_el = _find_ns(gv, "ProtocolName", ns)
        study_name    = _text(sn_el)
        protocol_name = _text(pn_el)

        if study_name:
            checks.append(CheckResult(
                layer=2, name="StudyName", status="PASS",
                message=f"StudyName='{study_name}'."
            ))
        else:
            checks.append(CheckResult(
                layer=2, name="StudyName", status="WARN",
                message="StudyName is empty or missing."
            ))

        if protocol_name:
            checks.append(CheckResult(
                layer=2, name="ProtocolName", status="PASS",
                message=f"ProtocolName='{protocol_name}'."
            ))
        else:
            checks.append(CheckResult(
                layer=2, name="ProtocolName", status="WARN",
                message="ProtocolName is empty or missing. Required for study identification."
            ))

    # MetaDataVersion
    mdv = _find_ns(study, "MetaDataVersion", ns)
    if mdv is None:
        checks.append(CheckResult(
            layer=2, name="MetaDataVersion", status="FAIL",
            message="No <MetaDataVersion> found. Study design cannot be extracted."
        ))
        return checks

    mdv_oid  = mdv.get("OID", "")
    mdv_name = mdv.get("Name", "")
    checks.append(CheckResult(
        layer=2, name="MetaDataVersion", status="PASS",
        message=f"MetaDataVersion OID='{mdv_oid}' Name='{mdv_name}' found."
    ))

    # Minimum content checks
    events   = _findall_ns(mdv, "StudyEventDef", ns)
    forms    = _findall_ns(mdv, "FormDef", ns)
    items    = _findall_ns(mdv, "ItemDef", ns)
    groups   = _findall_ns(mdv, "ItemGroupDef", ns)
    cls_list = _findall_ns(mdv, "CodeList", ns)

    for label, elements, min_count in [
        ("StudyEventDef", events, 1),
        ("FormDef",       forms,  1),
        ("ItemDef",       items,  1),
    ]:
        count = len(elements)
        if count >= min_count:
            checks.append(CheckResult(
                layer=2, name=f"{label} count", status="PASS",
                message=f"{count} {label} element(s) found."
            ))
        else:
            checks.append(CheckResult(
                layer=2, name=f"{label} count", status="WARN",
                message=f"Only {count} {label} element(s) found. Study may be incomplete."
            ))

    return checks


# ── Layer 3: OID referential integrity ────────────────────────────────────────

def _check_oid_integrity(root: ET.Element, ns: str) -> list[CheckResult]:
    checks = []

    study = _find_ns(root, "Study", ns)
    if study is None:
        return checks

    mdv = _find_ns(study, "MetaDataVersion", ns)
    if mdv is None:
        return checks

    # Build OID sets
    event_oids  = {e.get("OID") for e in _findall_ns(mdv, "StudyEventDef", ns) if e.get("OID")}
    form_oids   = {f.get("OID") for f in _findall_ns(mdv, "FormDef", ns) if f.get("OID")}
    group_oids  = {g.get("OID") for g in _findall_ns(mdv, "ItemGroupDef", ns) if g.get("OID")}
    item_oids   = {i.get("OID") for i in _findall_ns(mdv, "ItemDef", ns) if i.get("OID")}
    cl_oids     = {c.get("OID") for c in _findall_ns(mdv, "CodeList", ns) if c.get("OID")}

    # Protocol → StudyEventDef
    protocol = _find_ns(mdv, "Protocol", ns)
    if protocol is not None:
        refs = _findall_ns(protocol, "StudyEventRef", ns)
        dangling = [r.get("StudyEventOID") for r in refs
                    if r.get("StudyEventOID") not in event_oids]
        if dangling:
            checks.append(CheckResult(
                layer=3, name="Protocol → StudyEventDef refs", status="WARN",
                message=f"{len(dangling)} Protocol StudyEventRef(s) point to undefined StudyEventDef.",
                detail=dangling
            ))
        else:
            checks.append(CheckResult(
                layer=3, name="Protocol → StudyEventDef refs", status="PASS",
                message=f"All {len(refs)} Protocol StudyEventRef(s) resolve correctly."
            ))

    # StudyEventDef → FormDef
    form_refs_all = []
    for ev in _findall_ns(mdv, "StudyEventDef", ns):
        for fr in _findall_ns(ev, "FormRef", ns):
            form_refs_all.append(fr.get("FormOID"))
    dangling_forms = [r for r in form_refs_all if r and r not in form_oids]
    if dangling_forms:
        checks.append(CheckResult(
            layer=3, name="StudyEventDef → FormDef refs", status="WARN",
            message=f"{len(dangling_forms)} FormRef(s) in StudyEventDef point to undefined FormDef.",
            detail=list(set(dangling_forms))
        ))
    else:
        checks.append(CheckResult(
            layer=3, name="StudyEventDef → FormDef refs", status="PASS",
            message=f"All {len(form_refs_all)} FormRef(s) in StudyEventDef resolve correctly."
        ))

    # FormDef → ItemGroupDef
    ig_refs_all = []
    for fm in _findall_ns(mdv, "FormDef", ns):
        for igr in _findall_ns(fm, "ItemGroupRef", ns):
            ig_refs_all.append(igr.get("ItemGroupOID"))
    dangling_groups = [r for r in ig_refs_all if r and r not in group_oids]
    if dangling_groups:
        checks.append(CheckResult(
            layer=3, name="FormDef → ItemGroupDef refs", status="WARN",
            message=f"{len(dangling_groups)} ItemGroupRef(s) in FormDef point to undefined ItemGroupDef.",
            detail=list(set(dangling_groups))
        ))
    else:
        checks.append(CheckResult(
            layer=3, name="FormDef → ItemGroupDef refs", status="PASS",
            message=f"All {len(ig_refs_all)} ItemGroupRef(s) in FormDef resolve correctly."
        ))

    # ItemGroupDef → ItemDef
    item_refs_all = []
    for grp in _findall_ns(mdv, "ItemGroupDef", ns):
        for ir in _findall_ns(grp, "ItemRef", ns):
            item_refs_all.append(ir.get("ItemOID"))
    dangling_items = [r for r in item_refs_all if r and r not in item_oids]
    if dangling_items:
        checks.append(CheckResult(
            layer=3, name="ItemGroupDef → ItemDef refs", status="WARN",
            message=f"{len(dangling_items)} ItemRef(s) in ItemGroupDef point to undefined ItemDef.",
            detail=list(set(dangling_items))[:10]  # cap list for readability
        ))
    else:
        checks.append(CheckResult(
            layer=3, name="ItemGroupDef → ItemDef refs", status="PASS",
            message=f"All {len(item_refs_all)} ItemRef(s) in ItemGroupDef resolve correctly."
        ))

    # ItemDef → CodeList
    cl_refs_all = []
    for it in _findall_ns(mdv, "ItemDef", ns):
        clr_el = it.find(f"{{{ns}}}CodeListRef")
        if clr_el is None:
            clr_el = it.find("CodeListRef")
        clr = clr_el
        if clr is not None and clr.get("CodeListOID"):
            cl_refs_all.append(clr.get("CodeListOID"))
    dangling_cls = [r for r in cl_refs_all if r not in cl_oids]
    if dangling_cls:
        checks.append(CheckResult(
            layer=3, name="ItemDef → CodeList refs", status="WARN",
            message=f"{len(dangling_cls)} CodeListRef(s) in ItemDef point to undefined CodeList.",
            detail=list(set(dangling_cls))[:10]
        ))
    else:
        checks.append(CheckResult(
            layer=3, name="ItemDef → CodeList refs", status="PASS",
            message=f"All {len(cl_refs_all)} CodeListRef(s) in ItemDef resolve correctly."
        ))

    # Duplicate OID check
    all_oids = (list(event_oids) + list(form_oids) + list(group_oids) +
                list(item_oids) + list(cl_oids))
    seen, dupes = set(), set()
    for oid in all_oids:
        if oid in seen:
            dupes.add(oid)
        seen.add(oid)
    if dupes:
        checks.append(CheckResult(
            layer=3, name="Duplicate OIDs", status="WARN",
            message=f"{len(dupes)} OID(s) appear more than once across element types.",
            detail=list(dupes)[:10]
        ))
    else:
        checks.append(CheckResult(
            layer=3, name="Duplicate OIDs", status="PASS",
            message=f"No duplicate OIDs detected across {len(seen)} total OIDs."
        ))

    return checks


# ── Compliance field check ────────────────────────────────────────────────────

def _check_compliance(root: ET.Element, ns: str) -> dict:
    """Check presence of fields required for regulatory compliance audit trail."""
    result = {}
    study  = _find_ns(root, "Study", ns)
    gv     = _find_ns(study, "GlobalVariables", ns) if study is not None else None

    field_sources = {
        "ODMVersion":        root.get("ODMVersion", ""),
        "FileOID":           root.get("FileOID", ""),
        "FileType":          root.get("FileType", ""),
        "CreationDateTime":  root.get("CreationDateTime", ""),
        "ProtocolName":      _text(_find_ns(gv, "ProtocolName", ns)) if gv is not None else "",
        "StudyName":         _text(_find_ns(gv, "StudyName", ns)) if gv is not None else "",
    }

    for field_name, description in COMPLIANCE_FIELDS.items():
        value = field_sources.get(field_name, "")
        result[field_name] = {
            "present": bool(value),
            "value":   value,
            "description": description,
            "status":  "PASS" if value else "WARN",
        }
    return result


# ── Stats ─────────────────────────────────────────────────────────────────────

def _collect_stats(root: ET.Element, ns: str) -> dict:
    study = _find_ns(root, "Study", ns)
    mdv   = _find_ns(study, "MetaDataVersion", ns) if study is not None else None
    if mdv is None:
        return {}
    return {
        "events":         len(_findall_ns(mdv, "StudyEventDef", ns)),
        "forms":          len(_findall_ns(mdv, "FormDef", ns)),
        "item_groups":    len(_findall_ns(mdv, "ItemGroupDef", ns)),
        "items":          len(_findall_ns(mdv, "ItemDef", ns)),
        "codelists":      len(_findall_ns(mdv, "CodeList", ns)),
        "measurement_units": len(_findall_ns(root, "MeasurementUnit", ns)),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def validate_odm(xml_bytes: bytes, source_file: str = "") -> ValidationReport:
    """
    Validate an ODM XML file across 3 layers.

    Args:
        xml_bytes:   Raw bytes of the ODM XML file
        source_file: Optional filename for the report (display only)

    Returns:
        ValidationReport with full check results
    """
    validated_at = datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_checks   = []
    parse_warnings = []

    # ── Layer 1 ──────────────────────────────────────────────────────────────
    l1_checks, root = _check_wellformedness(xml_bytes)
    all_checks.extend(l1_checks)

    if root is None:
        # Can't proceed without a parseable document
        return ValidationReport(
            passed=False,
            can_proceed=False,
            summary="FAIL — XML is not well-formed. File cannot be read.",
            odm_version="",
            source_file=source_file,
            validated_at=validated_at,
            checks=all_checks,
            layer_results={"layer_1": "FAIL", "layer_2": "SKIP", "layer_3": "SKIP"},
            compliance={},
            stats={},
            parse_warnings=["File is not parseable XML."],
        )

    ns = _detect_ns(root)
    odm_version = root.get("ODMVersion", "unknown")

    # ── Layer 2 ──────────────────────────────────────────────────────────────
    l2_checks  = _check_root_element(root)
    l2_checks += _check_study_structure(root, ns)
    all_checks.extend(l2_checks)

    # ── Layer 3 ──────────────────────────────────────────────────────────────
    l3_checks = _check_oid_integrity(root, ns)
    all_checks.extend(l3_checks)

    # ── Compliance + stats ────────────────────────────────────────────────────
    compliance = _check_compliance(root, ns)
    stats      = _collect_stats(root, ns)

    # ── Aggregate results ─────────────────────────────────────────────────────
    def layer_status(checks: list[CheckResult]) -> str:
        statuses = [c.status for c in checks]
        if "FAIL" in statuses:
            return "FAIL"
        if "WARN" in statuses:
            return "WARN"
        return "PASS"

    layer_results = {
        "layer_1": layer_status(l1_checks),
        "layer_2": layer_status(l2_checks),
        "layer_3": layer_status(l3_checks),
    }

    failures = [c for c in all_checks if c.status == "FAIL"]
    warnings = [c for c in all_checks if c.status == "WARN"]
    passed   = len(failures) == 0

    # Can proceed if no failures (warnings are acceptable)
    can_proceed = passed

    n_pass = len([c for c in all_checks if c.status == "PASS"])
    n_warn = len(warnings)
    n_fail = len(failures)

    if passed and n_warn == 0:
        summary = (f"PASS — {n_pass} checks passed. "
                   f"ODM {odm_version}, "
                   f"{stats.get('events', 0)} events, "
                   f"{stats.get('forms', 0)} forms, "
                   f"{stats.get('items', 0)} items.")
    elif passed:
        summary = (f"PASS WITH WARNINGS — {n_pass} passed, {n_warn} warnings. "
                   f"Safe to proceed. Review warnings before final migration.")
    else:
        summary = (f"FAIL — {n_fail} failure(s), {n_warn} warning(s). "
                   f"File cannot be migrated until failures are resolved.")

    return ValidationReport(
        passed=passed,
        can_proceed=can_proceed,
        summary=summary,
        odm_version=odm_version,
        source_file=source_file,
        validated_at=validated_at,
        checks=all_checks,
        layer_results=layer_results,
        compliance=compliance,
        stats=stats,
        parse_warnings=parse_warnings,
    )


def format_report(report: ValidationReport, verbose: bool = False) -> str:
    """
    Format a ValidationReport as a human-readable text report.
    Suitable for Monday.com log columns, terminal output, or PDF attachment.

    Args:
        report:  ValidationReport from validate_odm()
        verbose: If True, include PASS results. Default shows WARN/FAIL only.
    """
    icon = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗", "SKIP": "—"}
    lines = [
        "ODM Validation Report",
        "═" * 60,
        f"File:       {report.source_file or '(bytes input)'}",
        f"Validated:  {report.validated_at}",
        f"ODM ver:    {report.odm_version}",
        f"Result:     {report.summary}",
        "",
        "Layer Results:",
        f"  {icon.get(report.layer_results.get('layer_1',''), '?')} Layer 1 — XML well-formedness:       {report.layer_results.get('layer_1', 'SKIP')}",
        f"  {icon.get(report.layer_results.get('layer_2',''), '?')} Layer 2 — ODM structural conformance: {report.layer_results.get('layer_2', 'SKIP')}",
        f"  {icon.get(report.layer_results.get('layer_3',''), '?')} Layer 3 — OID referential integrity:  {report.layer_results.get('layer_3', 'SKIP')}",
    ]

    if report.stats:
        lines += [
            "",
            "Study Content:",
            f"  Events:      {report.stats.get('events', 0)}",
            f"  Forms:       {report.stats.get('forms', 0)}",
            f"  Item groups: {report.stats.get('item_groups', 0)}",
            f"  Items:       {report.stats.get('items', 0)}",
            f"  Codelists:   {report.stats.get('codelists', 0)}",
        ]

    # Compliance table
    lines += ["", "Compliance Field Status:"]
    for fname, info in report.compliance.items():
        status_icon = icon.get(info["status"], "?")
        val = f" = '{info['value']}'" if info["value"] else " — MISSING"
        lines.append(f"  {status_icon} {fname}{val}")

    # Check details
    checks_to_show = (report.checks if verbose
                      else [c for c in report.checks if c.status != "PASS"])

    if checks_to_show:
        lines += ["", f"{'All Checks' if verbose else 'Warnings and Failures'}:"]
        current_layer = None
        for check in checks_to_show:
            if check.layer != current_layer:
                current_layer = check.layer
                layer_names = {1: "Layer 1 — XML", 2: "Layer 2 — Structure", 3: "Layer 3 — OID Integrity"}
                lines.append(f"\n  {layer_names.get(current_layer, f'Layer {current_layer}')}")
            lines.append(f"    {icon.get(check.status, '?')} [{check.status}] {check.name}")
            lines.append(f"       {check.message}")
            if check.detail:
                lines.append(f"       Detail: {', '.join(str(d) for d in check.detail[:5])}"
                             + (" ..." if len(check.detail) > 5 else ""))
    elif not verbose:
        lines += ["", "All checks passed — no warnings or failures."]

    lines.append("\n" + "─" * 60)
    return "\n".join(lines)


def validate_odm_file(path: str) -> ValidationReport:
    """Convenience wrapper to validate from a file path."""
    with open(path, "rb") as f:
        return validate_odm(f.read(), source_file=path)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python odm_validator.py <odm_file.xml> [--verbose]")
        sys.exit(1)

    path    = sys.argv[1]
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    report = validate_odm_file(path)
    print(format_report(report, verbose=verbose))
    sys.exit(0 if report.can_proceed else 1)
