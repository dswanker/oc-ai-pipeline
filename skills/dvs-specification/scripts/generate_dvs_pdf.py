"""
generate_dvs_pdf.py — DVS Specification PDF Generator
Builds a curated audit-ready PDF from a dvs_data dict.

The PDF is a sign-off / audit artifact, NOT a dump of all 30 DVS columns.
It contains:
  Cover page
  Section 1: DVS Summary (check counts by type / severity / priority)
  Section 2: Protocol Extraction (traceability table)
  Section 3: DVS Checks (curated 9-column view of DVS_OC4)
  Section 4: Query Text Library
  Section 5: UAT Summary (case counts + pass rate if tests run)
  Section 6: Calendaring Rules Summary
  Approval Block

Usage:
    from generate_dvs_pdf import build_dvs_pdf
    build_dvs_pdf(dvs_data, output_path)
"""

import datetime
from collections import Counter

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, NextPageTemplate,
    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

# ── Brand colours (match generate_dvs.py) ────────────────────────────────────
DARK_BLUE   = colors.HexColor("#1B3A6B")
MID_BLUE    = colors.HexColor("#2E6DA4")
LIGHT_BLUE  = colors.HexColor("#D6E4F0")
WHITE       = colors.white
GREY_LIGHT  = colors.HexColor("#F5F5F5")
GREY_MID    = colors.HexColor("#CCCCCC")
AMBER       = colors.HexColor("#FFF3CD")
GREEN_LIGHT = colors.HexColor("#D5F5E3")
RED_LIGHT   = colors.HexColor("#FADBD8")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
BODY_W = PAGE_W - 2 * MARGIN

# ── Styles ────────────────────────────────────────────────────────────────────
_base = getSampleStyleSheet()


def _style(name, parent="Normal", **kw):
    return ParagraphStyle(name, parent=_base[parent], **kw)


S = {
    "cover_title":  _style("cover_title",  "Normal", fontSize=26, textColor=WHITE,
                            fontName="Helvetica-Bold", alignment=TA_LEFT, leading=30),
    "cover_sub":    _style("cover_sub",    "Normal", fontSize=12, textColor=LIGHT_BLUE,
                            fontName="Helvetica", alignment=TA_LEFT, leading=16),
    "cover_meta":   _style("cover_meta",   "Normal", fontSize=9,  textColor=LIGHT_BLUE,
                            fontName="Helvetica", alignment=TA_LEFT, leading=13),
    "section_hdr":  _style("section_hdr",  "Normal", fontSize=13, textColor=WHITE,
                            fontName="Helvetica-Bold", alignment=TA_LEFT,
                            leading=16, spaceBefore=4, spaceAfter=2),
    "sub_hdr":      _style("sub_hdr",      "Normal", fontSize=9,  textColor=DARK_BLUE,
                            fontName="Helvetica-Bold", leading=12,
                            spaceBefore=6, spaceAfter=2),
    "body":         _style("body",         "Normal", fontSize=8,  textColor=colors.black,
                            fontName="Helvetica", leading=11, spaceAfter=2),
    "body_small":   _style("body_small",   "Normal", fontSize=7,  textColor=colors.black,
                            fontName="Helvetica", leading=9),
    "cell":         _style("cell",         "Normal", fontSize=7,  textColor=colors.black,
                            fontName="Helvetica", leading=9, wordWrap="CJK"),
    "cell_hdr":     _style("cell_hdr",     "Normal", fontSize=7,  textColor=WHITE,
                            fontName="Helvetica-Bold", leading=9, alignment=TA_LEFT),
    "cell_bold":    _style("cell_bold",    "Normal", fontSize=7,  textColor=colors.black,
                            fontName="Helvetica-Bold", leading=9),
    "footer":       _style("footer",       "Normal", fontSize=6,  textColor=GREY_MID,
                            fontName="Helvetica", alignment=TA_CENTER),
    "approval_lbl": _style("approval_lbl", "Normal", fontSize=8,  textColor=DARK_BLUE,
                            fontName="Helvetica-Bold", leading=11),
    "approval_val": _style("approval_val", "Normal", fontSize=8,  textColor=colors.black,
                            fontName="Helvetica", leading=11),
}


