"""
pipeline.py — oc-ai-pipeline orchestration

Architecture
────────────
  Claude API       → analytical tasks → returns JSON
  File builders    → convert JSON to XLSX / PDF / ZIP locally on this server
  Skill scripts    → pricing-model scripts generate quote PDFs + XLSXs
  Monday.com API   → input download / output upload

Outputs uploaded per skill:
  EDC Structure  → {protocol}_EDC_Structure.xlsx + {protocol}_EDC_Structure.pdf
  Pricing Model  → {protocol}_Quote_Internal.pdf + {protocol}_Quote_Client.pdf
                   {protocol}_Quote_Internal.xlsx + {protocol}_Quote_Client.xlsx
  EDC Build      → {protocol}_EDC_Build.zip  (one XLSForm .xlsx per CRF form)
  DVS            → {protocol}_DVS.xlsx
"""
import asyncio, io, json, os, sys, tempfile, zipfile

from monday_client import get_item, download_file, upload_file, set_status, append_log, set_text, COL
from claude_client  import call_claude, extract_json
from prompts        import (EDC_STRUCTURE_PROMPT, PRICING_SUMMARY_PROMPT,
                             EDC_BUILD_PROMPT, DVS_PROMPT)

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
    from openpyxl.styles import Font, PatternFill, Alignment
    fill = PatternFill("solid", fgColor=bg)
    font = Font(name="Arial", bold=True, color=fg, size=10)
    aln  = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font, cell.fill, cell.alignment = font, fill, aln

def _xl_data_row(ws, values, bold=False):
    from openpyxl.styles import Font, Alignment
    ws.append(values)
    for cell in ws[ws.max_row]:
        cell.font = Font(name="Arial", bold=bold, size=9)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

def _xl_col_widths(ws, widths):
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


# ── EDC Structure → XLSX ──────────────────────────────────────────────────────

