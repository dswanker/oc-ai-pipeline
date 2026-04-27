"""
build_xlsforms.py — OpenClinica XLSForm Generator
Generates one production-ready .xlsx file per CRF form from the EDC
structure specification. Output matches the OpenClinica blank template
exactly: settings, choices, survey sheets in that order.

Usage:
    from build_xlsforms import build_all_xlsforms, build_single_xlsform
    results = build_all_xlsforms(spec_data, output_dir)
"""

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import os, re, datetime

# ── Colours ───────────────────────────────────────────────────────────────────
DARK_BLUE  = "1B3A6B"
MID_BLUE   = "2E6DA4"
LIGHT_BLUE = "D6E4F0"
WHITE      = "FFFFFF"
GREY_LIGHT = "F5F5F5"
GREY_MID   = "CCCCCC"
AMBER      = "FFF3CD"

# ── Exact column order from OpenClinica template ──────────────────────────────
SURVEY_COLS = [
    "type", "name", "label", "bind::oc:itemgroup", "hint", "appearance",
    "bind::oc:briefdescription", "bind::oc:description", "relevant",
    "required", "required_message", "constraint", "constraint_message",
    "default", "calculation", "trigger", "readonly", "image",
    "repeat_count", "bind::oc:external"
]

CHOICES_COLS = ["list_name", "label", "name", "image"]

SETTINGS_COLS = [
    "form_title", "form_id", "version", "style",
    "crossform_references", "namespaces"
]

# Column widths for survey sheet
SURVEY_WIDTHS = {
    "type": 18, "name": 20, "label": 36, "bind::oc:itemgroup": 14,
    "hint": 24, "appearance": 18, "bind::oc:briefdescription": 20,
    "bind::oc:description": 22, "relevant": 32, "required": 10,
    "required_message": 26, "constraint": 32, "constraint_message": 28,
    "default": 18, "calculation": 32, "trigger": 14, "readonly": 9,
    "image": 12, "repeat_count": 12, "bind::oc:external": 16,
    "choice_filter": 26, "bind::oc:constraint-type": 20,
    "bind::oc:required-type": 18,
}

CHOICES_WIDTHS = {"list_name": 18, "label": 32, "name": 24, "image": 10}
SETTINGS_WIDTHS = {
    "form_title": 28, "form_id": 16, "version": 8, "style": 14,
    "crossform_references": 24, "namespaces": 55
}

# ── Style helpers ─────────────────────────────────────────────────────────────
def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)

def _font(bold=False, color="000000", size=9, name="Arial"):
    return Font(name=name, bold=bold, color=color, size=size)

def _border():
    s = Side(style="thin", color=GREY_MID)
    return Border(left=s, right=s, top=s, bottom=s)

def _align(wrap=True, h="left", v="top"):
    return Alignment(wrap_text=wrap, horizontal=h, vertical=v)

def _header_cell(cell, value):
    cell.value = value
    cell.font = _font(bold=True, color=WHITE, size=9)
    cell.fill = _fill(DARK_BLUE)
    cell.border = _border()
    cell.alignment = _align(h="center", v="center")

