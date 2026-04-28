"""
Tests for ``core.fingerprint``.

These tests exercise the pure helper functions (serialization, prompt
construction, response parsing, override handling) without invoking
the Anthropic API. The full ``FingerprintExtractor.extract`` round-trip
is exercised separately by the integration check at
``tests/integration_fingerprint_real_api.py`` — that one needs a real
API key and is opt-in.

Run as a script::

    python tests/test_fingerprint_extractor.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Standalone-script support — pytest doesn't need this.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.fingerprint import (
    StudyFingerprint,
    _filter_overrides,
    _strip_json_fences,
    build_extraction_prompt,
    parse_fingerprint_response,
    serialize_parsed_form,
)
from core.form_parser.odm_xml import ODMXMLParser

FIXTURE = Path(__file__).parent / "fixtures" / "prtk05_sample.odm.xml"


def _load_parsed():
    """Parse the PrTK05 fixture once. Returns a ParsedForm."""
    return asyncio.run(ODMXMLParser().parse(FIXTURE.read_bytes()))


# ─── serialize_parsed_form ─────────────────────────────────────────


def test_serialize_includes_top_level_metadata() -> None:
    parsed = _load_parsed()
    out = serialize_parsed_form(parsed)
    assert out["study_name"].startswith("PrTK05")
    assert out["study_oid"] == "S_PRTK05"
    assert out["form_count"] == 9
    assert isinstance(out["forms"], list)
    assert len(out["forms"]) == 9


def test_serialize_includes_extracted_sponsor() -> None:
    parsed = _load_parsed()
    out = serialize_parsed_form(parsed)
    assert out["extracted_sponsor"] == "Candel Therapeutics, Inc."


def test_serialize_each_form_has_required_keys() -> None:
    parsed = _load_parsed()
    out = serialize_parsed_form(parsed)
    for f in out["forms"]:
        assert set(f.keys()) >= {"oid", "name", "domain", "item_count", "sample_items"}


def test_serialize_caps_sample_items_per_form() -> None:
    parsed = _load_parsed()
    out = serialize_parsed_form(parsed, max_items_per_form=2)
    for f in out["forms"]:
        # AE form has 6 items but should be capped at 2 in sample_items
        assert len(f["sample_items"]) <= 2


def test_serialize_includes_openclinica_details_when_present() -> None:
    parsed = _load_parsed()
    out = serialize_parsed_form(parsed)
    assert "openclinica_details" in out
    assert out["openclinica_details"]["Phase"] == "Phase 2"
    assert out["openclinica_details"]["ProtocolType"] == "interventional"


def test_serialize_truncation_note_when_over_max_forms() -> None:
    parsed = _load_parsed()
    out = serialize_parsed_form(parsed, max_forms=3)
    assert len(out["forms"]) == 3
    assert "_truncation_note" in out
    assert "9 forms" in out["_truncation_note"]


def test_serialize_no_truncation_note_when_under_max() -> None:
    parsed = _load_parsed()
    out = serialize_parsed_form(parsed, max_forms=100)
    assert "_truncation_note" not in out


def test_serialize_domain_collapses_when_consistent() -> None:
    """A form whose items all share one domain should serialize as a string."""
    parsed = _load_parsed()
    out = serialize_parsed_form(parsed)
    dm = next(f for f in out["forms"] if f["oid"] == "F_DM")
    assert dm["domain"] == "DM"


# ─── build_extraction_prompt ───────────────────────────────────────


def test_prompt_includes_required_schema_keys() -> None:
    parsed = _load_parsed()
    prompt = build_extraction_prompt(parsed)
    for key in [
        "sponsor", "intervention", "indication", "phase",
        "study_type", "therapeutic_area",
        "extraction_confidence", "notes",
    ]:
        assert f'"{key}"' in prompt, f"Schema key {key!r} missing from prompt"


def test_prompt_includes_parsed_form_payload() -> None:
    parsed = _load_parsed()
    prompt = build_extraction_prompt(parsed)
    assert "<parsed_form>" in prompt
    assert "</parsed_form>" in prompt
    assert "S_PRTK05" in prompt  # study_oid surfaced


def test_prompt_includes_human_overrides_section_when_supplied() -> None:
    parsed = _load_parsed()
    prompt = build_extraction_prompt(
        parsed, overrides={"sponsor": "Acme Therapeutics"}
    )
    assert "<human_supplied>" in prompt
    assert "Acme Therapeutics" in prompt
    assert "ground-truth" in prompt.lower() or "ground truth" in prompt.lower()


def test_prompt_omits_overrides_section_when_none() -> None:
    parsed = _load_parsed()
    prompt = build_extraction_prompt(parsed, overrides=None)
    assert "<human_supplied>" not in prompt


def test_prompt_drops_unknown_override_keys() -> None:
    """Overrides with unknown keys shouldn't bleed through to the prompt."""
    parsed = _load_parsed()
    prompt = build_extraction_prompt(
        parsed,
        overrides={"sponsor": "Acme", "secret_field": "leaked"},
    )
    assert "Acme" in prompt
    assert "leaked" not in prompt
    assert "secret_field" not in prompt