def _struct_xlsx(struct_json):
    """Multi-sheet XLSX from EDC structure JSON. Returns bytes."""
    from openpyxl import Workbook
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
    for cl_name, entries in struct_json.get("codelists", {}).items():
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
        f"Protocol Pricing Summary — {protocol_num}", h1))
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
    Expected shape: { "forms": { "DM.xlsx": { "survey": [...], "choices": [...],
                                               "settings": {...} } } }
    Returns bytes.
    """
    from openpyxl import Workbook
    forms   = build_json.get("forms", {})
    zip_buf = io.BytesIO()

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, form_data in forms.items():
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

    zip_buf.seek(0)
    return zip_buf.getvalue()


# ── DVS → XLSX ────────────────────────────────────────────────────────────────

def _dvs_xlsx(dvs_json):
    """Convert DVS JSON into an XLSX workbook. Returns bytes."""
    from openpyxl import Workbook
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


# ── Pricing model — run scripts locally ───────────────────────────────────────

def _add_scripts(skill_name):
    path = os.path.join(SKILLS_DIR, skill_name, "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)


def run_pricing_model(pricing_summary_dict, live_rates=None):
    """Run pricing-model scripts locally. Returns dict of file bytes."""
    _add_scripts("pricing-model")
    from pricing_engine      import calculate_quote
    from generate_quote_pdf  import build_quote_pdfs
    from generate_quote_xlsx import build_quote_xlsx

    quote    = calculate_quote(pricing_summary_dict, live_rates=live_rates)
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
        oc_subdomain = cols.get(COL["oc_subdomain"],    {}).get("text", "").strip()

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
        for asset in await get_asset_url(item_id):
            if (asset.get("name") or "").lower().endswith(".pdf"):
                for fa in await get_asset_url(item_id):
                    if fa.get("id") == asset.get("id"):
                        url = fa.get("public_url") or fa.get("url")
                        if url:
                            protocol_pdf = await download_file(url)
                        break
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

        await upload_file(item_id, COL["spec_xlsx"],
                          f"{protocol_num}_EDC_Structure.xlsx",
                          _struct_xlsx(struct_json))
        await upload_file(item_id, COL["spec_pdf"],
                          f"{protocol_num}_EDC_Structure.pdf",
                          _struct_pdf(struct_json, protocol_num))

        await set_status(item_id, COL["pipeline_status"], STATUS["edc_structure_complete"])
        await append_log(item_id, "EDC Structure complete — XLSX + PDF uploaded.")
        await asyncio.sleep(15)

        # ── 3. Pricing Summary (feeds Pricing Model — not uploaded) ───────────
        await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
        await append_log(item_id, "Pricing Summary started.")
        print("Claude — Pricing Summary...", flush=True)

        pricing_text = await call_claude(
            PRICING_SUMMARY_PROMPT,
            pdf_bytes  = protocol_pdf or None,
            extra_text = "EDC Structure JSON:\n" + json.dumps(struct_json),
        )
        try:
            pricing_json = extract_json(pricing_text)
            if isinstance(pricing_json, list):
                pricing_json = {"study_meta": {"protocol_number": protocol_num}}
        except ValueError:
            pricing_json = {"study_meta": {"protocol_number": protocol_num}}
            print("Warning: Pricing Summary not valid JSON", flush=True)

        # Upload Pricing Summary PDF
        await upload_file(item_id, COL["pricing_summary"],
                          f"{protocol_num}_Pricing_Summary.pdf",
                          _pricing_summary_pdf(pricing_json, protocol_num))
        await append_log(item_id, "Pricing Summary PDF uploaded.")

        # ── 4. Pricing Model → Internal + Client PDF + XLSX ───────────────────
        await append_log(item_id, "Pricing Model started.")
        print("Pricing Model scripts...", flush=True)
        try:
            qf = run_pricing_model(pricing_json)
            await upload_file(item_id, COL["pricing_quote"],
                              f"{protocol_num}_Quote_Internal.pdf",  qf["internal_pdf"])
            await upload_file(item_id, COL["pricing_quote"],
                              f"{protocol_num}_Quote_Client.pdf",    qf["client_pdf"])
            await upload_file(item_id, COL["pricing_quote"],
                              f"{protocol_num}_Quote_Internal.xlsx", qf["internal_xlsx"])
            await upload_file(item_id, COL["pricing_quote"],
                              f"{protocol_num}_Quote_Client.xlsx",   qf["client_xlsx"])
            await append_log(item_id, "Pricing complete — 4 files uploaded.")
        except Exception as e:
            print(f"Pricing Model error: {e}", flush=True)
            await append_log(item_id, f"Pricing Model error: {e}")

        await set_status(item_id, COL["pipeline_status"], STATUS["pricing_complete"])
        await asyncio.sleep(15)

        # ── 5. EDC Build → ZIP of XLSForm XLSXs ──────────────────────────────
        await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
        await append_log(item_id, "EDC Build started.")
        print("Claude — EDC Build...", flush=True)

        build_text = await call_claude(
            EDC_BUILD_PROMPT,
            extra_text = "EDC Structure JSON:\n" + json.dumps(struct_json),
        )
        try:
            build_json = extract_json(build_text)
            if isinstance(build_json, list):
                build_json = {"forms": {f"form_{i+1}.xlsx": item
                                        for i, item in enumerate(build_json)}}
        except ValueError:
            build_json = {"forms": {}}
            print("Warning: EDC Build not valid JSON", flush=True)

        await upload_file(item_id, COL["edc_build"],
                          f"{protocol_num}_EDC_Build.zip",
                          _xlsform_zip(build_json))

        await set_status(item_id, COL["pipeline_status"], STATUS["build_complete"])
        await append_log(item_id, "EDC Build complete — ZIP uploaded.")
        await asyncio.sleep(15)

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

        # ── 6. DVS → XLSX ─────────────────────────────────────────────────────
        await set_status(item_id, COL["pipeline_status"], STATUS["dvs_running"])
        await append_log(item_id, "DVS started.")
        print("Claude — DVS...", flush=True)

        dvs_text = await call_claude(
            DVS_PROMPT,
            extra_text = (
                "EDC Structure JSON:\n" + json.dumps(struct_json) + "\n\n"
                + "EDC Build JSON:\n"   + json.dumps(build_json)
            ),
        )
        try:
            dvs_json = extract_json(dvs_text)
            if isinstance(dvs_json, list):
                dvs_json = {"checks": dvs_json}
        except ValueError:
            dvs_json = {"checks": []}
            print("Warning: DVS not valid JSON", flush=True)

        await upload_file(item_id, COL["dvs_output"],
                          f"{protocol_num}_DVS.xlsx",
                          _dvs_xlsx(dvs_json))

        await set_status(item_id, COL["pipeline_status"], STATUS["dvs_complete"])
        await append_log(item_id, "DVS complete — XLSX uploaded.")

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
