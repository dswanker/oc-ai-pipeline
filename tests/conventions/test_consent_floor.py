"""End-to-end: consent-date floor (cross-form fetch + same-form) via the real global conventions."""
import io, contextlib, copy
from conventions_engine import apply_conventions


def _spec():
    return {"study_meta": {"protocol_number": "T"}, "forms": [
        {"form_id": "ICF", "visits_assigned": ["SE_SCREEN"], "survey": [
            {"type": "date", "name": "ICFDAT", "label": "Consent date", "bind__oc_itemgroup": "ICF"}], "choices": []},
        {"form_id": "F_AE", "survey": [
            {"type": "date", "name": "ONSET", "label": "AE start", "concept": "AESTDAT", "bind__oc_itemgroup": "AE"},
            {"type": "date", "name": "MHSTDAT", "label": "History start", "bind__oc_itemgroup": "AE"}], "choices": []},
        {"form_id": "DM", "survey": [
            {"type": "date", "name": "RFICDAT", "label": "Consent", "bind__oc_itemgroup": "DM"},
            {"type": "date", "name": "VISDAT", "label": "Visit date", "bind__oc_itemgroup": "DM"},
            {"type": "date", "name": "BRTHDAT", "label": "Birth date", "bind__oc_itemgroup": "DM"}], "choices": []},
    ]}


def _run(spec):
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
    return {f"{f['form_id']}.{r['name']}": r for f in spec["forms"] for r in f["survey"]}, spec


def test_cross_form_floor_adds_fetch_and_check():
    rows, spec = _run(_spec())
    ae = rows["F_AE.ONSET"]
    assert ". >= ${ICFDAT_CF}" in ae["constraint"] and "CONSENT_FLOOR" in ae["edit_checks"]
    helper = rows["F_AE.ICFDAT_CF"]
    assert helper["type"] == "calculate" and helper["bind__oc_external"] == "clinicaldata"
    assert "FormOID='F_ICF'" in helper["calculation"] and "ItemName='ICFDAT'" in helper["calculation"]
    assert "F_F_" not in helper["calculation"]
    f_ae = next(f for f in spec["forms"] if f["form_id"] == "F_AE")
    assert f_ae["settings"]["crossform_references"] == "SE_SCREEN"


def test_history_and_birth_dates_are_not_floored():
    rows, _ = _run(_spec())
    assert "ICFDAT_CF" not in str(rows["F_AE.MHSTDAT"].get("constraint"))
    assert "RFICDAT" not in str(rows["DM.BRTHDAT"].get("constraint"))


def test_same_form_floor_uses_the_local_consent_date():
    rows, _ = _run(_spec())
    v = rows["DM.VISDAT"]
    assert ". >= ${RFICDAT}" in v["constraint"] and "ICFDAT_CF" not in v["constraint"]
    assert "DM.ICFDAT_CF" not in rows                                  # no cross-form fetch on the consent form


def test_no_check_without_a_fetchable_source():
    spec = _spec()
    spec["forms"][0]["survey"][0].pop("bind__oc_itemgroup")             # source has no item group: fetch impossible
    rows, _ = _run(spec)
    assert "ICFDAT_CF" not in str(rows["F_AE.ONSET"].get("constraint")) and "F_AE.ICFDAT_CF" not in rows


def test_idempotent():
    _, once = _run(_spec())
    twice = copy.deepcopy(once)
    _run(twice)
    strip = lambda s: [[{k: v for k, v in r.items() if k != "conventions_applied"} for r in f["survey"]] for f in s["forms"]]
    assert strip(once) == strip(twice)