def test_prompt_drops_empty_override_values() -> None:
    parsed = _load_parsed()
    prompt = build_extraction_prompt(
        parsed,
        overrides={"sponsor": "", "indication": None},
    )
    # Empty values shouldn't trigger the overrides section at all
    assert "<human_supplied>" not in prompt


# ─── parse_fingerprint_response ────────────────────────────────────


def test_parse_clean_json_response() -> None:
    raw = json.dumps({
        "sponsor": "Candel Therapeutics, Inc.",
        "intervention": ["aglatimagene besadenovec", "valacyclovir"],
        "indication": "intermediate-risk prostate cancer",
        "phase": "2",
        "study_type": "interventional",
        "therapeutic_area": "oncology",
        "extraction_confidence": 0.95,
        "notes": None,
    })
    fp = parse_fingerprint_response(raw)
    assert fp.sponsor == "Candel Therapeutics, Inc."
    assert fp.intervention == ["aglatimagene besadenovec", "valacyclovir"]
    assert fp.indication == "intermediate-risk prostate cancer"
    assert fp.phase == "2"
    assert fp.study_type == "interventional"
    assert fp.therapeutic_area == "oncology"
    assert fp.extraction_confidence == 0.95
    assert fp.notes is None


def test_parse_handles_markdown_json_fence() -> None:
    raw = '```json\n{"sponsor": "Acme", "phase": "1"}\n```'
    fp = parse_fingerprint_response(raw)
    assert fp.sponsor == "Acme"
    assert fp.phase == "1"


def test_parse_handles_bare_triple_backtick_fence() -> None:
    raw = '```\n{"sponsor": "Acme"}\n```'
    fp = parse_fingerprint_response(raw)
    assert fp.sponsor == "Acme"


def test_parse_returns_diagnostic_for_malformed_json() -> None:
    raw = "this is not JSON at all"
    fp = parse_fingerprint_response(raw)
    assert fp.sponsor is None
    assert fp.notes is not None
    assert "JSON parse failed" in fp.notes


def test_parse_returns_diagnostic_for_non_object_json() -> None:
    raw = '["a", "b"]'  # JSON array, not object
    fp = parse_fingerprint_response(raw)
    assert fp.notes is not None
    assert "not a JSON object" in fp.notes


def test_parse_coerces_int_phase_to_string() -> None:
    raw = '{"phase": 2}'  # phase as integer instead of string
    fp = parse_fingerprint_response(raw)
    assert fp.phase == "2"


def test_parse_coerces_string_intervention_to_list() -> None:
    raw = '{"intervention": "DrugX"}'
    fp = parse_fingerprint_response(raw)
    assert fp.intervention == ["DrugX"]


def test_parse_handles_missing_keys() -> None:
    raw = '{"sponsor": "Acme"}'
    fp = parse_fingerprint_response(raw)
    assert fp.sponsor == "Acme"
    assert fp.intervention is None
    assert fp.indication is None
    assert fp.extraction_confidence == 0.0


def test_parse_normalizes_empty_strings_to_none() -> None:
    raw = '{"sponsor": "", "indication": "  "}'
    fp = parse_fingerprint_response(raw)
    assert fp.sponsor is None
    assert fp.indication is None


def test_parse_drops_empty_intervention_entries() -> None:
    raw = '{"intervention": ["DrugX", "", null, "  "]}'
    fp = parse_fingerprint_response(raw)
    assert fp.intervention == ["DrugX"]


def test_parse_handles_invalid_confidence_gracefully() -> None:
    raw = '{"extraction_confidence": "very high"}'
    fp = parse_fingerprint_response(raw)
    assert fp.extraction_confidence == 0.0


# ─── Override handling ─────────────────────────────────────────────


def test_overrides_force_sponsor_even_if_claude_disagrees() -> None:
    raw = '{"sponsor": "Wrong Inc.", "phase": "2"}'
    fp = parse_fingerprint_response(
        raw, overrides={"sponsor": "Candel Therapeutics, Inc."}
    )
    assert fp.sponsor == "Candel Therapeutics, Inc."
    assert fp.phase == "2"  # untouched


def test_overrides_apply_even_when_claude_returns_null() -> None:
    raw = '{"sponsor": null, "indication": null}'
    fp = parse_fingerprint_response(
        raw, overrides={"sponsor": "Acme", "indication": "cancer"}
    )
    assert fp.sponsor == "Acme"
    assert fp.indication == "cancer"


def test_overrides_apply_even_to_malformed_response() -> None:
    """Override should still be applied when the response is malformed."""
    fp = parse_fingerprint_response(
        "garbage", overrides={"sponsor": "Override Inc."}
    )
    assert fp.sponsor == "Override Inc."
    assert fp.notes is not None
    assert "JSON parse failed" in fp.notes


