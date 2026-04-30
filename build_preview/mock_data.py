"""
build_preview/mock_data.py — Phase 2 mock data generation

Generates a JavaScript mock data object from struct_json.
The JS object is embedded in every form HTML page so the interactive
simulator can resolve:
  - instance('clinicaldata') cross-form references
  - pulldata() CSV lookups (timepoints, lab ranges)
  - StudySubjectID, StudyOID, current event, etc.

The mock data uses realistic placeholder values. The user can override
any value in the browser (values entered in one form are saved to
localStorage and used by other forms in the same session).
"""
import json
import re


# ── Realistic CDASH field defaults ────────────────────────────────────────────
# Keyed by field name (case-insensitive). Used to pre-populate common fields
# so cross-form calculations produce meaningful output rather than blank.

_CDASH_DEFAULTS = {
    # Demographics
    'SUBJID':       'SUBJ-001',
    'SITEID':       'SITE-001',
    'AGE':          '52',
    'SEX':          'M',
    'RACE':         'WHITE',
    'ETHNIC':       'NOTHISP',
    'BRTHDAT':      '1972-03-15',   # gives age ~52
    'COUNTRY':      'USA',

    # Vital signs
    'SYSBP':        '122',
    'DIABP':        '78',
    'PULSE':        '72',
    'RESP':         '16',
    'TEMP':         '36.8',
    'WEIGHT':       '82',
    'HEIGHT':       '178',
    'BMI':          '25.9',

    # Lab common
    'CREAT':        '0.9',
    'CREATCL':      '85',       # creatinine clearance
    'WBC':          '6.5',
    'HGB':          '14.2',
    'PLAT':         '220',
    'NEUT':         '4.1',
    'LYM':          '1.8',
    'AST':          '28',
    'ALT':          '32',
    'BILI':         '0.7',
    'ALP':          '68',

    # PSA
    'PSA':          '5.2',

    # Enrollment
    'ARMCD':        'TRT',

    # Dates (ISO format)
    'VISDT':        '2026-04-30',
    'RFICDAT':      '2026-04-01',
    'ENRLDAT':      '2026-04-01',
}


def _field_default(name: str) -> str:
    """Return a mock value for a CDASH field name, or '' if unknown."""
    return _CDASH_DEFAULTS.get(name.upper(), '')


def generate_mock_js(struct_json: dict, study_spec: dict) -> str:
    """
    Generate a JavaScript object literal containing all mock data for the
    interactive simulator.

    Args:
        struct_json: the pipeline's in-memory study spec dict
        study_spec:  the parsed study_spec dict (events, form_to_events, etc.)

    Returns:
        JS string: `window.__mockDB = { ... };`
    """
    study_meta   = struct_json.get('study_meta') or {}
    study_id     = study_meta.get('study_id', 'STUDY')
    protocol_num = study_meta.get('protocol_number', study_id)

    # ── Timepoint lookup {event_oid → label} ─────────────────────────────
    tpt_rows = (struct_json.get('timepoint_csv') or {}).get('rows') or []
    timepoints = {}
    for row in tpt_rows:
        oid = (row.get('event') or '').strip()
        lbl = (row.get('timepoint') or '').strip()
        if oid:
            timepoints[oid] = lbl

    # Also include events from study_spec in case tpt_rows is empty
    for ev in (study_spec.get('events') or []):
        oid = ev.get('event', '')
        lbl = ev.get('timepoint', '')
        if oid and oid not in timepoints:
            timepoints[oid] = lbl

    # ── Lab ranges lookup {test_code → {lower, upper, unit}} ─────────────
    lab_rows = (struct_json.get('labranges_csv') or {}).get('rows') or []
    labranges = {}
    for row in lab_rows:
        code = (row.get('test_code') or row.get('LBTESTCD') or '').strip()
        if code:
            labranges[code] = {
                'lower': str(row.get('lower', '') or ''),
                'upper': str(row.get('upper', '') or ''),
                'unit':  str(row.get('unit', '') or ''),
            }

    # ── Per-form field defaults ───────────────────────────────────────────
    # Walk every form's survey to find field names → pre-populate defaults
    forms = struct_json.get('forms') or []
    form_defaults = {}   # {formOID: {fieldName: value}}

    for form in forms:
        fid    = (form.get('form_id') or '').strip()
        survey = form.get('survey') or []
        if not fid:
            continue
        defaults = {}
        for row in survey:
            name = (row.get('name') or '').strip()
            if not name or name.startswith('_') or row.get('type', '').startswith(('begin', 'end', 'calculate', 'note')):
                continue
            val = _field_default(name)
            if val:
                defaults[name] = val
                # Also key by FormOID.FieldName pattern (used in XPath lookups)
                defaults[f'{fid}.{name}'] = val
        form_defaults[fid] = defaults

    # ── Form→events mapping for navigator ────────────────────────────────
    form_to_events = study_spec.get('form_to_events') or {}
    form_inventory = study_spec.get('form_inventory') or {}

    # Build an event→forms index for the navigator
    event_to_forms = {}
    for form_id, events in form_to_events.items():
        for ev in events:
            event_to_forms.setdefault(ev, [])
            if form_id not in event_to_forms[ev]:
                event_to_forms[ev].append(form_id)

    # ── First event as the default navigation context ─────────────────────
    first_event = tpt_rows[0]['event'] if tpt_rows else (
        study_spec['events'][0]['event'] if study_spec.get('events') else 'SE_SCREENING'
    )

    # ── Assemble the JS object ────────────────────────────────────────────
    db = {
        'studyId':      study_id,
        'protocol':     protocol_num,
        'currentEvent': first_event,
        'subjectId':    'SUBJ-001',
        'siteId':       'SITE-001',
        'today':        '2026-04-30',   # fixed for reproducibility in previews
        'startDate':    '2026-04-01',
        'timepoints':   timepoints,
        'labranges':    labranges,
        'formDefaults': form_defaults,
        'eventToForms': event_to_forms,
        'formInventory': {
            fid: {'title': info.get('title', ''), 'category': info.get('category', '')}
            for fid, info in form_inventory.items()
        },
        # Values entered by user — starts empty, populated at runtime via localStorage
        'userValues':   {},
    }

    js_obj = json.dumps(db, indent=2, ensure_ascii=False)
    return f'window.__mockDB = {js_obj};\n'
