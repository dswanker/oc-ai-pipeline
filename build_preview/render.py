"""
Build Preview renderer for the OC AI Pipeline.

Public API:
    render_build_preview(study_spec_pdf_bytes, edc_zip_bytes) -> bytes

Pipeline:
    Study Spec PDF              EDC Build .zip
        │                              │
        ▼                              ▼
    parse_study_spec_bytes        unzip → forms/*.xlsx
        │                              │
        │                              ▼
        │                        sanitize PHANTOM rows
        │                              │
        │                              ▼
        │                        pyxform → XForm XML
        │                              │
        │                              ▼
        │                        enketo-transformer in Chromium
        │                        (one browser, reused for all forms)
        │                              │
        │                              ▼
        │                        Chromium → per-form PDF
        │                              │
        ▼                              ▼
    SoE landscape PDF       merged with per-form PDFs
                                       │
                                       ▼
                            final Build Preview PDF (bytes)

This module is fully deterministic — no Claude API calls, no network outside
local HTTP loopback for the transformer scaffold.
"""
import os
import sys
import io
import re
import time
import glob
import shutil
import tempfile
import threading
import http.server
import socketserver
import contextlib
from xml.etree import ElementTree as ET

import openpyxl
from pyxform.xls2xform import xls2xform_convert
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter, PdfReader

from .sanitize import sanitize_xlsform_bytes, get_form_settings_bytes
from .study_spec_parser import parse_study_spec_bytes


# Paths to vendored static assets (transformer.js + grid.css + scaffold.html)
VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')


# --------- itemset expansion -----------------------------------------------

def parse_choices_from_model(model_xml: str) -> dict:
    """
    Enketo's model has <instance id="LIST_NAME"><root><item><label>X</label><name>x</name>...
    Returns {'list_name': [{'label':..., 'name':...}, ...]}
    """
    wrapped = re.sub(r'\sxmlns="[^"]+"', '', f"<wrap>{model_xml}</wrap>")
    root = ET.fromstring(wrapped)
    out = {}
    for inst in root.iter('instance'):
        list_id = inst.get('id')
        if not list_id:
            continue
        choices = []
        for item in inst.iter('item'):
            label_el = item.find('label')
            name_el = item.find('name')
            if label_el is not None and name_el is not None:
                choices.append({
                    'label': (label_el.text or '').strip(),
                    'name':  (name_el.text or '').strip(),
                })
        if choices:
            out[list_id] = choices
    return out


def expand_itemsets(form_html: str, choices: dict) -> str:
    """Replace itemset templates with concrete <label> entries per choice."""
    pattern = re.compile(
        r'<label class="itemset-template"\s+data-items-path="instance\(\'([^\']+)\'\)/root/item">'
        r'(.*?)</label>'
        r'\s*<span class="itemset-labels"[^>]*>\s*</span>',
        re.DOTALL,
    )

    def replacer(m):
        list_id, inner = m.group(1), m.group(2)
        opts = choices.get(list_id, [])
        if not opts:
            return m.group(0)
        rendered = []
        for opt in opts:
            inp = re.sub(
                r'value="[^"]*"',
                f'value="{opt["name"]}"',
                inner,
            )
            rendered.append(
                f'<label class="">{inp}'
                f'<span lang="" class="option-label active">{opt["label"]}</span></label>'
            )
        return ''.join(rendered)

    return pattern.sub(replacer, form_html)


# --------- annotation pass -------------------------------------------------

