"""
pipeline.py — oc-ai-pipeline orchestration

Architecture
────────────
  call_claude()  → JSON text  (analysis, fast, no code execution)
  run_*_*()      → real binary files via LOCAL scripts in skills/*/scripts/

File generation happens via local python imports from skills/*/scripts/
rather than the Anthropic Skills API sandbox (which previously failed to
reliably return file_ids for file retrieval). Every chain imports its
skill's scripts directly and runs them in a thread pool executor.

Flow (fresh run):
  1. call_claude           : protocol PDF  → Study Spec JSON
  2. run_study_spec_files  : JSON          → Study Spec PDF + XLSX
  3. call_claude           : JSON          → Protocol Summary JSON
  4. run_protocol_summary_pdf : JSON       → Protocol Summary PDF
  5. run_pricing_quote     : JSON          → Quote PDFs + XLSXs
  6. run_edc_build         : JSON          → EDC Build ZIP
  7. run_dvs_xlsx          : JSON + ZIP    → DVS XLSX
  8. create_oc_study       : JSON          → OC study + design board

Human-in-the-loop paths:
  A. Edited Study Spec XLSX uploaded  → skip steps 1-2, run 3-8
  B. Edited Build ZIP uploaded        → skip steps 1-6, run 7 only
  C. Edited DVS uploaded              → translate changes → rebuild ZIP + DVS
  D. Edited Quote XLSX uploaded       → DEPRECATED, logs a message + skips
  E. Edited SOE CSV uploaded          → update SOE in OpenClinica (not impl)
"""

import asyncio, io, json, os, sys, tempfile, traceback, zipfile, datetime as _dt
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from monday_client import (get_item, download_file, upload_file, set_status,
                            append_log, set_text, set_link, download_column_file,
                            list_column_filenames, COL, BOARD_ID)
from claude_client  import call_claude, extract_json
from migration_pipeline import run_migration as run_edc_migration
from trainer_integration import (
    run_protocol_analysis_quick,
    retrieve_examples,
    create_pending_row,
    format_examples_block,
    trainer_enabled,
)
from prompts        import (
    EDC_STRUCTURE_PROMPT, PRICING_SUMMARY_PROMPT,
    DVS_TRANSLATE_PROMPT,
)

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills')

STATUS = {
    "not_started":            "Not Started",
    "analysis_running":       "Analysis Running",
    "analysis_complete":      "Analysis Complete",
    "build_pricing_running":  "Build + Pricing Running",
    "build_complete":         "Build Complete",
    "pricing_complete":       "Pricing Complete",
    "dvs_running":            "DVS Running",
    "dvs_complete":           "DVS Complete — Awaiting Review",
    "creating_oc_study":      "Creating OC Study",
    "all_complete":           "All Complete",
    "build_preview_running":  "Build Preview Running",
    "failed":                 "Failed",
}
# Trainer retrieval — number of similar past pairs to request.
# Phase 1 starts at 3; raise after observing prompt length & quality.
TRAINER_K = 3

# ── Helpers ───────────────────────────────────────────────────────────────────

def _find(files: dict, *patterns) -> bytes | None:
    """Return bytes for first filename ending with any pattern."""
    for pat in patterns:
        for name, data in files.items():
            if name.lower().endswith(pat.lower()):
                return data
    return None


async def _noop_bytes():
    """Awaitable that returns None — used as a placeholder in asyncio.gather."""
    return None


def _vendor_slug_from_display_name(display_name):
    """Translate a monday `source_edc_system` display name (e.g. "REDCap")
    to a conventions/vendors/ slug (e.g. "redcap") via the existing
    VENDOR_CONVENTION_FILES dict in migration/odm_to_spec.py.

    Returns None for unknown / empty input — caller treats that as
    non-migration build (cascade vendor bucket is skipped).

    odm_to_spec.py lives in migration/ but uses bare-name imports
    (`from odm_reader import ...`), so we add migration/ to sys.path
    on first use — same pattern as migration_pipeline.py.

    >>> _vendor_slug_from_display_name("REDCap")
    'redcap'
    >>> _vendor_slug_from_display_name("Castor EDC")
    'castor'
    >>> _vendor_slug_from_display_name("UnknownEDC") is None
    True
    >>> _vendor_slug_from_display_name("") is None
    True
    >>> _vendor_slug_from_display_name(None) is None
    True
    """
    if not display_name:
        return None
    import os as _os, sys as _sys
    _mig_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "migration")
    if _mig_dir not in _sys.path:
        _sys.path.insert(0, _mig_dir)
    try:
        from odm_to_spec import VENDOR_CONVENTION_FILES
    except ImportError:
        return None
    filename = VENDOR_CONVENTION_FILES.get(display_name)
    if not filename:
        return None
    return filename[:-3] if filename.endswith(".md") else filename


# ── Customer Convention Questions (CQ) ─────────────────────────────────────────
# Customers can supply convention preferences via columns on the AI Hub board
# whose titles start with "CQ " (preferred, full question becomes the key) or
# "CQ_" (legacy underscore form). The pipeline reads ALL such columns
# dynamically — adding a new question to the board requires zero code changes,
# the next pipeline run picks it up automatically.
#
# These customer answers are injected into the EDC structure prompt as part of
# extra_parts, so Claude sees them when generating the Study Spec JSON. The
# Study Spec then flows through the rest of the pipeline (build, DVS, etc.),
# so conventions injected here propagate to all downstream stages.

CQ_PREFIX_NEW    = "CQ "    # human-readable: "CQ How do you want X?"
CQ_PREFIX_LEGACY = "CQ_"    # short identifier: "CQ_How_Do_You_Want_X"


def _strip_cq_prefix(title: str) -> str:
    """Remove the CQ prefix and normalize the question text for use as a key."""
    if title.startswith(CQ_PREFIX_NEW):
        return title[len(CQ_PREFIX_NEW):].strip()
    if title.startswith(CQ_PREFIX_LEGACY):
        return title[len(CQ_PREFIX_LEGACY):].replace("_", " ").strip()
    return title


def _extract_customer_conventions(cols: dict) -> dict:
    """
    Extract customer convention answers from the cols dict (column_id -> column).
    Recognizes columns whose title starts with 'CQ ' or 'CQ_'. Empty answers
    are skipped. Returns dict[question_text -> answer_text].
    """
    out = {}
    for col_id, col in cols.items():
        title = (col.get("title") or "").strip()
        is_cq = (title.startswith(CQ_PREFIX_NEW)
                 or title.startswith(CQ_PREFIX_LEGACY)
                 or col_id.startswith(CQ_PREFIX_LEGACY))
        if not is_cq:
            continue
        answer = (col.get("text") or "").strip()
        if not answer:
            continue
        out[_strip_cq_prefix(title)] = answer
    return out


def _build_customer_conventions_block(conventions: dict) -> str:
    """Format customer conventions as a prompt-ready text block. Empty when no answers."""
    if not conventions:
        return ""
    lines = [
        "Customer Convention Preferences (apply these when generating the Study Spec):",
    ]
    for question, answer in conventions.items():
        lines.append(f"  - Q: {question}")
        lines.append(f"    A: {answer}")
    return "\n".join(lines)


def _xl_header_row(ws, headers, bg="1B3A6B", fg="FFFFFF"):
    fill = PatternFill("solid", fgColor=bg)
    font = Font(name="Arial", bold=True, color=fg, size=10)
    aln  = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font, cell.fill, cell.alignment = font, fill, aln

def _xl_data_row(ws, values, bold=False):
    ws.append(values)
    for cell in ws[ws.max_row]:
        cell.font = Font(name="Arial", bold=bold, size=9)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

def _xl_col_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


# ── XLSForm ZIP builder (local, from JSON) ────────────────────────────────────

def _xlsform_zip(build_json):
    """Convert EDC Build JSON into a ZIP of XLSForm xlsx files. Returns bytes.

    Uses build_single_xlsform from the edc-builder skill scripts so every
    output form carries the OC form template (reference tabs + dropdown).
    Falls back to building from scratch if the skill scripts are unavailable.
    """
    # Standard survey columns — used to detect extra_cols from JSON data
    _SURVEY_COLS = {
        "type", "name", "label", "bind::oc:itemgroup", "hint", "appearance",
        "bind::oc:briefdescription", "bind::oc:description", "relevant",
        "required", "required_message", "constraint", "constraint_message",
        "default", "calculation", "trigger", "readonly", "image",
        "repeat_count", "bind::oc:external"
    }

    # Try to import the edc-builder script so we get the template + dropdown
    _add_scripts("edc-builder")
    try:
        from build_xlsforms import build_single_xlsform as _build_single
        _use_skill = True
    except ImportError:
        _build_single = None
        _use_skill = False
        print("_xlsform_zip: build_xlsforms not available — using scratch builder",
              flush=True)

    forms   = build_json.get("forms", {})
    zip_buf = io.BytesIO()

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, form_data in forms.items():

            # ── CSV pass-through (timepoint, lab ranges, checklist) ────────
            if filename.endswith('.csv'):
                if isinstance(form_data, str):
                    zf.writestr(filename, form_data)
                elif isinstance(form_data, list):
                    import csv as _csv
                    cbuf = io.StringIO()
                    if form_data:
                        writer = _csv.DictWriter(cbuf, fieldnames=form_data[0].keys())
                        writer.writeheader()
                        writer.writerows(form_data)
                    zf.writestr(filename, cbuf.getvalue())
                continue

            survey   = form_data.get("survey", [])
            choices  = form_data.get("choices", [])
            settings = form_data.get("settings", {}) or {}

            # Derive form_id from settings or strip extension from filename
            form_id = (settings.get("form_id")
                       or os.path.splitext(filename)[0])

            if _use_skill:
                # ── Template-based path via build_single_xlsform ──────────
                # Detect any extra columns beyond the standard 20
                extra_cols = []
                for row in survey:
                    for k in row:
                        if k not in _SURVEY_COLS and k not in extra_cols:
                            extra_cols.append(k)

                skill_form_data = {
                    "form_id":    form_id,
                    "form_title": settings.get("form_title", form_id),
                    "settings":   settings,
                    "survey":     survey,
                    "choices":    choices,
                    "extra_cols": extra_cols,
                }
                build_log = {"placeholder_applied": [], "build_errors": []}
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
                    tmp_path = tf.name
                try:
                    _build_single(skill_form_data, tmp_path, build_log)
                    with open(tmp_path, "rb") as f:
                        zf.writestr(filename, f.read())
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

            else:
                # ── Scratch fallback (no template, no reference tabs) ──────
                wb   = Workbook()
                ws_s = wb.active
                ws_s.title = "survey"
                if survey:
                    hdrs = list(survey[0].keys())
                    _xl_header_row(ws_s, hdrs)
                    for row in survey:
                        _xl_data_row(ws_s, [row.get(h, "") for h in hdrs])

                ws_c = wb.create_sheet("choices")
                if choices:
                    hdrs = list(choices[0].keys())
                    _xl_header_row(ws_c, hdrs)
                    for row in choices:
                        _xl_data_row(ws_c, [row.get(h, "") for h in hdrs])

                ws_t = wb.create_sheet("settings")
                if settings:
                    _xl_header_row(ws_t, list(settings.keys()))
                    _xl_data_row(ws_t, list(settings.values()))

                xbuf = io.BytesIO()
                wb.save(xbuf)
                zf.writestr(filename, xbuf.getvalue())

        checklist = build_json.get("study_checklist")
        if checklist and isinstance(checklist, list) and checklist:
            import csv as _csv
            cbuf = io.StringIO()
            writer = _csv.DictWriter(cbuf, fieldnames=checklist[0].keys())
            writer.writeheader()
            writer.writerows(checklist)
            zf.writestr("study_checklist.csv", cbuf.getvalue())

    zip_buf.seek(0)
    return zip_buf.getvalue()


def _convert_to_pdf(file_bytes: bytes, filename: str) -> bytes:
    """
    Convert a document file to PDF bytes for Claude ingestion.
    Supports: .docx, .doc (via LibreOffice)
    Returns PDF bytes, or empty bytes if conversion fails.
    """
    import subprocess, tempfile, shutil, os
    ext = (filename.rsplit('.', 1)[-1] if '.' in filename else '').lower()
    if ext not in ('docx', 'doc'):
        return b''
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = os.path.join(tmpdir, f'protocol.{ext}')
            with open(src_path, 'wb') as f:
                f.write(file_bytes)
            result = subprocess.run(
                ['libreoffice', '--headless', '--convert-to', 'pdf',
                 '--outdir', tmpdir, src_path],
                capture_output=True, timeout=90, text=True
            )
            pdf_path = os.path.join(tmpdir, f'protocol.pdf')
            if os.path.exists(pdf_path):
                with open(pdf_path, 'rb') as f:
                    pdf = f.read()
                print(f"Converted {filename} → PDF ({len(pdf):,} bytes)", flush=True)
                return pdf
            else:
                print(f"LibreOffice conversion failed for {filename}: "
                      f"{result.stderr[:200]}", flush=True)
                return b''
    except Exception as e:
        print(f"_convert_to_pdf error: {e}", flush=True)
        return b''


