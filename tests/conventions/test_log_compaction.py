"""The conventions log and prompt block do not grow with repeated passes (they used to: PrTK05 reached 15 MB)."""
import copy
import io
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from conventions_engine import apply_conventions, record  # noqa: E402
from test_core_checks import _spec  # noqa: E402


def _run(spec):
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
    return spec


def test_repeated_passes_do_not_grow_the_log_or_prompt_block():
    spec = _run(_spec())
    n1 = len(spec["study_meta"]["conventions_engine_applied"])
    pb1 = spec["study_meta"]["conventions_prompt_block"]
    forms1 = copy.deepcopy(spec["forms"])
    _run(spec)
    _run(spec)
    assert len(spec["study_meta"]["conventions_engine_applied"]) == n1
    assert spec["study_meta"]["conventions_prompt_block"] == pb1
    assert spec["forms"] == forms1                                         # forms unaffected
    keys = [(e["convention_id"], e["applied_to"]) for e in spec["study_meta"]["conventions_engine_applied"]]
    assert len(keys) == len(set(keys))


def test_prompt_block_lists_each_guidance_once():
    pb = _run(_spec())["study_meta"]["conventions_prompt_block"]
    blocks = [b for b in pb.split("\n\n") if b.startswith("### ")]
    assert len(blocks) == len(set(blocks))


def test_bloated_log_from_old_builds_is_compacted_without_losing_mutations():
    spec = _run(_spec())
    log = spec["study_meta"]["conventions_engine_applied"]
    with_mut = next(e for e in log if e.get("mutations"))
    dup = dict(with_mut, mutations=[], effect_summary="(no-op for this entity)")   # what a later pass logged
    spec["study_meta"]["conventions_engine_applied"] = log + [copy.deepcopy(dup), copy.deepcopy(dup)]
    record.build_index(spec)
    after = spec["study_meta"]["conventions_engine_applied"]
    assert len(after) == len(log)
    kept = next(e for e in after if (e["convention_id"], e["applied_to"]) ==
                (with_mut["convention_id"], with_mut["applied_to"]))
    assert kept["mutations"] == with_mut["mutations"] and kept["effect_summary"] == with_mut["effect_summary"]
