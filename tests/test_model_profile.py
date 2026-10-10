"""MODEL_PROFILE: the model per pipeline step (claude_client.model_for). Default = today's model and request."""
import asyncio
import json
import os
import re
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_client as cc

OPUS47, OPUS55, SONNET55 = "claude-opus-4-7", "claude-opus-5-5", "claude-sonnet-5-5"
DECIDING = {"main_analysis", "completeness", "protocol_fields", "basis", "merged_checks", "schedule_question",
            "standards_close_call"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("MODEL_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cc.set_economy(False)
    del cc.USAGE[:]


def test_default_profile_is_todays_model_for_every_step(monkeypatch):
    assert cc.model_profile() == "default"
    assert set(cc.profile_map("default").values()) == {OPUS47} and cc.model_for(None) == OPUS47
    monkeypatch.setenv("MODEL_PROFILE", "something else")
    assert cc.model_profile() == "default"


def test_opus55_moves_every_opus_call_and_mixed55_splits_by_what_the_step_decides():
    assert set(cc.profile_map("opus55").values()) == {OPUS55}
    mixed = cc.profile_map("mixed55")
    assert {s for s, m in mixed.items() if m == OPUS55} == DECIDING == set(cc.DECIDING_STEPS)
    assert {m for s, m in mixed.items() if s not in DECIDING} == {SONNET55}
    for step in ("quick_analysis", "concept_tagging", "standard_logic", "sdv_endpoint", "ai_edit_checks",
                 "pricing_summary", "dvs_added_checks", "dvs_build_json", "dvs_translate"):
        assert mixed[step] == SONNET55


def test_a_call_that_is_not_on_opus_today_keeps_its_model_and_one_step_can_be_overridden(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILE", "mixed55")
    assert cc.model_for("anything", today="claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert cc.model_for(None) == SONNET55            # a call without a step name is one of the other calls
    monkeypatch.setenv("MODEL_STEP_PRICING_SUMMARY", OPUS47)
    assert cc.model_for("pricing_summary") == OPUS47 and cc.model_for("ai_edit_checks") == SONNET55


def test_every_call_site_of_the_pipeline_names_a_known_step():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "pipeline.py")).read()
    named = re.findall(r'await _ai\("(\w+)"', src)
    assert len(named) == 16 and set(named) <= set(cc.STEPS)
    assert "await call_claude(" not in src.replace("return await call_claude(*args, **kwargs)", "")
    assert '_cc.STEP.set("quick_analysis")' in open(os.path.join(root, "trainer_integration.py")).read()


def test_request_for_a_55_model_drops_the_old_beta_and_leaves_room_for_thinking(monkeypatch):
    base = {"model": OPUS47, "max_tokens": 96000, "messages": [{"role": "user", "content": [{"type": "text", "text": "p"}]}]}
    kw, betas = cc.adapt_request(base, ["output-128k-2025-02-19"], OPUS55)
    assert betas is None and kw["model"] == OPUS55 and kw["max_tokens"] == 128000 and kw["messages"] is base["messages"]
    assert "output_config" not in kw and "thinking" not in kw and "temperature" not in kw
    assert cc.adapt_request(dict(base, max_tokens=120000), None, SONNET55)[0]["max_tokens"] == 128000
    assert cc.adapt_request(dict(base, max_tokens=2000), None, SONNET55)[0]["max_tokens"] == 18000
    monkeypatch.setenv("MODEL_EFFORT", "high")
    assert cc.adapt_request(base, None, OPUS55)[0]["output_config"] == {"effort": "high"}
    # an override to another model of today's generation changes the id only
    kw, betas = cc.adapt_request(base, ["output-128k-2025-02-19"], "claude-opus-4-8")
    assert kw == dict(base, model="claude-opus-4-8") and betas == ["output-128k-2025-02-19"]


def test_reply_text_skips_thinking_blocks_and_a_refusal_is_an_error():
    block = lambda t, **k: types.SimpleNamespace(type=t, **k)
    r = types.SimpleNamespace(stop_reason="end_turn", content=[block("thinking", thinking=""), block("text", text='{"a": 1}')])
    assert cc.response_text(r) == '{"a": 1}'
    with pytest.raises(RuntimeError):
        cc.response_text(types.SimpleNamespace(stop_reason="refusal", content=[]))
    with pytest.raises(RuntimeError):
        cc.response_text(types.SimpleNamespace(stop_reason="max_tokens", content=[block("thinking", thinking="")]))


def test_cost_uses_the_models_own_prices():
    rec = {"input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000, "cache_write_1h": 1_000_000, "cache_write_5m": 1_000_000}
    assert cc.call_cost(rec) == 46.75
    assert cc.call_cost(dict(rec, model=OPUS55)) == 37.2
    assert cc.call_cost(dict(rec, model=SONNET55)) == 18.6
    assert cc.call_cost(dict(rec, model=SONNET55, batch_id="b")) == 9.3


class _Stream:
    def __init__(self, sent, kwargs):
        sent.append(kwargs)
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        blocks = [types.SimpleNamespace(type="text", text="{}")]
        if cc.is_55(self.kwargs["model"]):
            blocks.insert(0, types.SimpleNamespace(type="thinking", thinking=""))
        return types.SimpleNamespace(content=blocks, stop_reason="end_turn", usage=types.SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None))


def _client(sent):
    msgs = types.SimpleNamespace(stream=lambda **kw: _Stream(sent, dict(kw, _api="messages")))
    beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: _Stream(sent, dict(kw, _api="beta"))))
    return types.SimpleNamespace(messages=msgs, beta=beta)


def _call(monkeypatch, step, **kw):
    sent = []
    monkeypatch.setattr(cc.anthropic, "AsyncAnthropic", lambda **k: _client(sent))

    async def go():
        token = cc.STEP.set(step)
        try:
            return await cc.call_claude("PROMPT", **kw)
        finally:
            cc.STEP.reset(token)
    asyncio.run(go())
    return sent[0]


def test_call_claude_sends_todays_request_by_default_and_the_profiles_request_otherwise(monkeypatch):
    today = _call(monkeypatch, "main_analysis", pdf_bytes=b"%PDF", extended_output=True, max_tokens=96000)
    assert today["model"] == OPUS47 and today["betas"] == ["output-128k-2025-02-19"] and today["_api"] == "beta"
    assert today["max_tokens"] == 96000 and "model" not in cc.USAGE[-1]
    monkeypatch.setenv("MODEL_PROFILE", "mixed55")
    main = _call(monkeypatch, "main_analysis", pdf_bytes=b"%PDF", extended_output=True, max_tokens=96000)
    assert main["model"] == OPUS55 and "betas" not in main and main["_api"] == "messages" and main["max_tokens"] == 128000
    assert json.dumps(main["messages"]) == json.dumps(today["messages"])      # content and prompt untouched
    assert cc.USAGE[-1]["model"] == OPUS55 and cc.USAGE[-1]["step_name"] == "main_analysis"
    other = _call(monkeypatch, "pricing_summary", extra_text="x", max_tokens=64000)
    assert other["model"] == SONNET55 and other["max_tokens"] == 85333 and cc.USAGE[-1]["model"] == SONNET55
    monkeypatch.setenv("MODEL_PROFILE", "opus55")
    assert _call(monkeypatch, "pricing_summary", extra_text="x")["model"] == OPUS55
