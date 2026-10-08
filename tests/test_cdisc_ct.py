"""Unit tests for cdisc_ct.py (deterministic CDISC layer). Synthetic CT + CDASH metadata:
no network, no member files."""
import copy
import cdisc_ct as c

HDR = ("Code\tCodelist Code\tCodelist Extensible (Yes/No)\tCodelist Name\tCDISC Submission Value\t"
       "CDISC Synonym(s)\tCDISC Definition\tNCI Preferred Term\n")
ROWS = [
    ("C66742", "", "No", "No Yes Response", "NY", "", "", ""),
    ("C49487", "C66742", "", "", "N", "No", "", "No"),
    ("C49488", "C66742", "", "", "Y", "Yes", "", "Yes"),
    ("C66767", "", "No", "Action Taken with Study Treatment", "ACN", "", "", ""),
    ("C49504", "C66767", "", "", "DOSE NOT CHANGED", "", "", "Dose Not Changed"),
    ("C49503", "C66767", "", "", "DOSE REDUCED", "", "", "Dose Reduced"),
    ("C49501", "C66767", "", "", "DRUG WITHDRAWN", "", "", "Drug Withdrawn"),
    ("C66769", "", "No", "Severity/Intensity Scale for Adverse Events", "AESEV", "", "", ""),
    ("C41338", "C66769", "", "", "MILD", "", "", "Mild"),
    ("C41339", "C66769", "", "", "MODERATE", "", "", "Moderate"),
    ("C41340", "C66769", "", "", "SEVERE", "", "", "Severe"),
    ("C66731", "", "No", "Sex", "SEX", "", "", ""),
    ("C20197", "C66731", "", "", "M", "Male", "", "Male"),
    ("C16576", "C66731", "", "", "F", "Female", "", "Female"),
    ("C66729", "", "Yes", "Route of Administration Response", "ROUTE", "", "", ""),
    ("C38288", "C66729", "", "", "ORAL", "PO", "", "Oral Route of Administration"),
    ("C38276", "C66729", "", "", "INTRAVENOUS", "IV", "", "Intravenous Route of Administration"),
    ("C66727", "", "Yes", "Completion/Reason for Non-Completion", "NCOMPLT", "", "", ""),
    ("C25250", "C66727", "", "", "COMPLETED", "", "", "Completed"),
    ("C28554", "C66727", "", "", "DEATH", "", "", "Death"),
    ("C150811", "", "Yes", "Other Event", "OTHEVENT", "", "", ""),
    ("C1", "C150811", "", "", "SOMETHING ELSE", "", "", "Something Else"),
]
CT = c.CTPackage(c._parse_evs(HDR + "".join("\t".join(r) + "\n" for r in ROWS)), "test", "2026-01-01")
CDASHIG = {"AEACN": ["C66767"], "AESEV": ["C66769"], "SEX": ["C66731"], "CMROUTE": ["C66729", "C78420"],
           "DSDECOD": ["C66727", "C150811"], "AEREL": [], "RACE": ["C74457"]}
CRF_SPECS = {"AESEV": {"codelist": "C66769", "values": ["MILD", "MODERATE", "SEVERE"],
                       "displays": ["Mild", "Moderate", "Severe"], "selection": "Single"}}
STD = c.Standards(CT, CRF_SPECS, CDASHIG)


def _bind(var, values=()):
    cl, by, note = c.bind(STD, var, values)
    return (cl and cl["short_name"], by, note)


def test_cdashig_is_authoritative():
    assert _bind("AEACN") == ("ACN", "cdashig", None)
    assert _bind("AEREL", ["NOT", "PROBABLE"]) == (None, None, "sponsor_defined_per_cdashig")
    assert _bind("RACE") == (None, None, "codelist_not_in_release:C74457")
    # retired CDASH subset C78420 is not in the release -> the general codelist
    assert _bind("CMROUTE", ["PO"])[0] == "ROUTE"
    # alternative codelists: the one matching the study's values wins
    assert _bind("DSDECOD", ["COMPLETED", "DEATH"])[0] == "NCOMPLT"
    # extensible codelist, nothing overlaps -> not applied
    assert _bind("CMROUTE", ["TRANSRECTAL"]) == (None, None, "no_value_overlap:ROUTE")


def test_fallback_rules():
    assert _bind("ANYFIELDYN", ["yes", "no"])[:2] == ("NY", "yes_no_values")
    assert _bind("FREETEXTISH", ["A", "B"]) == (None, None, None)
    assert _bind("SEX2", []) == (None, None, None)


def _resolve(var, existing):
    cl, _, _ = c.bind(STD, var, [n for n, _ in existing])
    return c.resolve(STD, cl, var, existing)


def test_resolution_rules():
    r = _resolve("AESEV", [("mild", "Mild"), ("severe", "Severe")])
    assert r["rule"] == "subset_of_cdisc_recommended" and [x["name"] for x in r["choices"]] == ["MILD", "SEVERE"]
    assert _resolve("AESEV", [("1", "Grade 1")])["rule"] == "cdisc_recommended"
    r = _resolve("AEACN", [("NONE", "None"), ("REDUCE", "Reduced")])  # invented codes
    assert r["rule"] == "full_codelist" and len(r["choices"]) == 3
    assert [x["name"] for x in r["choices"]][0] == "DOSE_NOT_CHANGED"
    assert r["choices"][0]["submission_value"] == "DOSE NOT CHANGED" and r["choices"][0]["code"] == "C49504"
    r = _resolve("CMROUTE", [("PO", "Oral"), ("INH", "Inhaled")])
    assert r["rule"] == "mapped_plus_extensions"
    assert [(x["name"], x["extension"]) for x in r["choices"]] == [("ORAL", False), ("INH", True)]
    assert _resolve("AEACN", [])["rule"] == "full_codelist"


