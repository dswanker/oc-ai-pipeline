"""Every shipped convention and canonical list must load cleanly.

The loader skips a file that fails schema validation and only records a review flag, which silently
switches the rule off. That happened to validation.calculate_requires_readonly: a history entry used an
action word the schema does not allow. It went unnoticed because the loader only validates when the
jsonschema package is installed (the project venv has it; the production container does not).
So these tests do not depend on that package for the history check.
"""
from __future__ import annotations

import glob
import json

import pytest

from conventions_engine import _default_data_root, loader

ROOT = _default_data_root()
CONV = ROOT / "conventions"
SCHEMA = json.load(open(CONV / "schema" / "convention.schema.json"))


def _convention_files():
    out = []
    for p in sorted(glob.glob(str(CONV / "*" / "**" / "*.json"), recursive=True)):
        if "/schema/" in p or "/canonical_lists/" in p:
            continue
        out.append(p)
    return out


def _find_enum(node, key):
    """Find the enum list under a property called `key` anywhere in the schema."""
    if isinstance(node, dict):
        if key in node.get("properties", {}) and "enum" in node["properties"][key]:
            return node["properties"][key]["enum"]
        for v in node.values():
            found = _find_enum(v, key)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_enum(v, key)
            if found:
                return found
    return None


FILES = _convention_files()
ALLOWED_ACTIONS = _find_enum(SCHEMA, "action")


def test_schema_exposes_allowed_history_actions():
    assert ALLOWED_ACTIONS and "created" in ALLOWED_ACTIONS


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.split("conventions/")[-1])
def test_history_actions_are_allowed(path):
    """Dependency-free: catches the exact typo class that disabled a global rule."""
    bad = [h.get("action") for h in json.load(open(path)).get("history", []) if h.get("action") not in ALLOWED_ACTIONS]
    assert bad == [], f"history action(s) {bad} not in {ALLOWED_ACTIONS}"


def test_every_convention_file_passes_the_full_schema():
    jsonschema = pytest.importorskip("jsonschema")  # runs in the project venv, skipped where not installed
    failures = []
    for p in FILES:
        try:
            jsonschema.validate(json.load(open(p)), SCHEMA)
        except Exception as e:  # noqa: BLE001
            failures.append((p.split("conventions/")[-1], str(e).splitlines()[0][:140]))
    assert failures == []


def _errors(loaded):
    return [(e.path.split("conventions/")[-1], e.reason) for e in loaded.get("errors", [])]


def _subdirs(name):
    d = CONV / name
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


def test_global_conventions_all_load():
    assert _errors(loader.load_all(ROOT, "", "TEST_STUDY")) == []


@pytest.mark.parametrize("customer", _subdirs("customers") or [""])
def test_customer_conventions_all_load(customer):
    assert _errors(loader.load_all(ROOT, customer, "TEST_STUDY")) == []


@pytest.mark.parametrize("vendor", _subdirs("vendors") or [""])
def test_vendor_conventions_all_load(vendor):
    assert _errors(loader.load_all(ROOT, "", "TEST_STUDY", migration_source=vendor)) == []


def test_canonical_lists_all_load():
    lists, errs = loader.load_canonical_lists(ROOT)
    assert [(e.path.split("conventions/")[-1], e.reason) for e in errs] == []
    assert lists, "expected at least one canonical list"
