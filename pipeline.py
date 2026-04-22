"""
pipeline.py — oc-ai-pipeline orchestration

Architecture
────────────
  Claude API       → analytical tasks → returns JSON
  File builders    → convert JSON to XLSX / PDF / ZIP locally on this server
  Skill scripts    → pricing-quote scripts generate quote PDFs + XLSXs
  Monday.com API   → input download / output upload

Outputs uploaded per skill:
  EDC Structure  → {protocol}_EDC_Structure.xlsx + {protocol}_EDC_Structure.pdf
  Pricing Model  → {protocol}_Quote_Internal.pdf + {protocol}_Quote_Client.pdf
                   {protocol}_Quote_Internal.xlsx + {protocol}_Quote_Client.xlsx
  EDC Build      → {protocol}_EDC_Build.zip  (one XLSForm .xlsx per CRF form)
  DVS            → {protocol}_DVS.xlsx
"""
import asyncio, io, json, os, sys, tempfile, zipfile
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from monday_client import (get_item, download_file, upload_file, set_status,
                           append_log, set_text, download_column_file, COL)
from claude_client  import call_claude, extract_json
from prompts        import (EDC_STRUCTURE_PROMPT, PRICING_SUMMARY_PROMPT,
                             EDC_BUILD_PROMPT, DVS_PROMPT, DVS_TRANSLATE_PROMPT,
                             SPEC_FROM_BUILD_PROMPT)

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills')

STATUS = {
    "not_started":            "Not Started",
    "edc_structure_running":  "EDC Structure Running",
    "edc_structure_complete": "EDC Structure Complete",
    "build_pricing_running":  "Build + Pricing Running",
    "build_complete":         "Build Complete",
    "pricing_complete":       "Pricing Complete",
    "dvs_running":            "DVS Running",
    "dvs_complete":           "DVS Complete — Awaiting Review",
    "creating_oc_study":      "Creating OC Study",
    "all_complete":           "All Complete",
    "failed":                 "Failed",
}


# ── Shared openpyxl helpers ───────────────────────────────────────────────────

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


# ── EDC Structure → XLSX ──────────────────────────────────────────────────────

def _struct_xlsx(struct_json):
    """Multi-sheet XLSX from EDC structure JSON. Returns bytes."""
    wb = Workbook()

    # Sheet 1: Summary
    ws = wb.active
    ws.title = "Summary"
    meta = struct_json.get("study_meta", {})
    _xl_header_row(ws, ["Field", "Value"])
    _xl_col_widths(ws, [30, 60])
    for k, v in meta.items():
        _xl_data_row(ws, [k.replace("_", " ").title(), str(v)])

    # Sheet 2: Forms
    ws2 = wb.create_sheet("Forms")
    forms = struct_json.get("forms", [])
    _xl_header_row(ws2, ["Form Name", "OID", "Domain", "Visit Schedule", "Notes"])
    _xl_col_widths(ws2, [25, 20, 15, 40, 40])
    for f in forms:
        _xl_data_row(ws2, [
            f.get("name", ""), f.get("oid", ""), f.get("domain", ""),
            str(f.get("visit_schedule", "")), f.get("notes", ""),
        ])

    # Sheet 3: Fields
    ws3 = wb.create_sheet("Fields")
    _xl_header_row(ws3, ["Form", "Field Name", "OID", "Type", "Label",
                          "Codelist", "Required", "Notes"])
    _xl_col_widths(ws3, [20, 20, 20, 12, 35, 20, 10, 30])
    for f in forms:
        for fld in f.get("fields", []):
            _xl_data_row(ws3, [
                f.get("name", ""), fld.get("name", ""), fld.get("oid", ""),
                fld.get("type", ""), fld.get("label", ""), fld.get("codelist", ""),
                "Y" if fld.get("required") else "", fld.get("notes", ""),
            ])

    # Sheet 4: Review Flags
    ws4 = wb.create_sheet("Review Flags")
    _xl_header_row(ws4, ["Category", "Flagged Item"])
    _xl_col_widths(ws4, [30, 80])
    for cat, items in struct_json.get("review_flags", {}).items():
        if isinstance(items, list):
            for item in items:
                _xl_data_row(ws4, [cat.replace("_", " ").title(), str(item)])

    # Sheet 5: Codelists
    ws5 = wb.create_sheet("Codelists")
    _xl_header_row(ws5, ["Codelist", "Code", "Decode"])
    _xl_col_widths(ws5, [30, 20, 50])
    codelists = struct_json.get("codelists", {})
    if isinstance(codelists, list):
        codelists = {}
    for cl_name, entries in codelists.items():
        if isinstance(entries, list):
            for entry in entries:
                _xl_data_row(ws5, [cl_name,
                                   entry.get("code", ""),
                                   entry.get("decode", "")])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── EDC Structure → PDF ───────────────────────────────────────────────────────

