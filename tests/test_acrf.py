"""acrf.py: SDTM-annotated CRF. Annotations come from the SDTM Mapping Specification rows. Synthetic CDASHIG rows.
Includes the pipeline step (own output, copy of the spec, kill switch) and a pass over a spec that went through the
real conventions engine."""
import ast, asyncio, contextlib, copy, io, os
import cdisc_cdash as cd
import cdisc_concepts as cc
import acrf
import sdtm_mapping as sm
from test_cdisc_ct import STD


def _rec(dom, var, target, mapping=""):
    return {"domain": dom, "variable": var, "label": var, "question": "", "prompt": "", "instruction": "",
            "core": "HR", "type": "Char", "sdtm_target": target, "mapping": mapping}


FIELDS = {(d, v): _rec(d, v, t) for d, v, t in [
    ("AE", "AETERM", "AETERM"), ("AE", "AESTDAT", "AESTDTC"), ("AE", "AESEV", "AESEV"), ("AE", "AEYN", ""),
    ("VS", "VSORRES", "VSORRES"), ("DM", "RACEOTH", "QVAL"), ("DM", "SEX", "SEX")]}


def setup_module():
    cd._MEMO["fields"] = FIELDS


def teardown_module():
    cd._MEMO.clear()


def _spec():
    spec = {"study_meta": {"protocol_number": "ACRF-1", "study_title": "Annotated CRF test"}, "forms": [
        {"form_id": "AE", "form_title": "Adverse Events", "cdash_domain": "AE", "visits_assigned": ["SE_COMMON"],
         "choices": [{"list_name": "sev", "name": "MILD", "label": "Mild"}, {"list_name": "sev", "name": "SEVERE", "label": "Severe"},
                     {"list_name": "yn", "name": "Y", "label": "Yes"}, {"list_name": "yn", "name": "N", "label": "No"}],
         "survey": [
             {"type": "begin group", "name": "G1", "label": "Event details"},
             {"type": "note", "name": "N1", "label": "Record one event per row."},
             {"type": "select_one yn", "name": "AEYN", "label": "Any adverse events?", "required": "yes"},
             {"type": "text", "name": "AETERM", "label": "Adverse event term", "relevant": "${AEYN} = 'Y'"},
             {"type": "date", "name": "ONSET", "label": "Start date <b>&</b> more"},
             {"type": "select_one sev", "name": "AESEV", "label": "Severity"},
             {"type": "text", "name": "SITECOMMENT", "label": "Site comment"},
             {"type": "calculate", "name": "X_CF", "calculation": "1"},
             {"type": "end group", "name": "G1"}]},
        {"form_id": "VIT", "form_title": "Vitals", "visits_assigned": ["SE_V1", "SE_V2"], "choices": [],
         "survey": [{"type": "integer", "name": "BPSYS", "label": "Systolic BP"},
                    {"type": "text", "name": "DEVICE", "label": "Device used"}]},
        {"form_id": "EMPTY", "form_title": "Notes only", "survey": [{"type": "note", "name": "N", "label": "Nothing"}]},
    ]}
    cc.tag_deterministic(spec, STD, {"ONSET": "AESTDAT"})
    rows = {r["name"]: r for f in spec["forms"] for r in f["survey"]}
    cc._set(rows["BPSYS"], "VSORRES", "claude", "SYSBP")
    return spec


def _fields(model, fid):
    return {x["name"]: x for x in next(f for f in model if f["form_id"] == fid)["rows"] if x["kind"] == "field"}


def test_annotations_follow_the_mapping_spec():
    spec = _spec()
    model = acrf.build_model(spec)
    assert [f["form_id"] for f in model] == ["AE", "VIT"]                   # a form without data fields is left out
    ae, vit = _fields(model, "AE"), _fields(model, "VIT")
    assert ae["AETERM"]["annotation"] == {"kind": "mapped", "lines": ["AE.AETERM"], "tag": ""}
    assert ae["ONSET"]["annotation"]["lines"] == ["AE.AESTDTC"]              # customer-named field, alias
    assert ae["AEYN"]["annotation"]["lines"][0] == "NOT SUBMITTED" and ae["AEYN"]["annotation"]["kind"] == "not_submitted"
    assert ae["SITECOMMENT"]["annotation"] == {"kind": "supp", "lines": ["SUPPAE.QVAL candidate"], "tag": ""}
    assert vit["BPSYS"]["annotation"] == {"kind": "review", "lines": ["VS.VSORRES", "when VSTESTCD = SYSBP"],
                                          "tag": "AI - review"}
    assert vit["DEVICE"]["annotation"] == {"kind": "not_submitted", "lines": ["NOT SUBMITTED"], "tag": ""}  # no domain
    assert "X_CF" not in ae
    # every annotation is the mapping specification's row for that field
    for m in sm.build_rows(spec):
        a = (ae | vit)[m["Field"]]["annotation"]
        if m["Confidence"] != "None" and m["SDTM Variable"] and not m["SDTM Variable"].startswith("("):
            assert a["lines"][0] == f"{m['SDTM Domain']}.{m['SDTM Variable']}"
    kinds = [x["kind"] for x in next(f for f in model if f["form_id"] == "AE")["rows"]]
    assert kinds[:2] == ["section", "note"]
    assert ae["AESEV"]["options"] == ["( ) Mild  [MILD]", "( ) Severe  [SEVERE]"] and ae["AEYN"]["required"]
    assert next(f for f in model if f["form_id"] == "AE")["domains"] == ["AE"]
    assert acrf.summarize(model) == {"forms": 2, "fields": 7, "annotated": 4, "review": 1, "not_submitted": 2,
                                     "supp_candidates": 1}


