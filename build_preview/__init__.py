"""Build Preview renderer for the OC AI Pipeline.

Generates two outputs from an EDC build:
  1. A flat PDF visualising every form in OpenClinica's Enketo layout
  2. A ZIP of standalone interactive HTML files — open index.html in any
     browser to test constraints, relevance logic, and field calculations

Public API — two entry points, each returning (pdf_bytes, html_zip_bytes):

    # Inside pipeline.py — preferred (avoids PDF parsing):
    from build_preview import render_build_preview_from_spec
    pdf_bytes, html_zip_bytes = render_build_preview_from_spec(
        struct_json, edc_zip_bytes)

    # Standalone / testing — when only PDFs are available:
    from build_preview import render_build_preview
    pdf_bytes, html_zip_bytes = render_build_preview(
        study_spec_pdf_bytes, edc_zip_bytes)

Phase 1 interactive simulator supports:
  ✓ Field show/hide based on data-relevant XPath conditions
  ✓ Constraint error messages on blur/change
  ✓ Required field indicators
  ✗ Cross-form references (instance('clinicaldata')) — Phase 2
  ✗ Complex XPath functions (date arithmetic) — Phase 2
"""
from .render import render_build_preview, render_build_preview_from_spec

__all__ = ['render_build_preview', 'render_build_preview_from_spec']