def _struct_pdf(struct_json, protocol_num):
    """Readable PDF summary of EDC structure. Returns bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph,
                                    Spacer, Table, TableStyle)

    OC_NAVY = colors.HexColor("#1B3A6B")
    WHITE   = colors.white
    GREY    = colors.HexColor("#F5F5F5")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm,  bottomMargin=2*cm)
    PW = A4[0] - 4*cm

    h1   = ParagraphStyle("h1",   fontName="Helvetica-Bold", fontSize=14,
                           textColor=OC_NAVY, spaceAfter=6)
    h2   = ParagraphStyle("h2",   fontName="Helvetica-Bold", fontSize=10,
                           textColor=OC_NAVY, spaceBefore=10, spaceAfter=4)

    def _tbl(data, col_w):
        t = Table(data, colWidths=col_w, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTNAME",      (0, 0), (-1,  0), "Helvetica-Bold"),
            ("BACKGROUND",    (0, 0), (-1,  0), OC_NAVY),
            ("TEXTCOLOR",     (0, 0), (-1,  0), WHITE),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, GREY]),
            ("GRID",          (0, 0), (-1, -1), 0.3,
             colors.HexColor("#CCCCCC")),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ]))
        return t

    story = []
    meta  = struct_json.get("study_meta", {})

    story.append(Paragraph(
        f"EDC Structure Specification — {protocol_num}", h1))
    story.append(Paragraph("Study Information", h2))
    story.append(_tbl(
        [["Field", "Value"]] + [[k.replace("_"," ").title(), str(v)]
                                 for k, v in meta.items()],
        [PW*0.35, PW*0.65]))
    story.append(Spacer(1, 12))

    forms = struct_json.get("forms", [])
    if forms:
        story.append(Paragraph(f"CRF Forms ({len(forms)} total)", h2))
        story.append(_tbl(
            [["Form Name", "OID", "Domain", "Fields"]] + [
                [f.get("name",""), f.get("oid",""), f.get("domain",""),
                 str(len(f.get("fields",[])))]
                for f in forms],
            [PW*0.35, PW*0.25, PW*0.25, PW*0.15]))
        story.append(Spacer(1, 12))

    flags = struct_json.get("review_flags", {})
    total = sum(len(v) for v in flags.values() if isinstance(v, list))
    if total:
        story.append(Paragraph(f"Review Flags ({total} items)", h2))
        rows = [["Category", "Count", "Items"]]
        for cat, items in flags.items():
            if isinstance(items, list) and items:
                rows.append([
                    cat.replace("_"," ").title(),
                    str(len(items)),
                    ", ".join(str(i) for i in items[:8])
                    + (" …" if len(items) > 8 else ""),
                ])
        story.append(_tbl(rows, [PW*0.28, PW*0.10, PW*0.62]))

    doc.build(story)
    return buf.getvalue()


# ── Pricing Summary → PDF ────────────────────────────────────────────────────

def _pricing_summary_pdf(pricing_json, protocol_num):
    """
    Protocol content summary PDF for internal teams.
    Shows study info, customer segment, duration, modules detected,
    and flag category counts. No dollar amounts.
    Returns bytes.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph,
                                    Spacer, Table, TableStyle, HRFlowable)

    OC_NAVY  = colors.HexColor("#1B3A6B")
    OC_LIGHT = colors.HexColor("#D6E4F0")
    WHITE    = colors.white
    GREY     = colors.HexColor("#F5F5F5")
    GREY_MID = colors.HexColor("#CCCCCC")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm,  bottomMargin=2*cm)
    PW = A4[0] - 4*cm

    h1   = ParagraphStyle("h1",   fontName="Helvetica-Bold", fontSize=14,
                           textColor=OC_NAVY, spaceAfter=4)
    h2   = ParagraphStyle("h2",   fontName="Helvetica-Bold", fontSize=10,
                           textColor=OC_NAVY, spaceBefore=12, spaceAfter=4)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9,
                           leading=13, spaceAfter=3)
    fn   = ParagraphStyle("fn",   fontName="Helvetica-Oblique", fontSize=7.5,
                           textColor=colors.HexColor("#666666"), spaceBefore=4)

    def _tbl(data, col_w):
        t = Table(data, colWidths=col_w, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTNAME",      (0, 0), (-1,  0), "Helvetica-Bold"),
            ("BACKGROUND",    (0, 0), (-1,  0), OC_NAVY),
            ("TEXTCOLOR",     (0, 0), (-1,  0), WHITE),
            ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, GREY]),
            ("GRID",          (0, 0), (-1, -1), 0.3, GREY_MID),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ]))
        return t

    story = []
    meta  = pricing_json.get("study_meta", {})
    flags = pricing_json.get("review_flags", {})
    mods  = pricing_json.get("modules_detected", {})

    # Title
    story.append(Paragraph(
        f"Protocol Summary — {protocol_num}", h1))
    story.append(HRFlowable(width=PW, thickness=2,
                             color=colors.HexColor("#F47920"), spaceAfter=8))

    # Study information
    story.append(Paragraph("Study Information", h2))
    info_rows = [["Field", "Value"]]
    display_fields = [
        ("protocol_number",             "Protocol Number"),
        ("study_title",                 "Study Title"),
        ("sponsor",                     "Sponsor"),
        ("study_phase",                 "Phase"),
        ("indication",                  "Indication"),
        ("customer_segment",            "Customer Segment"),
        ("volume_studies",              "Studies in Contract"),
        ("total_study_duration_months", "Estimated Duration (months)"),
    ]
    for key, label in display_fields:
        val = meta.get(key)
        if val is not None and val != "":
            info_rows.append([label, str(val)])
    story.append(_tbl(info_rows, [PW * 0.38, PW * 0.62]))
    story.append(Spacer(1, 8))

    # Modules detected
    if mods:
        story.append(Paragraph("Modules Detected", h2))
        mod_labels = {
            "is_epro_required":          "ePRO / eCOA (Participate)",
            "is_econsent_required":      "eConsent",
            "is_randomization_required": "Randomization",
        }
        mod_rows = [["Module", "Required"]]
        for key, label in mod_labels.items():
            mod_rows.append([label, "Yes" if mods.get(key) else "No"])
        story.append(_tbl(mod_rows, [PW * 0.70, PW * 0.30]))
        story.append(Spacer(1, 8))

    # Review flags
    total_flags = sum(len(v) for v in flags.values() if isinstance(v, list))
    if total_flags:
        story.append(Paragraph(
            f"Protocol Review Flags — {total_flags} items identified", h2))
        story.append(Paragraph(
            "Items below require specialist review or clarification during EDC build.",
            body))

        flag_rows = [["Category", "Items", "Count"]]
        for cat, items in flags.items():
            if not isinstance(items, list) or not items:
                continue
            flag_rows.append([
                cat.replace("_", " ").title(),
                ", ".join(str(i) for i in items[:6])
                + (" …" if len(items) > 6 else ""),
                str(len(items)),
            ])
        story.append(_tbl(flag_rows, [PW * 0.25, PW * 0.62, PW * 0.13]))
        story.append(Spacer(1, 8))

    # Footer note
    import datetime
    story.append(HRFlowable(width=PW, thickness=1, color=GREY_MID, spaceAfter=4))
    story.append(Paragraph(
        f"Generated {datetime.date.today().strftime('%B %d, %Y')}  ·  "
        f"OpenClinica AI Pipeline  ·  Internal use only — no pricing information",
        fn))

    doc.build(story)
    return buf.getvalue()


