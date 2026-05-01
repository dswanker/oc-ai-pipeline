"""
generate_pdf.py
Generates a formatted landscape PDF pricing summary from structured data.
Called by the protocol-to-pricing-summary skill after extracting protocol data.

Usage:
    python generate_pdf.py --data <json_data_string> --output <output_path>
    
Or imported and called directly:
    from generate_pdf import build_pricing_pdf
    build_pricing_pdf(data_dict, output_path)
"""

from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
import datetime

# ── Colour palette ────────────────────────────────────────────────────────────
DARK_BLUE   = colors.HexColor("#1B3A6B")
MID_BLUE    = colors.HexColor("#2E6DA4")
LIGHT_BLUE  = colors.HexColor("#D6E4F0")
ACCENT      = colors.HexColor("#E8F4FD")
WHITE       = colors.white
GREY_LIGHT  = colors.HexColor("#F5F5F5")
GREY_MID    = colors.HexColor("#CCCCCC")
GREY_DARK   = colors.HexColor("#555555")
RED_FLAG    = colors.HexColor("#C0392B")
AMBER_FLAG  = colors.HexColor("#E67E22")
GREEN_FLAG  = colors.HexColor("#27AE60")
TEXT_DARK   = colors.HexColor("#1A1A1A")

PAGE_W, PAGE_H = landscape(A4)
MARGIN = 1.8 * cm
CONTENT_W = PAGE_W - 2 * MARGIN


def make_styles():
    base = getSampleStyleSheet()
    styles = {}

    styles["title"] = ParagraphStyle(
        "title", fontName="Helvetica-Bold", fontSize=16,
        textColor=WHITE, alignment=TA_LEFT, spaceAfter=2
    )
    styles["subtitle"] = ParagraphStyle(
        "subtitle", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#BDD7EE"), alignment=TA_LEFT, spaceAfter=0
    )
    styles["section_header"] = ParagraphStyle(
        "section_header", fontName="Helvetica-Bold", fontSize=10,
        textColor=WHITE, alignment=TA_LEFT, spaceBefore=0, spaceAfter=0,
        leftIndent=6
    )
    styles["body"] = ParagraphStyle(
        "body", fontName="Helvetica", fontSize=8.5,
        textColor=TEXT_DARK, leading=13, spaceAfter=4
    )
    styles["body_small"] = ParagraphStyle(
        "body_small", fontName="Helvetica", fontSize=7.5,
        textColor=TEXT_DARK, leading=11, spaceAfter=2
    )
    styles["label"] = ParagraphStyle(
        "label", fontName="Helvetica-Bold", fontSize=8.5,
        textColor=DARK_BLUE, spaceAfter=2
    )
    styles["cell"] = ParagraphStyle(
        "cell", fontName="Helvetica", fontSize=7.5,
        textColor=TEXT_DARK, leading=10
    )
    styles["cell_bold"] = ParagraphStyle(
        "cell_bold", fontName="Helvetica-Bold", fontSize=7.5,
        textColor=DARK_BLUE, leading=10
    )
    styles["cell_header"] = ParagraphStyle(
        "cell_header", fontName="Helvetica-Bold", fontSize=8,
        textColor=WHITE, leading=10, alignment=TA_CENTER
    )
    styles["flag_high"] = ParagraphStyle(
        "flag_high", fontName="Helvetica", fontSize=8,
        textColor=TEXT_DARK, leading=11
    )
    styles["pending"] = ParagraphStyle(
        "pending", fontName="Helvetica-Oblique", fontSize=8,
        textColor=RED_FLAG, leading=11
    )
    styles["disclaimer"] = ParagraphStyle(
        "disclaimer", fontName="Helvetica-Oblique", fontSize=7.5,
        textColor=GREY_DARK, leading=11, spaceBefore=6
    )
    return styles


def header_band(text, styles, width=CONTENT_W):
    """Dark blue section header band."""
    tbl = Table([[Paragraph(text, styles["section_header"])]],
                colWidths=[width])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK_BLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    return tbl


