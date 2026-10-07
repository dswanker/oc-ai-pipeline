"""Unit tests for cdisc_ct.py using a synthetic CT package (no network)."""
import cdisc_ct as c

HDR = "Code\tCodelist Code\tCodelist Extensible (Yes/No)\tCodelist Name\tCDISC Submission Value\tCDISC Synonym(s)\tCDISC Definition\tNCI Preferred Term\n"
ROWS = [
    ("C66742", "", "No", "No Yes Response", "NY", "", "", ""),
    ("C49487", "C66742", "", "No Yes Response", "N", "No", "", "No"),
    ("C49488", "C66742", "", "No Yes Response", "Y", "Yes", "", "Yes"),
    ("C66767", "", "No", "Action Taken with Study Treatment", "ACN", "", "", ""),
    ("C49503", "C66767", "", "", "DOSE REDUCED", "", "", "Dose Reduced"),
    ("C49501", "C66767", "", "", "DRUG WITHDRAWN", "", "", "Drug Withdrawn"),
    ("C66729", "", "Yes", "Route of Administration Response", "ROUTE", "", "", ""),
    ("C38288", "C66729", "", "", "ORAL", "PO", "", "Oral Route of Administration"),
    ("C128689", "", "Yes", "Race As Collected", "RACEC", "", "", ""),
    ("C41261", "C128689", "", "", "WHITE", "", "", "White"),
]
CT = c.CTPackage(c._parse_evs(HDR + "".join("\t".join(r) + "\n" for r in ROWS)), "test", "2026-01-01")


def _assess(name, values):
    row = {"type": "select_one L", "name": name}
    return c.assess_field(CT, row, [{"list_name": "L", "name": v} for v in values])


def test_binding_rules():
    assert c.bind(CT, "NY", [])[1] == "exact"
    assert c.bind(CT, "AEACN", [])[1] == "domain_prefix"
    assert c.bind(CT, "RACE", [])[1] == "as_collected"
    assert c.bind(CT, "ANYFIELDYN", ["yes", "no"])[1] == "yes_no_values"
    assert c.bind(CT, "FREETEXTISH", ["A", "B"]) == (None, None)


def test_statuses():
    assert _assess("AEACN", ["DOSE REDUCED"])["status"] == "subset"
    assert _assess("AEACN", ["DOSE REDUCED", "DRUG WITHDRAWN"])["status"] == "conformant"
    assert _assess("AEACN", ["REDUCE"])["status"] == "nonconformant"
    assert _assess("CMROUTE", ["PO"])["status"] == "mappable"
    assert _assess("CMROUTE", ["TRANSRECTAL"])["status"] == "extended"
    assert _assess("MHYN", ["yes", "no"])["synonym_matches"] == {"yes": "Y", "no": "N"}
    assert _assess("OTHER", ["A"])["status"] == "unbound"


def test_report_shape():
    spec = {"forms": [{"form_id": "F", "choices": [{"list_name": "yn", "name": "Y"}, {"list_name": "yn", "name": "N"}],
                       "survey": [{"type": "select_one yn", "name": "Q1"}, {"type": "text", "name": "T"}]}]}
    rep = c.report(spec, CT)
    assert rep["ct_package_date"] == "2026-01-01"
    assert rep["summary"] == {"conformant": 1}
    assert rep["fields"][0]["codelist_code"] == "C66742"
