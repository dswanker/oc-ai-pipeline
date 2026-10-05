"""Conventions follow the customer: tenant subdomain first, then the item's Client name (loader.resolve_customer)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conventions_engine import apply_conventions, loader

SCHEMA = Path(__file__).resolve().parent.parent.parent / "conventions_engine" / "conventions" / "schema"


@pytest.fixture
def root(tmp_path):
    shutil.copytree(SCHEMA, tmp_path / "conventions" / "schema")
    d = tmp_path / "conventions" / "customers" / "BioCo"
    d.mkdir(parents=True)
    conv = {"id": "customer.bioco.relabel", "title": "BioCo relabels Q", "kind": "structured", "scope": "customer", "scope_id": "BioCo",
            "status": "active", "natural_key": "bioco_relabel", "description": "Sets the label of Q, used only to prove which customer folder loaded.",
            "target": "field", "applies_when": {"field.name": "Q"}, "effect": {"set": {"field.label": "FROM BIOCO"}},
            "created_at": "2026-01-01T00:00:00Z", "created_by": "human:test", "source": "unit test"}
    (d / "relabel.json").write_text(json.dumps(conv))
    (tmp_path / "conventions" / "customers" / "OtherCo").mkdir()
    return tmp_path


def _spec():
    return {"forms": [{"form_id": "F", "survey": [{"type": "text", "name": "Q", "label": "original"}], "choices": []}]}


@pytest.mark.parametrize("subdomain,client,want", [
    ("bioco", "", "BioCo"),                 # registry spelling vs folder spelling: same customer
    ("BioCo", "Whoever", "BioCo"),          # the tenant folder wins over the client
    ("cust1", "BioCo", "BioCo"),            # rehearsal in another tenant: the client's folder is used
    ("cust1", "Bio Co", "BioCo"),           # spaces and case are ignored
    ("cust1", "", "cust1"),                 # nothing matches: unchanged, no customer conventions
    ("cust1", "Karius", "cust1"),
    ("", "", ""),
])
def test_resolve_customer(root, subdomain, client, want):
    assert loader.resolve_customer(root, subdomain, client) == want


def test_other_tenant_with_matching_client_gets_the_customers_conventions(root):
    out = apply_conventions(_spec(), study_id="S", customer_subdomain="cust1", client_name="BioCo", repo_root=root)
    assert out["forms"][0]["survey"][0]["label"] == "FROM BIOCO"


def test_other_tenant_without_a_matching_client_is_unchanged(root):
    for client in ("", "Karius"):
        out = apply_conventions(_spec(), study_id="S", customer_subdomain="cust1", client_name=client, repo_root=root)
        assert out["forms"][0]["survey"][0]["label"] == "original"


def test_existing_calls_without_client_name_behave_exactly_as_before(root):
    out = apply_conventions(_spec(), study_id="S", customer_subdomain="BioCo", repo_root=root)
    assert out["forms"][0]["survey"][0]["label"] == "FROM BIOCO"