# ── EDC Build → ZIP of XLSForm XLSXs ─────────────────────────────────────────

def _xlsform_zip(build_json):
    """
    Convert EDC Build JSON into a ZIP of XLSForm-compliant XLSX files.
    Also includes any CSV files (e.g. schedule of events) if present.

    Expected shape:
      { "forms": { "DM.xlsx": { "survey": [...], "choices": [...],
                                 "settings": {...} },
                   "schedule_of_events.csv": "csv content string" } }
    Returns bytes.
    """
    forms   = build_json.get("forms", {})
    zip_buf = io.BytesIO()

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, form_data in forms.items():

            # ── CSV files (e.g. schedule of events) ──────────────────────────
            if filename.endswith('.csv'):
                if isinstance(form_data, str):
                    zf.writestr(filename, form_data)
                elif isinstance(form_data, list):
                    # List of dicts — convert to CSV
                    import csv as _csv
                    cbuf = io.StringIO()
                    if form_data:
                        writer = _csv.DictWriter(cbuf, fieldnames=form_data[0].keys())
                        writer.writeheader()
                        writer.writerows(form_data)
                    zf.writestr(filename, cbuf.getvalue())
                continue

            # ── XLSForm XLSX files ────────────────────────────────────────────
            wb   = Workbook()
            ws_s = wb.active
            ws_s.title = "survey"

            survey = form_data.get("survey", [])
            if survey:
                hdrs = list(survey[0].keys())
                _xl_header_row(ws_s, hdrs)
                for row in survey:
                    _xl_data_row(ws_s, [row.get(h, "") for h in hdrs])

            ws_c = wb.create_sheet("choices")
            choices = form_data.get("choices", [])
            if choices:
                hdrs = list(choices[0].keys())
                _xl_header_row(ws_c, hdrs)
                for row in choices:
                    _xl_data_row(ws_c, [row.get(h, "") for h in hdrs])

            ws_t = wb.create_sheet("settings")
            settings = form_data.get("settings", {})
            if settings:
                _xl_header_row(ws_t, list(settings.keys()))
                _xl_data_row(ws_t, list(settings.values()))

            xbuf = io.BytesIO()
            wb.save(xbuf)
            zf.writestr(filename, xbuf.getvalue())

        # Also include study_checklist as CSV if present
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


