"""Shared fixtures for conventions_engine tests."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


# ──────────────────────────────────────────────────────────────────────
# Convention dict builder
# ──────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def make_convention():
    """Callable that builds a convention dict with sensible defaults."""
    def _build(**overrides) -> Dict[str, Any]:
        conv = {
            "id":          "test.example.rule",
            "title":       "Test rule",
            "kind":        "structured",
            "scope":       "global",
            "status":      "active",
            "natural_key": "test_natural_key",
            "description": "A test convention used by automated tests.",
            "target":      "form",
            "created_at":  _now_iso(),
            "created_by":  "system:test",
            "source":      "test:fixture",
            "applies_when": {"form.form_id": "DM"},
            "effect":       {"set": {"form.visits_assigned": ["SE_COMMON"]}},
        }
        # Special handling: advisory kind cannot have applies_when/effect
        if overrides.get("kind") == "advisory":
            conv.pop("applies_when", None)
            conv.pop("effect", None)
        # Special handling: scope == "global" cannot have scope_id
        if overrides.get("scope") in ("customer", "study") and "scope_id" not in overrides:
            overrides["scope_id"] = "test_scope_id"
        conv.update(overrides)
        return conv
    return _build


# ──────────────────────────────────────────────────────────────────────
# Spec / form / field builders
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def make_field():
    """Callable that builds an XLSForm-style survey row dict."""
    def _build(**overrides) -> Dict[str, Any]:
        f = {
            "type":      "text",
            "name":      "SUBJID",
            "label":     "Subject ID",
            "required":  True,
            "cross_form_dependencies": [],
        }
        f.update(overrides)
        return f
    return _build


@pytest.fixture
def make_form(make_field):
    """Callable that builds a form dict with a small default survey."""
    def _build(**overrides) -> Dict[str, Any]:
        form = {
            "form_id":             "DM",
            "form_name":           "Demographics",
            "has_repeating_group": False,
            "visits_assigned":     ["SE_SCREENING"],
            "survey":              [make_field()],
            "choices":             [],
            "cross_form_dependencies": [],
        }
        form.update(overrides)
        return form
    return _build


@pytest.fixture
def empty_spec():
    """A minimal spec with no forms and no convention scaffolding."""
    return {
        "study_meta": {"protocol_id": "TEST-001", "arms": []},
        "timepoint_csv": {"rows": [
            {"event": "SE_SCREENING", "timepoint": "Screening"},
            {"event": "SE_BASELINE",  "timepoint": "Baseline"},
            {"event": "SE_COMMON",    "timepoint": "Common"},
        ]},
        "forms": [],
        "review_flags": {},
    }


@pytest.fixture
def spec_with_two_forms(empty_spec, make_form):
    """Spec containing DM + AE for testing target iteration."""
    empty_spec["forms"] = [
        make_form(form_id="DM", form_name="Demographics",
                  visits_assigned=["SE_SCREENING"]),
        make_form(form_id="AE", form_name="Adverse Events",
                  visits_assigned=["SE_SCREENING"],
                  has_repeating_group=True),
    ]
    return empty_spec


# ──────────────────────────────────────────────────────────────────────
# Filesystem fixture: a real conventions/ store under tmp_path
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to tests/conventions/fixtures/ on disk."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_repo_root(tmp_path: Path) -> Path:
    """
    Create a minimal repo-root-shaped tmp directory with a valid
    conventions/schema/ baseline. Test cases lay convention files into
    conventions/global/ etc. as needed.
    """
    # Copy the real schema files into tmp so version + JSON Schema
    # validation work against this temp repo root.
    real_root = Path(__file__).resolve().parent.parent.parent
    real_schema_dir = real_root / "conventions" / "schema"

    tmp_schema = tmp_path / "conventions" / "schema"
    tmp_schema.mkdir(parents=True)
    for f in real_schema_dir.glob("*"):
        if f.is_file():
            (tmp_schema / f.name).write_bytes(f.read_bytes())

    (tmp_path / "conventions" / "global").mkdir()
    return tmp_path


@pytest.fixture
def repo_root_with_real_fixtures(tmp_path: Path, fixtures_dir: Path) -> Path:
    """
    Build a tmp repo root with the schema files plus a global/ dir
    populated from fixtures/*.json (the ones that should successfully
    load). For loader-specific tests.
    """
    real_root = Path(__file__).resolve().parent.parent.parent
    real_schema_dir = real_root / "conventions" / "schema"

    tmp_schema = tmp_path / "conventions" / "schema"
    tmp_schema.mkdir(parents=True)
    for f in real_schema_dir.glob("*"):
        if f.is_file():
            (tmp_schema / f.name).write_bytes(f.read_bytes())

    tmp_global = tmp_path / "conventions" / "global"
    tmp_global.mkdir()
    for f in fixtures_dir.glob("*.json"):
        (tmp_global / f.name).write_bytes(f.read_bytes())

    return tmp_path
