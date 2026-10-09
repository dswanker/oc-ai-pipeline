"""Word protocol text fallback keeps tables (the Schedule of Activities) in document order; it used to read
paragraphs only, so every table was lost when the PDF conversion was unavailable."""
import ast
import io
import os

import pytest

docx = pytest.importorskip("docx")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _extractor():
    src = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_extract_docx_as_text")
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "pipeline.py", "exec"), ns)
    return ns["_extract_docx_as_text"]


def _doc():
    d = docx.Document()
    d.add_paragraph("8. SCHEDULE OF ACTIVITIES")
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate([["Assessment", "Screening", "Week 4"], ["Vital signs", "X", "X"], ["Widget scan", "X", ""]]):
        for c, v in enumerate(row):
            t.cell(r, c).text = v
    t.cell(0, 1).merge(t.cell(0, 2))          # a merged header cell must appear once
    d.add_paragraph("9. STUDY PROCEDURES")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_tables_are_kept_in_document_order():
    text = _extractor()(_doc())
    lines = [l for l in text.splitlines() if l.strip()]
    assert lines[0] == "8. SCHEDULE OF ACTIVITIES" and lines[-1] == "9. STUDY PROCEDURES"
    assert "Vital signs | X | X" in lines and "Widget scan | X" in " ".join(lines)
    header = next(l for l in lines if l.startswith("Assessment"))
    assert header.count("Screening") == 1


def test_unreadable_bytes_give_empty_text():
    assert _extractor()(b"not a word file") == ""
