"""acrf.py: SDTM-annotated CRF (aCRF) PDF for a finished Study Spec. No AI.

One section per built form: each question with its response options and an annotation box showing where the
value goes in SDTM. The annotations are exactly the SDTM Mapping Specification rows (sdtm_mapping.build_rows is
the single source of truth), drawn on the form:

  DOMAIN.VARIABLE                      mapped (customer alias or CDASH variable name)
    when --TESTCD = X                  value-level detail (test code, category, QNAM ...)
  DOMAIN.VARIABLE  [AI - review]       mapped by AI and validated, or matched by questionnaire item order: review
  NOT SUBMITTED                        collected, not submitted as its own SDTM variable
  SUPP<DOMAIN>.QVAL candidate          no CDASH equivalent identified on a form with a known domain

Layout follows the Study Specification PDF (landscape A4, same palette). A proposed annotation for review, like
the mapping specification it is drawn from.
"""
import io, re

import cdisc_concepts
import sdtm_mapping

MAX_OPTIONS = 14
_NON_DATA_SHOWN = ("note", "begin group", "begin repeat")
# box fill / border per annotation kind
STYLE = {"mapped": ("D6E4F0", "2E6DA4"), "review": ("FDEBD0", "E67E22"),
         "not_submitted": ("EEEEEE", "999999"), "supp": ("FFF9C4", "B7950B")}
LEGEND = [("mapped", "DOMAIN.VARIABLE", "Mapped deterministically: the customer's alias list or a CDASH variable name."),
          ("review", "DOMAIN.VARIABLE  [AI - review]", "Mapped by AI and validated against CDISC metadata, or matched to a "
                                                     "questionnaire by item order. Review before use."),
          ("not_submitted", "NOT SUBMITTED", "Collected on the CRF but not submitted as its own SDTM variable "
                                             "(for example a prompt, or a value only used to derive another variable)."),
          ("supp", "SUPP--.QVAL candidate", "No CDASH equivalent identified: candidate for a supplemental qualifier "
                                            "or a custom domain.")]
DISCLAIMER = ("Proposed SDTM annotations for review, drawn from the SDTM Mapping Specification of this build "
              "(CDASHIG v{cdashig}, SDTMIG v{sdtmig}, CDISC CT {ct}). Derivations are described, not performed.")


def annotation(m, form_domain=""):
    """Annotation for one SDTM Mapping Specification row: {kind, lines, tag}."""
    dom, var, vlm = m.get("SDTM Domain") or "", m.get("SDTM Variable") or "", m.get("Value-Level Detail") or ""
    if m.get("Confidence") == "None":
        fd = str(form_domain or "").strip().upper()
        if fd:
            return {"kind": "supp", "lines": [f"SUPP{fd}.QVAL candidate"], "tag": ""}
        return {"kind": "not_submitted", "lines": ["NOT SUBMITTED"], "tag": ""}
    review = m.get("Confidence") == "Medium"
    tag = "" if not review else ("AI - review" if m.get("Basis") == "AI (validated)" else "review")
    if not var or var.startswith("("):
        note = f"({m.get('CDASH Concept')}: collected, not its own SDTM variable)" if m.get("CDASH Concept") else ""
        return {"kind": "review" if review else "not_submitted", "lines": ["NOT SUBMITTED"] + ([note] if note else []),
                "tag": tag}
    target = f"{dom}.{var}" if dom and not dom.startswith("(") else (f"--.{var}" if dom else var)
    lines = [target]
    for part in [p.strip() for p in vlm.split(";") if p.strip()]:
        lines.append(part if part.lower().startswith("qualifier") else f"when {part}")
    if dom.startswith("("):
        lines.append(dom)
    return {"kind": "review" if review else "mapped", "lines": lines, "tag": tag}


def _options(form, row):
    t = str(row.get("type") or "").strip()
    kind, _, ln = t.partition(" ")
    kind = kind.lower()
    if kind in ("select_one", "select_multiple") and ln.strip():
        ch = [c for c in form.get("choices") or [] if isinstance(c, dict) and c.get("list_name") == ln.strip()]
        mark = "( )" if kind == "select_one" else "[ ]"
        out = [f"{mark} {c.get('label') if c.get('label') not in (None, '') else c.get('name')}  [{c.get('name')}]"
               for c in ch[:MAX_OPTIONS]]
        if len(ch) > MAX_OPTIONS:
            out.append(f"... (+{len(ch) - MAX_OPTIONS} more)")
        return out or [f"{mark} (choice list {ln.strip()})"]
    return [{"date": "Date", "datetime": "Date and time", "time": "Time", "integer": "Number (integer)",
             "decimal": "Number (decimal)", "text": "Text", "calculate": "Calculated"}.get(kind, kind or "Text")]


