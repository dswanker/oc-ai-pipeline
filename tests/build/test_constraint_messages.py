"""
Unit tests for constraint message standardization.
Tests _normalize_constraint_messages in build_xlsforms.py.
No API calls, fully offline.
"""
import sys
import pytest

sys.path.insert(0, '/Users/danswanker/oc-ai-pipeline/skills/edc-builder/scripts')
from build_xlsforms import _normalize_constraint_messages


class TestNormalizeConstraintMessages:

    # ── Future date constraint ──────────────────────────────────────────────
    def test_future_date_variation_1(self):
        rows = [{"name": "AESTDAT", "type": "date",
                 "constraint": ". <= today()", "constraint_message": "Future dates are not allowed."}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["constraint_message"] == "Date cannot be in the future."

    def test_future_date_variation_2(self):
        rows = [{"name": "SVDAT", "type": "date",
                 "constraint": ". <= today()", "constraint_message": "Date must not be in the future."}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["constraint_message"] == "Date cannot be in the future."

    def test_future_date_already_canonical(self):
        rows = [{"name": "ICFDAT", "type": "date",
                 "constraint": ". <= today()", "constraint_message": "Date cannot be in the future."}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["constraint_message"] == "Date cannot be in the future."

    def test_future_date_missing_message_filled(self):
        rows = [{"name": "MHSTDAT", "type": "date",
                 "constraint": ". <= today()", "constraint_message": ""}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["constraint_message"] == "Date cannot be in the future."

    def test_past_date_constraint(self):
        rows = [{"name": "BIRTHDT", "type": "date",
                 "constraint": ". < today()", "constraint_message": "Must be in the past."}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["constraint_message"] == "Date must be in the past."

    # ── ICF date constraint ─────────────────────────────────────────────────
    def test_icfdat_constraint_normalized(self):
        rows = [{"name": "CMSTDAT", "type": "date",
                 "constraint": ". >= ${icfdat}", "constraint_message": "Must be after consent."}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["constraint_message"] == "Date must be on or after the date of informed consent."

    # ── Required message ────────────────────────────────────────────────────
    def test_required_message_mandatory(self):
        rows = [{"name": "AETERM", "type": "text", "constraint": "",
                 "required_message": "This field is mandatory."}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["required_message"] == "This field is required."

    def test_required_message_already_canonical(self):
        rows = [{"name": "AETERM", "type": "text", "constraint": "",
                 "required_message": "This field is required."}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["required_message"] == "This field is required."

    # ── No constraint — should not be touched ──────────────────────────────
    def test_no_constraint_untouched(self):
        rows = [{"name": "SUBJID", "type": "text", "constraint": "",
                 "constraint_message": ""}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["constraint_message"] == ""

    def test_non_date_constraint_untouched(self):
        rows = [{"name": "AEGRCD", "type": "select_one grade",
                 "constraint": ". >= 1 and . <= 5",
                 "constraint_message": "Grade must be between 1 and 5."}]
        result = _normalize_constraint_messages(rows)
        # Should not overwrite a message for a non-matching constraint pattern
        assert result[0]["constraint_message"] == "Grade must be between 1 and 5."

    # ── Multiple rows — count of normalizations ─────────────────────────────
    def test_multiple_rows_all_normalized(self):
        rows = [
            {"name": "D1", "type": "date", "constraint": ". <= today()", "constraint_message": "No future."},
            {"name": "D2", "type": "date", "constraint": ". <= today()", "constraint_message": "Future not allowed."},
            {"name": "D3", "type": "date", "constraint": ". <= today()", "constraint_message": "Date cannot be in the future."},
            {"name": "TX", "type": "text", "constraint": "",             "constraint_message": ""},
        ]
        result = _normalize_constraint_messages(rows)
        canonical = "Date cannot be in the future."
        assert result[0]["constraint_message"] == canonical
        assert result[1]["constraint_message"] == canonical
        assert result[2]["constraint_message"] == canonical
        assert result[3]["constraint_message"] == ""

    # ── Row preservation ────────────────────────────────────────────────────
    def test_all_other_fields_preserved(self):
        rows = [{"name": "X", "type": "date", "label": "Test Date",
                 "constraint": ". <= today()", "constraint_message": "bad",
                 "bind__oc_itemgroup": "IG", "required": "yes"}]
        result = _normalize_constraint_messages(rows)
        assert result[0]["name"] == "X"
        assert result[0]["type"] == "date"
        assert result[0]["label"] == "Test Date"
        assert result[0]["required"] == "yes"

    def test_empty_rows_list(self):
        result = _normalize_constraint_messages([])
        assert result == []
