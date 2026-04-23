"""
generate_pdf.py — EDC Structure Summary PDF Generator
Generates a formatted landscape PDF from the EDC structure JSON output.
Called by the protocol-to-edc-structure skill.

Usage:
    from generate_pdf import build_edc_pdf
    build_edc_pdf(data_dict, output_path)
"""

from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER
import datetime
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from dep_utils import (
    extract_all_form_dependencies, extract_row_dependencies,
    annotate_survey_with_dependencies, format_deps_short
)

# ── Colour palette (shared with pricing summary) ──────────────────────────────
DARK_BLUE  = colors.HexColor("#1B3A6B")
MID_BLUE   = colors.HexColor("#2E6DA4")
LIGHT_BLUE = colors.HexColor("#D6E4F0")
WHITE      = colors.white
GREY_LIGHT = colors.HexColor("#F5F5F5")
GREY_MID   = colors.HexColor("#CCCCCC")
GREY_DARK  = colors.HexColor("#555555")
RED_FLAG   = colors.HexColor("#C0392B")
AMBER_FLAG = colors.HexColor("#E67E22")
GREEN_FLAG = colors.HexColor("#27AE60")
TEAL       = colors.HexColor("#1A7A6B")
TEXT_DARK  = colors.HexColor("#1A1A1A")

PAGE_W, PAGE_H = landscape(A4)
MARGIN    = 1.8 * cm
CONTENT_W = PAGE_W - 2 * MARGIN


# ── Styles ────────────────────────────────────────────────────────────────────
def make_styles():
    s = {}
    s["title"] = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=16,
        textColor=WHITE, alignment=TA_LEFT, spaceAfter=2)
    s["subtitle"] = ParagraphStyle("subtitle", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#BDD7EE"), alignment=TA_LEFT, spaceAfter=0)
    s["section_header"] = ParagraphStyle("section_header", fontName="Helvetica-Bold",
        fontSize=10, textColor=WHITE, alignment=TA_LEFT, leftIndent=6)
    s["subsection"] = ParagraphStyle("subsection", fontName="Helvetica-Bold",
        fontSize=9, textColor=DARK_BLUE, spaceBefore=6, spaceAfter=3)
    s["body"] = ParagraphStyle("body", fontName="Helvetica", fontSize=8.5,
        textColor=TEXT_DARK, leading=13, spaceAfter=4)
    s["body_small"] = ParagraphStyle("body_small", fontName="Helvetica", fontSize=7.5,
        textColor=TEXT_DARK, leading=11, spaceAfter=2)
    s["label"] = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8.5,
        textColor=DARK_BLUE, spaceAfter=2)
    s["cell"] = ParagraphStyle("cell", fontName="Helvetica", fontSize=7,
        textColor=TEXT_DARK, leading=9)
    s["cell_bold"] = ParagraphStyle("cell_bold", fontName="Helvetica-Bold", fontSize=7,
        textColor=DARK_BLUE, leading=9)
    s["cell_header"] = ParagraphStyle("cell_header", fontName="Helvetica-Bold", fontSize=7.5,
        textColor=WHITE, leading=10, alignment=TA_CENTER)
    s["cell_mono"] = ParagraphStyle("cell_mono", fontName="Courier", fontSize=6.5,
        textColor=TEXT_DARK, leading=9)
    s["pending"] = ParagraphStyle("pending", fontName="Helvetica-Oblique", fontSize=8,
        textColor=RED_FLAG, leading=11)
    s["flagged"] = ParagraphStyle("flagged", fontName="Helvetica-Oblique", fontSize=7,
        textColor=AMBER_FLAG, leading=9)
    s["complete"] = ParagraphStyle("complete", fontName="Helvetica", fontSize=7,
        textColor=GREEN_FLAG, leading=9)
    s["disclaimer"] = ParagraphStyle("disclaimer", fontName="Helvetica-Oblique", fontSize=7.5,
        textColor=GREY_DARK, leading=11, spaceBefore=4)
    return s


# ── Reusable components ───────────────────────────────────────────────────────
def header_band(text, styles, width=None):
    w = width or CONTENT_W
    tbl = Table([[Paragraph(text, styles["section_header"])]], colWidths=[w])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), DARK_BLUE),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("RIGHTPADDING",  (0,0),(-1,-1), 8),
    ]))
    return tbl


def sub_band(text, styles, width=None):
    w = width or CONTENT_W
    tbl = Table([[Paragraph(text, styles["subsection"])]], colWidths=[w])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), LIGHT_BLUE),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("LINEBELOW",     (0,0),(-1,-1), 0.5, MID_BLUE),
    ]))
    return tbl


def kv_table(rows, styles, col_widths=None):
    cw = col_widths or [CONTENT_W * 0.28, CONTENT_W * 0.72]
    data = []
    for k, v in rows:
        v_str = str(v) if v is not None else "—"
        if "NOT SPECIFIED" in v_str or "PLACEHOLDER" in v_str:
            val_para = Paragraph(v_str, styles["pending"])
        else:
            val_para = Paragraph(v_str, styles["body_small"])
        data.append([Paragraph(k, styles["label"]), val_para])
    tbl = Table(data, colWidths=cw)
    tbl.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ("RIGHTPADDING",  (0,0),(-1,-1), 6),
        ("ROWBACKGROUNDS",(0,0),(-1,-1), [WHITE, GREY_LIGHT]),
        ("LINEBELOW",     (0,0),(-1,-1), 0.3, GREY_MID),
    ]))
    return tbl


def status_color(status):
    if not status: return TEXT_DARK
    s = str(status).upper()
    if "COMPLETE" in s: return GREEN_FLAG
    if "FLAGGED"  in s: return AMBER_FLAG
    if "PLACEHOLDER" in s: return RED_FLAG
    return TEXT_DARK


def match_color(match):
    if not match: return TEXT_DARK
    m = str(match).upper()
    if "EXACT"    in m: return GREEN_FLAG
    if "PARTIAL"  in m: return AMBER_FLAG
    if "NO_MATCH" in m: return RED_FLAG
    if "PROTOCOL_ONLY" in m: return MID_BLUE
    return TEXT_DARK


def grid_table(headers, rows, styles, col_widths, zebra=True):
    data = [[Paragraph(h, styles["cell_header"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c) if c is not None else "—", styles["cell"]) for c in row])
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    ts = [
        ("BACKGROUND",    (0,0),(-1,0),  DARK_BLUE),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("TOPPADDING",    (0,0),(-1,-1), 3),
        ("BOTTOMPADDING", (0,0),(-1,-1), 3),
        ("LEFTPADDING",   (0,0),(-1,-1), 3),
        ("RIGHTPADDING",  (0,0),(-1,-1), 3),
        ("GRID",          (0,0),(-1,-1), 0.3, GREY_MID),
    ]
    if zebra:
        ts.append(("ROWBACKGROUNDS", (0,1),(-1,-1), [WHITE, GREY_LIGHT]))
    tbl.setStyle(TableStyle(ts))
    return tbl