# ── DVS → XLSX ────────────────────────────────────────────────────────────────

def _dvs_xlsx(dvs_json):
    """Convert DVS JSON into an XLSX workbook. Returns bytes."""
    wb     = Workbook()
    checks = dvs_json.get("checks",
             dvs_json.get("validation_checks", []))

    if checks and isinstance(checks, list) and isinstance(checks[0], dict):
        ws = wb.active
        ws.title = "Validation Checks"
        hdrs = list(checks[0].keys())
        _xl_header_row(ws, hdrs)
        _xl_col_widths(ws, [max(12, len(h) + 2) for h in hdrs])
        for row in checks:
            _xl_data_row(ws, [str(row.get(h, "")) for h in hdrs])
    else:
        ws = wb.active
        ws.title = "DVS Raw"
        ws["A1"] = json.dumps(dvs_json, indent=2)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Reverse-engineer spec from build ZIP ─────────────────────────────────────

async def _spec_from_build(build_json, original_struct_json,
                           protocol_num, version, item_id):
    """
    Reverse-engineer a Protocol Specification from built XLSForms.
    Preserves review_flags from the original EDC Structure run.
    Uploads updated spec XLSX + PDF to Monday.
    """
    # Slim down build_json for Claude — just survey and choices
    slim_forms = {}
    for fname, fdata in build_json.get("forms", {}).items():
        if fname.endswith('.csv'):
            continue  # skip schedule of events CSV
        slim_forms[fname] = {
            "survey":  fdata.get("survey", []),
            "choices": fdata.get("choices", []),
            "settings": fdata.get("settings", {}),
        }

    print("Claude — reverse-engineering spec from build...", flush=True)
    spec_text = await call_claude(
        SPEC_FROM_BUILD_PROMPT,
        extra_text = (
            "Study meta (preserve exactly):\n"
            + json.dumps(original_struct_json.get("study_meta", {}))
            + "\n\nXLSForm JSON:\n"
            + json.dumps({"forms": slim_forms})
        ),
    )
    try:
        new_struct = extract_json(spec_text)
        if isinstance(new_struct, list):
            new_struct = {}
    except ValueError:
        new_struct = {}
        print("Warning: spec from build not valid JSON — using original", flush=True)

    # Inject preserved fields from original run
    new_struct["study_meta"]   = original_struct_json.get("study_meta", {})
    new_struct["review_flags"] = original_struct_json.get("review_flags", {})

    # Upload updated spec
    await asyncio.gather(
        upload_file(item_id, COL["spec_xlsx"],
                    f"{protocol_num}_EDC_Structure_{version}.xlsx",
                    _struct_xlsx(new_struct)),
        upload_file(item_id, COL["spec_pdf"],
                    f"{protocol_num}_EDC_Structure_{version}.pdf",
                    _struct_pdf(new_struct, protocol_num)),
    )
    await append_log(item_id,
        "Protocol Specification updated from build — XLSX + PDF uploaded.")
    print("Spec from build uploaded.", flush=True)
    return new_struct


# ── Read user-uploaded XLSForm ZIP ───────────────────────────────────────────

def _read_zip_xlsforms(zip_bytes):
    """
    Read a ZIP of XLSForm XLSX files uploaded by the user.
    Returns the same forms dict structure that Claude generates:
      { "forms": { "filename.xlsx": { "survey": [...], "choices": [...],
                                       "settings": {...} } } }
    Preserves original filenames exactly — no versioning inside ZIP.
    """
    forms = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith('.xlsx') or name.startswith('__'):
                continue
            xlsx_bytes = zf.read(name)
            wb = Workbook()
            # Load the existing workbook using openpyxl load_workbook
            import openpyxl
            src = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
            form_data = {}
            for sheet_name in ['survey', 'choices', 'settings']:
                if sheet_name in src.sheetnames:
                    ws = src[sheet_name]
                    rows = list(ws.values)
                    if not rows:
                        form_data[sheet_name] = []
                        continue
                    headers = [str(h).strip() if h else '' for h in rows[0]]
                    if sheet_name == 'settings':
                        # settings is a single row
                        if len(rows) > 1:
                            form_data[sheet_name] = dict(zip(headers, [
                                str(v) if v is not None else ''
                                for v in rows[1]
                            ]))
                        else:
                            form_data[sheet_name] = {}
                    else:
                        form_data[sheet_name] = [
                            {h: (str(v) if v is not None else '')
                             for h, v in zip(headers, row)}
                            for row in rows[1:]
                            if any(v is not None for v in row)
                        ]
                else:
                    form_data[sheet_name] = [] if sheet_name != 'settings' else {}
            # Use just the basename, no path
            basename = os.path.basename(name)
            forms[basename] = form_data

    print(f"Read {len(forms)} XLSForm(s) from ZIP: {list(forms.keys())}", flush=True)
    return {"forms": forms}


# ── Translate DVS changes into updated XLSForms ───────────────────────────────

