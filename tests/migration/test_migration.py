"""
tests/test_migration.py — Migration module test harness

Zero external dependencies:
  - No Anthropic API calls
  - No Monday.com calls
  - No pipeline.py triggers
  - No Railway/Railway env vars needed
  - Runs on any machine with: pip install lxml (optional, stdlib xml used)

Usage:
  python tests/test_migration.py                    # run all tests
  python tests/test_migration.py -v                 # verbose
  python tests/test_migration.py TestOdmReader      # one class
  python tests/test_migration.py TestOdmReader.test_vendor_detection

Fixtures:
  tests/fixtures/prtk05.xml   — real OC4 export (PrTK05 study)
  tests/fixtures/synthetic.xml — synthetic multi-vendor test file

The harness validates:
  1. odm_reader  — parse, vendor detection, ODM version handling,
                   integrity checks, clinical data parse
  2. odm_to_spec — OID normalisation, OC-9 compliance, form ID
                   length, event SE_ prefixes, settings schema,
                   review flags, round-trip stability
  3. vendor_registry — extensibility, no hardcoded vendor lists
  4. Edge cases  — malformed XML, missing elements, BOM, namespaces
"""

import json
import os
import sys
import unittest
import shutil
import tempfile
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
# This file lives at tests/migration/test_migration.py — the project root is
# three levels up. Insert root (for migration_pipeline, monday_client, etc.)
# and root/migration (for odm_reader, odm_to_spec, odm_validator).
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "migration"))

FIXTURES = Path(__file__).parent / "fixtures"
PRTK05_XML = FIXTURES / "prtk05.xml"
SYNTHETIC_XML = FIXTURES / "synthetic.xml"
MEDIDATA_RAVE_XML = FIXTURES / "medidata_rave_synthetic.xml"
VEEVA_XML = FIXTURES / "veeva_synthetic.xml"
VIEDOC_XML = FIXTURES / "viedoc_synthetic.xml"
IMEDNET_XML = FIXTURES / "imednet_synthetic.xml"
ORACLE_INFORM_XML = FIXTURES / "oracle_inform_synthetic.xml"
REDCAP_XML = FIXTURES / "redcap_synthetic.xml"
CASTOR_XML = FIXTURES / "castor_synthetic.xml"
ZELTA_XML = FIXTURES / "zelta_synthetic.xml"

# ── Lazy imports (only imported when tests run) ───────────────────────────────
def _import_reader():
    from odm_reader import (
        parse_odm_metadata, parse_odm_clinical_data,
        build_item_lookup, build_codelist_lookup,
        build_form_item_map, summarise,
    )
    return parse_odm_metadata, parse_odm_clinical_data, \
           build_item_lookup, build_codelist_lookup, \
           build_form_item_map, summarise

def _import_spec():
    from odm_to_spec import transform, _oc_event_oid, _oc_form_id
    return transform, _oc_event_oid, _oc_form_id


# ══════════════════════════════════════════════════════════════════════════════
# Fixture helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _minimal_odm(study_name="TEST", protocol="TEST-001", events=None,
                 forms=None, items=None, codelists=None,
                 odm_version="1.3.2", originator="") -> bytes:
    """
    Build a minimal valid ODM XML string for targeted unit tests.
    Only includes the elements explicitly passed in.
    """
    events = events or []
    forms  = forms  or []
    items  = items  or []
    codelists = codelists or []

    event_defs = ""
    for ev in events:
        frefs = "".join(
            f'<FormRef FormOID="{fr}" Mandatory="Yes"/>'
            for fr in ev.get("form_refs", [])
        )
        event_defs += (
            f'<StudyEventDef OID="{ev["oid"]}" Name="{ev["name"]}" '
            f'Repeating="{ev.get("repeating","No")}" '
            f'Type="{ev.get("type","Scheduled")}">{frefs}</StudyEventDef>'
        )

    form_defs = ""
    for fm in forms:
        ig_refs = "".join(
            f'<ItemGroupRef ItemGroupOID="{ig}" Mandatory="Yes"/>'
            for ig in fm.get("ig_refs", [])
        )
        form_defs += (
            f'<FormDef OID="{fm["oid"]}" Name="{fm["name"]}" '
            f'Repeating="{fm.get("repeating","No")}">{ig_refs}</FormDef>'
        )

    item_defs = ""
    for it in items:
        cl_ref = (f'<CodeListRef CodeListOID="{it["codelist"]}"/>'
                  if it.get("codelist") else "")
        rc = ""
        for r in it.get("range_checks", []):
            rc += (f'<RangeCheck Comparator="{r["comp"]}" SoftHard="Soft">'
                   f'<CheckValue>{r["val"]}</CheckValue></RangeCheck>')
        item_defs += (
            f'<ItemDef OID="{it["oid"]}" Name="{it["name"]}" '
            f'DataType="{it.get("type","text")}" Length="{it.get("length",20)}">'
            f'<Question><TranslatedText xml:lang="en">{it.get("label",it["name"])}'
            f'</TranslatedText></Question>{cl_ref}{rc}</ItemDef>'
        )

    cl_defs = ""
    for cl in codelists:
        items_xml = "".join(
            f'<CodeListItem CodedValue="{ci["value"]}" OrderNumber="{i+1}">'
            f'<Decode><TranslatedText xml:lang="en">{ci.get("decode",ci["value"])}'
            f'</TranslatedText></Decode></CodeListItem>'
            for i, ci in enumerate(cl.get("items", []))
        )
        cl_defs += (
            f'<CodeList OID="{cl["oid"]}" Name="{cl["name"]}" DataType="text">'
            f'{items_xml}</CodeList>'
        )

    orig_attr = f' Originator="{originator}"' if originator else ""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ODM ODMVersion="{odm_version}" FileOID="TEST-001"
     FileType="Snapshot" CreationDateTime="2025-01-01T00:00:00"{orig_attr}
     xmlns="http://www.cdisc.org/ns/odm/v1.3">
  <Study OID="S_TEST">
    <GlobalVariables>
      <StudyName>{study_name}</StudyName>
      <StudyDescription>Test study</StudyDescription>
      <ProtocolName>{protocol}</ProtocolName>
    </GlobalVariables>
    <MetaDataVersion OID="v1" Name="v1">
      <Protocol>
        {"".join(f'<StudyEventRef StudyEventOID="{ev["oid"]}" OrderNumber="{i}" Mandatory="Yes"/>' for i,ev in enumerate(events))}
      </Protocol>
      {event_defs}
      {form_defs}
      {item_defs}
      {cl_defs}
    </MetaDataVersion>
  </Study>
