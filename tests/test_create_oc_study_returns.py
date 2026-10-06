"""create_oc_study must always return its result dict.

When the OpenClinica login session was rejected, the function once did a bare `return`. The caller then indexed into None and the run died with
"'NoneType' object is not subscriptable", which hid the actionable "SSO session expired - re-capture required" message. A bare return anywhere
in the function (outside nested helpers) now fails this test; the caller also fails the chain on result["session_expired"].
"""
import ast
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline.py"


def _own_returns(func):
    out, stack = [], list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue                                   # returns inside nested helpers belong to them
        if isinstance(node, ast.Return):
            out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _func():
    tree = ast.parse(PIPELINE.read_text())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_oc_study")


def test_create_oc_study_has_returns_to_check():
    assert len(_own_returns(_func())) >= 2


def test_create_oc_study_never_returns_none():
    bare = [r.lineno for r in _own_returns(_func()) if r.value is None or (isinstance(r.value, ast.Constant) and r.value.value is None)]
    assert not bare, f"create_oc_study returns None at line(s) {bare}; the caller indexes into the result"


def test_caller_fails_the_chain_on_session_expired():
    src = PIPELINE.read_text()
    assert 'result.get("session_expired")' in src and 'raise RuntimeError(result["session_expired"])' in src
