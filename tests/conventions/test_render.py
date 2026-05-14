"""Tests for conventions_engine.render."""
from __future__ import annotations

from conventions_engine import render, ResolvedConvention, Overridden


def test_render_one_advisory(make_convention):
    c = make_convention(kind="advisory", title="Doc reasoning",
                        description="Document Claude's reasoning when source is ambiguous.")
    out = render.render_one(c, [], [])
    assert "Doc reasoning" in out
    assert "advisory" in out
    assert "Document Claude's reasoning" in out


def test_render_one_includes_soft_hints(make_convention):
    c = make_convention(kind="hybrid",
                        applies_when={"form.form_id": "VS", "soft": "vital sign result"},
                        effect={"soft": "use CDASH naming"})
    out = render.render_one(c, ["vital sign result"], ["use CDASH naming"])
    assert "Apply when" in out
    assert "vital sign result" in out
    assert "use CDASH naming" in out


def test_render_prompt_block_empty():
    assert render.render_prompt_block([]) == ""


def test_render_prompt_block_with_content():
    out = render.render_prompt_block(["block A", "block B"])
    assert "Active Conventions" in out
    assert "block A" in out
    assert "block B" in out


def test_render_overrides_for_spec(make_convention):
    overrode = [Overridden(
        convention_id="g.x", scope="global", kind="structured",
        would_have_done="set form.visits_assigned",
    )]
    resolved = [ResolvedConvention(
        convention=make_convention(id="s.x", scope="study", scope_id="P"),
        overrode=overrode,
    )]
    out = render.render_overrides_for_spec(resolved)
    assert len(out) == 1
    assert out[0]["winning_id"] == "s.x"
    assert out[0]["overridden_id"] == "g.x"
