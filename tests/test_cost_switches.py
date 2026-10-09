"""Cost switches and economy runs. PROTOCOL_DOC_FIRST, PROTOCOL_CHECKS_MERGED and SPEC_INPUT_TRIM are OFF by
default, and off means the requests are exactly what they were before the switches existed (snapshots recorded from
that commit: tests/standards/cost_switch_snapshots.json). Economy runs send the same request through the Message
Batches API (mocked client here). Synthetic data only."""
import asyncio
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import claude_client as cc
import protocol_basis as pb
import protocol_checks as pc
import protocol_forms as pf
import spec_trim
import standards_match as sm

_spec = importlib.util.spec_from_file_location("cost_cases", os.path.join(ROOT, "tests", "standards", "cost_cases.py"))
cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cases)
SNAP = json.load(open(os.path.join(ROOT, "tests", "standards", "cost_switch_snapshots.json")))
SWITCHES = ("PROTOCOL_DOC_FIRST", "PROTOCOL_CHECKS_MERGED", "SPEC_INPUT_TRIM", "PROTOCOL_DOC_CACHE_TTL", "ECONOMY_RUNS")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setitem(sm._CDASH, "v", (None, {}, {}, set()))
    cc.set_economy(False)
    del cc.USAGE[:]
    yield
    cc.set_economy(False)


def _norm(x):
    return json.loads(json.dumps(x, sort_keys=True))


# ── switches off: today's requests ───────────────────────────────────────────────

def test_with_every_switch_off_the_requests_are_what_they_were(monkeypatch):
    # the snapshots were recorded before the cost switches; two instructions have since changed on purpose
    # (every recorded data item is an assessment; a basis passage names the form's subject), each behind its own
    # kill switch, which restores the recorded request exactly
    monkeypatch.setenv("PROTOCOL_FORMS_ALL_ITEMS", "0")
    monkeypatch.setenv("PROTOCOL_BASIS_SUBJECT", "0")
    assert not cc.doc_first_enabled() and not pc.enabled() and not spec_trim.enabled()
    now = _norm(cases.all_cases())
    assert now["client"] == SNAP["client"]
    assert set(now["builders"]) == set(SNAP["builders"])
    for name, req in SNAP["builders"].items():
        got = now["builders"][name]
        if name == "concept_tagging":       # its catalogue comes from the installed CDISC files: compare the rest
            tail = lambda r: [r[0], r[1][r[1].index("\n\nFIELDS ("):]]
            got, req = tail(got), tail(req)
        assert got == req, name


# ── A: the protocol first, cached ────────────────────────────────────────────────