def kv_table(rows, styles, col_widths=None):
    """Two-column key-value table."""
    if col_widths is None:
        col_widths = [CONTENT_W * 0.28, CONTENT_W * 0.72]
    data = []
    for k, v in rows:
        if v and "NOT SPECIFIED" in str(v):
            val_para = Paragraph(str(v), styles["pending"])
        else:
            val_para = Paragraph(str(v) if v else "—", styles["body_small"])
        data.append([
            Paragraph(k, styles["label"]),
            val_para
        ])
    tbl = Table(data, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [WHITE, GREY_LIGHT]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.3, GREY_MID),
    ]))
    return tbl


def confidence_color(conf):
    if not conf:
        return TEXT_DARK
    c = conf.upper()
    if "HIGH" in c:
        return GREEN_FLAG
    if "MEDIUM" in c or "MED" in c:
        return AMBER_FLAG
    return RED_FLAG


def build_pricing_pdf(data: dict, output_path: str):
    styles = make_styles()
    doc = SimpleDocTemplate(
        output_path,
        pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="Protocol Pricing Summary",
    )

    story = []
    so  = data.get("study_overview", {})
    pp  = data.get("patient_population", {})
    vs  = data.get("visit_summary", {})
    crf = data.get("crf_summary", {})
    cf  = data.get("complexity_flags", [])
    crn = data.get("confidence_review_notes", [])
    cb  = data.get("conditional_branching", [])
    dc  = data.get("data_cleaning_estimate", {})

    # ── Cover header ─────────────────────────────────────────────────────────
    header_data = [[
        Paragraph("PROTOCOL PRICING SUMMARY", styles["title"]),
        Paragraph(
            f"Generated: {datetime.date.today().strftime('%d %b %Y')}  |  "
            f"Status: <b>PENDING HUMAN REVIEW</b>  |  Mode: "
            f"{data.get('skill_meta', {}).get('mode', 'PROTOCOL_ONLY')}",
            styles["subtitle"]
        )
    ]]
    header_tbl = Table(header_data, colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), DARK_BLUE),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ("SPAN",          (0, 0), (0, 0)),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 10))

    # ── Section 1: Study Overview ─────────────────────────────────────────────
    story.append(header_band("SECTION 1 — STUDY OVERVIEW", styles))
    story.append(Spacer(1, 4))

    left_rows = [
        ("Protocol Number",  so.get("protocol_number", "—")),
        ("Sponsor",          so.get("sponsor", "—")),
        ("Therapeutic Area", so.get("therapeutic_area", "—")),
        ("Study Phase",      so.get("study_phase", "—")),
        ("Study Type",       so.get("study_type", "—")),
    ]
    right_rows = [
        ("Number of Sites",  so.get("number_of_sites") or "[NOT SPECIFIED — PLEASE COMPLETE]"),
        ("Region(s)",        so.get("regions") or "[NOT SPECIFIED — PLEASE COMPLETE]"),
        ("Start Date",       so.get("start_date", "—")),
        ("End Date",         so.get("end_date", "—")),
        ("Duration (months)",str(so.get("duration_months", "—"))),
    ]

    half = CONTENT_W / 2 - 4
    left_tbl  = kv_table(left_rows,  styles, [half * 0.38, half * 0.62])
    right_tbl = kv_table(right_rows, styles, [half * 0.38, half * 0.62])

    two_col = Table([[left_tbl, right_tbl]], colWidths=[half, half])
    two_col.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(two_col)
    story.append(Spacer(1, 8))

    # ── Study title full width ────────────────────────────────────────────────
    title_tbl = kv_table(
        [("Study Title", so.get("title", data.get("study_title", "See protocol")))],
        styles, [CONTENT_W * 0.13, CONTENT_W * 0.87]
    )
    story.append(title_tbl)
    story.append(Spacer(1, 10))

    # ── Section 2: Patient Population ────────────────────────────────────────
    story.append(header_band("SECTION 2 — PATIENT POPULATION", styles))
    story.append(Spacer(1, 4))

    arms = pp.get("arms", [])
    pop_rows = [
        ("Total Enrollment", str(pp.get("total_enrollment", "—"))),
        ("Number of Arms",   str(pp.get("number_of_arms", "—"))),
    ]
    for arm in arms:
        pop_rows.append((
            arm.get("name", "Arm"),
            f"n={arm.get('n','?')}  |  {arm.get('description','')}"
        ))
    story.append(kv_table(pop_rows, styles))
    story.append(Spacer(1, 10))

    # ── Section 3: Visit Summary ──────────────────────────────────────────────
    story.append(header_band("SECTION 3 — VISIT SUMMARY", styles))
    story.append(Spacer(1, 4))

    vis_rows = []
    for arm in vs.get("arms", []):
        vis_rows.append((
            arm.get("name", "Arm"),
            f"{arm.get('visits_per_patient','?')} visits × "
            f"{arm.get('patients','?')} patients = "
            f"{arm.get('total_visits','?')} patient visits"
        ))
    vis_rows.append((
        "TOTAL PATIENT VISITS",
        str(vs.get("total_patient_visits_all_arms", "—"))
    ))
    story.append(kv_table(vis_rows, styles))
    story.append(Spacer(1, 10))

    # ── Section 4: CRF Summary ────────────────────────────────────────────────
    story.append(header_band("SECTION 4 — CRF SUMMARY", styles))
    story.append(Spacer(1, 4))

    # Totals row
    totals_data = [
        [
            Paragraph("Total Unique CRFs", styles["label"]),
            Paragraph(str(crf.get("total_unique_crfs", "—")), styles["body"]),
            Paragraph("Simple", styles["label"]),
            Paragraph(str(crf.get("simple_crfs", "—")), styles["body"]),
            Paragraph("Average", styles["label"]),
            Paragraph(str(crf.get("average_crfs", "—")), styles["body"]),
            Paragraph("Complex", styles["label"]),
            Paragraph(str(crf.get("complex_crfs", "—")), styles["body"]),
            Paragraph("Total Re-use CRFs", styles["label"]),
            Paragraph(str(crf.get("total_reuse_crfs", "—")), styles["body"]),
        ]
    ]
    col_w = CONTENT_W / 10
    totals_tbl = Table(totals_data, colWidths=[col_w * 1.6, col_w * 0.6,
                                                col_w * 0.7, col_w * 0.5,
                                                col_w * 0.7, col_w * 0.5,
                                                col_w * 0.7, col_w * 0.5,
                                                col_w * 1.6, col_w * 0.6])
    totals_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), LIGHT_BLUE),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("BOX",           (0, 0), (-1, -1), 0.5, MID_BLUE),
    ]))
    story.append(totals_tbl)
    story.append(Spacer(1, 6))

    # CRF detail table
    crf_headers = ["#", "Domain", "CDASH", "Source", "Arms / Visits",
                   "Complexity", "Re-uses", "Confidence", "Notes"]
    crf_col_w = [
        CONTENT_W * 0.03,  # #
        CONTENT_W * 0.13,  # Domain
        CONTENT_W * 0.05,  # CDASH
        CONTENT_W * 0.09,  # Source
        CONTENT_W * 0.16,  # Visits
        CONTENT_W * 0.07,  # Complexity
        CONTENT_W * 0.05,  # Re-uses
        CONTENT_W * 0.07,  # Confidence
        CONTENT_W * 0.35,  # Notes
    ]

    def complexity_bg(val):
        if not val:
            return WHITE
        v = val.upper()
        if "COMPLEX" in v:  return colors.HexColor("#FADBD8")
        if "AVERAGE" in v:  return colors.HexColor("#FDEBD0")
        if "SIMPLE"  in v:  return colors.HexColor("#D5F5E3")
        return WHITE

    crf_table_data = [[
        Paragraph(h, styles["cell_header"]) for h in crf_headers
    ]]

    details = crf.get("crf_detail", [])
    for i, row in enumerate(details):
        visits_text = ", ".join(row.get("visits_used", [])) if row.get("visits_used") else "—"
        source_raw  = row.get("source", "CDASH_ESTIMATE")
        source_disp = source_raw.replace("_", " ").replace("CDASH ESTIMATE", "CDASH Est.")
        conf_raw    = row.get("confidence", "")

        crf_table_data.append([
            Paragraph(str(i + 1), styles["cell"]),
            Paragraph(row.get("domain_name", ""), styles["cell_bold"]),
            Paragraph(row.get("cdash_code", ""), styles["cell"]),
            Paragraph(source_disp, styles["cell"]),
            Paragraph(visits_text, styles["cell"]),
            Paragraph(row.get("complexity", ""), styles["cell_bold"]),
            Paragraph(str(row.get("reuse_count", "—")), styles["cell"]),
            Paragraph(conf_raw, ParagraphStyle(
                "conf_dyn", fontName="Helvetica-Bold", fontSize=7.5,
                textColor=confidence_color(conf_raw), leading=10
            )),
            Paragraph(row.get("notes", ""), styles["cell"]),
        ])

    detail_tbl = Table(crf_table_data, colWidths=crf_col_w, repeatRows=1)

    row_styles = [
        ("BACKGROUND",    (0, 0), (-1, 0),  DARK_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.3, GREY_MID),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, GREY_LIGHT]),
    ]
    # Complexity column coloring
    for i, row in enumerate(details, start=1):
        bg = complexity_bg(row.get("complexity", ""))
        row_styles.append(("BACKGROUND", (5, i), (5, i), bg))

    detail_tbl.setStyle(TableStyle(row_styles))
    story.append(detail_tbl)
    story.append(Spacer(1, 10))

    # ── Section 5: Complexity Flags ───────────────────────────────────────────
    story.append(PageBreak())
    story.append(header_band("SECTION 5 — COMPLEXITY FLAGS", styles))
    story.append(Spacer(1, 4))

    flag_data = []
    for i, flag in enumerate(cf, 1):
        flag_data.append([
            Paragraph(str(i), styles["cell_bold"]),
            Paragraph(str(flag), styles["body_small"])
        ])
    if flag_data:
        flag_tbl = Table(flag_data, colWidths=[CONTENT_W * 0.04, CONTENT_W * 0.96])
        flag_tbl.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [WHITE, GREY_LIGHT]),
            ("LINEBELOW",     (0, 0), (-1, -1), 0.3, GREY_MID),
        ]))
        story.append(flag_tbl)
    story.append(Spacer(1, 10))

    # ── Section 6: Confidence & Review Notes ──────────────────────────────────
    story.append(header_band("SECTION 6 — CONFIDENCE & REVIEW NOTES", styles))
    story.append(Spacer(1, 4))

    note_data = []
    for i, note in enumerate(crn, 1):
        note_data.append([
            Paragraph(str(i), styles["cell_bold"]),
            Paragraph(str(note), styles["body_small"])
        ])
    if note_data:
        note_tbl = Table(note_data, colWidths=[CONTENT_W * 0.04, CONTENT_W * 0.96])
        note_tbl.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [WHITE, GREY_LIGHT]),
            ("LINEBELOW",     (0, 0), (-1, -1), 0.3, GREY_MID),
        ]))
        story.append(note_tbl)
    story.append(Spacer(1, 10))

    # ── Section 7: Conditional Branching ─────────────────────────────────────
    story.append(header_band("SECTION 7 — CONDITIONAL BRANCHING INDICATORS", styles))
    story.append(Spacer(1, 4))

    branch_headers = ["#", "Description", "Type", "Affected Domains", "Confidence", "Note"]
    branch_col_w   = [
        CONTENT_W * 0.03,
        CONTENT_W * 0.30,
        CONTENT_W * 0.09,
        CONTENT_W * 0.18,
        CONTENT_W * 0.08,
        CONTENT_W * 0.32,
    ]
    branch_data = [[Paragraph(h, styles["cell_header"]) for h in branch_headers]]
    for i, b in enumerate(cb, 1):
        conf_raw = b.get("confidence", "")
        branch_data.append([
            Paragraph(str(i), styles["cell"]),
            Paragraph(b.get("description", ""), styles["cell"]),
            Paragraph(b.get("type", "").upper(), styles["cell_bold"]),
            Paragraph(", ".join(b.get("affected_domains", [])), styles["cell"]),
            Paragraph(conf_raw, ParagraphStyle(
                "conf_b", fontName="Helvetica-Bold", fontSize=7.5,
                textColor=confidence_color(conf_raw), leading=10
            )),
            Paragraph(b.get("note", ""), styles["cell"]),
        ])
    branch_tbl = Table(branch_data, colWidths=branch_col_w, repeatRows=1)
    branch_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  DARK_BLUE),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.3, GREY_MID),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, GREY_LIGHT]),
    ]))
    story.append(branch_tbl)
    story.append(Spacer(1, 10))

    # ── Section 8: Data Cleaning Estimate ────────────────────────────────────
    story.append(PageBreak())
    story.append(header_band("SECTION 8 — DATA CLEANING COMPLEXITY ESTIMATE", styles))
    story.append(Spacer(1, 4))

    dc_headers = ["Domain", "CDASH", "Complexity", "Implied Checks"]
    dc_col_w   = [
        CONTENT_W * 0.15,
        CONTENT_W * 0.06,
        CONTENT_W * 0.09,
        CONTENT_W * 0.70,
    ]
    dc_data = [[Paragraph(h, styles["cell_header"]) for h in dc_headers]]

    def dc_bg(rating):
        if not rating: return WHITE
        r = rating.upper()
        if "HIGH"   in r: return colors.HexColor("#FADBD8")
        if "MEDIUM" in r: return colors.HexColor("#FDEBD0")
        if "LOW"    in r: return colors.HexColor("#D5F5E3")
        return WHITE

    for i, dom in enumerate(dc.get("domains", [])):
        checks = dom.get("implied_checks", [])
        checks_text = " • ".join(checks) if checks else "—"
        rating = dom.get("complexity_rating", "")
        dc_data.append([
            Paragraph(dom.get("domain", ""), styles["cell_bold"]),
            Paragraph(dom.get("cdash_code", ""), styles["cell"]),
            Paragraph(rating, styles["cell_bold"]),
            Paragraph(checks_text, styles["cell"]),
        ])

    dc_tbl = Table(dc_data, colWidths=dc_col_w, repeatRows=1)
    dc_row_styles = [
        ("BACKGROUND",    (0, 0), (-1, 0),  DARK_BLUE),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.3, GREY_MID),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, GREY_LIGHT]),
    ]
    for i, dom in enumerate(dc.get("domains", []), start=1):
        dc_row_styles.append(("BACKGROUND", (2, i), (2, i), dc_bg(dom.get("complexity_rating", ""))))
    dc_tbl.setStyle(TableStyle(dc_row_styles))
    story.append(dc_tbl)

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        dc.get("disclaimer",
               "Precise check counts require downstream CRF specification and "
               "data management plan review. This estimate is directional only."),
        styles["disclaimer"]
    ))
    story.append(Spacer(1, 10))

    # ── Footer review block ───────────────────────────────────────────────────
    review_text = (
        "<b>HUMAN REVIEW REQUIRED</b>  —  "
        "1. Complete fields marked [NOT SPECIFIED]  "
        "2. Verify CRF complexity classifications  "
        "3. Confirm conditional branching points  "
        "4. Add corrections to crf-categorization-examples.md  "
        "5. Update crf-complexity-rules.md if definition changes"
    )
    review_tbl = Table(
        [[Paragraph(review_text, ParagraphStyle(
            "review", fontName="Helvetica", fontSize=7.5,
            textColor=WHITE, leading=11
        ))]],
        colWidths=[CONTENT_W]
    )
    review_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), MID_BLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    story.append(review_tbl)

    doc.build(story)
    print(f"PDF written to: {output_path}")


