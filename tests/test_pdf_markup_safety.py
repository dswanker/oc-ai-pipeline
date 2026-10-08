"""Study content with HTML or bare '<' never aborts a PDF (PrTK05 2026-10-08: a customer-standard label
'<span style="color:white"> </span>' crashed the Study Spec PDF, and the XLSX was lost with it)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "protocol-analysis", "scripts"))

from generate_study_spec_pdf import Paragraph, make_styles, build_edc_pdf  # noqa: E402

NASTY = ['<span style="color:white"> </span>', "AST <3 x ULN & ALT", '<b>unclosed', "Temp <span style=\"color:red\">(°C)</span>"]


def test_unparseable_markup_falls_back_to_plain_text():
    styles = make_styles()
    for text in NASTY:
        p = Paragraph(text, styles["cell"])
        assert p is not None
    assert "Temp" in Paragraph(NASTY[3], styles["cell"]).text and "span" not in Paragraph(NASTY[3], styles["cell"]).text
    assert "<b>" not in Paragraph('<b>unclosed', styles["cell"]).text


def test_valid_markup_still_renders():
    p = Paragraph("<b>Bold</b> heading", make_styles()["cell"])
    assert p.frags and any(getattr(f, "bold", 0) for f in p.frags)


def test_study_spec_pdf_builds_with_customer_standard_html_labels(tmp_path):
    survey = [{"type": "text", "name": f"F{i}", "label": lbl, "required": "no"} for i, lbl in enumerate(NASTY)]
    spec = {"study_meta": {"protocol_number": "T1", "study_title": "T <1>"},
            "forms": [{"form_id": "VS", "form_title": "Vital <i>Signs", "cdash_domain": "VS",
                       "survey": survey, "choices": [], "visits_assigned": []}],
            "events": []}
    out = tmp_path / "spec.pdf"
    build_edc_pdf(spec, str(out))
    assert out.stat().st_size > 1000
