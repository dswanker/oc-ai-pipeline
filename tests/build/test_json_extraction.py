"""
Unit tests for JSON extraction from Claude API responses.
Tests extract_json in claude_client.py — fully offline.
"""
import sys
import json
import pytest

sys.path.insert(0, '/Users/danswanker/oc-ai-pipeline')
from claude_client import extract_json


MINIMAL_SPEC = {"study_meta": {"protocol_number": "TEST-001"}, "forms": []}

class TestExtractJson:

    def test_extracts_clean_json(self):
        text = json.dumps(MINIMAL_SPEC)
        result = extract_json(text, expected_keys=["study_meta", "forms"])
        assert result["study_meta"]["protocol_number"] == "TEST-001"

    def test_extracts_json_with_preamble(self):
        text = "Here is the study specification:\n\n" + json.dumps(MINIMAL_SPEC)
        result = extract_json(text, expected_keys=["study_meta", "forms"])
        assert "study_meta" in result

    def test_extracts_json_with_postamble(self):
        text = json.dumps(MINIMAL_SPEC) + "\n\nPlease review the above."
        result = extract_json(text, expected_keys=["study_meta", "forms"])
        assert "forms" in result

    def test_extracts_json_from_code_fence(self):
        text = "```json\n" + json.dumps(MINIMAL_SPEC) + "\n```"
        result = extract_json(text, expected_keys=["study_meta", "forms"])
        assert "study_meta" in result

    def test_raises_on_no_json(self):
        with pytest.raises(Exception):
            extract_json("No JSON here at all.", expected_keys=["study_meta"])

    def test_raises_on_missing_expected_key(self):
        text = json.dumps({"wrong_key": []})
        with pytest.raises(Exception):
            extract_json(text, expected_keys=["study_meta", "forms"])

    def test_large_spec_with_forms(self):
        spec = {
            "study_meta": {"protocol_number": "KAR-0025"},
            "forms": [
                {"form_id": "AE", "form_title": "Adverse Events", "survey": [], "choices": []},
                {"form_id": "DM", "form_title": "Demographics", "survey": [], "choices": []},
            ],
            "events": [{"event_id": "SE_SCREEN", "event_name": "Screening"}],
        }
        text = "Result:\n" + json.dumps(spec, indent=2) + "\nEnd of response."
        result = extract_json(text, expected_keys=["study_meta", "forms"])
        assert len(result["forms"]) == 2
        assert result["forms"][0]["form_id"] == "AE"

    def test_extracts_first_valid_json_object(self):
        # If there are multiple JSON objects, should get the one with expected keys
        spec = json.dumps(MINIMAL_SPEC)
        text = '{"irrelevant": true}\n\n' + spec
        result = extract_json(text, expected_keys=["study_meta", "forms"])
        assert "study_meta" in result