# ── Page templates ────────────────────────────────────────────────────────────

def _make_doc(output_path, meta):
    """Return a BaseDocTemplate with Cover and Body page templates."""
    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=16 * mm,
        title=f"DVS — {meta.get('protocol_number', '')}",
        author="OpenClinica AI Pipeline",
    )

    def _header_footer(canvas, doc, is_cover=False):
        canvas.saveState()
        if not is_cover:
            # Top bar
            canvas.setFillColor(DARK_BLUE)
            canvas.rect(0, PAGE_H - 10 * mm, PAGE_W, 10 * mm, fill=1, stroke=0)
            canvas.setFillColor(WHITE)
            canvas.setFont("Helvetica-Bold", 7)
            canvas.drawString(MARGIN, PAGE_H - 6.5 * mm,
                              f"DVS  |  {meta.get('protocol_number', '')}  "
                              f"|  {meta.get('study_id', '')}")
            canvas.setFont("Helvetica", 7)
            canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 6.5 * mm,
                                   meta.get("build_date", ""))
            # Footer
            canvas.setFillColor(GREY_MID)
            canvas.rect(0, 0, PAGE_W, 8 * mm, fill=1, stroke=0)
            canvas.setFillColor(colors.HexColor("#666666"))
            canvas.setFont("Helvetica", 6)
            canvas.drawCentredString(PAGE_W / 2, 2.5 * mm,
                                     f"Page {doc.page}  |  CONFIDENTIAL — FOR INTERNAL USE ONLY  "
                                     f"|  OpenClinica 4 Data Validation Specification")
        canvas.restoreState()

    cover_frame = Frame(MARGIN, MARGIN, BODY_W, PAGE_H - 2 * MARGIN, id="cover")
    body_frame  = Frame(MARGIN, 10 * mm, BODY_W,
                        PAGE_H - MARGIN - 18 * mm, id="body")

    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame],
                     onPage=lambda c, d: _header_footer(c, d, is_cover=True)),
        PageTemplate(id="Body",  frames=[body_frame],
                     onPage=_header_footer),
    ])
    return doc


# ── Helper: coloured section heading ─────────────────────────────────────────

def _section_heading(title, n):
    """Return a Table that renders as a coloured section header bar."""
    label = f"Section {n}  —  {title}"
    t = Table([[Paragraph(label, S["section_hdr"])]], colWidths=[BODY_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK_BLUE),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [DARK_BLUE]),
    ]))
    return t


def _hr():
    return HRFlowable(width="100%", thickness=0.5, color=GREY_MID,
                      spaceAfter=4, spaceBefore=4)


# ── Helper: generic data table ────────────────────────────────────────────────

def _make_table(headers, rows_data, col_widths, row_colors=None):
    """
    Build a styled Platypus Table.
    headers: list of str
    rows_data: list of lists (already Paragraphs or strings)
    col_widths: list of floats (mm or points)
    row_colors: optional list of colors per data row (len == len(rows_data))
    """
    hdr_row = [Paragraph(h, S["cell_hdr"]) for h in headers]
    table_data = [hdr_row] + rows_data

    style = [
        # Header
        ("BACKGROUND",   (0, 0), (-1, 0), DARK_BLUE),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [GREY_LIGHT, WHITE]),
        ("GRID",         (0, 0), (-1, -1), 0.3, GREY_MID),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
    ]
    if row_colors:
        for i, rc in enumerate(row_colors):
            if rc is not None:
                style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), rc))

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle(style))
    return t


def _p(text, style="body"):
    return Paragraph(str(text) if text is not None else "", S[style])


def _placeholder_color(val):
    if val and "[PLACEHOLDER" in str(val).upper():
        return AMBER
    return None


# ── Section builders ──────────────────────────────────────────────────────────