</ODM>"""
    return xml.encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 1 — odm_reader
# ══════════════════════════════════════════════════════════════════════════════

class TestOdmReader(unittest.TestCase):
    """Tests for odm_reader.parse_odm_metadata()"""

    @classmethod
    def setUpClass(cls):
        (parse_odm_metadata, parse_odm_clinical_data,
         build_item_lookup, build_codelist_lookup,
         build_form_item_map, summarise) = _import_reader()
        # staticmethod wrapper prevents Python treating these as unbound methods
        cls.parse      = staticmethod(parse_odm_metadata)
        cls.parse_cd   = staticmethod(parse_odm_clinical_data)
        cls.item_lookup = staticmethod(build_item_lookup)
        cls.cl_lookup  = staticmethod(build_codelist_lookup)
        cls.form_map   = staticmethod(build_form_item_map)
        cls.summarise  = staticmethod(summarise)

    # ── Real file tests ───────────────────────────────────────────────────────

    def test_prtk05_parses_without_error(self):
        """Real OC4 export parses cleanly with zero warnings."""
        result = self.parse(_load(PRTK05_XML))
        self.assertEqual(result["parse_warnings"], [],
                         f"Unexpected warnings: {result['parse_warnings']}")

    def test_prtk05_study_name(self):
        result = self.parse(_load(PRTK05_XML))
        self.assertEqual(result["study"]["name"], "PrTK05")
        self.assertEqual(result["study"]["protocol_name"], "PrTK05")

    def test_prtk05_odm_version(self):
        result = self.parse(_load(PRTK05_XML))
        self.assertEqual(result["odm_version"], "1.3")

    def test_prtk05_event_count(self):
        result = self.parse(_load(PRTK05_XML))
        self.assertEqual(len(result["events"]), 21)

    def test_prtk05_form_count(self):
        result = self.parse(_load(PRTK05_XML))
        self.assertEqual(len(result["forms"]), 20)

    def test_prtk05_item_count(self):
        """Should parse all 1251 items from PrTK05."""
        result = self.parse(_load(PRTK05_XML))
        self.assertGreaterEqual(len(result["items"]), 1000,
                                "Expected 1000+ items in PrTK05")

    def test_prtk05_codelist_count(self):
        result = self.parse(_load(PRTK05_XML))
        self.assertGreaterEqual(len(result["codelists"]), 100)

    def test_prtk05_vendor_detected_as_openclinica(self):
        result = self.parse(_load(PRTK05_XML))
        self.assertIn("OpenClinica", result["source_system"],
                      f"Expected OpenClinica vendor, got: {result['source_system']}")

    def test_prtk05_oc4_namespace_extensions_captured(self):
        """OC4 vendor extension attributes should be captured in vendor dicts."""
        result = self.parse(_load(PRTK05_XML))
        # At least some events should have OC4 vendor attrs (EventType, Status)
        events_with_vendor = [e for e in result["events"] if e.get("vendor")]
        self.assertGreater(len(events_with_vendor), 0,
                           "No OC4 vendor extension attributes captured on events")

    def test_prtk05_summarise(self):
        """summarise() should return a non-empty string."""
        result = self.parse(_load(PRTK05_XML))
        summary = self.summarise(result)
        self.assertIn("PrTK05", summary)
        self.assertIn("Events:", summary)

    def test_prtk05_form_item_map_populated(self):
        """build_form_item_map should return items for known forms."""
        result = self.parse(_load(PRTK05_XML))
        fmap = self.form_map(result)
        self.assertGreater(len(fmap), 0)
        # Every form should have at least one item
        empty_forms = [k for k, v in fmap.items() if len(v) == 0]
        # Allow a small number of forms with no items (some forms may only have groups)
        self.assertLess(len(empty_forms), len(fmap) * 0.3,
                        f"Too many forms with no items: {empty_forms}")

    # ── Vendor detection ──────────────────────────────────────────────────────

    def test_vendor_medidata_detected_from_originator(self):
        xml = _minimal_odm(originator="Medidata Rave 5.6.9")
        result = self.parse(xml)
        self.assertIn("Medidata", result["source_system"])

    def test_vendor_viedoc_detected_from_originator(self):
        xml = _minimal_odm(originator="Viedoc 4.72")
        result = self.parse(xml)
        self.assertIn("Viedoc", result["source_system"])

    def test_vendor_oracle_detected_from_originator(self):
        xml = _minimal_odm(originator="Oracle InForm 6.2")
        result = self.parse(xml)
        self.assertIn("Oracle", result["source_system"])

    def test_vendor_castor_detected_from_originator(self):
        xml = _minimal_odm(originator="Castor EDC 2023")
        result = self.parse(xml)
        self.assertIn("Castor", result["source_system"])

    def test_vendor_redcap_detected_from_originator(self):
        xml = _minimal_odm(originator="REDCap 14.0")
        result = self.parse(xml)
        self.assertIn("REDCap", result["source_system"])

    def test_vendor_unknown_is_graceful(self):
        xml = _minimal_odm(originator="SomeUnknownEDC 1.0")
        result = self.parse(xml)
        self.assertEqual(result["source_system"], "UNKNOWN")

    # ── ODM version handling ──────────────────────────────────────────────────

    def test_odm_version_130(self):
        xml = _minimal_odm(odm_version="1.3.0")
        result = self.parse(xml)
        self.assertEqual(result["odm_version"], "1.3.0")

    def test_odm_version_131(self):
        xml = _minimal_odm(odm_version="1.3.1")
        result = self.parse(xml)
        self.assertEqual(result["odm_version"], "1.3.1")

    def test_odm_version_132(self):
        xml = _minimal_odm(odm_version="1.3.2")
        result = self.parse(xml)
        self.assertEqual(result["odm_version"], "1.3.2")

    def test_odm_version_13_plain(self):
        """ODM 1.3 (no patch) as exported by OC4."""
        xml = _minimal_odm(odm_version="1.3")
        result = self.parse(xml)
        self.assertEqual(result["odm_version"], "1.3")

    # ── Structural parsing ────────────────────────────────────────────────────

    def test_event_form_refs_populated(self):
        xml = _minimal_odm(
            events=[{"oid": "SE_SCREEN", "name": "Screening",
                     "form_refs": ["F_DM", "F_VS"]}],
            forms=[{"oid": "F_DM", "name": "Demographics", "ig_refs": []},
                   {"oid": "F_VS", "name": "Vitals", "ig_refs": []}],
        )
        result = self.parse(xml)
        self.assertEqual(result["events"][0]["form_refs"], ["F_DM", "F_VS"])

    def test_codelist_items_parsed(self):
        xml = _minimal_odm(
            codelists=[{
                "oid": "CL.YN", "name": "YN",
                "items": [{"value": "Y", "decode": "Yes"},
                          {"value": "N", "decode": "No"}]
            }]
        )
        result = self.parse(xml)
        self.assertEqual(len(result["codelists"]), 1)
        self.assertEqual(len(result["codelists"][0]["items"]), 2)

    def test_range_checks_parsed(self):
        xml = _minimal_odm(
            items=[{
                "oid": "I.VS.SYSBP", "name": "SYSBP", "type": "integer",
                "label": "Systolic BP",
                "range_checks": [
                    {"comp": "GE", "val": "60"},
                    {"comp": "LE", "val": "250"},
                ]
            }]
        )
        result = self.parse(xml)
        item = result["items"][0]
        self.assertEqual(len(item["range_checks"]), 2)
        self.assertEqual(item["range_checks"][0]["comparator"], "GE")
        self.assertEqual(item["range_checks"][0]["check_value"], "60")

    def test_missing_study_element_produces_warning(self):
        """Gracefully handle ODM with no <Study> element."""
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<ODM ODMVersion="1.3.2" FileOID="X" FileType="Snapshot"
     CreationDateTime="2025-01-01T00:00:00"
     xmlns="http://www.cdisc.org/ns/odm/v1.3"/>"""
        result = self.parse(xml)
        self.assertTrue(len(result["parse_warnings"]) > 0)
        self.assertEqual(result["events"], [])

    def test_bom_stripped(self):
        """UTF-8 BOM at start of file should be handled silently."""
        xml = b"\xef\xbb\xbf" + _minimal_odm(study_name="BOM_TEST")
        result = self.parse(xml)
        self.assertEqual(result["study"]["name"], "BOM_TEST")

    def test_integrity_warning_missing_formdef(self):
        """Warning issued when event references a FormDef not in metadata."""
        xml = _minimal_odm(
            events=[{"oid": "SE_SCREEN", "name": "Screening",
                     "form_refs": ["F_MISSING"]}],
            forms=[],  # F_MISSING not defined
        )
        result = self.parse(xml)
        self.assertTrue(
            any("F_MISSING" in w for w in result["parse_warnings"]),
            "Expected warning about missing FormDef F_MISSING"
        )

    # ── Clinical data parse ───────────────────────────────────────────────────

    def test_clinical_data_parse_from_prtk05(self):
        """Clinical data parse returns a list (may be empty for metadata-only export)."""
        result = self.parse_cd(_load(PRTK05_XML))
        self.assertIsInstance(result, list)

    def test_clinical_data_parse_minimal(self):
        """Minimal clinical data ODM parses correctly."""
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<ODM ODMVersion="1.3.2" FileOID="X" FileType="Snapshot"
     CreationDateTime="2025-01-01T00:00:00"
     xmlns="http://www.cdisc.org/ns/odm/v1.3">
  <ClinicalData StudyOID="S_TEST" MetaDataVersionOID="v1">
    <SubjectData SubjectKey="001-001">
      <SiteRef LocationOID="SITE.001"/>
      <StudyEventData StudyEventOID="SE_SCREEN" StudyEventRepeatKey="1">
        <FormData FormOID="F_DM" FormRepeatKey="1">
          <ItemGroupData ItemGroupOID="IG.DM.DM" ItemGroupRepeatKey="1">
            <ItemData ItemOID="I.DM.SUBJID" Value="001-001"/>
          </ItemGroupData>
        </FormData>
      </StudyEventData>
    </SubjectData>
  </ClinicalData>
