"""
odm_serializer.py — OdmStudy dict -> CDISC ODM 1.3.2 XML serialiser

Takes the same OdmStudy dict shape produced by odm_reader.parse_odm_metadata()
(see that module's docstring for the full schema) and writes it back out as
valid ODM 1.3.2 XML. This is the deterministic output stage of the XLS-to-ODM
converter: migration/xls_reader.py (not yet built) will construct an OdmStudy
dict from a freeform customer spreadsheet, and this module turns that dict
into XML bytes that feed unchanged into the existing migration pipeline
(odm_validator -> odm_reader -> odm_to_spec -> gap_analysis).

Design principles
------------------
- Deterministic only. No AI calls, no inference -- the OdmStudy dict must
  already be fully formed by the time it reaches serialise().
- Round-trip safe. Every element this module writes must be readable back
  by odm_reader.parse_odm_metadata() with matching counts. validate_round_trip()
  below is the single-call sanity check for that guarantee.
- Originator is always "XLS Converted" so re-parsed XML is never mistaken
  for a real vendor export by odm_reader._detect_vendor() -- downstream gap
  analysis and Syndeo need to know the source is low-fidelity.

Public API
----------
  serialise(odm_study: dict) -> bytes
  validate_round_trip(odm_study: dict) -> list[str]   # empty list = clean
"""

import re
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from odm_reader import parse_odm_metadata

ODM_NS = "http://www.cdisc.org/ns/odm/v1.3"

# Valid ODM 1.3.2 DataType enumeration values.
VALID_ODM_DATATYPES = {
    "text", "integer", "float", "double", "date", "time", "datetime",
    "boolean", "string", "partialdate", "partialtime", "partialdatetime",
    "uri", "base64binary", "hexbinary",
}

# XLSForm-style type strings that xls_reader may put in an item dict before
# odm_serializer runs. Maps back to a valid ODM DataType.
XLSFORM_TO_ODM_DATATYPE = {
    "select_one":       "text",
    "select_multiple":  "text",
    "decimal":          "float",
    "integer":          "integer",
    "date":             "date",
    "time":             "time",
    "dateTime":         "datetime",
    "datetime":         "datetime",
    "text":             "text",
    "string":           "text",
    "note":             "text",
    "calculate":        "text",
}


def _normalise_dtype(dtype):
    """Return a valid ODM DataType string for any item data_type value,
    whether it's already ODM-style or an XLSForm type leaked in from
    xls_reader before this module runs."""
    if not dtype:
        return "text"
    d = dtype.strip()
    if d.lower() in VALID_ODM_DATATYPES:
        return d.lower()
    return XLSFORM_TO_ODM_DATATYPE.get(d, "text")


def _sub(parent, tag, text=None, **attrib):
    """Create a subelement, set text if given, return it."""
    el = ET.SubElement(parent, tag)
    for k, v in attrib.items():
        if v is not None and v != "":
            el.set(k, str(v))
    if text:
        el.text = text
    return el


def _translated_text_el(parent, tag, text):
    """Write <tag><TranslatedText>text</TranslatedText></tag> if text is non-empty."""
    if not text:
        return
    wrapper = _sub(parent, tag)
    tt = _sub(wrapper, "TranslatedText")
    tt.set("xml:lang", "en")
    tt.text = text


def _bool_yn(val):
    return "Yes" if val else "No"


# -- Section builders --------------------------------------------------------

def _write_global_variables(study_el, study):
    gv = _sub(study_el, "GlobalVariables")
    _sub(gv, "StudyName", study.get("name", "") or study.get("protocol_name", "") or "Untitled Study")
    _sub(gv, "StudyDescription", study.get("description", ""))
    _sub(gv, "ProtocolName", study.get("protocol_name", "") or study.get("name", ""))


def _write_measurement_units(study_el, units):
    if not units:
        return
    bd = _sub(study_el, "BasicDefinitions")
    for u in units:
        mu = _sub(bd, "MeasurementUnit", OID=u.get("oid", ""), Name=u.get("name", ""))
        if u.get("symbol"):
            _translated_text_el(mu, "Symbol", u["symbol"])


def _write_protocol(mdv_el, odm_study):
    """Build <Protocol> from odm_study['protocol'] if present, else derive a
    StudyEventRef for every event in document order (first-pass default)."""
    protocol = odm_study.get("protocol") or {}
    events = odm_study.get("events", [])
    proto_el = _sub(mdv_el, "Protocol")

    refs = protocol.get("study_event_refs")
    if not refs:
        refs = [
            {"ref_oid": ev["oid"], "order": i + 1, "mandatory": True}
            for i, ev in enumerate(events)
        ]
    for ref in refs:
        _sub(
            proto_el, "StudyEventRef",
            StudyEventOID=ref.get("ref_oid", ""),
            OrderNumber=ref.get("order", 1),
            Mandatory=_bool_yn(ref.get("mandatory", True)),
        )


