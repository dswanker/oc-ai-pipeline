"""The /admin/* endpoints fail closed: no hard-coded fallback secret, no comparison that lets an unset ADMIN_SECRET
through, constant-time check. The repo is public, so any default value would be a published password."""
import ast
import os
import re

import pytest
from fastapi import HTTPException

_MAIN = os.path.join(os.path.dirname(__file__), "..", "main.py")
_SRC = open(_MAIN).read()


def _helpers():
    tree = ast.parse(_SRC)
    ns = {"os": os, "hmac": __import__("hmac"), "HTTPException": HTTPException}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("_admin_ok", "_require_admin"):
            exec(compile(ast.Module(body=[node], type_ignores=[]), _MAIN, "exec"), ns)
    return ns["_admin_ok"], ns["_require_admin"]


def test_no_fallback_secret_and_no_fail_open_compare():
    assert not re.search(r'environ\.get\(\s*["\']ADMIN_SECRET["\']\s*,\s*["\'][^"\']+["\']', _SRC), \
        "ADMIN_SECRET must never have a non-empty default"
    assert not re.search(r'!=\s*os\.environ\.get\(\s*["\']ADMIN_SECRET', _SRC), \
        "comparing a header to os.environ.get('ADMIN_SECRET', '') lets an empty header in when the secret is unset"


def test_every_admin_route_checks_the_secret():
    tree = ast.parse(_SRC)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        paths = [d.args[0].value for d in node.decorator_list
                 if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
                 and str(d.args[0].value).startswith("/admin")]
        if not paths:
            continue
        body = ast.get_source_segment(_SRC, node)
        assert ("_require_admin(" in body or "_admin_ok(" in body
                or ("if not admin_secret" in body and "ADMIN_SECRET" in body)), f"{paths[0]} has no admin check"


def test_helper_fails_closed(monkeypatch):
    admin_ok, require_admin = _helpers()
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    assert admin_ok("") is False and admin_ok("anything") is False
    with pytest.raises(HTTPException) as e:
        require_admin("")
    assert e.value.status_code == 503
    monkeypatch.setenv("ADMIN_SECRET", "s3cret-value")
    assert admin_ok("s3cret-value") is True and admin_ok("wrong") is False and admin_ok(None) is False
    with pytest.raises(HTTPException) as e:
        require_admin("wrong")
    assert e.value.status_code == 403
    require_admin("s3cret-value")