def build_model(spec):
    """Forms -> rows to draw. Data fields carry their annotation; group headers and notes are kept for context."""
    by_key = {(m["Form"], m["Field"]): m for m in sdtm_mapping.build_rows(spec)}
    forms = []
    for form in spec.get("forms") or []:
        if not isinstance(form, dict):
            continue
        fdom = str(form.get("cdash_domain") or "").strip().upper()
        rows = []
        for r in form.get("survey") or []:
            if not isinstance(r, dict):
                continue
            t = str(r.get("type") or "").strip().lower()
            if cdisc_concepts._is_data_field(r):
                m = by_key.get((form.get("form_id"), r.get("name")))
                if m is None:
                    continue
                rows.append({"kind": "field", "name": r.get("name"), "label": str(r.get("label") or r.get("name") or ""),
                             "options": _options(form, r), "required": str(r.get("required") or "").lower() in ("yes", "true"),
                             "annotation": annotation(m, fdom)})
            elif t in _NON_DATA_SHOWN and str(r.get("label") or "").strip():
                rows.append({"kind": "section" if t != "note" else "note", "label": str(r.get("label")).strip(),
                             "repeat": t == "begin repeat"})
        if not any(x["kind"] == "field" for x in rows):
            continue
        domains = []
        for x in rows:
            if x["kind"] == "field" and x["annotation"]["kind"] in ("mapped", "review"):
                d = x["annotation"]["lines"][0].split(".")[0]
                if d and d != "NOT SUBMITTED" and not d.startswith("--") and d not in domains:
                    domains.append(d)
        forms.append({"form_id": form.get("form_id"), "title": form.get("form_title") or form.get("form_id"),
                      "visits": list(form.get("visits_assigned") or []), "domain": fdom, "domains": domains, "rows": rows})
    return forms


def summarize(model):
    k = {"mapped": 0, "review": 0, "not_submitted": 0, "supp": 0}
    for f in model:
        for x in f["rows"]:
            if x["kind"] == "field":
                k[x["annotation"]["kind"]] += 1
    return {"forms": len(model), "fields": sum(k.values()), "annotated": k["mapped"] + k["review"],
            "review": k["review"], "not_submitted": k["not_submitted"], "supp_candidates": k["supp"]}


