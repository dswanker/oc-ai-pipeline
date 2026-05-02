"""
generate_accuracy_report.py — Accuracy Scorer for Study Build Trainer

Compares AI-predicted EDC build against human-approved actual build.
Produces an XLSX with two sheets:
  Sheet 1: "Accuracy Scorecard" — per-layer scores + overall %
  Sheet 2: "Diff Appendix"     — row-by-row diffs, human-editable

Layers scored (and their sources):
  1. Study          — ODM XML study OID/name  vs  Study Spec JSON study_meta
  2. Events         — ODM XML StudyEventDef   vs  Study Spec JSON visits_assigned
  3. Form Placement — ODM XML FormRef/event   vs  Study Spec JSON form visits_assigned
  4. Forms          — actual XLSForm ZIP       vs  predicted EDC ZIP (settings sheet)
  5. Items          — actual XLSForm ZIP       vs  predicted EDC ZIP (survey sheet)
  6. Choices        — actual XLSForm ZIP       vs  predicted EDC ZIP (choices sheet)
  7. Logic          — actual XLSForm ZIP       vs  predicted EDC ZIP (relevant/constraint/calc)

Overall score = average of all 7 layer scores.

Entry point:
  generate_accuracy_report(
      actual_xml_bytes,        # ODM XML — human approved final build
      actual_xls_bytes,        # XLSForm ZIP — human approved final forms
      predicted_spec_json,     # Study Spec JSON dict from protocol-analysis skill
      predicted_edc_zip_bytes, # EDC build ZIP from edc-builder skill
      output_path,             # where to write the .xlsx
      study_name="",           # display name for the header
  ) -> dict   # {"overall_pct": float, "layer_scores": {...}}
"""

from __future__ import annotations

import io
import json
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import (
    Alignment, Border, Font, PatternFill, Side
)
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


def _safe_val(v) -> str:
    """
    Safely convert a worksheet cell value to a plain string.
    Handles openpyxl MergedCell objects (non-top-left cells of a merged range)
    which appear when iterating over worksheets with merged cells.
    """
    try:
        from openpyxl.cell.cell import MergedCell
        if isinstance(v, MergedCell):
            return ""
    except ImportError:
        pass
    return str(v).strip() if v is not None else ""


# ── Brand colours (mirror pricing XLSX palette) ──────────────────────────────

OC_DARK   = "1B3A6B"
OC_MID    = "2E6DA4"
OC_LIGHT  = "D6E4F0"
OC_TEAL   = "00A99D"
OC_ORANGE = "F47920"
WHITE     = "FFFFFF"
GREY_L    = "F5F5F5"
GREY_M    = "CCCCCC"
AMBER     = "FFF3CD"
RED_C     = "CC0000"
GREEN_C   = "1A7A1A"

LAYER_COLORS = {
    "Study":          "E8EAF0",
    "Events":         "E3F2FD",
    "Form Placement": "E8F5E9",
    "Forms":          "FFF8E1",
    "Items":          "FCE4EC",
    "Choices":        "F3E5F5",
    "Logic":          "E0F7FA",
}

CONVENTION_OPTIONS = '"This customer only,All customers,Skip — not a convention"'


# ── Style helpers ─────────────────────────────────────────────────────────────

def _fl(h): return PatternFill("solid", fgColor=h)
def _fn(bold=False, color="000000", size=10, italic=False):
    return Font(name="Arial", bold=bold, italic=italic, color=color, size=size)
def _bd(color=GREY_M):
    s = Side(style="thin", color=color)
    return Border(left=s, right=s, top=s, bottom=s)
def _al(h="left", v="center", wrap=True):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)

def _hdr_cell(ws, r, c, val, bg=OC_DARK, fg=WHITE, size=10, bold=True, span=None):
    cell = ws.cell(row=r, column=c, value=val)
    cell.font = _fn(bold=bold, color=fg, size=size)
    cell.fill = _fl(bg)
    cell.alignment = _al()
    cell.border = _bd()
    if span:
        ws.merge_cells(start_row=r, start_column=c,
                       end_row=r, end_column=c + span - 1)
    return cell

def _data_cell(ws, r, c, val, bg=WHITE, bold=False, color="000000",
               h="left", size=9, italic=False, fmt=None):
    cell = ws.cell(row=r, column=c, value=val)
    cell.font = _fn(bold=bold, color=color, size=size, italic=italic)
    cell.fill = _fl(bg)
    cell.alignment = _al(h=h)
    cell.border = _bd()
    if fmt:
        cell.number_format = fmt
    return cell

