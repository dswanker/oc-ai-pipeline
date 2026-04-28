"""
Parser factory — pick the right parser for an input file.

Detection is by extension primarily, with content sniffing as fallback.
"""
from __future__ import annotations

from pathlib import Path

from core.form_parser.base import FormParser, ParsedForm  # noqa: F401 (re-exported)
from core.form_parser.odm_xml import ODMXMLParser
from core.form_parser.pdf import PDFParser
from core.form_parser.xlsform import XLSFormParser


def parser_for(filename: str, data: bytes | None = None) -> FormParser:
    """
    Return a parser appropriate for the given filename.

    Args:
        filename: filename or path. Used for extension-based detection.
        data: optional file bytes. Used for content sniffing if the
              extension is ambiguous (e.g. .xml could be ODM or
              something else).
    """
    suffix = Path(filename).suffix.lower()

    if suffix in (".xml", ".odm"):
        return ODMXMLParser()
    if suffix in (".xlsx", ".xls", ".xlsm"):
        return XLSFormParser()
    if suffix == ".pdf":
        return PDFParser()

    # TODO: add content-sniffing fallback for files without recognizable
    # extensions (e.g. check for "<?xml" header, ZIP magic bytes for XLSX,
    # "%PDF" header for PDF).
    raise ValueError(f"No parser available for {filename!r} (extension: {suffix!r})")


__all__ = ["parser_for", "FormParser", "ParsedForm"]