def _extract_docx_as_text(file_bytes: bytes) -> str:
    """
    Extract plain text from a .docx file using python-docx.
    Fallback when LibreOffice is unavailable.
    """
    try:
        import io
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        print(f"_extract_docx_as_text error: {e}", flush=True)
        return ''


def _google_doc_export_url(link: str) -> str:
    """
    Convert a Google Docs / Drive share link into a PDF-export URL.

    Handles the three common share-link shapes:
      - https://docs.google.com/document/d/<ID>/edit?usp=sharing
      - https://docs.google.com/document/d/<ID>/view
      - https://drive.google.com/file/d/<ID>/view?usp=sharing

    Returns the public ``?export=pdf`` URL when the document ID can be
    extracted; otherwise returns "" so callers can skip cleanly. Only
    works for documents whose share setting is "Anyone with the link"
    — protected docs will 401/403 and the caller treats that as empty.
    """
    import re as _re
    m = _re.search(r"/d/([A-Za-z0-9_-]{20,})", link or "")
    if not m:
        return ""
    doc_id = m.group(1)
    if "drive.google.com" in link:
        return f"https://drive.google.com/uc?export=download&id={doc_id}"
    return f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"


def _detect_oc_standard_type(file_bytes):
    """
    Detect whether the file in the oc_standard column is an ODM XML or a
    ZIP of XLSForms. Returns 'ODM_XML', 'XLSFORM_ZIP', or 'UNKNOWN'.
    """
    if not file_bytes:
        return 'UNKNOWN'
    # ZIP magic bytes: PK\x03\x04
    if file_bytes[:4] == b'PK\x03\x04':
        return 'XLSFORM_ZIP'
    # XML: starts with BOM or <?xml or <ODM
    head = file_bytes[:200].lstrip()
    if (head.startswith(b'<?xml') or head.startswith(b'<ODM') or
            head.startswith(b'\xef\xbb\xbf<?xml')):
        return 'ODM_XML'
    # Try decoding as text and check for ODM signature
    try:
        text = file_bytes[:500].decode('utf-8', errors='ignore')
        if '<ODM' in text or 'xmlns:odm' in text.lower():
            return 'ODM_XML'
    except Exception:
        pass
    return 'UNKNOWN'


def _read_zip_xlsforms(zip_bytes):
    """Read a ZIP of XLSForm xlsx files. Returns forms dict."""
    import openpyxl
    forms = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith('.xlsx') or name.startswith('__'):
                continue
            src = openpyxl.load_workbook(io.BytesIO(zf.read(name)))
            form_data = {}
            for sheet_name in ['survey', 'choices', 'settings']:
                if sheet_name in src.sheetnames:
                    ws   = src[sheet_name]
                    rows = list(ws.values)
                    if not rows:
                        form_data[sheet_name] = [] if sheet_name != 'settings' else {}
                        continue
                    headers = [str(h).strip() if h else '' for h in rows[0]]
                    if sheet_name == 'settings':
                        form_data[sheet_name] = dict(zip(headers, [
                            str(v) if v is not None else '' for v in rows[1]
                        ])) if len(rows) > 1 else {}
                    else:
                        form_data[sheet_name] = [
                            {h: (str(v) if v is not None else '')
                             for h, v in zip(headers, row)}
                            for row in rows[1:]
                            if any(v is not None for v in row)
                        ]
                else:
                    form_data[sheet_name] = [] if sheet_name != 'settings' else {}
            forms[os.path.basename(name)] = form_data
    print(f"Read {len(forms)} XLSForm(s) from ZIP", flush=True)
    return {"forms": forms}


