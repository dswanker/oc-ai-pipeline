"""In an economy run the trainer quick analysis runs at normal speed (not batched), so the main analysis always gets
the trainer's examples; every other call of the run stays batched."""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import claude_client as cc  # noqa: E402
import trainer_integration as ti  # noqa: E402


def test_quick_analysis_is_not_batched_in_an_economy_run(monkeypatch):
    monkeypatch.setenv("ECONOMY_RUNS", "1")
    seen = {}

    async def fake_call(prompt, **kw):
        seen["economy_inside"] = cc.economy_active()
        return '{"sponsor": "S", "indication": "I", "phase": "2"}'

    async def run():
        cc.set_economy(True)
        assert cc.economy_active()
        await ti.run_protocol_analysis_quick(b"%PDF-1.4 x", call_claude_fn=fake_call, extract_json_fn=lambda t: {"sponsor": "S"})
        seen["economy_after"] = cc.economy_active()

    asyncio.run(run())
    assert seen["economy_inside"] is False      # the quick analysis ran at normal speed
    assert seen["economy_after"] is True        # the rest of the run is still an economy run
