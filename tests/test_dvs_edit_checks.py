"""DVS_OC4 is the single editable list of checks: sources, rule ids, plain English, cross-form source columns,
AI proposals listed (Status Proposed) but not applied, template dropdowns kept on the right headers."""
import copy, io, json, os, sys, tempfile, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "conventions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "dvs-specification", "scripts"))
import openpyxl
from conventions_engine import apply_conventions
import ai_edit_checks as ai
from extract_dvs_from_forms import extract_dvs_data
from generate_dvs import build_dvs, DVS_OC4_COLS
from test_consent_floor import _spec as consent_spec
from test_core_checks import _spec as core_spec


def _engine(spec):
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
    return spec


def _forms_json(spec):
    return {"forms": {f"{f['form_id']}.xlsx": {"survey": [{("bind::oc:itemgroup" if k == "bind__oc_itemgroup" else k): v
                                                         for k, v in r.items()} for r in f["survey"]],
                                              "choices": f.get("choices") or []} for f in spec["forms"]}}


def _dvs(spec):
    with contextlib.redirect_stdout(io.StringIO()):
        return extract_dvs_data(spec, _forms_json(spec))


def test_cross_form_check_has_source_columns_and_rule():
    rows = _dvs(_engine(consent_spec()))["dvs_oc4"]
    floor = next(r for r in rows if r["Target Item Name"] == "ONSET" and r["Rule / Proposal ID"] == "CONSENT_FLOOR")
    assert floor["Check Source"] == "Global Rule" and floor["Status"] == "Draft"
    assert (floor["Source Form OID(s)"], floor["Source Item Name(s)"], floor["Helper Item OID"]) == \
        ("F_ICF", "ICFDAT", "F_AE.ICFDAT_CF")
    assert floor["Plain-English Description"] == "AE start must be on or after Consent date (F_ICF)."


def test_cdisc_sources_one_row_per_clause():
    rows = _dvs(_engine(core_spec()))["dvs_oc4"]
    ser = next(r for r in rows if r["Target Item Name"] == "SERIOUS" and r["Check Type"] == "Constraint")
    assert ser["Check Source"] == "CDISC CORE (CORE-000022)" and ser["Rule / Proposal ID"] == "CORE-000022"
    assert ser["Protocol Reference"] == "CORE-000022"
    why = next(r for r in rows if r["Target Item Name"] == "TEMPWHY" and r["Check Type"] == "Conditional Display")
    assert why["Check Source"] == "CDISC CORE (CORE-000440)"
    lif = next(r for r in rows if r["Target Item Name"] == "AESLIFE" and r["Check Type"] == "Conditional Display")
    assert lif["Check Source"] == "Study Build"


PROPOSALS = json.dumps({"checks": [
    {"target_form": "F_AE", "target_field": "ONSET", "operator": ">=", "source_form": "DM", "source_field": "VISDAT",
     "message": "AE start must be on or after the visit date.", "rationale": "test", "category": "cross_form"}]})


def test_ai_proposals_listed_under_their_form_not_applied():
    spec = _engine(consent_spec())
    spec["study_meta"]["ai_edit_checks"] = {"proposals": ai.validate_response(spec, PROPOSALS)["proposals"]}
    before = copy.deepcopy(spec)
    rows = _dvs(spec)["dvs_oc4"]
    assert spec == before
    i = next(i for i, r in enumerate(rows) if r["Check ID"] == "AI.001")
    p = rows[i]
    assert p["Status"] == "Proposed" and p["Check Source"] == "AI-Proposed" and p["Target Form OID"] == "F_AE"
    assert rows[i - 1]["Target Form OID"] == "F_AE"                       # directly under the form's own checks
    assert (p["Source Form OID(s)"], p["Source Item Name(s)"]) == ("DM", "VISDAT")
    assert json.loads(p["Machine Data"])["source_field"] == "VISDAT"
    assert ai.apply_proposal(spec, json.loads(p["Machine Data"])) is True   # approving it applies it


def test_workbook_dropdowns_follow_headers_and_machine_data_hidden():
    spec = _engine(consent_spec())
    spec["study_meta"]["ai_edit_checks"] = {"proposals": ai.validate_response(spec, PROPOSALS)["proposals"]}
    path = os.path.join(tempfile.mkdtemp(), "dvs.xlsx")
    with contextlib.redirect_stdout(io.StringIO()):
        build_dvs(_dvs(spec), path)
    wb = openpyxl.load_workbook(path)
    ws = wb["DVS_OC4"]
    hdr = [c.value for c in ws[3]]
    assert hdr == DVS_OC4_COLS and "Edit Checks" not in wb.sheetnames
    letter = {h: openpyxl.utils.get_column_letter(i) for i, h in enumerate(hdr, 1)}
    dvs = {str(d.sqref).split(":")[0].rstrip("0123456789"): str(d.formula1) for d in ws.data_validations.dataValidation}
    assert "Delete,Change,Approve,Reject,Add" in dvs[letter["Action"]]
    assert "$K$8" in dvs[letter["Status"]] and "Lookups!$B$" in dvs[letter["Check Type"]]
    assert ws.column_dimensions[letter["Machine Data"]].hidden is True