# ── Main builder ──────────────────────────────────────────────────────────────
def build_edc_pdf(data: dict, output_path: str):
    styles = make_styles()
    meta      = data.get("study_meta", {})
    tpt_csv   = data.get("timepoint_csv", {})
    labranges = data.get("labranges_csv", {})
    forms     = data.get("forms", [])
    flags     = data.get("review_flags", {})
    xdeps     = data.get("cross_form_dependencies", [])

    # Study ID: prefer explicit study_id, fall back to protocol_number
    study_id_display = (meta.get("study_id")
                        or meta.get("protocol_number")
                        or "STUDY")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{study_id_display} Study Specification",
    )
    # Reassemble story list (doc was created above, below rest stays)
    story = []

    # ── Cover header ─────────────────────────────────────────────────────────
    mode_disp = meta.get("input_mode", "PROTOCOL_ONLY").replace("_", " ")
    header_data = [[
        Paragraph(f"{study_id_display} STUDY SPECIFICATION", styles["title"]),
        Paragraph(
            f"Generated: {datetime.date.today().strftime('%d %b %Y')}  |  "
            f"Status: <b>PENDING HUMAN REVIEW — DO NOT BUILD</b>  |  Mode: {mode_disp}",
            styles["subtitle"]
        )
    ]]
    header_tbl = Table(header_data, colWidths=[CONTENT_W * 0.55, CONTENT_W * 0.45])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), DARK_BLUE),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 12),
        ("BOTTOMPADDING", (0,0),(-1,-1), 12),
        ("LEFTPADDING",   (0,0),(-1,-1), 12),
        ("RIGHTPADDING",  (0,0),(-1,-1), 12),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 8))

    # ── Study meta strip ─────────────────────────────────────────────────────
    meta_rows = [
        ("Protocol Number", meta.get("protocol_number", "—")),
        ("Study ID",        study_id_display),
        ("Library Files",   ", ".join(meta.get("library_files_provided", [])) or "None"),
    ]
    story.append(kv_table(meta_rows, styles, [CONTENT_W*0.15, CONTENT_W*0.85]))
    story.append(Spacer(1, 10))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 1 — Study Event Schedule
    # ─────────────────────────────────────────────────────────────────────────
    story.append(header_band("SECTION 1 — STUDY EVENT SCHEDULE", styles))
    story.append(Spacer(1, 4))

    tpt_rows = tpt_csv.get("rows", [])
    # Build event schedule table from forms data — derive unique events
    event_map = {}
    for form in forms:
        for ev in form.get("visits_assigned", []):
            if ev not in event_map:
                event_map[ev] = {"forms": []}
            event_map[ev]["forms"].append(form.get("form_id", ""))

    # Merge timepoint labels
    tpt_lookup = {r["event"]: r["timepoint"] for r in tpt_rows}

    sched_headers = ["Event OID", "Timepoint Label", "Arm", "Forms Assigned"]
    sched_cw = [CONTENT_W*0.16, CONTENT_W*0.20, CONTENT_W*0.10, CONTENT_W*0.54]
    sched_data = []
    for ev, info in event_map.items():
        arm = "TREATMENT" if "CTL" not in ev and ev not in ["SE_BASELINE","SE_UNSCH"] else \
              "CONTROL" if "CTL" in ev else "BOTH"
        if ev in ["SE_BASELINE", "SE_UNSCH"]: arm = "BOTH"
        label = tpt_lookup.get(ev, ev)
        forms_str = ", ".join(info["forms"][:8])
        if len(info["forms"]) > 8:
            forms_str += f" +{len(info['forms'])-8} more"
        sched_data.append([ev, label, arm, forms_str])

    story.append(grid_table(sched_headers, sched_data, styles, sched_cw))
    story.append(Spacer(1, 10))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 2 — Timepoint CSV
    # ─────────────────────────────────────────────────────────────────────────
    story.append(header_band("SECTION 2 — TIMEPOINT CSV", styles))
    story.append(Spacer(1, 4))

    story.append(kv_table([
        ("Filename", tpt_csv.get("filename", "—")),
        ("Rows",     str(len(tpt_rows))),
    ], styles, [CONTENT_W*0.12, CONTENT_W*0.88]))
    story.append(Spacer(1, 4))

    csv_headers = ["event", "timepoint"]
    csv_cw = [CONTENT_W*0.30, CONTENT_W*0.70]
    csv_data = [[r.get("event",""), r.get("timepoint","")] for r in tpt_rows]
    story.append(grid_table(csv_headers, csv_data, styles, csv_cw))
    story.append(Spacer(1, 10))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 3 — Form Inventory
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(header_band("SECTION 3 — FORM INVENTORY", styles))
    story.append(Spacer(1, 4))

    inv_headers = ["#", "Form OID", "Form Title", "Category", "CDASH", "Repeating",
                   "Library Match", "Fields (est.)", "Flagged", "Placeholder", "Dependencies"]
    inv_cw = [
        CONTENT_W*0.03, CONTENT_W*0.06, CONTENT_W*0.12, CONTENT_W*0.09,
        CONTENT_W*0.05, CONTENT_W*0.06, CONTENT_W*0.10,
        CONTENT_W*0.07, CONTENT_W*0.06, CONTENT_W*0.07, CONTENT_W*0.22
    ]

    inv_data = []
    for i, form in enumerate(forms, 1):
        lm    = form.get("library_match", {})
        match = lm.get("status", "PROTOCOL_ONLY")
        total = lm.get("fields_from_library",0) + lm.get("fields_extended_from_protocol",0) + lm.get("fields_from_cdash_default",0)
        # Count flagged/placeholder from survey
        flagged = sum(1 for r in form.get("survey",[]) if r.get("completion_status","") == "FLAGGED")
        placeholder = sum(1 for r in form.get("survey",[]) if r.get("completion_status","") == "PLACEHOLDER")
        rep = "Yes" if form.get("has_repeating_group") else "No"
        form_deps = extract_all_form_dependencies(form)
        inv_data.append([
            str(i),
            form.get("form_id",""),
            form.get("form_title",""),
            form.get("form_category","").replace("CDASH_CLINICAL","CDASH").replace("INFRASTRUCTURE","INFRA"),
            form.get("cdash_domain","") or "—",
            rep,
            match.replace("PROTOCOL_ONLY","Protocol Only").replace("_"," "),
            str(total) if total else "—",
            str(flagged) if flagged else "—",
            str(placeholder) if placeholder else "—",
            format_deps_short(form_deps, max_items=3),
        ])

    inv_tbl = Table(
        [[Paragraph(h, styles["cell_header"]) for h in inv_headers]] +
        [[Paragraph(str(c), styles["cell"]) for c in row] for row in inv_data],
        colWidths=inv_cw, repeatRows=1
    )

    inv_ts = [
        ("BACKGROUND",    (0,0),(-1,0),  DARK_BLUE),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("TOPPADDING",    (0,0),(-1,-1), 3),
        ("BOTTOMPADDING", (0,0),(-1,-1), 3),
        ("LEFTPADDING",   (0,0),(-1,-1), 3),
        ("RIGHTPADDING",  (0,0),(-1,-1), 3),
        ("GRID",          (0,0),(-1,-1), 0.3, GREY_MID),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, GREY_LIGHT]),
    ]
    # Color the Library Match column
    for i, form in enumerate(forms, 1):
        lm_status = form.get("library_match",{}).get("status","")
        bg = colors.HexColor("#D5F5E3") if "EXACT" in lm_status else \
             colors.HexColor("#FDEBD0") if "PARTIAL" in lm_status else \
             colors.HexColor("#FADBD8") if "NO_MATCH" in lm_status else \
             colors.HexColor("#EBF5FB")
        inv_ts.append(("BACKGROUND", (6,i), (6,i), bg))

    inv_tbl.setStyle(TableStyle(inv_ts))
    story.append(inv_tbl)
    story.append(Spacer(1, 10))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 4 — Form Definitions
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(header_band("SECTION 4 — FORM DEFINITIONS", styles))
    story.append(Spacer(1, 6))

    for i, form in enumerate(forms, 1):
        form_id    = form.get("form_id","")
        form_title = form.get("form_title","")
        category   = form.get("form_category","")
        cdash      = form.get("cdash_domain","") or "—"
        lm         = form.get("library_match", {})
        settings   = form.get("settings", {})
        survey     = form.get("survey", [])
        choices    = form.get("choices", [])
        visits     = form.get("visits_assigned", [])
        has_repeat = form.get("has_repeating_group", False)

        # Count by status
        n_complete    = sum(1 for r in survey if r.get("completion_status","") == "COMPLETE")
        n_flagged     = sum(1 for r in survey if r.get("completion_status","") == "FLAGGED")
        n_placeholder = sum(1 for r in survey if r.get("completion_status","") == "PLACEHOLDER")

        block = []

        # Form sub-header
        block.append(sub_band(
            f"{i}. {form_title}  ({form_id})  —  "
            f"{'CDASH: ' + cdash if cdash != '—' else category.replace('_',' ')}",
            styles
        ))
        block.append(Spacer(1, 3))

        # Settings + meta row
        meta_left = [
            ("Form OID",      settings.get("form_id", form_id)),
            ("Form Title",    settings.get("form_title", form_title)),
            ("Version",       settings.get("version","1")),
            ("Style",         settings.get("style","theme-grid")),
            ("Category",      category.replace("_"," ")),
        ]
        form_all_deps = extract_all_form_dependencies(form)
        meta_right = [
            ("Visits",        ", ".join(visits[:6]) + (f" +{len(visits)-6} more" if len(visits)>6 else "")),
            ("Repeating",     "Yes" if has_repeat else "No"),
            ("Library Match", lm.get("status","PROTOCOL_ONLY").replace("_"," ")),
            ("Data Items",    f"{len(survey)} total  ({n_complete} complete / {n_flagged} flagged / {n_placeholder} placeholder)"),
            ("Choice Lists",  f"{len(set(c.get('list_name','') for c in choices))} lists, {len(choices)} choices"),
            ("Dependencies",  ", ".join(form_all_deps) if form_all_deps else "None"),
        ]

        half = CONTENT_W/2 - 4
        left_tbl  = kv_table(meta_left,  styles, [half*0.38, half*0.62])
        right_tbl = kv_table(meta_right, styles, [half*0.38, half*0.62])
        two_col = Table([[left_tbl, right_tbl]], colWidths=[half, half])
        two_col.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),0),
            ("RIGHTPADDING",(0,0),(-1,-1),4),
        ]))
        block.append(two_col)
        block.append(Spacer(1, 4))

        # Survey rows table — includes Appearance and Dependencies columns
        if survey:
            annotate_survey_with_dependencies(survey, form)
            s_headers = ["type", "name", "label", "itemgroup", "appearance",
                         "relevant", "required", "constraint", "calculation",
                         "dependencies", "status"]
            # NOTE: itemgroup widened so the header "itemgroup" (9 chars) fits
            # on a single line. Compensating width reductions elsewhere.
            s_cw = [
                CONTENT_W*0.07, CONTENT_W*0.08, CONTENT_W*0.11, CONTENT_W*0.08,
                CONTENT_W*0.07, CONTENT_W*0.10, CONTENT_W*0.05, CONTENT_W*0.10,
                CONTENT_W*0.10, CONTENT_W*0.12, CONTENT_W*0.06
            ]

            show_rows = survey[:30]
            s_data = [[Paragraph(h, styles["cell_header"]) for h in s_headers]]
            for row in show_rows:
                st = row.get("completion_status","")
                st_style = styles["complete"] if st=="COMPLETE" else \
                           styles["flagged"]  if st=="FLAGGED"  else \
                           styles["pending"]  if st=="PLACEHOLDER" else styles["cell"]
                row_deps = row.get("dependencies", [])
                s_data.append([
                    Paragraph(str(row.get("type",""))[:25],       styles["cell_bold"]),
                    Paragraph(str(row.get("name",""))[:20],       styles["cell_mono"]),
                    Paragraph(str(row.get("label",""))[:35],      styles["cell"]),
                    Paragraph(str(row.get("bind__oc_itemgroup","") or "")[:14], styles["cell"]),
                    Paragraph(str(row.get("appearance","") or "")[:18],         styles["cell_mono"]),
                    Paragraph(str(row.get("relevant","") or "")[:30],           styles["cell_mono"]),
                    Paragraph(str(row.get("required","") or "")[:8],            styles["cell"]),
                    Paragraph(str(row.get("constraint","") or "")[:30],         styles["cell_mono"]),
                    Paragraph(str(row.get("calculation","") or "")[:30],        styles["cell_mono"]),
                    Paragraph(format_deps_short(row_deps, 2),                   styles["cell_mono"]),
                    Paragraph(st[:11], st_style),
                ])

            s_tbl = Table(s_data, colWidths=s_cw, repeatRows=1)
            s_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0,0),(-1,0),  DARK_BLUE),
                ("VALIGN",        (0,0),(-1,-1), "TOP"),
                ("TOPPADDING",    (0,0),(-1,-1), 2),
                ("BOTTOMPADDING", (0,0),(-1,-1), 2),
                ("LEFTPADDING",   (0,0),(-1,-1), 2),
                ("RIGHTPADDING",  (0,0),(-1,-1), 2),
                ("GRID",          (0,0),(-1,-1), 0.3, GREY_MID),
                ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, GREY_LIGHT]),
            ]))
            block.append(s_tbl)

            if len(survey) > 30:
                block.append(Paragraph(
                    f"... {len(survey)-30} additional survey rows not shown. "
                    f"See JSON output for complete form definition.",
                    styles["disclaimer"]
                ))

        # Choice lists table — one row per choice: list_name, label, name, source
        if choices:
            block.append(Spacer(1, 4))
            n_lists = len(set(c.get("list_name","") for c in choices))
            ch_band = Table(
                [[Paragraph(
                    f"CHOICE LISTS  ({n_lists} lists, {len(choices)} options)",
                    ParagraphStyle("ch_hdr2", fontName="Helvetica-Bold", fontSize=7.5,
                    textColor=WHITE, leftIndent=4))]],
                colWidths=[CONTENT_W]
            )
            ch_band.setStyle(TableStyle([
                ("BACKGROUND",    (0,0),(-1,-1), MID_BLUE),
                ("TOPPADDING",    (0,0),(-1,-1), 3),
                ("BOTTOMPADDING", (0,0),(-1,-1), 3),
                ("LEFTPADDING",   (0,0),(-1,-1), 6),
            ]))
            block.append(ch_band)

            ch_headers = ["list_name", "label", "name", "source"]
            ch_cw = [CONTENT_W*0.18, CONTENT_W*0.32, CONTENT_W*0.25, CONTENT_W*0.25]

            ch_data = [[Paragraph(h, styles["cell_header"]) for h in ch_headers]]

            list_names_order = list(dict.fromkeys(c.get("list_name","") for c in choices))
            list_bg = {ln: (GREY_LIGHT if idx % 2 == 0 else WHITE)
                       for idx, ln in enumerate(list_names_order)}

            for ch in choices:
                ln  = ch.get("list_name","")
                src = ch.get("source","")
                src_style = ParagraphStyle(
                    "src_ch", fontName="Helvetica-Oblique", fontSize=6.5, leading=9,
                    textColor=AMBER_FLAG if src == "PROTOCOL_SPECIFIC"
                              else colors.HexColor("#555555")
                )
                ch_data.append([
                    Paragraph(ln,                  styles["cell_bold"]),
                    Paragraph(ch.get("label",""),  styles["cell"]),
                    Paragraph(ch.get("name",""),   styles["cell_mono"]),
                    Paragraph(src[:22],            src_style),
                ])

            ch_tbl = Table(ch_data, colWidths=ch_cw, repeatRows=1)
            ch_ts = [
                ("BACKGROUND",    (0,0),(-1,0),  MID_BLUE),
                ("VALIGN",        (0,0),(-1,-1), "TOP"),
                ("TOPPADDING",    (0,0),(-1,-1), 2),
                ("BOTTOMPADDING", (0,0),(-1,-1), 2),
                ("LEFTPADDING",   (0,0),(-1,-1), 3),
                ("GRID",          (0,0),(-1,-1), 0.3, GREY_MID),
            ]
            for row_i, ch in enumerate(choices, start=1):
                ch_ts.append(("BACKGROUND", (0,row_i), (-1,row_i),
                               list_bg.get(ch.get("list_name",""), WHITE)))
            ch_tbl.setStyle(TableStyle(ch_ts))
            block.append(ch_tbl)

        # Flagged items summary
        flagged_items = [r for r in survey if r.get("completion_status") in ("FLAGGED","PLACEHOLDER")]
        if flagged_items:
            block.append(Spacer(1, 4))
            flag_data = []
            for r in flagged_items[:8]:
                flag_data.append([
                    Paragraph(r.get("name",""), styles["cell_bold"]),
                    Paragraph(r.get("completion_status",""), ParagraphStyle(
                        "fs", fontName="Helvetica-Bold", fontSize=7,
                        textColor=AMBER_FLAG if r.get("completion_status")=="FLAGGED" else RED_FLAG,
                        leading=9
                    )),
                    Paragraph(r.get("flag_reason","") or r.get("bind__oc_external","") or "Requires review", styles["cell"]),
                ])
            flag_tbl = Table(flag_data, colWidths=[CONTENT_W*0.15, CONTENT_W*0.12, CONTENT_W*0.73])
            flag_tbl.setStyle(TableStyle([
                ("VALIGN",        (0,0),(-1,-1), "TOP"),
                ("TOPPADDING",    (0,0),(-1,-1), 3),
                ("BOTTOMPADDING", (0,0),(-1,-1), 3),
                ("LEFTPADDING",   (0,0),(-1,-1), 4),
                ("BACKGROUND",    (0,0),(-1,-1), colors.HexColor("#FFFDE7")),
                ("LINEBELOW",     (0,0),(-1,-1), 0.3, GREY_MID),
            ]))
            block.append(flag_tbl)
            if len(flagged_items) > 8:
                block.append(Paragraph(
                    f"... {len(flagged_items)-8} more flagged items. See JSON output.",
                    styles["disclaimer"]
                ))

        block.append(Spacer(1, 8))
        story.extend([KeepTogether(block[:4])] + block[4:])

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 5 — Cross-Form Dependency Map
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(header_band("SECTION 5 — CROSS-FORM DEPENDENCY MAP", styles))
    story.append(Spacer(1, 4))

    # Explanatory paragraph
    story.append(Paragraph(
        "This section lists every data field on one form that references or "
        "pulls data from a field on <b>another</b> form. Examples include "
        "subject identification (<i>SUBJID</i> carried from Demographics to "
        "every other form), creatinine clearance auto-calculation (which "
        "needs <i>AGE</i> from Demographics and <i>WEIGHT</i> from Vital "
        "Signs to compute on the Lab form), and linking a Serious Adverse "
        "Event record back to its parent Adverse Event.",
        styles["body_small"]
    ))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        "Every row is marked <b>OID CONFIRMATION REQUIRED</b> because "
        "OpenClinica assigns the actual OID paths (e.g. "
        "<font face='Courier'>F_DEMO.SUBJID</font>) at study-build time. "
        "After the study is built in OC, each row becomes a QA checklist "
        "item to replace the placeholder path with the real OID reference "
        "(typically an XPath expression using "
        "<font face='Courier'>instance('clinicaldata')</font>).",
        styles["body_small"]
    ))
    story.append(Spacer(1, 6))

    # Collect all cross_form_dependencies from forms
    all_deps = []
    for form in forms:
        for dep in form.get("cross_form_dependencies", []):
            all_deps.append({**dep, "_target_form": form.get("form_id","")})

    if all_deps:
        # Primary table — dotted OID notation + target + purpose
        dep_headers = ["Source Item OID", "Target Form", "Purpose", "Visit Context", "Status"]
        dep_cw = [CONTENT_W*0.18, CONTENT_W*0.10, CONTENT_W*0.28,
                  CONTENT_W*0.15, CONTENT_W*0.29]
        dep_data = []
        for d in all_deps:
            # Prefer source_item_oid (dotted notation) when Claude provides it;
            # fall back to composing from source_form + source_field.
            source_oid = d.get("source_item_oid")
            if not source_oid:
                sf = d.get("source_form", "")
                fd = d.get("source_field", "")
                if sf and fd:
                    source_oid = f"{sf}.{fd}"
                else:
                    source_oid = sf or fd or ""
            dep_data.append([
                source_oid,
                d.get("_target_form",""),
                d.get("purpose",""),
                d.get("visit_context",""),
                d.get("status","FLAGGED — OID CONFIRMATION REQUIRED"),
            ])
        story.append(grid_table(dep_headers, dep_data, styles, dep_cw))
        story.append(Spacer(1, 8))

        # Secondary: full XPath expressions for every dependency that has one
        deps_with_xpath = [d for d in all_deps if d.get("xpath_expression")]
        if deps_with_xpath:
            story.append(Paragraph(
                "<b>XPath Expressions</b>",
                ParagraphStyle(
                    "xpath_header", fontName="Helvetica-Bold", fontSize=9.5,
                    textColor=DARK_BLUE, leading=12,
                    spaceBefore=2, spaceAfter=4
                )
            ))
            story.append(Paragraph(
                "Full OpenClinica XPath expressions for each cross-form "
                "reference. Use these in the XLSForm <code>calculation</code> "
                "column (together with <code>bind::oc:external = clinicaldata</code>) "
                "to pull the source value into the target form.",
                styles["body_small"]
            ))
            story.append(Spacer(1, 4))

            xpath_headers = ["Source Item OID", "XPath Expression"]
            xpath_cw = [CONTENT_W * 0.20, CONTENT_W * 0.80]
            xpath_data = []
            for d in deps_with_xpath:
                source_oid = d.get("source_item_oid") or \
                    (f"{d.get('source_form','')}.{d.get('source_field','')}"
                     if d.get("source_form") and d.get("source_field") else "")
                xpath = d.get("xpath_expression", "") or ""
                # Pass expression through as monospace cell — wrap in paragraph
                # so long XPaths wrap inside the cell instead of overflowing
                xpath_data.append([
                    Paragraph(source_oid, styles["cell_mono"]),
                    Paragraph(xpath, styles["cell_mono"]),
                ])
            xpath_table_data = [[
                Paragraph(h, styles["cell_header"]) for h in xpath_headers
            ]] + xpath_data
            xpath_tbl = Table(xpath_table_data, colWidths=xpath_cw, repeatRows=1)
            xpath_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0),  DARK_BLUE),
                ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING",   (0, 0), (-1, -1), 4),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
                ("GRID",          (0, 0), (-1, -1), 0.3, GREY_MID),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, GREY_LIGHT]),
            ]))
            story.append(xpath_tbl)
    else:
        story.append(Paragraph(
            "Cross-form dependencies will be populated after OID confirmation. "
            "See Section 7 for the full list of dependencies requiring review.",
            styles["body_small"]
        ))
    story.append(Spacer(1, 10))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 6 — Lab Ranges CSV Placeholder
    # ─────────────────────────────────────────────────────────────────────────
    story.append(header_band("SECTION 6 — LAB RANGES CSV PLACEHOLDER", styles))
    story.append(Spacer(1, 4))

    story.append(kv_table([
        ("Filename",   labranges.get("filename","labranges.csv")),
        ("Columns",    ", ".join(labranges.get("columns",[]))),
        ("Test Rows",  str(len(labranges.get("rows",[])))),
        ("Note",       "All lower/upper/unit values are PLACEHOLDERS — site-specific values required"),
    ], styles, [CONTENT_W*0.12, CONTENT_W*0.88]))
    story.append(Spacer(1, 4))

    lr_rows = labranges.get("rows", [])
    if lr_rows:
        lr_headers = ["test_code", "test_name", "lower", "upper", "unit", "lab_name"]
        lr_cw = [CONTENT_W*0.08, CONTENT_W*0.22, CONTENT_W*0.15,
                 CONTENT_W*0.15, CONTENT_W*0.15, CONTENT_W*0.25]
        lr_data = []
        for r in lr_rows:
            lr_data.append([
                r.get("test_code",""), r.get("test_name",""),
                r.get("lower","[PLACEHOLDER]"), r.get("upper","[PLACEHOLDER]"),
                r.get("unit","[PLACEHOLDER]"),  r.get("lab_name","[PLACEHOLDER]"),
            ])
        story.append(grid_table(lr_headers, lr_data, styles, lr_cw))
    story.append(Spacer(1, 10))

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 7 — Items Requiring Human Review
    # ─────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(header_band("SECTION 7 — ITEMS REQUIRING HUMAN REVIEW", styles))
    story.append(Spacer(1, 6))

    flag_categories = {
        "site_specific":        ("SITE SPECIFIC", RED_FLAG),
        "oid_confirmation":     ("OID CONFIRMATION REQUIRED", AMBER_FLAG),
        "protocol_ambiguous":   ("PROTOCOL AMBIGUOUS", AMBER_FLAG),
        "constraint_review":    ("CONSTRAINT REVIEW", AMBER_FLAG),
        "choice_list_review":   ("CHOICE LIST REVIEW", MID_BLUE),
        "custom_domain":        ("CUSTOM DOMAIN", MID_BLUE),
        "pdf_mapping_uncertain":("PDF MAPPING UNCERTAIN", AMBER_FLAG),
        "name_deviation":       ("NAME DEVIATION", MID_BLUE),
    }

    for key, (label, cat_color) in flag_categories.items():
        items = flags.get(key, [])
        if not items:
            continue

        cat_tbl = Table([[Paragraph(f"  {label}  ({len(items)} items)", ParagraphStyle(
            "cat", fontName="Helvetica-Bold", fontSize=8.5, textColor=WHITE
        ))]], colWidths=[CONTENT_W])
        cat_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), cat_color),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ]))
        story.append(cat_tbl)

        flag_rows = []
        for j, item in enumerate(items, 1):
            # item may be a plain string, a dict like
            # {"flag_reason": "..."}, or a richer dict. Render the human-
            # readable text, not the Python dict repr.
            if isinstance(item, dict):
                text = (item.get("flag_reason")
                        or item.get("reason")
                        or item.get("description")
                        or item.get("item")
                        or item.get("comment")
                        or "; ".join(f"{k}: {v}" for k, v in item.items()))
            else:
                text = str(item)
            flag_rows.append([
                Paragraph(str(j), styles["cell_bold"]),
                Paragraph(text, styles["body_small"])
            ])
        f_tbl = Table(flag_rows, colWidths=[CONTENT_W*0.04, CONTENT_W*0.96])
        f_tbl.setStyle(TableStyle([
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
            ("ROWBACKGROUNDS",(0,0),(-1,-1), [WHITE, GREY_LIGHT]),
            ("LINEBELOW",     (0,0),(-1,-1), 0.3, GREY_MID),
        ]))
        story.append(f_tbl)
        story.append(Spacer(1, 6))

    # ── Review footer banner ──────────────────────────────────────────────────
    story.append(Spacer(1, 6))
    review_text = (
        "<b>EDC STRUCTURE REVIEW REQUIRED — DO NOT BUILD UNTIL COMPLETE</b>  |  "
        "1. Complete all PLACEHOLDER fields  "
        "2. Confirm all OID paths after study configuration  "
        "3. Verify all FLAGGED survey rows  "
        "4. Validate biospecimen form against Lab Manual  "
        "5. Populate lab ranges CSV with site-specific values  "
        "6. Pass reviewed JSON to edc-builder skill"
    )
    review_tbl = Table(
        [[Paragraph(review_text, ParagraphStyle(
            "review", fontName="Helvetica", fontSize=7.5,
            textColor=WHITE, leading=11
        ))]],
        colWidths=[CONTENT_W]
    )
    review_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), MID_BLUE),
        ("TOPPADDING",    (0,0),(-1,-1), 7),
        ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
        ("RIGHTPADDING",  (0,0),(-1,-1), 10),
    ]))
    story.append(review_tbl)

    doc.build(story)
    print(f"EDC Structure PDF written to: {output_path}")