def annotate_form_html(form_html: str) -> str:
    """
    Hoist data-relevant from inner inputs to the .or-branch container
    so the skip-logic chip renders once per question, not per option.
    Make hidden Calculations/Preloads panels visible.
    """
    out = form_html

    branch_re = re.compile(
        r'(<(?P<tag>fieldset|label|section)[^>]*class="[^"]*\bor-branch\b[^"]*"[^>]*?)>'
        r'(?P<inner>.*?)'
        r'</(?P=tag)>',
        re.DOTALL,
    )

    def hoist(m):
        head = m.group(1)
        inner = m.group('inner')
        tag = m.group('tag')
        if 'data-relevant=' not in head:
            rm = re.search(r'data-relevant="([^"]+)"', inner)
            if rm:
                head = head + f' data-relevant="{rm.group(1)}"'
        full = head + '>' + inner + f'</{tag}>'
        full = re.sub(
            r'(<(?:input|label)[^>]*?)\sdata-relevant="[^"]*"', r'\1', full,
        )
        return full

    out = branch_re.sub(hoist, out)
    out = out.replace(
        '<fieldset id="or-calculated-items" style="display:none;">',
        '<fieldset id="or-calculated-items" class="reveal-panel"><legend>Calculations</legend>',
    )
    out = out.replace(
        '<fieldset id="or-preload-items" style="display:none;">',
        '<fieldset id="or-preload-items" class="reveal-panel"><legend>Preloads</legend>',
    )
    return out


# --------- HTML skeletons --------------------------------------------------

ANNOT_CSS = r"""
@page { size: Letter; margin: 0.5in 0.5in 0.55in 0.5in;
        @bottom-left  { content: "OpenClinica Build Preview"; font-size: 8pt; color: #888; }
        @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; color: #888; } }
body { background: white; padding: 0; font-family: "Helvetica Neue", Arial, sans-serif; }
.or { background: white; }
form.or { page-break-after: always; }
form.or:last-child { page-break-after: auto; }

.or-required-msg, .or-constraint-msg {
  display: inline-block !important; font-size: 10px; color: #b54708;
  background: #fff7ed; border-left: 3px solid #fb923c; padding: 2px 8px;
  margin-top: 4px; border-radius: 0 3px 3px 0;
}
.or-required-msg::before { content: "Required: "; font-weight: 600; }
.or-constraint-msg::before { content: "Constraint: "; font-weight: 600; }

.or-branch, .or-branch.pre-init, .or-branch.disabled {
  display: block !important; opacity: 1 !important; height: auto !important;
}
.or-branch[data-relevant]:not([data-relevant=""])::before {
  content: "Show if: " attr(data-relevant);
  display: block; font-size: 9px; color: #92400e; background: #fef3c7;
  padding: 2px 8px; border-radius: 3px; margin: 0 0 4px 0;
  font-family: ui-monospace, Menlo, monospace; font-weight: 600;
}

.reveal-panel { display: block !important; margin-top: 16px; padding: 8px 12px;
                background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; }
.reveal-panel legend { font-weight: 600; color: #334155; font-size: 11px; padding: 0 6px; }
.reveal-panel .calculation { display: block; padding: 4px 0; border-bottom: 1px solid #e2e8f0; font-size: 10px; }
.reveal-panel .calculation:last-child { border-bottom: none; }
.reveal-panel .calculation input { display: none; }
.reveal-panel .calculation::after {
  content: attr(class) " | name=" attr(data-name) " calc=" attr(data-calculate) " preload=" attr(data-preload);
}
.reveal-panel label.calculation::before { content: "•  "; color: #64748b; }

.poc-header {
  background: linear-gradient(180deg, #2c8fc9 0%, #1f7aae 100%);
  color: white; padding: 14px 20px; border-radius: 4px;
  margin: 0 0 10px 0;
}
.poc-header h2 { margin: 0; font-size: 18px; font-weight: 600; }
.poc-header .meta { font-size: 11px; opacity: 0.95; margin-top: 4px;
                    font-family: ui-monospace, Menlo, monospace; }

.soe-page h1 { color: #1f7aae; font-size: 22pt; margin: 0 0 6px 0; }
.soe-page .sub { color: #475569; font-size: 11pt; margin-bottom: 12px; }
.matrix { border-collapse: collapse; width: 100%; font-size: 9pt; }
.matrix th, .matrix td { border: 1px solid #d8dde2; padding: 6px 8px; vertical-align: middle; }
.matrix thead th { background: #2c8fc9; color: white; font-weight: 600; text-align: left; }
.event-oid { font-family: ui-monospace, Menlo, monospace; font-size: 8pt; opacity: 0.85; }
.event-tp  { font-size: 9pt; margin-top: 2px; }
.matrix-cell { text-align: center; font-size: 13pt; color: #ddd; }
.matrix-cell.active { color: #1f7aae; background: #eef7ff; }
.muted { color: #64748b; font-size: 8pt; }
"""


