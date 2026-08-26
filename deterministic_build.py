"""
deterministic_build.py — Chain BX building blocks (no AI calls)

Shared helpers for producing DVS / spec artifacts directly from a
customer-supplied EDC Build ZIP, without running Protocol Analysis.

build_forms_json_from_zip()
    Reads every .xlsx in an EDC Build ZIP into {survey, choices, settings}
    per form. settings is needed here (not just survey/choices) because
    event-placement matching keys off each form's declared form_title.

build_event_form_map()
    Rebuilds the {form_key: [event_oid, ...]} shape extract_dvs_data()
    needs, in the absence of a Chain-A-produced Study Spec JSON.

    IMPORTANT — corrected 2026-08-19: the original version of this
    function matched ODM FormRef entries by raw FormOID string only.
    Against a real GON001 test, this produced a mapping keyed entirely
    by the OLD build's OIDs (F_JEN01_SCREEN, F_GON01_GONORR, ...) with
    ZERO correspondence to the actual uploaded XLSForms' own identity —
    the reported "32/43 forms placed" was fabricated-by-coincidence,
    not a real match. See conversation 2026-08-19 for the full incident.

    Matching precedence per form, highest first:
      1. manual_overrides — {form_key: event_oid | [event_oid, ...]},
         human-supplied, always wins.
      2. ODM match BY NAME — form_title (from the form's own settings
         sheet) fuzzy-matched against each ODM FormDef's Name attribute.
         This is the primary, trustworthy automated path — it doesn't
         depend on OIDs corresponding across builds, only on the form's
         human-readable name being reasonably stable.
      3. ODM match BY OID — exact FormOID string match. Fallback only;
         reliable ONLY when the ODM export is from the SAME build as
         the XLSForm ZIP (OIDs won't coincidentally agree otherwise).

    A form can legitimately appear at MULTIPLE events (e.g. Physical
    Exam at nearly every visit) — the return shape is a list per form,
    not a single OID, and manual_overrides accepts either a single
    value or a list.

    REMOVED 2026-08-19 — settings-heuristic fallback (cross-form
    calculate-reference inference). Against real GON001 data it
    produced confident-looking but WRONG single-event placements for
    AE, CM, and BIOSP (all landed on SE_SCREENING because each has an
    unrelated calculate field that reads the ICF consent date FROM
    SE_SCREENING — "an event this form reads from" is not "the event
    this form lives at"). A false placement is worse than an honest
    gap, so forms that don't resolve via manual_overrides or ODM
    matching now go to unplaced rather than getting a guessed value.

Known limitation: forms with no manual override and no ODM name/OID
match cannot be placed automatically and need a human decision.
"""

import io
import os
import re
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

import openpyxl

_ODM_NS = {"odm": "http://www.cdisc.org/ns/odm/v1.3"}
_NAME_MATCH_THRESHOLD = 0.6


def build_forms_json_from_zip(zip_bytes):
    """Read every .xlsx in an EDC Build ZIP into:
    {"forms": {filename: {"survey": [...], "choices": [...], "settings": {...}}}}

    settings is a flat dict of the settings sheet's single data row
    (form_title, form_id, version, etc.) — needed for name-based event
    matching. survey/choices remain row-lists as before.
    """
    import zipfile

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    forms_json = {"forms": {}}

    for name in sorted(zf.namelist()):
        if not name.lower().endswith(".xlsx"):
            continue
        try:
            with zf.open(name) as f:
                wb_bytes = f.read()
            wb = openpyxl.load_workbook(io.BytesIO(wb_bytes),
                                         read_only=True, data_only=True)

            def _sheet_rows(sheet_name):
                if sheet_name not in wb.sheetnames:
                    return []
                ws = wb[sheet_name]
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    return []
                headers = [str(h or "").strip() for h in rows[0]]
                out = []
                for r in rows[1:]:
                    row_dict = {headers[i]: r[i]
                                for i in range(len(headers))
                                if i < len(r) and r[i] is not None}
                    if row_dict:
                        out.append(row_dict)
                return out

            def _settings_dict():
                if "settings" not in wb.sheetnames:
                    return {}
                ws = wb["settings"]
                rows = list(ws.iter_rows(values_only=True))
                if len(rows) < 2:
                    return {}
                headers = [str(h or "").strip() for h in rows[0]]
                values = rows[1]
                return {headers[i]: values[i]
                        for i in range(len(headers))
                        if i < len(values) and values[i] is not None}

            forms_json["forms"][os.path.basename(name)] = {
                "survey":   _sheet_rows("survey"),
                "choices":  _sheet_rows("choices"),
                "settings": _settings_dict(),
            }
        except Exception as e:
            print(f"[deterministic-build] skipping {name}: {e}", flush=True)

    return forms_json