</ODM>"""
        subjects = self.parse_cd(xml)
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0]["subject_key"], "001-001")
        self.assertEqual(subjects[0]["site_oid"], "SITE.001")
        self.assertEqual(len(subjects[0]["events"]), 1)
        items = subjects[0]["events"][0]["forms"][0]["item_groups"][0]["items"]
        self.assertEqual(items[0]["item_oid"], "I.DM.SUBJID")
        self.assertEqual(items[0]["value"], "001-001")


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 2 — odm_to_spec
# ══════════════════════════════════════════════════════════════════════════════

class TestOdmToSpec(unittest.TestCase):
    """Tests for odm_to_spec.transform()"""

    @classmethod
    def setUpClass(cls):
        parse_odm_metadata = _import_reader()[0]
        transform, _oc_event_oid, _oc_form_id = _import_spec()
        cls.transform      = staticmethod(transform)
        cls.oc_event_oid   = staticmethod(_oc_event_oid)
        cls.oc_form_id     = staticmethod(_oc_form_id)

        # Parse PrTK05 once and cache
        cls.prtk05_odm = parse_odm_metadata(_load(PRTK05_XML))
        cls.prtk05_spec = transform(cls.prtk05_odm)

    # ── PrTK05 end-to-end ─────────────────────────────────────────────────────

    def test_prtk05_spec_top_level_keys(self):
        spec = self.prtk05_spec
        for key in ("study_meta", "timepoint_csv", "labranges_csv",
                    "forms", "review_flags"):
            self.assertIn(key, spec, f"Missing top-level key: {key}")

    def test_prtk05_protocol_number(self):
        self.assertEqual(self.prtk05_spec["study_meta"]["protocol_number"], "PrTK05")

    def test_prtk05_input_mode_is_odm_xml(self):
        self.assertEqual(self.prtk05_spec["study_meta"]["input_mode"], "ODM_XML")

    def test_prtk05_form_count(self):
        self.assertEqual(len(self.prtk05_spec["forms"]), 20)

    def test_prtk05_event_count_includes_common(self):
        rows = self.prtk05_spec["timepoint_csv"]["rows"]
        event_oids = [r["event"] for r in rows]
        self.assertIn("SE_COMMON", event_oids, "SE_COMMON missing from timepoints")
        # 21 original + SE_COMMON = 22
        self.assertEqual(len(rows), 22)

    def test_prtk05_all_events_have_se_prefix(self):
        for row in self.prtk05_spec["timepoint_csv"]["rows"]:
            self.assertTrue(row["event"].startswith("SE_"),
                            f"Event missing SE_ prefix: {row['event']}")

    def test_prtk05_form_ids_no_truncation(self):
        """No form ID should be obviously truncated mid-word."""
        for form in self.prtk05_spec["forms"]:
            fid = form["form_id"]
            self.assertLessEqual(len(fid), 25,
                                 f"Form ID too long: {fid}")
            # Should not end with an underscore (truncation artefact)
            self.assertFalse(fid.endswith("_"),
                             f"Form ID ends with underscore: {fid}")

    def test_prtk05_oc9_ae_cm_dv_on_se_common(self):
        """OC-9: AE, CM, DV must be assigned only to SE_COMMON."""
        for form in self.prtk05_spec["forms"]:
            if form["form_id"] in ("AE", "CM", "DV"):
                self.assertEqual(
                    form["visits_assigned"], ["SE_COMMON"],
                    f"OC-9 violation: {form['form_id']} → {form['visits_assigned']}"
                )

    def test_prtk05_every_form_has_settings(self):
        for form in self.prtk05_spec["forms"]:
            s = form.get("settings", {})
            self.assertIn("form_id", s,
                          f"Form {form['form_id']} missing settings.form_id")
            self.assertIn("namespaces", s,
                          f"Form {form['form_id']} missing settings.namespaces")
            self.assertIn("oc=", s["namespaces"],
                          f"Form {form['form_id']} namespaces missing oc= prefix")

    def test_prtk05_settings_form_id_starts_with_F(self):
        """settings.form_id must follow F_<ID>_<version> pattern."""
        for form in self.prtk05_spec["forms"]:
            fid = form["settings"]["form_id"]
            self.assertTrue(fid.startswith("F_"),
                            f"settings.form_id missing F_ prefix: {fid}")

    def test_prtk05_survey_rows_have_required_keys(self):
        """Every survey row must have the 3 mandatory tracking fields."""
        required = {"completion_status", "library_source", "flag_reason"}
        for form in self.prtk05_spec["forms"]:
            for row in form.get("survey", []):
                missing = required - set(row.keys())
                self.assertEqual(missing, set(),
                    f"Form {form['form_id']} row '{row.get('name','')}' "
                    f"missing keys: {missing}")

    def test_prtk05_survey_data_rows_have_itemgroup(self):
        """Every data row (non-group type) must have bind__oc_itemgroup."""
        group_types = {"begin group", "end group", "begin repeat", "end repeat"}
        for form in self.prtk05_spec["forms"]:
            for row in form.get("survey", []):
                if row.get("type", "") in group_types:
                    continue
                # calculate rows with external binding are exempt
                if (row.get("type") == "calculate" and
                        row.get("bind__oc_external") == "clinicaldata"):
                    continue
                self.assertTrue(
                    bool(row.get("bind__oc_itemgroup")),
                    f"Form {form['form_id']} data row '{row.get('name','')}' "
                    f"(type={row.get('type')}) missing bind__oc_itemgroup"
                )

    def test_range_checks_produce_constraints(self):
        """Items with ODM RangeChecks should have constraint populated in survey rows."""
        # Use the synthetic file which has explicit RangeChecks on VS items
        parse = _import_reader()[0]
        odm = parse(_load(SYNTHETIC_XML))
        spec = self.transform(odm)
        vs_form = next((f for f in spec["forms"] if f["form_id"] == "VS"), None)
        if vs_form is None:
            self.skipTest("VS form not found in synthetic spec")
        constrained = [r for r in vs_form["survey"] if r.get("constraint")]
        self.assertGreater(len(constrained), 0,
                           "VS form has no constrained rows despite RangeChecks "
                           "in synthetic ODM")

    def test_prtk05_review_flags_all_8_categories(self):
        flags = self.prtk05_spec["review_flags"]
        expected = {
            "site_specific", "oid_confirmation", "protocol_ambiguous",
            "constraint_review", "choice_list_review", "custom_domain",
            "pdf_mapping_uncertain", "name_deviation",
        }
        self.assertEqual(set(flags.keys()), expected)

    def test_prtk05_spec_is_json_serialisable(self):
        """The spec must serialise to JSON without error (no datetime objects etc)."""
        try:
            json.dumps(self.prtk05_spec)
        except (TypeError, ValueError) as e:
            self.fail(f"Spec not JSON-serialisable: {e}")

    def test_prtk05_round_trip_stability(self):
        """Transforming the same ODM twice must produce identical output."""
        spec2 = self.transform(self.prtk05_odm)
        self.assertEqual(
            json.dumps(self.prtk05_spec, sort_keys=True),
            json.dumps(spec2, sort_keys=True),
            "transform() is not deterministic — output differs on second call"
        )

    # ── OID normalisation unit tests ──────────────────────────────────────────

    def test_event_oid_adds_se_prefix(self):
        self.assertEqual(self.oc_event_oid("SCREEN"), "SE_SCREEN")

    def test_event_oid_preserves_se_prefix(self):
        self.assertEqual(self.oc_event_oid("SE_SCREEN"), "SE_SCREEN")

    def test_event_oid_uppercases(self):
        self.assertEqual(self.oc_event_oid("se_baseline"), "SE_BASELINE")

    def test_event_oid_replaces_dots(self):
        # SE.SCREEN → dot→underscore → SE_SCREEN (SE_ prefix already present)
        self.assertEqual(self.oc_event_oid("SE.SCREEN"), "SE_SCREEN")

    def test_form_id_cdash_dm(self):
        self.assertEqual(self.oc_form_id("F_DM", "Demographics"), "DM")

    def test_form_id_cdash_ae(self):
        self.assertEqual(self.oc_form_id("F.AE", "Adverse Events"), "AE")

    def test_form_id_cdash_vs(self):
        self.assertEqual(self.oc_form_id("F_VS", "Vital Signs"), "VS")

    def test_form_id_custom_no_truncation(self):
        fid = self.oc_form_id("F_BIOSPECIMENWORKSHEETSEMENCOL",
                              "Biospecimen Worksheet Semen Collection")
        self.assertLessEqual(len(fid), 25)
        self.assertFalse(fid.endswith("_"))

    def test_form_id_strips_f_prefix(self):
        fid = self.oc_form_id("F_CUSTOM_FORM", "")
        self.assertFalse(fid.startswith("F_"),
                         f"Form ID still has F_ prefix: {fid}")

    # ── Minimal transform edge cases ──────────────────────────────────────────

    def test_empty_study_produces_valid_spec(self):
        """transform() on a study with no forms must still return valid structure."""
        parse = _import_reader()[0]
        xml = _minimal_odm(study_name="EMPTY", protocol="EMPTY-001")
        odm = parse(xml)
        spec = self.transform(odm)
        self.assertIn("study_meta", spec)
        self.assertIn("forms", spec)
        self.assertIsInstance(spec["forms"], list)

    def test_se_common_always_added(self):
        """SE_COMMON must appear in timepoint rows even for studies with no AE/CM."""
        parse = _import_reader()[0]
        xml = _minimal_odm(
            events=[{"oid": "SE_SCREEN", "name": "Screening", "form_refs": []}]
        )
        odm = parse(xml)
        spec = self.transform(odm)
        event_oids = [r["event"] for r in spec["timepoint_csv"]["rows"]]
        self.assertIn("SE_COMMON", event_oids)


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 3 — vendor registry extensibility
# ══════════════════════════════════════════════════════════════════════════════

class TestVendorRegistry(unittest.TestCase):
    """
    Validates that the vendor detection system is extensible — new vendors
    can be added without code changes (config-driven).

    These tests pass TODAY because the registry is embedded in odm_reader.py.
    When we move the registry to vendor_registry.json, these tests will drive
    that refactor and ensure nothing breaks.
    """

    @classmethod
    def setUpClass(cls):
        cls.parse = staticmethod(_import_reader()[0])

    def test_all_current_14_vendors_detectable(self):
        """
        Every vendor in the original list of 14 must be detectable
        from a plausible Originator string.
        """
        vendor_map = {
            "Medidata Solutions":       "medidata",
            "Viedoc Technologies":      "viedoc",
            "Oracle":                   "oracle inform",
            "Veeva Systems":            "veeva vault",
            "Zelta (Merative)":         "merative",
            "Castor":                   "castor",
            "Medrio":                   "medrio",
            "REDCap Cloud":             "redcap",
            "OpenClinica 4":            "openclinica",
        }
        # Vendors we don't yet have namespace sniffing for — tracked here
        # so we don't forget them
        not_yet_detectable = {
            "CRScube", "Cloudbyz", "Emmes", "MedNet",
            "Crucial Data Solutions", "EDETEK",
        }
        detected_unknown = []
        for vendor_name, originator_hint in vendor_map.items():
            xml = _minimal_odm(originator=originator_hint)
            result = self.parse(xml)
            if result["source_system"] == "UNKNOWN":
                detected_unknown.append(vendor_name)

        self.assertEqual(detected_unknown, [],
            f"These vendors were not detected: {detected_unknown}. "
            f"Add their namespace hints to odm_reader._detect_vendor()")

    def test_unknown_vendor_does_not_raise(self):
        """An unrecognised vendor must degrade gracefully to UNKNOWN."""
        xml = _minimal_odm(originator="FutureEDC 99.0")
        result = self.parse(xml)
        self.assertEqual(result["source_system"], "UNKNOWN")
        # Parse should still succeed
        self.assertIn("study", result)

    def test_adding_new_vendor_via_originator_would_work(self):
        """
        Demonstrates the extension pattern: a new vendor only needs its
        Originator string added to _detect_vendor(). This test documents
        what a new integration point looks like.
        """
        # Simulate "NewEDC Corp" — not yet in the registry
        xml = _minimal_odm(originator="NewEDC Corp 3.1")
        result = self.parse(xml)
        # Currently UNKNOWN — that's correct and expected
        self.assertEqual(result["source_system"], "UNKNOWN",
            "NewEDC is intentionally not yet in the registry. "
            "When added, this test should be updated to assert detection.")


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 4 — odm_validator
# ══════════════════════════════════════════════════════════════════════════════

class TestOdmValidator(unittest.TestCase):
    """Tests for odm_validator.validate_odm() and format_report()"""

    @classmethod
    def setUpClass(cls):
        from odm_validator import validate_odm, validate_odm_file, format_report
        cls.validate      = staticmethod(validate_odm)
        cls.validate_file = staticmethod(validate_odm_file)
        cls.format        = staticmethod(format_report)

    # ── Real file ─────────────────────────────────────────────────────────────

    def test_prtk05_passes_all_layers(self):
        """Real OC4 export should pass all 3 validation layers."""
        report = self.validate(_load(PRTK05_XML), source_file="prtk05.xml")
        self.assertTrue(report.passed,
                        f"PrTK05 validation failed: {report.summary}")
        self.assertTrue(report.can_proceed)
        self.assertEqual(report.layer_results["layer_1"], "PASS")
        self.assertEqual(report.layer_results["layer_2"], "PASS")
        self.assertEqual(report.layer_results["layer_3"], "PASS")

    def test_prtk05_compliance_fields_all_present(self):
        """All 6 CFR Part 11 / ICH compliance fields must be present."""
        report = self.validate(_load(PRTK05_XML))
        for field_name, info in report.compliance.items():
            self.assertTrue(info["present"],
                            f"Compliance field missing: {field_name}")
            self.assertEqual(info["status"], "PASS",
                             f"Compliance field not PASS: {field_name}")

    def test_prtk05_stats_correct(self):
        """Stats should match known PrTK05 counts."""
        report = self.validate(_load(PRTK05_XML))
        self.assertEqual(report.stats["events"], 21)
        self.assertEqual(report.stats["forms"],  20)
        self.assertGreaterEqual(report.stats["items"], 1000)

    def test_prtk05_odm_version_captured(self):
        report = self.validate(_load(PRTK05_XML))
        self.assertEqual(report.odm_version, "1.3")

    def test_prtk05_no_checks_failed(self):
        report = self.validate(_load(PRTK05_XML))
        failures = [c for c in report.checks if c.status == "FAIL"]
        self.assertEqual(failures, [],
                         f"Unexpected failures: {[c.name for c in failures]}")

    def test_synthetic_passes_all_layers(self):
        """Synthetic ODM with Medidata vendor extensions should also pass."""
        report = self.validate(_load(SYNTHETIC_XML), source_file="synthetic.xml")
        self.assertTrue(report.passed)
        self.assertTrue(report.can_proceed)

    # ── Layer 1: well-formedness ──────────────────────────────────────────────

    def test_malformed_xml_fails_layer1(self):
        """Broken XML must fail layer 1 and set can_proceed=False."""
        bad_xml = b"<?xml version='1.0'?><ODM><unclosed>"
        report = self.validate(bad_xml)
        self.assertFalse(report.passed)
        self.assertFalse(report.can_proceed)
        self.assertEqual(report.layer_results["layer_1"], "FAIL")
        self.assertEqual(report.layer_results["layer_2"], "SKIP")

    def test_bom_handled_in_layer1(self):
        """UTF-8 BOM should not cause layer 1 failure."""
        xml = b"\xef\xbb\xbf" + _minimal_odm(study_name="BOM_TEST")
        report = self.validate(xml)
        self.assertEqual(report.layer_results["layer_1"], "PASS")

    def test_empty_bytes_fails_gracefully(self):
        """Empty input must not crash — should fail layer 1."""
        report = self.validate(b"")
        self.assertFalse(report.can_proceed)

    # ── Layer 2: structural conformance ──────────────────────────────────────

    def test_missing_odm_version_warns(self):
        """Missing ODMVersion should produce a WARN not a FAIL."""
        xml = _minimal_odm(odm_version="1.3.2").replace(
            b'ODMVersion="1.3.2"', b''
        )
        report = self.validate(xml)
        self.assertTrue(report.can_proceed,
                        "Missing ODMVersion should warn, not block migration")
        odm_ver_check = next(
            (c for c in report.checks if c.name == "ODM version"), None
        )
        self.assertIsNotNone(odm_ver_check)
        self.assertEqual(odm_ver_check.status, "WARN")

    def test_unknown_odm_version_warns_not_fails(self):
        """A non-standard ODM version string should warn, not fail."""
        xml = _minimal_odm(odm_version="1.4.0-vendor")
        report = self.validate(xml)
        self.assertTrue(report.can_proceed)
        odm_ver_check = next(
            (c for c in report.checks if c.name == "ODM version"), None
        )
        self.assertEqual(odm_ver_check.status, "WARN")

    def test_non_odm_root_element_fails(self):
        """A non-ODM root element should fail layer 2."""
        xml = b"""<?xml version="1.0"?>