def test_overrides_normalize_string_intervention_to_list() -> None:
    raw = '{}'
    fp = parse_fingerprint_response(
        raw, overrides={"intervention": "DrugX"}
    )
    assert fp.intervention == ["DrugX"]


def test_filter_overrides_drops_unknown_keys() -> None:
    out = _filter_overrides({"sponsor": "X", "fake": "Y"})
    assert out == {"sponsor": "X"}


def test_filter_overrides_drops_empty_values() -> None:
    out = _filter_overrides(
        {"sponsor": "", "indication": None, "phase": "2", "intervention": []}
    )
    assert out == {"phase": "2"}


def test_filter_overrides_returns_none_when_nothing_useful() -> None:
    assert _filter_overrides(None) is None
    assert _filter_overrides({}) is None
    assert _filter_overrides({"fake": "x"}) is None
    assert _filter_overrides({"sponsor": ""}) is None


# ─── _strip_json_fences ────────────────────────────────────────────


def test_strip_json_fences_handles_json_label() -> None:
    assert _strip_json_fences('```json\n{"x":1}\n```') == '{"x":1}'


def test_strip_json_fences_handles_no_label() -> None:
    assert _strip_json_fences('```\n{"x":1}\n```') == '{"x":1}'


def test_strip_json_fences_passes_through_clean_input() -> None:
    assert _strip_json_fences('{"x":1}') == '{"x":1}'


def test_strip_json_fences_trims_whitespace() -> None:
    assert _strip_json_fences('   {"x":1}   ') == '{"x":1}'


# ─── End-to-end with a stub Claude client ──────────────────────────


class _StubResponseBlock:
    """Mimic anthropic.types.TextBlock (only the .text attr we use)."""
    def __init__(self, text: str) -> None:
        self.text = text


class _StubResponse:
    def __init__(self, text: str) -> None:
        self.content = [_StubResponseBlock(text)]


class _StubMessages:
    """Records what was passed in; returns a canned response."""
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[dict] = []

    async def create(self, **kwargs):  # noqa: ANN201, ANN003
        self.calls.append(kwargs)
        return _StubResponse(self._response_text)


class _StubAnthropicClient:
    def __init__(self, response_text: str) -> None:
        self.messages = _StubMessages(response_text)


def test_extractor_round_trip_with_stub_client() -> None:
    """The full extract() path should work when given a stub client."""
    from core.fingerprint import FingerprintExtractor

    parsed = _load_parsed()
    canned = json.dumps({
        "sponsor": "Candel Therapeutics, Inc.",
        "intervention": ["aglatimagene besadenovec"],
        "indication": "prostate cancer",
        "phase": "2",
        "study_type": "interventional",
        "therapeutic_area": "oncology",
        "extraction_confidence": 0.92,
        "notes": None,
    })
    client = _StubAnthropicClient(canned)

    extractor = FingerprintExtractor(client=client, model="test-model")
    fp = asyncio.run(extractor.extract(parsed))

    assert fp.sponsor == "Candel Therapeutics, Inc."
    assert fp.phase == "2"
    assert fp.extraction_confidence == 0.92

    # Also verify the prompt was sent with the right shape.
    assert len(client.messages.calls) == 1
    call = client.messages.calls[0]
    assert call["model"] == "test-model"
    assert call["messages"][0]["role"] == "user"
    assert "S_PRTK05" in call["messages"][0]["content"]


def test_extractor_overrides_flow_through_round_trip() -> None:
    """Overrides supplied to extract() should win over Claude's output."""
    from core.fingerprint import FingerprintExtractor

    parsed = _load_parsed()
    # Claude returns a wrong sponsor — override should fix it.
    canned = '{"sponsor": "Wrong Inc.", "phase": "2"}'
    client = _StubAnthropicClient(canned)

    extractor = FingerprintExtractor(client=client, model="test-model")
    fp = asyncio.run(extractor.extract(
        parsed, overrides={"sponsor": "Candel Therapeutics, Inc."}
    ))

    assert fp.sponsor == "Candel Therapeutics, Inc."
    assert fp.phase == "2"

    # The prompt should also have included the override section.
    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "<human_supplied>" in prompt
    assert "Candel Therapeutics" in prompt


# ─── Script entry point ────────────────────────────────────────────


if __name__ == "__main__":
    import traceback

    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    failed: list[tuple[str, str]] = []
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception:  # noqa: BLE001
            failed.append((t.__name__, traceback.format_exc()))
            print(f"  FAIL  {t.__name__}")

    print()
    print(f"Ran {len(tests)} tests, {len(failed)} failures.")
    for name, tb in failed:
        print()
        print(f"── {name} ──")
        print(tb)
    sys.exit(1 if failed else 0)
