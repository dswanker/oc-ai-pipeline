"""
PDF parser.

The least-structured input — we get free text and have to recover
form structure heuristically. Two cases:

  1. A scanned/printed annotated CRF (visual layout of the form).
     We extract field names and labels from the page text, group
     by visual/spatial cues, and produce a best-effort ParsedForm.

  2. A study specification document that lists forms in tabular form.
     Easier — pdfplumber can extract tables directly.

Either way, parser output is lower-fidelity than ODM XML / XLSForm.
The fingerprint extractor downstream is robust to incomplete input,
but if PDF is the only signal available, expect lower CT.gov match
confidence.
"""
from __future__ import annotations

from core.form_parser.base import FormFormat, FormParser, ParsedForm


class PDFParser(FormParser):
    format = FormFormat.PDF

    async def parse(self, data: bytes, *, filename: str | None = None) -> ParsedForm:
        """
        Best-effort PDF form parsing.

        Implementation strategy:
          1. Extract text and tables with pdfplumber.
          2. If tables are detected: try to read rows as form items.
          3. Otherwise: dump all text and let the fingerprint extractor
             (Claude) pull what it can from raw text.

        We don't try to be clever about visual layout — Claude is
        better at that than any heuristic we'd write here.
        """
        # TODO: implement using pdfplumber.
        # 1. Open PDF with pdfplumber.
        # 2. For each page, extract text and tables.
        # 3. Concatenate text into raw_metadata["full_text"].
        # 4. Try to identify form names from headers / table titles.
        # 5. Return ParsedForm with whatever we could recover.
        raise NotImplementedError("PDFParser.parse is not yet implemented")
