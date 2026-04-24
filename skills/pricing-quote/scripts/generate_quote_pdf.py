"""
generate_quote_pdf.py — OpenClinica Quote PDF Generator
Both internal and client-facing versions with full OpenClinica branding.
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
import datetime, os

# ── Brand colours (from logo: navy #2D3561, orange gradient, white bg) ────────
OC_NAVY       = colors.HexColor("#2D3561")   # wordmark navy
OC_ORANGE     = colors.HexColor("#F47920")   # logo orange
OC_MAGENTA    = colors.HexColor("#D91E8C")   # logo magenta accent
OC_LIGHT_NAVY = colors.HexColor("#E8EAF0")   # light navy tint for backgrounds
OC_MID_NAVY   = colors.HexColor("#3D4A7A")   # mid navy for section headers
WHITE         = colors.white
GREY_LIGHT    = colors.HexColor("#F5F5F5")
GREY_MID      = colors.HexColor("#CCCCCC")
GREY_DARK     = colors.HexColor("#666666")
TEXT_DARK     = colors.HexColor("#1A1A1A")
AMBER_WARN    = colors.HexColor("#FFF3CD")
RED_WARN      = colors.HexColor("#CC0000")

LOGO_PATH = os.path.join(os.path.dirname(__file__), '..', 'assets', 'oc_logo.jpg')

PAGE_W, PAGE_H = A4
MARGIN    = 2.0 * cm
CONTENT_W = PAGE_W - 2 * MARGIN


# ── Styles ────────────────────────────────────────────────────────────────────
def _st():
    return {
        "section":  ParagraphStyle("section",  fontName="Helvetica-Bold",    fontSize=10, textColor=WHITE, leftIndent=8),
        "subsect":  ParagraphStyle("subsect",  fontName="Helvetica-Bold",    fontSize=9,  textColor=OC_NAVY, spaceBefore=4, spaceAfter=2),
        "body":     ParagraphStyle("body",     fontName="Helvetica",         fontSize=9,  textColor=TEXT_DARK, leading=13, spaceAfter=3),
        "small":    ParagraphStyle("small",    fontName="Helvetica",         fontSize=8,  textColor=GREY_DARK, leading=11, spaceAfter=2),
        "label":    ParagraphStyle("label",    fontName="Helvetica-Bold",    fontSize=9,  textColor=OC_NAVY),
        "cell":     ParagraphStyle("cell",     fontName="Helvetica",         fontSize=8.5, textColor=TEXT_DARK, leading=11),
        "cell_b":   ParagraphStyle("cell_b",   fontName="Helvetica-Bold",    fontSize=8.5, textColor=OC_NAVY, leading=11),
        "cell_hdr": ParagraphStyle("cell_hdr", fontName="Helvetica-Bold",    fontSize=8.5, textColor=WHITE, leading=11, alignment=TA_CENTER),
        "cell_r":   ParagraphStyle("cell_r",   fontName="Helvetica",         fontSize=8.5, textColor=TEXT_DARK, leading=11, alignment=TA_RIGHT),
        "cell_br":  ParagraphStyle("cell_br",  fontName="Helvetica-Bold",    fontSize=8.5, textColor=OC_NAVY, leading=11, alignment=TA_RIGHT),
        "total_l":  ParagraphStyle("total_l",  fontName="Helvetica-Bold",    fontSize=10, textColor=WHITE),
        "total_r":  ParagraphStyle("total_r",  fontName="Helvetica-Bold",    fontSize=10, textColor=OC_ORANGE, alignment=TA_RIGHT),
        "fn":       ParagraphStyle("fn",       fontName="Helvetica-Oblique", fontSize=7.5, textColor=GREY_DARK, leading=11, spaceBefore=3),
        "warn":     ParagraphStyle("warn",     fontName="Helvetica-Bold",    fontSize=8, textColor=RED_WARN, alignment=TA_CENTER),
        "footer":   ParagraphStyle("footer",   fontName="Helvetica",         fontSize=7, textColor=GREY_DARK, alignment=TA_CENTER),
        "conf":     ParagraphStyle("conf",     fontName="Helvetica",         fontSize=8, textColor=GREY_DARK, alignment=TA_RIGHT),
    }

def _fmt(v, sym='$'): return f"{sym}{v:,.2f}"
def _fh(h):           return f"{h} hr{'s' if h != 1 else ''}"

def _hdr(text, st, bg=None):
    t = Table([[Paragraph(text, st["section"])]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), bg or OC_NAVY),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
    ]))
    return t

def _grid(hdrs, rows, st, cw, extra_ts=None):
    data = [[Paragraph(h, st["cell_hdr"]) for h in hdrs]] + rows
    ts = [
        ("BACKGROUND",    (0,0),(-1,0),  OC_NAVY),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ("LEFTPADDING",   (0,0),(-1,-1), 5),
        ("RIGHTPADDING",  (0,0),(-1,-1), 5),
        ("GRID",          (0,0),(-1,-1), 0.3, GREY_MID),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, GREY_LIGHT]),
    ]
    if extra_ts:
        ts.extend(extra_ts)
    t = Table(data, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(ts))
    return t

def _c(style, text, align=TA_LEFT):
    return Paragraph(text, ParagraphStyle("_c", fontName=style.fontName,
        fontSize=style.fontSize, textColor=style.textColor,
        leading=style.leading if hasattr(style,'leading') else 11, alignment=align))


# ── Cover header with logo ────────────────────────────────────────────────────
def _cover(story, quote, is_internal):
    st   = _st()
    meta = quote.get('study_meta', {})
    today = datetime.date.today().strftime('%B %d, %Y')

    # ── Header band: logo left, document type right ───────────────────────────
    try:
        logo = RLImage(LOGO_PATH, width=6.5*cm, height=2.4*cm)
        logo.hAlign = 'LEFT'
    except Exception:
        logo = Paragraph("OpenClinica", ParagraphStyle("fb",
            fontName="Helvetica-Bold", fontSize=22, textColor=OC_NAVY))

    doc_type_lines = [
        Paragraph("EDC Services", ParagraphStyle("dt1", fontName="Helvetica-Bold",
            fontSize=13, textColor=OC_NAVY, alignment=TA_RIGHT)),
        Paragraph(
            "INTERNAL — CONFIDENTIAL" if is_internal else "Commercial Proposal",
            ParagraphStyle("dt2", fontName="Helvetica",
            fontSize=9, textColor=GREY_DARK, alignment=TA_RIGHT, spaceBefore=2)),
        Paragraph(today, ParagraphStyle("dt3", fontName="Helvetica",
            fontSize=8, textColor=GREY_DARK, alignment=TA_RIGHT, spaceBefore=2)),
    ]
    doc_type_block = Table([[p] for p in doc_type_lines],
                           colWidths=[CONTENT_W * 0.42])
    doc_type_block.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("TOPPADDING",    (0,0),(-1,-1), 2),
        ("BOTTOMPADDING", (0,0),(-1,-1), 2),
        ("LEFTPADDING",   (0,0),(-1,-1), 0),
    ]))

    hdr_tbl = Table([[logo, doc_type_block]],
                    colWidths=[CONTENT_W * 0.58, CONTENT_W * 0.42])
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 10),
        ("BOTTOMPADDING", (0,0),(-1,-1), 10),
        ("LEFTPADDING",   (0,0),(-1,-1), 0),
        ("RIGHTPADDING",  (0,0),(-1,-1), 0),
    ]))
    story.append(hdr_tbl)

    # Branded rule — orange line under logo
    story.append(HRFlowable(width=CONTENT_W, thickness=3, color=OC_ORANGE,
                            spaceAfter=4, spaceBefore=2))

    # Internal warning strip
    if is_internal:
        wm = Table([[Paragraph(
            "⚠  INTERNAL USE ONLY — CONFIDENTIAL PRICING — DO NOT DISTRIBUTE",
            st["warn"])]], colWidths=[CONTENT_W])
        wm.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), AMBER_WARN),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ]))
        story.append(wm)
        story.append(Spacer(1, 8))

    story.append(Spacer(1, 10))

    # ── Study info block ──────────────────────────────────────────────────────
    story.append(_hdr("STUDY INFORMATION", st))
    story.append(Spacer(1, 2))

    rows = [
        ("Protocol",      meta.get('protocol_number', '')),
        ("Study Title",   meta.get('study_title', '—')),
        ("Sponsor",       meta.get('sponsor', '—')),
        ("Phase",         meta.get('study_phase', '—')),
        ("Indication",    meta.get('indication', '—')),
        ("Quote Date",    today),
        ("Valid For",     "30 days from quote date"),
    ]
    if is_internal:
        rows.append(("Internal Ref",
            f"QUOTE-{meta.get('protocol_number','')}-"
            f"{datetime.date.today().strftime('%Y%m%d')}"))

    md = []
    for k, v in rows:
        if v and v != "—" or k in ("Quote Date", "Valid For"):
            md.append([Paragraph(k, st["label"]), Paragraph(str(v), st["body"])])

    mt = Table(md, colWidths=[CONTENT_W * 0.22, CONTENT_W * 0.78])
    mt.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("TOPPADDING",    (0,0),(-1,-1), 3),
        ("BOTTOMPADDING", (0,0),(-1,-1), 3),
        ("LEFTPADDING",   (0,1),(-1,-1), 6),
        ("ROWBACKGROUNDS",(0,0),(-1,-1), [WHITE, GREY_LIGHT]),
        ("LINEBELOW",     (0,0),(-1,-1), 0.3, GREY_MID),
    ]))
    story.append(mt)
    story.append(Spacer(1, 14))


# ── Flag analysis section (internal only) ─────────────────────────────────────
def _flags(story, quote):
    st = _st()
    fa = quote['flag_analysis']
    story.append(_hdr("SCOPE ANALYSIS — FLAGGED ITEMS", st))
    story.append(Spacer(1, 5))
    story.append(Paragraph(
        f"Build fee is calculated from <b>{fa['total_flagged_counted']} flagged items</b> "
        f"identified during protocol extraction. Each flagged item = 1 hour of specialist effort.",
        st["body"]))
    if fa['total_flagged_excluded']:
        story.append(Paragraph(
            f"<b>{fa['total_flagged_excluded']} item(s)</b> in Choice List Review are excluded — "
            f"resolved during client review, not billable build effort.", st["small"]))
    story.append(Spacer(1, 6))

    total = fa['total_flagged_counted']
    rows = []
    for cat, count in fa['category_counts'].items():
        pct = (count / max(total, 1)) * 100
        rows.append([
            Paragraph(cat.replace('_', ' ').title(), st["cell"]),
            Paragraph(str(count), ParagraphStyle("cn", fontName="Helvetica-Bold",
                fontSize=8.5, textColor=OC_NAVY if count else GREY_DARK,
                leading=11, alignment=TA_CENTER)),
            Paragraph(f"{pct:.0f}%" if count else "—",
                ParagraphStyle("pt", fontName="Helvetica", fontSize=8,
                    textColor=GREY_DARK, leading=11, alignment=TA_RIGHT)),
        ])
    excl = ", ".join(c.replace('_', ' ').title() for c in fa['excluded_categories'])
    rows.append([
        Paragraph(f"Excluded: {excl}", ParagraphStyle("ex", fontName="Helvetica-Oblique",
            fontSize=8, textColor=GREY_DARK, leading=11)),
        Paragraph(str(fa['total_flagged_excluded']), ParagraphStyle("exn",
            fontName="Helvetica-Oblique", fontSize=8, textColor=GREY_DARK,
            leading=11, alignment=TA_CENTER)),
        Paragraph("—", ParagraphStyle("exp", fontName="Helvetica-Oblique",
            fontSize=8, textColor=GREY_DARK, leading=11, alignment=TA_RIGHT)),
    ])
    story.append(_grid(["Flag Category", "Items", "% of Total"], rows, st,
        [CONTENT_W*0.60, CONTENT_W*0.20, CONTENT_W*0.20],
        extra_ts=[("BACKGROUND", (0, len(fa['category_counts'])+1),
                   (-1, len(fa['category_counts'])+1), GREY_LIGHT)]))
    story.append(Spacer(1, 10))


# ── Build fee section ─────────────────────────────────────────────────────────
def _build_fee(story, quote, is_internal):
    st  = _st()
    bf  = quote['build_fee']
    sym = quote.get('currency_symbol', '$')
    cw  = [CONTENT_W*0.44, CONTENT_W*0.14, CONTENT_W*0.20, CONTENT_W*0.22]

    story.append(_hdr("ONE-TIME FEES", st))
    story.append(Spacer(1, 5))

    def _row(label, hrs, rate, amt, bold=False):
        fs = st["cell_b"] if bold else st["cell"]
        ar = ParagraphStyle("ar", fontName="Helvetica-Bold" if bold else "Helvetica",
            fontSize=8.5, textColor=OC_NAVY if bold else TEXT_DARK,
            leading=11, alignment=TA_RIGHT)
        ac = ParagraphStyle("ac", fontName="Helvetica", fontSize=8.5,
            textColor=TEXT_DARK, leading=11, alignment=TA_CENTER)
        return [Paragraph(label, fs), Paragraph(hrs, ac),
                Paragraph(rate, ParagraphStyle("ar2", fontName="Helvetica",
                    fontSize=8.5, textColor=TEXT_DARK, leading=11, alignment=TA_RIGHT)),
                Paragraph(amt, ar)]

    if is_internal:
        # Show basis note
        story.append(Paragraph(
            f"Basis: {bf['flagged_items']} flagged items × {bf['minutes_per_item']:.0f} min/item "
            f"= {bf['raw_hours']:.2f} raw hrs → {bf['base_hours']} base hrs + "
            f"{bf['pm_hours']} PM hrs = {bf['pre_cont_hours']} hrs pre-contingency",
            st["small"]))
        story.append(Spacer(1, 4))
        rows = [
            _row("CRF Configuration — Specialist Effort",
                 _fh(bf['base_hours']),
                 _fmt(bf['hourly_rate'], sym) + "/hr",
                 _fmt(bf['base_fee'], sym)),
            _row("Project Management, Review & Support",
                 _fh(bf['pm_hours']),
                 _fmt(bf['hourly_rate'], sym) + "/hr",
                 _fmt(bf['pm_fee'], sym)),
            _row(f"Contingency ({bf['contingency_pct_display']}) on "
                 f"{_fh(bf['pre_cont_hours'])}",
                 _fh(bf['contingency_hours']),
                 _fmt(bf['hourly_rate'], sym) + "/hr",
                 _fmt(bf['contingency_fee'], sym), bold=True),
        ]
    else:
        # Client version — contingency absorbed into specialist effort hrs
        client_specialist_hrs = bf['base_hours'] + bf['contingency_hours']
        client_specialist_fee = client_specialist_hrs * bf['hourly_rate']
        rows = [
            _row("CRF Configuration — Specialist Effort",
                 _fh(client_specialist_hrs),
                 _fmt(bf['hourly_rate'], sym) + "/hr",
                 _fmt(client_specialist_fee, sym)),
            _row("Project Management, Review & Support",
                 _fh(bf['pm_hours']),
                 _fmt(bf['hourly_rate'], sym) + "/hr",
                 _fmt(bf['pm_fee'], sym)),
        ]

    story.append(_grid(["Service", "Hours", "Rate", "Amount"], rows, st, cw))
    story.append(Spacer(1, 4))

    # Additional services discount (if any) — from monday column override
    tots     = quote['totals']
    svc_disc = tots.get('additional_svc_disc', 0.0)
    if svc_disc > 0:
        disc_row = Table([[
            Paragraph(f"Additional Services Discount ({int(svc_disc*100)}%)",
                      ParagraphStyle("dl", fontName="Helvetica", fontSize=9,
                                     textColor=TEXT_DARK)),
            Paragraph(f"−{_fmt(tots['svc_disc_amount'], sym)}",
                      ParagraphStyle("da", fontName="Helvetica-Bold", fontSize=9,
                                     textColor=OC_ORANGE, alignment=TA_RIGHT)),
        ]], colWidths=[CONTENT_W*0.70, CONTENT_W*0.30])
        disc_row.setStyle(TableStyle([
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ("RIGHTPADDING",  (0,0),(-1,-1), 10),
        ]))
        story.append(disc_row)
        story.append(Spacer(1, 4))

    # Total bar
    t = Table([[
        Paragraph("TOTAL ONE-TIME FEE", ParagraphStyle("tl", fontName="Helvetica-Bold",
            fontSize=10, textColor=WHITE)),
        Paragraph(_fmt(bf['total_fee'], sym), ParagraphStyle("tv",
            fontName="Helvetica-Bold", fontSize=10, textColor=OC_ORANGE,
            alignment=TA_RIGHT)),
    ]], colWidths=[CONTENT_W*0.70, CONTENT_W*0.30])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), OC_NAVY),
        ("TOPPADDING",    (0,0),(-1,-1), 9),
        ("BOTTOMPADDING", (0,0),(-1,-1), 9),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
        ("RIGHTPADDING",  (0,0),(-1,-1), 10),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))


# ── Subscriptions section ─────────────────────────────────────────────────────
def _subscriptions(story, quote, is_internal):
    st      = _st()
    modules = quote.get('modules', [])
    sym     = quote.get('currency_symbol', '$')
    dur     = quote['study_duration']
    pc      = quote.get('pricing_context', {})

    story.append(_hdr("SUBSCRIPTION FEES", st))
    story.append(Spacer(1, 5))

    # Pricing context summary
    seg_label = pc.get('segment', 'COMMERCIAL').replace('_', ' ').title()
    vol_disc  = pc.get('volume_discount_display', '0%')
    plat_disc = pc.get('platform_discount_display', '0%')
    bundle    = pc.get('use_bundle', False)
    rates_date = pc.get('rates_effective_date', 'unknown')
    src_note  = f"(rates effective {rates_date})"

    story.append(Paragraph(
        f"Subscription fees for <b>{seg_label}</b> segment — "
        f"{dur['months']}-month study ({dur['contract_years']} year contract). "
        f"Volume/term discount: <b>{vol_disc}</b>. "
        + (f"Platform discount: <b>{plat_disc}</b>. " if pc.get('use_platform_discount') else "")
        + (f"Core Bundle pricing applied. " if bundle else "")
        + f"Rates {src_note}.",
        st["body"]))
    story.append(Spacer(1, 6))

    if is_internal:
        # Internal: show list price, discounts, net monthly, total
        cw = [CONTENT_W*0.30, CONTENT_W*0.13, CONTENT_W*0.12,
              CONTENT_W*0.12, CONTENT_W*0.13, CONTENT_W*0.20]
        hdrs = ["Module", "List/mo", "Vol Disc", "Plat Disc", "Net/mo", "Total"]
        rows = []
        for m in modules:
            lp      = m.get('list_price', 0)
            vd      = m.get('vol_discount', 0)
            pd      = m.get('plat_discount', 0)
            net     = m['monthly_fee']
            total   = m['total_fee']
            rows.append([
                Paragraph(m['name'], st["cell_b"]),
                Paragraph(_fmt(lp, sym), ParagraphStyle("c", fontName="Helvetica",
                    fontSize=8, leading=11, alignment=TA_RIGHT)),
                Paragraph(f"{int(vd*100)}%", ParagraphStyle("c", fontName="Helvetica",
                    fontSize=8, leading=11, alignment=TA_CENTER)),
                Paragraph(f"{int(pd*100)}%", ParagraphStyle("c", fontName="Helvetica",
                    fontSize=8, leading=11, alignment=TA_CENTER)),
                Paragraph(_fmt(net, sym), ParagraphStyle("c", fontName="Helvetica",
                    fontSize=8, leading=11, alignment=TA_RIGHT)),
                Paragraph(_fmt(total, sym), ParagraphStyle("c", fontName="Helvetica-Bold",
                    fontSize=8, leading=11, alignment=TA_RIGHT, textColor=OC_NAVY)),
            ])
    else:
        # Client: show module, monthly fee, months, total — no internal discount detail
        cw = [CONTENT_W*0.38, CONTENT_W*0.14, CONTENT_W*0.14,
              CONTENT_W*0.14, CONTENT_W*0.20]
        hdrs = ["Module", "Term", "Monthly Fee", "Months", "Total"]
        rows = []
        for m in modules:
            rows.append([
                Paragraph(m['name'], st["cell_b"]),
                Paragraph("Monthly", ParagraphStyle("c", fontName="Helvetica",
                    fontSize=8.5, leading=11, alignment=TA_CENTER)),
                Paragraph(_fmt(m['monthly_fee'], sym), ParagraphStyle("c",
                    fontName="Helvetica", fontSize=8.5, leading=11, alignment=TA_RIGHT)),
                Paragraph(str(dur['months']), ParagraphStyle("c", fontName="Helvetica",
                    fontSize=8.5, leading=11, alignment=TA_CENTER)),
                Paragraph(_fmt(m['total_fee'], sym), ParagraphStyle("c",
                    fontName="Helvetica-Bold", fontSize=8.5, leading=11,
                    alignment=TA_RIGHT, textColor=OC_NAVY)),
            ])

    story.append(_grid(hdrs, rows, st, cw))

    # Additional subscription discount (if any) — from monday column override
    tots     = quote['totals']
    sub_disc = tots.get('additional_sub_disc', 0.0)
    if sub_disc > 0:
        disc_row = Table([[
            Paragraph(f"Additional Subscription Discount ({int(sub_disc*100)}%)",
                      ParagraphStyle("dl", fontName="Helvetica", fontSize=9,
                                     textColor=TEXT_DARK)),
            Paragraph(f"−{_fmt(tots['sub_disc_amount'], sym)}",
                      ParagraphStyle("da", fontName="Helvetica-Bold", fontSize=9,
                                     textColor=OC_ORANGE, alignment=TA_RIGHT)),
        ]], colWidths=[CONTENT_W*0.70, CONTENT_W*0.30])
        disc_row.setStyle(TableStyle([
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ("RIGHTPADDING",  (0,0),(-1,-1), 10),
        ]))
        story.append(disc_row)

    story.append(Spacer(1, 14))


# ── Grand total ───────────────────────────────────────────────────────────────
def _grand_total(story, quote):
    st     = _st()
    totals = quote['totals']
    sym    = quote.get('currency_symbol', '$')

    story.append(_hdr("QUOTE SUMMARY", st))
    story.append(Spacer(1, 5))

    rows_data = [
        [Paragraph("One-Time Fees (Build + Project Management)", st["cell"]),
         Paragraph(_fmt(totals['build_fee'], sym),
             ParagraphStyle("c", fontName="Helvetica", fontSize=8.5,
                 leading=11, alignment=TA_RIGHT))],
        [Paragraph("Subscription Fees (all modules × study duration)", st["cell"]),
         Paragraph(_fmt(totals['module_total'], sym) if totals['module_total'] > 0 else "TBD",
             ParagraphStyle("c", fontName="Helvetica", fontSize=8.5,
                 leading=11, alignment=TA_RIGHT,
                 textColor=GREY_DARK if totals['module_total'] == 0 else TEXT_DARK))],
    ]
    st_tbl = Table(rows_data, colWidths=[CONTENT_W*0.70, CONTENT_W*0.30])
    st_tbl.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
        ("RIGHTPADDING",  (0,0),(-1,-1), 10),
        ("LINEBELOW",     (0,0),(-1,-1), 0.3, GREY_MID),
        ("ROWBACKGROUNDS",(0,0),(-1,-1), [WHITE, GREY_LIGHT]),
    ]))
    story.append(st_tbl)
    story.append(Spacer(1, 4))

    grand = totals['grand_total']
    t = Table([[
        Paragraph("ESTIMATED TOTAL", ParagraphStyle("gl", fontName="Helvetica-Bold",
            fontSize=11, textColor=WHITE)),
        Paragraph(_fmt(grand, sym) if grand > 0 else "TBD",
            ParagraphStyle("gv", fontName="Helvetica-Bold", fontSize=11,
                textColor=OC_ORANGE, alignment=TA_RIGHT)),
    ]], colWidths=[CONTENT_W*0.70, CONTENT_W*0.30])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), OC_NAVY),
        ("TOPPADDING",    (0,0),(-1,-1), 10),
        ("BOTTOMPADDING", (0,0),(-1,-1), 10),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
        ("RIGHTPADDING",  (0,0),(-1,-1), 10),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))



# ── Scope appendix — item-level detail ───────────────────────────────────────
def _scope_appendix(story, quote):
    """
    Appendix on new page — one row per flagged item.
    Columns: # | Category | Item | Comment (if EDC structure supplied)
    """
    st  = _st()
    fa  = quote['flag_analysis']
    raw = quote.get('_raw_flags', {})
    has_comments = quote.get('_has_edc_comments', False)

    # Category display names and row colours
    cat_meta = {
        'site_specific':         ('Site Specific',         colors.HexColor("#FADBD8")),
        'oid_confirmation':      ('OID Confirmation',      colors.HexColor("#FDEBD0")),
        'protocol_ambiguous':    ('Protocol Ambiguous',    colors.HexColor("#FDEBD0")),
        'constraint_review':     ('Constraint Review',     colors.HexColor("#FEF9E7")),
        'custom_domain':         ('Custom Domain',         colors.HexColor("#EBF5FB")),
        'pdf_mapping_uncertain': ('PDF Mapping Uncertain', colors.HexColor("#FDEBD0")),
        'name_deviation':        ('Name Deviation',        colors.HexColor("#EBF5FB")),
    }

    # Collect items — handle both plain strings and {item, comment} dicts
    all_items = []   # (cat_label, item_str, comment_str, bg_colour)
    for cat in fa['counted_categories']:
        entries = raw.get(cat, [])
        if not isinstance(entries, list) or not entries:
            continue
        label, bg = cat_meta.get(cat, (cat.replace('_',' ').title(),
                                        colors.HexColor("#F5F5F5")))
        for entry in entries:
            if isinstance(entry, dict):
                item    = entry.get('item', '')
                comment = entry.get('comment', '')
            else:
                # Plain string — try to split on ' — ' to derive comment
                s = str(entry).strip()
                if ' — ' in s:
                    parts   = s.split(' — ', 1)
                    item    = parts[0].strip()
                    comment = parts[1].strip()
                elif ' - ' in s:
                    parts   = s.split(' - ', 1)
                    item    = parts[0].strip()
                    comment = parts[1].strip()
                else:
                    item    = s
                    comment = ''
            all_items.append((label, item, comment, bg))

    if not all_items:
        return

    from reportlab.platypus import PageBreak as PB
    story.append(PB())

    story.append(_hdr("APPENDIX — SCOPE ITEM DETAIL", st))
    story.append(Spacer(1, 5))

    # Detect whether any comments are actually present
    any_comments = any(c for _, _, c, _ in all_items)
    source_note  = " Comments sourced from EDC structure analysis." if any_comments else ""

    story.append(Paragraph(
        f"The following {len(all_items)} items were identified during protocol analysis "
        f"and form the basis of the specialist effort estimate. Each item represents a "
        f"configuration element, constraint, or field requiring specialist review "
        f"and build effort.{source_note}",
        st["body"]))
    story.append(Spacer(1, 8))

    # Column widths — 4-col when comments present, 3-col otherwise
    cell_s = ParagraphStyle("cell_s", fontName="Helvetica",      fontSize=7.5,
                             textColor=TEXT_DARK, leading=10)
    cell_b = ParagraphStyle("cell_b", fontName="Helvetica-Bold", fontSize=7.5,
                             textColor=OC_NAVY,   leading=10)
    cell_m = ParagraphStyle("cell_m", fontName="Helvetica-Oblique", fontSize=7,
                             textColor=GREY_DARK, leading=10)
    num_s  = ParagraphStyle("num_s",  fontName="Helvetica",      fontSize=7.5,
                             textColor=GREY_DARK, leading=10, alignment=TA_CENTER)

    if any_comments:
        cw   = [CONTENT_W*0.04, CONTENT_W*0.18, CONTENT_W*0.28, CONTENT_W*0.50]
        hdrs = ["#", "Category", "Item", "Comment / Action Required"]
    else:
        cw   = [CONTENT_W*0.04, CONTENT_W*0.20, CONTENT_W*0.76]
        hdrs = ["#", "Category", "Item Description"]

    data = [[Paragraph(h, st["cell_hdr"]) for h in hdrs]]

    for i, (label, item, comment, bg) in enumerate(all_items, start=1):
        if any_comments:
            data.append([
                Paragraph(str(i), num_s),
                Paragraph(label,   cell_b),
                Paragraph(item,    cell_s),
                Paragraph(comment, cell_m),
            ])
        else:
            data.append([
                Paragraph(str(i), num_s),
                Paragraph(label,  cell_b),
                Paragraph(item,   cell_s),
            ])

    tbl = Table(data, colWidths=cw, repeatRows=1)
    ts  = [
        ("BACKGROUND",    (0,0),(-1,0),  OC_NAVY),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ("TOPPADDING",    (0,0),(-1,-1), 2),
        ("BOTTOMPADDING", (0,0),(-1,-1), 2),
        ("LEFTPADDING",   (0,0),(-1,-1), 3),
        ("RIGHTPADDING",  (0,0),(-1,-1), 3),
        ("GRID",          (0,0),(-1,-1), 0.3, GREY_MID),
    ]
    for i, (_, _, _, bg) in enumerate(all_items, start=1):
        ts.append(("BACKGROUND", (0,i), (-1,i), bg))

    tbl.setStyle(TableStyle(ts))
    story.append(tbl)
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        f"Items in the Choice List Review category ({fa['total_flagged_excluded']} items) "
        f"are not included above and are not counted in the build fee — "
        f"these are standard code list confirmations resolved during client review.",
        st["fn"]))

# ── Footer ────────────────────────────────────────────────────────────────────
def _footer(story, quote, is_internal):
    st = _st()

    story.append(HRFlowable(width=CONTENT_W, thickness=2, color=OC_ORANGE,
                            spaceBefore=4, spaceAfter=6))

    if is_internal:
        bf = quote['build_fee']
        fa = quote['flag_analysis']
        story.append(Paragraph("INTERNAL NOTES", ParagraphStyle("in",
            fontName="Helvetica-Bold", fontSize=8, textColor=RED_WARN)))
        story.append(Paragraph(
            f"Build: {fa['total_flagged_counted']} items × {bf['minutes_per_item']:.0f} min = "
            f"{bf['raw_hours']:.2f} raw hrs → {bf['base_hours']} base + "
            f"{bf['pm_hours']} PM = {bf['pre_cont_hours']} pre-contingency + "
            f"{bf['contingency_hours']} contingency ({bf['contingency_pct_display']}) = "
            f"{bf['total_hours']} total hrs @ ${bf['hourly_rate']:.2f}/hr",
            st["small"]))
    else:
        terms = [
            "This proposal is valid for 30 days from the date of issue.",
            "Fees are quoted in USD and are exclusive of applicable taxes.",
            "Subscription fees are billed monthly or annually per agreement terms.",
            "One-time fees are payable upon project initiation.",
            "Scope changes after build commencement are subject to change order pricing.",
            "This proposal is confidential and intended solely for the named recipient.",
        ]
        story.append(Paragraph("Terms & Conditions", st["subsect"]))
        for t in terms:
            story.append(Paragraph(f"• {t}", st["small"]))

    story.append(Spacer(1, 8))

    # Footer with logo tagline
    footer_row = Table([[
        Paragraph("openclinica.com", ParagraphStyle("fl", fontName="Helvetica",
            fontSize=7, textColor=OC_NAVY)),
        Paragraph(f"© {datetime.date.today().year} OpenClinica LLC. All rights reserved.",
            ParagraphStyle("fc", fontName="Helvetica", fontSize=7,
                textColor=GREY_DARK, alignment=TA_CENTER)),
        Paragraph(f"Generated: {datetime.date.today().strftime('%B %d, %Y')}",
            ParagraphStyle("fr", fontName="Helvetica", fontSize=7,
                textColor=GREY_DARK, alignment=TA_RIGHT)),
    ]], colWidths=[CONTENT_W*0.30, CONTENT_W*0.40, CONTENT_W*0.30])
    footer_row.setStyle(TableStyle([
        ("VALIGN",  (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 0),
        ("BOTTOMPADDING", (0,0),(-1,-1), 0),
    ]))
    story.append(footer_row)


# ── Page template with branded header/footer on every page ────────────────────
class BrandedDoc(SimpleDocTemplate):
    """Adds a thin orange top rule and logo watermark on every page."""
    def __init__(self, *args, logo_path=None, is_internal=False, **kwargs):
        self.logo_path  = logo_path
        self.is_internal = is_internal
        super().__init__(*args, **kwargs)

    def handle_pageBegin(self):
        super().handle_pageBegin()
        canvas = self.canv
        canvas.saveState()
        canvas.setStrokeColor(OC_ORANGE)
        canvas.setLineWidth(3)
        canvas.line(MARGIN, PAGE_H - 0.8*cm, PAGE_W - MARGIN, PAGE_H - 0.8*cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY_DARK)
        canvas.drawRightString(PAGE_W - MARGIN, 0.6*cm,
                               f"Page {canvas.getPageNumber()}")
        canvas.restoreState()


# ── Main builder ──────────────────────────────────────────────────────────────
def _build_one_pdf(quote, path, is_internal):
    story = []

    doc = BrandedDoc(
        path,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + 0.4*cm,
        bottomMargin=MARGIN + 0.4*cm,
        logo_path=LOGO_PATH,
        is_internal=is_internal,
        title=f"OpenClinica Quote — "
              f"{quote.get('study_meta',{}).get('protocol_number','')}",
        author="OpenClinica",
    )

    _cover(story, quote, is_internal)
    if is_internal:
        _flags(story, quote)
    _build_fee(story, quote, is_internal)
    if quote.get('modules'):
        _subscriptions(story, quote, is_internal)
    _grand_total(story, quote)
    _footer(story, quote, is_internal)
    _scope_appendix(story, quote)

    doc.build(story)
    print(f"{'Internal' if is_internal else 'Client'} PDF: {path}")


def build_quote_pdfs(quote, internal_path, client_path):
    _build_one_pdf(quote, internal_path, is_internal=True)
    _build_one_pdf(quote, client_path,   is_internal=False)


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from pricing_engine import calculate_quote

    sample = {
        'study_meta': {
            'protocol_number': 'PrTK05',
            'study_title':     'CAN-2409 Phase 2a Prostate Cancer Study',
            'sponsor':         'Candel Therapeutics',
            'study_phase':     'Phase 2a',
            'indication':      'Prostate Cancer',
            'total_study_duration_months': 24,
        },
        'review_flags': {
            'site_specific':         ['Lab ranges', 'LBNAM', 'Site count'],
            'oid_confirmation':      [],
            'protocol_ambiguous':    ['BE qPCR', 'Biomarker list', 'BES type', 'SE_UNSCH', 'DC'],
            'constraint_review':     ['VS window', 'LB window', 'EBRT date', 'EC dates', 'EXDOSE'],
            'choice_list_review':    ['IE003CD', 'DSDECOD'],
            'custom_domain':         ['BE Lab Manual', 'EC_DIARY', 'DC sponsor'],
            'pdf_mapping_uncertain': [],
            'name_deviation':        [],
        },
        'is_epro_required': True,
    }
    quote = calculate_quote(sample)
    build_quote_pdfs(
        quote,
        '/mnt/user-data/outputs/PrTK05_Quote_Internal.pdf',
        '/mnt/user-data/outputs/PrTK05_Quote_Client.pdf',
    )