def _normalize_name(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _odm_form_index(odm_xml_bytes):
    """Parse ODM into {form_oid: {"name": Name, "events": [event_oid, ...]}}.

    events lists EVERY StudyEventDef that references the form (order of
    appearance in the ODM) — a form legitimately repeating across many
    visits gets all of them, not just the first.
    """
    try:
        root = ET.fromstring(odm_xml_bytes)
    except ET.ParseError as e:
        print(f"[deterministic-build] ODM parse failed: {e}", flush=True)
        return {}

    form_names = {}
    for form_def in root.iter("{http://www.cdisc.org/ns/odm/v1.3}FormDef"):
        oid = form_def.get("OID", "")
        if oid:
            form_names[oid] = form_def.get("Name", "")

    form_events = {}
    for event_def in root.iter("{http://www.cdisc.org/ns/odm/v1.3}StudyEventDef"):
        event_oid = event_def.get("OID", "")
        if not event_oid:
            continue
        for form_ref in event_def.findall("odm:FormRef", _ODM_NS):
            form_oid = form_ref.get("FormOID", "")
            if form_oid:
                form_events.setdefault(form_oid, [])
                if event_oid not in form_events[form_oid]:
                    form_events[form_oid].append(event_oid)

    return {oid: {"name": form_names.get(oid, ""), "events": events}
            for oid, events in form_events.items()}


def _best_name_match(target_name, odm_index):
    """Fuzzy-match target_name against every ODM FormDef Name.
    Returns the matching ODM form_oid, or None if nothing clears the
    similarity threshold.
    """
    target_norm = _normalize_name(target_name)
    if not target_norm:
        return None
    best_oid, best_score = None, 0.0
    for oid, data in odm_index.items():
        name_norm = _normalize_name(data.get("name", ""))
        if not name_norm:
            continue
        score = SequenceMatcher(None, target_norm, name_norm).ratio()
        if score > best_score:
            best_score, best_oid = score, oid
    return best_oid if best_score >= _NAME_MATCH_THRESHOLD else None


def build_event_form_map(forms_json, odm_xml_bytes=None, manual_overrides=None):
    """Return ({form_key: [event_oid, ...]}, unplaced_flags).

    form_key is the filename without ".xlsx" (e.g. "AE", "MH") — stable
    regardless of which matching pass resolved it, so callers don't need
    to know which pass fired.

    manual_overrides values may be a single event_oid string or a list;
    both are normalized to a list internally.
    """
    manual_overrides = manual_overrides or {}
    odm_index = _odm_form_index(odm_xml_bytes) if odm_xml_bytes else {}

    mapping = {}
    unplaced = []

    for filename, form_data in forms_json.get("forms", {}).items():
        survey = form_data.get("survey") or []
        if not survey:
            continue  # not a form (e.g. a bundled checklist file)

        key = filename.replace(".xlsx", "")
        settings = form_data.get("settings") or {}
        form_title = settings.get("form_title") or key
        form_id    = settings.get("form_id") or key

        # 1. Manual override — always wins
        override = manual_overrides.get(key) or manual_overrides.get(filename)
        if override is not None:
            mapping[key] = override if isinstance(override, list) else [override]
            continue

        # 2. ODM match by name
        matched_oid = _best_name_match(form_title, odm_index)
        if matched_oid:
            mapping[key] = list(odm_index[matched_oid]["events"])
            continue

        # 3. ODM match by OID (exact, case-insensitive)
        oid_hit = next((oid for oid in odm_index
                         if oid.upper() in (form_id.upper(), key.upper())), None)
        if oid_hit:
            mapping[key] = list(odm_index[oid_hit]["events"])
            continue

        unplaced.append({
            "filename": filename,
            "form_oid_guess": key,
            "reason": ("no manual override, no ODM name/OID match — "
                       "the settings-heuristic fallback was removed "
                       "2026-08-19 for producing false-confident wrong "
                       "placements; needs a human decision"),
            "category": "oid_confirmation",
        })

    return mapping, unplaced