# ── Inline test data (PrTK05) ─────────────────────────────────────────────────
if __name__ == "__main__":
    sample_data = {
        "skill_meta": {
            "mode": "PROTOCOL_ONLY",
            "library_files_provided": [],
            "library_format_detected": "N/A"
        },
        "study_title": "A Biomarker Study in Men with Localized, Intermediate-Risk Prostate Cancer Treated with Aglatimagene Besadenovec",
        "study_overview": {
            "protocol_number": "PrTK05",
            "sponsor": "Candel Therapeutics, Inc.",
            "therapeutic_area": "Oncology",
            "study_phase": "Phase 2a",
            "study_type": "Open-label, prospective, multi-center, concurrent control group",
            "number_of_sites": None,
            "regions": "USA",
            "start_date": "30 November 2025",
            "end_date": "30 June 2026",
            "duration_months": 7
        },
        "patient_population": {
            "total_enrollment": 45,
            "number_of_arms": 2,
            "arms": [
                {"name": "Treatment Group", "n": 30,
                 "description": "3 injections aglatimagene besadenovec + valacyclovir prodrug + EBRT"},
                {"name": "Concurrent Control Group", "n": 15,
                 "description": "Standard of care EBRT only; blood/urine biomarker collection"}
            ]
        },
        "visit_summary": {
            "arms": [
                {"name": "Treatment Group",        "visits_per_patient": 12, "patients": 30, "total_visits": 360},
                {"name": "Concurrent Control Group","visits_per_patient": 5,  "patients": 15, "total_visits": 75}
            ],
            "total_patient_visits_all_arms": 435
        },
        "crf_summary": {
            "total_unique_crfs": 21,
            "simple_crfs": 8,
            "average_crfs": 9,
            "complex_crfs": 4,
            "total_reuse_crfs": 91,
            "crf_detail": [
                {"domain_name": "Demographics",            "cdash_code": "DM",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening"], "complexity": "Simple",  "reuse_count": 0,  "confidence": "High",   "notes": "Standard demographic fields ~8 items. Separate from MH."},
                {"domain_name": "Medical History",         "cdash_code": "MH",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening"], "complexity": "Average", "reuse_count": 0,  "confidence": "Medium", "notes": "1 repeating group (conditions). Separate from DM."},
                {"domain_name": "Informed Consent",        "cdash_code": "SC",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening"], "complexity": "Simple",  "reuse_count": 0,  "confidence": "High",   "notes": "Standard consent date/signature ~5 fields."},
                {"domain_name": "I/E Criteria — Treatment","cdash_code": "SC",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening"], "complexity": "Simple",  "reuse_count": 0,  "confidence": "High",   "notes": "Full 10 inclusion + 10 exclusion criteria for treatment arm."},
                {"domain_name": "I/E Criteria — Control",  "cdash_code": "SC",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening"], "complexity": "Simple",  "reuse_count": 0,  "confidence": "High",   "notes": "Subset I/E (criteria 5, 8, 10 excluded) for control arm."},
                {"domain_name": "Disease Assessment",      "cdash_code": "TU/RS", "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening"], "complexity": "Average", "reuse_count": 0,  "confidence": "Medium", "notes": "PSA, biopsy, T-staging, NCCN risk group, ECOG ~15-20 fields."},
                {"domain_name": "Vital Signs — Full",      "cdash_code": "VS",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening","Inj#1","Treat.Assess#1","Treat.Assess#2","W6-8","W8-10","W12-14","W16-18"], "complexity": "Simple", "reuse_count": 7, "confidence": "Medium", "notes": "BP, HR, temp, weight, height. Weight/height at baseline only."},
                {"domain_name": "Vital Signs — Follow-up", "cdash_code": "VS",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Follow-up visits post-baseline"], "complexity": "Simple", "reuse_count": 4, "confidence": "Medium", "notes": "BP, HR, temp only. No weight/height."},
                {"domain_name": "Physical Exam — Full",    "cdash_code": "PE",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening"], "complexity": "Average", "reuse_count": 0,  "confidence": "Medium", "notes": "Full PE at screening."},
                {"domain_name": "Physical Exam — Symptom-Directed", "cdash_code": "PE", "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Pre-injection visits","W16-18"], "complexity": "Simple", "reuse_count": 4, "confidence": "Medium", "notes": "Targeted PE at follow-up visits."},
                {"domain_name": "Laboratory — Full Panel", "cdash_code": "LB",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening","Treat.Assess#1","Treat.Assess#2","W16-18"], "complexity": "Average", "reuse_count": 3, "confidence": "Medium", "notes": "CBC + chemistry ~12 fields. Treatment group only."},
                {"domain_name": "PSA",                     "cdash_code": "LB",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening","W2-3","W8-10","W16-18"], "complexity": "Simple", "reuse_count": 7, "confidence": "High", "notes": "Single analyte. Both arms."},
                {"domain_name": "Adverse Events",          "cdash_code": "AE",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["All treatment visits"], "complexity": "Average", "reuse_count": 11, "confidence": "High", "notes": "1 repeating group. NCI-CTCAE grading. 24-hr SAE reporting."},
                {"domain_name": "Concomitant Medications", "cdash_code": "CM",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Screening + all follow-up, both arms"], "complexity": "Average", "reuse_count": 15, "confidence": "High", "notes": "1 repeating group. Both arms."},
                {"domain_name": "Study Drug Exposure (Injection)", "cdash_code": "EX", "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Inj#1","Inj#2","Inj#3"], "complexity": "Simple", "reuse_count": 2, "confidence": "Medium", "notes": "Injection date, dose, route, quadrant. Treatment only."},
                {"domain_name": "Prodrug Exposure (Valacyclovir)", "cdash_code": "EX", "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Post each injection x3"], "complexity": "Simple", "reuse_count": 2, "confidence": "Medium", "notes": "CrCl-adjusted dose, 14-day course."},
                {"domain_name": "Valacyclovir Diary (ePRO)",       "cdash_code": "QS", "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["Post each injection x3 (patient-entered)"], "complexity": "Simple", "reuse_count": 2, "confidence": "Medium", "notes": "Patient-reported daily dose compliance. Built in OpenClinica ePRO module."},
                {"domain_name": "Biospecimen — Blood/Urine (Treatment)", "cdash_code": "BS", "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["11 treatment timepoints"], "complexity": "Average", "reuse_count": 10, "confidence": "Medium", "notes": "Includes qPCR shedding fields. Lab Manual needed for full scope."},
                {"domain_name": "Biospecimen — Blood/Urine (Control)", "cdash_code": "BS", "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["5 control timepoints"], "complexity": "Simple", "reuse_count": 4, "confidence": "Medium", "notes": "Biomarker only, no qPCR. Simpler than treatment version."},
                {"domain_name": "Semen Biospecimen",       "cdash_code": "BS",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["W1","W2-3","W16-18 (optional)"], "complexity": "Simple", "reuse_count": 2, "confidence": "Medium", "notes": "Optional home collection ~5 fields."},
                {"domain_name": "Disposition / Withdrawal","cdash_code": "DS",    "source": "CDASH_ESTIMATE", "customer_form_name": None, "visits_used": ["As needed"], "complexity": "Simple", "reuse_count": 0, "confidence": "High", "notes": "Standard completion/withdrawal fields."},
            ]
        },
        "complexity_flags": [
            "Biological/gene therapy IND product — elevated regulatory complexity",
            "Two-arm differential scheduling — materially different CRF sets per arm",
            "Home-based semen sample collection — remote data entry workflow needed",
            "High-frequency early biosampling: 2-4hr, 24hr, 48hr post-injection",
            "Patient replacement rules — enrollment tracking complexity",
            "CrCl calculated field with dose adjustment table — EDC calculation required",
            "Multi-center USA — exact site count unknown — local lab reference ranges needed",
            "qPCR biomarker scope unknown — Laboratory Manual not provided",
            "Biomarker analysis scope open-ended per protocol language",
        ],
        "confidence_review_notes": [
            "Number of sites: not specified — required for pricing",
            "DM/MH classified as separate forms per standing rule",
            "I/E criteria: 2 unique CRFs confirmed (treatment vs control)",
            "PE: 2 unique CRFs confirmed (full vs symptom-directed)",
            "Valacyclovir diary classified as ePRO CRF per standing rule",
            "Biospecimen qPCR scope requires Laboratory Manual",
            "Biomarker analysis full field list unknown",
            "Visit count variability from early discontinuation not included in totals",
        ],
        "conditional_branching": [
            {"description": "Treatment vs control arm assessment differences", "type": "arm", "affected_domains": ["AE","EX","VS","PE","LB","BS"], "confidence": "High", "note": "Most significant branching structure in study"},
            {"description": "I/E criteria arm-specific items", "type": "arm", "affected_domains": ["SC"], "confidence": "High", "note": ""},
            {"description": "PSA visit-specific activation", "type": "visit", "affected_domains": ["LB"], "confidence": "High", "note": ""},
            {"description": "Full lab panel visit-specific activation", "type": "visit", "affected_domains": ["LB"], "confidence": "High", "note": ""},
            {"description": "Weight/height baseline only", "type": "visit", "affected_domains": ["VS"], "confidence": "High", "note": ""},
            {"description": "CrCl recalculation if creatinine abnormal", "type": "condition", "affected_domains": ["LB","EX"], "confidence": "High", "note": "Field-level detail requires CRF spec confirmation"},
            {"description": "AE Grade >=3 triggers SAE fields", "type": "condition", "affected_domains": ["AE"], "confidence": "High", "note": "Field-level detail requires CRF spec confirmation"},
            {"description": "Abnormal lab triggers AE entry", "type": "condition", "affected_domains": ["LB","AE"], "confidence": "High", "note": "Field-level detail requires CRF spec confirmation"},
            {"description": "Drug/prodrug stopping rules", "type": "condition", "affected_domains": ["EX","AE","DS"], "confidence": "Medium", "note": "Field-level detail requires CRF spec confirmation"},
            {"description": "Semen collection optional", "type": "optional", "affected_domains": ["BS"], "confidence": "High", "note": ""},
            {"description": "Telemedicine / remote visit option", "type": "optional", "affected_domains": ["All clinical domains"], "confidence": "Medium", "note": "Field-level detail requires CRF spec confirmation"},
        ],
        "data_cleaning_estimate": {
            "disclaimer": "Precise check counts require downstream CRF specification and data management plan review. This estimate is directional only.",
            "domains": [
                {"domain": "Demographics",    "cdash_code": "DM", "complexity_rating": "Low",    "implied_checks": ["Age >= 18","Required fields present","Consent date precedes all study dates"]},
                {"domain": "Medical History", "cdash_code": "MH", "complexity_rating": "Medium", "implied_checks": ["Exclusion criteria cross-check","MH dates precede enrollment","Required fields per row"]},
                {"domain": "Vital Signs",     "cdash_code": "VS", "complexity_rating": "Low",    "implied_checks": ["HR range 40-200 bpm","SBP 70-200 mmHg","DBP 40-130 mmHg","Temp 35-40C","Weight/height at baseline only"]},
                {"domain": "Lab Assessments", "cdash_code": "LB", "complexity_rating": "High",   "implied_checks": ["Eligibility thresholds at screening","CrCl calculation formula check","Valacyclovir dose adjustment cross-check","Lab before injection if same day","Grade 4 abnormality triggers AE","PSA visit window enforcement"]},
                {"domain": "Adverse Events",  "cdash_code": "AE", "complexity_rating": "High",   "implied_checks": ["AE start >= first injection date","Grade >= 3 triggers SAE flag","SAE report within 24hrs","End date >= start date","Causality required"]},
                {"domain": "Concomitant Meds","cdash_code": "CM", "complexity_rating": "Medium", "implied_checks": ["New prostate cancer therapy flag","Corticosteroids >10mg/day exclusion flag","Stop date >= start date"]},
                {"domain": "Study Drug EX",   "cdash_code": "EX", "complexity_rating": "Medium", "implied_checks": ["Injection timing windows","Fixed dose deviation flag","Valacyclovir 14-day course check","CrCl dose adjustment cross-check"]},
                {"domain": "Biospecimen",     "cdash_code": "BS", "complexity_rating": "Medium", "implied_checks": ["Blood volume <= 42mL per visit","Collection window checks","Baseline before injection #1","Sample type required"]},
                {"domain": "Disposition",     "cdash_code": "DS", "complexity_rating": "Low",    "implied_checks": ["Withdrawal reason required","Date within study period"]},
            ]
        }
    }

    build_pricing_pdf(sample_data, "/mnt/user-data/outputs/PrTK05_Pricing_Summary.pdf")