def _cw(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


# ── ODM XML parser ────────────────────────────────────────────────────────────

_ODM_NS = "http://www.cdisc.org/ns/odm/v1.3"

def _tag(local): return f"{{{_ODM_NS}}}{local}"

def _parse_odm(xml_bytes: bytes) -> dict:
    """
    Parse ODM XML into a structured dict.

    Returns:
      {
        "study_oid": str,
        "study_name": str,
        "events": {oid: {"name": str, "forms": [form_oid]}},
        "forms": {oid: {"name": str}},
        "items": {oid: {"name": str, "data_type": str, "label": str,
                        "codelist_oid": str|None}},
        "codelists": {oid: {"name": str,
                            "items": [{"value": str, "label": str}]}},
      }
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"Invalid ODM XML: {e}") from e

    # Detect namespace
    ns = _ODM_NS if root.tag.startswith("{") else ""
    def t(local): return f"{{{ns}}}{local}" if ns else local

    study_el = root.find(t("Study")) or root.find("Study")
    if study_el is None:
        # Try direct children
        for child in root:
            if child.tag.endswith("Study"):
                study_el = child
                break
    if study_el is None:
        return {"study_oid": "", "study_name": "", "events": {},
                "forms": {}, "items": {}, "codelists": {}}

    study_oid  = study_el.get("OID", "")
    gv         = study_el.find(t("GlobalVariables")) or study_el.find("GlobalVariables")
    study_name = ""
    if gv is not None:
        sn = gv.find(t("StudyName")) or gv.find("StudyName")
        if sn is not None and sn.text:
            study_name = sn.text.strip()

    mdv = study_el.find(t("MetaDataVersion")) or study_el.find("MetaDataVersion")
    if mdv is None:
        return {"study_oid": study_oid, "study_name": study_name,
                "events": {}, "forms": {}, "items": {}, "codelists": {}}

    events    = {}
    forms     = {}
    items     = {}
    codelists = {}

    # Events
    for ev in mdv.findall(t("StudyEventDef")) or mdv.findall("StudyEventDef"):
        oid  = ev.get("OID", "")
        name = ev.get("Name", "")
        form_refs = []
        for fr in ev.findall(t("FormRef")) or ev.findall("FormRef"):
            form_refs.append(fr.get("FormOID", ""))
        events[oid] = {"name": name, "forms": form_refs}

    # Forms
    for fd in mdv.findall(t("FormDef")) or mdv.findall("FormDef"):
        oid  = fd.get("OID", "")
        name = fd.get("Name", "")
        forms[oid] = {"name": name}

    # Items
    for itd in mdv.findall(t("ItemDef")) or mdv.findall("ItemDef"):
        oid       = itd.get("OID", "")
        name      = itd.get("Name", "")
        data_type = itd.get("DataType", "")
        label     = ""
        q = itd.find(t("Question")) or itd.find("Question")
        if q is not None:
            tt = q.find(t("TranslatedText")) or q.find("TranslatedText")
            if tt is not None and tt.text:
                label = tt.text.strip()
        cl_ref = itd.find(t("CodeListRef")) or itd.find("CodeListRef")
        cl_oid = cl_ref.get("CodeListOID", "") if cl_ref is not None else ""
        items[oid] = {"name": name, "data_type": data_type,
                      "label": label, "codelist_oid": cl_oid}

    # Codelists
    for cl in mdv.findall(t("CodeList")) or mdv.findall("CodeList"):
        oid  = cl.get("OID", "")
        name = cl.get("Name", "")
        cl_items = []
        for cli in cl.findall(t("CodeListItem")) or cl.findall("CodeListItem"):
            val   = cli.get("CodedValue", "")
            lbl   = ""
            dc = cli.find(t("Decode")) or cli.find("Decode")
            if dc is not None:
                tt = dc.find(t("TranslatedText")) or dc.find("TranslatedText")
                if tt is not None and tt.text:
                    lbl = tt.text.strip()
            cl_items.append({"value": val, "label": lbl})
        codelists[oid] = {"name": name, "items": cl_items}

    return {
        "study_oid":  study_oid,
        "study_name": study_name,
        "events":     events,
        "forms":      forms,
        "items":      items,
        "codelists":  codelists,
    }


# ── XLSForm ZIP parser ────────────────────────────────────────────────────────

def _parse_xlsform_zip(zip_bytes: bytes) -> dict:
    """
    Parse a ZIP of XLSForm .xlsx files.

    Returns:
      {
        form_id: {
          "form_title": str,
          "survey":  [{"name": str, "type": str, "label": str,
                       "relevant": str, "constraint": str, "calculation": str}],
          "choices": [{"list_name": str, "name": str, "label": str}],
        }
      }
    """
    import openpyxl

    result = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if not name.endswith(".xlsx") or name.startswith("__"):
                    continue
                try:
                    wb = openpyxl.load_workbook(
                        io.BytesIO(zf.read(name)), data_only=True, read_only=True
                    )
                except Exception:
                    continue

                # settings
                settings   = {}
                form_id    = ""
                form_title = ""
                if "settings" in wb.sheetnames:
                    ws   = wb["settings"]
                    rows = list(ws.values)
                    if len(rows) >= 2:
                        hdrs = [_safe_val(h) for h in rows[0]]
                        vals = [_safe_val(v) for v in rows[1]]
                        settings   = dict(zip(hdrs, vals))
                        form_id    = settings.get("form_id", "")
                        form_title = settings.get("form_title", "")

                # Strip F_ prefix from form_id for matching
                raw_form_id = form_id
                if form_id.upper().startswith("F_"):
                    form_id = form_id[2:]

                # Use filename as fallback form_id
                if not form_id:
                    form_id = Path(name).stem

                # survey
                survey = []
                if "survey" in wb.sheetnames:
                    ws   = wb["survey"]
                    rows = list(ws.values)
                    if rows:
                        hdrs = [str(h).strip().lower() if h else "" for h in rows[0]]
                        for row in rows[1:]:
                            if not any(v for v in row):
                                continue
                            d = {h: (str(v).strip() if v is not None else "")
                                 for h, v in zip(hdrs, row)}
                            if d.get("name") or d.get("type"):
                                survey.append({
                                    "name":        d.get("name", ""),
                                    "type":        d.get("type", ""),
                                    "label":       d.get("label", ""),
                                    "relevant":    d.get("relevant", ""),
                                    "constraint":  d.get("constraint", ""),
                                    "calculation": d.get("calculation", ""),
                                })

                # choices
                choices = []
                if "choices" in wb.sheetnames:
                    ws   = wb["choices"]
                    rows = list(ws.values)
                    if rows:
                        hdrs = [str(h).strip().lower() if h else "" for h in rows[0]]
                        for row in rows[1:]:
                            if not any(v for v in row):
                                continue
                            d = {h: (str(v).strip() if v is not None else "")
                                 for h, v in zip(hdrs, row)}
                            if d.get("list_name") or d.get("name"):
                                choices.append({
                                    "list_name": d.get("list_name", ""),
                                    "name":      d.get("name", ""),
                                    "label":     d.get("label", ""),
                                })

                wb.close()
                result[form_id.upper()] = {
                    "form_title": form_title,
                    "raw_form_id": raw_form_id,
                    "survey":  survey,
                    "choices": choices,
                }
    except Exception as e:
        raise ValueError(f"Cannot parse XLSForm ZIP: {e}") from e

    return result


# ── Predicted build extractor ─────────────────────────────────────────────────

def _extract_predicted(spec_json: dict) -> dict:
    """
    Extract predicted Study/Event/Form Placement from Study Spec JSON.

    Returns:
      {
        "study_oid":  str,
        "study_name": str,
        "events":     {event_oid: True},         # set of known events
        "placements": {(event_oid, form_id): True},
        "forms":      {form_id: {"form_title": str}},
      }
    """
    meta       = spec_json.get("study_meta", {})
    study_oid  = meta.get("study_oid", meta.get("protocol_number", ""))
    study_name = meta.get("study_title", meta.get("protocol_number", ""))

    events     = {}
    placements = {}
    forms_out  = {}

    form_list = spec_json.get("forms", [])
    if isinstance(form_list, dict):
        # Some skill outputs use a dict keyed by form_id
        form_list = [{"form_id": k, **v} for k, v in form_list.items()]

    for form in form_list:
        form_id    = str(form.get("form_id", "")).upper()
        form_title = (form.get("form_title", "")
                      or form.get("settings", {}).get("form_title", ""))
        visits     = form.get("visits_assigned", [])

        forms_out[form_id] = {"form_title": form_title}

        for ev in visits:
            ev = str(ev).strip()
            if ev:
                events[ev] = True
                placements[(ev, form_id)] = True

    return {
        "study_oid":  study_oid,
        "study_name": study_name,
        "events":     events,
        "placements": placements,
        "forms":      forms_out,
    }


# ── Scoring ───────────────────────────────────────────────────────────────────

@dataclass
class DiffRow:
    layer:    str
    element:  str
    ai_value: str
    hu_value: str   # human/actual value
    note:     str = ""


def _score(matched: int, total: int) -> float:
    if total == 0:
        return 100.0
    return round(100.0 * matched / total, 1)


def score_study(actual_odm: dict, predicted: dict) -> tuple[float, list[DiffRow]]:
    diffs   = []
    matched = 0
    total   = 2   # OID + name

    if actual_odm["study_oid"] == predicted["study_oid"]:
        matched += 1
    else:
        diffs.append(DiffRow("Study", "Study OID",
                             predicted["study_oid"], actual_odm["study_oid"]))

    # Normalise names for comparison
    a_name = actual_odm["study_name"].strip().lower()
    p_name = predicted["study_name"].strip().lower()
    if a_name == p_name or not a_name or not p_name:
        matched += 1
    else:
        diffs.append(DiffRow("Study", "Study Name",
                             predicted["study_name"], actual_odm["study_name"]))

    return _score(matched, total), matched, total, diffs


def score_events(actual_odm: dict, predicted: dict) -> tuple[float, list[DiffRow]]:
    """Score event-level match. Matches by OID."""
    actual_ev    = set(actual_odm["events"].keys())
    predicted_ev = set(predicted["events"].keys())
    diffs        = []

    total   = len(actual_ev)
    matched = 0

    for ev in sorted(actual_ev):
        name = actual_odm["events"][ev]["name"]
        if ev in predicted_ev:
            matched += 1
        else:
            diffs.append(DiffRow("Events", f"Event: {name} ({ev})",
                                 "— missing —", ev))

    for ev in sorted(predicted_ev - actual_ev):
        diffs.append(DiffRow("Events", f"Extra event: {ev}",
                             ev, "— not in actual —"))

    return _score(matched, total), matched, total, diffs


def score_form_placement(actual_odm: dict, predicted: dict) -> tuple[float, list[DiffRow]]:
    """Score (event, form) assignment pairs."""
    # Build actual placements from ODM
    actual_pairs = set()
    for ev_oid, ev in actual_odm["events"].items():
        for form_oid in ev["forms"]:
            # Normalise form OID: strip F_ prefix
            fid = form_oid[2:].upper() if form_oid.upper().startswith("F_") else form_oid.upper()
            actual_pairs.add((ev_oid, fid))

    predicted_pairs = set(predicted["placements"].keys())
    diffs           = []
    total           = len(actual_pairs)
    matched         = 0

    for (ev, fid) in sorted(actual_pairs):
        if (ev, fid) in predicted_pairs:
            matched += 1
        else:
            ev_name   = actual_odm["events"].get(ev, {}).get("name", ev)
            diffs.append(DiffRow(
                "Form Placement",
                f"{fid} → {ev_name}",
                "— missing —",
                f"{fid} assigned to {ev}",
            ))

    for (ev, fid) in sorted(predicted_pairs - actual_pairs):
        diffs.append(DiffRow(
            "Form Placement",
            f"Extra: {fid} → {ev}",
            f"{fid} assigned to {ev}",
            "— not in actual —",
        ))

    return _score(matched, total), matched, total, diffs


def score_forms(actual_xls: dict, predicted_xls: dict) -> tuple[float, list[DiffRow]]:
    """Score form-level match by form_id."""
    actual_ids    = set(actual_xls.keys())
    predicted_ids = set(predicted_xls.keys())
    diffs         = []
    total         = len(actual_ids)
    matched       = 0

    for fid in sorted(actual_ids):
        a_title = actual_xls[fid]["form_title"]
        if fid in predicted_ids:
            matched += 1
            p_title = predicted_xls[fid]["form_title"]
            if a_title.strip().lower() != p_title.strip().lower() and a_title and p_title:
                diffs.append(DiffRow(
                    "Forms", f"Title mismatch: {fid}",
                    p_title, a_title,
                ))
        else:
            diffs.append(DiffRow("Forms", f"Form: {fid} ({a_title})",
                                 "— missing —", fid))

    for fid in sorted(predicted_ids - actual_ids):
        p_title = predicted_xls[fid]["form_title"]
        diffs.append(DiffRow("Forms", f"Extra form: {fid} ({p_title})",
                             fid, "— not in actual —"))

    return _score(matched, total), matched, total, diffs


def score_items(actual_xls: dict, predicted_xls: dict) -> tuple[float, list[DiffRow]]:
    """Score item-level match across all forms."""
    diffs   = []
    total   = 0
    matched = 0

    DATA_TYPES = {"text", "integer", "decimal", "date", "datetime",
                  "time", "select_one", "select_multiple", "note", "calculate"}

    for fid in sorted(actual_xls.keys()):
        a_items = {
            r["name"]: r for r in actual_xls[fid]["survey"]
            if r["name"] and r["type"].split(" ")[0].lower() in DATA_TYPES
        }
        p_items = {}
        if fid in predicted_xls:
            p_items = {
                r["name"]: r for r in predicted_xls[fid]["survey"]
                if r["name"] and r["type"].split(" ")[0].lower() in DATA_TYPES
            }

        for name in sorted(a_items.keys()):
            total += 1
            a = a_items[name]
            if name in p_items:
                p = p_items[name]
                # Check data type match
                a_type = a["type"].split(" ")[0].lower()
                p_type = p["type"].split(" ")[0].lower()
                if a_type == p_type:
                    matched += 1
                else:
                    diffs.append(DiffRow(
                        "Items", f"{fid}.{name} — data type",
                        f"{p_type}",
                        f"{a_type}",
                        "Data type differs",
                    ))
            else:
                diffs.append(DiffRow(
                    "Items", f"{fid}.{name}",
                    "— missing —",
                    f"{name} ({a['type']})",
                ))

        for name in sorted(set(p_items) - set(a_items)):
            diffs.append(DiffRow(
                "Items", f"Extra: {fid}.{name}",
                f"{name} ({p_items[name]['type']})",
                "— not in actual —",
            ))

    return _score(matched, total), matched, total, diffs


def score_choices(actual_xls: dict, predicted_xls: dict) -> tuple[float, list[DiffRow]]:
    """Score choice list match."""
    diffs   = []
    total   = 0
    matched = 0

    # Aggregate choice lists across all forms
    def _agg(xls):
        lists = {}  # list_name -> {value: label}
        for fid, form in xls.items():
            for ch in form["choices"]:
                ln = ch["list_name"]
                if ln not in lists:
                    lists[ln] = {}
                lists[ln][ch["name"]] = ch["label"]
        return lists

    actual_lists    = _agg(actual_xls)
    predicted_lists = _agg(predicted_xls)

    for ln in sorted(actual_lists.keys()):
        a_vals = actual_lists[ln]
        if ln not in predicted_lists:
            total   += len(a_vals)
            for val, lbl in a_vals.items():
                diffs.append(DiffRow(
                    "Choices", f"{ln} — entire list missing",
                    "— missing —", f"{val}: {lbl}",
                ))
            continue

        p_vals = predicted_lists[ln]
        for val in sorted(a_vals.keys()):
            total += 1
            if val in p_vals:
                matched += 1
                # Flag label mismatches
                a_lbl = a_vals[val].strip().lower()
                p_lbl = p_vals[val].strip().lower()
                if a_lbl != p_lbl and a_lbl and p_lbl:
                    diffs.append(DiffRow(
                        "Choices", f"{ln}.{val} — label",
                        p_vals[val], a_vals[val],
                        "Label differs",
                    ))
            else:
                diffs.append(DiffRow(
                    "Choices", f"{ln}.{val}",
                    "— missing —", f"{val}: {a_vals[val]}",
                ))

        for val in sorted(set(p_vals) - set(a_vals)):
            diffs.append(DiffRow(
                "Choices", f"Extra: {ln}.{val}",
                f"{val}: {p_vals[val]}", "— not in actual —",
            ))

    return _score(matched, total), matched, total, diffs


def score_logic(actual_xls: dict, predicted_xls: dict) -> tuple[float, list[DiffRow]]:
    """
    Score logic match (relevant, constraint, calculation).
    Checks presence, not exact XPath equality (which would be too strict).
    An item gets credit if both actual and predicted have logic (or both don't).
    """
    diffs   = []
    total   = 0
    matched = 0

    LOGIC_COLS = ("relevant", "constraint", "calculation")
    DATA_TYPES = {"text", "integer", "decimal", "date", "datetime",
                  "time", "select_one", "select_multiple", "note", "calculate"}

    def _has_logic(row):
        return any(row.get(c, "").strip() for c in LOGIC_COLS)

    def _logic_summary(row):
        parts = []
        for c in LOGIC_COLS:
            v = row.get(c, "").strip()
            if v:
                parts.append(f"{c}: {v[:60]}{'…' if len(v)>60 else ''}")
        return " | ".join(parts) or "—"

    for fid in sorted(actual_xls.keys()):
        a_survey = {
            r["name"]: r for r in actual_xls[fid]["survey"]
            if r["name"] and r["type"].split(" ")[0].lower() in DATA_TYPES
        }
        p_survey = {}
        if fid in predicted_xls:
            p_survey = {
                r["name"]: r for r in predicted_xls[fid]["survey"]
                if r["name"] and r["type"].split(" ")[0].lower() in DATA_TYPES
            }

        for name in sorted(a_survey.keys()):
            a = a_survey[name]
            if not _has_logic(a) and name not in p_survey:
                continue  # neither has logic, skip

            total += 1
            a_logic = _has_logic(a)
            p_logic = _has_logic(p_survey.get(name, {}))

            if a_logic == p_logic:
                matched += 1
            elif a_logic and not p_logic:
                diffs.append(DiffRow(
                    "Logic", f"{fid}.{name} — missing logic",
                    "— no logic —",
                    _logic_summary(a),
                    "Actual has logic; AI prediction missing",
                ))
            else:
                diffs.append(DiffRow(
                    "Logic", f"{fid}.{name} — extra logic",
                    _logic_summary(p_survey[name]),
                    "— no logic —",
                    "AI added logic not in actual build",
                ))

    return _score(matched, total), matched, total, diffs


# ── XLSX builder ──────────────────────────────────────────────────────────────

def _build_scorecard_sheet(ws, layer_scores: dict, overall: float,
                           study_name: str, generated_date: str):
    """Build Sheet 1: Accuracy Scorecard."""
    _cw(ws, [28, 16, 16, 16, 30])
    row = 1

    # Title banner
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    c = ws.cell(row=row, column=1,
                value="OpenClinica Study Build Trainer — Accuracy Scorecard")
    c.font = _fn(bold=True, color=WHITE, size=14)
    c.fill = _fl(OC_DARK)
    c.alignment = _al(h="center")
    ws.row_dimensions[row].height = 28
    row += 1

    # Study info row
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    c = ws.cell(row=row, column=1, value=f"Study: {study_name}")
    c.font = _fn(size=10, color=WHITE)
    c.fill = _fl(OC_MID)
    c.alignment = _al()
    ws.cell(row=row, column=4).fill = _fl(OC_MID)
    ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=5)
    c2 = ws.cell(row=row, column=4, value=f"Generated: {generated_date}")
    c2.font = _fn(size=9, color=WHITE, italic=True)
    c2.fill = _fl(OC_MID)
    c2.alignment = _al(h="right")
    ws.row_dimensions[row].height = 18
    row += 1

    # Overall score band
    row += 1
    # Overall score band — split into label (cols 1-3) and value (cols 4-5)
    # Calculate the formula reference BEFORE writing cells
    score_start_row = row + 4  # actual data rows start after blank + header
    score_end_row   = score_start_row + len(layer_scores) - 1
    overall_cell    = f"=AVERAGE(C{score_start_row}:C{score_end_row})"

    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    c = ws.cell(row=row, column=1)
    c.value = "OVERALL ACCURACY SCORE"
    c.font  = _fn(bold=True, color=WHITE, size=13)
    c.fill  = _fl(OC_TEAL)
    c.alignment = _al(h="left")

    ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=5)
    score_cell = ws.cell(row=row, column=4)
    score_cell.value        = overall_cell
    score_cell.font         = _fn(bold=True, color=WHITE, size=13)
    score_cell.fill         = _fl(OC_TEAL)
    score_cell.alignment    = _al(h="right")
    score_cell.number_format = '0.0"%"'
    ws.row_dimensions[row].height = 26
    row += 1

    row += 1  # blank
    # Column headers
    for col, (hdr, w) in enumerate([
        ("Layer", None), ("Matched", None), ("Score %", None),
        ("Total Elements", None), ("Notes", None),
    ], start=1):
        _hdr_cell(ws, row, col, hdr, bg=OC_MID)
    ws.row_dimensions[row].height = 18
    row += 1

    # Layer rows
    LAYER_NOTES = {
        "Study":          "Study OID and study name match",
        "Events":         "Study event definitions (visits) match",
        "Form Placement": "Form-to-event assignment match",
        "Forms":          "Form definitions match",
        "Items":          "Data items match (name + data type)",
        "Choices":        "Choice list values match",
        "Logic":          "Relevance, constraint, calculation presence match",
    }

    for layer, data in layer_scores.items():
        bg = LAYER_COLORS.get(layer, GREY_L)
        _data_cell(ws, row, 1, layer, bg=bg, bold=True, color=OC_DARK, size=9)
        _data_cell(ws, row, 2, data["matched"], bg=bg, h="center", size=9)
        score_cell = ws.cell(row=row, column=3, value=data["score"] / 100)
        score_cell.font        = _fn(bold=True, size=9,
                                    color=GREEN_C if data["score"] >= 80 else RED_C)
        score_cell.fill        = _fl(bg)
        score_cell.alignment   = _al(h="center")
        score_cell.border      = _bd()
        score_cell.number_format = '0.0%'
        _data_cell(ws, row, 4, data["total"], bg=bg, h="center", size=9)
        _data_cell(ws, row, 5, LAYER_NOTES.get(layer, ""), bg=bg,
                   italic=True, color="555555", size=8)
        ws.row_dimensions[row].height = 16
        row += 1

    row += 1  # spacer
    # Legend
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    c = ws.cell(row=row, column=1,
                value="Score ≥ 80% = Good  |  50–79% = Review Needed  |  < 50% = Significant Gaps")
    c.font      = _fn(size=8, italic=True, color="555555")
    c.fill      = _fl(GREY_L)
    c.alignment = _al(h="center")
    ws.row_dimensions[row].height = 14

    ws.freeze_panes = "A5"


def _build_diff_sheet(ws, diffs: list[DiffRow]):
    """Build Sheet 2: Diff Appendix.

    Columns:
      1. Layer
      2. Element / Field
      3. AI Generated
      4. Human Approved
      5. Notes (Human)          ← human fills in
      6. Convention              ← dropdown: This customer only / All customers / Skip
      7. Claude Questions        ← Claude writes questions on re-submission
      8. Human Response          ← human answers Claude's questions
    """
    NC = 8
    _cw(ws, [18, 30, 32, 32, 28, 22, 34, 34])
    row = 1

    # Title
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NC)
    c = ws.cell(row=row, column=1, value="APPENDIX — Build Accuracy Diff Detail")
    c.font      = _fn(bold=True, color=WHITE, size=12)
    c.fill      = _fl(OC_DARK)
    c.alignment = _al(h="center")
    ws.row_dimensions[row].height = 24
    row += 1

    # Subtitle
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NC)
    c = ws.cell(row=row, column=1,
                value=("Each row is a mismatch between AI prediction and human-approved build. "
                       "Fill in Notes + Convention, then upload to the Convention Rulebook board "
                       "and set Submit Trigger → Submit for Review. Claude will write questions "
                       "in column G if any rows need clarification — answer in column H and re-submit."))
    c.font      = _fn(size=9, italic=True, color="555555")
    c.fill      = _fl(GREY_L)
    c.alignment = _al()
    ws.row_dimensions[row].height = 20
    row += 1

    row += 1  # blank

    # Column headers
    headers = [
        "Layer",
        "Element / Field",
        "AI Generated",
        "Human Approved",
        "Notes (Human)",
        "Convention",
        "Claude Questions",
        "Human Response",
    ]
    for col, hdr in enumerate(headers, start=1):
        bg = OC_MID
        # Highlight the two Q&A columns differently
        if col in (7, 8):
            bg = OC_DARK
        _hdr_cell(ws, row, col, hdr, bg=bg)
    ws.row_dimensions[row].height = 18
    data_start_row = row + 1
    row += 1

    # Column header notes row — sub-header explaining each input column
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    c = ws.cell(row=row, column=1,
                value="← Generated by scorer — do not edit")
    c.font      = _fn(size=7, italic=True, color="888888")
    c.fill      = _fl(GREY_L)
    c.alignment = _al(h="center")

    ws.merge_cells(start_row=row, start_column=5, end_row=row, end_column=6)
    c = ws.cell(row=row, column=5,
                value="← Human fills these before submitting")
    c.font      = _fn(size=7, italic=True, color=OC_DARK)
    c.fill      = _fl("FFFDE7")
    c.alignment = _al(h="center")

    ws.merge_cells(start_row=row, start_column=7, end_row=row, end_column=8)
    c = ws.cell(row=row, column=7,
                value="← Q&A between Claude and Human (multi-round)")
    c.font      = _fn(size=7, italic=True, color=WHITE)
    c.fill      = _fl(OC_DARK)
    c.alignment = _al(h="center")
    ws.row_dimensions[row].height = 13
    row += 1

    if not diffs:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NC)
        c = ws.cell(row=row, column=1, value="✓ No differences found — perfect match!")
        c.font      = _fn(bold=True, color=GREEN_C, size=10)
        c.fill      = _fl(WHITE)
        c.alignment = _al(h="center")
        ws.row_dimensions[row].height = 20
        return

    # Data rows, grouped by layer
    current_layer = None
    for diff in diffs:
        bg = LAYER_COLORS.get(diff.layer, GREY_L)

        if diff.layer != current_layer:
            current_layer = diff.layer
            # Layer group header — spans all 8 columns
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NC)
            c = ws.cell(row=row, column=1, value=f"  {diff.layer.upper()}")
            c.font      = _fn(bold=True, color=WHITE, size=9)
            c.fill      = _fl(OC_DARK)
            c.alignment = _al()
            ws.row_dimensions[row].height = 16
            row += 1

        # Columns 1-4: scorer-generated, read-only visually
        _data_cell(ws, row, 1, diff.layer, bg=bg, bold=True, color=OC_DARK, size=8)
        _data_cell(ws, row, 2, diff.element, bg=bg, size=8)
        _data_cell(ws, row, 3, diff.ai_value, bg=bg, size=8,
                   color=RED_C if "missing" in diff.ai_value.lower() else "000000")
        _data_cell(ws, row, 4, diff.hu_value, bg=bg, size=8,
                   color=RED_C if "missing" in diff.hu_value.lower() else "000000")

        # Column 5: Notes — pale yellow, human fills in
        _data_cell(ws, row, 5, diff.note, bg="FFFDE7" if not diff.note else AMBER,
                   italic=True, color="555555", size=8)

        # Column 6: Convention dropdown — pale yellow, human fills in
        c = ws.cell(row=row, column=6, value="")
        c.font      = _fn(size=8)
        c.fill      = _fl("FFFDE7")
        c.alignment = _al()
        c.border    = _bd()

        # Column 7: Claude Questions — light blue, Claude writes here on re-submission
        c = ws.cell(row=row, column=7, value="")
        c.font      = _fn(size=8, italic=True, color="003366")
        c.fill      = _fl("E8F4FD")   # pale blue — Claude's territory
        c.alignment = _al()
        c.border    = _bd()

        # Column 8: Human Response — pale green, human answers Claude's questions
        c = ws.cell(row=row, column=8, value="")
        c.font      = _fn(size=8)
        c.fill      = _fl("F0FFF0")   # pale green — human response
        c.alignment = _al()
        c.border    = _bd()

        ws.row_dimensions[row].height = 20
        row += 1

    # Convention dropdown validation on column F
    dv = DataValidation(
        type="list",
        formula1=CONVENTION_OPTIONS,
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Invalid selection",
        error="Choose: 'This customer only', 'All customers', or 'Skip — not a convention'",
    )
    ws.add_data_validation(dv)
    dv.sqref = f"F{data_start_row}:F{row}"

    # Footer
    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NC)
    c = ws.cell(row=row, column=1,
                value=(f"Total differences: {len(diffs)}  |  "
                       f"Yellow = human input needed  |  "
                       f"Blue = Claude questions  |  "
                       f"Green = human responses to Claude"))
    c.font      = _fn(bold=True, size=8, color=OC_DARK)
    c.fill      = _fl(OC_LIGHT)
    c.alignment = _al()
    ws.row_dimensions[row].height = 14

    ws.freeze_panes = "A6"


# ── Public entry point ────────────────────────────────────────────────────────

def generate_accuracy_report(
    actual_xml_bytes: bytes,
    actual_xls_bytes: bytes,
    predicted_spec_json: dict,
    predicted_edc_zip_bytes: bytes,
    output_path: str,
    study_name: str = "",
) -> dict:
    """
    Generate accuracy report XLSX.

    Args:
      actual_xml_bytes:        ODM XML (human-approved build)
      actual_xls_bytes:        XLSForm ZIP (human-approved forms)
      predicted_spec_json:     Study Spec JSON (from protocol-analysis skill)
      predicted_edc_zip_bytes: EDC Build ZIP (from edc-builder skill)
      output_path:             Where to write the .xlsx file
      study_name:              Display name for header

    Returns:
      {
        "overall_pct": float,
        "layer_scores": {layer: {"score": float, "matched": int, "total": int}},
        "diff_count": int,
      }
    """
    # 1. Parse inputs
    actual_odm   = _parse_odm(actual_xml_bytes)
    actual_xls   = _parse_xlsform_zip(actual_xls_bytes)
    predicted    = _extract_predicted(predicted_spec_json)
    predicted_xls = _parse_xlsform_zip(predicted_edc_zip_bytes)

    if not study_name:
        study_name = (actual_odm.get("study_name")
                      or predicted.get("study_name")
                      or "Unknown Study")

    # 2. Score each layer
    all_diffs: list[DiffRow] = []

    def _run(fn, *args):
        result = fn(*args)
        score, matched, total, diffs = result
        all_diffs.extend(diffs)
        return score, matched, total

    study_score,  study_matched,  study_total  = _run(score_study, actual_odm, predicted)
    event_score,  event_matched,  event_total  = _run(score_events, actual_odm, predicted)
    place_score,  place_matched,  place_total  = _run(score_form_placement, actual_odm, predicted)
    form_score,   form_matched,   form_total   = _run(score_forms, actual_xls, predicted_xls)
    item_score,   item_matched,   item_total   = _run(score_items, actual_xls, predicted_xls)
    choice_score, choice_matched, choice_total = _run(score_choices, actual_xls, predicted_xls)
    logic_score,  logic_matched,  logic_total  = _run(score_logic, actual_xls, predicted_xls)


    layer_scores = {
        "Study":          {"score": study_score,  "matched": study_matched,  "total": study_total},
        "Events":         {"score": event_score,  "matched": event_matched,  "total": event_total},
        "Form Placement": {"score": place_score,  "matched": place_matched,  "total": place_total},
        "Forms":          {"score": form_score,   "matched": form_matched,   "total": form_total},
        "Items":          {"score": item_score,   "matched": item_matched,   "total": item_total},
        "Choices":        {"score": choice_score, "matched": choice_matched, "total": choice_total},
        "Logic":          {"score": logic_score,  "matched": logic_matched,  "total": logic_total},
    }

    overall = round(sum(d["score"] for d in layer_scores.values()) / len(layer_scores), 1)

    # 3. Build XLSX
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Accuracy Scorecard"
    ws1.sheet_properties.tabColor = OC_TEAL

    ws2 = wb.create_sheet(title="Diff Appendix")
    ws2.sheet_properties.tabColor = OC_ORANGE

    _build_scorecard_sheet(
        ws1, layer_scores, overall, study_name,
        date.today().strftime("%B %d, %Y"),
    )
    _build_diff_sheet(ws2, all_diffs)

    wb.save(output_path)

    return {
        "overall_pct":  overall,
        "layer_scores": {k: {"score": v["score"]} for k, v in layer_scores.items()},
        "diff_count":   len(all_diffs),
    }


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    # Usage: python generate_accuracy_report.py actual.xml actual.zip predicted_spec.json predicted_edc.zip output.xlsx
    if len(sys.argv) != 6:
        print("Usage: generate_accuracy_report.py <actual_xml> <actual_zip> "
              "<predicted_spec_json> <predicted_edc_zip> <output_xlsx>")
        sys.exit(1)

    actual_xml_path, actual_zip_path, spec_json_path, pred_zip_path, out_path = sys.argv[1:]

    result = generate_accuracy_report(
        actual_xml_bytes        = open(actual_xml_path,  "rb").read(),
        actual_xls_bytes        = open(actual_zip_path,  "rb").read(),
        predicted_spec_json     = json.loads(open(spec_json_path).read()),
        predicted_edc_zip_bytes = open(pred_zip_path,    "rb").read(),
        output_path             = out_path,
    )
    print(f"Overall: {result['overall_pct']}%")
    for layer, data in result["layer_scores"].items():
        print(f"  {layer:20s}: {data['score']}%")
    print(f"Diffs: {result['diff_count']}")
    print(f"Written: {out_path}")
