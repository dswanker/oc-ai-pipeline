"""
Tests for ``core.form_parser.odm_xml.ODMXMLParser``.

Runs under pytest (preferred) or as a plain script:

    python tests/test_odm_xml_parser.py

Both modes exercise the same assertions.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make this work standalone (`python tests/test_odm_xml_parser.py`) by
# putting the package root on sys.path before importing core.*
# pytest doesn't need this — it picks up tool.pytest.ini_options.pythonpath
# from pyproject.toml — but harmless when pytest is the runner.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.form_parser.base import FormFormat
from core.form_parser.odm_xml import ODMXMLParser, _infer_domain

FIXTURE = Path(__file__).parent / "fixtures" / "prtk05_sample.odm.xml"


def _load_fixture() -> bytes:
    return FIXTURE.read_bytes()


def _parse() -> "ParsedForm":  # noqa: F821 — runtime import
    parser = ODMXMLParser()
    return asyncio.run(parser.parse(_load_fixture(), filename=FIXTURE.name))


# ─── Top-level metadata ────────────────────────────────────────────

def test_returns_parsed_form_with_correct_format() -> None:
    parsed = _parse()
    assert parsed.source_format == FormFormat.ODM_XML


def test_extracts_study_oid() -> None:
    parsed = _parse()
    assert parsed.study_oid == "S_PRTK05"


def test_extracts_study_name() -> None:
    parsed = _parse()
    assert parsed.study_name is not None
    assert "PrTK05" in parsed.study_name


def test_extracts_sponsor_from_openclinica_extension() -> None:
    """OC:Sponsor extension should win over the regex on StudyDescription."""
    parsed = _parse()
    assert parsed.sponsor == "Candel Therapeutics, Inc."


def test_extracts_protocol_name_into_raw_metadata() -> None:
    parsed = _parse()
    assert parsed.raw_metadata.get("protocol_name") == "PrTK05"


def test_includes_openclinica_details_in_raw_metadata() -> None:
    parsed = _parse()
    details = parsed.raw_metadata.get("openclinica_details")
    assert details is not None
    assert details.get("Phase") == "Phase 2"
    assert details.get("ProtocolType") == "interventional"


# ─── Form structure ────────────────────────────────────────────────

def test_finds_all_nine_forms() -> None:
    parsed = _parse()
    assert len(parsed.forms) == 9


def test_form_oids_match_fixture() -> None:
    parsed = _parse()
    expected = {
        "F_DM", "F_IE", "F_VS", "F_PSA", "F_EX",
        "F_CM", "F_AE", "F_BIOMARKER", "F_DS",
    }
    actual = {f.oid for f in parsed.forms}
    assert actual == expected


def test_demographics_form_has_expected_items() -> None:
    parsed = _parse()
    dm = next(f for f in parsed.forms if f.oid == "F_DM")
    # One group, five items.
    assert len(dm.groups) == 1
    items = dm.groups[0].items
    assert len(items) == 5
    item_names = [it.name for it in items]
    assert item_names == ["BRTHDAT", "AGE", "SEX", "RACE", "ETHNIC"]


def test_exposure_form_has_two_groups() -> None:
    """F_EX has both INJECTION and VALACYCLOVIR groups."""
    parsed = _parse()
    ex = next(f for f in parsed.forms if f.oid == "F_EX")
    assert len(ex.groups) == 2
    group_oids = [g.oid for g in ex.groups]
    assert "IG_EX_INJECTION" in group_oids
    assert "IG_EX_VALACYCLOVIR" in group_oids


def test_item_labels_extracted() -> None:
    parsed = _parse()
    psa_form = next(f for f in parsed.forms if f.oid == "F_PSA")
    psa_val = next(it for it in psa_form.groups[0].items if it.name == "PSAVAL")
    assert "PSA result" in psa_val.label
    assert "ng/mL" in psa_val.label


def test_codelist_items_marked_as_select_one() -> None:
    """Items that reference a CodeListRef should be reported as select_one,
    not plain text."""
    parsed = _parse()
    dm = next(f for f in parsed.forms if f.oid == "F_DM")
    sex = next(it for it in dm.groups[0].items if it.name == "SEX")
    assert sex.data_type == "select_one"


def test_data_type_preserved_for_non_coded_items() -> None:
    parsed = _parse()
    vs = next(f for f in parsed.forms if f.oid == "F_VS")
    sysbp = next(it for it in vs.groups[0].items if it.name == "SYSBP")
    assert sysbp.data_type == "integer"


# ─── CDASH domain inference ────────────────────────────────────────

def test_demographics_form_inferred_as_dm_domain() -> None:
    parsed = _parse()
    dm = next(f for f in parsed.forms if f.oid == "F_DM")
    assert all(it.domain == "DM" for it in dm.groups[0].items)


def test_adverse_events_form_inferred_as_ae_domain() -> None:
    parsed = _parse()
    ae = next(f for f in parsed.forms if f.oid == "F_AE")
    assert all(it.domain == "AE" for it in ae.groups[0].items)


def test_psa_form_inferred_as_lb_domain() -> None:
    """PSA is a lab — the domain table maps the keyword PSA to LB."""
    parsed = _parse()
    psa = next(f for f in parsed.forms if f.oid == "F_PSA")
    assert all(it.domain == "LB" for it in psa.groups[0].items)


def test_biomarker_form_inferred_as_lb_domain() -> None:
    parsed = _parse()
    bio = next(f for f in parsed.forms if f.oid == "F_BIOMARKER")
    assert all(it.domain == "LB" for it in bio.groups[0].items)


# ─── Domain inference unit tests ───────────────────────────────────

def test_infer_domain_from_oid_prefix() -> None:
    assert _infer_domain("F_DM", "Demographics") == "DM"
    assert _infer_domain("F_AE", "Adverse Events") == "AE"
    assert _infer_domain("F_EX", "Some custom name") == "EX"


def test_infer_domain_from_name_when_oid_unhelpful() -> None:
    """Form OIDs that don't follow conventions still resolve via name."""
    assert _infer_domain("F_CUSTOM_001", "Vital Signs Procedure") == "VS"
    assert _infer_domain("F_CUSTOM_002", "Concomitant Medications") == "CM"