def build_pdf(spec):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, CondPageBreak
    from xml.sax.saxutils import escape
    model, meta = build_model(spec), sdtm_mapping._meta(spec)
    summ = summarize(model)
    dark, mid, light = colors.HexColor("#1B3A6B"), colors.HexColor("#2E6DA4"), colors.HexColor("#D6E4F0")
    grey = colors.HexColor("#555555")
    page_w = landscape(A4)[0]
    margin = 1.2 * cm
    content_w = page_w - 2 * margin
    w_q, w_r, w_a = 9.6 * cm, 9.2 * cm, content_w - 18.8 * cm
    box_w = w_a - 12
    S = lambda name, **kw: ParagraphStyle(name, **{"fontName": "Helvetica", "fontSize": 8, "leading": 10, **kw})
    title = S("t", fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=dark, spaceAfter=4)
    body = S("b")
    band = S("band", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=colors.white)
    sub = S("sub", fontSize=7.5, leading=9.5, textColor=grey)
    hcell = S("hc", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white)
    qlabel = S("ql", fontSize=8.5, leading=10.5)
    qname = S("qn", fontName="Courier", fontSize=6.5, leading=8, textColor=grey)
    opt = S("opt", fontSize=7.5, leading=9.5)
    sect = S("sect", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=dark)
    note = S("note", fontName="Helvetica-Oblique", fontSize=7.5, leading=9.5, textColor=grey)
    ann = S("ann", fontName="Helvetica-Bold", fontSize=8, leading=10)
    ann_sub = S("anns", fontSize=7, leading=8.8)
    P = lambda t, s=body: Paragraph(escape(str(t if t is not None else "")), s)

    def box(a, width=box_w):
        fill, edge = (colors.HexColor("#" + c) for c in STYLE[a["kind"]])
        head = escape(a["lines"][0]) + (f'  <font size="6.5" color="#A04000">[{escape(a["tag"])}]</font>' if a["tag"] else "")
        cells = [[Paragraph(head, ann)]] + [[P(line, ann_sub)] for line in a["lines"][1:]]
        t = Table(cells, colWidths=[width])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fill), ("BOX", (0, 0), (-1, -1), 0.8, edge),
                               ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
        return t

    def footer(c, doc):
        c.saveState()
        c.setFont("Helvetica", 7)
        c.setFillColor(grey)
        c.drawString(margin, 0.7 * cm, f"{meta['protocol']}  |  Annotated CRF (SDTM)  |  {meta['generated']}")
        c.drawRightString(page_w - margin, 0.7 * cm, f"Page {doc.page}")
        c.restoreState()

    story = [Paragraph("Annotated CRF (SDTM)", title),
             P(f"{meta['protocol']}  {meta['title']}".strip()),
             P(f"CDISC CT {meta['ct']}  |  CDASHIG {meta['cdashig']}  |  SDTMIG {meta['sdtmig']}  |  "
               f"{summ['forms']} forms, {summ['fields']} fields: {summ['annotated']} annotated "
               f"({summ['review']} for review), {summ['not_submitted']} not submitted, "
               f"{summ['supp_candidates']} supplemental-qualifier candidates"), Spacer(1, 6)]
    leg = [[P("Annotation", hcell), P("Meaning", hcell)]]
    for kind, sample, meaning in LEGEND:
        head, _, tag = sample.partition("  [")
        leg.append([box({"kind": kind, "lines": [head], "tag": tag.rstrip("]")}, 6.4 * cm), P(meaning)])
    lt = Table(leg, colWidths=[6.8 * cm, content_w - 6.8 * cm])
    lt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), dark), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [lt, Spacer(1, 5), P(DISCLAIMER.format(**meta)), Spacer(1, 4),
              P("Value-level lines (\"when VSTESTCD = SYSBP\") tell repeated uses of one SDTM variable apart. "
                "Codes in square brackets after a response option are the values stored in the EDC.", sub)]
    idx = [[P("Form", hcell), P("Title", hcell), P("SDTM domains annotated", hcell), P("Fields", hcell)]]
    for f in model:
        idx.append([P(f["form_id"]), P(f["title"]), P(", ".join(f["domains"]) or "-"),
                    P(sum(1 for x in f["rows"] if x["kind"] == "field"))])
    it = Table(idx, colWidths=[4 * cm, 11 * cm, content_w - 17.5 * cm, 2.5 * cm], repeatRows=1)
    it.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), dark), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light])]))
    story += [Spacer(1, 8), it]

    for f in model:
        story.append(PageBreak())
        head = Table([[P(f"{f['form_id']}  -  {f['title']}", band)]], colWidths=[content_w])
        head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), dark), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                  ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        visits = ", ".join(f["visits"][:12]) + (f" (+{len(f['visits']) - 12} more)" if len(f["visits"]) > 12 else "")
        story += [head, Spacer(1, 2),
                  P(f"SDTM domains: {', '.join(f['domains']) or 'none annotated'}"
                    + (f"   |   Visits: {visits}" if visits else ""), sub), Spacer(1, 3)]
        data = [[P("Question", hcell), P("Response", hcell), P("SDTM annotation", hcell)]]
        style = [("BACKGROUND", (0, 0), (-1, 0), mid), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
                 ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
                 ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        for x in f["rows"]:
            i = len(data)
            if x["kind"] == "field":
                q = [P(x["label"] + (" *" if x["required"] else ""), qlabel), P(x["name"], qname)]
                data.append([q, [P(o, opt) for o in x["options"]], box(x["annotation"])])
            else:
                text = x["label"] + ("  (repeating)" if x.get("repeat") else "")
                data.append([P(text, sect if x["kind"] == "section" else note), "", ""])
                style += [("SPAN", (0, i), (-1, i))]
                if x["kind"] == "section":
                    style.append(("BACKGROUND", (0, i), (-1, i), light))
        t = Table(data, colWidths=[w_q, w_r, w_a], repeatRows=1)
        t.setStyle(TableStyle(style))
        story.append(t)
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=margin, rightMargin=margin, topMargin=margin,
                      bottomMargin=1.3 * cm, title=f"{meta['protocol']} Annotated CRF").build(
        story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def build_files(spec):
    """{'pdf': bytes, 'summary': dict}."""
    return {"pdf": build_pdf(spec), "summary": summarize(build_model(spec))}
