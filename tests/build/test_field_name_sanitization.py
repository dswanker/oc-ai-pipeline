"""
Unit tests for field name sanitization rules applied across the pipeline.
These rules are: strip [^A-Za-z0-9_-], prefix F_ if starts with digit,
skip if empty after sanitization.
Tests the regex logic directly — no imports from pipeline needed.
"""
import re
import pytest


def sanitize(name: str) -> str | None:
    """Mirror of sanitization logic in _parse_crf_standards_questions."""
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', name).strip('_')
    if not safe:
        return None
    if safe[0].isdigit():
        safe = 'F_' + safe
    return safe


def is_valid_xlsform_name(name: str) -> bool:
    return bool(re.match(r'^[A-Za-z_][A-Za-z0-9_-]*$', name))


class TestFieldNameSanitization:

    # ── Known bad names from iMedNet QUESTIONS.csv ─────────────────────────
    def test_hide_plus_clear(self):
        result = sanitize("HIDE + CLEAR")
        assert result is not None
        assert is_valid_xlsform_name(result)
        assert 'HIDE' in result.upper() or result == 'HIDE___CLEAR'

    def test_field_with_parens(self):
        result = sanitize("FIELD (1)")
        assert result is not None
        assert is_valid_xlsform_name(result)

    def test_field_with_spaces(self):
        result = sanitize("MY FIELD NAME")
        assert result is not None
        assert is_valid_xlsform_name(result)

    def test_field_with_plus(self):
        result = sanitize("A+B")
        assert result is not None
        assert is_valid_xlsform_name(result)

    def test_field_with_dot(self):
        result = sanitize("FIELD.NAME")
        # dots are not in [A-Za-z0-9_-] so should be replaced
        result = sanitize("FIELD.NAME")
        assert result is not None
        assert is_valid_xlsform_name(result)

    def test_digit_start(self):
        result = sanitize("123FIELD")
        assert result is not None
        assert result.startswith('F_')
        assert is_valid_xlsform_name(result)

    def test_digit_only(self):
        result = sanitize("123")
        assert result is not None
        assert result.startswith('F_')
        assert is_valid_xlsform_name(result)

    # ── Valid names should pass through unchanged ──────────────────────────
    def test_valid_name_unchanged(self):
        assert sanitize("AETERM") == "AETERM"

    def test_valid_name_with_underscore(self):
        assert sanitize("AE_TERM") == "AE_TERM"

    def test_valid_name_with_hyphen(self):
        assert sanitize("AE-TERM") == "AE-TERM"

    # ── Edge cases ─────────────────────────────────────────────────────────
    def test_empty_string_returns_none(self):
        assert sanitize("") is None

    def test_all_special_chars_returns_none(self):
        assert sanitize("+ + +") is None or sanitize("+ + +") == ""

    def test_leading_trailing_underscores_stripped(self):
        result = sanitize("_FIELD_")
        # After strip('_'), leading/trailing _ removed
        assert result is not None
        assert not result.startswith('_')

    def test_all_valid_chars_preserved(self):
        for name in ["AETERM", "AESEV", "SUBJID", "DOV", "ICF", "RACE1", "VS_SYS"]:
            assert sanitize(name) == name, f"Valid name {name!r} was modified"

    # ── All sanitized names must pass XLSForm validation ──────────────────
    @pytest.mark.parametrize("bad_name", [
        "HIDE + CLEAR", "FIELD (1)", "MY FIELD", "A+B", "123START",
        "FIELD.NAME", "A@B", "X#Y", "A/B", "C\\D",
    ])
    def test_all_bad_names_produce_valid_xlsform_name(self, bad_name):
        result = sanitize(bad_name)
        if result:  # skip None (all-invalid input)
            assert is_valid_xlsform_name(result), \
                f"Sanitized {bad_name!r} → {result!r} is not a valid XLSForm name"