def render_form_page_html(form_settings: dict, form_html: str) -> str:
    title = form_settings.get('form_title', '')
    fid   = form_settings.get('form_id', '')
    ver   = form_settings.get('version', '')
    return (
        f'<div class="poc-header"><h2>{title}</h2>'
        f'<div class="meta">Form OID: {fid} · Version: {ver}</div></div>'
        f'{form_html}'
    )


def render_soe_html(forms_meta: list, events: list, study_spec: dict) -> str:
    form_to_events = study_spec['form_to_events']
    form_titles = {oid: meta.get('title', '') for oid, meta in study_spec.get('form_inventory', {}).items()}
    md = study_spec.get('metadata', {})
    protocol_no = md.get('Protocol Number', '')
    title = md.get('Protocol Title', '')
    sub_line = (
        f'Protocol <b>{protocol_no}</b> · {len(forms_meta)} forms · '
        f'{len(events)} events &nbsp;·&nbsp; '
        f'<span class="muted">{title}</span>'
    )

    head = '<th class="form-col">Form</th>' + ''.join(
        f'<th><div class="event-oid">{e["event"]}</div>'
        f'<div class="event-tp">{e["timepoint"]}</div></th>'
        for e in events
    )

    rows = []
    for fr in forms_meta:
        fid = fr['fid']
        display_title = form_titles.get(fid) or fr.get('title', '')
        cells = [f'<td class="form-col"><b>{fid}</b><br>'
                 f'<span class="muted">{display_title}</span></td>']
        assigned = set(form_to_events.get(fid, []))
        for e in events:
            if e['event'] in assigned:
                cells.append('<td class="matrix-cell active">X</td>')
            else:
                cells.append('<td class="matrix-cell"></td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')

    return f'''
    <section class="soe-page">
      <h1>Schedule of Events</h1>
      <div class="sub">{sub_line}</div>
      <table class="matrix">
        <thead><tr>{head}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
      <div class="muted" style="margin-top:10px;">
        X = form is configured for this event &nbsp;·&nbsp;
        Source: Study Specification (Section 1)
      </div>
    </section>
    '''


# --------- HTTP scaffold for the Enketo transformer ------------------------