def test_supplemental_qualifier_and_generic_targets():
    a = acrf.annotation({"SDTM Domain": "SUPPDM", "SDTM Variable": "QVAL", "Value-Level Detail": "QNAM = RACEOTH",
                         "Confidence": "High", "Basis": "CDASH variable name"})
    assert a == {"kind": "mapped", "lines": ["SUPPDM.QVAL", "when QNAM = RACEOTH"], "tag": ""}
    a = acrf.annotation({"SDTM Domain": "(each domain)", "SDTM Variable": "VISIT", "Confidence": "High"})
    assert a["lines"] == ["--.VISIT", "(each domain)"]
    a = acrf.annotation({"SDTM Domain": "QS", "SDTM Variable": "QSORRES", "Confidence": "Medium",
                         "Value-Level Detail": "QSTESTCD = PHQ0101; QSCAT = PHQ-9", "Basis": "QRS instrument (item order)"})
    assert a == {"kind": "review", "lines": ["QS.QSORRES", "when QSTESTCD = PHQ0101", "when QSCAT = PHQ-9"], "tag": "review"}


def test_pdf_builds_and_never_says_claude(monkeypatch):
    from reportlab import rl_config
    monkeypatch.setattr(rl_config, "pageCompression", 0)                  # page text readable in the bytes
    spec = _spec()
    before = copy.deepcopy(spec)
    out = acrf.build_files(spec)
    assert out["pdf"][:4] == b"%PDF" and len(out["pdf"]) > 3000 and spec == before
    text = out["pdf"].decode("latin-1")
    assert "Annotated CRF" in text and "AE.AETERM" in text and "AI - review" in text and "NOT SUBMITTED" in text
    assert "Claude" not in text and "claude" not in text
    # a study with 40 choices and long labels still lays out (rows wrap and split across pages)
    big = _spec()
    big["forms"][0]["choices"] += [{"list_name": "sev", "name": f"V{i}", "label": "Long option text " * 6} for i in range(40)]
    big["forms"][0]["survey"][3]["label"] = "A very long question label " * 30
    assert acrf.build_pdf(big)[:4] == b"%PDF"


def test_runs_on_a_spec_from_the_real_conventions_engine():
    from conventions_engine import apply_conventions
    spec = _spec()
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
    out = acrf.build_files(spec)
    assert out["pdf"][:4] == b"%PDF" and out["summary"]["forms"] >= 2
    assert out["summary"]["fields"] == len(sm.build_rows(spec))            # one annotation per mapping row


def _step(uploads, logs, col):
    src = open(os.path.join(os.path.dirname(__file__), "..", "pipeline.py")).read()
    node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_acrf_step")

    async def upload_file(item_id, col_id, name, data):
        uploads.append((col_id, name, data[:4]))

    async def append_log(item_id, msg):
        logs.append(msg)

    async def _tag_concepts(*a, **k):
        logs.append("tagged")

    ns = {"os": os, "asyncio": asyncio, "COL": col, "upload_file": upload_file, "append_log": append_log,
          "_tag_concepts": _tag_concepts}
    exec(compile(ast.get_source_segment(src, node), "<pipeline>", "exec"), ns)
    return ns["_acrf_step"]


def _run(coro):
    with contextlib.redirect_stdout(io.StringIO()):
        return asyncio.new_event_loop().run_until_complete(coro)


def test_pipeline_step_uploads_its_own_file_on_a_copy(monkeypatch):
    uploads, logs = [], []
    step = _step(uploads, logs, {"acrf_pdf": "file_x"})
    spec = _spec()
    spec["study_meta"]["cdisc_standards"] = {"concepts": {}, "ct_version": "none"}
    before = copy.deepcopy(spec)
    monkeypatch.setattr("cdisc_ct.load_standards", lambda v=None: None)
    _run(step(1, spec, "ACRF-1", "v2", "", ""))
    assert uploads == [("file_x", "ACRF-1_Annotated_CRF_v2.pdf", b"%PDF")] and spec == before
    assert logs[-1].startswith("Annotated CRF: 2 forms, 4 of 7 fields annotated (1 marked for review)")
    # a spec saved before concept tagging existed is tagged on the copy first
    old = _spec()
    _run(step(1, old, "P", "v1", "", ""))
    assert "tagged" in logs
    # kill switch
    uploads.clear()
    monkeypatch.setenv("ACRF_OUTPUT", "0")
    _run(step(1, spec, "P", "v1", "", ""))
    assert uploads == []
    monkeypatch.delenv("ACRF_OUTPUT")
    # column not registered, or the generator fails: logged, never raised
    step2 = _step(uploads, logs, {})
    _run(step2(1, spec, "P", "v1", "", ""))
    assert uploads == [] and logs[-1].startswith("Annotated CRF could not be generated")


def test_monday_columns_are_registered():
    src = open(os.path.join(os.path.dirname(__file__), "..", "monday_client.py")).read()
    tree = ast.parse(src)
    col = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
               and getattr(n.targets[0], "id", "") == "COL")
    assert col["acrf_pdf"] == "file_mm7y8tr7" and col["usdm_input"] == "file_mm7yx5qb"
    assert len(set(col.values())) >= len(col) - 1          # no accidental reuse (spec_xlsx_working is a known alias)
