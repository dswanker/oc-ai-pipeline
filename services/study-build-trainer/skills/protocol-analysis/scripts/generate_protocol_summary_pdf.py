"""
generate_pdf.py
Generates a formatted landscape PDF protocol summary from structured data.
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


def grid_table(headers, rows, styles, col_widths, zebra=True):
    """
    Grid-style table with a dark header row and optional zebra striping.
    Mirrors the helper in generate_study_spec_pdf.py so the Study Event
    Schedule sub-table in Section 3 of the Protocol Summary looks
    consistent with Section 1 of the Study Spec.
    """
    data = [[Paragraph(h, styles["cell_header"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c) if c is not None else "—", styles["cell"])
                     for c in row])
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    ts = [
        ("BACKGROUND",    (0, 0), (-1, 0),  DARK_BLUE),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("GRID",          (0, 0), (-1, -1), 0.3, GREY_MID),
    ]
    if zebra:
        ts.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, GREY_LIGHT]))
    tbl.setStyle(TableStyle(ts))
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


def build_pricing_pdf(data: dict, output_path: str, struct_json: dict = None):
    """
    Build the Protocol Summary PDF.

    Parameters
    ----------
    data : dict
        The Protocol Summary JSON (study_meta, patient_population,
        visit_summary, crf_summary, ...).
    output_path : str
        Where to write the PDF.
    struct_json : dict, optional
        The Study Spec JSON (with forms[] and timepoint_csv). If provided,
        Section 3 will include a detailed Study Event Schedule sub-table
        mirroring Study Spec Section 1 (without the Event OID column).
    """
    styles = make_styles()
    doc = SimpleDocTemplate(
        output_path,
        pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="Protocol Summary",
    )

    story = []

    # ── Map study_meta → study_overview (script-expected shape) ─────────────
    # The Protocol Summary JSON uses 'study_meta' as the cover data bucket,
    # with field names like 'study_title', 'indication',
    # 'total_study_duration_months'. Older code expected 'study_overview'
    # with 'title', 'therapeutic_area', 'duration_months' etc. Build a
    # compatibility view so both shapes render.
    meta_raw = data.get("study_meta") or data.get("study_overview") or {}
    so = {
        "protocol_number":  meta_raw.get("protocol_number", "—"),
        "sponsor":          meta_raw.get("sponsor", "—"),
        "therapeutic_area": (meta_raw.get("therapeutic_area")
                             or meta_raw.get("indication", "—")),
        "study_phase":      meta_raw.get("study_phase", "—"),
        "study_type":       (meta_raw.get("study_type")
                             or meta_raw.get("type", "—")),
        "number_of_sites":  meta_raw.get("number_of_sites"),
        "regions":          meta_raw.get("regions"),
        "start_date":       meta_raw.get("start_date", "—"),
        "end_date":         meta_raw.get("end_date", "—"),
        "duration_months":  (meta_raw.get("duration_months")
                             or meta_raw.get("total_study_duration_months", "—")),
        "title":            (meta_raw.get("title")
                             or meta_raw.get("study_title", "See protocol")),
    }
    pp  = data.get("patient_population", {})
    vs  = data.get("visit_summary", {})
    crf = data.get("crf_summary", {})
    cf  = data.get("complexity_flags", [])
    crn = data.get("confidence_review_notes", [])
    cb  = data.get("conditional_branching", [])
    dc  = data.get("data_cleaning_estimate", {})

    # ── Normalize actual JSON shape → shape the renderer expects ────────────
    # The protocol-analysis skill emits richer, differently-keyed data than
    # this PDF script was originally written for. Build compatibility views
    # so the sections populate correctly.

    # patient_population: prefer top-level total_enrollment/number_of_arms,
    # else derive from arms[] planned_enrollment sum; map arm names.
    pp_total = pp.get("total_enrollment")
    pp_arms_raw = pp.get("arms", []) or []
    if pp_total is None:
        try:
            pp_total = sum(int(a.get("planned_enrollment") or 0)
                           for a in pp_arms_raw) or None
        except Exception:
            pp_total = None
    pp_n_arms = pp.get("number_of_arms")
    if pp_n_arms is None and pp_arms_raw:
        pp_n_arms = len(pp_arms_raw)
    pp_normalized_arms = []
    for a in pp_arms_raw:
        pp_normalized_arms.append({
            "name":        a.get("name") or a.get("arm_name") or a.get("arm_code") or "Arm",
            "n":           a.get("n") or a.get("planned_enrollment") or "?",
            "description": a.get("description", ""),
        })
    # study_meta may also carry these
    if pp_total is None:
        pp_total = meta_raw.get("total_enrollment")
    if pp_n_arms is None:
        pp_n_arms = meta_raw.get("number_of_arms")
    pp = {
        **pp,
        "total_enrollment": pp_total if pp_total is not None else "—",
        "number_of_arms":   pp_n_arms if pp_n_arms is not None else "—",
        "arms":             pp_normalized_arms,
    }

    # visit_summary: build per-arm rows, either from vs["arms"] if present
    # or from total_visits_<CODE> fields + pp arms.
    vs_arms = vs.get("arms", []) or []
    if not vs_arms and pp_arms_raw:
        for a in pp_arms_raw:
            code = a.get("arm_code") or ""
            name = a.get("arm_name") or a.get("name") or code or "Arm"
            visits_per = (vs.get(f"total_visits_{code}")
                          or vs.get(f"visits_per_patient_{code}")
                          or vs.get("visits_per_patient"))
            patients = a.get("planned_enrollment")
            if visits_per is not None and patients is not None:
                try:
                    total = int(visits_per) * int(patients)
                except Exception:
                    total = "?"
            else:
                total = vs.get(f"total_patient_visits_{code}") or "?"
            vs_arms.append({
                "name":              name,
                "visits_per_patient": visits_per if visits_per is not None else "?",
                "patients":          patients if patients is not None else "?",
                "total_visits":      total,
            })
    # compute grand total across arms if not provided
    grand = vs.get("total_patient_visits_all_arms")
    if grand is None:
        grand = 0
        any_known = False
        for a in vs_arms:
            tv = a.get("total_visits")
            if isinstance(tv, int):
                grand += tv
                any_known = True
        grand = grand if any_known else "—"
    vs = {**vs, "arms": vs_arms, "total_patient_visits_all_arms": grand}

    # crf_summary: map total_forms + forms_by_complexity into the named fields
    by_c = crf.get("forms_by_complexity") or {}
    crf = {
        **crf,
        "total_unique_crfs": crf.get("total_unique_crfs", crf.get("total_forms", "—")),
        "simple_crfs":       crf.get("simple_crfs",  by_c.get("simple", "—")),
        "average_crfs":      crf.get("average_crfs", by_c.get("average", "—")),
        "complex_crfs":      crf.get("complex_crfs", by_c.get("complex", "—")),
        "total_reuse_crfs":  crf.get("total_reuse_crfs",
                                     len(crf.get("high_frequency_forms", []))
                                     if crf.get("high_frequency_forms") is not None
                                     else "—"),
    }

    # ── Cover header ─────────────────────────────────────────────────────────
    header_data = [[
        Paragraph("PROTOCOL SUMMARY", styles["title"]),
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

    # ── Section 3 — Study Event Schedule (SoE) sub-table ────────────────────
    # Mirrors Study Spec Section 1's event-schedule table, but drops the
    # Event OID column so the Protocol Summary stays high-level for the
    # client audience. Only rendered when struct_json was provided.
    if struct_json and isinstance(struct_json, dict):
        forms_list = struct_json.get("forms", []) or []
        tpt_rows   = (struct_json.get("timepoint_csv", {}) or {}).get("rows", []) or []

        if forms_list:
            # Derive event → list of form_ids from forms[].visits_assigned
            event_map = {}
            for form in forms_list:
                for ev in form.get("visits_assigned", []) or []:
                    event_map.setdefault(ev, []).append(form.get("form_id", ""))

            # event_oid → timepoint label
            tpt_lookup = {r.get("event", ""): r.get("timepoint", "")
                          for r in tpt_rows if isinstance(r, dict)}

            soe_headers = ["Timepoint Label", "Arm", "Forms Assigned"]
            soe_cw = [CONTENT_W * 0.24, CONTENT_W * 0.12, CONTENT_W * 0.64]
            soe_data = []
            for ev, form_ids in event_map.items():
                # Derive arm from event OID convention
                if "CTL" in ev:
                    arm = "CONTROL"
                elif ev in ("SE_BASELINE", "SE_UNSCH", "SE_COMMON"):
                    arm = "BOTH"
                else:
                    arm = "TREATMENT"

                label = tpt_lookup.get(ev, ev)
                forms_str = ", ".join(form_ids[:8])
                if len(form_ids) > 8:
                    forms_str += f" +{len(form_ids) - 8} more"
                soe_data.append([label, arm, forms_str])

            if soe_data:
                story.append(Paragraph(
                    "Study Event Schedule",
                    styles.get("subhead") or styles.get("label") or styles["body"]))
                story.append(Spacer(1, 3))
                story.append(grid_table(soe_headers, soe_data, styles, soe_cw))
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

    # CRF detail — prefer crf.crf_detail if present; otherwise fall back to
    # forms_by_domain + high_frequency_forms + custom_forms summaries.
    details = crf.get("crf_detail", [])

    if details:
        crf_headers = ["#", "Domain", "CDASH", "Source", "Arms / Visits",
                       "Complexity", "Re-use CRFs", "Confidence", "Notes"]
        crf_col_w = [
            CONTENT_W * 0.03, CONTENT_W * 0.13, CONTENT_W * 0.05,
            CONTENT_W * 0.09, CONTENT_W * 0.16, CONTENT_W * 0.07,
            CONTENT_W * 0.05, CONTENT_W * 0.07, CONTENT_W * 0.35,
        ]

        def complexity_bg(val):
            if not val: return WHITE
            v = val.upper()
            if "COMPLEX" in v:  return colors.HexColor("#FADBD8")
            if "AVERAGE" in v:  return colors.HexColor("#FDEBD0")
            if "SIMPLE"  in v:  return colors.HexColor("#D5F5E3")
            return WHITE

        crf_table_data = [[Paragraph(h, styles["cell_header"]) for h in crf_headers]]
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
                    textColor=confidence_color(conf_raw), leading=10)),
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
        for i, row in enumerate(details, start=1):
            row_styles.append(("BACKGROUND", (5, i), (5, i),
                               complexity_bg(row.get("complexity", ""))))
        detail_tbl.setStyle(TableStyle(row_styles))
        story.append(detail_tbl)
    else:
        # Fallback: render by-domain, custom, high-frequency summaries
        by_dom = crf.get("forms_by_domain") or {}
        if by_dom:
            dom_data = [[Paragraph("CDASH Domain", styles["cell_header"]),
                         Paragraph("Form Count",   styles["cell_header"])]]
            for k in sorted(by_dom.keys()):
                dom_data.append([
                    Paragraph(str(k), styles["cell_bold"]),
                    Paragraph(str(by_dom[k]), styles["cell"]),
                ])
            dom_tbl = Table(dom_data,
                            colWidths=[CONTENT_W * 0.3, CONTENT_W * 0.7])
            dom_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0),  DARK_BLUE),
                ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING",    (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING",   (0, 0), (-1, -1), 6),
                ("GRID",          (0, 0), (-1, -1), 0.3, GREY_MID),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, GREY_LIGHT]),
            ]))
            story.append(Paragraph("Forms by CDASH Domain", styles["label"]))
            story.append(Spacer(1, 2))
            story.append(dom_tbl)
            story.append(Spacer(1, 6))

        custom = crf.get("custom_forms") or []
        if custom:
            story.append(Paragraph("Custom / Non-CDASH Forms", styles["label"]))
            story.append(Spacer(1, 2))
            story.append(kv_table(
                [(f"Custom Form {i+1}", str(v)) for i, v in enumerate(custom)],
                styles, [CONTENT_W * 0.2, CONTENT_W * 0.8]
            ))
            story.append(Spacer(1, 6))

        hifreq = crf.get("high_frequency_forms") or []
        if hifreq:
            story.append(Paragraph("High-Frequency Forms", styles["label"]))
            story.append(Spacer(1, 2))
            story.append(kv_table(
                [(f"#{i+1}", str(v)) for i, v in enumerate(hifreq)],
                styles, [CONTENT_W * 0.06, CONTENT_W * 0.94]
            ))
    story.append(Spacer(1, 10))

    # ── Section 5: Complexity Flags ───────────────────────────────────────────
    story.append(PageBreak())
    story.append(header_band("SECTION 5 — COMPLEXITY FLAGS", styles))
    story.append(Spacer(1, 4))

    # Normalize complexity_flags into a list of (label, detail) rows.
    # Claude may emit a dict (keyed by flag name) or a list of strings or dicts.
    flag_rows = []
    def _fmt_val(v):
        if isinstance(v, (list, tuple)):
            return "  •  ".join(str(x) for x in v)
        if isinstance(v, dict):
            return ",  ".join(f"{k}: {v2}" for k, v2 in v.items())
        return str(v)

    if isinstance(cf, dict):
        for k, v in cf.items():
            flag_rows.append((k.replace("_", " ").title(), _fmt_val(v)))
    elif isinstance(cf, list):
        for item in cf:
            if isinstance(item, dict):
                label = (item.get("name") or item.get("flag")
                         or item.get("label") or "")
                detail = (item.get("value") or item.get("detail")
                          or item.get("description") or _fmt_val(item))
                flag_rows.append((label, detail))
            else:
                flag_rows.append(("", str(item)))

    flag_data = []
    for i, (label, detail) in enumerate(flag_rows, 1):
        flag_data.append([
            Paragraph(str(i), styles["cell_bold"]),
            Paragraph(f"<b>{label}</b>" if label else "", styles["cell_bold"]),
            Paragraph(detail, styles["body_small"]),
        ])
    if flag_data:
        flag_tbl = Table(flag_data,
                         colWidths=[CONTENT_W * 0.04,
                                    CONTENT_W * 0.22,
                                    CONTENT_W * 0.74])
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

    # ── Section 6: Review Flags (counts + critical items) ────────────────────
    story.append(header_band("SECTION 6 — REVIEW FLAGS", styles))
    story.append(Spacer(1, 4))

    rf = data.get("review_flags", {}) or {}
    if isinstance(rf, dict):
        count_labels = [
            ("Site Specific",           rf.get("site_specific_count")),
            ("OID Confirmation",        rf.get("oid_confirmation_count")),
            ("Protocol Ambiguous",      rf.get("protocol_ambiguous_count")),
            ("Constraint Review",       rf.get("constraint_review_count")),
            ("Choice List Review",      rf.get("choice_list_review_count")),
            ("Custom Domain",           rf.get("custom_domain_count")),
            ("PDF Mapping Uncertain",   rf.get("pdf_mapping_uncertain_count")),
            ("Name Deviation",          rf.get("name_deviation_count")),
            ("TOTAL FLAGS",             rf.get("total_flags")),
        ]
        rf_rows = [(label, str(val) if val is not None else "—")
                   for label, val in count_labels]
        story.append(kv_table(rf_rows, styles,
                              [CONTENT_W * 0.25, CONTENT_W * 0.75]))
        story.append(Spacer(1, 6))

        crit = rf.get("critical_items") or []
        if crit:
            story.append(Paragraph("Critical Items Requiring Attention",
                                   styles["label"]))
            story.append(Spacer(1, 2))
            ci_data = [[Paragraph(str(i + 1), styles["cell_bold"]),
                        Paragraph(str(item), styles["body_small"])]
                       for i, item in enumerate(crit)]
            ci_tbl = Table(ci_data,
                           colWidths=[CONTENT_W * 0.04, CONTENT_W * 0.96])
            ci_tbl.setStyle(TableStyle([
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING",    (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING",   (0, 0), (-1, -1), 6),
                ("ROWBACKGROUNDS",(0, 0), (-1, -1), [WHITE, GREY_LIGHT]),
                ("LINEBELOW",     (0, 0), (-1, -1), 0.3, GREY_MID),
            ]))
            story.append(ci_tbl)
    story.append(Spacer(1, 10))

    # ── Section 7: Modules Detected ──────────────────────────────────────────
    story.append(header_band("SECTION 7 — MODULES DETECTED", styles))
    story.append(Spacer(1, 4))

    # Clarifying description
    story.append(Paragraph(
        "Modules represent OpenClinica feature areas that drive "
        "subscription licensing and build effort. Items shown are the "
        "<b>form IDs</b> from the Study Specification that populate each "
        "module (e.g. F14_AE = Adverse Events form). Modules with no "
        "forms are listed as \"— none —\".",
        styles["body_small"]
    ))
    story.append(Spacer(1, 6))

    mods = data.get("modules_detected", {}) or {}
    if isinstance(mods, dict) and mods:
        mod_rows = []
        for mod_name, forms_list in mods.items():
            label = mod_name.replace("_", " ").title()
            if isinstance(forms_list, list):
                value = ", ".join(str(f) for f in forms_list) if forms_list else "— none —"
            else:
                value = str(forms_list)
            mod_rows.append((label, value))
        story.append(kv_table(mod_rows, styles,
                              [CONTENT_W * 0.22, CONTENT_W * 0.78]))
    story.append(Spacer(1, 10))

    # ── Section 8: Legacy conditional branching / data cleaning (if present) ─
    if cb or dc:
        story.append(PageBreak())
        story.append(header_band("SECTION 8 — ADDITIONAL NOTES", styles))
        story.append(Spacer(1, 4))

    if cb:
        story.append(Paragraph(
            "<b>Conditional Branching</b>",
            ParagraphStyle("subheader8a", fontName="Helvetica-Bold",
                           fontSize=10, textColor=DARK_BLUE, leading=14,
                           spaceBefore=4, spaceAfter=4)
        ))
        branch_headers = ["#", "Description", "Type", "Affected Domains", "Confidence", "Note"]
        branch_col_w   = [CONTENT_W * 0.03, CONTENT_W * 0.30, CONTENT_W * 0.09,
                          CONTENT_W * 0.18, CONTENT_W * 0.08, CONTENT_W * 0.32]
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
                    textColor=confidence_color(conf_raw), leading=10)),
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

    if dc and dc.get("domains"):
        story.append(Paragraph(
            "<b>Data Cleaning Logic</b>",
            ParagraphStyle("subheader8b", fontName="Helvetica-Bold",
                           fontSize=10, textColor=DARK_BLUE, leading=14,
                           spaceBefore=6, spaceAfter=4)
        ))
        dc_headers = ["Domain", "CDASH", "Complexity", "Implied Checks"]
        dc_col_w   = [CONTENT_W * 0.15, CONTENT_W * 0.06,
                      CONTENT_W * 0.09, CONTENT_W * 0.70]
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
            dc_data.append([
                Paragraph(dom.get("domain", ""), styles["cell_bold"]),
                Paragraph(dom.get("cdash_code", ""), styles["cell"]),
                Paragraph(dom.get("complexity_rating", ""), styles["cell_bold"]),
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
            dc_row_styles.append(("BACKGROUND", (2, i), (2, i),
                                  dc_bg(dom.get("complexity_rating", ""))))
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

# ── Alias so the function can also be imported by its skill-level name ────
build_protocol_summary_pdf = build_pricing_pdf