@contextlib.contextmanager
def transformer_scaffold_server(work_dir: str):
    """
    Serve work_dir over HTTP loopback. enketo-transformer's web bundle uses
    ES module imports which need an http(s) origin (file:// triggers CORS).
    """
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=work_dir, **kw)

        def log_message(self, *a, **kw):
            pass

    httpd = socketserver.TCPServer(('127.0.0.1', 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()


def prepare_scaffold(target_dir: str):
    """Copy vendored transformer.js, grid.css, scaffold.html into target_dir."""
    for fname in ('transformer.js', 'grid.css', 'scaffold.html'):
        src = os.path.join(VENDOR_DIR, fname)
        if not os.path.exists(src):
            raise FileNotFoundError(
                f'Vendored asset missing: {src}. '
                'Did the build_preview/vendor/ directory get deployed?'
            )
        shutil.copy(src, os.path.join(target_dir, fname))
    # The scaffold expects index.html
    shutil.copy(
        os.path.join(VENDOR_DIR, 'scaffold.html'),
        os.path.join(target_dir, 'index.html'),
    )


# --------- main entry ------------------------------------------------------

def _struct_json_to_study_spec(struct_json: dict) -> dict:
    """
    Convert the in-memory `struct_json` dict (the canonical study representation
    used throughout pipeline.py) into the same shape that `parse_study_spec_bytes`
    produces from a Study Spec PDF. This lets the renderer accept either input
    without conditionals throughout the rendering code.

    Source: struct_json contains:
        study_meta: {protocol_number, study_id, study_title, ...}
        timepoint_csv: {filename, rows: [{event, timepoint, ...}, ...]}
        forms: [{form_id, form_title, visits_assigned, ...}, ...]

    Output: same dict shape as parse_study_spec_bytes:
        metadata: {Protocol Number, Study ID, Protocol Title}
        events:   [{event, timepoint}]
        form_inventory: {form_id: {title, category}}
        form_to_events: {form_id: [SE_OIDs]}
    """
    study_meta = struct_json.get('study_meta') or {}
    metadata = {
        'Protocol Number': study_meta.get('protocol_number', ''),
        'Study ID':        study_meta.get('study_id', ''),
        'Protocol Title':  study_meta.get('study_title', ''),
    }

    # Events from timepoint_csv.rows. Deduplicate by event OID, preserving order.
    raw_rows = (struct_json.get('timepoint_csv') or {}).get('rows') or []
    seen = set()
    events = []
    for row in raw_rows:
        oid = (row.get('event') or '').strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        events.append({
            'event': oid,
            'timepoint': (row.get('timepoint') or '').strip(),
        })

    # Per-form metadata + form↔event mapping.
    forms = struct_json.get('forms') or []
    form_inventory = {}
    form_to_events = {}
    for f in forms:
        fid = (f.get('form_id') or '').strip()
        if not fid:
            continue
        # Strip F_ prefix for display so it matches XLSForm settings.form_id
        # (the build zip's filenames use the unprefixed form_id).
        display_fid = fid[2:] if fid.startswith('F_') else fid
        form_inventory[display_fid] = {
            'title':    f.get('form_title') or '',
            'category': f.get('form_category') or '',
        }
        visits = f.get('visits_assigned') or []
        # ALL_EVENTS sentinel → expand to every event in the schedule
        if any(v == 'ALL_EVENTS' for v in visits):
            visits = [e['event'] for e in events]
        form_to_events[display_fid] = sorted(
            set(v for v in visits if isinstance(v, str) and v.startswith('SE_'))
        )

    return {
        'metadata': metadata,
        'events': events,
        'form_inventory': form_inventory,
        'form_to_events': form_to_events,
    }


def render_build_preview_from_spec(struct_json: dict, edc_zip_bytes: bytes,
                                   protocol_id_for_filename: str = 'study') -> bytes:
    """
    Render a Build Preview PDF directly from the in-memory `struct_json` dict
    (the same one produced by call_claude → extract_json in pipeline.py) plus
    the EDC Build .zip bytes.

    This is the preferred entry point inside the pipeline because it:
      - skips PDF parsing entirely (no parser fragility)
      - works on the exact data the rest of the pipeline already has in memory
      - has lossless access to study_meta, events, forms

    Args:
        struct_json: the Study Spec dict (study_meta, timepoint_csv, forms).
        edc_zip_bytes: bytes of the EDC Build .zip.
        protocol_id_for_filename: short id used in temp paths; not in the PDF.

    Returns:
        bytes of the merged Build Preview PDF.
    """
    study_spec = _struct_json_to_study_spec(struct_json)
    return _render_with_study_spec(study_spec, edc_zip_bytes, protocol_id_for_filename)


def render_build_preview(study_spec_pdf_bytes: bytes, edc_zip_bytes: bytes,
                         protocol_id_for_filename: str = 'study') -> bytes:
    """
    Render a Build Preview PDF from a Study Specification PDF + EDC Build .zip.

    This is the standalone/testing entry point — it parses the PDF then calls
    the same rendering core. Inside pipeline.py prefer
    `render_build_preview_from_spec(struct_json, ...)` which avoids the PDF
    parsing step entirely.

    Args:
        study_spec_pdf_bytes: bytes of the Study Specification PDF
            (the protocol-analysis output).
        edc_zip_bytes: bytes of the EDC Build .zip.
        protocol_id_for_filename: short id used in temp paths; not in the PDF.

    Returns:
        bytes of the merged Build Preview PDF.
    """
    study_spec = parse_study_spec_bytes(study_spec_pdf_bytes)
    return _render_with_study_spec(study_spec, edc_zip_bytes, protocol_id_for_filename)


def _render_with_study_spec(study_spec: dict, edc_zip_bytes: bytes,
                            protocol_id_for_filename: str = 'study') -> bytes:
    """
    Internal: render the PDF given an already-parsed study_spec dict and the
    EDC zip. Both entry points (PDF-based and dict-based) flow through here.
    """
    t_start = time.time()

    work_root = tempfile.mkdtemp(prefix='build_preview_')
    try:
        # 2) Unzip EDC build to find form .xlsx files
        import zipfile
        zip_path = os.path.join(work_root, 'edc.zip')
        with open(zip_path, 'wb') as f:
            f.write(edc_zip_bytes)
        unzip_dir = os.path.join(work_root, 'unzipped')
        os.makedirs(unzip_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(unzip_dir)
        # Find forms — the zip contains a top-level folder with forms/ inside
        form_xlsx_paths = sorted(glob.glob(os.path.join(unzip_dir, '**', 'forms', '*.xlsx'),
                                           recursive=True))
        if not form_xlsx_paths:
            form_xlsx_paths = sorted(glob.glob(os.path.join(unzip_dir, '**', '*.xlsx'),
                                               recursive=True))
        if not form_xlsx_paths:
            raise RuntimeError(
                f'No XLSForm files found in EDC zip. '
                f'Extracted to {unzip_dir}'
            )
        print(f'[build_preview] Found {len(form_xlsx_paths)} forms in EDC zip', flush=True)

        # 3) Sanitize XLSForms (strip *_PHANTOM rows) and convert to XForm via pyxform
        sanitized_dir = os.path.join(work_root, 'sanitized')
        os.makedirs(sanitized_dir, exist_ok=True)
        xform_dir = os.path.join(work_root, 'xforms')
        os.makedirs(xform_dir, exist_ok=True)

        forms_meta = []
        for fp in form_xlsx_paths:
            fid = os.path.basename(fp).replace('.xlsx', '')
            with open(fp, 'rb') as f:
                src_bytes = f.read()
            cleaned = sanitize_xlsform_bytes(src_bytes)
            sanitized_path = os.path.join(sanitized_dir, f'{fid}.xlsx')
            with open(sanitized_path, 'wb') as f:
                f.write(cleaned)
            xform_path = os.path.join(xform_dir, f'{fid}.xml')
            try:
                xls2xform_convert(
                    xlsform_path=sanitized_path,
                    xform_path=xform_path,
                    validate=False,
                )
            except Exception as e:
                print(f'[build_preview] pyxform failed for {fid}: {e}', flush=True)
                continue
            settings = get_form_settings_bytes(src_bytes)
            forms_meta.append({
                'fid': fid,
                'title': settings.get('form_title', ''),
                'version': settings.get('version', ''),
                'xform_path': xform_path,
                'settings': settings,
            })
        forms_meta.sort(key=lambda m: m['fid'])

        # 4) Set up scaffold dir for Playwright
        scaffold_dir = os.path.join(work_root, 'scaffold')
        os.makedirs(scaffold_dir, exist_ok=True)
        prepare_scaffold(scaffold_dir)
        with open(os.path.join(scaffold_dir, 'grid.css')) as f:
            grid_css = f.read()

        # 5) Launch Chromium once, transform + render every form, then close.
        per_form_pdfs = []
        with transformer_scaffold_server(scaffold_dir) as port:
            with sync_playwright() as p:
                browser = p.chromium.launch()

                # Reusable page running the transformer module
                tp = browser.new_page()
                tp.goto(f'http://127.0.0.1:{port}/index.html')
                tp.wait_for_function('window.__ready === true', timeout=15000)

                # ---- SoE first (landscape) ----
                soe_inner = render_soe_html(forms_meta, study_spec['events'], study_spec)
                soe_html = (
                    f'<!doctype html><html><head><meta charset="utf-8"><style>{grid_css}\n{ANNOT_CSS}\n'
                    '@page { size: Letter landscape; margin: 0.35in 0.4in; }\n'
                    '.soe-page { page-break-after: auto !important; }\n'
                    '.soe-page h1 { font-size: 14pt !important; margin: 0 0 4px 0 !important; }\n'
                    '.soe-page .sub { font-size: 8.5pt !important; margin-bottom: 6px !important; }\n'
                    '.matrix { font-size: 7.5pt !important; }\n'
                    '.matrix th, .matrix td { font-size: 7.5pt !important; padding: 2px 5px !important; line-height: 1.15 !important; vertical-align: middle !important; }\n'
                    '.matrix .form-col { width: 90px !important; }\n'
                    '.matrix thead th .event-oid { font-size: 6.5pt !important; }\n'
                    '.matrix thead th .event-tp  { font-size: 7.5pt !important; line-height: 1.1 !important; }\n'
                    '.matrix .form-col b { font-size: 8.5pt !important; }\n'
                    '.matrix .form-col .muted { font-size: 7pt !important; line-height: 1.1 !important; }\n'
                    '.matrix .matrix-cell { font-size: 11pt !important; font-weight: 700; text-align: center; }\n'
                    f'</style></head><body>{soe_inner}</body></html>'
                )
                soe_pdf = os.path.join(work_root, '_soe.pdf')
                rp = browser.new_page()
                rp.set_content(soe_html, wait_until='load')
                rp.pdf(
                    path=soe_pdf, format='Letter', landscape=True,
                    margin={'top': '0.35in', 'bottom': '0.4in',
                            'left': '0.4in', 'right': '0.4in'},
                    print_background=True,
                )
                rp.close()
                per_form_pdfs.append(soe_pdf)

                # ---- Per-form PDFs ----
                for fm in forms_meta:
                    with open(fm['xform_path']) as f:
                        xform = f.read()
                    try:
                        result = tp.evaluate(
                            '(xf) => window.__transform({ xform: xf })', xform,
                        )
                    except Exception as e:
                        print(f'[build_preview] transform failed for {fm["fid"]}: {e}',
                              flush=True)
                        continue

                    choices = parse_choices_from_model(result['model'])
                    form_html = expand_itemsets(result['form'], choices)
                    form_html = annotate_form_html(form_html)

                    page_html = (
                        f'<!doctype html><html><head><meta charset="utf-8">'
                        f'<style>{grid_css}\n{ANNOT_CSS}</style></head><body>'
                        f'{render_form_page_html(fm["settings"], form_html)}'
                        f'</body></html>'
                    )
                    out_pdf = os.path.join(work_root, f'_form_{fm["fid"]}.pdf')
                    rp = browser.new_page()
                    rp.set_content(page_html, wait_until='load')
                    rp.pdf(
                        path=out_pdf, format='Letter',
                        margin={'top': '0.5in', 'bottom': '0.55in',
                                'left': '0.5in', 'right': '0.5in'},
                        print_background=True,
                    )
                    rp.close()
                    per_form_pdfs.append(out_pdf)

                browser.close()

        # 6) Merge per-form PDFs, dropping any blank trailing pages from each
        writer = PdfWriter()
        for pdf in per_form_pdfs:
            for page in PdfReader(pdf).pages:
                txt = (page.extract_text() or '').strip()
                if len(txt) < 80 and 'Page' in txt and 'of' in txt:
                    continue
                writer.add_page(page)

        out = io.BytesIO()
        writer.write(out)
        result_bytes = out.getvalue()

        elapsed = time.time() - t_start
        print(f'[build_preview] Rendered {len(forms_meta)} forms + SoE in {elapsed:.1f}s '
              f'-> {len(result_bytes):,} bytes', flush=True)
        return result_bytes

    finally:
        shutil.rmtree(work_root, ignore_errors=True)