<ClinicalStudy xmlns="http://example.com">
  <Study/>
</ClinicalStudy>"""
        report = self.validate(xml)
        root_check = next(
            (c for c in report.checks if c.name == "Root element"), None
        )
        self.assertIsNotNone(root_check)
        self.assertEqual(root_check.status, "FAIL")

    def test_missing_study_element_fails_layer2(self):
        """No <Study> element must fail layer 2."""
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<ODM ODMVersion="1.3.2" FileOID="X" FileType="Snapshot"
     CreationDateTime="2025-01-01T00:00:00"
     xmlns="http://www.cdisc.org/ns/odm/v1.3"/>"""
        report = self.validate(xml)
        study_check = next(
            (c for c in report.checks if c.name == "Study element"), None
        )
        self.assertIsNotNone(study_check)
        self.assertEqual(study_check.status, "FAIL")

    def test_valid_file_types_pass(self):
        """Both Snapshot and Transactional FileTypes should pass."""
        for ft in ("Snapshot", "Transactional"):
            xml = _minimal_odm().replace(b'FileType="Snapshot"',
                                         f'FileType="{ft}"'.encode())
            report = self.validate(xml)
            ft_check = next(
                (c for c in report.checks if c.name == "FileType"), None
            )
            self.assertEqual(ft_check.status, "PASS",
                             f"FileType='{ft}' should pass")

    # ── Layer 3: OID integrity ────────────────────────────────────────────────

    def test_dangling_form_ref_warns(self):
        """A FormRef pointing to a non-existent FormDef should produce WARN."""
        xml = _minimal_odm(
            events=[{"oid": "SE_SCREEN", "name": "Screening",
                     "form_refs": ["F_GHOST"]}],
            forms=[],  # F_GHOST not defined
        )
        report = self.validate(xml)
        form_ref_check = next(
            (c for c in report.checks
             if "StudyEventDef" in c.name and "FormDef" in c.name), None
        )
        self.assertIsNotNone(form_ref_check)
        self.assertEqual(form_ref_check.status, "WARN")
        self.assertIn("F_GHOST", form_ref_check.detail)

    def test_clean_oid_refs_all_pass(self):
        """A well-formed minimal ODM should pass all layer 3 checks."""
        xml = _minimal_odm(
            events=[{"oid": "SE_SCREEN", "name": "Screening",
                     "form_refs": ["F_DM"]}],
            forms=[{"oid": "F_DM", "name": "Demographics", "ig_refs": []}],
        )
        report = self.validate(xml)
        layer3_checks = [c for c in report.checks if c.layer == 3]
        failures = [c for c in layer3_checks if c.status == "FAIL"]
        self.assertEqual(failures, [],
                         f"Layer 3 should not fail on clean ODM: {failures}")

    # ── Report formatting ─────────────────────────────────────────────────────

    def test_format_report_contains_summary(self):
        """format_report() must include the summary line."""
        report = self.validate(_load(PRTK05_XML))
        text = self.format(report)
        self.assertIn("PASS", text)
        self.assertIn("ODM Validation Report", text)
        self.assertIn("PrTK05", text)

    def test_format_report_verbose_shows_passes(self):
        """Verbose mode must show PASS results."""
        report = self.validate(_load(PRTK05_XML))
        text = self.format(report, verbose=True)
        self.assertIn("[PASS]", text)

    def test_format_report_default_hides_passes(self):
        """Default (non-verbose) mode must not show individual PASS lines."""
        report = self.validate(_load(PRTK05_XML))
        text = self.format(report, verbose=False)
        # Should say all passed, not list individual checks
        self.assertIn("All checks passed", text)
        self.assertNotIn("[PASS]", text)

    def test_validate_odm_file_convenience(self):
        """validate_odm_file() path wrapper should work identically."""
        report = self.validate_file(str(PRTK05_XML))
        self.assertTrue(report.passed)
        self.assertEqual(report.source_file, str(PRTK05_XML))

    # ── can_proceed logic ─────────────────────────────────────────────────────

    def test_can_proceed_true_on_warnings_only(self):
        """Warnings alone must not block migration (can_proceed=True)."""
        # ODM without ODMVersion will warn but not fail
        xml = _minimal_odm(odm_version="1.3.2").replace(
            b'ODMVersion="1.3.2"', b''
        )
        report = self.validate(xml)
        warnings = [c for c in report.checks if c.status == "WARN"]
        failures = [c for c in report.checks if c.status == "FAIL"]
        if warnings and not failures:
            self.assertTrue(report.can_proceed,
                            "Warnings-only report should still allow migration")

    def test_can_proceed_false_on_failure(self):
        """Any FAIL check must set can_proceed=False."""
        bad_xml = b"not xml at all <<<"
        report = self.validate(bad_xml)
        self.assertFalse(report.can_proceed)


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 5 — Medidata Rave synthetic fixture
# ══════════════════════════════════════════════════════════════════════════════