def _build_cover(meta, story):
    today = meta.get("build_date", datetime.date.today().isoformat())
    protocol = meta.get("protocol_number", "[Protocol]")
    study_id = meta.get("study_id", "")
    review_status = meta.get("review_status", "PENDING REVIEW")

    # Dark blue cover panel
    cover_table = Table(
        [[
            Paragraph("DATA VALIDATION SPECIFICATION", S["cover_title"]),
        ]],
        colWidths=[BODY_W],
    )
    cover_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), DARK_BLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
        ("LEFTPADDING",   (0, 0), (-1, -1), 14),
    ]))
    story.append(cover_table)
    story.append(Spacer(1, 6 * mm))

    # Meta block
    meta_rows = [
        ("Protocol Number", protocol),
        ("Study ID",         study_id),
        ("Generated Date",   today),
        ("Review Status",    review_status),
        ("Generated By",     "OpenClinica AI Pipeline (dvs-specification skill)"),
    ]
    for label, value in meta_rows:
        row_t = Table(
            [[Paragraph(label, S["approval_lbl"]),
              Paragraph(str(value), S["approval_val"])]],
            colWidths=[50 * mm, BODY_W - 50 * mm],
        )
        row_t.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(row_t)

    story.append(Spacer(1, 8 * mm))
    story.append(_hr())
    story.append(Spacer(1, 4 * mm))
    story.append(_p(
        "This document is a curated audit artifact summarising the OpenClinica 4 Data Validation "
        "Specification for the above study. The complete working DVS (including all 30 columns, "
        "write-back expressions, and UAT pass/fail data) resides in the companion XLSX workbook. "
        "This PDF is intended for sign-off, archival, and audit purposes.",
        "body",
    ))


def _build_summary(dvs_data, story, section_n):
    story.append(_section_heading("DVS Summary", section_n))
    story.append(Spacer(1, 3 * mm))

    checks   = dvs_data.get("dvs_oc4", [])
    uat      = dvs_data.get("uat_cases", [])
    cal      = dvs_data.get("calendaring_rules", [])

    if not checks:
        story.append(_p("No checks generated.", "body"))
        return

    # Counts by type
    by_type = Counter(r.get("Check Type", "Unknown") for r in checks)
    by_sev  = Counter(r.get("Severity",   "Unknown") for r in checks)
    by_pri  = Counter(r.get("Priority",   "Unknown") for r in checks)
    by_stat = Counter(r.get("Status",     "Unknown") for r in checks)

    story.append(_p("Checks by Type", "sub_hdr"))
    type_rows = [[_p(t, "cell"), _p(str(c), "cell_bold")] for t, c in sorted(by_type.items())]
    type_rows.append([_p("TOTAL", "cell_bold"), _p(str(len(checks)), "cell_bold")])
    story.append(_make_table(["Check Type", "Count"], type_rows,
                              [BODY_W * 0.7, BODY_W * 0.3]))
    story.append(Spacer(1, 3 * mm))

    story.append(_p("Checks by Severity", "sub_hdr"))
    sev_rows = [[_p(s, "cell"), _p(str(c), "cell_bold")] for s, c in sorted(by_sev.items())]
    story.append(_make_table(["Severity", "Count"], sev_rows,
                              [BODY_W * 0.7, BODY_W * 0.3]))
    story.append(Spacer(1, 3 * mm))

    story.append(_p("Checks by Priority", "sub_hdr"))
    pri_rows = [[_p(p, "cell"), _p(str(c), "cell_bold")] for p, c in sorted(by_pri.items())]
    story.append(_make_table(["Priority", "Count"], pri_rows,
                              [BODY_W * 0.7, BODY_W * 0.3]))
    story.append(Spacer(1, 3 * mm))

    story.append(_p("Review Status", "sub_hdr"))
    stat_rows = [[_p(s, "cell"), _p(str(c), "cell_bold")] for s, c in sorted(by_stat.items())]
    story.append(_make_table(["Status", "Count"], stat_rows,
                              [BODY_W * 0.7, BODY_W * 0.3]))
    story.append(Spacer(1, 3 * mm))

    # Quick stats line
    n_uat = len(uat)
    n_cal = len(cal)
    story.append(_p(
        f"UAT Cases: {n_uat}  |  Calendaring Rules: {n_cal}  |  "
        f"Total DVS Checks: {len(checks)}", "body"))


