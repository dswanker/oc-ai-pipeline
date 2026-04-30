"""Build Preview renderer for the OC AI Pipeline.

Generates a PDF that visualizes how each XLSForm in an EDC build will render
in OpenClinica's Enketo data-entry view, plus a Schedule of Events matrix
showing form↔event placement.

Public API — two entry points:

    # Inside pipeline.py — preferred (avoids PDF parsing):
    from build_preview import render_build_preview_from_spec
    pdf_bytes = render_build_preview_from_spec(struct_json, edc_zip_bytes)

    # Standalone / testing — when only PDFs are available:
    from build_preview import render_build_preview
    pdf_bytes = render_build_preview(study_spec_pdf_bytes, edc_zip_bytes)

Pure Python + Node browser bundle in headless Chromium. No Claude API calls.
"""
from .render import render_build_preview, render_build_preview_from_spec

__all__ = ['render_build_preview', 'render_build_preview_from_spec']