def _dvs_xlsx_to_text(dvs_bytes):
    """Extract DVS XLSX as structured text for Claude to read."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(dvs_bytes))
    lines = []
    for sheet_name in wb.sheetnames:
        ws   = wb[sheet_name]
        rows = list(ws.values)
        if not rows:
            continue
        lines.append(f"\n=== Sheet: {sheet_name} ===")
        headers = [str(h) if h else '' for h in rows[0]]
        lines.append('\t'.join(headers))
        for row in rows[1:]:
            if any(v is not None for v in row):
                lines.append('\t'.join(str(v) if v is not None else '' for v in row))
    return '\n'.join(lines)


# ── Pricing model — run scripts locally ───────────────────────────────────────

def _add_scripts(skill_name):
    path = os.path.join(SKILLS_DIR, skill_name, "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)


def run_pricing_quote(pricing_summary_dict,
                      additional_sub_disc=0.0, additional_svc_disc=0.0,
                      edc_structure=None):
    """Run pricing-quote scripts locally. Returns dict of file bytes.

    Args:
      pricing_summary_dict: the Protocol Summary JSON (primary pricing input)
      additional_sub_disc:  user-entered subscription discount (decimal,
                            e.g., 0.10 for 10% off). Applied to all module
                            totals (monthly_fee × duration) after volume +
                            platform discounts.
      additional_svc_disc:  user-entered services discount (decimal). Applied
                            to the build_fee (ps_hours + pm_hours + contingency).
      edc_structure:        Study Spec JSON for enriching flag comments.
    """
    _add_scripts("pricing-quote")
    from pricing_engine      import calculate_quote
    from generate_quote_pdf  import build_quote_pdfs
    from generate_quote_xlsx import build_quote_xlsx

    quote    = calculate_quote(pricing_summary_dict,
                               additional_sub_disc=additional_sub_disc,
                               additional_svc_disc=additional_svc_disc,
                               edc_structure=edc_structure)
    protocol = quote["study_meta"].get("protocol_number", "STUDY")

    with tempfile.TemporaryDirectory() as tmp:
        paths = {
            "internal_pdf":  os.path.join(tmp, f"{protocol}_Quote_Internal.pdf"),
            "client_pdf":    os.path.join(tmp, f"{protocol}_Quote_Client.pdf"),
            "internal_xlsx": os.path.join(tmp, f"{protocol}_Quote_Internal.xlsx"),
            "client_xlsx":   os.path.join(tmp, f"{protocol}_Quote_Client.xlsx"),
        }
        build_quote_pdfs(quote, paths["internal_pdf"], paths["client_pdf"])
        build_quote_xlsx(quote, paths["internal_xlsx"], paths["client_xlsx"])
        return {k: open(v, "rb").read() for k, v in paths.items()}


# ── Local runners for Study Spec, Protocol Summary, EDC Build, DVS ──────────
# These mirror test_skills_locally.py — imported directly from the skills
# folder's scripts/ directory and run in a thread pool executor.

def run_study_spec_files(struct_json, customer_subdomain="", migration_source=None):
    """Generate Study Spec PDF + XLSX locally. Returns {'pdf': bytes, 'xlsx': bytes}."""
    _add_scripts("protocol-analysis")
    from generate_study_spec_pdf  import build_edc_pdf
    from generate_study_spec_xlsx import build_edc_xlsx

    # Compute conventions_applied metrics from the forms data per
    try:
        from conventions_engine import apply_conventions
        study_meta = struct_json.get("study_meta", {})
        study_id = study_meta.get("protocol_number", "UNKNOWN")
        
        apply_conventions(
            spec=struct_json,
            study_id=study_id,
            customer_subdomain=customer_subdomain,
            migration_source=migration_source,
        )
        
        applied_list = struct_json.get("study_meta", {}).get("conventions_engine_applied", [])
        print(f"conventions_engine: applied {len(applied_list)} conventions", flush=True)
    except Exception as ex:
        print(f"conventions_engine FAILED — continuing without conventions: {ex}", flush=True)
        import traceback
        traceback.print_exc()
    protocol = (struct_json.get("study_meta", {}).get("protocol_number")
                or "STUDY")
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path  = os.path.join(tmp, f"{protocol}_Study_Specification.pdf")
        xlsx_path = os.path.join(tmp, f"{protocol}_Study_Specification.xlsx")
        build_edc_pdf(struct_json, pdf_path)
        build_edc_xlsx(struct_json, xlsx_path)
        return {
            "pdf":  open(pdf_path, "rb").read(),
            "xlsx": open(xlsx_path, "rb").read(),
        }


def run_protocol_summary_pdf(pricing_json, struct_json=None):
    """Generate Protocol Summary PDF locally. Returns bytes.

    If struct_json is provided, the PDF includes a Study Event Schedule
    sub-table in Section 3 (Timepoint Label | Arm | Forms Assigned —
    Event OID omitted for client-facing simplicity).
    """
    _add_scripts("protocol-analysis")
    from generate_protocol_summary_pdf import build_pricing_pdf

    protocol = (pricing_json.get("study_meta", {}).get("protocol_number")
                or "STUDY")
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, f"{protocol}_Protocol_Summary.pdf")
        build_pricing_pdf(pricing_json, pdf_path, struct_json=struct_json)
        return open(pdf_path, "rb").read()


def run_edc_build(struct_json):
    """Build EDC ZIP locally. Returns (zip_bytes, build_log, forms_json)."""
    _add_scripts("edc-builder")
    from build_xlsforms  import build_all_xlsforms, write_timepoint_csv, write_labranges_csv
    from build_checklist import build_checklist_pdf, build_checklist_xlsx
    from build_package   import build_package

    protocol = (struct_json.get("study_meta", {}).get("protocol_number")
                or "STUDY")
    build_log = {
        'forms_built':         [],
        'forms_skipped':       [],
        'build_errors':        [],
        'build_warnings':      [],
        'placeholder_applied': [],
        'oid_placeholders':    [],
    }

    with tempfile.TemporaryDirectory() as tmp:
        forms_dir     = os.path.join(tmp, 'forms')
        csv_dir       = os.path.join(tmp, 'csv')
        checklist_dir = os.path.join(tmp, 'checklist')
        package_dir   = os.path.join(tmp, 'package')
        for d in (forms_dir, csv_dir, checklist_dir, package_dir):
            os.makedirs(d, exist_ok=True)

        build_all_xlsforms(struct_json, forms_dir, build_log)
        write_timepoint_csv(struct_json.get('timepoint_csv', {}),
                            os.path.join(csv_dir, f'{protocol}_tpt.csv'),
                            build_log)
        write_labranges_csv(struct_json.get('labranges_csv', {}),
                            os.path.join(csv_dir, f'{protocol}_labranges.csv'),
                            build_log)
        build_checklist_pdf(struct_json, build_log,
                            os.path.join(checklist_dir,
                                         f'{protocol}_Build_Checklist.pdf'))
        build_checklist_xlsx(struct_json, build_log,
                             os.path.join(checklist_dir,
                                          f'{protocol}_Build_Checklist.xlsx'))

        zip_path = build_package(struct_json, build_log,
                                 forms_dir, csv_dir, checklist_dir, package_dir)
        zip_bytes = open(zip_path, "rb").read()

        # Also build the forms_json view used by DVS (survey rows per form)
        forms_json = {"forms": {}}
        for fname in sorted(os.listdir(forms_dir)):
            if fname.lower().endswith('.xlsx'):
                import openpyxl
                wb = openpyxl.load_workbook(os.path.join(forms_dir, fname),
                                            read_only=True, data_only=True)
                survey_rows = []
                if 'survey' in wb.sheetnames:
                    ws = wb['survey']
                    rows = list(ws.iter_rows(values_only=True))
                    if rows:
                        headers = [str(h or '').strip() for h in rows[0]]
                        for r in rows[1:]:
                            row_dict = {headers[i]: r[i] for i in range(len(headers))
                                        if i < len(r) and r[i] is not None}
                            if row_dict:
                                survey_rows.append(row_dict)
                forms_json["forms"][fname] = {"survey": survey_rows}
        return zip_bytes, build_log, forms_json


def run_dvs_xlsx(struct_json, forms_json):
    """Build DVS XLSX locally. Returns bytes or None if builder not available.

    The DVS is a MECHANICAL MIRROR of what's actually in the XLSForms —
    not an invention of new checks from the protocol. Every constraint,
    required flag, calculation, and relevant expression in the forms
    produces corresponding rows in DVS_OC4, Protocol_Extraction,
    Query_Text_Library, and UAT_Cases. Humans add missing checks by
    editing the DVS and re-uploading (DVS_TRANSLATE_PROMPT picks up the
    diff and injects it back into the forms)."""
    _add_scripts("dvs-specification")
    try:
        from generate_dvs import build_dvs
        from extract_dvs_from_forms import extract_dvs_data
    except ImportError as e:
        print(f"DVS builder not available locally: {e}", flush=True)
        return None

    protocol = (struct_json.get("study_meta", {}).get("protocol_number")
                or "STUDY")

    # Mechanical extraction — walks every survey row in every form and
    # emits the 4 DVS content arrays in the shape build_dvs expects.
    dvs_data = extract_dvs_data(struct_json, forms_json)
    print(f"DVS extraction — {len(dvs_data['dvs_oc4'])} checks, "
          f"{len(dvs_data['query_text_library'])} unique messages, "
          f"{len(dvs_data['uat_cases'])} UAT cases", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        xlsx_path = os.path.join(tmp, f"{protocol}_DVS.xlsx")
        build_dvs(dvs_data, xlsx_path)
        return open(xlsx_path, "rb").read()


# ── OpenClinica Study Service API ─────────────────────────────────────────────

async def _get_oc_token(subdomain, is_production=False):
    """
    Get OC auth token via user-service password grant.

    Note: `is_production` is retained for logging/monday-column compatibility
    but no longer affects the URL — all OC traffic goes to openclinica.io
    (the single customer-facing environment).
    """
    import httpx
    username = os.environ.get("OC_API_USERNAME", "").strip()
    password = os.environ.get("OC_API_PASSWORD", "").strip()
    if not username or not password:
        raise ValueError("OC_API_USERNAME or OC_API_PASSWORD not set")
    url = f"https://{subdomain}.build.openclinica.io/user-service/api/oauth/token"
    print(f"Getting OC auth token from {url}", flush=True)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url,
                         headers={"Content-Type": "application/json"},
                         json={"username": username, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"OC auth failed {r.status_code}: {r.text[:200]}")
    return r.text.strip()


async def _check_study_exists(subdomain, token, protocol_num, is_production=False):
    import httpx
    url = f"https://{subdomain}.build.openclinica.io/study-service/api/studies"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url,
                        headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"},
                        params={"archived": "false", "size": 500})
    if r.status_code != 200:
        return None
    uid = protocol_num[:30].lower()
    for s in r.json():
        if s.get("uniqueIdentifier", "").lower() == uid:
            return s.get("uuid")
    return None


def _build_board_json(struct_json):
    """
    Build a board.json payload for the OpenClinica Study Designer
    from the Study Specification JSON.

    board.json structure:
      lists = Events (one per timepoint row)
      cards = Forms (one per form per event it is assigned to)

    Uses Meteor-style 17-char random IDs generated from the OIDs
    so the import is deterministic and repeatable.

    Deduplicates events by `event` OID before building lists — the Claude
    extraction sometimes emits duplicate rows when protocols contain multiple
    overlapping SOE tables (e.g., detailed injection schedule + summary
    weekly schedule). We keep the FIRST occurrence of each event OID; later
    duplicates are dropped with a log line.
    """
    import hashlib

    def _meteor_id(seed):
        """Generate a stable 17-char alphanumeric ID from a seed string."""
        chars = "23456789ABCDEFGHJKLMNPQRSTWXYZabcdefghijkmnopqrstuvwxyz"
        h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
        result = []
        for _ in range(17):
            result.append(chars[h % len(chars)])
            h //= len(chars)
        return ''.join(result)

    raw_timepoint_rows = struct_json.get("timepoint_csv", {}).get("rows", [])
    forms              = struct_json.get("forms", [])

    # ── Deduplicate events by OID (preserve first-seen order) ─────────────
    seen_oids      = set()
    timepoint_rows = []
    dropped        = []
    for row in raw_timepoint_rows:
        oid = row.get("event", "")
        if not oid:
            continue
        if oid in seen_oids:
            dropped.append(oid)
            continue
        seen_oids.add(oid)
        timepoint_rows.append(row)
    if dropped:
        print(f"_build_board_json: dropped {len(dropped)} duplicate "
              f"event row(s): {sorted(set(dropped))}", flush=True)

    # Build event list (lists)
    lists = []
    event_id_map = {}   # event_oid → meteor _id
    for i, row in enumerate(timepoint_rows):
        event_oid   = row.get("event", f"SE_EVENT{i+1}")
        label       = row.get("timepoint", event_oid)
        # Seed Meteor ID with (oid, index) so any future dedup leak still
        # produces unique IDs rather than silently colliding.
        meteor_id   = _meteor_id(f"{event_oid}|{i}")
        event_id_map[event_oid] = meteor_id

        # Determine if repeating — common events don't have visit windows
        is_repeating = "UNSCH" in event_oid.upper() or "COMMON" in event_oid.upper()
        event_type   = "Common" if is_repeating else "Visit-Based"

        lists.append({
            "_id":         meteor_id,
            "title":       label,
            "sort":        i,
            "eventOcoid":  event_oid,
            "isRepeating": is_repeating,
            "type":        event_type,
        })

    # Build form cards
    cards = []
    card_sort = {}          # event_oid → current sort index
    original_card_id = {}  # form_id → first meteor card _id (for _parentId)

    for form in forms:
        form_id      = form.get("form_id", "")
        form_title   = form.get("form_title", form_id)
        visits       = form.get("visits_assigned", [])
        first_card   = True

        # Dedup the visits_assigned list too — same root cause can produce
        # a form assigned to the same event twice.
        visits = list(dict.fromkeys(visits))  # preserves order

        for event_oid in visits:
            if event_oid not in event_id_map:
                continue
            list_id  = event_id_map[event_oid]
            sort_idx = card_sort.get(event_oid, 0)
            card_sort[event_oid] = sort_idx + 1

            # Generate stable card ID from form+event+sort to guarantee
            # uniqueness even if some form/event combo somehow repeats.
            card_id  = _meteor_id(f"{form_id}|{event_oid}|{sort_idx}")

            card = {
                "_id":      card_id,
                "title":    form_title,
                "listId":   list_id,
                "formOcoid": form_id,
                "sort":     sort_idx,
            }

            # First occurrence is the original; subsequent ones reference it
            if first_card:
                original_card_id[form_id] = card_id
                first_card = False
            else:
                card["_parentId"] = original_card_id[form_id]

            cards.append(card)

    return {"labels": [], "lists": lists, "cards": cards}


async def _get_board_id(subdomain, study_uuid, is_production, token=None):
    """
    Get the Study Designer board ID for a newly created study.
    The board ID is embedded in the currentBoardUrl returned by the study-service.
    URL format: https://{subdomain}.design.openclinica.io/b/{boardId}/...
    """
    import httpx
    if token is None:
        token = await _get_oc_token(subdomain, is_production=is_production)
    base_url = f"https://{subdomain}.build.openclinica.io"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{base_url}/study-service/api/studies/{study_uuid}",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Could not fetch study details: {r.status_code} {r.text[:200]}")
    data          = r.json()
    board_url     = data.get("currentBoardUrl", "")
    # Extract board ID from URL: .../b/{boardId}/...
    if "/b/" in board_url:
        parts    = board_url.split("/b/")
        board_id = parts[1].split("/")[0]
        print(f"Board ID: {board_id}", flush=True)
        return board_id
    raise RuntimeError(f"Could not extract board ID from URL: {board_url}")


async def _import_board(subdomain, board_id, board_json, is_production, token=None):
    """
    Import the board.json into the study designer.
    POST {designer_url}/api/importStudy/{boardId}
    """
    import httpx
    if token is None:
        token = await _get_oc_token(subdomain, is_production=is_production)
    designer_url = f"https://{subdomain}.design.openclinica.io"
    endpoint    = f"{designer_url}/api/importStudy/{board_id}"

    print(f"Importing board to: {endpoint}", flush=True)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=board_json,
        )
    print(f"Board import: {r.status_code} {r.text[:200]}", flush=True)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Board import failed {r.status_code}: {r.text[:300]}")
    return True


async def create_oc_study(subdomain, struct_json, is_production=False):
    """
    Create a study in OpenClinica and import the Study Design Board (SOE).

    Steps:
    1. Create study shell via study-service API
       (skips if study already exists)
    2. Build board.json from struct_json (events + forms)
    3. Get the board ID from the newly created study
    4. Import board.json via study designer API
    """
    import httpx
    token    = await _get_oc_token(subdomain, is_production=is_production)
    headers  = {"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}
    base_url = f"https://{subdomain}.build.openclinica.io"
    meta     = struct_json.get("study_meta", {})
    protocol_num = meta.get("protocol_number", "STUDY")

    # ── Step 1: Create or find the study ──────────────────────────────────────
    existing_uuid = await _check_study_exists(subdomain, token, protocol_num,
                                               is_production=is_production)
    if existing_uuid:
        print(f"Study already exists (uuid: {existing_uuid}) — skipping creation.", flush=True)
        study_uuid = existing_uuid
    else:
        type_map  = {"interventional": "INTERVENTIONAL", "observational": "OBSERVATIONAL"}
        phase_map = {"phase i": "PHASEI", "phase 1": "PHASEI",
                     "phase ii": "PHASEII", "phase 2": "PHASEII",
                     "phase iii": "PHASEIII", "phase 3": "PHASEIII",
                     "phase iv": "PHASEIV", "phase 4": "PHASEIV"}
        today      = _dt.date.today().isoformat()
        dur_months = int(meta.get("total_study_duration_months", 24) or 24)
        end_date   = (_dt.date.today().replace(
                       year=_dt.date.today().year + dur_months // 12)).isoformat()

        payload = {
            "name":               protocol_num,
            "description":        meta.get("study_title",
                                   meta.get("description",
                                            f"{protocol_num} — {meta.get('indication', '')}")),
            "uniqueIdentifier":   protocol_num[:30],
            "type":               type_map.get(str(meta.get("type","")).lower(),
                                               "INTERVENTIONAL"),
            "phase":              phase_map.get(str(meta.get("study_phase","")).lower().strip(),
                                               "OTHER_NON_IND"),
            "expectedStartDate":  today,
            "expectedEndDate":    end_date,
            "expectedEnrollment": int(meta.get("expected_enrollment", 0) or 0),
            "collectSex":         True,
            "collectDateOfBirth": "ONLY_THE_YEAR",
            "collectPersonId":    "ALWAYS",
        }

        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{base_url}/study-service/api/studies",
                             headers=headers, json=payload)
        print(f"OC Study API: {r.status_code} {r.text[:300]}", flush=True)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"OC Study API returned {r.status_code}: {r.text[:300]}")
        study_uuid = r.json().get("uuid", "")
        if not study_uuid:
            raise RuntimeError("Study created but no UUID returned")

    designer_url = f"https://{subdomain}.design.openclinica.io"
    study_url    = f"{designer_url}/b/{study_uuid}"

    # ── Step 2: Build board.json from struct_json ──────────────────────────────
    print("Building board.json from Study Specification...", flush=True)
    board_json = _build_board_json(struct_json)
    print(f"Board: {len(board_json['lists'])} events, "
          f"{len(board_json['cards'])} form cards", flush=True)

    # ── Steps 3 + 4: Board import via design service ──────────────────────────
    # NOTE: /api/importStudy/{boardId} is a CLONE-INTO-EMPTY operation. It
    # returns HTTP 500 {"error":"Copy error"} if the target board already
    # contains events/forms. When the pipeline creates a brand-new study,
    # the board is always empty so this succeeds. When re-running against
    # an existing study (study-already-exists branch above), the board
    # probably has content from the prior run and import will fail —
    # we handle that case gracefully with Option A (skip + log).
    board_imported = False
    board_error    = None
    try:
        board_id = await _get_board_id(subdomain, study_uuid, is_production, token=token)
        await _import_board(subdomain, board_id, board_json, is_production, token=token)
        print("Study design board imported successfully.", flush=True)
        board_imported = True
    except Exception as e:
        board_error = str(e)
        # Classify the failure so the user gets an actionable message
        err_lower = board_error.lower()
        if "copy error" in err_lower or "500" in board_error:
            print(f"Board import failed — target board is not empty.",
                  flush=True)
            print(f"  The OpenClinica design service's importStudy endpoint "
                  f"only works on empty boards. To re-import, open the design "
                  f"board in the UI, delete existing events/forms, then re-run "
                  f"this pipeline.", flush=True)
            board_error = ("Board already has content — manual cleanup required "
                           "before re-import. See the design board URL above.")
        elif "401" in board_error or "unauthorized" in err_lower:
            print(f"Board import failed — authentication rejected (401).",
                  flush=True)
            print(f"  Check OC_API_USERNAME / OC_API_PASSWORD Railway env vars "
                  f"match a valid OpenClinica user account.", flush=True)
        else:
            print(f"Board import failed — {board_error}", flush=True)
            print(f"  Unexpected error — check Railway logs for full traceback.",
                  flush=True)

    # Return a dict so callers can surface both the URL and the import state
    return {
        "study_url":      study_url,
        "board_imported": board_imported,
        "board_error":    board_error,
    }


# ── Publish-to-Test workflow (button webhook → main.py → publish_to_test) ────
#
# Triggered when the user clicks the "Publish to Test" button on a row in
# Monday. main.py's /webhook/monday dispatches button-click events here.
#
# Flow:
#   1. read oc_subdomain + oc_study_url from monday
#   2. parse study_uuid from the /b/<uuid> path of oc_study_url
#   3. GET /api/studies/{uuid}/study-environments → (env_uuid, oid)
#   4. POST /api/studies/{uuid}/study-versions with studyEnvironmentUuid
#   5. update Study OID + Published Status columns
#
# Status transitions on the Published Status column:
#   Publishing → Published   (happy path)
#   Publishing → Failed      (any exception)
#
# publish_to_test() catches all exceptions and writes them to ai_run_log +
# Published Status. It never raises — the webhook should always return.


def _extract_study_uuid_from_url(study_url: str) -> str:
    """Parse the path component `/b/<uuid>` out of an OC designer URL.

    Pure function — no API call. Easy to unit-test offline.

        >>> _extract_study_uuid_from_url(
        ...     "https://acme.design.openclinica.io/b/abcd1234-5678")
        'abcd1234-5678'

    Raises ValueError with the bad URL if no /b/<...> path is present.
    """
    import re
    m = re.search(r"/b/([^/?#]+)", study_url or "")
    if not m:
        raise ValueError(
            f"Could not extract study UUID from URL: {study_url!r}. "
            f"Expected a '/b/<UUID>' path component."
        )
    return m.group(1)


async def _get_study_environment_uuid(
    subdomain,
    study_uuid,
    env_name="Test",
    is_production=False,
    token=None,
):
    """GET /api/studies/{study_uuid}/study-environments and return
    (env_uuid, oid) for the entry whose environmentName matches env_name
    (case-insensitive).

    Returns a 2-tuple so the caller can populate both the studyEnvironmentUuid
    input to /study-versions AND the Study OID monday column from one call.

    Raises RuntimeError if env_name isn't found, with the list of envs
    that WERE returned — actionable for the operator.
    """
    import httpx
    if token is None:
        token = await _get_oc_token(subdomain, is_production=is_production)
    url = (f"https://{subdomain}.build.openclinica.io"
           f"/study-service/api/studies/{study_uuid}/study-environments")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        })
    if r.status_code != 200:
        raise RuntimeError(
            f"GET /study-environments failed: {r.status_code} {r.text[:300]}"
        )
    envs = r.json()
    if not isinstance(envs, list):
        raise RuntimeError(
            f"GET /study-environments returned non-list: "
            f"{type(envs).__name__} — {str(envs)[:200]}"
        )

    target = env_name.strip().lower()
    for env in envs:
        if (env.get("environmentName") or "").strip().lower() == target:
            env_uuid = env.get("uuid")
            oid      = env.get("oid") or ""
            if not env_uuid:
                raise RuntimeError(
                    f"Environment {env_name!r} found but has no uuid field. "
                    f"Full entry: {env}"
                )
            return env_uuid, oid

    available = [e.get("environmentName", "?") for e in envs]
    raise RuntimeError(
        f"No environment named {env_name!r} found for study {study_uuid}. "
        f"Available: {available}"
    )


async def _publish_study_version(
    subdomain,
    study_uuid,
    study_environment_uuid,
    version_name=None,
    description=None,
    is_production=False,
    token=None,
):
    """POST /api/studies/{study_uuid}/study-versions.

    Body:  {"studyEnvironmentUuid": ..., "versionName": ..., "description": ...}
    Returns: the parsed StudyVersion response dict.

    Raises RuntimeError on non-2xx with the response body in the message
    so the caller can write a meaningful Published Status note.
    """
    import httpx
    if token is None:
        token = await _get_oc_token(subdomain, is_production=is_production)
    url = (f"https://{subdomain}.build.openclinica.io"
           f"/study-service/api/studies/{study_uuid}/study-versions")
    body = {"studyEnvironmentUuid": study_environment_uuid}
    if version_name:
        body["versionName"] = version_name
    if description:
        body["description"] = description

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }, json=body)
    print(f"OC Publish API: {r.status_code} {r.text[:300]}", flush=True)
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"Publish failed: {r.status_code} {r.text[:300]}"
        )
    return r.json()


async def publish_to_test(item_id):
    """Entry point invoked by main.py's safe_run_publish background task.

    See the section comment above for the full flow. This function never
    raises — every failure path is captured into Published Status="Failed"
    and append_log() so the operator sees what went wrong on the monday row.
    """
    item_id = str(item_id)

    try:
        # 1. Show we're working on it
        await set_status(item_id, COL["published_status"], "Publishing")
        await append_log(item_id, "Publish to Test: starting")

        # 2. Read inputs from monday
        item = await get_item(item_id)
        cols = {cv["id"]: cv for cv in (item.get("column_values") or [])}
        oc_subdomain = (cols.get(COL["oc_subdomain"], {}).get("text") or "").strip()
        oc_study_url = (cols.get(COL["oc_study_url"], {}).get("text") or "").strip()

        if not oc_subdomain:
            raise RuntimeError(
                "OC Subdomain is empty. Set it on this row before clicking "
                "Publish to Test.")
        if not oc_study_url:
            raise RuntimeError(
                "OC Study URL is empty. The study must be created via the "
                "main pipeline (Send to AI) before it can be published.")

        # 3. Parse study_uuid from the stored designer URL
        study_uuid = _extract_study_uuid_from_url(oc_study_url)
        await append_log(item_id,
                         f"Publish to Test: study_uuid={study_uuid}")

        # 4. Resolve env uuid + oid via /study-environments
        env_uuid, oid = await _get_study_environment_uuid(
            oc_subdomain, study_uuid,
            env_name="Test", is_production=False,
        )
        await append_log(item_id,
            f"Publish to Test: env_uuid={env_uuid}  oid={oid!r}")

        # 5. Persist the Study OID column (cheap bonus from the same call)
        if oid:
            await set_text(item_id, COL["study_oid"], oid)

        # 6. Publish
        ts = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        version_name = f"UAT-{ts}"
        response = await _publish_study_version(
            oc_subdomain, study_uuid, env_uuid,
            version_name=version_name,
            is_production=False,
        )
        await append_log(item_id,
            f"Publish to Test: success — version={version_name}  "
            f"response_keys={list(response.keys())[:6]}")

        # 7. Mark as published
        await set_status(item_id, COL["published_status"], "Published")

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"PUBLISH_TO_TEST FAILED for item {item_id}: {err}", flush=True)
        print(traceback.format_exc(), flush=True)
        # Best-effort error reporting; do NOT raise — webhook must return.
        try:
            await set_status(item_id, COL["published_status"], "Failed")
            await append_log(item_id, f"Publish to Test FAILED: {err}")
        except Exception as inner:
            print(f"PUBLISH_TO_TEST status-update fallback also failed: "
                  f"{inner}", flush=True)


# ── OC-9 backstop: Common Visit for cross-visit forms ────────────────────────

def _enforce_common_visit(struct_json):
    """RULE OC-9 backstop. Ensure SE_COMMON exists and AE/CM/DV/AESAE
    forms only live there. Runs after Claude returns the Study Spec JSON
    and fixes the structure deterministically if Claude forgot.

    Idempotent — safe to call multiple times.
    """
    COMMON_FORMS = {"AE", "CM", "DV", "AESAE"}

    if not isinstance(struct_json, dict):
        return struct_json

    forms = struct_json.get("forms", [])
    if not isinstance(forms, list):
        return struct_json

    # Events may be stored under "events" or "visits" depending on version.
    # We canonicalize to "events" but tolerate both.
    events = struct_json.get("events")
    if events is None:
        events = struct_json.get("visits", [])
    if not isinstance(events, list):
        events = []

    # Only create SE_COMMON if at least one of the common forms is actually
    # in scope for this protocol. If none are scoped, skip entirely.
    common_forms_in_study = [
        f for f in forms
        if isinstance(f, dict) and f.get("form_id") in COMMON_FORMS
    ]
    if not common_forms_in_study:
        return struct_json

    # Find enrollment event to anchor SE_COMMON's availability window
    enrollment_oid = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        oid = str(ev.get("event_oid", "")).upper()
        if "ENRL" in oid or "RAND" in oid or "ENROLL" in oid:
            enrollment_oid = ev.get("event_oid")
            break

    # Ensure SE_COMMON exists in events
    has_se_common = any(
        isinstance(ev, dict) and ev.get("event_oid") == "SE_COMMON"
        for ev in events
    )
    if not has_se_common:
        events.append({
            "event_oid":       "SE_COMMON",
            "event_title":     "Common Visit",
            "event_type":      "common",
            "is_repeating":    True,
            "available_after": enrollment_oid or "",
        })
        struct_json["events"] = events

    # Force visits_assigned=["SE_COMMON"] on each common form
    fixed_count = 0
    for f in common_forms_in_study:
        if f.get("visits_assigned") != ["SE_COMMON"]:
            f["visits_assigned"] = ["SE_COMMON"]
            fixed_count += 1

    if fixed_count:
        print(f"OC-9 backstop: reassigned {fixed_count} form(s) to SE_COMMON",
              flush=True)

    return struct_json


def _backfill_migration_fields(spec):
    """Add schedule_of_events + per-form migration lifecycle fields if
    missing. Idempotent — safe to call on every spec load."""
    if not isinstance(spec, dict):
        return spec

    # Top-level schedule_of_events
    if "schedule_of_events" not in spec:
        # Derive target-side visit_mappings from timepoint_csv
        tpt_rows = (spec.get("timepoint_csv") or {}).get("rows") or []
        visit_mappings = []
        seen_events = set()
        for row in tpt_rows:
            ev = row.get("event")
            if ev and ev not in seen_events:
                seen_events.add(ev)
                visit_mappings.append({
                    "source_oid": None,
                    "source_name": None,
                    "target_oid": ev,
                    "target_name": row.get("timepoint", ""),
                    "action": "pending",
                    "notes": "",
                })

        # Derive form_placements as a flat list (one row per form/visit)
        form_placements = []
        for f in spec.get("forms") or []:
            for v in f.get("visits_assigned") or []:
                form_placements.append({
                    "target_visit_oid": v,
                    "form_id": f.get("form_id", ""),
                    "required": True,
                    "repeating": bool(f.get("has_repeating_group")),
                    "notes": "",
                })

        # Arm mappings from study_meta.arms
        arms = (spec.get("study_meta") or {}).get("arms") or []
        arm_mappings = [
            {"source_arm": None, "target_arm": a.get("arm_code", ""), "action": "pending"}
            for a in arms
        ]

        spec["schedule_of_events"] = {
            "migration_status": "draft",
            "approved_by": "",
            "approved_at": "",
            "visit_mappings": visit_mappings,
            "form_placements": form_placements,
            "arm_mappings": arm_mappings,
        }

    # Top-level study_settings — separate from SOE per UX design
    if "study_settings" not in spec:
        # Migrate subject_id_rule from old SOE-nested location if present
        legacy_rule = (spec.get("schedule_of_events") or {}).pop("subject_id_rule", None)
        spec["study_settings"] = {
            "migration_status": "draft",
            "approved_by": "",
            "approved_at": "",
            "subject_id_rule": legacy_rule or {
                "mode": "passthrough",
                "template": "",
                "pattern": "",
                "replacement": "",
            },
        }
    else:
        # study_settings exists, but if SOE still has subject_id_rule from
        # an in-flight spec, fold it in (preferring study_settings if both)
        soe = spec.get("schedule_of_events") or {}
        if "subject_id_rule" in soe:
            spec["study_settings"].setdefault("subject_id_rule", soe.pop("subject_id_rule"))

    # Per-form lifecycle fields
    for f in spec.get("forms") or []:
        f.setdefault("migration_status", "draft")
        f.setdefault("approved_by", "")
        f.setdefault("approved_at", "")
        f.setdefault("rejected_reason", "")

    return spec


# ── Main pipeline ──────────────────────────────────────────────────────────────

async def run_pipeline(item_id):
    try:
        # ── 0. Fetch item from monday.com ─────────────────────────────────────
        item         = await get_item(item_id)
        cols         = {c["id"]: c for c in item["column_values"]}
        protocol_num = cols.get(COL["protocol_number"], {}).get("text", "STUDY")
        oc_subdomain = cols.get(COL["oc_subdomain"],    {}).get("text", "").strip()

        # Fetch library filenames for both columns so we can inject them into
        # the Study Spec JSON's study_meta.library_files_provided (overrides
        # whatever Claude emits). This gives humans a clear record of which
        # library inputs were used.
        try:
            crf_lib_names    = await list_column_filenames(item_id, COL["crf_library"])
            oc_std_lib_names = await list_column_filenames(item_id, COL["oc_standard"])
        except Exception as e:
            print(f"Warning: could not fetch library filenames: {e}", flush=True)
            crf_lib_names, oc_std_lib_names = [], []
        library_files_provided = crf_lib_names + oc_std_lib_names
        print(f"Library files: CRF={crf_lib_names} | OC4={oc_std_lib_names}", flush=True)

        _now    = _dt.datetime.utcnow()
        version = f"V{_now.strftime('%m%d')}.{_now.strftime('%H%M')}"
        print(f"Protocol: {protocol_num} | Version: {version}", flush=True)

        def _pct(col_key):
            raw = cols.get(COL[col_key], {}).get("text", "").strip()
            try:
                return float(raw) / 100.0 if raw else 0.0
            except ValueError:
                return 0.0

        additional_sub_disc = _pct("subscription_discount")
        additional_svc_disc = _pct("services_discount")

        output_raw        = cols.get(COL["output_requested"], {}).get("text", "") or ""
        output_selections = {s.strip().lower() for s in output_raw.split(",") if s.strip()}
        run_all = len(output_selections) == 0
        def _want(label):
            return run_all or label.lower() in output_selections
        print(f"Output requested: {output_raw!r} | run_all={run_all}", flush=True)

        create_study_val = cols.get(COL["create_study"], {}).get("value")
        try:
            parsed = json.loads(create_study_val or "{}")
            create_study = bool(parsed.get("checked", False)) if isinstance(parsed, dict) else bool(parsed)
        except Exception:
            create_study = False

        oc_production_val = cols.get(COL["oc_production"], {}).get("value")
        try:
            parsed = json.loads(oc_production_val or "{}")
            oc_production = bool(parsed.get("checked", False)) if isinstance(parsed, dict) else bool(parsed)
        except Exception:
            oc_production = False

        # Trainer integration is gated on TRAINER_URL being set in env
        # (see trainer_integration.trainer_enabled). No per-row Monday
        # checkbox — successful pipeline runs always feed the trainer
        # when the service is wired up; dedup on the trainer side
        # prevents duplicate corpus rows.
        trainer_on = trainer_enabled()

        print(f"Create OC Study: {create_study} | Subdomain: {oc_subdomain} | "
              f"Production: {oc_production} | Trainer enabled: {trainer_on}",
              flush=True)

        # ── 1. Check for human-uploaded inputs (parallel downloads) ──────────
        (edited_spec_xlsx,
         edited_build_zip,
         edited_dvs_xlsx,
         edited_quote_xlsx,
         edited_soe_csv,
         source_edc_export_bytes) = await asyncio.gather(
            download_column_file(item_id, COL["edited_spec_input"]),
            download_column_file(item_id, COL["build_input"]),
            download_column_file(item_id, COL["dvs_input"]),
            download_column_file(item_id, COL["quote_input"]),
            download_column_file(item_id, COL["soe_input"]),
            download_column_file(item_id, COL["source_edc_export"]),
        )

        print(f"Human inputs — spec:{edited_spec_xlsx is not None} "
              f"build:{edited_build_zip is not None} dvs:{edited_dvs_xlsx is not None} "
              f"quote:{edited_quote_xlsx is not None} soe:{edited_soe_csv is not None} "
              f"edc_export:{source_edc_export_bytes is not None}",
              flush=True)

        # ── Path D: Edited Quote XLSX → regenerate Quote PDFs ─────────────────
        # DEPRECATED: there's no local script to regenerate Quote PDFs from
        # an edited XLSX. To refresh the PDFs, edit the Protocol Summary JSON
        # or Study Spec and re-run the full pipeline.
        if edited_quote_xlsx:
            await append_log(item_id,
                "Edited Quote XLSX detected. Automatic PDF regeneration from edited "
                "XLSX is not currently supported — to regenerate PDFs, edit the "
                "underlying Protocol Summary JSON or Study Spec and re-run the "
                "full pipeline."
            )
            print("Path D: edited Quote XLSX detected — regeneration not supported, "
                  "skipping without changes.", flush=True)
            await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
            await append_log(item_id, "Pipeline complete (Path D — no-op).")
            return

        # ── Path E: Edited SOE CSV → update OpenClinica ───────────────────────
        if edited_soe_csv and oc_subdomain:
            await append_log(item_id, "Edited SOE CSV detected — updating OpenClinica.")
            print("Path E: updating SOE in OpenClinica...", flush=True)
            # TODO: implement SOE update API call when OC API supports it
            await append_log(item_id, "SOE update in OpenClinica — not yet implemented.")
            await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
            return

        # ── Download inputs in parallel ───────────────────────────────────────
        async def _get_protocol_doc():
            """
            Download the protocol document from the protocol column ONLY.

            Reads exclusively from COL["protocol"] (no whole-item asset scan).
            Supports:
              - PDF: returned as-is.
              - Word (.docx / .doc): converted to PDF via LibreOffice when
                possible; otherwise extracted as text and returned with the
                ``%%DOCX_TEXT%%`` marker so callers can pass it as
                ``extra_text`` instead of ``pdf_bytes``.
              - Google Doc / Drive link (URL in the column rather than an
                uploaded file): exported as PDF.

            Returns bytes on success, or None when the column is empty / the
            content can't be resolved. Callers must tolerate None.
            """
            if edited_spec_xlsx:
                return None  # Skip — Path A already gives us struct via XLSX

            col_entry = cols.get(COL["protocol"], {})
            raw_value = col_entry.get("value")

            # 1. Uploaded-file case (asset on the column).
            if raw_value:
                try:
                    parsed = json.loads(raw_value)
                except (ValueError, TypeError):
                    parsed = {}
                files = parsed.get("files") if isinstance(parsed, dict) else None
                if files:
                    body = await download_column_file(item_id, COL["protocol"])
                    if not body:
                        return None
                    fname = (files[-1].get("name") or "").lower()

                    # PDF magic or .pdf extension → pass through.
                    if body.startswith(b"%PDF-") or fname.endswith(".pdf"):
                        return body

                    # ZIP magic (.docx/.xlsx OOXML) or .docx/.doc extension
                    # → try LibreOffice, fall back to extracted text.
                    if body.startswith(b"PK\x03\x04") or \
                       fname.endswith((".docx", ".doc")):
                        print(f"Converting Word doc: {fname or '<unnamed>'}",
                              flush=True)
                        pdf = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: _convert_to_pdf(body, fname or "protocol.docx"),
                        )
                        if pdf:
                            return pdf
                        text = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: _extract_docx_as_text(body),
                        )
                        if text:
                            return b"%%DOCX_TEXT%%" + text.encode("utf-8")
                        return None

                    # Anything else (no recognisable header) — accept the
                    # bytes verbatim and let Claude decide.
                    return body

            # 2. Link case — Monday file columns can also carry a URL
            # (e.g. Google Doc / Drive share link). Detect a docs/drive URL
            # in the column's `.text` and fetch the PDF export.
            link_text = (col_entry.get("text") or "").strip()
            if link_text and ("docs.google.com" in link_text or
                              "drive.google.com" in link_text):
                export_url = _google_doc_export_url(link_text)
                if export_url:
                    print(f"Exporting Google Doc as PDF: {link_text[:80]}",
                          flush=True)
                    return await download_file(export_url) or None

            return None

        # Use download_column_file for CRF / OC standard so we go through the
        # asset → public_url (S3) path that doesn't need session auth. The
        # previous code used the column's `.text` URL which is a Monday
        # `protected_static/...` link that returns HTTP 406 to bearer-token
        # requests, silently dropping the customer's library inputs.
        protocol_bytes, crf_pdf, oc_zip = await asyncio.gather(
            _get_protocol_doc(),
            download_column_file(item_id, COL["crf_library"]),
            download_column_file(item_id, COL["oc_standard"]),
        )
        _proto_desc = (
            f"{len(protocol_bytes):,} bytes PDF" if protocol_bytes and
            not protocol_bytes.startswith(b"%%DOCX_TEXT%%")
            else f"{len(protocol_bytes) - 14:,} chars text (Word doc)"
            if protocol_bytes else "0 bytes"
        )
        print(f"Protocol: {_proto_desc} | "
              f"CRF: {len(crf_pdf) if crf_pdf else 0} | "
              f"OC ZIP: {len(oc_zip) if oc_zip else 0}", flush=True)

        # ── Determine if analysis/chains are needed ───────────────────────────
        needs_analysis = (
            _want("protocol specification") or _want("protocol summary")
            or _want("price quote") or _want("study build zip")
            or (create_study and oc_subdomain)
        )

        # ── Steps 1-2: Study Specification ────────────────────────────────────
        struct_json = None

        # Holds NOTES_FOR_AI content extracted from the edited XLSX — injected
        # into the prompt so Claude understands what the reviewer changed and why.
        reviewer_notes_block = ""

        if edited_spec_xlsx:
            # Path A: User uploaded edited Study Spec XLSX
            await append_log(item_id, "Edited Study Specification XLSX detected.")
            print("Path A: reading edited Study Spec XLSX...", flush=True)
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(edited_spec_xlsx))

            # ── Extract NOTES_FOR_AI from all survey/choices sheets ───────────
            notes_collected = []
            for sheet_name in wb.sheetnames:
                if not (sheet_name.endswith('_survey') or sheet_name.endswith('_choices')):
                    continue
                ws = wb[sheet_name]
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    continue
                # Find ACTION col (A=0) and NOTES_FOR_AI col (B=1) — fixed positions
                # but verify by reading header row (row index 2 = row 3 in 1-indexed)
                header_row_idx = None
                for ri, row in enumerate(rows):
                    if row and str(row[0] or '').strip().upper() == 'ACTION':
                        header_row_idx = ri
                        break
                if header_row_idx is None:
                    continue
                notes_col_idx = 1   # column B = index 1
                action_col_idx = 0  # column A = index 0
                for row in rows[header_row_idx + 1:]:
                    if not row or len(row) <= notes_col_idx:
                        continue
                    action = str(row[action_col_idx] or '').strip().upper()
                    note   = str(row[notes_col_idx] or '').strip()
                    if note and note.lower() not in ('notes_for_ai', 'notes for ai'):
                        field_name = str(row[2] or '').strip() if len(row) > 2 else ''
                        prefix = f"[{sheet_name}][{action or 'MODIFIED'}]"
                        if field_name:
                            prefix += f" {field_name}:"
                        notes_collected.append(f"{prefix} {note}")

            if notes_collected:
                reviewer_notes_block = (
                    "\n\nREVIEWER NOTES FROM EDITED STUDY SPECIFICATION:\n"
                    + "\n".join(f"  • {n}" for n in notes_collected)
                    + "\nApply these notes when interpreting ACTION=DELETE/ADD rows "
                    "and when regenerating any affected forms."
                )
                print(f"Path A: extracted {len(notes_collected)} reviewer notes "
                      f"from edited XLSX", flush=True)

            # ── Try to extract embedded JSON ──────────────────────────────────
            for sheet_name in wb.sheetnames:
                if 'json' in sheet_name.lower() or 'spec' in sheet_name.lower():
                    ws = wb[sheet_name]
                    raw = '\n'.join(str(cell.value or '') for row in ws.iter_rows() for cell in row)
                    try:
                        struct_json = extract_json(
                            raw,
                            expected_keys=["study_meta", "forms"],
                        )
                        print("Extracted JSON from edited Study Spec XLSX.", flush=True)
                        # OC-9 backstop: apply to edited-XLSX path as well
                        struct_json = _enforce_common_visit(struct_json)
                        struct_json = _backfill_migration_fields(struct_json)
                        # ── Conventions engine pass + three-way conflict detection (Phase C.4) ──
                        # Path X.1 is the edited-XLSX path. Three snapshots make TRUE
                        # conflict detection possible:
                        #   baseline        = previous spec_json from monday (what user downloaded)
                        #   user_edit       = struct_json after XLSX parse (what user uploaded)
                        #   post_convention = struct_json after apply_conventions (what engine did)
                        # True conflict: engine touched a path the user ALSO touched.
                        # First run (no baseline) or download failure → falls back to
                        # Phase C.2 two-way behavior (all engine mutations reported).
                        # No false-negative suppression at any stage; degraded modes
                        # only inflate the conflict list, never silence it.
                        try:
                            import copy as _copy
                            from conventions_engine import apply_conventions, diff as _conv_diff, attribution as _conv_attr
                            _study_id = (struct_json.get("study_meta") or {}).get("protocol_number") or protocol_num
                            # TODO(B.1b follow-up): This path does not currently extract monday's
                            # source_edc_system column (dropdown_mm382w7d). Vendor conventions
                            # apply only on migration path (Path M). If non-migration builds need
                            # vendor conventions in future, extract the column at build entry and
                            # thread it through as migration_source here.

                            # Phase C.4: try to fetch the prior spec_json from monday as baseline.
                            # Failures (first run for this study, monday hiccup, malformed JSON)
                            # → None, which silently degrades to Phase C.2 two-way diff.
                            _baseline_spec = None
                            try:
                                _baseline_bytes = await download_column_file(item_id, COL["spec_json"])
                                _baseline_spec = json.loads(_baseline_bytes.decode("utf-8"))
                            except Exception as _be:
                                print(f"conventions_engine: no baseline spec_json available "
                                      f"(first run or download failed: {_be}); falling back to "
                                      f"Phase C.2 two-way diff", flush=True)

                            # Snapshot user's edit BEFORE the engine mutates anything.
                            _user_edit_spec = _copy.deepcopy(struct_json)

                            # User changes = diff(baseline, user_edit). Empty list when no baseline.
                            _user_changes = (
                                _conv_diff.deep_diff(_baseline_spec, _user_edit_spec)
                                if _baseline_spec is not None else []
                            )
                            _user_change_paths = {r["field_path"] for r in _user_changes}

                            apply_conventions(struct_json, study_id=_study_id,
                                              customer_subdomain=oc_subdomain)

                            # Engine changes = diff(user_edit, post_convention). Attribute to conventions.
                            _engine_changes = _conv_diff.deep_diff(_user_edit_spec, struct_json)
                            _applied_log = (struct_json.get("study_meta") or {}).get(
                                "conventions_engine_applied", [])
                            _engine_changes_attributed = _conv_attr.attribute_changes(
                                _engine_changes, _applied_log)

                            # True conflicts: filter engine changes to paths the user also touched.
                            # When no baseline → fallback to all engine mutations (Phase C.2 schema:
                            # 4-key rows lacking baseline_value, renderers handle the missing key).
                            if _baseline_spec is not None:
                                _conflicts = _conv_diff.filter_to_user_intersected(
                                    _engine_changes_attributed, _user_change_paths, _baseline_spec,
                                )
                            else:
                                _conflicts = _engine_changes_attributed

                            _sm = struct_json.setdefault("study_meta", {})
                            _sm["user_changes"] = _user_changes
                            _sm["convention_conflicts"] = _conflicts

                            print(f"conventions_engine: {len(_user_changes)} user change(s), "
                                  f"{len(_engine_changes_attributed)} engine mutation(s), "
                                  f"{len(_conflicts)} true conflict(s) on user-edited spec "
                                  f"(Path X.1, {'three-way' if _baseline_spec is not None else 'two-way fallback'})",
                                  flush=True)
                        except Exception as _ce:
                            print(f"conventions_engine FAILED — continuing without conventions: {_ce}",
                                  flush=True)
                            try:
                                await append_log(item_id, f"Conventions engine error (build continues): {_ce}")
                            except Exception:
                                pass
                        break
                    except ValueError:
                        pass
            if struct_json is None:
                await append_log(item_id, "Could not extract JSON from edited XLSX — running fresh analysis.")

        # ── Path M: Source EDC Export (migration) ─────────────────────────────
        # Customer uploaded an ODM XML (optionally inside a ZIP) to the
        # Source EDC Export column. Run it through validate → parse →
        # transform to produce struct_json, replacing the EDC_STRUCTURE_PROMPT
        # branch below (which is gated by `struct_json is None`).
        # Migration also uploads the Study Spec JSON to COL["spec_json"]
        # itself, so no spec_json_upload_task wiring is needed here.
        if struct_json is None and source_edc_export_bytes:
            # protocol_bytes may be a real PDF, the b"%%DOCX_TEXT%%"-marked
            # text fallback from Word docs, or b""/None. run_migration
            # accepts all three: it routes truthy bytes into enrichment
            # mode and unwraps the DOCX_TEXT marker internally.
            _proto_for_migration = protocol_bytes if protocol_bytes else None
            _enrichment = bool(_proto_for_migration)
            _mode = ("ODM+Protocol enrichment mode (AI-assisted)"
                     if _enrichment else "ODM-only mode")
            await append_log(item_id, f"Source EDC Export detected — running migration path ({_mode}).")
            print(f"Path M: running EDC migration from Source EDC Export — {_mode}", flush=True)
            await set_status(item_id, COL["pipeline_status"], STATUS["analysis_running"])
            mig_result = await run_edc_migration(
                item_id,
                raw_bytes=source_edc_export_bytes,
                protocol_bytes=_proto_for_migration,
            )
            if mig_result["status"] != "ok":
                msg = f"Migration {mig_result['status']}: {mig_result['summary']}"
                print(f"Path M FAIL: {msg}", flush=True)
                await append_log(item_id, msg)
                await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
                return
            # On success, fetch the freshly-uploaded Study Spec JSON so
            # downstream stages see the same in-memory struct.
            spec_bytes = await download_column_file(item_id, COL["spec_json"])
            struct_json = json.loads(spec_bytes.decode("utf-8"))
            struct_json = _enforce_common_visit(struct_json)
            struct_json = _backfill_migration_fields(struct_json)
            # ── Conventions engine pass (no-op until conventions/ store is populated) ─
            try:
                from conventions_engine import apply_conventions
                _study_id = (struct_json.get("study_meta") or {}).get("protocol_number") or protocol_num
                _vendor_slug = _vendor_slug_from_display_name(mig_result.get("source_system"))
                apply_conventions(struct_json, study_id=_study_id,
                                  customer_subdomain=oc_subdomain,
                                  migration_source=_vendor_slug)
            except Exception as _ce:
                print(f"conventions_engine FAILED — continuing without conventions: {_ce}",
                      flush=True)
                try:
                    await append_log(item_id, f"Conventions engine error (build continues): {_ce}")
                except Exception:
                    pass
            print(f"Path M: struct_json loaded — "
                  f"{len(struct_json.get('forms', []))} forms, "
                  f"source={mig_result.get('source_system')}", flush=True)

        # ── Read AI Instructions from edited XLSX (if present) ──────────────
        ai_instructions_block = ""
        if edited_spec_xlsx:
            try:
                import openpyxl as _opx
                _wb = _opx.load_workbook(io.BytesIO(edited_spec_xlsx), data_only=True)
                if "AI_INSTRUCTIONS" in _wb.sheetnames:
                    _ws = _wb["AI_INSTRUCTIONS"]
                    _rows = list(_ws.iter_rows(values_only=True))
                    _study_instrs  = []
                    _form_instrs   = []
                    _in_s1 = _in_s2 = False
                    for _row in _rows:
                        if not _row or not any(v for v in _row if v):
                            continue
                        _cell0 = str(_row[0] or "").strip().upper()
                        if "SECTION 1" in _cell0:
                            _in_s1, _in_s2 = True, False; continue
                        if "SECTION 2" in _cell0:
                            _in_s1, _in_s2 = False, True; continue
                        if "SECTION 3" in _cell0 or "VERSION HISTORY" in _cell0:
                            _in_s1 = _in_s2 = False; continue
                        if _cell0 in ("PRIORITY", "FORM OID", "VERSION"):
                            continue   # skip header rows
                        if _in_s1:
                            _instr = str(_row[1] or "").strip()
                            _pri   = str(_row[0] or "").strip()
                            if _instr and _instr.lower() != "instruction":
                                _study_instrs.append(
                                    f"[{_pri.upper() or 'MEDIUM'}] {_instr}")
                        elif _in_s2:
                            _foid  = str(_row[0] or "").strip()
                            _instr = str(_row[1] or "").strip()
                            if _foid and _instr and _instr.lower() != "instruction":
                                _form_instrs.append(f"  {_foid}: {_instr}")

                    parts = []
                    if _study_instrs:
                        parts.append("STUDY-LEVEL INSTRUCTIONS FROM HUMAN REVIEWER "
                                     "(apply to the entire study):\n" +
                                     "\n".join(f"  • {i}" for i in _study_instrs))
                    if _form_instrs:
                        parts.append("FORM-SPECIFIC INSTRUCTIONS FROM HUMAN REVIEWER:\n" +
                                     "\n".join(_form_instrs))
                    if parts:
                        ai_instructions_block = (
                            "\n\n══════════════════════════════════════════\n"
                            "AI INSTRUCTIONS — HIGHEST PRIORITY — APPLY BEFORE ALL OTHER INPUTS\n"
                            "══════════════════════════════════════════\n" +
                            "\n\n".join(parts) +
                            "\n══════════════════════════════════════════\n"
                        )
                        n_si = len(_study_instrs)
                        n_fi = len(_form_instrs)
                        print(f"AI Instructions: {n_si} study-level, "
                              f"{n_fi} form-specific", flush=True)
                        await append_log(item_id,
                            f"AI Instructions read: {n_si} study-level, "
                            f"{n_fi} form-specific.")
            except Exception as _ai_exc:
                print(f"AI Instructions read error: {_ai_exc}", flush=True)

        # Fresh analysis if needed and not already populated
        # B7: will hold the optional JSON-upload coroutine, to be awaited
        # concurrently with the chains at the main asyncio.gather below.
        # It's set to a coroutine only when we fresh-extract struct_json.
        spec_json_upload_task = None

        if struct_json is None and needs_analysis:
            await set_status(item_id, COL["pipeline_status"], STATUS["analysis_running"])
            await append_log(item_id, "Protocol Analysis started.")

            extra_parts = []
            if ai_instructions_block:
                extra_parts.insert(0, ai_instructions_block.strip())
            if reviewer_notes_block:
                extra_parts.append(reviewer_notes_block.strip())
            # Customer Convention Questions (CQ_* / 'CQ ' columns).
            # Read dynamically from the cols dict — any column with a CQ prefix
            # is picked up automatically. New questions require zero code change.
            customer_conventions = _extract_customer_conventions(cols)
            if customer_conventions:
                cq_block = _build_customer_conventions_block(customer_conventions)
                extra_parts.append(cq_block)
                print(f"Customer conventions: {len(customer_conventions)} answer(s) provided",
                      flush=True)
                await append_log(item_id,
                    f"Customer conventions: {len(customer_conventions)} answer(s) "
                    f"injected into Study Spec generation.")
            else:
                print("Customer conventions: none provided", flush=True)
            if oc_zip:
                oc_file_type = _detect_oc_standard_type(oc_zip)
                if oc_file_type == 'ODM_XML':
                    extra_parts.append(
                        "Customer OpenClinica Study ODM XML attached — use as Priority 1 "
                        "(most authoritative source; reflects what customer has built in OC). "
                        "Extract: (a) StudyEventDef structure to understand the customer's "
                        "schedule-of-events pattern — note any StudyEventDef with "
                        "Type='Common' which indicates the customer uses a Common event for "
                        "non-visit-dependent forms like AE and CM rather than per-module "
                        "events; (b) FormDef/ItemGroupDef/ItemDef to use as form templates; "
                        "(c) CodeLists as choice list baselines; "
                        "(d) existing OIDs as the naming convention to follow."
                    )
                else:
                    extra_parts.append(
                        "Customer OC4 XLSForm Standards (ZIP) attached — use as Priority 1 "
                        "(most authoritative source; reflects what customer has built in OC)."
                    )
            if crf_pdf:
                extra_parts.append(
                    "Customer CRF Library (PDF) attached — use as Priority 2 "
                    "(fallback for any forms not found in the Priority 1 source)."
                )
            # ─── Trainer retrieval: fetch similar past pairs as few-shot examples ──
            # Gated on TRAINER_URL presence (trainer_enabled). When the trainer
            # is wired up, retrieval runs on every pipeline pass; otherwise it
            # is silently skipped (no noisy "not reachable" warnings on local
            # dev runs without a trainer).
            if not trainer_on:
                print("Step 0: Trainer retrieval — SKIPPED (TRAINER_URL not set)",
                      flush=True)
            else:
                try:
                    print("Step 0: Trainer retrieval — quick protocol analysis...", flush=True)
                    quick_analysis = await run_protocol_analysis_quick(protocol_bytes or b"")
                    if quick_analysis:
                        print(f"Step 0: Trainer retrieval — fetching examples (k={TRAINER_K})...",
                              flush=True)
                        matches = await retrieve_examples(
                            quick_analysis, k=TRAINER_K, reserve_same_sponsor=True,
                        )
                        if matches:
                            block = format_examples_block(
                                matches,
                                sponsor_hint=quick_analysis.get("sponsor"),
                                reserve_same_sponsor=True,
                            )
                            if block:
                                extra_parts.append(block)
                                await append_log(item_id,
                                    f"Trainer retrieval: {len(matches)} similar past "
                                    f"build(s) injected as examples.")
                except Exception as _trainer_exc:  # noqa: BLE001
                    print(f"Trainer retrieval failed: {_trainer_exc} — continuing without examples",
                          flush=True)

            print("Step 1: Claude extracting Study Spec JSON...", flush=True)
            # Handle DOCX-as-text fallback: protocol arrived as text, not PDF
            _docx_text_marker = b"%%DOCX_TEXT%%"
            if protocol_bytes and protocol_bytes.startswith(_docx_text_marker):
                docx_text = protocol_bytes[len(_docx_text_marker):].decode("utf-8",
                                                                            errors="replace")
                print(f"Protocol is Word text ({len(docx_text):,} chars) — "
                      f"passing as extra_text", flush=True)
                _pdf_arg   = None
                _text_args = [docx_text] + (extra_parts or [])
            else:
                _pdf_arg   = protocol_bytes or None
                _text_args = extra_parts or []

            struct_text = await call_claude(
                EDC_STRUCTURE_PROMPT,
                pdf_bytes  = _pdf_arg,
                extra_text = "\n".join(_text_args) if _text_args else None,
            )
            try:
                struct_json = extract_json(
                    struct_text,
                    expected_keys=["study_meta", "forms"],
                )
                # Normalize — handle list at top level
                if isinstance(struct_json, list):
                    struct_json = {"study_meta": {"protocol_number": protocol_num},
                                   "forms": struct_json, "review_flags": {}}
                # Normalize — ensure forms is a list of dicts, not strings
                forms = struct_json.get("forms", [])
                if forms and isinstance(forms[0], str):
                    struct_json["forms"] = [{"form_id": f, "form_title": f} for f in forms]
                print(f"Study Spec JSON extracted — "
                      f"{len(struct_json.get('forms', []))} forms, "
                      f"keys: {list(struct_json.keys())}", flush=True)
            except ValueError:
                struct_json = {"study_meta": {"protocol_number": protocol_num},
                               "forms": [], "review_flags": {}}
                print("Warning: Study Spec JSON not valid — using empty fallback", flush=True)

            # OC-9 backstop: ensure SE_COMMON exists and AE/CM/DV/AESAE
            # forms live only there. Deterministic fix-up if Claude missed it.
            struct_json = _enforce_common_visit(struct_json)
            struct_json = _backfill_migration_fields(struct_json)
            # ── Conventions engine pass (no-op until conventions/ store is populated) ─
            try:
                from conventions_engine import apply_conventions
                _study_id = (struct_json.get("study_meta") or {}).get("protocol_number") or protocol_num
                # TODO(B.1b follow-up): This path does not currently extract monday's
                # source_edc_system column (dropdown_mm382w7d). Vendor conventions
                # apply only on migration path (Path M). If non-migration builds need
                # vendor conventions in future, extract the column at build entry and
                # thread it through as migration_source here.
                apply_conventions(struct_json, study_id=_study_id,
                                  customer_subdomain=oc_subdomain)
            except Exception as _ce:
                print(f"conventions_engine FAILED — continuing without conventions: {_ce}",
                      flush=True)
                try:
                    await append_log(item_id, f"Conventions engine error (build continues): {_ce}")
                except Exception:
                    pass

            # Inject library filenames from monday columns into study_meta —
            # overrides whatever Claude may have guessed for
            # library_files_provided. This ensures the Study Spec PDF shows
            # the actual files that were uploaded to the item.
            if library_files_provided:
                sm = struct_json.setdefault("study_meta", {})
                sm["library_files_provided"] = library_files_provided

            # B7: JSON upload is prepared as a coroutine but NOT awaited here —
            # it runs concurrently with chains A-D via the main gather() below.
            # This block only executes when we freshly extracted struct_json
            # (guaranteed by the outer `if struct_json is None` guard).
            spec_json_upload_task = upload_file(
                item_id, COL["spec_json"],
                f"{protocol_num}_Study_Specification_{version}.json",
                json.dumps(struct_json, indent=2).encode()
            )

            # ── Trainer: create pending row on trainer board ────────────────
            # Fires unconditionally on every successful pipeline run when the
            # trainer is wired up (TRAINER_URL set). Best-effort — any failure
            # is logged but does not block the pipeline. The corpus dedup
            # logic in /pending-row prevents duplicates. The new row sits in
            # "Awaiting Build Completion" status until a human uploads the
            # final form definitions.
            if trainer_on and protocol_bytes:
                try:
                    sponsor_hint = (struct_json.get("study_meta", {})
                                    .get("sponsor")
                                    or struct_json.get("study_meta", {})
                                    .get("sponsor_name"))
                    print(f"[trainer] creating pending row: name={protocol_num!r} "
                          f"sponsor={sponsor_hint!r}", flush=True)
                    new_trainer_item_id = await create_pending_row(
                        protocol_bytes,
                        name=protocol_num,
                        protocol_filename=f"{protocol_num}.pdf",
                        sponsor_client=sponsor_hint,
                        source_pipeline_item=str(item_id),
                    )
                    if new_trainer_item_id:
                        await append_log(
                            item_id,
                            f"Trainer pending row created: item_id={new_trainer_item_id}",
                        )
                except Exception as _trainer_exc:  # noqa: BLE001
                    print(f"[trainer] create_pending_row failed: {_trainer_exc} "
                          f"— continuing without trainer row", flush=True)

        # ── Mapping review UI deep-link ─────────────────────────────────────
        # Populate COL["mapping_review_url"] once we have a Study Spec JSON,
        # regardless of which path produced it (Path B protocol-PDF or Path M
        # migration). Gated on MAPPING_UI_URL env — when unset (local dev,
        # mapping-ui not yet deployed) the column write is skipped silently.
        # Best-effort; failure does not block the chains.
        if struct_json:
            mapping_ui_base = os.environ.get("MAPPING_UI_URL", "").strip().rstrip("/")
            if mapping_ui_base:
                review_url = f"{mapping_ui_base}/?item={item_id}&board={BOARD_ID}"
                try:
                    await set_link(item_id, COL["mapping_review_url"], review_url,
                                   text="Open Mapping Review")
                    await append_log(item_id,
                        f"Mapping review URL written: {review_url}")
                except Exception as _link_exc:  # noqa: BLE001
                    print(f"mapping_review_url write failed (non-fatal): "
                          f"{_link_exc}", flush=True)

        # ── Launch parallel chains if struct_json is available ────────────────
        if struct_json and needs_analysis:
            await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
            await append_log(item_id, "Chains A (spec files), B (summary+quote), C (build+DVS), D (OC study) starting in parallel.")

            # Shared state for parallel chains
            pricing_json = {"study_meta": {"protocol_number": protocol_num}}

            # ── Chain A: Study Spec files ──────────────────────────────────────
            async def chain_a():
                if not _want("protocol specification"):
                    return
                print("Chain A: Generating Study Spec PDF + XLSX (local)...", flush=True)
                try:
                    loop = asyncio.get_event_loop()
                    spec_files = await loop.run_in_executor(
                        None, lambda: run_study_spec_files(struct_json, oc_subdomain, None)
                    )
                    await asyncio.gather(
                        upload_file(item_id, COL["spec_pdf"],
                            f"{protocol_num}_Study_Specification_{version}.pdf",
                            spec_files["pdf"]),
                        upload_file(item_id, COL["spec_xlsx"],
                            f"{protocol_num}_Study_Specification_{version}.xlsx",
                            spec_files["xlsx"]),
                    )
                    print(f"Chain A complete — pdf:{len(spec_files['pdf'])} bytes "
                          f"xlsx:{len(spec_files['xlsx'])} bytes", flush=True)
                except Exception as e:
                    print(f"Chain A error: {e}", flush=True)
                    traceback.print_exc()
                    await append_log(item_id, f"Study Spec file generation error: {e}")

            # ── Chain B: Protocol Summary JSON → PDF + Quote ───────────────────
            async def chain_b():
                nonlocal pricing_json
                want_summary = _want("protocol summary")
                want_quote   = _want("price quote")
                if not want_summary and not want_quote:
                    return

                # Both branches need pricing_json — extract it once
                print("Chain B: Claude extracting Protocol Summary JSON...", flush=True)
                # Slim struct_json for Protocol Summary — keep keys that use
                # the actual Study Spec field names (form_id, form_title,
                # cdash_domain). Previously slimming to "name"/"domain" lost
                # all form identity → empty Protocol Summary → empty Quote.
                # Strip convention-engine debug payloads out of study_meta —
                # these are sized for engine audit (≈3 MB / 750K tokens for
                # CRS-136) and would blow the Claude 1M context cap on the
                # Chain B Protocol Summary call. Chain B only needs the
                # protocol-metadata fields. See pipeline failure 2026-05-18
                # at 19:19:25 UTC (1.22M-token prompt rejected).
                _META_BLOAT_KEYS = {
                    "conventions_prompt_block",
                    "conventions_engine_applied",
                    "customer_vendor_conflicts",
                }
                _study_meta_full = struct_json.get("study_meta", {})
                struct_slim = {
                    "study_meta":    {k: v for k, v in _study_meta_full.items()
                                      if k not in _META_BLOAT_KEYS},
                    "timepoint_csv": struct_json.get("timepoint_csv", {}),
                    "review_flags":  struct_json.get("review_flags", {}),
                    "forms": [
                        {"form_id":         f.get("form_id", ""),
                         "form_title":      f.get("form_title", ""),
                         "cdash_domain":    f.get("cdash_domain", ""),
                         "form_category":   f.get("form_category", ""),
                         "complexity":      f.get("complexity", ""),
                         "visits_assigned": f.get("visits_assigned", []),
                         "reuse_count":     f.get("reuse_count", 1),
                         "is_epro":         f.get("is_epro", False),
                         "has_repeating_group": f.get("has_repeating_group", False),
                         "arm_applicability":   f.get("arm_applicability", "ALL")}
                        for f in struct_json.get("forms", [])
                        if isinstance(f, dict)
                    ],
                }
                pricing_text = await call_claude(
                    PRICING_SUMMARY_PROMPT,
                    extra_text="Study Specification JSON:\n" + json.dumps(struct_slim),
                    max_tokens=64000,  # B4: Chain B may also produce large output
                )
                pricing_json_valid = False
                try:
                    pricing_json = extract_json(
                        pricing_text,
                        expected_keys=["study_meta", "patient_population",
                                       "visit_summary", "crf_summary"],
                    )
                    if isinstance(pricing_json, list):
                        print("Warning: Protocol Summary was a list, not a dict", flush=True)
                        pricing_json = {"study_meta": {"protocol_number": protocol_num}}
                    else:
                        # B5: Validate required keys actually came through.
                        # Fewer than 3 of the 4 expected keys means extraction
                        # gave us a fragment, not a complete summary.
                        expected = {"study_meta", "patient_population",
                                    "visit_summary", "crf_summary"}
                        present  = expected & set(pricing_json.keys())
                        if len(present) >= 3:
                            pricing_json_valid = True
                            print(f"Protocol Summary JSON extracted — "
                                  f"keys: {list(pricing_json.keys())}", flush=True)
                        else:
                            print(f"Warning: Protocol Summary missing expected keys. "
                                  f"Got: {sorted(present)}. Skipping Chain B work.", flush=True)
                except ValueError:
                    print("Warning: Protocol Summary JSON not valid — "
                          "skipping Chain B work.", flush=True)

                # B1+B5: Bail out of Chain B entirely if pricing_json is unusable.
                # Prevents uploading empty/garbage PDFs to monday.com.
                if not pricing_json_valid:
                    await append_log(item_id,
                        "Chain B skipped — Protocol Summary JSON invalid or incomplete. "
                        "Study Spec JSON is still available; re-run may recover.")
                    return

                # Steps 4 + 5 in parallel: Protocol Summary PDF + Pricing Quote
                async def gen_ps_pdf():
                    if not want_summary:
                        return
                    print("Chain B: Generating Protocol Summary PDF (local)...", flush=True)
                    try:
                        loop = asyncio.get_event_loop()
                        ps_pdf = await loop.run_in_executor(
                            None, lambda: run_protocol_summary_pdf(pricing_json, struct_json)
                        )
                        uploads = [upload_file(item_id, COL["pricing_summary"],
                            f"{protocol_num}_Protocol_Summary_{version}.json",
                            json.dumps(pricing_json, indent=2).encode())]
                        if ps_pdf:
                            uploads.append(upload_file(item_id, COL["pricing_summary"],
                                f"{protocol_num}_Protocol_Summary_{version}.pdf", ps_pdf))
                        await asyncio.gather(*uploads)
                        print(f"Protocol Summary PDF: {len(ps_pdf) if ps_pdf else 0} bytes", flush=True)
                    except Exception as e:
                        print(f"Protocol Summary PDF error: {e}", flush=True)
                        traceback.print_exc()
                        await append_log(item_id, f"Protocol Summary PDF error: {e}")
                        raise   # B6: surface failure to chain_b

                async def gen_quote():
                    if not want_quote:
                        return
                    print("Chain B: Generating Price Quote (local scripts)...", flush=True)
                    try:
                        loop = asyncio.get_event_loop()
                        qf = await loop.run_in_executor(
                            None,
                            lambda: run_pricing_quote(
                                pricing_json,
                                additional_sub_disc=additional_sub_disc,
                                additional_svc_disc=additional_svc_disc,
                                edc_structure=struct_json,
                            )
                        )
                        await asyncio.gather(
                            upload_file(item_id, COL["pricing_quote"],
                                f"{protocol_num}_Quote_Internal_{version}.pdf",  qf["internal_pdf"]),
                            upload_file(item_id, COL["pricing_quote"],
                                f"{protocol_num}_Quote_Client_{version}.pdf",    qf["client_pdf"]),
                            upload_file(item_id, COL["pricing_quote"],
                                f"{protocol_num}_Quote_Internal_{version}.xlsx", qf["internal_xlsx"]),
                            upload_file(item_id, COL["pricing_quote"],
                                f"{protocol_num}_Quote_Client_{version}.xlsx",   qf["client_xlsx"]),
                        )
                        await append_log(item_id, "Price Quote complete — 4 files uploaded.")
                    except Exception as e:
                        print(f"Price Quote error: {e}", flush=True)
                        traceback.print_exc()
                        await append_log(item_id, f"Price Quote error: {e}")
                        raise   # B6: surface failure to chain_b

                # B6: use return_exceptions so gen_ps_pdf failure doesn't cancel
                # gen_quote (or vice versa). If either raised, chain_b re-raises
                # the first exception so chain-level tracking sees the failure.
                sub_results = await asyncio.gather(gen_ps_pdf(), gen_quote(),
                                                    return_exceptions=True)
                sub_errors = [r for r in sub_results if isinstance(r, Exception)]
                if sub_errors:
                    await append_log(item_id,
                        f"Chain B finished with {len(sub_errors)} subtask failure(s).")
                    print(f"Chain B had {len(sub_errors)} failure(s); re-raising first.",
                          flush=True)
                    raise sub_errors[0]
                await append_log(item_id, "Chain B complete.")
                print("Chain B complete.", flush=True)

            # ── Chain C: EDC Build → DVS ──────────────────────────────────────
            build_zip_holder  = [None]   # mutable container for async closure
            build_json_holder = [{"forms": {}}]
            # Event set when chain_c's build is done (or if chain_c is skipped).
            # Chain E waits on this instead of re-triggering a duplicate build
            # when both "study build zip" and "build preview" are selected.
            edc_build_event = asyncio.Event()

            async def chain_c():
                if not _want("study build zip"):
                    edc_build_event.set()   # not running — unblock chain_e
                    return
                print("Chain C: EDC Build starting...", flush=True)
                try:
                    await _run_edc_and_dvs(suppress_uploads=False)
                finally:
                    edc_build_event.set()   # always unblock chain_e
                print("Chain C complete.", flush=True)

            async def _run_edc_and_dvs(suppress_uploads=False):

                if edited_build_zip:
                    # Path B: User uploaded edited XLSForm ZIP
                    print("Path B: using user-uploaded XLSForm ZIP...", flush=True)
                    await append_log(item_id, "Using user-uploaded XLSForm ZIP.")
                    try:
                        build_json_holder[0] = _read_zip_xlsforms(edited_build_zip)
                        build_zip_holder[0]  = edited_build_zip
                    except Exception as e:
                        print(f"Error reading build ZIP: {e}", flush=True)
                        await append_log(item_id, f"Error reading build ZIP: {e}")

                elif edited_dvs_xlsx:
                    # Path C: User uploaded edited DVS → translate to XLSForms
                    print("Path C: translating DVS changes to XLSForms...", flush=True)
                    await append_log(item_id, "Translating DVS input to XLSForm updates.")
                    dvs_text = _dvs_xlsx_to_text(edited_dvs_xlsx)

                    # Claude generates XLSForm JSON directly (no file generation)
                    build_prompt_json = (
                        "You need to generate the XLSForm structure as JSON.\n"
                        "Return a single valid JSON object with this structure:\n"
                        '{"forms": {"<filename>.xlsx": {"survey": [...], "choices": [...], "settings": {...}}}}\n'
                        "No text before or after. No markdown code fences.\n\n"
                    )
                    struct_slim = {
                        "study_meta": struct_json.get("study_meta", {}) if struct_json else {},
                        "forms":      struct_json.get("forms", []) if struct_json else [],
                    }
                    base_build_text = await call_claude(
                        build_prompt_json,
                        extra_text="Study Specification JSON:\n" + json.dumps(struct_slim),
                    )
                    try:
                        base_build = extract_json(base_build_text)
                        if isinstance(base_build, list):
                            base_build = {"forms": {}}
                    except ValueError:
                        base_build = {"forms": {}}

                    updated_text = await call_claude(
                        DVS_TRANSLATE_PROMPT,
                        extra_text=("Current XLSForm JSON:\n" + json.dumps(base_build) +
                                    "\n\nDVS Changes:\n" + dvs_text),
                    )
                    try:
                        build_json_holder[0] = extract_json(updated_text)
                    except ValueError:
                        build_json_holder[0] = base_build
                    build_zip_holder[0] = _xlsform_zip(build_json_holder[0])

                else:
                    # Fresh run: EDC builder (local scripts)
                    print("Chain C: Running edc-builder (local)...", flush=True)
                    try:
                        loop = asyncio.get_event_loop()
                        zip_bytes, edc_log, forms_json = await loop.run_in_executor(
                            None, lambda: run_edc_build(struct_json)
                        )
                        build_zip_holder[0]  = zip_bytes
                        build_json_holder[0] = forms_json
                        # Summarise validation results if present
                        v_results = edc_log.get('validation_results', [])
                        if v_results:
                            n_total  = len(v_results)
                            n_err    = sum(1 for r in v_results if r.get('errors'))
                            n_warn   = sum(1 for r in v_results if r.get('warnings'))
                            n_skip   = sum(1 for r in v_results if r.get('skipped'))
                            v_msg    = (f"validation: {n_total} forms, "
                                        f"{n_err} with errors, {n_warn} with warnings"
                                        + (f", {n_skip} skipped" if n_skip else ""))
                        else:
                            v_msg = "validation: not run"
                        print(f"EDC Build complete — "
                              f"built={len(edc_log.get('forms_built', []))} "
                              f"zip={len(zip_bytes) if zip_bytes else 0} bytes "
                              f"| {v_msg}",
                              flush=True)
                    except Exception as e:
                        print(f"EDC Build error: {e}", flush=True)
                        traceback.print_exc()
                        await append_log(item_id, f"EDC Build error: {e}")
                        raise   # B6: propagate to chain_c

                if build_zip_holder[0] and not suppress_uploads:
                    await upload_file(item_id, COL["edc_build"],
                                      f"{protocol_num}_EDC_Build_{version}.zip",
                                      build_zip_holder[0])
                    await append_log(item_id, "EDC Build complete — ZIP uploaded.")
                elif build_zip_holder[0] and suppress_uploads:
                    print("EDC Build complete (preview-only mode — zip not uploaded)",
                          flush=True)

                # DVS — skip when called as a silent prerequisite for Build Preview
                if suppress_uploads:
                    print("DVS skipped (preview-only mode)", flush=True)
                    return

                print("Chain C: Running DVS (local)...", flush=True)
                try:
                    loop = asyncio.get_event_loop()
                    dvs_xlsx = await loop.run_in_executor(
                        None,
                        lambda: run_dvs_xlsx(
                            struct_json if struct_json
                                else {"study_meta": {"protocol_number": protocol_num}},
                            build_json_holder[0] or {"forms": {}},
                        ),
                    )
                    if dvs_xlsx:
                        await upload_file(item_id, COL["dvs_output"],
                                          f"{protocol_num}_DVS_{version}.xlsx",
                                          dvs_xlsx)
                        await append_log(item_id, "DVS complete.")
                    else:
                        await append_log(item_id, "DVS skipped — builder unavailable.")
                except Exception as e:
                    print(f"DVS error: {e}", flush=True)
                    traceback.print_exc()
                    await append_log(item_id, f"DVS error: {e}")
                    raise   # B6: propagate to chain_c

            # ── Chain D: Create OC Study (parallel, only needs struct_json) ───
            async def chain_d():
                if not (create_study and oc_subdomain and struct_json):
                    if create_study and not oc_subdomain:
                        await append_log(item_id, "Create Study requested but no OC Subdomain — skipped.")
                    return
                env_label = "production" if oc_production else "test"
                await append_log(item_id, f"Creating study in OpenClinica {env_label} ({oc_subdomain})...")
                try:
                    result = await create_oc_study(oc_subdomain, struct_json,
                                                    is_production=oc_production)
                    study_url      = result["study_url"]
                    board_imported = result["board_imported"]
                    board_error    = result.get("board_error", "")
                    await set_text(item_id, COL["oc_study_url"], study_url)
                    if board_imported:
                        await append_log(item_id,
                            f"Study + design board created: {study_url}")
                    else:
                        await append_log(item_id,
                            f"Study shell created: {study_url}  |  "
                            f"Design board import skipped — {board_error}")
                    print(f"Chain D complete: {study_url} "
                          f"(board_imported={board_imported})", flush=True)
                except Exception as e:
                    print(f"OC Study error: {e}", flush=True)
                    await append_log(item_id, f"OC Study creation failed: {e}")
                    raise   # B6: propagate to the chain-outcome tracker

            # ── Chain E: Build Preview PDF (local renderer, no Claude API) ────
            # Consumes the in-memory `struct_json` and the EDC build zip held by
            # `build_zip_holder[0]` (populated by chain_c). If the user only
            # selected "build preview" in the dropdown (chain_c skipped), we
            # trigger _run_edc_and_dvs() inline so the build zip exists before
            # we render. Renderer is fully local — no Claude API calls.
            async def chain_e():
                if not _want("build preview"):
                    return
                if not struct_json:
                    print("Chain E: skipped — no struct_json available", flush=True)
                    return
                # Build Preview needs the EDC zip from chain_c. If chain_c isn't
                # producing one (chain_c gated off), trigger the build inline.
                if not build_zip_holder[0]:
                    if _want("study build zip"):
                        # Chain C is running — wait for it rather than triggering
                        # a duplicate build (which would double-upload the zip/DVS)
                        print("Chain E: waiting for Chain C EDC build…", flush=True)
                        await edc_build_event.wait()
                    else:
                        # Build Preview selected without Study Build ZIP —
                        # run a silent build (no uploads to monday) just to get
                        # the zip bytes needed for rendering.
                        print("Chain E: building EDC zip (preview-only, no upload)…",
                              flush=True)
                        try:
                            await _run_edc_and_dvs(suppress_uploads=True)
                        except Exception as e:
                            print(f"Chain E: EDC build failed: {e}", flush=True)
                            await append_log(item_id,
                                f"Build Preview skipped — EDC build failed: {e}")
                            return
                if not build_zip_holder[0]:
                    print("Chain E: skipped — EDC zip unavailable", flush=True)
                    await append_log(item_id,
                        "Build Preview skipped — no EDC build zip produced.")
                    return

                print("Chain E: rendering Build Preview PDF (local)...",
                      flush=True)
                await append_log(item_id, "Build Preview started.")
                try:
                    from build_preview import render_build_preview_from_spec
                    loop = asyncio.get_event_loop()

                    # Defensive unpack — render returns (pdf_bytes, html_zip_bytes)
                    # Use indexed access to avoid ValueError if shape is unexpected.
                    _render_out = await loop.run_in_executor(
                        None,
                        lambda: render_build_preview_from_spec(
                            struct_json, build_zip_holder[0], protocol_num),
                    )
                    print(f"Chain E: render returned type={type(_render_out).__name__} "
                          f"len={len(_render_out) if hasattr(_render_out,'__len__') else 'N/A'}",
                          flush=True)

                    # Accept tuple, list, or plain bytes (legacy fallback)
                    if isinstance(_render_out, (tuple, list)) and len(_render_out) >= 2:
                        pdf_bytes      = _render_out[0]
                        html_zip_bytes = _render_out[1]
                    elif isinstance(_render_out, (tuple, list)) and len(_render_out) == 1:
                        pdf_bytes      = _render_out[0]
                        html_zip_bytes = b""
                    else:
                        pdf_bytes      = _render_out if isinstance(_render_out, bytes) else b""
                        html_zip_bytes = b""

                    # Upload PDF
                    if pdf_bytes:
                        await upload_file(item_id, COL["build_preview"],
                            f"{protocol_num}_Build_Preview_{version}.pdf",
                            pdf_bytes)

                    # Upload interactive ZIP to the same column if produced
                    if html_zip_bytes:
                        await upload_file(item_id, COL["build_preview"],
                            f"{protocol_num}_Form_Simulator_{version}.zip",
                            html_zip_bytes)

                    await append_log(item_id,
                        f"Build Preview complete — PDF {len(pdf_bytes):,} bytes "
                        + (f"+ Simulator ZIP {len(html_zip_bytes):,} bytes" if html_zip_bytes else ""))
                    print(f"Chain E complete — PDF {len(pdf_bytes):,}b "
                          f"+ ZIP {len(html_zip_bytes):,}b",
                          flush=True)
                except Exception as e:
                    import io as _io
                    _tb_buf = _io.StringIO()
                    traceback.print_exc(file=_tb_buf)
                    _tb_str = _tb_buf.getvalue()
                    # Print each line separately so Railway doesn't truncate
                    for _line in _tb_str.splitlines():
                        print(f"[chain_e_tb] {_line}", flush=True)
                    await append_log(item_id, f"Build Preview error: {e}")

            # ── Launch all four chains in parallel ─────────────────────────────
            # return_exceptions=True prevents one chain's failure from cancelling
            # others. B7: the Study Spec JSON upload runs concurrently here too
            # (saves 1-3s vs blocking before chain launch).
            tasks       = [chain_a(), chain_b(), chain_c(), chain_d(), chain_e()]
            task_names  = ["A", "B", "C", "D", "E"]
            if spec_json_upload_task is not None:
                tasks.append(spec_json_upload_task)
                task_names.append("spec_json_upload")
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # B2: track outcomes so the final status reflects reality.
            failed_chains = []
            for name, result in zip(task_names, results):
                if isinstance(result, Exception):
                    failed_chains.append(name)
                    print(f"Task {name} exception escaped: {result}", flush=True)
                    await append_log(item_id, f"Task {name} error: {result}")

            if failed_chains:
                final_status = STATUS["failed"]
                final_log    = (f"Pipeline finished with errors in chains: "
                                f"{', '.join(failed_chains)}. Check uploaded files "
                                f"and logs above for details.")
            else:
                final_status = STATUS["all_complete"]
                final_log    = "Pipeline complete. All outputs uploaded."

            await asyncio.gather(
                set_status(item_id, COL["pipeline_status"], final_status),
                append_log(item_id, final_log),
            )

    except Exception as e:
        print(f"PIPELINE CRASHED: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        await append_log(item_id, f"PIPELINE ERROR: {e}")
        await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
        raise
