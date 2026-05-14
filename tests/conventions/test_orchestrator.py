"""Tests for conventions_engine.apply_conventions (orchestrator)."""
from __future__ import annotations
import json

import pytest

from conventions_engine import apply_conventions


def test_empty_store_is_noop(spec_with_two_forms, tmp_repo_root):
    """With no conventions on disk, apply_conventions should be a no-op
    except for guaranteeing study_meta.conventions_engine_applied exists."""
    original_forms = json.dumps(spec_with_two_forms["forms"], sort_keys=True)
    result = apply_conventions(
        spec_with_two_forms,
        study_id="TEST-001",
        customer_subdomain="anyone",
        repo_root=tmp_repo_root,
    )
    assert json.dumps(result["forms"], sort_keys=True) == original_forms
    assert result["study_meta"]["conventions_engine_applied"] == []


def test_structured_global_rule_applied(spec_with_two_forms, tmp_repo_root,
                                         make_convention):
    """Place a structured global rule on disk; verify it modifies the spec."""
    rule = make_convention(
        id="form_placement.ae_common_visit",
        scope="global", natural_key="ae_visit",
        applies_when={"form.form_id": "AE"},
        effect={"set": {"form.visits_assigned": ["SE_COMMON"]}},
    )
    global_dir = tmp_repo_root / "conventions" / "global"
    (global_dir / "rule.json").write_text(json.dumps(rule))

    result = apply_conventions(
        spec_with_two_forms, study_id="TEST-001",
        customer_subdomain="anyone", repo_root=tmp_repo_root,
    )
    ae_form = next(f for f in result["forms"] if f["form_id"] == "AE")
    assert ae_form["visits_assigned"] == ["SE_COMMON"]


def test_advisory_does_not_mutate(spec_with_two_forms, tmp_repo_root,
                                  make_convention):
    rule = make_convention(
        id="process.x",
        kind="advisory", scope="global",
        natural_key="advisory_test",
        target="form",
        description="A purely advisory rule.",
    )
    global_dir = tmp_repo_root / "conventions" / "global"
    (global_dir / "rule.json").write_text(json.dumps(rule))

    original_forms = json.dumps(spec_with_two_forms["forms"], sort_keys=True)
    result = apply_conventions(
        spec_with_two_forms, study_id="TEST-001",
        customer_subdomain="anyone", repo_root=tmp_repo_root,
    )
    assert json.dumps(result["forms"], sort_keys=True) == original_forms


def test_advisory_contributes_to_prompt_block(spec_with_two_forms, tmp_repo_root,
                                              make_convention):
    rule = make_convention(
        id="process.x",
        kind="advisory", scope="global",
        natural_key="advisory_test",
        target="form",
        description="A purely advisory rule for the prompt.",
    )
    global_dir = tmp_repo_root / "conventions" / "global"
    (global_dir / "rule.json").write_text(json.dumps(rule))

    result = apply_conventions(
        spec_with_two_forms, study_id="TEST-001",
        customer_subdomain="anyone", repo_root=tmp_repo_root,
    )
    block = result["study_meta"]["conventions_prompt_block"]
    assert "A purely advisory rule for the prompt." in block
    assert "Active Conventions" in block


def test_override_recorded_in_conventions_engine_applied(spec_with_two_forms,
                                                    tmp_repo_root, make_convention):
    """Study-scope rule overrides global; both should be visible in
    conventions_engine_applied."""
    g = make_convention(
        id="g.ae", scope="global", natural_key="ae_topic",
        applies_when={"form.form_id": "AE"},
        effect={"set": {"form.visits_assigned": ["SE_COMMON"]}},
    )
    s = make_convention(
        id="s.ae", scope="study", scope_id="TEST-001",
        natural_key="ae_topic",
        applies_when={"form.form_id": "AE"},
        effect={"set": {"form.visits_assigned": ["SE_BASELINE"]}},
    )
    (tmp_repo_root / "conventions" / "global" / "g.json").write_text(json.dumps(g))
    study_dir = tmp_repo_root / "conventions" / "studies" / "TEST-001"
    study_dir.mkdir(parents=True)
    (study_dir / "s.json").write_text(json.dumps(s))

    result = apply_conventions(
        spec_with_two_forms, study_id="TEST-001",
        customer_subdomain="anyone", repo_root=tmp_repo_root,
    )
    # Study rule's effect won
    ae_form = next(f for f in result["forms"] if f["form_id"] == "AE")
    assert ae_form["visits_assigned"] == ["SE_BASELINE"]

    # Both should be visible — winning rule recorded, overrode array populated
    entries = result["study_meta"]["conventions_engine_applied"]
    matching = [e for e in entries if e["convention_id"] == "s.ae"]
    assert len(matching) >= 1
    assert "overrode" in matching[0]
    overrode_ids = [o["convention_id"] for o in matching[0]["overrode"]]
    assert "g.ae" in overrode_ids


def test_load_errors_surface_in_review_flags(tmp_repo_root, spec_with_two_forms):
    """A malformed convention file should not crash; it should land in
    review_flags.convention_load_errors."""
    bad_path = tmp_repo_root / "conventions" / "global" / "bad.json"
    bad_path.write_text("{ this is not valid json")

    result = apply_conventions(
        spec_with_two_forms, study_id="TEST-001",
        customer_subdomain="anyone", repo_root=tmp_repo_root,
    )
    errors = result["review_flags"]["convention_load_errors"]
    assert len(errors) == 1
    assert "bad.json" in errors[0]["path"]


def test_archived_status_skipped(spec_with_two_forms, tmp_repo_root,
                                  make_convention):
    rule = make_convention(
        id="x", scope="global", status="archived",
        natural_key="archived_topic",
        applies_when={"form.form_id": "AE"},
        effect={"set": {"form.visits_assigned": ["SE_COMMON"]}},
    )
    (tmp_repo_root / "conventions" / "global" / "rule.json").write_text(json.dumps(rule))

    original_forms = json.dumps(spec_with_two_forms["forms"], sort_keys=True)
    result = apply_conventions(
        spec_with_two_forms, study_id="TEST-001",
        customer_subdomain="anyone", repo_root=tmp_repo_root,
    )
    # Archived rule must not mutate
    assert json.dumps(result["forms"], sort_keys=True) == original_forms


def test_prompt_block_empty_when_no_conventions(spec_with_two_forms, tmp_repo_root):
    result = apply_conventions(
        spec_with_two_forms, study_id="TEST-001",
        customer_subdomain="anyone", repo_root=tmp_repo_root,
    )
    assert result["study_meta"]["conventions_prompt_block"] == ""