# ── Test run with PrTK05 data ─────────────────────────────────────────────────
if __name__ == "__main__":
    sample_data = {
        "study_meta": {
            "protocol_number": "PrTK05",
            "study_id": "prtk05",
            "generated_date": "2026-04-16",
            "review_status": "PENDING_HUMAN_REVIEW",
            "input_mode": "PROTOCOL_ONLY",
            "library_files_provided": [],
            "library_file_types": []
        },
        "timepoint_csv": {
            "filename": "prtk05_tpt.csv",
            "rows": [
                {"event": "SE_BASELINE",     "timepoint": "Baseline"},
                {"event": "SE_C1",           "timepoint": "Course 1"},
                {"event": "SE_C1POST2H4H",   "timepoint": "Post-Course 1 (2 - 4 Hours)"},
                {"event": "SE_C1POSTD1",     "timepoint": "Post-Course 1 (Day 1)"},
                {"event": "SE_C1POSTD2",     "timepoint": "Post-Course 1 (Day 2)"},
                {"event": "SE_C1POSTW1",     "timepoint": "Post-Course 1 (Week 1)"},
                {"event": "SE_C2",           "timepoint": "Course 2 (Week 2 - 3)"},
                {"event": "SE_C3",           "timepoint": "Course 3 (Week 4 - 6)"},
                {"event": "SE_C3POSTW6W8",   "timepoint": "Post-Course 3 (Week 6 - 8)"},
                {"event": "SE_C3POSTW8W10",  "timepoint": "Post-Course 3 (Week 8 - 10)"},
                {"event": "SE_C3POSTW12W14", "timepoint": "Post-Course 3 (Week 12 - 14)"},
                {"event": "SE_EOS",          "timepoint": "End of Study"},
                {"event": "SE_EOT",          "timepoint": "End of Treatment"},
                {"event": "SE_CTLBASELINE",  "timepoint": "Control Baseline"},
                {"event": "SE_CTLW2W3",      "timepoint": "Control Week 2 - 3"},
                {"event": "SE_CTLW4W6",      "timepoint": "Control Week 4 - 6"},
                {"event": "SE_CTLW8W10",     "timepoint": "Control Week 8 - 10"},
                {"event": "SE_CTLW16W18",    "timepoint": "Control Week 16 - 18"},
                {"event": "SE_UNSCH",        "timepoint": "Unscheduled"},
            ]
        },
        "labranges_csv": {
            "filename": "labranges.csv",
            "columns": ["lab_name","test_code","test_name","lower","upper","unit","sex_filter","age_lower","age_upper"],
            "rows": [
                {"lab_name":"[PLACEHOLDER]","test_code":"WBC","test_name":"White Blood Cells","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"HGB","test_name":"Hemoglobin","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"NEUT","test_name":"Neutrophils","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"LYMPH","test_name":"Lymphocytes","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"PLT","test_name":"Platelets","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"AST","test_name":"Aspartate Aminotransferase","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"ALT","test_name":"Alanine Aminotransferase","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"BILI","test_name":"Total Bilirubin","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"ALKPH","test_name":"Alkaline Phosphatase","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
                {"lab_name":"[PLACEHOLDER]","test_code":"CREAT","test_name":"Creatinine","lower":"[PLACEHOLDER]","upper":"[PLACEHOLDER]","unit":"[PLACEHOLDER]"},
            ]
        },
        "forms": [
            {
                "form_id": "DOV", "form_title": "Date of Visit",
                "form_category": "INFRASTRUCTURE", "cdash_domain": None,
                "visits_assigned": ["ALL_EVENTS"],
                "has_repeating_group": False,
                "library_match": {"status": "PROTOCOL_ONLY","source_type":"NONE","fields_from_library":0,"fields_extended_from_protocol":0,"fields_from_cdash_default":4},
                "settings": {"form_title":"Date of Visit","form_id":"DOV","version":"1","style":"theme-grid","namespaces":"oc=\"http://openclinica.org/xforms\"","crossform_references":""},
                "choices": [{"list_name":"NY","label":"No","name":"N","source":"STANDARD"},{"list_name":"NY","label":"Yes","name":"Y","source":"STANDARD"}],
                "survey": [
                    {"type":"calculate","name":"EVENT_CF","label":"","bind__oc_itemgroup":"","calculation":"instance('clinicaldata')/ODM/...","bind__oc_external":"clinicaldata","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"calculate","name":"TPTCALC","label":"","bind__oc_itemgroup":"DOV","calculation":"pulldata('prtk05_tpt','timepoint','event',${EVENT_CF})","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"begin group","name":"DOV","label":"","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"text","name":"DOVTPT","label":"** Timepoint: **","bind__oc_itemgroup":"DOV","calculation":"${TPTCALC}","readonly":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one NY","name":"VISYN","label":"Was the visit done?","bind__oc_itemgroup":"DOV","relevant":"${TPTCALC} != 'Baseline'","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"date","name":"VISDT","label":"Provide the date of visit.","bind__oc_itemgroup":"DOV","relevant":"${VISYN}='Y' or ${TPTCALC}='Baseline'","required":"yes","constraint":". <= today()","constraint_message":"Cannot be a future date.","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"text","name":"VISNDRSN","label":"Reason visit not done:","bind__oc_itemgroup":"DOV","relevant":"${VISYN}='N'","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"end group","name":"","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                ],
                "cross_form_dependencies": []
            },
            {
                "form_id": "DM", "form_title": "Demographics",
                "form_category": "CDASH_CLINICAL", "cdash_domain": "DM",
                "visits_assigned": ["SE_BASELINE","SE_CTLBASELINE"],
                "has_repeating_group": False,
                "library_match": {"status":"PROTOCOL_ONLY","source_type":"NONE","fields_from_library":0,"fields_extended_from_protocol":0,"fields_from_cdash_default":7},
                "settings": {"form_title":"Demographics","form_id":"DM","version":"1","style":"theme-grid","namespaces":"oc=\"http://openclinica.org/xforms\"","crossform_references":""},
                "choices": [
                    {"list_name":"SEX","label":"Male","name":"M","source":"STANDARD"},
                    {"list_name":"SEX","label":"Female","name":"F","source":"STANDARD"},
                    {"list_name":"ETHNIC","label":"Hispanic or Latino","name":"HISPANIC_OR_LATINO","source":"STANDARD"},
                    {"list_name":"RACE","label":"White","name":"WHITE","source":"STANDARD"},
                    {"list_name":"RACE","label":"Black or African American","name":"BLACK_OR_AFRICAN_AMERICAN","source":"STANDARD"},
                ],
                "survey": [
                    {"type":"calculate","name":"EVENT_CF","label":"","calculation":"instance('clinicaldata')/ODM/...","bind__oc_external":"clinicaldata","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"calculate","name":"TPTCALC","label":"","bind__oc_itemgroup":"DM","calculation":"pulldata('prtk05_tpt',...)","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"begin group","name":"DM1","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"integer","name":"AGE","label":"Age:","bind__oc_itemgroup":"DM","required":"yes","constraint":". >= 18 and . <= 100","constraint_message":"Age must be 18–100.","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one SEX","name":"SEX","label":"Sex:","bind__oc_itemgroup":"DM","required":"yes","calculation":"if(true(),'M','')","readonly":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one ETHNIC","name":"ETHNIC","label":"Ethnicity:","bind__oc_itemgroup":"DM","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_multiple RACE","name":"RACE","label":"Race:","bind__oc_itemgroup":"DM","required":"yes","constraint":"not(selected(${RACE},'NOT_REPORTED') and count-selected > 1)","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"text","name":"RACEOTH","label":"If other, specify:","bind__oc_itemgroup":"DM","relevant":"selected(${RACE},'OTHER')","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"end group","name":"DM1","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                ],
                "cross_form_dependencies": [
                    {"source_form":"DM","source_field":"AGE","purpose":"Age used in MH date validation and AE reporting","xpath_pattern":"instance('clinicaldata')/ODM/.../ItemData[@ItemOID='DM.AGE']/@Value","visit_context":"SE_BASELINE","status":"FLAGGED — OID CONFIRMATION REQUIRED"}
                ]
            },
            {
                "form_id": "AE", "form_title": "Adverse Events",
                "form_category": "CDASH_CLINICAL", "cdash_domain": "AE",
                "visits_assigned": ["SE_C1","SE_C2","SE_C3","SE_C3POSTW6W8","SE_C3POSTW8W10","SE_C3POSTW12W14","SE_EOS","SE_EOT"],
                "has_repeating_group": True,
                "library_match": {"status":"PROTOCOL_ONLY","source_type":"NONE","fields_from_library":0,"fields_extended_from_protocol":0,"fields_from_cdash_default":28},
                "settings": {"form_title":"Adverse Events","form_id":"AE","version":"1","style":"theme-grid","namespaces":"oc=\"http://openclinica.org/xforms\"","crossform_references":""},
                "choices": [
                    {"list_name":"NY","label":"No","name":"N","source":"STANDARD"},
                    {"list_name":"NY","label":"Yes","name":"Y","source":"STANDARD"},
                    {"list_name":"AESEV","label":"Grade 1","name":"1","source":"STANDARD"},
                    {"list_name":"AESEV","label":"Grade 2","name":"2","source":"STANDARD"},
                    {"list_name":"AESEV","label":"Grade 3","name":"3","source":"STANDARD"},
                    {"list_name":"AESEV","label":"Grade 4","name":"4","source":"STANDARD"},
                    {"list_name":"AESEV","label":"Grade 5","name":"5","source":"STANDARD"},
                    {"list_name":"REL","label":"No, Not Related","name":"NO_REL","source":"STANDARD"},
                    {"list_name":"REL","label":"Yes, Possibly Related","name":"YES_POSS","source":"STANDARD"},
                    {"list_name":"OUT","label":"Recovered/Resolved","name":"RECOVERED/RESOLVED","source":"STANDARD"},
                ],
                "survey": [
                    {"type":"calculate","name":"AEID","label":"","calculation":"once(instance('clinicaldata')/.../AEID...)","bind__oc_external":"clinicaldata","completion_status":"FLAGGED","library_source":"CDASH_DEFAULT","flag_reason":"OID confirmation required for once() pattern"},
                    {"type":"calculate","name":"AEID_CALC","label":"","bind__oc_itemgroup":"AE","calculation":"if(${AEID}!='',${AEID},'Scheduled')","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one NY","name":"AEYN","label":"Did participant report any adverse events?","bind__oc_itemgroup":"AE","relevant":"${AEID}=1","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"begin group","name":"AE1","label":"","appearance":"w6","relevant":"${AEYN}='Y' or ${AEYN_CF}='Y'","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"text","name":"AETERM","label":"What is the adverse event term?","bind__oc_itemgroup":"AE","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"date","name":"AESTDAT","label":"Start date:","bind__oc_itemgroup":"AE","required":"yes","constraint":". <= today()","constraint_message":"Cannot be a future date.","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one NY","name":"AEONGO","label":"Ongoing:","bind__oc_itemgroup":"AE","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"date","name":"AEENDAT","label":"End date:","bind__oc_itemgroup":"AE","relevant":"${AEONGO}='N'","required":"yes","constraint":". <= today() and . >= ${AESTDAT}","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one AESEV","name":"AESEV","label":"Severity (NCI-CTCAE v5.0):","bind__oc_itemgroup":"AE","required":"yes","constraint":"...Grade warning","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one NY","name":"AESER","label":"Was the AE serious?","bind__oc_itemgroup":"AE","required":"yes","constraint":"Grade 5 must be Y","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one REL","name":"AEREL1","label":"Relationship to **CAN-2409**:","bind__oc_itemgroup":"AE","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one NY","name":"AEREL2","label":"Relationship to **prodrug**:","bind__oc_itemgroup":"AE","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one OUT","name":"AEOUT","label":"Outcome:","bind__oc_itemgroup":"AE","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"end group","name":"","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"begin group","name":"AE2","label":"Safety Reporting","relevant":"${AESER}='Y'","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"calculate","name":"AGE_CF","label":"","calculation":"instance('clinicaldata')/.../AGE...","bind__oc_external":"clinicaldata","completion_status":"FLAGGED","library_source":"CDASH_DEFAULT","flag_reason":"Cross-form OID confirmation required"},
                    {"type":"calculate","name":"WEIGHT_VSORRES_CF","label":"","calculation":"instance('clinicaldata')/.../WEIGHT...","bind__oc_external":"clinicaldata","completion_status":"FLAGGED","library_source":"CDASH_DEFAULT","flag_reason":"Cross-form OID confirmation required"},
                    {"type":"date","name":"AESERSTDAT","label":"Date event became serious:","bind__oc_itemgroup":"AE","required":"yes","constraint":". <= today() and . >= ${AESTDAT}","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_multiple AESERCRIT","name":"AESERCRIT","label":"Which seriousness criteria does the AE meet?","bind__oc_itemgroup":"AE","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"text","name":"AESERNARRTE","label":"** Event Narrative: **","bind__oc_itemgroup":"AE","appearance":"w6 multiline","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"end group","name":"","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                ],
                "cross_form_dependencies": [
                    {"source_form":"DM","source_field":"AGE","purpose":"Age at event onset","xpath_pattern":"instance('clinicaldata')/.../DM.AGE...","visit_context":"SE_BASELINE","status":"FLAGGED — OID CONFIRMATION REQUIRED"},
                    {"source_form":"VS","source_field":"WEIGHT_VSORRES","purpose":"Weight at AE onset","xpath_pattern":"instance('clinicaldata')/.../VS.WEIGHT_VSORRES...","visit_context":"SE_BASELINE","status":"FLAGGED — OID CONFIRMATION REQUIRED"},
                ]
            },
            {
                "form_id": "LB", "form_title": "Laboratory Assessments",
                "form_category": "CDASH_CLINICAL", "cdash_domain": "LB",
                "visits_assigned": ["SE_C2","SE_C3POSTW6W8","SE_C3POSTW8W10","SE_C3POSTW12W14","SE_EOS","SE_UNSCH"],
                "has_repeating_group": True,
                "library_match": {"status":"PROTOCOL_ONLY","source_type":"NONE","fields_from_library":0,"fields_extended_from_protocol":0,"fields_from_cdash_default":95},
                "settings": {"form_title":"Laboratory","form_id":"LB","version":"1","style":"theme-grid","namespaces":"oc=\"http://openclinica.org/xforms\"","crossform_references":""},
                "choices": [
                    {"list_name":"NY","label":"No","name":"N","source":"STANDARD"},
                    {"list_name":"NY","label":"Yes","name":"Y","source":"STANDARD"},
                    {"list_name":"LBNAM","label":"[PLACEHOLDER — Site Lab 1]","name":"LAB_1","source":"PROTOCOL_SPECIFIC"},
                    {"list_name":"NYU","label":"Yes","name":"Y","source":"STANDARD"},
                    {"list_name":"NYU","label":"No","name":"N","source":"STANDARD"},
                    {"list_name":"NYU","label":"Unknown","name":"U","source":"STANDARD"},
                    {"list_name":"ND","label":"Not Done","name":"ND","source":"STANDARD"},
                ],
                "survey": [
                    {"type":"calculate","name":"SITE_CF","label":"","calculation":"instance('clinicaldata')/ODM/ClinicalData/@StudyOID","bind__oc_external":"clinicaldata","completion_status":"FLAGGED","library_source":"CDASH_DEFAULT","flag_reason":"OID confirm"},
                    {"type":"calculate","name":"EXDAT_CF","label":"","calculation":"instance('clinicaldata')/.../EX.EXDAT...","bind__oc_external":"clinicaldata","completion_status":"FLAGGED","library_source":"CDASH_DEFAULT","flag_reason":"Cross-form OID confirmation required"},
                    {"type":"text","name":"LBTPT","label":"** Timepoint: **","bind__oc_itemgroup":"LB","calculation":"${TPTCALC}","readonly":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one NY","name":"LBPERF","label":"Was the laboratory assessment done?","bind__oc_itemgroup":"LB","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"text","name":"LBRSN","label":"If no, reason not done:","bind__oc_itemgroup":"LB","relevant":"${LBPERF}='N'","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"select_one LBNAM","name":"LBNAM","label":"Name of local lab:","bind__oc_itemgroup":"LB","relevant":"${LBPERF}='Y'","required":"yes","choice_filter":"contains(site_filter,${SITE_CF})","completion_status":"PLACEHOLDER","library_source":"CDASH_DEFAULT","flag_reason":"LBNAM choice list requires site-specific lab names and site_filter OIDs"},
                    {"type":"date","name":"LBDAT","label":"Collection date:","bind__oc_itemgroup":"LB","relevant":"${LBPERF}='Y'","required":"yes","constraint":". >= ${EXDAT_CF}+7 and . <= ${EXDAT_CF}+14 and . <= today()","constraint_message":"Must be 1-2 weeks post injection.","completion_status":"FLAGGED","library_source":"CDASH_DEFAULT","flag_reason":"Visit window constraint — confirm 1-2 week window applies uniformly"},
                    {"type":"text","name":"LBTIM","label":"Collection time:","bind__oc_itemgroup":"LB","relevant":"${LBPERF}='Y'","required":"yes","constraint":"regex(.,'([01][0-9]|2[0-3]):[0-5][0-9]') and string-length(.)=5","constraint_message":"Time must be HH:MM","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"begin group","name":"LB_WBC","label":"White blood cells","relevant":"${LBPERF}='Y'","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"decimal","name":"WBC_LBORRES","label":"Result:","bind__oc_itemgroup":"LB","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"calculate","name":"WBC_UNIT_CALC","label":"","bind__oc_itemgroup":"LB","calculation":"instance('labranges')/root/item[lab_name=${LBNAM} and test_code='WBC']/unit","bind__oc_external":"labranges","completion_status":"PLACEHOLDER","library_source":"CDASH_DEFAULT","flag_reason":"Requires labranges.csv to be populated with site-specific values"},
                    {"type":"select_one NYU","name":"WBC_CCSIG","label":"Clinically significant?","bind__oc_itemgroup":"LB","relevant":"(${WBC_LBORRES} <= ${WBC_LBORNRLO_CALC}) or (${WBC_LBORRES} >= ${WBC_LBORNRHI_CALC})","required":"yes","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                    {"type":"end group","name":"","completion_status":"COMPLETE","library_source":"CDASH_DEFAULT","flag_reason":""},
                ],
                "cross_form_dependencies": [
                    {"source_form":"EX","source_field":"EXDAT","purpose":"Lab collection date visit window constraint","xpath_pattern":"instance('clinicaldata')/.../EX.EXDAT...","visit_context":"SE_C1/SE_C2/SE_C3","status":"FLAGGED — OID CONFIRMATION REQUIRED"},
                ]
            },
        ],
        "review_flags": {
            "site_specific": [
                "Lab ranges CSV — all lower/upper/unit/lab_name values require site-specific input from each participating lab",
                "LBNAM choice list — lab names and site_filter values must match actual OpenClinica site OIDs",
                "Number of participating sites determines how many LBNAM rows and labranges rows are needed"
            ],
            "oid_confirmation": [
                "MH AGE_CF — XPath to DM.AGE requires study OID configuration",
                "MH BL_CF — XPath to IE.VISDT requires study OID configuration",
                "AE AGE_CF, WEIGHT_VSORRES_CF, HEIGHT_VSORRES_CF — cross-form pulls from DM/VS",
                "LB AGE_CF, WEIGHT_CF, RACE_CF, EXDAT_CF — all cross-form pulls",
                "EC ECSTDAT/ECENDAT — reference to EX.EXDAT from current or prior event",
                "PR_EBRT PRSTDAT — reference to SE_C2/EX/EXDAT",
                "VS VSDAT visit window — reference to EX.EXDAT",
                "LB LBDAT visit window — reference to EX.EXDAT",
                "BE BEYN ARM_CF — reference to IE.ARM",
                "CM AGE_XF — cross-form age reference",
                "All DSDECOD choice_filter timepoint values — must match final SE_ event OIDs",
                "MH MEDHID, MEDHYN_CF — once() pattern requires form OID",
                "AE AEID, AEYN_CF — once() pattern requires form OID"
            ],
            "protocol_ambiguous": [
                "BE qPCR result fields — Lab Manual not provided; field names, types, units unknown",
                "Biomarker analysis full field list — protocol states 'not limited to' — scope unclear without Lab Manual",
                "BES semen form — confirm event type (standard visit vs. ad-hoc)",
                "SE_UNSCH form assignment — confirm which forms available at unscheduled visits"
            ],
            "constraint_review": [
                "VS VSDAT window — confirm 1-2 week post-injection window applies to all VS visits",
                "LB LBDAT window — confirm same window applies uniformly across lab visits",
                "PR_EBRT PRSTDAT — confirm within-3-days-of-C2 constraint is correct",
                "EC date constraints — confirm start=inj+1 and end=inj+14 for all 3 courses",
                "EXDOSE constraint — confirm dose is always exactly 2.0 mL"
            ],
            "choice_list_review": [
                "IE003CD — confirm intermediate risk factor options match NCCN 2025 criteria exactly",
                "DSDECOD timepoint column — confirm SE_ OID strings match final study event configuration"
            ],
            "custom_domain": [
                "BE/BE_CTL — highly study-specific biospecimen table; validate all timepoint groups and specimen types against Lab Manual"
            ],
            "pdf_mapping_uncertain": [],
            "name_deviation": []
        }
    }

    build_edc_pdf(sample_data, "/mnt/user-data/outputs/PrTK05_EDC_Structure.pdf")

# ── Alias so the function can also be imported by its skill-level name ────
build_study_spec_pdf = build_edc_pdf

