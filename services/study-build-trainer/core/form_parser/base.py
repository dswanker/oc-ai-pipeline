"""
Form parser abstractions.

A parser takes raw bytes (ODM XML, XLSForm .xlsx, or PDF) and returns
a ParsedForm — a structured representation of the form designed for
downstream use by the fingerprint extractor and the embedder.

The shape of ParsedForm is deliberately format-agnostic: every parser
produces the same output regardless of whether the input is XML or
Excel or PDF. Adapter logic for each format lives in the format-specific
module (odm_xml.py, xlsform.py, pdf.py).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class FormFormat(StrEnum):
    ODM_XML = "odm_xml"
    XLSFORM = "xlsform"
    PDF = "pdf"


@dataclass
class FormItem:
    """A single field on a form (one CRF question)."""

    oid: str | None = None
    name: str = ""
    label: str = ""
    data_type: str = ""  # "text", "select_one", "date", etc.
    domain: str | None = None  # CDASH domain if known (AE, VS, DM, ...)


@dataclass
class FormGroup:
    """A logical grouping within a form (an item group / section)."""

    oid: str | None = None
    name: str = ""
    items: list[FormItem] = field(default_factory=list)


@dataclass
class ParsedForm:
    """
    Format-agnostic representation of a form design.

    All parsers produce this. Downstream code should never branch on
    format — only on what's present in the parsed structure.
    """

    source_format: FormFormat
    study_oid: str | None = None
    study_name: str | None = None
    sponsor: str | None = None
    forms: list["FormDef"] = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)
    """Format-specific metadata that didn't fit elsewhere — for debugging."""


@dataclass
class FormDef:
    """A single CRF (one form within a study)."""

    oid: str | None = None
    name: str = ""
    title: str = ""
    groups: list[FormGroup] = field(default_factory=list)


class FormParser(ABC):
    """Base class for all parsers."""

    format: FormFormat

    @abstractmethod
    async def parse(self, data: bytes, *, filename: str | None = None) -> ParsedForm:
        """Parse raw bytes into a ParsedForm."""
        ...