def _write_events(mdv_el, events):
    for ev in events:
        se = _sub(
            mdv_el, "StudyEventDef",
            OID=ev.get("oid", ""),
            Name=ev.get("name", "") or ev.get("oid", ""),
            Repeating=_bool_yn(ev.get("repeating", False)),
            Type=ev.get("event_type", "Scheduled"),
        )
        for form_oid in ev.get("form_refs", []):
            _sub(se, "FormRef", FormOID=form_oid)


def _write_forms(mdv_el, forms):
    for fd in forms:
        f = _sub(
            mdv_el, "FormDef",
            OID=fd.get("oid", ""),
            Name=fd.get("name", "") or fd.get("oid", ""),
            Repeating=_bool_yn(fd.get("repeating", False)),
        )
        if fd.get("description"):
            _translated_text_el(f, "Description", fd["description"])
        for ig_oid in fd.get("item_group_refs", []):
            _sub(f, "ItemGroupRef", ItemGroupOID=ig_oid)
        if fd.get("alias"):
            _sub(f, "Alias", Context="Alias", Name=fd["alias"])


def _write_item_groups(mdv_el, item_groups):
    for ig in item_groups:
        g = _sub(
            mdv_el, "ItemGroupDef",
            OID=ig.get("oid", ""),
            Name=ig.get("name", "") or ig.get("oid", ""),
            Repeating=_bool_yn(ig.get("repeating", False)),
        )
        if ig.get("description"):
            _translated_text_el(g, "Description", ig["description"])
        for ir in sorted(ig.get("item_refs", []), key=lambda x: x.get("order", 0)):
            _sub(
                g, "ItemRef",
                ItemOID=ir.get("oid", ""),
                Mandatory=_bool_yn(ir.get("mandatory", False)),
                OrderNumber=ir.get("order", 1),
            )


def _write_items(mdv_el, items):
    for it in items:
        i_el = _sub(
            mdv_el, "ItemDef",
            OID=it.get("oid", ""),
            Name=it.get("name", "") or it.get("oid", ""),
            DataType=_normalise_dtype(it.get("data_type")),
            Length=it.get("length"),
            SignificantDigits=it.get("significant_digits"),
            Comment=it.get("comment", ""),
        )
        if it.get("label"):
            _translated_text_el(i_el, "Question", it["label"])
        if it.get("description"):
            _translated_text_el(i_el, "Description", it["description"])
        if it.get("codelist_ref"):
            _sub(i_el, "CodeListRef", CodeListOID=it["codelist_ref"])
        for unit_oid in it.get("units", []):
            _sub(i_el, "MeasurementUnitRef", MeasurementUnitOID=unit_oid)
        for rc in it.get("range_checks", []):
            rc_el = _sub(
                i_el, "RangeCheck",
                Comparator=rc.get("comparator", ""),
                SoftHard=rc.get("soft_hard", "Soft"),
            )
            _sub(rc_el, "CheckValue", text=rc.get("check_value", ""))
        if it.get("cdash_alias"):
            _sub(i_el, "Alias", Context="CDASH", Name=it["cdash_alias"])
        if it.get("sdtm_alias"):
            _sub(i_el, "Alias", Context="SDTM", Name=it["sdtm_alias"])


def _write_codelists(mdv_el, codelists):
    for cl in codelists:
        cl_el = _sub(
            mdv_el, "CodeList",
            OID=cl.get("oid", ""),
            Name=cl.get("name", "") or cl.get("oid", ""),
            DataType=_normalise_dtype(cl.get("data_type")) if cl.get("data_type") else "text",
        )
        for item in sorted(cl.get("items", []), key=lambda x: x.get("order", 0)):
            coded = item.get("coded_value", "")
            decode = item.get("decode", "")
            if coded == decode or not decode:
                _sub(cl_el, "EnumeratedItem", CodedValue=coded, OrderNumber=item.get("order", 1))
            else:
                cli = _sub(cl_el, "CodeListItem", CodedValue=coded, OrderNumber=item.get("order", 1))
                _translated_text_el(cli, "Decode", decode)


# -- Public API ----------------------------------------------------------------