def _build_protocol_extraction(dvs_data, story, section_n):
    story.append(PageBreak())
    story.append(_section_heading("Protocol Extraction", section_n))
    story.append(Spacer(1, 3 * mm))
    story.append(_p(
        "Source requirements extracted from the protocol that drove DVS check generation. "
        "Protocol Reference cells highlighted amber require manual population.", "body"))
    story.append(Spacer(1, 2 * mm))

    rows = dvs_data.get("protocol_extraction", [])
    if not rows:
        story.append(_p("No protocol extraction rows.", "body"))
        return

    HDR = ["Category", "Requirement / Fact", "Protocol Ref", "Check Needed?",
           "Check ID", "Priority", "Status"]
    KEYS = ["Category", "Structured Requirement / Fact", "Protocol Reference",
            "Potential Check Needed?", "Candidate Check ID", "Priority", "Status"]
    WIDTHS = [
        BODY_W * 0.11, BODY_W * 0.34, BODY_W * 0.10,
        BODY_W * 0.10, BODY_W * 0.10, BODY_W * 0.10, BODY_W * 0.15,
    ]
    data_rows = []
    row_colors = []
    for r in rows:
        data_rows.append([_p(r.get(k, ""), "cell") for k in KEYS])
        row_colors.append(_placeholder_color(r.get("Protocol Reference")))

    story.append(_make_table(HDR, data_rows, WIDTHS, row_colors))


def _build_dvs_checks(dvs_data, story, section_n):
    story.append(PageBreak())
    story.append(_section_heading("DVS Checks", section_n))
    story.append(Spacer(1, 3 * mm))
    story.append(_p(
        "Curated view of all generated checks. The complete 30-column specification "
        "with all XPath expressions is in the companion XLSX workbook.", "body"))
    story.append(Spacer(1, 2 * mm))

    rows = dvs_data.get("dvs_oc4", [])
    if not rows:
        story.append(_p("No checks generated.", "body"))
        return

    HDR = ["Check ID", "Check Name", "Type", "Severity",
           "Target Form", "Target Item", "Expression",
           "Expected Site Action", "Status"]
    KEYS = ["Check ID", "Check Name", "Check Type", "Severity",
            "Target Form OID", "Target Item Name",
            "Expression / Calculation", "Expected Site Action", "Status"]
    WIDTHS = [
        BODY_W * 0.07, BODY_W * 0.14, BODY_W * 0.09, BODY_W * 0.07,
        BODY_W * 0.08, BODY_W * 0.10, BODY_W * 0.20,
        BODY_W * 0.16, BODY_W * 0.09,
    ]

    SEV_COLORS = {
        "Hard":          RED_LIGHT,
        "Soft":          AMBER,
        "Informational": GREEN_LIGHT,
    }

    data_rows = []
    row_colors = []
    for r in rows:
        data_rows.append([_p(r.get(k, ""), "cell") for k in KEYS])
        sev = r.get("Severity", "")
        row_colors.append(SEV_COLORS.get(sev))

    story.append(_make_table(HDR, data_rows, WIDTHS, row_colors))
    story.append(Spacer(1, 2 * mm))
    story.append(_p(
        "Row shading: Red = Hard constraint  |  Amber = Soft constraint  "
        "|  Green = Informational", "body_small"))


def _build_query_text(dvs_data, story, section_n):
    story.append(PageBreak())
    story.append(_section_heading("Query Text Library", section_n))
    story.append(Spacer(1, 3 * mm))
    story.append(_p(
        "Standardised messages shown to site users when checks fire. "
        "Wording marked [PLACEHOLDER] requires review before study go-live.", "body"))
    story.append(Spacer(1, 2 * mm))

    rows = dvs_data.get("query_text_library", [])
    if not rows:
        story.append(_p("No query text entries.", "body"))
        return

    HDR = ["QT ID", "Standard Message", "Audience", "Related Check ID(s)", "Status"]
    KEYS = ["Query Text ID", "Standard Message", "Audience",
            "Related Check ID(s)", "Status"]
    WIDTHS = [
        BODY_W * 0.09, BODY_W * 0.44, BODY_W * 0.10,
        BODY_W * 0.22, BODY_W * 0.15,
    ]
    data_rows = []
    row_colors = []
    for r in rows:
        data_rows.append([_p(r.get(k, ""), "cell") for k in KEYS])
        row_colors.append(_placeholder_color(r.get("Standard Message")))

    story.append(_make_table(HDR, data_rows, WIDTHS, row_colors))


