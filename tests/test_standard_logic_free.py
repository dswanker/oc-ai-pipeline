"""Two rules for the rules engine on customer standard forms (decisions of 2026-10-10).

1. A standard that carries NO logic of its own (an ODM gives structure only; an XLSForm may simply have none) gets
   the engine's data checks built on it, each recorded with the reason. A standard that carries its own logic
   keeps proposals. STANDARD_LOGIC_FREE_APPLY=0 restores proposals only.
2. A rule about how the form file is authored (settings sheet, naming, layout) is a build rule, not a data check:
   it is never a DVS row. Decided by what the proposal's operations target, not by its wording.
All fixtures are synthetic (tests/standards/fixtures.py)."""
import contextlib, copy, io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "skills", "dvs-specification", "scripts"))
import openpyxl

import standards_match as sm
from conventions_engine import apply_conventions, customer_standard as cs
from standards import fixtures as fx


def _matched(xls=True, odm=True):
    files = ([("AEGEN.xlsx", fx.xlsform_bytes())] if xls else []) + ([("standard.xml", fx.ODM)] if odm else [])
    with contextlib.redirect_stdout(io.StringIO()):
        return sm.apply(fx.spec(), sm.load_sources(files, None))


def _engine(spec):
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
    return spec


def _form(spec, fid):
    return next(f for f in spec["forms"] if f["form_id"] == fid)


def _row(form, name):
    return next(r for r in form["survey"] if r.get("name") == name)


def _dvs_rows(spec):
    from extract_dvs_from_forms import extract_dvs_data
    fj = {"forms": {f"{f['form_id']}.xlsx": {"survey": [{("bind::oc:itemgroup" if k == "bind__oc_itemgroup" else k): v
                                                          for k, v in r.items()} for r in f["survey"]],
                                               "choices": f.get("choices") or []} for f in spec["forms"]}}
    with contextlib.redirect_stdout(io.StringIO()):
        return extract_dvs_data(spec, fj)["dvs_oc4"]


# ── what is a data check ──────────────────────────────────────────────────────

def test_a_proposal_is_a_data_check_by_what_its_operations_change():
    row = lambda **s: {"ops": [{"op": "row", "field": "X", "set": s, "before": {}}], "target_field": "X"}
    assert cs.is_data_check(row(constraint=". <= today()"))
    assert cs.is_data_check(row(relevant="${A} = 'Y'"))
    assert cs.is_data_check(row(required="yes"))
    assert cs.is_data_check(row(calculation="${A} + 1"))
    assert cs.is_data_check({"target_field": "X", "ops": [
        {"op": "insert", "after": "X", "row": {"type": "calculate", "name": "A_CF", "calculation": "instance('x')"}},
        {"op": "row", "field": "X", "set": {"constraint": ". >= ${A_CF}"}, "before": {}}]})
    # the form file's own metadata, and how an item is named, labelled or laid out: build rules
    assert not cs.is_data_check({"target_field": "", "ops": [{"op": "setting", "key": "namespaces", "value": "x"}]})
    assert not cs.is_data_check(row(appearance="w2"))
    assert not cs.is_data_check(row(label="New label", hint="h"))
    assert not cs.is_data_check(row(constraint_decimals=2))       # a directive no builder turns into a check
    assert not cs.is_data_check({"target_field": "X", "ops": [{"op": "insert", "after": "X",
                                                              "row": {"type": "text", "name": "NEW"}}]})
    assert not cs.is_data_check({"target_field": "X", "ops": [{"op": "choice", "choice": {"list_name": "L"}}]})
    assert not cs.is_data_check(None) and not cs.is_data_check({})
    # a copy without its operations (older DVS machine data): judged by its target and type
    assert cs.is_data_check({"target_field": "X", "check_type": "Constraint"})
    assert not cs.is_data_check({"target_field": "", "check_type": "Other"})


# ── rule 1: a logic-free standard gets the checks built ───────────────────────

def test_odm_standard_is_logic_free_and_an_xlsform_with_logic_is_not():
    assert cs.logic_free(_form(_matched(xls=False), "AE"))                             # from the ODM
    assert not cs.logic_free(_form(_matched(odm=False), "AEGEN"))                      # XLSForm with its own logic
    assert not cs.logic_free({"form_id": "X", "survey": []})                           # not a customer standard


