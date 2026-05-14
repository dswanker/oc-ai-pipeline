"""Tests for conventions_engine.loader."""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from conventions_engine import loader, SchemaValidationError


def test_load_global_scope_finds_valid_records(repo_root_with_real_fixtures):
    records, errors = loader.load_scope(repo_root_with_real_fixtures, "global")
    ids = {r["id"] for r in records}
    assert "form_placement.safety_admin_common_visit" in ids
    assert "process.source_ambiguity" in ids
    assert "field_naming.vs_results_cdash" in ids


def test_load_filters_out_archived_status(repo_root_with_real_fixtures):
    records, _ = loader.load_scope(repo_root_with_real_fixtures, "global")
    ids = {r["id"] for r in records}
    assert "test.archived_should_be_skipped" not in ids


def test_load_filters_out_proposed_status(repo_root_with_real_fixtures):
    records, _ = loader.load_scope(repo_root_with_real_fixtures, "global")
    ids = {r["id"] for r in records}
    assert "test.proposed_should_be_skipped" not in ids


def test_load_reports_malformed_json_as_error(repo_root_with_real_fixtures):
    _, errors = loader.load_scope(repo_root_with_real_fixtures, "global")
    paths_with_errors = {e.path for e in errors}
    assert any("invalid_malformed" in p for p in paths_with_errors)


def test_load_reports_missing_required_as_error(repo_root_with_real_fixtures):
    _, errors = loader.load_scope(repo_root_with_real_fixtures, "global")
    paths_with_errors = {e.path for e in errors}
    assert any("invalid_missing_required" in p for p in paths_with_errors)


def test_load_scope_empty_when_dir_missing(tmp_path):
    # No conventions/ at all → empty result, no crash
    schema_dir = tmp_path / "conventions" / "schema"
    schema_dir.mkdir(parents=True)
    # Need a version.txt for _check_version not to crash
    (schema_dir / "version.txt").write_text("1")
    # Use a minimal valid schema file to satisfy the schema loader
    minimal_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object"
    }
    (schema_dir / "convention.schema.json").write_text(json.dumps(minimal_schema))

    records, errors = loader.load_scope(tmp_path, "global")
    assert records == []
    assert errors == []


def test_load_scope_returns_empty_for_unknown_customer(tmp_repo_root):
    records, errors = loader.load_scope(tmp_repo_root, "customer", "nonexistent_customer")
    assert records == []
    assert errors == []


def test_load_scope_returns_empty_for_unknown_study(tmp_repo_root):
    records, errors = loader.load_scope(tmp_repo_root, "study", "nonexistent_study")
    assert records == []
    assert errors == []


def test_load_scope_customer_no_id_returns_empty(tmp_repo_root):
    records, errors = loader.load_scope(tmp_repo_root, "customer", "")
    assert records == []
    assert errors == []


def test_load_scope_raises_on_unknown_scope(tmp_repo_root):
    with pytest.raises(ValueError, match="Unknown scope"):
        loader.load_scope(tmp_repo_root, "not_a_scope")


def test_load_all_returns_all_three_scope_keys(tmp_repo_root):
    result = loader.load_all(tmp_repo_root, "any_customer", "any_study")
    assert set(result.keys()) >= {"global", "customer", "study", "errors"}


def test_load_all_aggregates_errors(repo_root_with_real_fixtures):
    result = loader.load_all(repo_root_with_real_fixtures, "anyone", "any_study")
    # Both broken fixtures should surface as errors
    assert len(result["errors"]) >= 2


def test_version_mismatch_raises(tmp_path):
    # Set up a tmp store with version.txt = "99"
    schema_dir = tmp_path / "conventions" / "schema"
    schema_dir.mkdir(parents=True)
    (schema_dir / "version.txt").write_text("99")
    (schema_dir / "convention.schema.json").write_text("{}")

    with pytest.raises(SchemaValidationError, match="version"):
        loader.load_scope(tmp_path, "global")


def test_version_file_missing_is_tolerated(tmp_path):
    # version.txt absent → loader should not crash
    schema_dir = tmp_path / "conventions" / "schema"
    schema_dir.mkdir(parents=True)
    minimal_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object"
    }
    (schema_dir / "convention.schema.json").write_text(json.dumps(minimal_schema))

    records, _ = loader.load_scope(tmp_path, "global")
    assert records == []


def test_version_file_non_integer_raises(tmp_path):
    schema_dir = tmp_path / "conventions" / "schema"
    schema_dir.mkdir(parents=True)
    (schema_dir / "version.txt").write_text("not_a_number")
    (schema_dir / "convention.schema.json").write_text("{}")
    with pytest.raises(SchemaValidationError, match="integer"):
        loader.load_scope(tmp_path, "global")
