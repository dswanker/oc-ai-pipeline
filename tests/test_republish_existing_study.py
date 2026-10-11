"""
Republishing an existing study with updated forms (docs/REPUBLISH.md): the parts of that path that are decided in
code and can be checked without OpenClinica.
"""
import ast
import json
import os
import re
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO)
_SRC = open(os.path.join(_REPO, "pipeline.py")).read()


def _fns(*names):
    ns = {"os": os, "json": json, "re": re, "__file__": os.path.join(_REPO, "pipeline.py")}
    for node in ast.parse(_SRC).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "pipeline.py", "exec"), ns)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_BUILD_CODE_FILES" for t in node.targets):
            exec(compile(ast.Module([node], []), "pipeline.py", "exec"), ns)
    return ns


def test_the_upload_decision_covers_the_build_code_as_well_as_the_specification(monkeypatch):
    ns = _fns("_build_code_fingerprint", "_spec_upload_hash")
    spec = {"forms": [{"form_id": "A"}], "study_meta": {"protocol_number": "S-1"}}
    with_code = ns["_spec_upload_hash"](spec)
    assert with_code == ns["_spec_upload_hash"](json.loads(json.dumps(spec)))          # stable for the same input
    assert with_code != ns["_spec_upload_hash"]({**spec, "forms": []})
    ns["_build_code_fingerprint"] = lambda: "another build of the code"
    assert ns["_spec_upload_hash"](spec) != with_code            # same specification, changed build code: upload
    monkeypatch.setenv("UPLOAD_HASH_BUILD_CODE", "0")
    import hashlib
    assert ns["_spec_upload_hash"](spec) == hashlib.sha256(
        json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]         # as before


def test_the_fingerprint_reads_the_code_that_builds_forms():
    ns = _fns("_build_code_fingerprint")
    assert re.fullmatch(r"[0-9a-f]{16}", ns["_build_code_fingerprint"]())
    for rel in ns["_BUILD_CODE_FILES"]:
        assert os.path.exists(os.path.join(_REPO, rel)), rel


def test_saving_and_checking_use_the_same_hash():
    assert _SRC.count("_spec_upload_hash(struct_json)") == 3      # the definition and its two uses
    assert '_upload_record["spec_hash"] = _spec_upload_hash(struct_json)' in _SRC
    assert "_spec_hash  = _spec_upload_hash(struct_json)" in _SRC


def test_a_reused_specification_gets_the_logic_and_the_lookup_steps_before_any_chain():
    """Path R (the item's saved specification) ends before the shared pre-build steps: conventions engine, logic
    coverage, lookup CSVs. They are not inside the fresh-analysis branch, so a reused specification gets them."""
    reuse = _SRC.index("# ── Path R: re-use existing Study Spec JSON from monday")
    engine = _SRC.index("# ── Apply conventions BEFORE chains launch")
    coverage = _SRC.index("await _logic_coverage_step(item_id, struct_json)")
    lookups = _SRC.index("_lk = _lcsv.normalize_spec(struct_json, protocol_num)")
    chains = _SRC.index('"Chains A (spec files), B (summary+quote), C (build+DVS), D (OC study) starting in parallel."')
    assert reuse < engine < coverage < lookups < chains
    indent = lambda at: len(_SRC[_SRC.rfind("\n", 0, at) + 1:at]) - len(_SRC[_SRC.rfind("\n", 0, at) + 1:at].lstrip())
    assert indent(coverage) == indent(_SRC.index("await set_status(item_id, COL[\"pipeline_status\"], STATUS[\"build_pricing_running\"])"))
    assert "struct_json = _ensure_required_forms(struct_json, protocol_num," in _SRC[reuse:engine]


def test_a_reused_specification_never_reimports_the_board_or_publishes_anywhere_but_test():
    assert "fast_rerun = True" in _SRC[_SRC.index("# ── Path R: re-use existing Study Spec JSON from monday"):][:3000]
    assert 'env_name="Test"' in _SRC and "is_production=False" in _SRC
    assert len(re.findall(r"/study-versions", _SRC)) >= 1
    assert 'env_name="Production"' not in _SRC and "env_name='Production'" not in _SRC


def test_known_publish_failures_are_explained_and_others_are_left_as_they_are():
    hint = _fns("_publish_failure_hint")["_publish_failure_hint"]
    text = hint('RuntimeError: Publish failed: 400 {"message":"boardTransformError"}')
    assert "more than one form object" in text and "published study is unchanged" in text
    assert "has no form version" in hint("Publish failed: 400 No form version defined for card X")
    assert hint("Publish failed: 500 something else") == "" and hint(None) == ""