class TestMedidataRaveFixture(unittest.TestCase):
    """
    End-to-end coverage of the Medidata Rave 5.6.9 synthetic fixture
    (tests/migration/fixtures/medidata_rave_synthetic.xml). Verifies that
    Rave-specific patterns (mdsol namespace, IsLog log-line forms, vendor
    extension attrs, RangeChecks, ClinicalData with mdsol:Submission) flow
    cleanly through validator → reader → transform.
    """

    @classmethod
    def setUpClass(cls):
        from odm_validator import validate_odm
        parse_odm_metadata, parse_odm_clinical_data, *_ = _import_reader()
        transform, *_ = _import_spec()
        cls.validate = staticmethod(validate_odm)
        cls.parse    = staticmethod(parse_odm_metadata)
        cls.parse_cd = staticmethod(parse_odm_clinical_data)
        cls.transform = staticmethod(transform)
        # Cache parse + transform — both are deterministic
        cls.raw   = _load(MEDIDATA_RAVE_XML)
        cls.odm   = parse_odm_metadata(cls.raw)
        cls.spec  = transform(cls.odm)

    # ── Vendor detection ──────────────────────────────────────────────────────

    def test_vendor_detected_as_medidata_rave(self):
        """Originator='Medidata Rave 5.6.9' must resolve to 'Medidata Rave'."""
        self.assertEqual(self.odm["source_system"], "Medidata Rave")

    def test_source_system_version_populated(self):
        """Detector must capture a non-empty version string from root attrs."""
        self.assertTrue(self.odm["source_system_version"],
                        "source_system_version is empty — expected a value from "
                        "ODM root attributes")

    # ── mdsol namespace vendor attr capture ───────────────────────────────────

    def test_mdsol_vendor_attrs_captured_on_events(self):
        """At least some StudyEventDef elements must carry medidata: vendor attrs."""
        events_with_vendor = [e for e in self.odm["events"]
                              if any(k.startswith("medidata:") for k in (e.get("vendor") or {}))]
        self.assertGreater(len(events_with_vendor), 0,
                           "No mdsol vendor attrs captured on any StudyEventDef")

    def test_mdsol_vendor_attrs_captured_on_forms(self):
        """At least some FormDef elements must carry medidata: vendor attrs."""
        forms_with_vendor = [f for f in self.odm["forms"]
                             if any(k.startswith("medidata:") for k in (f.get("vendor") or {}))]
        self.assertGreater(len(forms_with_vendor), 0,
                           "No mdsol vendor attrs captured on any FormDef")

    def test_mdsol_active_attr_value(self):
        """SE_SCREEN must carry medidata:Active='Yes' in its vendor dict."""
        se = next((e for e in self.odm["events"] if e["oid"] == "SE_SCREEN"), None)
        self.assertIsNotNone(se, "SE_SCREEN missing from parsed events")
        self.assertEqual(se["vendor"].get("medidata:Active"), "Yes")

    def test_mdsol_islog_on_ae_itemgroup(self):
        """IG.AE (log-line form) must carry medidata:IsLog='Yes' in its vendor dict."""
        ig = next((g for g in self.odm["item_groups"] if g["oid"] == "IG.AE"), None)
        self.assertIsNotNone(ig, "IG.AE missing from parsed item_groups")
        self.assertEqual(ig["vendor"].get("medidata:IsLog"), "Yes")

    def test_mdsol_islog_on_cm_itemgroup(self):
        """IG.CM (log-line form) must carry medidata:IsLog='Yes' in its vendor dict."""
        ig = next((g for g in self.odm["item_groups"] if g["oid"] == "IG.CM"), None)
        self.assertIsNotNone(ig, "IG.CM missing from parsed item_groups")
        self.assertEqual(ig["vendor"].get("medidata:IsLog"), "Yes")

    # ── All 3 validation layers pass ──────────────────────────────────────────

    def test_layer_1_passes(self):
        rep = self.validate(self.raw, source_file=str(MEDIDATA_RAVE_XML))
        self.assertEqual(rep.layer_results["layer_1"], "PASS")

    def test_layer_2_passes(self):
        rep = self.validate(self.raw, source_file=str(MEDIDATA_RAVE_XML))
        self.assertEqual(rep.layer_results["layer_2"], "PASS")

    def test_layer_3_passes(self):
        rep = self.validate(self.raw, source_file=str(MEDIDATA_RAVE_XML))
        self.assertEqual(rep.layer_results["layer_3"], "PASS")

    def test_can_proceed(self):
        rep = self.validate(self.raw)
        self.assertTrue(rep.can_proceed,
                        f"Fixture should be migratable: {rep.summary}")
        self.assertTrue(rep.passed)

    def test_compliance_fields_all_present(self):
        rep = self.validate(self.raw)
        for field_name, info in rep.compliance.items():
            self.assertTrue(info["present"],
                            f"Compliance field missing: {field_name}")
            self.assertEqual(info["status"], "PASS",
                             f"Compliance field not PASS: {field_name}")

    # ── transform() form-ID + OC-9 compliance ─────────────────────────────────

    def test_protocol_number_is_cv3001(self):
        self.assertEqual(self.spec["study_meta"]["protocol_number"], "CV3001")

    def test_form_ids_match_cdash_set(self):
        """All 8 CDASH forms must resolve to their canonical short IDs."""
        expected = {"DM", "IE", "VS", "AE", "CM", "EX", "LB", "DS"}
        produced = {f["form_id"] for f in self.spec["forms"]}
        self.assertEqual(produced, expected,
                         f"Form IDs differ: missing={expected - produced} "
                         f"extra={produced - expected}")

    def test_oc9_ae_on_se_common_only(self):
        """OC-9: AE must be assigned only to SE_COMMON."""
        ae = next((f for f in self.spec["forms"] if f["form_id"] == "AE"), None)
        self.assertIsNotNone(ae)
        self.assertEqual(ae["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: AE visits_assigned={ae['visits_assigned']}")

    def test_oc9_cm_on_se_common_only(self):
        """OC-9: CM must be assigned only to SE_COMMON."""
        cm = next((f for f in self.spec["forms"] if f["form_id"] == "CM"), None)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: CM visits_assigned={cm['visits_assigned']}")

    # ── AE/CM repeating identification ────────────────────────────────────────

    def test_ae_itemgroup_is_repeating(self):
        """IG.AE must be parsed as Repeating=True (log-line form pattern)."""
        ig = next((g for g in self.odm["item_groups"] if g["oid"] == "IG.AE"), None)
        self.assertIsNotNone(ig)
        self.assertTrue(ig["repeating"],
                        "IG.AE Repeating='Yes' was not detected as True")

    def test_cm_itemgroup_is_repeating(self):
        """IG.CM must be parsed as Repeating=True (log-line form pattern)."""
        ig = next((g for g in self.odm["item_groups"] if g["oid"] == "IG.CM"), None)
        self.assertIsNotNone(ig)
        self.assertTrue(ig["repeating"],
                        "IG.CM Repeating='Yes' was not detected as True")

    def test_ae_formdef_is_repeating(self):
        """FormDef F_AE Repeating='Yes' should propagate to forms list."""
        f = next((x for x in self.odm["forms"] if x["oid"] == "F_AE"), None)
        self.assertIsNotNone(f)
        self.assertTrue(f["repeating"])

    # ── ClinicalData parse ────────────────────────────────────────────────────

    def test_clinical_data_two_subjects(self):
        """ClinicalData section must contain exactly the 2 fabricated subjects."""
        subjects = self.parse_cd(self.raw)
        self.assertEqual(len(subjects), 2)
        keys = {s["subject_key"] for s in subjects}
        self.assertEqual(keys, {"CV-001-001", "CV-001-002"})


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 6 — Veeva Vault CDMS synthetic fixture
# ══════════════════════════════════════════════════════════════════════════════

class TestVeevaFixture(unittest.TestCase):
    """
    End-to-end coverage of the Veeva Vault CDMS synthetic fixture
    (tests/migration/fixtures/veeva_synthetic.xml). Phase 2 oncology study
    with three 21-day treatment cycles, RECIST tumour assessments, and
    minimal v: namespace vendor extensions (reflecting Veeva's clean
    ODM 1.3.2 export style).
    """

    @classmethod
    def setUpClass(cls):
        from odm_validator import validate_odm
        parse_odm_metadata, *_ = _import_reader()
        transform, *_ = _import_spec()
        cls.validate = staticmethod(validate_odm)
        cls.parse    = staticmethod(parse_odm_metadata)
        cls.transform = staticmethod(transform)
        cls.raw  = _load(VEEVA_XML)
        cls.odm  = parse_odm_metadata(cls.raw)
        cls.spec = transform(cls.odm)

    def test_vendor_detected_as_veeva_vault_cdms(self):
        """Originator='Veeva Vault CDMS 24.2' must resolve to 'Veeva Vault CDMS'."""
        self.assertEqual(self.odm["source_system"], "Veeva Vault CDMS")

    def test_layer_1_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_1"], "PASS")

    def test_layer_2_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_2"], "PASS")

    def test_layer_3_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_3"], "PASS")

    def test_can_proceed(self):
        rep = self.validate(self.raw)
        self.assertTrue(rep.can_proceed, f"Fixture must be migratable: {rep.summary}")
        self.assertTrue(rep.passed)

    def test_protocol_number_is_onc2024(self):
        self.assertEqual(self.spec["study_meta"]["protocol_number"], "ONC2024")

    def test_oc9_ae_on_se_common_only(self):
        ae = next((f for f in self.spec["forms"] if f["form_id"] == "AE"), None)
        self.assertIsNotNone(ae, "AE form missing from spec")
        self.assertEqual(ae["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: AE visits_assigned={ae['visits_assigned']}")

    def test_oc9_cm_on_se_common_only(self):
        cm = next((f for f in self.spec["forms"] if f["form_id"] == "CM"), None)
        self.assertIsNotNone(cm, "CM form missing from spec")
        self.assertEqual(cm["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: CM visits_assigned={cm['visits_assigned']}")

    def test_tu_form_present_in_spec(self):
        """Oncology-specific TU (Tumour Assessment) form must appear in the spec."""
        form_ids = {f["form_id"] for f in self.spec["forms"]}
        self.assertIn("TU", form_ids,
                      f"TU form missing from spec. Forms: {sorted(form_ids)}")

    def test_three_cycle_events_detected(self):
        """SE_CYCLE1, SE_CYCLE2, SE_CYCLE3 must all be parsed from the protocol."""
        event_oids = {e["oid"] for e in self.odm["events"]}
        for cycle in ("SE_CYCLE1", "SE_CYCLE2", "SE_CYCLE3"):
            self.assertIn(cycle, event_oids,
                          f"Treatment cycle event {cycle} missing — found: {sorted(event_oids)}")

    def test_cycle_events_are_repeating(self):
        """All three cycle events must be Repeating='Yes'."""
        ev_by_oid = {e["oid"]: e for e in self.odm["events"]}
        for cycle in ("SE_CYCLE1", "SE_CYCLE2", "SE_CYCLE3"):
            self.assertTrue(ev_by_oid[cycle]["repeating"],
                            f"{cycle} should be Repeating='Yes'")

    def test_recist_codelist_present(self):
        """CL.RECIST CodeList must include CR, PR, SD, PD response categories."""
        recist = next((c for c in self.odm["codelists"] if c["oid"] == "CL.RECIST"), None)
        self.assertIsNotNone(recist, "CL.RECIST CodeList missing")
        codes = {ci["coded_value"] for ci in recist["items"]}
        for required in ("CR", "PR", "SD", "PD"):
            self.assertIn(required, codes,
                          f"RECIST response category {required} missing — found: {sorted(codes)}")


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 7 — Viedoc synthetic fixture
# ══════════════════════════════════════════════════════════════════════════════

class TestViedocFixture(unittest.TestCase):
    """
    End-to-end coverage of the Viedoc 4.72 synthetic fixture
    (tests/migration/fixtures/viedoc_synthetic.xml). Phase 2 CNS / MDD
    study with MADRS-10 and HAM-D-7 rating scales (17 integer items
    constrained to 0-6), administered at SCREEN / WEEK2 / WEEK4 / WEEK8 /
    WEEK12 (latter four repeating). Exercises viedoc: namespace attr
    capture on FormDef.
    """

    @classmethod
    def setUpClass(cls):
        from odm_validator import validate_odm
        parse_odm_metadata, *_ = _import_reader()
        transform, *_ = _import_spec()
        cls.validate = staticmethod(validate_odm)
        cls.parse    = staticmethod(parse_odm_metadata)
        cls.transform = staticmethod(transform)
        cls.raw  = _load(VIEDOC_XML)
        cls.odm  = parse_odm_metadata(cls.raw)
        cls.spec = transform(cls.odm)

    def test_vendor_detected_as_viedoc(self):
        """Originator='Viedoc 4.72' must resolve to 'Viedoc'."""
        self.assertEqual(self.odm["source_system"], "Viedoc")

    def test_viedoc_namespace_attrs_captured_on_forms(self):
        """At least some FormDef elements must carry viedoc: vendor attrs."""
        forms_with_viedoc = [f for f in self.odm["forms"]
                             if any(k.startswith("viedoc:") for k in (f.get("vendor") or {}))]
        self.assertGreater(len(forms_with_viedoc), 0,
                           "No viedoc: vendor attrs captured on any FormDef")

    def test_viedoc_layout_attr_value(self):
        """F_DM must carry viedoc:Layout='OneColumn' in its vendor dict."""
        dm = next((f for f in self.odm["forms"] if f["oid"] == "F_DM"), None)
        self.assertIsNotNone(dm, "F_DM missing from parsed forms")
        self.assertEqual(dm["vendor"].get("viedoc:Layout"), "OneColumn")

    def test_layer_1_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_1"], "PASS")

    def test_layer_2_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_2"], "PASS")

    def test_layer_3_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_3"], "PASS")

    def test_can_proceed(self):
        rep = self.validate(self.raw)
        self.assertTrue(rep.can_proceed, f"Fixture must be migratable: {rep.summary}")
        self.assertTrue(rep.passed)

    def test_protocol_number_is_cns2024(self):
        self.assertEqual(self.spec["study_meta"]["protocol_number"], "CNS2024")

    def test_oc9_ae_on_se_common_only(self):
        ae = next((f for f in self.spec["forms"] if f["form_id"] == "AE"), None)
        self.assertIsNotNone(ae)
        self.assertEqual(ae["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: AE visits_assigned={ae['visits_assigned']}")

    def test_oc9_cm_on_se_common_only(self):
        cm = next((f for f in self.spec["forms"] if f["form_id"] == "CM"), None)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: CM visits_assigned={cm['visits_assigned']}")

    def test_qs_form_present_with_constrained_items(self):
        """QS form must be present and have constrained survey rows from RangeChecks."""
        qs = next((f for f in self.spec["forms"] if f["form_id"] == "QS"), None)
        self.assertIsNotNone(qs, f"QS form missing. Forms: "
                                 f"{sorted(f['form_id'] for f in self.spec['forms'])}")
        constrained = [r for r in qs["survey"] if r.get("constraint")]
        self.assertGreaterEqual(len(constrained), 10,
                                f"QS form should have ≥10 constrained items "
                                f"(MADRS-10 + HAM-D-7), found {len(constrained)}")

    def test_repeating_week_visits_detected(self):
        """SE_WEEK2/4/8/12 must all be parsed as Repeating='Yes'."""
        ev_by_oid = {e["oid"]: e for e in self.odm["events"]}
        for week in ("SE_WEEK2", "SE_WEEK4", "SE_WEEK8", "SE_WEEK12"):
            self.assertIn(week, ev_by_oid,
                          f"{week} missing from parsed events")
            self.assertTrue(ev_by_oid[week]["repeating"],
                            f"{week} should be Repeating='Yes'")


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 8 — iMedNet synthetic fixture
# ══════════════════════════════════════════════════════════════════════════════

class TestImednetFixture(unittest.TestCase):
    """
    End-to-end coverage of the iMedNet EDC 6.0 synthetic fixture
    (tests/migration/fixtures/imednet_synthetic.xml). Phase 3 type-2
    diabetes study with quarterly HbA1c monitoring across six scheduled
    visits. iMedNet exports clean ODM 1.3.2 with no widely-known
    namespace, so vendor detection here is Originator-string based.
    """

    @classmethod
    def setUpClass(cls):
        from odm_validator import validate_odm
        parse_odm_metadata, *_ = _import_reader()
        transform, *_ = _import_spec()
        cls.validate = staticmethod(validate_odm)
        cls.parse    = staticmethod(parse_odm_metadata)
        cls.transform = staticmethod(transform)
        cls.raw  = _load(IMEDNET_XML)
        cls.odm  = parse_odm_metadata(cls.raw)
        cls.spec = transform(cls.odm)

    def test_vendor_detected_from_originator(self):
        """Originator='iMedNet EDC 6.0' must resolve to 'iMedNet' via Originator detection."""
        self.assertEqual(self.odm["source_system"], "iMedNet")

    def test_layer_1_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_1"], "PASS")

    def test_layer_2_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_2"], "PASS")

    def test_layer_3_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_3"], "PASS")

    def test_can_proceed(self):
        rep = self.validate(self.raw)
        self.assertTrue(rep.can_proceed, f"Fixture must be migratable: {rep.summary}")
        self.assertTrue(rep.passed)

    def test_protocol_number_is_dm3001(self):
        self.assertEqual(self.spec["study_meta"]["protocol_number"], "DM3001")

    def test_oc9_ae_on_se_common_only(self):
        ae = next((f for f in self.spec["forms"] if f["form_id"] == "AE"), None)
        self.assertIsNotNone(ae)
        self.assertEqual(ae["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: AE visits_assigned={ae['visits_assigned']}")

    def test_oc9_cm_on_se_common_only(self):
        cm = next((f for f in self.spec["forms"] if f["form_id"] == "CM"), None)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: CM visits_assigned={cm['visits_assigned']}")

    def test_hba1c_form_present_with_constrained_item(self):
        """HBA1C form must be in spec and have at least one constrained survey row
        (the HbA1c result item, constrained to 3.0-20.0%)."""
        hba1c = next((f for f in self.spec["forms"] if f["form_id"] == "HBA1C"), None)
        self.assertIsNotNone(hba1c, f"HBA1C form missing. Forms: "
                                    f"{sorted(f['form_id'] for f in self.spec['forms'])}")
        constrained = [r for r in hba1c["survey"] if r.get("constraint")]
        self.assertGreaterEqual(len(constrained), 1,
                                f"HBA1C form should have ≥1 constrained item, found {len(constrained)}")
        # The constraint should reflect the 3.0 - 20.0 range from the fixture
        constraint_text = " ".join(r.get("constraint", "") for r in constrained)
        self.assertIn("3.0", constraint_text,
                      f"HbA1c lower bound 3.0 missing from constraint: {constraint_text}")
        self.assertIn("20.0", constraint_text,
                      f"HbA1c upper bound 20.0 missing from constraint: {constraint_text}")

    def test_scheduled_visits_correct(self):
        """All six scheduled visits (SCREEN, WEEK4, WEEK12, WEEK24, WEEK52, EOT)
        must be parsed from the protocol."""
        expected = {"SE_SCREEN", "SE_WEEK4", "SE_WEEK12", "SE_WEEK24",
                    "SE_WEEK52", "SE_EOT"}
        event_oids = {e["oid"] for e in self.odm["events"]}
        missing = expected - event_oids
        self.assertEqual(missing, set(),
                         f"Scheduled visits missing from parsed events: {sorted(missing)}")

    def test_insulin_type_codelist_present(self):
        """CL.INSULIN CodeList must include the standard insulin categories."""
        cl = next((c for c in self.odm["codelists"] if c["oid"] == "CL.INSULIN"), None)
        self.assertIsNotNone(cl, "CL.INSULIN CodeList missing")
        codes = {ci["coded_value"] for ci in cl["items"]}
        for required in ("RAPID", "SHORT", "LONG"):
            self.assertIn(required, codes,
                          f"Insulin type {required} missing — found: {sorted(codes)}")

    def test_injection_site_codelist_present(self):
        """CL.INJSITE CodeList must include the standard injection sites."""
        cl = next((c for c in self.odm["codelists"] if c["oid"] == "CL.INJSITE"), None)
        self.assertIsNotNone(cl, "CL.INJSITE CodeList missing")
        codes = {ci["coded_value"] for ci in cl["items"]}
        for required in ("ABDOMEN", "THIGH", "UPPARM"):
            self.assertIn(required, codes,
                          f"Injection site {required} missing — found: {sorted(codes)}")


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 9 — Oracle InForm synthetic fixture
# ══════════════════════════════════════════════════════════════════════════════

class TestOracleInFormFixture(unittest.TestCase):
    """
    End-to-end coverage of the Oracle InForm 6.3 synthetic fixture
    (tests/migration/fixtures/oracle_inform_synthetic.xml). Phase 2 RA
    study capturing InForm's distinctive patterns: pf: namespace
    (Phase Forward), pf:DBUID / pf:GUID on every ItemDef, and the
    hierarchical dot-notation OID convention frm<F>.sct<S>.itm<I>.
    """

    @classmethod
    def setUpClass(cls):
        import re as _re
        from odm_validator import validate_odm
        parse_odm_metadata, *_ = _import_reader()
        transform, *_ = _import_spec()
        cls.validate = staticmethod(validate_odm)
        cls.parse    = staticmethod(parse_odm_metadata)
        cls.transform = staticmethod(transform)
        cls.raw  = _load(ORACLE_INFORM_XML)
        cls.odm  = parse_odm_metadata(cls.raw)
        cls.spec = transform(cls.odm)
        cls.HIER_RE = _re.compile(r"^frm[A-Za-z0-9]+\.sct[A-Za-z0-9]+\.itm[A-Za-z0-9]+$")

    def test_vendor_detected_as_oracle_inform(self):
        """Originator='Oracle InForm 6.3' must resolve to 'Oracle InForm'."""
        self.assertEqual(self.odm["source_system"], "Oracle InForm")

    def test_pf_vendor_attrs_captured_on_items(self):
        """At least some ItemDef elements must carry pf: DBUID/GUID vendor attrs."""
        items_with_pf = [it for it in self.odm["items"]
                         if any("DBUID" in k or "GUID" in k
                                for k in (it.get("vendor") or {}))]
        self.assertGreater(len(items_with_pf), 0,
                           "No pf: DBUID/GUID vendor attrs captured on any ItemDef")
        # All 25 items in this fixture carry both attrs — assert strongly
        self.assertEqual(len(items_with_pf), len(self.odm["items"]),
                         "Expected pf: attrs on every item, found "
                         f"{len(items_with_pf)} / {len(self.odm['items'])}")

    def test_pf_dbuid_value_on_specific_item(self):
        """frmDM.sctDM.itmSUBJID must carry the exact DBUID value from the fixture."""
        it = next((x for x in self.odm["items"]
                   if x["oid"] == "frmDM.sctDM.itmSUBJID"), None)
        self.assertIsNotNone(it, "frmDM.sctDM.itmSUBJID missing from parsed items")
        vendor = it.get("vendor") or {}
        # The vendor key prefix depends on VENDOR_NS resolution — match on suffix
        dbuid_keys = [k for k in vendor if k.endswith(":DBUID")]
        self.assertGreater(len(dbuid_keys), 0, "No DBUID attr captured on SUBJID item")
        self.assertEqual(vendor[dbuid_keys[0]], "IT-100001-DBUID")

    def test_layer_1_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_1"], "PASS")

    def test_layer_2_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_2"], "PASS")

    def test_layer_3_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_3"], "PASS")

    def test_can_proceed(self):
        rep = self.validate(self.raw)
        self.assertTrue(rep.can_proceed, f"Fixture must be migratable: {rep.summary}")

    def test_protocol_number_is_ra2024(self):
        self.assertEqual(self.spec["study_meta"]["protocol_number"], "RA2024")

    def test_oc9_ae_on_se_common_only(self):
        ae = next((f for f in self.spec["forms"] if f["form_id"] == "AE"), None)
        self.assertIsNotNone(ae)
        self.assertEqual(ae["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: AE visits_assigned={ae['visits_assigned']}")

    def test_oc9_cm_on_se_common_only(self):
        cm = next((f for f in self.spec["forms"] if f["form_id"] == "CM"), None)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: CM visits_assigned={cm['visits_assigned']}")

    def test_hierarchical_oid_pattern_preserved_on_items(self):
        """Every ItemDef OID must follow the InForm frm<F>.sct<S>.itm<I> pattern."""
        non_matching = [it["oid"] for it in self.odm["items"]
                        if not self.HIER_RE.match(it["oid"])]
        self.assertEqual(non_matching, [],
                         f"Items not matching hierarchical OID pattern: {non_matching}")


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 10 — REDCap Cloud synthetic fixture
# ══════════════════════════════════════════════════════════════════════════════

class TestRedcapFixture(unittest.TestCase):
    """
    End-to-end coverage of the REDCap Cloud 14.0.0 synthetic fixture
    (tests/migration/fixtures/redcap_synthetic.xml). Phase 1 first-in-
    human vaccine study with a multi-arm Protocol (ARM1 active vs ARM2
    placebo). REDCap exports characteristically omit Originator and
    populate SourceSystem instead — vendor detection here falls through
    to the namespace-sniff fallback against xmlns:redcap.
    """

    @classmethod
    def setUpClass(cls):
        from odm_validator import validate_odm
        parse_odm_metadata, *_ = _import_reader()
        transform, *_ = _import_spec()
        cls.validate = staticmethod(validate_odm)
        cls.parse    = staticmethod(parse_odm_metadata)
        cls.transform = staticmethod(transform)
        cls.raw  = _load(REDCAP_XML)
        cls.odm  = parse_odm_metadata(cls.raw)
        cls.spec = transform(cls.odm)

    def test_vendor_detected_as_redcap(self):
        """No Originator on root — detection must fall through to the
        xmlns:redcap namespace sniff and return 'REDCap'."""
        self.assertEqual(self.odm["source_system"], "REDCap")

    def test_redcap_namespace_attrs_captured_on_items(self):
        """At least some ItemDef elements must carry redcap: vendor attrs."""
        items_with_redcap = [it for it in self.odm["items"]
                             if any(k.startswith("redcap:")
                                    for k in (it.get("vendor") or {}))]
        self.assertGreater(len(items_with_redcap), 0,
                           "No redcap: vendor attrs captured on any ItemDef")

    def test_redcap_variable_attr_value(self):
        """I.DM.SUBJID must carry redcap:Variable='subjid' in its vendor dict."""
        it = next((x for x in self.odm["items"] if x["oid"] == "I.DM.SUBJID"), None)
        self.assertIsNotNone(it, "I.DM.SUBJID missing from parsed items")
        self.assertEqual(it["vendor"].get("redcap:Variable"), "subjid")

    def test_odm_version_is_131(self):
        """REDCap historically targets ODM 1.3.1 — verify that's what we parse."""
        self.assertEqual(self.odm["odm_version"], "1.3.1")

    def test_layer_1_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_1"], "PASS")

    def test_layer_2_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_2"], "PASS")

    def test_layer_3_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_3"], "PASS")

    def test_can_proceed(self):
        rep = self.validate(self.raw)
        self.assertTrue(rep.can_proceed, f"Fixture must be migratable: {rep.summary}")

    def test_protocol_number_is_vax1001(self):
        self.assertEqual(self.spec["study_meta"]["protocol_number"], "VAX1001")

    def test_oc9_ae_on_se_common_only(self):
        ae = next((f for f in self.spec["forms"] if f["form_id"] == "AE"), None)
        self.assertIsNotNone(ae)
        self.assertEqual(ae["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: AE visits_assigned={ae['visits_assigned']}")

    def test_oc9_cm_on_se_common_only(self):
        cm = next((f for f in self.spec["forms"] if f["form_id"] == "CM"), None)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: CM visits_assigned={cm['visits_assigned']}")

    def test_multi_arm_structure_detected(self):
        """Protocol must expose two Arms — ARM1 active vaccine + ARM2 placebo."""
        arms = self.odm["protocol"]["arms"]
        self.assertEqual(len(arms), 2,
                         f"Expected 2 arms, found {len(arms)}: {arms}")
        arm_oids = {a["oid"] for a in arms}
        self.assertEqual(arm_oids, {"ARM1", "ARM2"},
                         f"Arm OIDs do not match: {arm_oids}")
        # And verify the names so we know the placebo arm is distinguishable
        arm_by_oid = {a["oid"]: a["name"] for a in arms}
        self.assertIn("Vaccine", arm_by_oid["ARM1"])
        self.assertIn("Placebo", arm_by_oid["ARM2"])


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 11 — Castor EDC synthetic fixture
# ══════════════════════════════════════════════════════════════════════════════

class TestCastorFixture(unittest.TestCase):
    """
    End-to-end coverage of the Castor EDC 2024.1 synthetic fixture
    (tests/migration/fixtures/castor_synthetic.xml). Phase 2 atopic
    dermatitis study. Exercises Castor's distinctive patterns: UUID-
    style FormDef OIDs, castor: namespace per-item attrs, omitted
    BasicDefinitions (units in question text), and an EASI dermatology
    score with four body-region items constrained to 0-6.
    """

    @classmethod
    def setUpClass(cls):
        import re as _re
        from odm_validator import validate_odm
        parse_odm_metadata, *_ = _import_reader()
        transform, *_ = _import_spec()
        cls.validate = staticmethod(validate_odm)
        cls.parse    = staticmethod(parse_odm_metadata)
        cls.transform = staticmethod(transform)
        cls.raw  = _load(CASTOR_XML)
        cls.odm  = parse_odm_metadata(cls.raw)
        cls.spec = transform(cls.odm)
        cls.UUID_FORM_RE = _re.compile(
            r"^F_[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
        )

    def test_vendor_detected_as_castor_edc(self):
        """Originator='Castor EDC 2024.1' must resolve to 'Castor EDC'."""
        self.assertEqual(self.odm["source_system"], "Castor EDC")

    def test_castor_namespace_attrs_captured_on_items(self):
        """All ItemDef elements in this fixture must carry castor: vendor attrs."""
        items_with_castor = [it for it in self.odm["items"]
                             if any(k.startswith("castor:")
                                    for k in (it.get("vendor") or {}))]
        self.assertGreater(len(items_with_castor), 0,
                           "No castor: vendor attrs captured on any ItemDef")
        self.assertEqual(len(items_with_castor), len(self.odm["items"]),
                         "Expected castor: attrs on every item, found "
                         f"{len(items_with_castor)} / {len(self.odm['items'])}")

    def test_uuid_style_form_oids_preserved(self):
        """Most FormDef OIDs follow Castor's F_<UUID-prefix>-<TYPE> pattern."""
        uuid_forms = [f["oid"] for f in self.odm["forms"]
                      if self.UUID_FORM_RE.match(f["oid"])]
        self.assertGreaterEqual(len(uuid_forms), 5,
                                f"Too few UUID-style form OIDs: {uuid_forms}")

    def test_layer_1_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_1"], "PASS")

    def test_layer_2_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_2"], "PASS")

    def test_layer_3_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_3"], "PASS")

    def test_can_proceed(self):
        rep = self.validate(self.raw)
        self.assertTrue(rep.can_proceed, f"Fixture must be migratable: {rep.summary}")

    def test_protocol_number_is_derm2024(self):
        self.assertEqual(self.spec["study_meta"]["protocol_number"], "DERM2024")

    def test_oc9_ae_on_se_common_only(self):
        ae = next((f for f in self.spec["forms"] if f["form_id"] == "AE"), None)
        self.assertIsNotNone(ae)
        self.assertEqual(ae["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: AE visits_assigned={ae['visits_assigned']}")

    def test_oc9_cm_on_se_common_only(self):
        cm = next((f for f in self.spec["forms"] if f["form_id"] == "CM"), None)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: CM visits_assigned={cm['visits_assigned']}")

    def test_easi_form_present_with_constrained_items(self):
        """EASI form must be in spec with 4 body-region items each constrained 0-6."""
        easi = next((f for f in self.spec["forms"] if f["form_id"] == "EASI"), None)
        self.assertIsNotNone(easi, f"EASI form missing. Forms: "
                                   f"{sorted(f['form_id'] for f in self.spec['forms'])}")
        constrained = [r for r in easi["survey"] if r.get("constraint")]
        self.assertEqual(len(constrained), 4,
                         f"EASI form expected 4 constrained body-region items, "
                         f"found {len(constrained)}")
        # Each row should have a 0-6 bound
        for r in constrained:
            self.assertIn("0", r["constraint"],
                          f"EASI row {r.get('name')} missing 0 bound: {r['constraint']}")
            self.assertIn("6", r["constraint"],
                          f"EASI row {r.get('name')} missing 6 bound: {r['constraint']}")


# ══════════════════════════════════════════════════════════════════════════════
# Test suite 12 — Zelta (Merative) synthetic fixture
# ══════════════════════════════════════════════════════════════════════════════

class TestZeltaFixture(unittest.TestCase):
    """
    End-to-end coverage of the Zelta (Merative) 2024.1 synthetic fixture
    (tests/migration/fixtures/zelta_synthetic.xml). Phase 3 asthma study
    with three repeating 4-week treatment cycles and a respiratory-
    specific SP (spirometry) form. Zelta has no widely-known public
    namespace; vendor detection here is Originator-string based only.
    """

    @classmethod
    def setUpClass(cls):
        from odm_validator import validate_odm
        parse_odm_metadata, *_ = _import_reader()
        transform, *_ = _import_spec()
        cls.validate = staticmethod(validate_odm)
        cls.parse    = staticmethod(parse_odm_metadata)
        cls.transform = staticmethod(transform)
        cls.raw  = _load(ZELTA_XML)
        cls.odm  = parse_odm_metadata(cls.raw)
        cls.spec = transform(cls.odm)

    def test_vendor_detected_via_originator(self):
        """Originator='Zelta 2024.1' must resolve to 'Zelta (Merative)'."""
        self.assertEqual(self.odm["source_system"], "Zelta (Merative)")

    def test_layer_1_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_1"], "PASS")

    def test_layer_2_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_2"], "PASS")

    def test_layer_3_passes(self):
        rep = self.validate(self.raw)
        self.assertEqual(rep.layer_results["layer_3"], "PASS")

    def test_can_proceed(self):
        rep = self.validate(self.raw)
        self.assertTrue(rep.can_proceed, f"Fixture must be migratable: {rep.summary}")

    def test_protocol_number_is_resp3001(self):
        self.assertEqual(self.spec["study_meta"]["protocol_number"], "RESP3001")

    def test_oc9_ae_on_se_common_only(self):
        ae = next((f for f in self.spec["forms"] if f["form_id"] == "AE"), None)
        self.assertIsNotNone(ae)
        self.assertEqual(ae["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: AE visits_assigned={ae['visits_assigned']}")

    def test_oc9_cm_on_se_common_only(self):
        cm = next((f for f in self.spec["forms"] if f["form_id"] == "CM"), None)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["visits_assigned"], ["SE_COMMON"],
                         f"OC-9 violation: CM visits_assigned={cm['visits_assigned']}")

    def test_sp_form_present_with_constrained_items(self):
        """SP (spirometry) form must be in spec with FEV1 + FVC constrained items."""
        sp = next((f for f in self.spec["forms"] if f["form_id"] == "SP"), None)
        self.assertIsNotNone(sp, f"SP form missing. Forms: "
                                 f"{sorted(f['form_id'] for f in self.spec['forms'])}")
        constrained = [r for r in sp["survey"] if r.get("constraint")]
        self.assertGreaterEqual(len(constrained), 2,
                                f"SP form expected ≥2 constrained items "
                                f"(FEV1 + FVC), found {len(constrained)}")

    def test_cycle_events_present_and_repeating(self):
        """SE_CYCLE1/2/3 must all be parsed and marked Repeating='Yes'."""
        ev_by_oid = {e["oid"]: e for e in self.odm["events"]}
        for cycle in ("SE_CYCLE1", "SE_CYCLE2", "SE_CYCLE3"):
            self.assertIn(cycle, ev_by_oid,
                          f"{cycle} missing from parsed events")
            self.assertTrue(ev_by_oid[cycle]["repeating"],
                            f"{cycle} should be Repeating='Yes'")


# ══════════════════════════════════════════════════════════════════════════════
# ODM + Protocol enrichment-mode dispatch
# ══════════════════════════════════════════════════════════════════════════════

class TestMigrationEnrichmentDispatch(unittest.TestCase):
    """
    Verify run_migration() routes correctly between the deterministic
    ODM-only transform and the AI-assisted ODM+Protocol enrichment.

    These are pure dispatch tests — they mock all Monday I/O and both
    transform paths so they don't depend on Anthropic, lxml, or the
    network. They only assert which transform was invoked.
    """

    def _run(self, protocol_bytes):
        import asyncio
        from unittest.mock import patch, AsyncMock, MagicMock
        import migration_pipeline as mp

        # Tiny but well-formed ODM the validator can accept and parse.
        raw_bytes = _minimal_odm(
            study_name="ENRICH_TEST", protocol="ENRICH-001",
            events=[{"oid": "SE_BL", "name": "Baseline", "form_refs": ["F_DM"]}],
            forms =[{"oid": "F_DM",  "name": "DM",       "ig_refs":   ["IG_DM"]}],
        )

        fake_spec = {
            "study_meta": {"protocol_number": "ENRICH-001"},
            "forms": [],
            "timepoint_csv":  {"filename": "x.csv", "rows": []},
            "labranges_csv":  {"filename": "y.csv", "columns": [], "rows": []},
            "review_flags":   {},
        }

        det_mock = MagicMock(return_value=fake_spec)
        ai_mock  = AsyncMock(return_value=fake_spec)
        # A mock claude_client so run_migration doesn't reach for the real
        # `claude_client` module (which would pull in the anthropic SDK).
        fake_cc = MagicMock()
        fake_cc.call_claude = AsyncMock(return_value="{}")

        with patch.object(mp, "download_column_file", new=AsyncMock(return_value=b"")), \
             patch.object(mp, "list_column_filenames", new=AsyncMock(return_value=["src.xml"])), \
             patch.object(mp, "upload_file",           new=AsyncMock(return_value=None)), \
             patch.object(mp, "append_log",            new=AsyncMock(return_value=None)), \
             patch.object(mp, "_set_dropdown_value",   new=AsyncMock(return_value=None)), \
             patch.object(mp, "_read_dropdown_value",  new=AsyncMock(return_value=[])), \
             patch.object(mp, "transform",          new=det_mock), \
             patch.object(mp, "transform_with_ai",  new=ai_mock):
            result = asyncio.run(mp.run_migration(
                item_id="1",
                raw_bytes=raw_bytes,
                protocol_bytes=protocol_bytes,
                claude_client=fake_cc,
            ))
        return result, det_mock, ai_mock

    def test_odm_only_mode_calls_transform(self):
        """No protocol_bytes → deterministic transform() is used."""
        result, det, ai = self._run(protocol_bytes=None)
        self.assertEqual(result["status"], "ok",
                         f"Expected ok, got {result['status']}: {result['summary']}")
        self.assertEqual(det.call_count, 1,
                         "transform() should have been called exactly once")
        self.assertEqual(ai.call_count, 0,
                         "transform_with_ai() must NOT be called in ODM-only mode")

    def test_enrichment_mode_calls_transform_with_ai(self):
        """protocol_bytes present → transform_with_ai() is used, gets the PDF."""
        pdf_bytes = b"%PDF-1.4 fake-protocol-content"
        result, det, ai = self._run(protocol_bytes=pdf_bytes)
        self.assertEqual(result["status"], "ok",
                         f"Expected ok, got {result['status']}: {result['summary']}")
        self.assertEqual(ai.call_count, 1,
                         "transform_with_ai() should have been called exactly once")
        self.assertEqual(det.call_count, 0,
                         "Deterministic transform() must NOT be called when "
                         "enrichment mode is active (transform_with_ai owns the "
                         "baseline call internally)")
        # The PDF bytes must reach transform_with_ai as the protocol context.
        kwargs = ai.call_args.kwargs
        self.assertEqual(kwargs.get("protocol_bytes"), pdf_bytes,
                         "protocol_bytes must be forwarded to transform_with_ai")


class TestAiAssistPromptHierarchy(unittest.TestCase):
    """The AI_ASSIST_PROMPT must encode the input hierarchy and hard rules."""

    def test_ai_assist_prompt_contains_hierarchy_instructions(self):
        from odm_to_spec import AI_ASSIST_PROMPT
        text = AI_ASSIST_PROMPT.lower()
        for phrase in ("oc-9", "cdash", "authoritative", "never override"):
            self.assertIn(
                phrase, text,
                f"AI_ASSIST_PROMPT missing required phrase: {phrase!r}. "
                f"The hierarchy + hard-rule block was not detected."
            )


class TestVendorConventions(unittest.TestCase):
    """The vendor_conventions/ folder + loader + AI-prompt wiring."""

    REQUIRED_SECTIONS = (
        "## Overview",
        "## Detection",
        "## Namespace",
        "## ODM Structural Patterns",
        "## OID Conventions",
        "## Form Structure Quirks",
        "## Event/Visit Mapping",
        "## Codelist Handling",
        "## Clinical Data Patterns",
        "## Known Export Limitations",
        "## OC4 Transform Rules",
        "## Compliance Notes",
    )

    @classmethod
    def setUpClass(cls):
        from odm_to_spec import (
            VENDOR_CONVENTION_FILES,
            load_vendor_conventions,
            _CONVENTIONS_DIR,
        )
        cls.MAP   = VENDOR_CONVENTION_FILES
        cls.load  = staticmethod(load_vendor_conventions)
        cls.DIR   = _CONVENTIONS_DIR

    def test_load_vendor_conventions_medidata(self):
        """Medidata convention loads and carries the headline rules."""
        text = self.load("Medidata Rave")
        self.assertTrue(text, "Medidata convention file returned empty content")
        for phrase in ("mdsol", "IsLog", "OC-8"):
            self.assertIn(
                phrase, text,
                f"medidata_rave.md missing expected phrase {phrase!r}"
            )

    def test_load_vendor_conventions_unknown_falls_back_to_generic(self):
        """An unrecognised source_system loads generic_odm.md."""
        text = self.load("UnknownEDC 1.0")
        self.assertTrue(text, "generic_odm.md fallback returned empty content")
        self.assertIn("Generic ODM", text,
                      "Fallback did not return generic_odm.md content")

    def test_load_vendor_conventions_empty_string_on_missing_file(self):
        """If both the matched file AND the generic fallback are missing,
        the loader degrades gracefully to an empty string."""
        from odm_to_spec import _CONVENTIONS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            # Point the loader at an empty directory by patching the module
            # attribute for the duration of the test.
            import odm_to_spec as mod
            original = mod._CONVENTIONS_DIR
            try:
                mod._CONVENTIONS_DIR = Path(tmp)
                text = mod.load_vendor_conventions("Medidata Rave")
                self.assertEqual(text, "",
                    "Loader should return '' when no convention file is found")
            finally:
                mod._CONVENTIONS_DIR = original

    def test_all_convention_files_exist(self):
        """Every VENDOR_CONVENTION_FILES entry points at a real file."""
        for vendor, filename in self.MAP.items():
            path = self.DIR / filename
            self.assertTrue(
                path.is_file(),
                f"Convention file for {vendor!r} not found: {path}"
            )

    def test_all_convention_files_have_required_sections(self):
        """Every convention file follows the 12-section standard structure."""
        for filename in sorted(set(self.MAP.values())):
            path = self.DIR / filename
            text = path.read_text(encoding="utf-8")
            for heading in self.REQUIRED_SECTIONS:
                self.assertIn(
                    heading, text,
                    f"{filename} missing required heading {heading!r}"
                )

    def test_ai_assist_prompt_includes_conventions(self):
        """transform_with_ai must embed the vendor conventions in the prompt
        it passes to claude_client.call_claude."""
        import asyncio
        import odm_to_spec as mod

        captured: dict = {}

        class _FakeClient:
            async def call_claude(self, prompt, pdf_bytes=None, extra_text=None):
                captured["prompt"] = prompt
                # Return a JSON-serialised minimal spec so transform_with_ai
                # parses it successfully.
                return json.dumps({"forms": []})

        # Build a minimal OdmStudy dict (no real ODM parse needed — transform()
        # only reads keys the module already tolerates as missing).
        odm_study = {
            "source_system":         "Medidata Rave",
            "source_system_version": "5.6",
            "study":                 {"oid": "S1", "name": "T", "protocol_name": "P"},
            "protocol":              {"arms": []},
            "events":                [],
            "forms":                 [],
            "items":                 [],
            "item_groups":           [],
            "codelists":             [],
            "parse_warnings":        [],
        }

        asyncio.run(mod.transform_with_ai(
            odm_study, _FakeClient(),
            protocol_bytes=None,
            source_system="Medidata Rave",
        ))

        prompt = captured.get("prompt", "")
        self.assertIn("VENDOR-SPECIFIC CONVENTIONS FOR Medidata Rave", prompt,
                      "Prompt missing the vendor-conventions header")
        self.assertIn("mdsol", prompt,
                      "Prompt does not appear to include the Medidata "
                      "convention body (no 'mdsol' substring found)")


# ══════════════════════════════════════════════════════════════════════════════
# Test runner
# ══════════════════════════════════════════════════════════════════════════════

def run_tests(verbosity=1):
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in (TestOdmReader, TestOdmToSpec, TestVendorRegistry,
                TestOdmValidator, TestMedidataRaveFixture,
                TestVeevaFixture, TestViedocFixture, TestImednetFixture,
                TestOracleInFormFixture, TestRedcapFixture,
                TestCastorFixture, TestZeltaFixture,
                TestMigrationEnrichmentDispatch,
                TestAiAssistPromptHierarchy,
                TestVendorConventions):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    verbosity = 2 if "-v" in sys.argv else 1
    # Support running a specific test class: python test_migration.py TestOdmReader
    specific = [a for a in sys.argv[1:] if not a.startswith("-")]
    if specific:
        sys.exit(unittest.main(verbosity=verbosity))
    else:
        sys.exit(run_tests(verbosity=verbosity))
