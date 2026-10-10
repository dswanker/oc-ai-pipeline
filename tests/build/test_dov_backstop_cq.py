"""
The required-forms backstop honours the customer's CQ answer wherever it runs.

Run of 2026-10-10: the item's CQ "Do you collect Date of Visit (DOV) at scheduled visits?" was No, yet the log said
"[spec-backstop] Injected missing DOV form". On a reused Study Specification the backstop ran before the run had
read the CQ answers (customer_conventions was still its empty placeholder), so it injected DOV as if unanswered.
"""
import ast
import contextlib
import io
import os
import types

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _pipeline_fns():
    """The backstop functions compiled out of pipeline.py (as tests/build/conftest.py does), so the test does
    not need the whole pipeline's imports."""
    src = open(os.path.join(_REPO, "pipeline.py")).read()
    want = {"_strip_cq_prefix", "_extract_customer_conventions", "_backstop_conventions", "_ensure_required_forms"}
    parts = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            parts.append(ast.get_source_segment(src, node))
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", "").startswith("CQ_PREFIX") for t in node.targets):
            parts.append(ast.get_source_segment(src, node))
    ns = {"os": os}
    exec(compile("from __future__ import annotations\n" + "\n\n".join(parts), "<pipeline_backstop>", "exec"), ns)  # noqa: S102
    return types.SimpleNamespace(**{k: ns[k] for k in want})


pipeline = _pipeline_fns()

_Q = "Do you collect Date of Visit (DOV) at scheduled visits?"


def _cols(answer):
    return {"color_x1": {"id": "color_x1", "title": f"CQ {_Q}", "text": answer},
            "text_x2": {"id": "text_x2", "title": "Protocol Number", "text": "T-1"}}


def _backstop(cols, conventions):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        spec = pipeline._ensure_required_forms({"forms": [{"form_id": "DM"}]}, "T-1",
                                               pipeline._backstop_conventions(cols, conventions))
    return [f["form_id"] for f in spec["forms"]], out.getvalue()


def test_cq_no_suppresses_dov_when_the_run_has_not_read_the_answers_yet():
    forms, log = _backstop(_cols("No"), {})
    assert forms == ["DM"]
    assert "DOV suppressed by CQ answer" in log and "Injected missing DOV form" not in log


def test_cq_yes_or_unanswered_still_injects_dov():
    for answer in ("Yes", "", "Not Yet Answered"):
        forms, log = _backstop(_cols(answer), {})
        assert forms == ["DOV", "DM"], answer
        assert "Injected missing DOV form" in log


def test_answers_the_run_already_holds_are_used_as_they_are():
    forms, _ = _backstop(_cols("No"), {_Q: "Yes"})
    assert forms == ["DOV", "DM"]


def test_a_form_already_in_the_specification_is_never_removed():
    with contextlib.redirect_stdout(io.StringIO()):
        spec = pipeline._ensure_required_forms({"forms": [{"form_id": "DOV"}, {"form_id": "DM"}]}, "T-1",
                                               pipeline._backstop_conventions(_cols("No"), {}))
    assert [f["form_id"] for f in spec["forms"]] == ["DOV", "DM"]


def test_kill_switch_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setenv("DOV_BACKSTOP_HONOURS_CQ", "0")
    forms, log = _backstop(_cols("No"), {})
    assert forms == ["DOV", "DM"] and "Injected missing DOV form" in log


def test_every_backstop_call_in_the_run_reads_the_cq_answers():
    src = open(os.path.join(_REPO, "pipeline.py")).read()
    calls = src.count("struct_json = _ensure_required_forms(struct_json, protocol_num,")
    assert calls >= 4
    assert src.count("_backstop_conventions(cols, customer_conventions))") == calls