def _coerce_cell_value(value):
    """Coerce a value to something openpyxl can write to a cell.
    Lists/tuples become pipe-delimited strings; dicts become JSON;
    anything else is left as-is."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " | ".join(str(v) for v in value)
    if isinstance(value, dict):
        try:
            import json as _json
            return _json.dumps(value)
        except Exception:
            return str(value)
    return value


def _data_cell(cell, value, row_idx=0, flagged=False):
    cell.value = _coerce_cell_value(value)
    cell.font = _font(size=8)
    bg = AMBER if flagged else (GREY_LIGHT if row_idx % 2 == 0 else WHITE)
    cell.fill = _fill(bg)
    cell.border = _border()
    cell.alignment = _align()


# ── XLSX column name → XLSForm column name mapping ────────────────────────────
XLSX_TO_XLSFORM = {
    "bind__oc_itemgroup":        "bind::oc:itemgroup",
    "bind__oc_external":         "bind::oc:external",
    "bind__oc_briefdescription": "bind::oc:briefdescription",
    "bind__oc_description":      "bind::oc:description",
    "bind__oc_constraint_type":  "bind::oc:constraint-type",
    "bind__oc_required_type":    "bind::oc:required-type",
}
# Inverse: given the XLSForm column name, find the JSON key that carries
# its value. Used by _resolve_cell_value when the XLSForm name isn't
# present as a direct key in the row dict.
XLSFORM_TO_JSON = {v: k for k, v in XLSX_TO_XLSFORM.items()}


def _resolve_cell_value(row, col):
    """
    Given a row dict and an XLSForm column name, return the value.
    Tries both the XLSForm column name (e.g. 'bind::oc:itemgroup') AND its
    JSON underscore equivalent (e.g. 'bind__oc_itemgroup'), because
    Claude's extraction emits underscore keys (colons aren't friendly in
    JSON) while the XLSForm spec mandates colon keys.
    """
    # Try exact XLSForm name first
    if col in row and row[col] not in (None, ''):
        return row[col]
    # Fall back to JSON underscore variant
    json_key = XLSFORM_TO_JSON.get(col)
    if json_key and json_key in row:
        return row[json_key]
    return ''

# Columns from spec XLSX to strip (never include in output XLSForms)
STRIP_COLS = {
    "ACTION", "REVIEW_NOTES", "completion_status", "library_source",
    "pdf_original_label", "cdash_standard_name", "cdash_name_deviation",
    "cdash_name_confidence", "flag_reason", "choice_filter_meta",
    "source", "filter_column", "filter_value"
}

# ── Read spec XLSX ─────────────────────────────────────────────────────────────
def read_spec_xlsx(spec_path):
    """
    Read the EDC structure specification XLSX and return a dict of form data.
    Returns: {
        'study_meta': {...},
        'timepoint_csv': {'filename': ..., 'rows': [...]},
        'labranges_csv': {'filename': ..., 'columns': [...], 'rows': [...]},
        'forms': [{settings, choices, survey, form_id, form_title, ...}, ...]
    }
    """
    import openpyxl
    wb = openpyxl.load_workbook(spec_path, data_only=True)
    result = {'study_meta': {}, 'timepoint_csv': {}, 'labranges_csv': {}, 'forms': []}

    # ── INDEX sheet ────────────────────────────────────────────────────────
    if 'INDEX' in wb.sheetnames:
        ws = wb['INDEX']
        meta_map = {}
        for row in ws.iter_rows(min_row=2, max_row=10, values_only=True):
            if row[0] and row[1]:
                key = str(row[0]).strip()
                val = str(row[1]).strip() if row[1] else ''
                meta_map[key] = val
        result['study_meta'] = {
            'protocol_number': meta_map.get('Protocol Number', ''),
            'study_id':        meta_map.get('Study ID', ''),
            'input_mode':      meta_map.get('Input Mode', ''),
        }
        # Read form inventory from INDEX
        form_list = []
        reading_forms = False
        for row in ws.iter_rows(min_row=1, values_only=True):
            if row and row[0] == '#':
                reading_forms = True
                continue
            if reading_forms and row and row[0] and str(row[0]).strip().isdigit():
                form_list.append({
                    'num':        str(row[0]).strip(),
                    'form_id':    str(row[1]).strip() if row[1] else '',
                    'form_title': str(row[2]).strip() if row[2] else '',
                    'category':   str(row[3]).strip() if row[3] else '',
                    'cdash':      str(row[4]).strip() if row[4] else '',
                    'arm':        str(row[5]).strip() if row[5] else '',
                    'complexity': str(row[6]).strip() if row[6] else '',
                    'repeating':  str(row[7]).strip() if row[7] else 'No',
                    'epro':       str(row[8]).strip() if row[8] else 'No',
                    'reuse':      str(row[9]).strip() if row[9] else '0',
                    'survey_tab': str(row[10]).strip() if row[10] else '',
                })
        result['_form_list'] = form_list

    # ── TIMEPOINTS sheet ───────────────────────────────────────────────────
    if 'TIMEPOINTS' in wb.sheetnames:
        ws = wb['TIMEPOINTS']
        rows = []
        for i, row in enumerate(ws.iter_rows(min_row=3, values_only=True)):
            if row[0] and row[1]:
                action = str(row[2]).strip().upper() if len(row) > 2 and row[2] else ''
                if action != 'DELETE':
                    rows.append({'event': str(row[0]).strip(), 'timepoint': str(row[1]).strip()})
        study_id = result['study_meta'].get('study_id', 'study')
        result['timepoint_csv'] = {'filename': f"{study_id}_tpt.csv", 'rows': rows}

    # ── LAB_RANGES sheet ───────────────────────────────────────────────────
    if 'LAB_RANGES' in wb.sheetnames:
        ws = wb['LAB_RANGES']
        headers = []
        data_rows = []
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
            if i == 0:
                headers = [str(c).strip() for c in row if c is not None]
            else:
                if any(c is not None for c in row[:len(headers)]):
                    row_dict = {}
                    for j, h in enumerate(headers):
                        val = row[j] if j < len(row) else None
                        row_dict[h] = str(val).strip() if val is not None else ''
                    data_rows.append(row_dict)
        result['labranges_csv'] = {
            'filename': 'labranges.csv',
            'columns': headers,
            'rows': data_rows
        }

    # ── Per-form tabs ──────────────────────────────────────────────────────
    form_list = result.get('_form_list', [])
    sheet_names = wb.sheetnames

    for form_meta in form_list:
        form_id = form_meta['form_id']
        survey_tab = form_meta.get('survey_tab', f"{form_id}_survey")
        choices_tab = f"{form_id}_choices"
        settings_tab = f"{form_id}_settings"

        # Try to find tabs (tab names are truncated to 31 chars)
        s_tab = next((s for s in sheet_names if s == survey_tab or
                      s == survey_tab[:31]), None)
        c_tab = next((s for s in sheet_names if s == choices_tab or
                      s == choices_tab[:31]), None)
        st_tab = next((s for s in sheet_names if s == settings_tab or
                       s == settings_tab[:31]), None)

        if not s_tab:
            continue  # form tabs not found, skip

        # Read survey
        survey_rows = []
        extra_cols = []
        ws = wb[s_tab]
        header_row = None
        for i, row in enumerate(ws.iter_rows(min_row=3, max_row=3, values_only=True)):
            header_row = [str(c).strip() if c else '' for c in row]
        if header_row:
            for row in ws.iter_rows(min_row=4, values_only=True):
                if not any(c is not None for c in row):
                    continue
                action = str(row[0]).strip().upper() if row[0] else ''
                if action == 'DELETE':
                    continue
                row_dict = {}
                for j, h in enumerate(header_row):
                    if h in STRIP_COLS or not h:
                        continue
                    val = row[j] if j < len(row) else None
                    # Remap XLSX col names to XLSForm col names
                    xlsform_key = XLSX_TO_XLSFORM.get(h, h)
                    row_dict[xlsform_key] = str(val).strip() if val is not None else ''
                survey_rows.append(row_dict)
                # Track any extra columns (choice_filter, hard checks etc.)
                for k in row_dict:
                    if k not in SURVEY_COLS and k not in STRIP_COLS and k not in extra_cols:
                        extra_cols.append(k)

        # Read choices
        choices_rows = []
        if c_tab:
            ws = wb[c_tab]
            c_header = None
            for row in ws.iter_rows(min_row=2, max_row=2, values_only=True):
                c_header = [str(c).strip() if c else '' for c in row]
            if c_header:
                for row in ws.iter_rows(min_row=3, values_only=True):
                    if not any(c is not None for c in row):
                        continue
                    action = str(row[0]).strip().upper() if row[0] else ''
                    if action == 'DELETE':
                        continue
                    row_dict = {}
                    for j, h in enumerate(c_header):
                        if h in STRIP_COLS or not h:
                            continue
                        val = row[j] if j < len(row) else None
                        row_dict[h] = str(val).strip() if val is not None else ''
                    if row_dict.get('list_name') or row_dict.get('name'):
                        choices_rows.append(row_dict)

        # Read settings
        settings_dict = {
            'form_title': form_meta.get('form_title', ''),
            'form_id': form_id,
            'version': '1',
            'style': 'theme-grid',
            'crossform_references': '',
            'namespaces': 'oc="http://openclinica.org/xforms" , OpenClinica="http://openclinica.com/odm"'
        }
        if st_tab:
            ws = wb[st_tab]
            setting_keys = ['form_title', 'form_id', 'version', 'style',
                            'crossform_references', 'namespaces']
            for i, row in enumerate(ws.iter_rows(min_row=3, max_row=8, values_only=True)):
                if i < len(setting_keys) and row[1] is not None:
                    val = str(row[1]).strip()
                    if val:
                        settings_dict[setting_keys[i]] = val

        result['forms'].append({
            'form_id':    form_id,
            'form_title': form_meta.get('form_title', ''),
            'category':   form_meta.get('category', ''),
            'arm':        form_meta.get('arm', 'BOTH'),
            'complexity': form_meta.get('complexity', ''),
            'repeating':  form_meta.get('repeating', 'No'),
            'epro':       form_meta.get('epro', 'No'),
            'reuse':      form_meta.get('reuse', '0'),
            'settings':   settings_dict,
            'choices':    choices_rows,
            'survey':     survey_rows,
            'extra_cols': extra_cols,
            'source_tab': survey_tab,
        })

    return result


# ── Build a single XLSForm .xlsx ───────────────────────────────────────────────
def build_single_xlsform(form_data, output_path, build_log):
    """Build one XLSForm .xlsx file from form_data dict."""
    wb = Workbook()

    settings = form_data.get('settings', {}) or {}
    choices  = form_data.get('choices', [])
    survey   = form_data.get('survey', [])
    extra_c  = form_data.get('extra_cols', [])
    form_id  = form_data.get('form_id', 'FORM')

    # Apply OpenClinica XLSForm defaults for missing settings fields.
    # OpenClinica's XLSForm validator requires these — without them the
    # form won't load. Claude may omit them in the JSON extraction.
    OC_SETTINGS_DEFAULTS = {
        'form_title':           form_data.get('form_title', form_id),
        'form_id':               form_id,
        'version':               '1',
        'style':                 'theme-grid',
        'crossform_references': '',
        'namespaces':
            'oc="http://openclinica.org/xforms" , '
            'OpenClinica="http://openclinica.com/odm"',
    }
    for k, default in OC_SETTINGS_DEFAULTS.items():
        if not settings.get(k):
            settings[k] = default

    # ── Sheet 1: settings ──────────────────────────────────────────────────
    ws_set = wb.active
    ws_set.title = 'settings'

    for col_i, col in enumerate(SETTINGS_COLS, start=1):
        _header_cell(ws_set.cell(row=1, column=col_i), col)
        ws_set.column_dimensions[get_column_letter(col_i)].width = \
            SETTINGS_WIDTHS.get(col, 20)

    for col_i, col in enumerate(SETTINGS_COLS, start=1):
        val = settings.get(col, '')
        _data_cell(ws_set.cell(row=2, column=col_i), val)

    ws_set.row_dimensions[1].height = 16
    ws_set.row_dimensions[2].height = 14

    # ── Sheet 2: choices ───────────────────────────────────────────────────
    ws_ch = wb.create_sheet('choices')

    # Determine actual choice columns (base 4 + any filter columns)
    choice_extra_cols = []
    for ch in choices:
        for k in ch:
            if k not in CHOICES_COLS and k not in STRIP_COLS and k not in choice_extra_cols:
                choice_extra_cols.append(k)
    all_choice_cols = CHOICES_COLS + choice_extra_cols

    for col_i, col in enumerate(all_choice_cols, start=1):
        _header_cell(ws_ch.cell(row=1, column=col_i), col)
        ws_ch.column_dimensions[get_column_letter(col_i)].width = \
            CHOICES_WIDTHS.get(col, 18)

    for row_i, ch in enumerate(choices, start=2):
        for col_i, col in enumerate(all_choice_cols, start=1):
            val = ch.get(col, '')
            _data_cell(ws_ch.cell(row=row_i, column=col_i), val, row_i - 2)

    ws_ch.row_dimensions[1].height = 16
    ws_ch.freeze_panes = 'A2'

    # ── Sheet 3: survey ────────────────────────────────────────────────────
    ws_sv = wb.create_sheet('survey')

    # Determine actual survey columns
    # Additional columns present in this form's data
    additional_survey_cols = [c for c in extra_c
                               if c not in SURVEY_COLS and c not in STRIP_COLS]
    all_survey_cols = SURVEY_COLS + additional_survey_cols

    for col_i, col in enumerate(all_survey_cols, start=1):
        _header_cell(ws_sv.cell(row=1, column=col_i), col)
        ws_sv.column_dimensions[get_column_letter(col_i)].width = \
            SURVEY_WIDTHS.get(col, 16)

    placeholders_in_form = []
    for row_i, row in enumerate(survey, start=2):
        # Check if this row has any placeholder values
        has_placeholder = any(
            '[PLACEHOLDER' in str(v).upper() or '[UNIT]' in str(v).upper()
            for v in row.values() if v
        )
        if has_placeholder:
            placeholders_in_form.append(row.get('name', f'row_{row_i}'))

        for col_i, col in enumerate(all_survey_cols, start=1):
            val = _resolve_cell_value(row, col)
            _data_cell(ws_sv.cell(row=row_i, column=col_i), val,
                      row_i - 2, flagged=has_placeholder)

        ws_sv.row_dimensions[row_i].height = 13

    ws_sv.row_dimensions[1].height = 16
    ws_sv.freeze_panes = 'A2'

    # Log any placeholders found
    if placeholders_in_form:
        build_log['placeholder_applied'].append({
            'form_id': form_id,
            'fields': placeholders_in_form,
            'note': 'Best-guess values applied — require site-specific completion'
        })

    wb.save(output_path)
    return True


# ── Build all XLSForms ─────────────────────────────────────────────────────────
def build_all_xlsforms(spec_data, output_dir, build_log):
    """
    Build all XLSForm files from spec_data.
    Returns list of (form_id, output_path) tuples.

    Each successfully-built form is also validated using pyxform; results
    are accumulated in build_log['validation_results'] for downstream
    surfacing in BUILD_README and the Build Checklist PDF.
    """
    # Lazy import to keep build_xlsforms.py lean
    try:
        from validate_form import validate_xlsform
    except ImportError:
        validate_xlsform = None

    os.makedirs(output_dir, exist_ok=True)
    built = []
    build_log.setdefault('validation_results', [])

    # Track form_ids to handle variants (same form_id, different designs)
    seen_form_ids = {}

    for form in spec_data.get('forms', []):
        form_id = form.get('form_id', 'FORM')
        source_tab = form.get('source_tab', form_id)

        # Determine output filename
        # If source tab has a suffix (e.g., IE_TRT_survey), use that as filename
        tab_prefix = source_tab.replace('_survey', '').replace('_choices', '')

        if form_id in seen_form_ids:
            # Variant — use tab prefix as filename
            filename = f"{tab_prefix}.xlsx"
        else:
            seen_form_ids[form_id] = True
            # Check if there will be a variant (another form with same form_id)
            same_id_count = sum(1 for f in spec_data['forms'] if f.get('form_id') == form_id)
            if same_id_count > 1:
                filename = f"{tab_prefix}.xlsx"
            else:
                filename = f"{form_id}.xlsx"

        output_path = os.path.join(output_dir, filename)

        try:
            build_single_xlsform(form, output_path, build_log)
            built.append((tab_prefix, output_path))
            build_log['forms_built'].append(form_id)

            # Validate the form we just built
            if validate_xlsform is not None:
                v_result = validate_xlsform(output_path, form_id=tab_prefix)
                build_log['validation_results'].append(v_result)
        except Exception as e:
            build_log['build_errors'].append({
                'form_id': form_id,
                'error': str(e)
            })
            build_log['forms_skipped'].append(form_id)

    return built


# ── Write CSV files ────────────────────────────────────────────────────────────
def write_timepoint_csv(tpt_data, output_path, build_log):
    """Write the timepoint CSV file."""
    rows = tpt_data.get('rows', [])
    with open(output_path, 'w', newline='') as f:
        f.write('event,timepoint\r\n')
        for row in rows:
            event = row.get('event', '').replace(',', '')
            tpt = row.get('timepoint', '').replace(',', '')
            f.write(f'{event},{tpt}\r\n')
    build_log['build_warnings'] = build_log.get('build_warnings', [])
    if not rows:
        build_log['build_warnings'].append('Timepoint CSV has no rows — check TIMEPOINTS sheet')


def write_labranges_csv(lr_data, output_path, build_log):
    """Write the lab ranges CSV file."""
    cols = lr_data.get('columns', [])
    rows = lr_data.get('rows', [])
    placeholders = []

    with open(output_path, 'w', newline='') as f:
        f.write(','.join(cols) + '\r\n')
        for row in rows:
            values = []
            for c in cols:
                val = row.get(c, '')
                if '[PLACEHOLDER' in str(val).upper():
                    placeholders.append(f"{c}: {val}")
                    # Apply best-guess
                    if 'lower' in c.lower():
                        val = '0'
                    elif 'upper' in c.lower():
                        val = '999'
                    elif 'unit' in c.lower():
                        val = '[UNIT]'
                    elif 'lab_name' in c.lower():
                        val = '[LAB_NAME]'
                values.append(str(val).replace(',', ';'))
            f.write(','.join(values) + '\r\n')

    if placeholders:
        build_log['placeholder_applied'].append({
            'form_id': 'labranges.csv',
            'fields': placeholders[:5],
            'note': 'Lab ranges require site-specific values from each participating lab'
        })


if __name__ == '__main__':
    import sys, json
    if len(sys.argv) < 3:
        print("Usage: python build_xlsforms.py <spec_xlsx_path> <output_dir>")
        sys.exit(1)

    spec_path  = sys.argv[1]
    output_dir = sys.argv[2]

    build_log = {
        'forms_built': [], 'forms_skipped': [],
        'placeholder_applied': [], 'oid_placeholders': [],
        'qa_results': [], 'build_warnings': [], 'build_errors': []
    }

    print(f"Reading spec: {spec_path}")
    spec_data = read_spec_xlsx(spec_path)
    print(f"Found {len(spec_data['forms'])} forms")

    forms_dir = os.path.join(output_dir, 'forms')
    csv_dir   = os.path.join(output_dir, 'csv')
    os.makedirs(forms_dir, exist_ok=True)
    os.makedirs(csv_dir,   exist_ok=True)

    built = build_all_xlsforms(spec_data, forms_dir, build_log)
    print(f"Built {len(built)} XLSForms")

    study_id = spec_data['study_meta'].get('study_id', 'study')
    tpt_path = os.path.join(csv_dir, f"{study_id}_tpt.csv")
    write_timepoint_csv(spec_data['timepoint_csv'], tpt_path, build_log)
    print(f"Written: {tpt_path}")

    lr_path = os.path.join(csv_dir, 'labranges.csv')
    write_labranges_csv(spec_data['labranges_csv'], lr_path, build_log)
    print(f"Written: {lr_path}")

    print(f"\nBuild log: {json.dumps(build_log, indent=2)}")