def _spec():
    return {"study_meta": {}, "forms": [{
        "form_id": "AE",
        "survey": [
            {"type": "select_one yn", "name": "AEYN", "label": "Any AEs?"},
            {"type": "select_one sev", "name": "AESEV", "relevant": "${AEYN}='yes'"},
            {"type": "select_one act", "name": "AEACN", "relevant": "${AEYN} = 'yes'"},
            {"type": "select_one rel", "name": "AEREL"},
            {"type": "select_one sexl", "name": "SEX"},
            {"type": "text", "name": "SEXOTH", "relevant": "${SEX}='other'"},
            {"type": "select_one cust", "name": "CUSTSEV"},
        ],
        "choices": [
            {"list_name": "yn", "name": "yes", "label": "Yes"}, {"list_name": "yn", "name": "no", "label": "No"},
            {"list_name": "sev", "name": "mild", "label": "Mild"},
            {"list_name": "act", "name": "REDUCE", "label": "Reduced"},
            {"list_name": "rel", "name": "NOT", "label": "Not related"},
            {"list_name": "sexl", "name": "M", "label": "Male"}, {"list_name": "sexl", "name": "other", "label": "Other"},
            {"list_name": "cust", "name": "lo", "label": "Low"},
        ]}]}


def test_apply_hierarchy_and_safety():
    spec = _spec()
    dec = {d["field"]: d for d in c.apply_to_spec(spec, STD, protected_vars={"CUSTSEV"})}
    f = spec["forms"][0]
    rows = {r["name"]: r for r in f["survey"]}
    lists = {}
    for ch in f["choices"]:
        lists.setdefault(ch["list_name"], []).append(ch["name"])
    assert dec["CUSTSEV"]["action"] == "kept" and rows["CUSTSEV"]["type"] == "select_one cust"
    assert dec["AEREL"]["action"] == "no_cdisc_codelist" and lists["rel"] == ["NOT"]
    assert rows["AEYN"]["type"] == "select_one ct_ny" and lists["ct_ny"] == ["N", "Y"]
    # skip logic follows the code change
    assert rows["AESEV"]["relevant"] == "${AEYN}='Y'" and rows["AEACN"]["relevant"] == "${AEYN} = 'Y'"
    # value still used by skip logic is kept, flagged, rather than breaking the form
    assert "other" in lists[rows["SEX"]["type"].split()[1]] and rows["SEXOTH"]["relevant"] == "${SEX}='other'"
    # replaced lists are removed when nothing references them; customer list untouched
    assert "act" not in lists and "yn" not in lists and lists["cust"] == ["lo"]
    assert all(ch.get("source") == "CDISC_CT" for ch in f["choices"] if ch["list_name"].startswith("ct_"))
    assert rows["AEACN"]["cdisc_ct"]["codelist_code"] == "C66767"


def test_apply_is_idempotent():
    once = _spec()
    c.apply_to_spec(once, STD, {"CUSTSEV"})
    twice = copy.deepcopy(once)
    c.apply_to_spec(twice, STD, {"CUSTSEV"})
    assert once == twice


def test_safe_name():
    assert c.safe_name("NOT RECOVERED/NOT RESOLVED") == "NOT_RECOVERED_NOT_RESOLVED"
    assert c.safe_name("5 MG") == "c_5_MG"


def test_retired_codelist_uses_as_collected():
    rows = ROWS + [("C128689", "", "Yes", "Race As Collected", "RACEC", "", "", ""),
                   ("C41261", "C128689", "", "", "WHITE", "", "", "White"),
                   ("C16352", "C128689", "", "", "BLACK OR AFRICAN AMERICAN", "", "", "Black or African American")]
    ct = c.CTPackage(c._parse_evs(HDR + "".join("\t".join(r) + "\n" for r in rows)), "test", "2026-01-01")
    std = c.Standards(ct, CRF_SPECS, CDASHIG)  # CDASHIG names RACE -> C74457, absent from this release
    cl, by, note = c.bind(std, "RACE", ["WHITE"])
    assert cl["short_name"] == "RACEC" and by == "cdashig" and note is None
    assert c.bind(std, "AEREL", ["NOT"])[2] == "sponsor_defined_per_cdashig"


def test_self_references_follow_code_changes():
    spec = {"forms": [{"form_id": "IE", "survey": [
        {"type": "select_one yn", "name": "IEINC01", "constraint": ". = 'yes'"},
        {"type": "select_one yn2", "name": "IEEXC01", "constraint": "selected(., 'no') or . != 'yes'"}],
        "choices": [{"list_name": "yn", "name": "yes", "label": "Yes"}, {"list_name": "yn", "name": "no", "label": "No"},
                    {"list_name": "yn2", "name": "yes", "label": "Yes"}, {"list_name": "yn2", "name": "no", "label": "No"}]}]}
    c.apply_to_spec(spec, STD)
    rows = {r["name"]: r for r in spec["forms"][0]["survey"]}
    assert rows["IEINC01"]["constraint"] == ". = 'Y'"
    assert rows["IEEXC01"]["constraint"] == "selected(., 'N') or . != 'Y'"
