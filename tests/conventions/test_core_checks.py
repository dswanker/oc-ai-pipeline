"""End-to-end: CORE-derived checks via the real global conventions, then DVS/UAT generation."""
import io, os, sys, contextlib, copy
from conventions_engine import apply_conventions

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "skills", "dvs-specification", "scripts"))


def _spec():
    yn = lambda ln, y="Y", n="N": [{"list_name": ln, "name": y, "label": "Yes"}, {"list_name": ln, "name": n, "label": "No"}]
    return {"study_meta": {"protocol_number": "T"}, "forms": [
        {"form_id": "AE", "form_title": "Adverse Events", "cdash_domain": "AE", "survey": [
            {"type": "select_one ny", "name": "SERIOUS", "label": "Serious?", "concept": "AESER", "bind__oc_itemgroup": "AE"},
            {"type": "select_one ny2", "name": "DIED", "label": "Results in death", "concept": "AESDTH", "bind__oc_itemgroup": "AE"},
            {"type": "select_one ny", "name": "AESHOSP", "label": "Hospitalisation", "bind__oc_itemgroup": "AE"},
            {"type": "select_one ny", "name": "AESLIFE", "label": "Life threatening", "bind__oc_itemgroup": "AE",
             "relevant": "${AESHOSP} = 'Y'"},
            {"type": "select_one out", "name": "OUTCOME", "label": "Outcome", "concept": "AEOUT", "bind__oc_itemgroup": "AE"}],
         "choices": yn("ny") + yn("ny2", "yes", "no") + [
             {"list_name": "out", "name": "RECOVERED", "label": "Recovered/Resolved"},
             {"list_name": "out", "name": "DIED_OUT", "label": "Fatal", "cdisc_submission_value": "FATAL"}]},
        {"form_id": "VS", "form_title": "Vital Signs", "cdash_domain": "VS", "survey": [
            {"type": "select_one ny", "name": "TEMPTKN", "label": "Temperature taken?", "concept": "VSPERF",
             "concept_qualifier": "TEMP", "bind__oc_itemgroup": "VS"},
            {"type": "text", "name": "TEMPWHY", "label": "Reason not done", "concept": "VSREASND",
             "concept_qualifier": "TEMP", "bind__oc_itemgroup": "VS"}],
         "choices": yn("ny")},
        {"form_id": "EX", "form_title": "Exposure", "cdash_domain": "EX", "survey": [
            {"type": "select_one ny", "name": "DOSED", "label": "Dose given?", "concept": "EXOCCUR", "bind__oc_itemgroup": "EX"},
            {"type": "decimal", "name": "DOSEAMT", "label": "Dose", "concept": "EXDOSE", "bind__oc_itemgroup": "EX"}],
         "choices": yn("ny")},
    ]}


def _run(spec):
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
    return {f"{f['form_id']}.{r['name']}": r for f in spec["forms"] for r in f["survey"]}


def test_serious_ae_needs_a_criterion_with_each_lists_codes():
    r = _run(_spec())["AE.SERIOUS"]
    assert r["constraint"] == ". != 'Y' or (${DIED} = 'yes' or ${AESHOSP} = 'Y' or ${AESLIFE} = 'Y')"
    assert r["edit_checks"] == ["CORE-000022"]


def test_criteria_gated_by_serious_but_author_relevance_kept():
    rows = _run(_spec())
    assert rows["AE.DIED"]["relevant"] == "${SERIOUS} = 'Y'"
    assert rows["AE.AESHOSP"]["relevant"] == "${SERIOUS} = 'Y'"
    assert rows["AE.AESLIFE"]["relevant"] == "${AESHOSP} = 'Y'"      # author's relevance untouched


def test_fatal_outcome_requires_death_using_lists_own_codes():
    r = _run(_spec())["AE.OUTCOME"]
    assert r["constraint"] == ". != 'DIED_OUT' or ${DIED} = 'yes'"
    assert r["edit_checks"] == ["SDTM.AEOUT_FATAL_AESDTH"]


def test_reason_not_done_paired_by_concept_and_qualifier():
    r = _run(_spec())["VS.TEMPWHY"]
    assert r["relevant"] == "${TEMPTKN} = 'N'" and r["required"] == "yes"


def test_dose_only_when_occurred():
    assert _run(_spec())["EX.DOSEAMT"]["relevant"] == "${DOSED} = 'Y'"


def test_idempotent_and_dvs_gets_loadable_test_data():
    once = _spec(); _run(once)
    twice = copy.deepcopy(once); _run(twice)
    strip = lambda s: [[{k: v for k, v in r.items() if k != "conventions_applied"} for r in f["survey"]] for f in s["forms"]]
    assert strip(once) == strip(twice)
    from extract_dvs_from_forms import extract_dvs_data
    forms = {f"F_{f['form_id']}.xlsx": {"survey": [{("bind::oc:itemgroup" if k == "bind__oc_itemgroup" else k): v
                                                    for k, v in r.items()} for r in f["survey"]],
                                       "choices": f["choices"]} for f in once["forms"]}
    with contextlib.redirect_stdout(io.StringIO()):
        cases = extract_dvs_data(once, {"forms": forms})["uat_cases"]
    gate_loads = [str(c["Load_Value"]) for c in cases if c["Scenario"].startswith(("Shown path", "Hidden path"))]
    assert gate_loads and not any("ZZZ" in v for v in gate_loads)
    serious = [c for c in cases if c["Item_OID"].endswith("SERIOUS")]
    assert any(c["Scenario"].startswith("Sad") for c in serious) and any(c["Scenario"].startswith("Happy") for c in serious)
