"""Every conventions call in pipeline.py must pass client_name.

A customer's conventions follow the item's Client when the tenant subdomain has no folder of its own (so a build can be rehearsed in
another tenant such as cust1). One call that forgets it - for example the aliased synchronous pre-chain pass - silently builds without
the customer's conventions, and then races the later pass that has them. This guard fails on any such call, aliased or not.
"""
import ast
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline.py"
NAMES = {"apply_conventions", "_apply_conv"}


def _calls():
    tree = ast.parse(PIPELINE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in NAMES:
                yield node.lineno, {kw.arg for kw in node.keywords}


def test_there_are_conventions_calls_to_guard():
    assert len(list(_calls())) >= 5


def test_every_conventions_call_passes_client_name():
    missing = [line for line, kws in _calls() if "client_name" not in kws]
    assert not missing, f"conventions call(s) in pipeline.py without client_name at line(s) {missing}"
