"""End-to-end: the real global conventions write the date-order, ongoing-relevance and future-date checks."""
import io, contextlib, copy
from conventions_engine import apply_conventions


def _spec():
    return {"study_meta": {"protocol_number": "T"}, "forms": [
        {"form_id": "AE", "cdash_domain": "AE", "survey": [
            # customer names, tagged with CDASH concepts (as cdisc_concepts does before the engine runs)
            {"type": "date", "name": "ONSET_DT", "label": "Onset", "bind__oc_itemgroup": "AE", "concept": "AESTDAT"},
            {"type": "select_one yn", "name": "STILL_ON", "label": "Ongoing?", "bind__oc_itemgroup": "AE",
             "concept": "AEONGO"},
            {"type": "date", "name": "RESOLVED_DT", "label": "Resolved", "bind__oc_itemgroup": "AE", "concept": "AEENDAT"},
        ], "choices": [{"list_name": "yn", "name": "yes", "label": "Yes"}, {"list_name": "yn", "name": "no", "label": "No"}]},
        {"form_id": "CM", "cdash_domain": "CM", "survey": [
            {"type": "date", "name": "CMSTDAT", "label": "Start", "bind__oc_itemgroup": "CM"},
            {"type": "date", "name": "CMENDAT", "label": "End", "bind__oc_itemgroup": "CM",
             "constraint": ". >= ${CMSTDAT}", "constraint_message": "Author check."},
            {"type": "date", "name": "NEXTVISDAT", "label": "Planned next visit", "bind__oc_itemgroup": "CM",
             "constraint": ". > today()", "constraint_message": "Must be in the future."},
        ], "choices": []},
    ]}


def _run(spec):
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
    return {f"{f['form_id']}.{r['name']}": r for f in spec["forms"] for r in f["survey"]}


def test_concept_paired_dates_stack_with_future_check():
    r = _run(_spec())["AE.RESOLVED_DT"]
    assert "${ONSET_DT}" in r["constraint"] and "today()" in r["constraint"]
    assert set(r["edit_checks"]) == {"CDISC.DATE_ORDER", "NO_FUTURE_DATE"}
    assert "End date must be on or after the start date." in r["constraint_message"]


def test_ongoing_relevance_uses_the_lists_own_no_code():
    r = _run(_spec())["AE.RESOLVED_DT"]
    assert r["relevant"] == "${STILL_ON} = 'no'"


def test_author_constraints_are_respected():
    rows = _run(_spec())
    cm = rows["CM.CMENDAT"]
    assert cm["constraint"] == ". >= ${CMSTDAT}"                     # equivalent already there: untouched
    assert cm["edit_checks"] == ["CDISC.DATE_ORDER"]                 # recorded as present, not NO_FUTURE_DATE
    nv = rows["CM.NEXTVISDAT"]
    assert nv["constraint"] == ". > today()" and not nv.get("edit_checks")  # planned date left alone


def test_idempotent():
    once = _spec(); _run(once)
    twice = copy.deepcopy(once); _run(twice)
    strip = lambda s: [[{k: v for k, v in r.items() if k != "conventions_applied"} for r in f["survey"]] for f in s["forms"]]
    assert strip(once) == strip(twice)


def test_eligibility_checks_by_concept_qualifier():
    spec = {"study_meta": {"protocol_number": "T"}, "forms": [{"form_id": "IE8200", "survey": [
        {"type": "select_one ny", "name": "IEINC01", "label": "Age 18+", "concept": "IEORRES", "concept_qualifier": "INCLUSION"},
        {"type": "select_one ny", "name": "IEEXC01", "label": "Pregnant", "concept": "IEORRES", "concept_qualifier": "EXCLUSION"},
        {"type": "select_one ny", "name": "IEEXC02", "label": "Prior trial", "concept": "IEORRES",
         "concept_qualifier": "EXCLUSION", "constraint": ". = 'N' or ${IEINC01} = 'N'"}],
        "choices": [{"list_name": "ny", "name": "Y", "label": "Yes"}, {"list_name": "ny", "name": "N", "label": "No"}]}]}
    rows = _run(spec)
    assert rows["IE8200.IEINC01"]["constraint"] == ". = 'Y'" and rows["IE8200.IEINC01"]["edit_checks"] == ["CDISC.IE_INCLUSION"]
    assert rows["IE8200.IEEXC01"]["constraint"] == ". = 'N'" and "Exclusion criterion present" in rows["IE8200.IEEXC01"]["constraint_message"]
    assert rows["IE8200.IEEXC02"]["constraint"] == ". = 'N' or ${IEINC01} = 'N'"   # author's constraint left alone
