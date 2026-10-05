"""The `contains` applies_when operator: a list-valued path contains a value."""
from __future__ import annotations

from conventions_engine import EntityContext, applies_when


def _study(forms):
    spec = {"forms": [{"form_id": f} for f in forms]}
    return EntityContext(kind="study", entity=spec, parent=None, spec=spec, path="")


COND = {"study.forms[*].form_id": {"contains": "SHORTFORM"}}


def test_true_when_the_list_has_the_value():
    assert applies_when.evaluate(COND, _study(["DM", "SHORTFORM", "VS"])).matched


def test_false_when_the_list_lacks_the_value():
    assert not applies_when.evaluate(COND, _study(["DM", "VS"])).matched


def test_false_when_the_path_is_not_a_list():
    ctx = _study(["DM"])
    assert not applies_when.evaluate({"study.forms[0].form_id": {"contains": "D"}}, ctx).matched   # a string, not a list
    assert not applies_when.evaluate({"study.nonexistent": {"contains": "x"}}, ctx).matched        # path missing


def test_works_alongside_other_conditions():
    both = {"all_of": [COND, {"study.forms[*].form_id": {"contains": "VS"}}]}
    assert applies_when.evaluate(both, _study(["SHORTFORM", "VS"])).matched
    assert not applies_when.evaluate(both, _study(["SHORTFORM"])).matched