def _build_uat_summary(dvs_data, story, section_n):
    story.append(PageBreak())
    story.append(_section_heading("UAT Summary", section_n))
    story.append(Spacer(1, 3 * mm))

    uat_rows = dvs_data.get("uat_cases", [])
    if not uat_rows:
        story.append(_p("No UAT cases generated.", "body"))
        return

    by_result = Counter(r.get("Test Result", "Not Run") for r in uat_rows)
    by_pri    = Counter(r.get("Priority",    "Unknown") for r in uat_rows)

    total    = len(uat_rows)
    passed   = by_result.get("Pass", 0)
    failed   = by_result.get("Fail", 0)
    not_run  = by_result.get("Not Run", 0)
    pass_pct = f"{passed / total * 100:.0f}%" if total else "N/A"

    story.append(_p("Results at a Glance", "sub_hdr"))
    glance_rows = [
        [_p("Total UAT Cases",   "cell"), _p(str(total),   "cell_bold")],
        [_p("Pass",              "cell"), _p(str(passed),  "cell_bold")],
        [_p("Fail",              "cell"), _p(str(failed),  "cell_bold")],
        [_p("Not Run",           "cell"), _p(str(not_run), "cell_bold")],
        [_p("Pass Rate",         "cell"), _p(pass_pct,     "cell_bold")],
    ]
    story.append(_make_table(["Metric", "Value"], glance_rows,
                              [BODY_W * 0.7, BODY_W * 0.3]))
    story.append(Spacer(1, 3 * mm))

    story.append(_p("Cases by Priority", "sub_hdr"))
    pri_rows = [[_p(p, "cell"), _p(str(c), "cell_bold")]
                for p, c in sorted(by_pri.items())]
    story.append(_make_table(["Priority", "Count"], pri_rows,
                              [BODY_W * 0.7, BODY_W * 0.3]))
    story.append(Spacer(1, 3 * mm))

    # Case list — show ID, scenario, related check, result
    story.append(_p("Case List", "sub_hdr"))
    HDR = ["UAT Case ID", "Scenario", "Related Check", "Priority", "Test Result"]
    KEYS = ["UAT Case ID", "Scenario", "Related Check ID", "Priority", "Test Result"]
    WIDTHS = [
        BODY_W * 0.11, BODY_W * 0.42, BODY_W * 0.13,
        BODY_W * 0.11, BODY_W * 0.13,
    ]
    RESULT_COLORS = {"Pass": GREEN_LIGHT, "Fail": RED_LIGHT}
    data_rows  = []
    row_colors = []
    for r in uat_rows:
        data_rows.append([_p(r.get(k, ""), "cell") for k in KEYS])
        row_colors.append(RESULT_COLORS.get(r.get("Test Result", "")))
    story.append(_make_table(HDR, data_rows, WIDTHS, row_colors))
    story.append(Spacer(1, 2 * mm))
    story.append(_p(
        "Row shading: Green = Pass  |  Red = Fail  |  No shading = Not Run / Blocked",
        "body_small"))
    story.append(Spacer(1, 3 * mm))
    story.append(_p(
        "Note: ODM load coordinate columns (Site_OID, Participant_Key, Study_Event_OID, "
        "Form_OID, Item_Group_OID, Participant_ID, Load_Order, Load_Value) are omitted "
        "from this summary view. See the companion XLSX for the full 25-column UAT_Cases sheet.",
        "body_small"))