def test_doc_first_moves_the_protocol_to_the_front_with_a_one_hour_cache_marker(monkeypatch):
    monkeypatch.setenv("PROTOCOL_DOC_FIRST", "1")
    got = _norm(cases.client_requests())
    for (kind, new), (kind0, old) in zip(got, SNAP["client"]):
        assert kind == kind0 and {k: v for k, v in new.items() if k != "messages"} == {k: v for k, v in old.items() if k != "messages"}
        a, b = new["messages"][0]["content"], old["messages"][0]["content"]
        docs = [x for x in b if x["type"] == "document"]
        if not docs:
            assert a == b                      # a call without the protocol is not touched
            continue
        assert a[0] == {**docs[0], "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        assert a[1] == b[0]                    # the prompt block as it was, its own cache marker included
        assert a[2:] == [x for x in b[1:] if x["type"] != "document"]      # images and extra text in the same order
    # the protocol block is byte-identical in every call that carries it: one cache entry for the run
    blocks = {json.dumps(n["messages"][0]["content"][0], sort_keys=True) for _k, n in got
              if n["messages"][0]["content"][0]["type"] == "document"}
    assert len(blocks) == 1
    monkeypatch.setenv("PROTOCOL_DOC_CACHE_TTL", "5m")
    first = cc.build_content("P", b"%PDF", "x", False)[0]
    assert first["type"] == "document" and first["cache_control"] == {"type": "ephemeral"}


def test_doc_first_checks_leaves_the_main_analysis_request_as_it_is(monkeypatch):
    monkeypatch.setenv("PROTOCOL_DOC_FIRST", "checks")
    got = _norm(cases.client_requests())
    assert got[1] == SNAP["client"][1] and got[1][0] == "beta"          # the extended-output call: today's request
    for i in (0, 2):                                                    # the other protocol calls: protocol first
        first = got[i][1]["messages"][0]["content"][0]
        assert first["type"] == "document" and first["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert got[3:] == SNAP["client"][3:]


def test_usage_is_recorded_per_call_and_priced():
    rec = {"input": 1_000_000, "output": 100_000, "cache_read": 1_000_000, "cache_write_5m": 1_000_000, "cache_write_1h": 1_000_000}
    assert cc.call_cost(rec) == 5 + 2.5 + 0.5 + 6.25 + 10
    assert cc.call_cost({**rec, "batch_id": "msgbatch_1"}) == (5 + 2.5 + 0.5 + 6.25 + 10) / 2

    class U:
        input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 10, 20, 130, 40

        class cache_creation:
            ephemeral_5m_input_tokens, ephemeral_1h_input_tokens = 4, 36

    class R:
        usage = U()
    r = cc._record_usage(R(), "PROMPT", b"pdf", 100, 0.0)
    assert (r["input"], r["output"], r["cache_read"], r["cache_write_5m"], r["cache_write_1h"]) == (10, 20, 130, 4, 36)
    assert cc.USAGE[-1] is r and r["pdf_sha"] and r["batch_id"] is None


# ── C: the same facts in fewer tokens ────────────────────────────────────────────

def test_trim_removes_markup_only_and_keeps_every_field(monkeypatch):
    before = cases.builder_requests()
    monkeypatch.setenv("SPEC_INPUT_TRIM", "1")
    after = cases.builder_requests()
    spec = cases.matched()
    for name in ("ai_edit_checks", "ai_standard_logic", "sdv_endpoints_pdf", "protocol_fields", "concept_tagging"):
        b, a = before[name], after[name]
        assert a[0] == b[0], name                           # the instructions are not touched
        assert "<" not in a[1].split("PROTOCOL TEXT")[0] and len(a[1]) < len(b[1]), name
    for f in spec["forms"]:                                 # every field a step can use is still listed
        for r in f["survey"]:
            assert f"{r['name']} |" in after["ai_edit_checks"][1] and f"{f['form_id']}.{r['name']} |" in after["sdv_endpoints_pdf"][1]
            assert f"  {r['name']} | " in after["concept_tagging"][1]
    assert "1=Mild; 2=Moderate" in after["ai_edit_checks"][1] and "has check" in after["ai_standard_logic"][1] + "has check"
    # concept tagging: the form heading once per form instead of on every line
    assert after["concept_tagging"][1].count("AEGEN (AE General; domain AE)") == 1
    assert before["concept_tagging"][1].count("AEGEN (AE General; domain AE)") == 4
    # the checks that read the protocol are not trimmed
    assert after["completeness"] == before["completeness"] and after["basis"] == before["basis"]


def test_trim_helpers(monkeypatch, capsys):
    assert spec_trim.label("<b>End  date</b>", 5) == "<b>En" and spec_trim.text(None) == "None"
    monkeypatch.setenv("SPEC_INPUT_TRIM", "1")
    assert spec_trim.label("<b>End  date</b>", 20) == "End date" and spec_trim.label("<span> </span>") == ""
    spec_trim.compare("step", lambda x: ("p", "<b>" + x + "</b>"), ("p", "x"), "x")
    assert "[spec-trim] step: 9 -> 2 characters" in capsys.readouterr().out
    assert os.environ["SPEC_INPUT_TRIM"] == "1"


def test_trim_sends_a_long_list_of_review_flags_as_its_count_and_first_entries(monkeypatch):
    flags = {"many": [f"F.X{i}: label from name" for i in range(40)], "few": ["a", "b"], "n": 3}
    assert spec_trim.flags(flags) is flags                               # off: the same object, untouched
    monkeypatch.setenv("SPEC_INPUT_TRIM", "1")
    got = spec_trim.flags(flags)
    assert got["few"] == ["a", "b"] and got["n"] == 3
    assert got["many"] == {"count": 40, "first": flags["many"][:25], "not_listed": 15}
    src = open(os.path.join(ROOT, "pipeline.py")).read()
    assert '"review_flags":  _pricing_review_flags(struct_json),' in src


# ── B: three protocol checks in one call ─────────────────────────────────────────

def test_the_merged_request_keeps_each_tasks_instructions_and_splits_the_answer():
    tasks = {"completeness": (pf.prompt(), "CHECKLIST"), "basis": (pb.prompt(), "FORMS"), "protocol_fields": (sm.ADD_PROMPT, "CANDIDATES")}
    prompt, extra = pc.build_request(tasks, None)
    for p in (sm.ADD_PROMPT, pf.prompt(), pb.prompt()):
        assert p.strip() in prompt                         # the same wording per task
    assert prompt.index("TASK 1: PROTOCOL-SPECIFIED FIELDS") < prompt.index("TASK 2: COMPLETENESS CHECKLIST") < prompt.index("TASK 3: PROTOCOL BASIS")
    assert '{"protocol_fields": <the JSON of TASK 1>, "completeness": <the JSON of TASK 2>, "basis": <the JSON of TASK 3>}' in prompt
    assert extra.index("TASK 1") < extra.index("CANDIDATES") < extra.index("TASK 2") < extra.index("CHECKLIST") < extra.index("FORMS")
    assert "PROTOCOL TEXT" not in extra and "=== PROTOCOL TEXT (for every task) ===\nabc" in pc.build_request(tasks, "abc")[1]
    two = pc.build_request({"completeness": tasks["completeness"], "basis": tasks["basis"]})[0]
    assert '{"completeness": <the JSON of TASK 1>, "basis": <the JSON of TASK 2>}' in two and "PROTOCOL-SPECIFIED" not in two
    got = pc.split_response('```json\n{"protocol_fields": {"additions": []}, "completeness": {"entries": []}, "basis": []}\n```',
                            ["protocol_fields", "completeness", "basis"])
    assert json.loads(got["protocol_fields"]) == {"additions": []} and json.loads(got["completeness"]) == {"entries": []}
    assert got["basis"] is None                            # not an object: that task makes its own call
    assert pc.split_response("no json", ["basis"]) == {"basis": None}
    merged = pc.merge_basis('{"forms": [{"form_id": "A", "why": "first"}]}', '{"forms": [{"form_id": "A"}, {"form_id": "B"}]}')
    assert [f["form_id"] for f in json.loads(merged)["forms"]] == ["A", "B"] and json.loads(merged)["forms"][0]["why"] == "first"
    assert pc.answered_forms(merged) == {"A", "B"} and pc.answered_forms("x") == set()


def test_the_basis_task_also_sees_the_standard_forms_not_used_yet():
    spec = cases.matched()
    unused = copy.deepcopy(cases.standard_ae())
    unused.update(form_oid="XTRA", title="Extra Log")
    tmp, added = pc.with_possible_forms(spec, {"forms": [cases.standard_ae(), unused]})
    assert added == ["XTRA"] and [f["form_id"] for f in tmp["forms"]] == ["AEGEN", "VS", "WID", "XTRA"]
    assert [f["form_id"] for f in spec["forms"]] == ["AEGEN", "VS", "WID"]          # the spec itself is untouched
    assert "FORM XTRA | Extra Log" in pb.build_request(tmp, cases.PROTOCOL, with_text=False)[1]


def _pipeline(monkeypatch):
    for name in ("itsdangerous", "auth_manager"):
        try:
            __import__(name)
        except Exception:
            m = types.ModuleType(name)

            class _Any:
                def __init__(self, *a, **k): pass
                def __getattr__(self, n): return _Any()
                def __call__(self, *a, **k): return _Any()
            m.__getattr__ = lambda n, _A=_Any: _A
            monkeypatch.setitem(sys.modules, name, m)
    monkeypatch.delitem(sys.modules, "pipeline", raising=False)
    monkeypatch.chdir(ROOT)
    import pipeline
    monkeypatch.setitem(sys.modules, "pipeline", pipeline)
    return pipeline


Q_HOSP = "For each adverse event the investigator must record whether the event led to hospitalisation."
Q_AE = "All adverse events will be recorded in the eCRF from consent."
Q_VS = "Systolic blood pressure will be recorded in the eCRF at every visit."
Q_WID = "The number of widgets will be recorded in the eCRF at screening."
A_FIELDS = {"additions": [{"form_id": "AEGEN", "field": "AEHOSP", "covered_by": None, "quote": Q_HOSP},
                          {"form_id": "AEGEN", "field": "AEOTH", "covered_by": None, "quote": None}]}
_a = lambda name, dom, quote: {"name": name, "cdash_domain": dom, "section": "9", "quote": quote, "kind": "record",
                               "events": [], "log": False}
A_COMPLETE = {"entries": [], "extra": [_a("Adverse events", "AE", Q_AE), _a("Vital signs", "VS", Q_VS), _a("Widget count", None, Q_WID)]}
_b = lambda fid, quote, fields: {"form_id": fid, "quotes": [{"text": quote, "section": "9", "kind": "record"}], "fields": fields, "why": "w"}
A_BASIS = {"forms": [_b("AEGEN", Q_AE, ["AETERM"]), _b("VS", Q_VS, ["SYSBP"]), _b("WID", Q_WID, ["WIDN"])]}


def _run_steps(pl, monkeypatch, answer):
    calls, logs = [], []

    async def fake_call(prompt, pdf_bytes=None, extra_text=None, **kw):
        calls.append({"prompt": prompt, "extra": extra_text, "pdf": pdf_bytes, **kw})
        return answer(prompt, extra_text)

    async def fake_log(item, msg):
        logs.append(msg)
    monkeypatch.setattr(pl, "call_claude", fake_call)
    monkeypatch.setattr(pl, "append_log", fake_log)
    monkeypatch.setattr(pl, "_extract_customer_conventions", lambda cols: [])
    monkeypatch.setattr(pl, "_basis_required_ids", lambda cols: ["DOV"])
    proto = b"%%DOCX_TEXT%%" + cases.PROTOCOL.encode()
    src = {"forms": [cases.standard_ae()], "files": [], "fingerprint": "f1"}

    async def run():
        with contextlib.redirect_stdout(io.StringIO()):
            out = await pl._standards_match_step("t", cases.study(), src, proto)
            return await pl._post_match_forms_step("t", out, src, proto, [], [], {}, fresh=True)
    return asyncio.run(run()), calls, logs


def test_merged_off_three_calls_with_todays_requests(monkeypatch):
    pl = _pipeline(monkeypatch)

    def answer(prompt, extra):
        return json.dumps(A_FIELDS if prompt == sm.ADD_PROMPT else A_COMPLETE if prompt == pf.prompt() else A_BASIS)
    out, calls, _logs = _run_steps(pl, monkeypatch, answer)
    assert [c["prompt"] for c in calls] == [sm.ADD_PROMPT, pf.prompt(), pb.prompt()]
    assert [c["max_tokens"] for c in calls] == [8000, 16000, 8000]
    assert calls[0]["extra"] == SNAP["builders"]["protocol_fields"][1]          # byte-identical to before the switch
    assert sm.state(out)["additions"]["status"] == "done" and [a["field"] for a in sm.state(out)["additions"]["added"]] == ["AEHOSP"]
    assert pb.state(out)["status"] == "done" and [f["form_id"] for f in out["forms"]] == ["AEGEN", "VS", "WID"]


def test_merged_on_one_call_and_each_section_goes_through_its_own_validator(monkeypatch):
    monkeypatch.setenv("PROTOCOL_CHECKS_MERGED", "1")
    pl = _pipeline(monkeypatch)

    def answer(prompt, extra):
        return json.dumps({"protocol_fields": A_FIELDS, "completeness": A_COMPLETE, "basis": A_BASIS})
    out, calls, logs = _run_steps(pl, monkeypatch, answer)
    assert len(calls) == 1 and calls[0]["max_tokens"] == pc.MAX_TOKENS
    p, e = calls[0]["prompt"], calls[0]["extra"]
    assert all(x.strip() in p for x in (sm.ADD_PROMPT, pf.prompt(), pb.prompt()))
    assert e.count(cases.PROTOCOL) == 1 and "CANDIDATES BY FORM:" in e and "CHECKLIST (id" in e and "FORM WID | Widget Count" in e
    # the same outcome as the three separate calls
    st = sm.state(out)["additions"]
    assert st["status"] == "done" and [a["field"] for a in st["added"]] == ["AEHOSP"] and st["not_specified"] == [{"form_id": "AEGEN", "field": "AEOTH"}]
    assert pf.state(out)["status"] == "done" and len(pf.state(out)["assessments"]) == 3
    assert pb.state(out)["status"] == "done" and all(r["supported"] for r in pb.state(out)["forms"])
    assert any("one AI call for 3 checks" in l for l in logs)


def test_merged_on_a_missing_section_or_a_failed_call_falls_back_to_the_own_call(monkeypatch):
    monkeypatch.setenv("PROTOCOL_CHECKS_MERGED", "1")
    pl = _pipeline(monkeypatch)

    def answer(prompt, extra):                  # the merged answer lacks the basis section
        if prompt == pb.prompt():
            return json.dumps(A_BASIS)
        return json.dumps({"protocol_fields": A_FIELDS, "completeness": A_COMPLETE})
    out, calls, _l = _run_steps(pl, monkeypatch, answer)
    assert len(calls) == 2 and calls[1]["prompt"] == pb.prompt() and pb.state(out)["status"] == "done"

    def broken(prompt, extra):                  # the merged call fails: every check makes its own call
        if "TASK 1:" in prompt:
            raise RuntimeError("boom")
        return json.dumps(A_FIELDS if prompt == sm.ADD_PROMPT else A_COMPLETE if prompt == pf.prompt() else A_BASIS)
    out, calls, _l = _run_steps(pl, monkeypatch, broken)
    assert [c["prompt"] for c in calls[1:]] == [sm.ADD_PROMPT, pf.prompt(), pb.prompt()]
    assert sm.state(out)["additions"]["status"] == "done" and pb.state(out)["status"] == "done"


def test_merged_on_a_form_the_completeness_check_adds_gets_a_follow_up_basis_call(monkeypatch):
    monkeypatch.setenv("PROTOCOL_CHECKS_MERGED", "1")
    pl = _pipeline(monkeypatch)
    q_inc = "participants must be 18 years of age or older."
    extra_a = dict(_a("Eligibility review", "IE", Q_AE), name="Eligibility review")

    def answer(prompt, extra):
        if prompt == pb.prompt():
            added = [l.split(" | ")[0][5:] for l in extra.split("\n") if l.startswith("FORM ")]
            return json.dumps({"forms": [_b(fid, Q_AE, []) for fid in added]})
        return json.dumps({"protocol_fields": A_FIELDS, "completeness": dict(A_COMPLETE, extra=A_COMPLETE["extra"] + [extra_a]),
                           "basis": A_BASIS})
    out, calls, _l = _run_steps(pl, monkeypatch, answer)
    new = [f["form_id"] for f in out["forms"] if f["form_id"] not in ("AEGEN", "VS", "WID")]
    removed = [r["form_id"] for r in pb.state(out).get("removed") or []]
    assert len(new) + len(removed) == 1                     # the completeness check added one form
    assert len(calls) == 2 and calls[1]["prompt"] == pb.prompt()
    asked = [l for l in calls[1]["extra"].split("\n") if l.startswith("FORM ")]
    assert len(asked) == 1 and asked[0].startswith(f"FORM {(new + removed)[0]} |")      # only the added form is judged again


# ── Economy runs ─────────────────────────────────────────────────────────────────

class _Msg:
    def __init__(self, text="{\"ok\": 1}"):
        self.content = [types.SimpleNamespace(text=text)]
        self.usage = types.SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                           cache_creation_input_tokens=0, cache_creation=None)


class _Batches:
    def __init__(self, log, outcome="succeeded", polls=2):
        self.log, self.outcome, self.polls, self.n = log, outcome, polls, 0

    async def create(self, **kw):
        self.log.append(("create", kw))
        return types.SimpleNamespace(id="msgbatch_T1", processing_status="in_progress")

    async def retrieve(self, batch_id, **kw):
        self.n += 1
        self.log.append(("retrieve", batch_id))
        return types.SimpleNamespace(id=batch_id, processing_status="ended" if self.n >= self.polls else "in_progress")

    async def results(self, batch_id, **kw):
        self.log.append(("results", batch_id))
        outcome = self.outcome

        async def gen():
            yield types.SimpleNamespace(custom_id="call-1", result=types.SimpleNamespace(
                type=outcome, message=_Msg() if outcome == "succeeded" else None))
        return gen()

    async def cancel(self, batch_id, **kw):
        self.log.append(("cancel", batch_id))


def _economy_client(monkeypatch, outcome="succeeded", polls=2):
    log = []

    class Client(cases.FakeClient):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.messages.batches = _Batches(log, outcome, polls)
            self.beta.messages.batches = _Batches(log, outcome, polls)
    cases.FakeClient.calls = []
    monkeypatch.setattr(cc.anthropic, "AsyncAnthropic", Client)

    async def no_sleep(_s):
        return None
    monkeypatch.setattr(cc.asyncio, "sleep", no_sleep)
    return log


def _call(**kw):
    async def run():
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            text = await cc.call_claude("PROMPT", pdf_bytes=b"%PDF synthetic", extra_text="x", **kw)
        return text, buf.getvalue()
    return run


def test_economy_sends_the_same_request_as_a_batch_of_one_and_parses_the_result(monkeypatch):
    log = _economy_client(monkeypatch)

    async def run():
        cc.set_economy(True)
        return await _call(max_tokens=2000, cache_prompt=False)()
    text, out = asyncio.run(run())
    assert text == "{\"ok\": 1}" and cases.FakeClient.calls == []        # no normal request was made
    kind, kw = log[0]
    params = kw["requests"][0]["params"]
    assert kind == "create" and len(kw["requests"]) == 1 and "betas" not in kw
    assert params == {"model": cc.MODEL, "max_tokens": 2000,
                      "messages": [{"role": "user", "content": cc.build_content("PROMPT", b"%PDF synthetic", "x", False)}]}
    assert [k for k, _ in log] == ["create", "retrieve", "retrieve", "results"]
    assert "batch msgbatch_T1 created" in out and "batch msgbatch_T1 done after" in out
    rec = cc.USAGE[-1]
    assert rec["batch_id"] == "msgbatch_T1" and rec["batch_wait"] is not None
    assert rec["usd"] == round((1000 * 5 + 100 * 25) / 1e6 / 2, 4)       # half price


def test_economy_keeps_the_extended_output_beta_and_falls_back_once_when_the_batch_fails(monkeypatch):
    log = _economy_client(monkeypatch)

    async def ext():
        cc.set_economy(True)
        return await _call(max_tokens=96000, extended_output=True)()
    asyncio.run(ext())
    assert log[0][1]["betas"] == ["output-128k-2025-02-19"] and log[0][1]["requests"][0]["params"]["max_tokens"] == 96000
    for outcome in ("errored", "expired"):
        log = _economy_client(monkeypatch, outcome)

        async def run():
            cc.set_economy(True)
            return await _call(max_tokens=2000)()
        text, out = asyncio.run(run())
        assert text == "{}" and len(cases.FakeClient.calls) == 1          # retried normally, once
        assert f"batch failed (RuntimeError: batch msgbatch_T1 result: {outcome})" in out and "retrying this call normally, once" in out
        assert cc.USAGE == [] or cc.USAGE[-1]["batch_id"] is None


def test_economy_is_per_run_has_a_kill_switch_and_logs_progress(monkeypatch):
    log = _economy_client(monkeypatch)
    asyncio.run(_call(max_tokens=2000)())                                 # not an economy run: a normal call
    assert log == [] and len(cases.FakeClient.calls) == 1
    monkeypatch.setenv("ECONOMY_RUNS", "0")

    async def off():
        cc.set_economy(True)
        assert not cc.economy_active()
        return await _call(max_tokens=2000)()
    asyncio.run(off())
    assert log == [] and len(cases.FakeClient.calls) == 2
    monkeypatch.delenv("ECONOMY_RUNS")
    assert cc.batch_unsupported({"max_tokens": 0}) == "max_tokens 0" and cc.batch_unsupported({"max_tokens": 5, "speed": "fast"}) == "speed"
    assert cc.batch_unsupported({"max_tokens": 96000, "messages": []}) == ""
    # a progress line at most every 10 minutes while a batch is pending
    log = _economy_client(monkeypatch, polls=4)
    lines = []

    async def progress():
        async def monday(msg):
            lines.append(msg)
        cc.set_economy(True, monday)
        cc._ECONOMY.get()["last_progress"] -= 601
        return await _call(max_tokens=2000)()
    asyncio.run(progress())
    assert len(lines) == 1 and lines[0].startswith("Economy run: waiting for a batched AI call")


def test_the_pipeline_reads_the_economy_column_and_writes_the_start_line(monkeypatch):
    pl = _pipeline(monkeypatch)
    assert pl.ECONOMY_RUN_COLUMN == "color_mm7z4qsc"
    yes, no = {"color_mm7z4qsc": {"text": "Yes"}}, {"color_mm7z4qsc": {"text": "No"}}
    assert pl._economy_requested(yes) and not pl._economy_requested(no) and not pl._economy_requested({}) \
        and not pl._economy_requested({"color_mm7z4qsc": {"text": None}})
    logs = []

    async def fake_log(item, msg):
        logs.append(msg)
    monkeypatch.setattr(pl, "append_log", fake_log)

    async def run(cols):
        with contextlib.redirect_stdout(io.StringIO()):
            await pl._economy_run_step("t", cols)
        return cc.economy_active()
    assert asyncio.run(run(yes)) is True
    assert logs == ["Economy run: AI calls are batched at half price; this run may take longer"]
    assert asyncio.run(run(no)) is False and len(logs) == 1
    monkeypatch.setenv("ECONOMY_RUNS", "0")
    assert asyncio.run(run(yes)) is False and "switched off (ECONOMY_RUNS=0)" in logs[-1]
    src = open(os.path.join(ROOT, "pipeline.py")).read()
    assert src.index("cols         = {c[\"id\"]: c for c in item[\"column_values\"]}") < src.index("await _economy_run_step(item_id, cols)") < src.index("protocol_num = cols.get")
