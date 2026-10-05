"""Behavior of the global convention validation.calculate_requires_readonly.

OC rejects a visible (non-calculate) field that has a calculation but is not read-only, and rejects a
type=calculate field that IS read-only. The convention must therefore add readonly=yes to the first kind
and leave the second kind (and fields with a trigger) alone.
"""
from __future__ import annotations

import copy

import pytest

from conventions_engine import apply_conventions


def _run(row):
    spec = {"study_meta": {"protocol_number": "T"},
            "forms": [{"form_id": "T", "choices": [], "survey": [copy.deepcopy(row)]}]}
    out = apply_conventions(spec, study_id="T", customer_subdomain="")
    return out["forms"][0]["survey"][0], out


GAP = pytest.mark.xfail(
    reason="Known gap, owner's decision: the rule skips a row that has NO 'readonly' key at all "
           "(not_equals on a missing path is false). Real spec rows carry the key, so this has not shown up in practice.",
    strict=False)


@pytest.mark.parametrize("row", [
    pytest.param({"type": "integer", "name": "AGE", "calculation": "1+1"}, marks=GAP),           # neither key present
    {"type": "integer", "name": "AGE", "calculation": "1+1", "trigger": "", "readonly": ""},
    {"type": "integer", "name": "AGE", "calculation": "1+1", "readonly": ""},                    # trigger key absent
    pytest.param({"type": "integer", "name": "AGE", "calculation": "1+1", "trigger": ""}, marks=GAP),  # readonly key absent
    {"type": "text", "name": "TXT", "calculation": "${A}", "readonly": ""},
    pytest.param({"type": "date", "name": "DT", "calculation": "today()"}, marks=GAP),
])
def test_visible_calculation_gets_readonly(row):
    r, out = _run(row)
    assert r["readonly"] == "yes"
    assert out["review_flags"]["calculate_readonly_enforced"]


def test_calculate_type_is_left_alone():
    r, out = _run({"type": "calculate", "name": "BG", "calculation": "1+1"})
    assert not r.get("readonly")
    assert not out.get("review_flags", {}).get("calculate_readonly_enforced")


def test_row_with_a_trigger_is_left_alone():
    r, _ = _run({"type": "text", "name": "TRG", "calculation": "1+1", "trigger": "${A}"})
    assert not r.get("readonly")


def test_already_readonly_is_not_flagged():
    r, out = _run({"type": "text", "name": "OK", "calculation": "1+1", "readonly": "yes"})
    assert r["readonly"] == "yes"
    assert not out.get("review_flags", {}).get("calculate_readonly_enforced")


def test_no_calculation_is_left_alone():
    r, _ = _run({"type": "text", "name": "PLAIN"})
    assert not r.get("readonly")