def serialise(odm_study):
    """Serialise an OdmStudy dict (see odm_reader module docstring for schema)
    into ODM 1.3.2 XML bytes, ready to feed into odm_validator / odm_reader."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    root = ET.Element("ODM", {
        "xmlns": ODM_NS,
        "ODMVersion": odm_study.get("odm_version", "1.3.2"),
        "FileOID": odm_study.get("file_oid") or f"XLS_CONVERTED_{now}",
        "FileType": odm_study.get("file_type", "Snapshot"),
        "CreationDateTime": odm_study.get("creation_datetime") or now,
        "Originator": "XLS Converted",
    })

    study = odm_study.get("study", {}) or {}
    study_el = _sub(root, "Study", OID=study.get("oid", "") or "S_XLS_CONVERTED")
    _write_global_variables(study_el, study)
    _write_measurement_units(study_el, odm_study.get("measurement_units", []))

    mdv_el = _sub(
        study_el, "MetaDataVersion",
        OID=study.get("metadata_version_oid", "") or "MDV.1",
        Name=study.get("metadata_version_name", "") or "Version 1",
    )
    _write_protocol(mdv_el, odm_study)
    _write_events(mdv_el, odm_study.get("events", []))
    _write_forms(mdv_el, odm_study.get("forms", []))
    _write_item_groups(mdv_el, odm_study.get("item_groups", []))
    _write_items(mdv_el, odm_study.get("items", []))
    _write_codelists(mdv_el, odm_study.get("codelists", []))

    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass  # Python < 3.9 -- skip pretty-printing, still valid XML

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def validate_round_trip(odm_study):
    """Serialise odm_study, re-parse it with odm_reader, and compare counts
    for every element type plus study identity. Returns a list of issue
    strings -- empty list means the round trip was clean."""
    issues = []
    xml_bytes = serialise(odm_study)
    reparsed = parse_odm_metadata(xml_bytes)

    for key in ("events", "forms", "item_groups", "items", "codelists", "measurement_units"):
        orig_n = len(odm_study.get(key, []) or [])
        new_n = len(reparsed.get(key, []) or [])
        if orig_n != new_n:
            issues.append(f"{key}: expected {orig_n} after round-trip, got {new_n}")

    orig_oid = (odm_study.get("study") or {}).get("oid", "")
    new_oid = (reparsed.get("study") or {}).get("oid", "")
    if orig_oid != new_oid:
        issues.append(f"study.oid: expected '{orig_oid}', got '{new_oid}'")

    if reparsed.get("source_system") != "UNKNOWN":
        issues.append(
            f"source_system: expected 'UNKNOWN' (Originator should not match a "
            f"real vendor), got '{reparsed.get('source_system')}'"
        )

    return issues


# -- CLI entrypoint --------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2 or sys.argv[1] != "--selftest":
        print("Usage: python odm_serializer.py --selftest")
        print("Runs a built-in round-trip check against a representative OdmStudy dict.")
        sys.exit(1)

    sample = {
        "odm_version": "1.3.2",
        "study": {
            "oid": "S_TEST_STUDY",
            "name": "Test Study",
            "description": "Synthetic self-test",
            "protocol_name": "TEST-001",
            "metadata_version_oid": "MDV.1",
            "metadata_version_name": "Version 1",
        },
        "events": [
            {"oid": "SE_UNSCHEDULED", "name": "Unscheduled", "repeating": False,
             "event_type": "Unscheduled", "form_refs": ["F_SUBJECTS"], "vendor": {}},
        ],
        "forms": [
            {"oid": "F_SUBJECTS", "name": "Subjects", "repeating": False,
             "description": "Master subject table", "alias": "",
             "item_group_refs": ["IG_SUBJECTS"], "vendor": {}},
        ],
        "item_groups": [
            {"oid": "IG_SUBJECTS", "name": "Subjects", "repeating": False,
             "description": "", "item_refs": [
                 {"oid": "I_GENDER", "mandatory": False, "order": 1},
                 {"oid": "I_DOBDATE", "mandatory": False, "order": 2},
             ], "vendor": {}},
        ],
        "items": [
            {"oid": "I_GENDER", "name": "Gender", "data_type": "select_one",
             "length": None, "significant_digits": None, "label": "Gender",
             "description": "", "comment": "", "cdash_alias": "SEX", "sdtm_alias": "SEX",
             "codelist_ref": "CL_GENDER", "units": [], "range_checks": [], "vendor": {}},
            {"oid": "I_DOBDATE", "name": "DOB", "data_type": "date",
             "length": None, "significant_digits": None, "label": "Date of Birth",
             "description": "", "comment": "", "cdash_alias": "", "sdtm_alias": "",
             "codelist_ref": None, "units": [], "range_checks": [], "vendor": {}},
        ],
        "codelists": [
            {"oid": "CL_GENDER", "name": "Gender", "data_type": "text",
             "items": [
                 {"coded_value": "M", "decode": "Male", "order": 1},
                 {"coded_value": "F", "decode": "Female", "order": 2},
             ], "vendor": {}},
        ],
        "measurement_units": [],
        "protocol": {},
        "parse_warnings": [],
    }

    issues = validate_round_trip(sample)
    if issues:
        print(f"ROUND-TRIP FAILED -- {len(issues)} issue(s):")
        for i in issues:
            print(f"  x  {i}")
        sys.exit(1)
    else:
        xml_bytes = serialise(sample)
        print(f"ROUND-TRIP CLEAN. Output size: {len(xml_bytes):,} bytes")
        print()
        print(xml_bytes.decode("utf-8")[:600])
        sys.exit(0)
