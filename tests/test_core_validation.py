"""core_validation.py: CDISC CORE rules through an external, offline engine command. A fake engine stands in for
cdisc-rules-engine; the real engine is exercised when CORE_ENGINE_CMD is set in the environment."""
import contextlib, io, json, os, stat, sys, textwrap
import pytest
import core_validation as cv

REPORT = {"Conformance_Details": {"CORE_Engine_Version": "0.17.1", "Standard": "USDM", "Version": "V4.0"},
          "Rules_Report": [{"core_id": "CORE-1", "status": "SUCCESS"}, {"core_id": "CORE-2", "status": "ISSUE REPORTED"},
                           {"core_id": "CORE-3", "status": "ISSUE REPORTED"}, {"core_id": "CORE-4", "status": "SKIPPED"}],
          "Issue_Summary": [{"entity": "Activity", "core_id": "CORE-2", "cdisc_rule_id": "DDF00263", "message": "No procedure.", "issues": 25},
                            {"entity": "Code", "core_id": "CORE-3", "cdisc_rule_id": "DDF00035", "message": "Code/decode.", "issues": 3},
                            {"entity": "Encounter", "core_id": "CORE-3", "cdisc_rule_id": "DDF00035", "message": "Code/decode.", "issues": 4}]}


def _fake_engine(tmp_path, body):
    p = tmp_path / "core.py"
    p.write_text(textwrap.dedent(body))
    return f"{sys.executable} {p}"


def test_summary_and_log_line():
    s = cv.summarize(REPORT)
    assert (s["rules_run"], s["rules_passed"], s["rules_with_issues"], s["rules_skipped"], s["issues"]) == (4, 1, 2, 1, 32)
    assert [(t["core_id"], t["issues"], t["entities"]) for t in s["top"]] == [("CORE-2", 25, ["Activity"]),
                                                                           ("CORE-3", 7, ["Code", "Encounter"])]
    line = cv.log_line(s)
    assert "4 rules run, 2 with findings (32 findings), 1 skipped" in line and "do not block the build" in line
    assert "CORE-2/DDF00263 x25 (Activity): No procedure." in line and "offline" in line


def test_not_configured_or_disabled_is_a_no_op(monkeypatch):
    monkeypatch.delenv("CORE_ENGINE_CMD", raising=False)
    assert cv.configured() is False and cv.run_usdm(b"{}") is None
    monkeypatch.setenv("CORE_ENGINE_CMD", "python core.py")
    monkeypatch.setenv("CORE_VALIDATE", "0")
    assert cv.configured() is False and cv.run_usdm(b"{}") is None


def test_runs_the_engine_offline_and_reads_its_report(monkeypatch, tmp_path):
    cmd = _fake_engine(tmp_path, f"""
        import json, os, sys
        a = sys.argv[1:]
        assert a[0] == "validate" and a[a.index("-s") + 1] == "usdm" and a[a.index("-v") + 1] == "4-0"
        assert a[a.index("-ca") + 1] == "resources/cache" and "CDISC_LIBRARY_API_KEY" not in os.environ
        assert json.load(open(a[a.index("-dp") + 1]))["usdmVersion"] == "4.0.0"
        json.dump({REPORT!r}, open(a[a.index("-o") + 1] + ".json", "w"))
    """)
    monkeypatch.setenv("CORE_ENGINE_CMD", cmd)
    monkeypatch.setenv("CDISC_LIBRARY_API_KEY", "must-not-be-passed")
    monkeypatch.delenv("CORE_VALIDATE", raising=False)
    monkeypatch.delenv("CORE_CACHE_DIR", raising=False)
    monkeypatch.delenv("CORE_ENGINE_DIR", raising=False)
    s = cv.run_usdm(json.dumps({"usdmVersion": "4.0.0"}).encode())
    assert s["issues"] == 32 and s["engine_version"] == "0.17.1"


def test_engine_failure_never_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("CORE_VALIDATE", raising=False)
    monkeypatch.setenv("CORE_ENGINE_CMD", _fake_engine(tmp_path, "import sys; sys.exit(3)"))
    with contextlib.redirect_stdout(io.StringIO()):
        assert cv.run_usdm(b"{}") is None
    monkeypatch.setenv("CORE_ENGINE_CMD", _fake_engine(tmp_path, "import time; time.sleep(5)"))
    with contextlib.redirect_stdout(io.StringIO()):
        assert cv.run_usdm(b"{}", timeout=0.5) is None
    monkeypatch.setenv("CORE_ENGINE_CMD", "/no/such/binary")
    with contextlib.redirect_stdout(io.StringIO()):
        assert cv.run_usdm(b"{}") is None


@pytest.mark.skipif(not os.environ.get("CORE_ENGINE_CMD"), reason="real CORE engine not configured (CORE_ENGINE_CMD)")
def test_real_engine_on_a_ddf_ra_example():
    data = open(os.path.join(os.path.dirname(__file__), "usdm", "examples", "CDISC_Pilot_Study.json"), "rb").read()
    s = cv.run_usdm(data)
    assert s and s["rules_run"] > 100 and s["standard"].startswith("USDM")