def test_engine_checks_are_built_on_a_logic_free_standard_with_the_reason():
    out = _engine(_matched(xls=False))
    ae = _form(out, "AE")
    assert _row(ae, "AESTDAT").get("constraint") and "today()" in _row(ae, "AESTDAT")["constraint"]
    assert "${AESTDAT}" in _row(ae, "AEENDAT")["constraint"]
    st = sm.state(out)
    applied = [a for a in st["auto_applied"] if a["target_form"] == "AE"]
    assert applied and all(a["reason"].startswith("Applied: the customer standard form carries no logic") and
                           "uploaded ODM" in a["reason"] for a in applied)
    assert not [p for p in sm.proposals(out) if p["kind"] == "engine" and p["target_form"] == "AE"]
    assert all(a.get("auto") for a in ae["customer_standard"]["approved"])
    # in the DVS these are Draft (built) rows with their source and the reason in Notes
    rows = [r for r in _dvs_rows(out) if r["Target Form OID"] == "AE" and r["Target Item Name"] == "AEENDAT"
            and r["Check Type"] in ("Constraint", "Cross-form")]
    assert rows and all(r["Status"] == "Draft" for r in rows)
    assert all(r["Check Source"] not in ("Customer Standard", "Study Build") for r in rows)
    assert all("carries no logic of its own" in r["Notes"] for r in rows)


def test_applying_is_stable_over_the_engine_passes_of_a_build():
    out = _engine(_matched(xls=False))
    first = copy.deepcopy([sm.core_row(r) for r in _form(out, "AE")["survey"]])
    n = len(sm.state(out)["auto_applied"])
    _engine(out); _engine(out)
    assert [sm.core_row(r) for r in _form(out, "AE")["survey"]] == first
    assert len(sm.state(out)["auto_applied"]) == n
    assert len({a["id"] for a in sm.state(out)["auto_applied"]}) == n


def test_a_standard_with_its_own_logic_keeps_proposals():
    out = _engine(_matched(odm=False))
    form = _form(out, "AEGEN")
    assert [sm.core_row(r) for r in form["survey"]] == sm.parse_xlsform(fx.xlsform_bytes(), "AEGEN.xlsx")["survey"]
    props = [p for p in sm.proposals(out) if p["kind"] == "engine" and p["target_form"] == "AEGEN"]
    assert props and not sm.state(out).get("auto_applied")
    proposed = [r for r in _dvs_rows(out) if r.get("Status") == "Proposed" and r["Target Form OID"] == "AEGEN"]
    assert proposed and all("carries its own logic" in r["Notes"] for r in proposed)


def test_kill_switch_restores_proposals_only(monkeypatch):
    monkeypatch.setenv("STANDARD_LOGIC_FREE_APPLY", "0")
    out = _matched(xls=False)
    pristine = copy.deepcopy([sm.core_row(r) for r in _form(out, "AE")["survey"]])
    _engine(out)
    assert [sm.core_row(r) for r in _form(out, "AE")["survey"]] == pristine
    assert [p for p in sm.proposals(out) if p["kind"] == "engine" and p["target_form"] == "AE"]
    assert not sm.state(out).get("auto_applied")


def test_a_data_manager_can_still_delete_an_applied_check():
    """An applied check is an ordinary built check: its DVS row carries the id the Delete action needs."""
    out = _engine(_matched(xls=False))
    row = next(r for r in _dvs_rows(out) if r["Target Form OID"] == "AE" and r["Target Item Name"] == "AEENDAT"
               and r["Check Type"] in ("Constraint", "Cross-form"))
    assert row["Rule / Proposal ID"]


# ── rule 2: form-authoring rules are never DVS rows ───────────────────────────

def test_settings_rules_are_kept_as_build_rules_and_never_listed():
    out = _engine(_matched(xls=False))                 # the ODM gives no settings sheet: the settings rule fires
    st = sm.state(out)
    assert [b for b in st["build_rules"] if not b["target_field"] and b["logic"].startswith("settings.")]
    assert "namespaces" not in _form(out, "AE")["settings"]            # and it is not applied to the form either
    assert all(cs.is_data_check(p) for p in sm.proposals(out) if p["kind"] == "engine")
    rows = _dvs_rows(out)
    ids = {b["id"] for b in st["build_rules"]}
    assert not [r for r in rows if r.get("Check ID") in ids or r.get("Rule / Proposal ID") in ids]
    assert not [r for r in rows if r.get("Status") == "Proposed" and not r.get("Target Item Name")]


def test_a_specification_saved_before_the_rule_is_filtered_when_the_dvs_is_written(monkeypatch):
    monkeypatch.setenv("STANDARD_LOGIC_FREE_APPLY", "0")
    out = _engine(_matched())
    junk = {"id": "STD-JUNK0001", "kind": "engine", "convention_id": "form_metadata.x", "source": "Global Rule",
            "title": "A form-file rule", "target_form": "AEGEN", "target_field": "", "check_type": "Other",
            "logic": "settings.style = theme-grid", "message": "",
            "ops": [{"op": "setting", "key": "style", "value": "theme-grid", "before": None}]}
    sm.state(out)["proposals"].append(junk)
    rows = _dvs_rows(out)
    assert not [r for r in rows if r.get("Check ID") == "STD-JUNK0001"]
    assert [r for r in rows if r.get("Status") == "Proposed"]          # real proposals are still listed
