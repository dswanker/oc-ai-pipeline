"""
XLSForm parser.

XLSForm is the format already used by the oc-ai-pipeline edc-builder
skill. Each form is one .xlsx file with `survey`, `choices`, and
`settings` sheets. A "study" in XLSForm-land is a collection of these
.xlsx files plus an INDEX sheet that lists them.

For the trainer's purposes, a single XLSX upload may contain ONE form
(`{form_id}_survey/choices/settings` sheets) or a full multi-form pack
(INDEX + per-form sheets). We support both.

See: skills/edc-builder/references/xlsform-build-rules.md for the exact
sheet structure used by OpenClinica's pipeline.
"""
from __future__ import annotations

from core.form_parser.base import FormFormat, FormParser, ParsedForm


class XLSFormParser(FormParser):
    format = FormFormat.XLSFORM

    async def parse(self, data: bytes, *, filename: str | None = None) -> ParsedForm:
        """
        Parse an XLSForm .xlsx workbook.

        Sheet conventions (single-form workbook):
          - "survey"   — one row per item (FormItem)
          - "choices"  — one row per choice in select_one / select_multiple
          - "settings" — form-level metadata (form_id, form_title, etc.)

        Sheet conventions (multi-form pack):
          - "INDEX"            — list of forms in this pack
          - "TIMEPOINTS"       — visit schedule
          - "LAB_RANGES"       — lab reference ranges
          - "REVIEW_FLAGS"     — items needing review
          - "{form_id}_survey" — per-form survey sheet
          - "{form_id}_choices"
          - "{form_id}_settings"
        """
        # TODO: implement using openpyxl.
        # 1. Open workbook with openpyxl (data_only=True so formulas are
        #    evaluated rather than returned as strings).
        # 2. Detect single-form vs multi-form by checking for INDEX sheet.
        # 3. For each form, read survey/choices/settings into FormDef.
        # 4. Map XLSForm column types to ParsedForm.FormItem.data_type.
        raise NotImplementedError("XLSFormParser.parse is not yet implemented")