def test_infer_domain_returns_none_for_unknown() -> None:
    assert _infer_domain("F_QOL_EQ5D", "Quality of Life Survey") is None


# ─── Bad input handling ────────────────────────────────────────────

def test_invalid_xml_raises_value_error() -> None:
    parser = ODMXMLParser()
    try:
        asyncio.run(parser.parse(b"<not><well-formed></not>"))
    except ValueError as exc:
        assert "Invalid ODM XML" in str(exc)
    else:
        raise AssertionError("Expected ValueError on malformed XML")


def test_xml_without_study_element_raises() -> None:
    parser = ODMXMLParser()
    minimal = b'<?xml version="1.0"?><ODM xmlns="http://www.cdisc.org/ns/odm/v1.3"/>'
    try:
        asyncio.run(parser.parse(minimal))
    except ValueError as exc:
        assert "no <Study> element" in str(exc)
    else:
        raise AssertionError("Expected ValueError when Study element missing")


# ─── XXE / network-fetch defenses ──────────────────────────────────

def test_external_entity_is_not_resolved() -> None:
    """
    Classic XXE — if the parser had ``resolve_entities=True`` this would
    happily try to read /etc/passwd. With our hardened settings, lxml
    leaves the entity unresolved and the document parses without
    surfacing the file contents.
    """
    xxe_payload = b"""<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3">
  <Study OID="S_X">
    <GlobalVariables>
      <StudyName>&xxe;</StudyName>
    </GlobalVariables>
  </Study>
</ODM>
"""
    parser = ODMXMLParser()
    parsed = asyncio.run(parser.parse(xxe_payload))
    name = parsed.study_name or ""
    # Whatever the StudyName resolves to, it must not contain the
    # contents of /etc/passwd. The 'root:' prefix is the universal
    # marker for that file.
    assert "root:" not in name


# ─── Script entry point ────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