def _dvs_xlsx_to_text(dvs_bytes):
    """
    Extract DVS XLSX content as structured text for Claude to read.
    Returns a string representation of the DVS checks.
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(dvs_bytes))
    lines = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
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


async def _translate_dvs_to_xlsforms(dvs_bytes, current_forms_json):
    """
    Option B: Ask Claude to translate DVS changes into updated XLSForm fields.
    Returns updated forms JSON in the same structure as current_forms_json.
    """
    dvs_text = _dvs_xlsx_to_text(dvs_bytes)
    print("Claude — translating DVS changes to XLSForm updates...", flush=True)

    updated_text = await call_claude(
        DVS_TRANSLATE_PROMPT,
        extra_text = (
            "Current XLSForm JSON:\n" + json.dumps(current_forms_json) + "\n\n"
            "DVS Changes (from uploaded DVS XLSX):\n" + dvs_text
        ),
    )
    try:
        updated = extract_json(updated_text)
        if isinstance(updated, list):
            updated = {"forms": {}}
        print(f"DVS translation complete — {len(updated.get('forms', {}))} form(s) updated", flush=True)
        return updated
    except ValueError:
        print("Warning: DVS translation not valid JSON — using original forms", flush=True)
        return current_forms_json


# ── Generate DVS from built forms ─────────────────────────────────────────────

async def _generate_dvs(build_json, protocol_num, version, item_id):
    """
    Generate DVS XLSX from the built XLSForm JSON and upload to Monday.
    Always called after any build path.
    """
    # Strip to only survey rows (constraints, calculations, relevant)
    # to keep token count low
    dvs_forms = {}
    for fname, fdata in build_json.get("forms", {}).items():
        survey = fdata.get("survey", [])
        # Keep only rows that have constraint, calculation, or relevant
        relevant_rows = [
            {k: v for k, v in row.items()
             if k in ('type', 'name', 'label', 'constraint',
                      'constraint_message', 'calculation', 'relevant',
                      'required')}
            for row in survey
            if any(row.get(k) for k in ('constraint', 'calculation', 'relevant'))
        ]
        dvs_forms[fname] = {"survey": relevant_rows}

    dvs_text = await call_claude(
        DVS_PROMPT,
        extra_text = "XLSForm build data:\n" + json.dumps({"forms": dvs_forms}),
    )
    try:
        dvs_json = extract_json(dvs_text)
        if isinstance(dvs_json, list):
            dvs_json = {"checks": dvs_json}
    except ValueError:
        dvs_json = {"checks": []}
        print("Warning: DVS not valid JSON", flush=True)

    await upload_file(item_id, COL["dvs_output"],
                      f"{protocol_num}_DVS_{version}.xlsx",
                      _dvs_xlsx(dvs_json))
    await append_log(item_id, "DVS complete — XLSX uploaded.")
    print("DVS uploaded.", flush=True)


# ── Pricing model — run scripts locally ───────────────────────────────────────

def _add_scripts(skill_name):
    path = os.path.join(SKILLS_DIR, skill_name, "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)


def run_pricing_model(pricing_summary_dict, live_rates=None,
                      additional_sub_disc=0.0, additional_svc_disc=0.0):
    """Run pricing-quote scripts locally. Returns dict of file bytes."""
    _add_scripts("pricing-quote")
    from pricing_engine      import calculate_quote
    from generate_quote_pdf  import build_quote_pdfs
    from generate_quote_xlsx import build_quote_xlsx

    quote    = calculate_quote(pricing_summary_dict, live_rates=live_rates,
                               additional_sub_disc=additional_sub_disc,
                               additional_svc_disc=additional_svc_disc)
    protocol = quote["study_meta"].get("protocol_number", "STUDY")

    with tempfile.TemporaryDirectory() as tmp:
        paths = {
            "internal_pdf":  os.path.join(tmp, f"{protocol}_Quote_Internal.pdf"),
            "client_pdf":    os.path.join(tmp, f"{protocol}_Quote_Client.pdf"),
            "internal_xlsx": os.path.join(tmp, f"{protocol}_Quote_Internal.xlsx"),
            "client_xlsx":   os.path.join(tmp, f"{protocol}_Quote_Client.xlsx"),
        }
        build_quote_pdfs(quote, paths["internal_pdf"],  paths["client_pdf"])
        build_quote_xlsx(quote, paths["internal_xlsx"], paths["client_xlsx"])
        return {k: open(v, "rb").read() for k, v in paths.items()}


# ── OpenClinica Study Service API ─────────────────────────────────────────────

async def create_oc_study(subdomain, struct_json):
    """
    Create a study in OpenClinica via the Study Service API.

    Auth:   HTTP Basic Auth — OC_API_USERNAME / OC_API_PASSWORD env vars
    Host:   https://{subdomain}.build.openclinica.io
    Endpoint: POST /study-service/api/studies

    Returns the study URL string on success, or raises on failure.
    """
    import base64 as _b64
    import httpx
    username = os.environ.get("OC_API_USERNAME", "").strip()
    password = os.environ.get("OC_API_PASSWORD", "").strip()
    if not username or not password:
        raise ValueError("OC_API_USERNAME or OC_API_PASSWORD not set in environment")

    credentials = _b64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type":  "application/json",
    }

    base_url = f"https://{subdomain}.build.openclinica.io"
    endpoint = f"{base_url}/study-service/api/studies"

    meta = struct_json.get("study_meta", {})

    # Map study type from protocol — default to INTERVENTIONAL
    type_map = {
        "interventional": "INTERVENTIONAL",
        "observational":  "OBSERVATIONAL",
    }
    study_type = type_map.get(
        str(meta.get("type", "interventional")).lower(), "INTERVENTIONAL"
    )

    # Map phase — OC4 accepted values
    phase_map = {
        "phase i":   "PHASEI",   "phase 1":   "PHASEI",
        "phase ii":  "PHASEII",  "phase 2":   "PHASEII",
        "phase iii": "PHASEIII", "phase 3":   "PHASEIII",
        "phase iv":  "PHASEIV",  "phase 4":   "PHASEIV",
    }
    phase_raw = str(meta.get("study_phase", "")).lower().strip()
    phase = phase_map.get(phase_raw, "OTHER_NON_IND")

    import datetime as _dt
    today      = _dt.date.today().isoformat()
    dur_months = int(meta.get("total_study_duration_months", 24) or 24)
    end_date   = (
        _dt.date.today().replace(year=_dt.date.today().year + dur_months // 12)
    ).isoformat()

    protocol_num = meta.get("protocol_number", "STUDY")

    payload = {
        "name":               meta.get("study_title", protocol_num),
        "description":        meta.get("description",
                                       f"{protocol_num} — {meta.get('indication', '')}"),
        "uniqueIdentifier":   protocol_num[:30],   # max 30 chars
        "type":               study_type,
        "phase":              phase,
        "expectedStartDate":  today,
        "expectedEndDate":    end_date,
        "expectedEnrollment": int(meta.get("expected_enrollment", 0) or 0),
        "collectSex":         True,
        "collectDateOfBirth": "ONLY_THE_YEAR",
        "collectPersonId":    "ALWAYS",
    }

    print(f"Creating OC study at {endpoint}", flush=True)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(endpoint, headers=headers, json=payload)

    print(f"OC Study API response: {r.status_code} {r.text[:300]}", flush=True)

    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"OC Study API returned {r.status_code}: {r.text[:300]}"
        )

    data = r.json()
    study_uuid = data.get("uuid", "")
    study_url  = f"{base_url}/designer/#/studies/{study_uuid}" if study_uuid else base_url
    return study_url


# ── Main pipeline ──────────────────────────────────────────────────────────────

async def run_pipeline(item_id):
    try:

        # ── 1. Fetch item + download protocol PDF ─────────────────────────────
        item         = await get_item(item_id)
        cols         = {c["id"]: c for c in item["column_values"]}
        protocol_num = cols.get(COL["protocol_number"], {}).get("text", "STUDY")
        crf_url      = cols.get(COL["crf_library"],     {}).get("text")
        oc_std_url   = cols.get(COL["oc_standard"],     {}).get("text")

        # Version identifier — auto-generated from run timestamp V{MMDD}.{HHMM}
        import datetime as _dt
        _now    = _dt.datetime.utcnow()
        version = f"V{_now.strftime('%m%d')}.{_now.strftime('%H%M')}"
        print(f"Run version: {version}", flush=True)
        oc_subdomain = cols.get(COL["oc_subdomain"],    {}).get("text", "").strip()

        # Number/percent columns — convert from display % to decimal
        def _pct(col_key):
            raw = cols.get(COL[col_key], {}).get("text", "").strip()
            try:
                return float(raw) / 100.0 if raw else 0.0
            except ValueError:
                return 0.0

        additional_sub_disc = _pct("subscription_discount")
        additional_svc_disc = _pct("services_discount")

        # Multi-select dropdown — read text field which is comma-separated labels
        output_raw = cols.get(COL["output_requested"], {}).get("text", "") or ""
        output_selections = {s.strip().lower() for s in output_raw.split(",") if s.strip()}
        # If nothing selected, run everything (backwards compatible)
        run_all = len(output_selections) == 0
        def _want(label):
            return run_all or label.lower() in output_selections
        print(f"Output requested: {output_raw!r} | run_all={run_all}", flush=True)

        create_study_val = cols.get(COL["create_study"], {}).get("value")
        try:
            parsed = json.loads(create_study_val or "{}")
            if isinstance(parsed, bool):
                create_study = parsed
            elif isinstance(parsed, dict):
                create_study = bool(parsed.get("checked", False))
            else:
                create_study = False
        except Exception:
            create_study = False

        print(f"Protocol: {protocol_num}", flush=True)
        print(f"Create OC Study raw value: {create_study_val!r}", flush=True)
        print(f"Create OC Study: {create_study} | Subdomain: {oc_subdomain}", flush=True)

        from monday_client import get_asset_url
        protocol_pdf = b""
        assets = await get_asset_url(item_id)
        for asset in assets:
            if (asset.get("name") or "").lower().endswith(".pdf"):
                url = asset.get("public_url") or asset.get("url")
                if url:
                    protocol_pdf = await download_file(url)
                if protocol_pdf:
                    break
        print(f"Protocol PDF: {len(protocol_pdf)} bytes", flush=True)

        crf_pdf     = await download_file(crf_url)    if crf_url    else None
        oc_std_xlsx = await download_file(oc_std_url) if oc_std_url else None

        # ── 2. EDC Structure → XLSX + PDF ────────────────────────────────────
        await set_status(item_id, COL["pipeline_status"], STATUS["edc_structure_running"])
        await append_log(item_id, "EDC Structure started.")
        print("Claude — EDC Structure...", flush=True)

        struct_text = await call_claude(
            EDC_STRUCTURE_PROMPT,
            pdf_bytes  = protocol_pdf or None,
            extra_text = "Customer CRF library is attached." if crf_pdf else None,
        )
        try:
            struct_json = extract_json(struct_text)
            if isinstance(struct_json, list):
                struct_json = {"study_meta": {"protocol_number": protocol_num},
                               "forms": struct_json, "review_flags": {}}
        except ValueError:
            struct_json = {"study_meta": {"protocol_number": protocol_num},
                           "forms": [], "review_flags": {}}
            print("Warning: EDC Structure not valid JSON", flush=True)

        # EDC Structure always runs — it feeds all downstream steps
        # Files only uploaded if "Protocol specification" is selected
        if _want("protocol specification"):
            await asyncio.gather(
                upload_file(item_id, COL["spec_xlsx"],
                            f"{protocol_num}_EDC_Structure_{version}.xlsx",
                            _struct_xlsx(struct_json)),
                upload_file(item_id, COL["spec_pdf"],
                            f"{protocol_num}_EDC_Structure_{version}.pdf",
                            _struct_pdf(struct_json, protocol_num)),
            )
        else:
            print("Protocol specification not requested — skipping upload.", flush=True)

        await set_status(item_id, COL["pipeline_status"], STATUS["edc_structure_complete"])
        await append_log(item_id, "EDC Structure complete — XLSX + PDF uploaded.")

        # ── 3. Protocol Summary ───────────────────────────────────────────────
        pricing_json = {"study_meta": {"protocol_number": protocol_num}}
        if _want("protocol summary"):
            await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
            await append_log(item_id, "Protocol Summary started.")
            print("Claude — Protocol Summary...", flush=True)

            struct_summary = {
                "study_meta":   struct_json.get("study_meta", {}),
                "review_flags": struct_json.get("review_flags", {}),
                "forms":        [{"name": f.get("name"), "domain": f.get("domain")}
                                 for f in struct_json.get("forms", [])],
            }

            pricing_text = await call_claude(
                PRICING_SUMMARY_PROMPT,
                extra_text = "EDC Structure JSON:\n" + json.dumps(struct_summary),
            )
            try:
                pricing_json = extract_json(pricing_text)
                if isinstance(pricing_json, list):
                    pricing_json = {"study_meta": {"protocol_number": protocol_num}}
            except ValueError:
                pricing_json = {"study_meta": {"protocol_number": protocol_num}}
                print("Warning: Protocol Summary not valid JSON", flush=True)

            await upload_file(item_id, COL["pricing_summary"],
                              f"{protocol_num}_Protocol_Summary_{version}.pdf",
                              _pricing_summary_pdf(pricing_json, protocol_num))
            await append_log(item_id, "Protocol Summary PDF uploaded.")
        else:
            print("Protocol summary not requested — skipping.", flush=True)

        # ── 4. Price quote → Internal + Client PDF + XLSX ────────────────────
        if _want("price quote"):
            await append_log(item_id, "Price quote started.")
            print("Pricing Model scripts...", flush=True)
            try:
                qf = run_pricing_model(pricing_json,
                                       additional_sub_disc=additional_sub_disc,
                                       additional_svc_disc=additional_svc_disc)
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
                await append_log(item_id, "Price quote complete — 4 files uploaded.")
            except Exception as e:
                print(f"Price quote error: {e}", flush=True)
                await append_log(item_id, f"Price quote error: {e}")
            await set_status(item_id, COL["pipeline_status"], STATUS["pricing_complete"])
        else:
            print("Price quote not requested — skipping.", flush=True)

        # ── 5. Study build ZIP + DVS (always together) ───────────────────────
        build_json = {"forms": {}}
        if _want("study build zip"):
            await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
            await append_log(item_id, "EDC Build started.")

            # Check for user-uploaded inputs
            build_input_bytes = await download_column_file(item_id, COL["build_input"])
            dvs_input_bytes   = await download_column_file(item_id, COL["dvs_input"])

            if build_input_bytes:
                # ── Path A: User uploaded edited XLSForm ZIP ──────────────
                print("Build path A: reading user-uploaded XLSForm ZIP...", flush=True)
                await append_log(item_id, "Using user-uploaded XLSForm ZIP for build.")
                try:
                    build_json = _read_zip_xlsforms(build_input_bytes)
                except Exception as e:
                    print(f"Error reading build input ZIP: {e}", flush=True)
                    await append_log(item_id, f"Error reading build input ZIP: {e}")
                    build_json = {"forms": {}}

            elif dvs_input_bytes:
                # ── Path B: User uploaded edited DVS — translate to XLSForms
                print("Build path B: translating DVS changes to XLSForms...", flush=True)
                await append_log(item_id, "Translating DVS input to XLSForm updates.")
                # Start from Claude's last EDC build as base forms
                struct_slim = {
                    "study_meta": struct_json.get("study_meta", {}),
                    "forms":      struct_json.get("forms", []),
                }
                base_build_text = await call_claude(
                    EDC_BUILD_PROMPT,
                    extra_text = "EDC Structure JSON:\n" + json.dumps(struct_slim),
                )
                try:
                    base_build = extract_json(base_build_text)
                    if isinstance(base_build, list):
                        base_build = {"forms": {}}
                except ValueError:
                    base_build = {"forms": {}}
                build_json = await _translate_dvs_to_xlsforms(dvs_input_bytes, base_build)

            else:
                # ── Path C: No user input — Claude builds from protocol spec
                print("Build path C: Claude EDC Build from protocol spec...", flush=True)
                await append_log(item_id, "EDC Build: generating from protocol specification.")
                struct_slim = {
                    "study_meta": struct_json.get("study_meta", {}),
                    "forms":      struct_json.get("forms", []),
                }
                build_text = await call_claude(
                    EDC_BUILD_PROMPT,
                    extra_text = "EDC Structure JSON:\n" + json.dumps(struct_slim),
                )
                try:
                    build_json = extract_json(build_text)
                    if isinstance(build_json, list):
                        build_json = {"forms": {f"form_{i+1}.xlsx": item
                                                for i, item in enumerate(build_json)}}
                except ValueError:
                    build_json = {"forms": {}}
                    print("Warning: EDC Build not valid JSON", flush=True)

            # Upload ZIP (versioned name, original filenames inside)
            await upload_file(item_id, COL["edc_build"],
                              f"{protocol_num}_EDC_Build_{version}.zip",
                              _xlsform_zip(build_json))
            await set_status(item_id, COL["pipeline_status"], STATUS["build_complete"])
            await append_log(item_id, "EDC Build complete — ZIP uploaded.")

            # If protocol specification also selected, reverse-engineer
            # updated spec from the built XLSForms
            if _want("protocol specification"):
                await append_log(item_id,
                    "Updating Protocol Specification from build...")
                struct_json = await _spec_from_build(
                    build_json, struct_json, protocol_num, version, item_id)

            # DVS always runs after any build path
            await set_status(item_id, COL["pipeline_status"], STATUS["dvs_running"])
            await append_log(item_id, "DVS started.")
            print("Claude — DVS...", flush=True)
            await _generate_dvs(build_json, protocol_num, version, item_id)
            await set_status(item_id, COL["pipeline_status"], STATUS["dvs_complete"])

        else:
            print("Study build ZIP not requested — skipping.", flush=True)

        # ── 6. Create study in OpenClinica (if requested) ─────────────────────
        if create_study and oc_subdomain:
            await set_status(item_id, COL["pipeline_status"], STATUS["creating_oc_study"])
            await append_log(item_id, f"Creating study in OpenClinica ({oc_subdomain})...")
            print(f"Creating OC study on {oc_subdomain}...", flush=True)
            try:
                study_url = await create_oc_study(oc_subdomain, struct_json)
                await set_text(item_id, COL["oc_study_url"], study_url)
                await append_log(item_id,
                    f"Study created in OpenClinica. URL: {study_url}")
                print(f"OC Study created: {study_url}", flush=True)
            except Exception as e:
                print(f"OC Study creation error: {e}", flush=True)
                await append_log(item_id, f"OC Study creation failed: {e}")
        elif create_study and not oc_subdomain:
            await append_log(item_id,
                "Create Study requested but no OC Subdomain provided — skipped.")
        else:
            await append_log(item_id, "Create Study in OC not requested — skipped.")


        # ── Done ──────────────────────────────────────────────────────────────
        await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
        await append_log(item_id, "Pipeline complete. All outputs uploaded.")

    except Exception as e:
        import traceback
        print(f"PIPELINE CRASHED: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        await append_log(item_id, f"PIPELINE ERROR: {e}")
        await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
        raise
