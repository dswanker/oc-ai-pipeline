"""End-to-end: CORE-000034 death consistency (cross-form fetch + same-form)."""
import io, contextlib, copy
from conventions_engine import apply_conventions

DS_CHOICES = [{"list_name": "ds", "name": "COMPLETED", "label": "Completed"},
              {"list_name": "ds", "name": "DIED", "label": "Death", "cdisc_submission_value": "DEATH"}]


def _spec(death_on_ds=False, ds_choices=DS_CHOICES):
    ds_survey = [{"type": "select_one ds", "name": "DSDECOD", "label": "Status", "bind__oc_itemgroup": "DS"},
                 {"type": "date", "name": "DSSTDAT", "label": "Disposition date", "bind__oc_itemgroup": "DS"}]
    forms = [{"form_id": "DS", "survey": ds_survey, "choices": list(ds_choices)}]
    if death_on_ds:
        ds_survey.append({"type": "date", "name": "DTHDAT", "label": "Date of death", "bind__oc_itemgroup": "DS"})
    else:
        forms.append({"form_id": "DD", "visits_assigned": ["SE_COMMON"], "survey": [
            {"type": "date", "name": "DEATH_DT", "label": "Date of death", "concept": "DTHDAT", "bind__oc_itemgroup": "DD"}],
            "choices": []})
    return {"study_meta": {"protocol_number": "T"}, "forms": forms}


def _run(spec):
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
    return {f"{f['form_id']}.{r['name']}": r for f in spec["forms"] for r in f["survey"]}


def test_cross_form_death_date_check_uses_list_code():
    rows = _run(_spec())
    ds = rows["DS.DSSTDAT"]
    assert "${DSDECOD} != 'DIED' or ${DTHDAT_CF} = '' or . = ${DTHDAT_CF}" in ds["constraint"]
    assert "CORE-000034" in ds["edit_checks"]
    assert "ItemName='DEATH_DT'" in rows["DS.DTHDAT_CF"]["calculation"]


def test_same_form_death_date_compared_directly():
    rows = _run(_spec(death_on_ds=True))
    ds = rows["DS.DSSTDAT"]
    assert "${DSDECOD} != 'DIED' or ${DTHDAT} = '' or . = ${DTHDAT}" in ds["constraint"]
    assert "DS.DTHDAT_CF" not in rows


def test_no_death_option_no_check():
    rows = _run(_spec(ds_choices=DS_CHOICES[:1]))
    assert "DS.DTHDAT_CF" not in rows                                  # no orphan cross-form fetch
    assert "CORE-000034" not in (rows["DS.DSSTDAT"].get("edit_checks") or [])


def test_idempotent():
    once = _spec(); _run(once)
    twice = copy.deepcopy(once); _run(twice)
    strip = lambda s: [[{k: v for k, v in r.items() if k != "conventions_applied"} for r in f["survey"]] for f in s["forms"]]
    assert strip(once) == strip(twice)