def _build_calendaring(dvs_data, story, section_n):
    story.append(PageBreak())
    story.append(_section_heading("Calendaring Rules Summary", section_n))
    story.append(Spacer(1, 3 * mm))
    story.append(_p(
        "OC4 calendaring rules derived from the protocol. "
        "The JSON Output column in the companion XLSX contains the "
        "complete rule JSON ready to paste into OC4 Rules Management. "
        "All calendaring UAT is manual — see the Calendaring_UAT tab in the XLSX.", "body"))
    story.append(Spacer(1, 2 * mm))

    rows = dvs_data.get("calendaring_rules", [])
    if not rows:
        story.append(_p(
            "No calendaring rules extracted. If this study uses OC4 automated scheduling "
            "or notifications, populate the Calendaring_Rules sheet in the companion XLSX manually.",
            "body"))
        return

    HDR = ["Rule ID", "Rule Name", "Trigger Type", "Trigger OID",
           "Condition (Plain English)", "Action Type", "Priority", "Status"]
    KEYS = ["Rule ID", "Rule Name", "Trigger Type", "Trigger OID",
            "Condition (Plain English)", "Action Type", "Priority", "Status"]
    WIDTHS = [
        BODY_W * 0.08, BODY_W * 0.16, BODY_W * 0.12, BODY_W * 0.10,
        BODY_W * 0.24, BODY_W * 0.12, BODY_W * 0.08, BODY_W * 0.10,
    ]
    data_rows = []
    for r in rows:
        data_rows.append([_p(r.get(k, ""), "cell") for k in KEYS])

    story.append(_make_table(HDR, data_rows, WIDTHS))


def _build_approval_block(story):
    story.append(PageBreak())
    story.append(_section_heading("Approval and Sign-Off", 7))
    story.append(Spacer(1, 5 * mm))
    story.append(_p(
        "This DVS has been reviewed and is approved for use in UAT and study go-live. "
        "By signing below, the reviewer confirms that all checks, query text, "
        "and UAT cases have been reviewed and any PLACEHOLDER cells have been resolved.",
        "body"))
    story.append(Spacer(1, 8 * mm))

    sig_rows = [
        ["Role", "Name (print)", "Signature", "Date"],
        ["Data Manager", "", "", ""],
        ["Clinical Project Manager", "", "", ""],
        ["Sponsor Representative", "", "", ""],
    ]
    sig_widths = [BODY_W * 0.25, BODY_W * 0.28, BODY_W * 0.27, BODY_W * 0.20]

    style = TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), DARK_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("GRID",          (0, 0), (-1, -1), 0.5, GREY_MID),
        ("ROWHEIGHTS",    (0, 1), (-1, -1), 22),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
    ])
    t = Table(sig_rows, colWidths=sig_widths)
    t.setStyle(style)
    story.append(t)
    story.append(Spacer(1, 6 * mm))
    story.append(_p(
        "CONFIDENTIAL — This document contains proprietary clinical trial build information. "
        "Do not distribute outside the study team without authorisation.",
        "body_small"))


# ── Main entry point ──────────────────────────────────────────────────────────

def build_dvs_pdf(dvs_data, output_path):
    """
    Build a DVS audit PDF from dvs_data dict.

    Args:
        dvs_data:    Same dict passed to build_dvs() (generate_dvs.py).
        output_path: Absolute path for the output .pdf file.

    Returns:
        output_path
    """
    meta  = dvs_data.get("study_meta", {})
    if "build_date" not in meta:
        meta["build_date"] = datetime.date.today().isoformat()

    doc   = _make_doc(output_path, meta)
    story = []

    # Cover (Cover page template)
    story.append(NextPageTemplate("Body"))
    _build_cover(meta, story)
    story.append(PageBreak())

    # Sections
    _build_summary(dvs_data,             story, section_n=1)
    _build_protocol_extraction(dvs_data, story, section_n=2)
    _build_dvs_checks(dvs_data,          story, section_n=3)
    _build_query_text(dvs_data,          story, section_n=4)
    _build_uat_summary(dvs_data,         story, section_n=5)
    _build_calendaring(dvs_data,         story, section_n=6)
    _build_approval_block(story)

    doc.build(story)
    print(f"DVS PDF written to: {output_path}")
    return output_path


if __name__ == "__main__":
    import sys, json

    if len(sys.argv) < 3:
        print("Usage: python generate_dvs_pdf.py <dvs_data.json> <output.pdf>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        data = json.load(f)
    build_dvs_pdf(data, sys.argv[2])
