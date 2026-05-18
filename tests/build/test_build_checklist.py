"""Regression tests for build_checklist.run_qa_checks.

The OC-8 convention requires repeating forms to emit a "phantom" end_group
between begin repeat and end repeat (see
conventions/global/repeating_groups.form_structural_pattern.json). The
groups_balanced check must recognize this pattern and report PASS, not FAIL.

The bug this guards against: a naive open/close count flagged all 6 of CRS-136's
repeating forms (MH, AE, AESAE, CM, DV, DEVCOMP) as "2 opens, 3 closes" and
blocked Build Preview. Fix in skills/edc-builder/scripts/build_checklist.py
walks the survey with a stack so phantom end_groups (encountered when
innermost open is a repeat) are not counted as imbalance.
"""

import os
import sys
import types

# Make both copies importable as `build_checklist`.
PRIMARY = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "skills", "edc-builder", "scripts",
)
MIRROR = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "services", "study-build-trainer", "skills", "edc-builder", "scripts",
)


class _Stub(types.ModuleType):
    """Module stub that returns more stubs on any attribute / call / op.

    build_checklist.py imports openpyxl + reportlab at module scope for PDF
    and XLSX rendering, and runs module-level code like
    `PAGE_W, PAGE_H = landscape(A4)` and `1.8 * cm`. Those are Railway-only
    deps, but run_qa_checks needs neither. Stub the modules so the file
    imports under the local venv.
    """

    def __init__(self, name):
        super().__init__(name)

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        child = _Stub(f"{self.__name__}.{item}")
        sys.modules[child.__name__] = child
        return child

    def __call__(self, *args, **kwargs):
        return _Stub(self.__name__)

    def __iter__(self):
        # Supports `a, b = landscape(A4)` style tuple-unpacking at module load.
        return iter((_Stub(self.__name__ + ".0"), _Stub(self.__name__ + ".1")))

    def __mul__(self, other):  return self
    def __rmul__(self, other): return self
    def __sub__(self, other):  return self
    def __rsub__(self, other): return self
    def __add__(self, other):  return self
    def __radd__(self, other): return self


def _install_stubs():
    for name in (
        "openpyxl", "openpyxl.styles", "openpyxl.utils",
        "reportlab", "reportlab.lib", "reportlab.lib.pagesizes",
        "reportlab.lib.styles", "reportlab.lib.units", "reportlab.lib.enums",
        "reportlab.platypus",
    ):
        sys.modules.setdefault(name, _Stub(name))


def _import_checklist(scripts_dir):
    """Import build_checklist from a specific scripts directory."""
    _install_stubs()
    sys.path.insert(0, scripts_dir)
    try:
        if "build_checklist" in sys.modules:
            del sys.modules["build_checklist"]
        import build_checklist  # noqa: WPS433
        return build_checklist
    finally:
        sys.path.pop(0)


def _qa_dict(form, module):
    """Run run_qa_checks and return a {check_id: (status, note)} dict."""
    return {cid: (status, note) for cid, status, note in module.run_qa_checks(form, build_log=None)}


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _mh_like_repeating_form():
    """OC-8 repeating form shape mirroring CRS-136 MH exactly."""
    return {
        "form_id": "MH",
        "settings": {
            "form_title": "Medical History",
            "form_id": "MH",
            "version": "1.0",
            "style": "pages",
            "namespaces": "openclinica xmlns:oc",
        },
        "choices": [
            {"list_name": "yn", "name": "Y", "label": "Yes"},
            {"list_name": "yn", "name": "N", "label": "No"},
        ],
        "survey": [
            {"type": "note",          "name": "VISITHDR",  "label": "Visit"},
            {"type": "select_one yn", "name": "MHYN",      "label": "Any MH?"},
            {"type": "begin group",   "name": "MHGRP",     "label": ""},
            {"type": "text",          "name": "MHTERM",    "label": "Term"},
            {"type": "date",          "name": "MHSTDAT",   "label": "Start"},
            {"type": "select_one yn", "name": "MHONGO",    "label": "Ongoing?"},
            {"type": "date",          "name": "MHENDAT",   "label": "End"},
            {"type": "end group",     "name": "",          "label": ""},
            {"type": "begin repeat",  "name": "MH",        "label": ""},
            {"type": "end group",     "name": "",          "label": ""},   # OC-8 phantom
            {"type": "end repeat",    "name": "",          "label": ""},
        ],
    }


def _flat_non_repeating_form():
    """Plain form with one fieldset, no repeats."""
    return {
        "form_id": "DM",
        "settings": {
            "form_title": "Demographics",
            "form_id": "DM",
            "version": "1.0",
            "style": "pages",
            "namespaces": "openclinica xmlns:oc",
        },
        "choices": [],
        "survey": [
            {"type": "begin group", "name": "DMGRP", "label": ""},
            {"type": "date",        "name": "DMDAT", "label": "Date"},
            {"type": "end group",   "name": "",      "label": ""},
        ],
    }


def _genuinely_unbalanced_form():
    """An actually broken form: begin group with no end group."""
    return {
        "form_id": "BAD",
        "settings": {
            "form_title": "Bad",
            "form_id": "BAD",
            "version": "1.0",
            "style": "pages",
            "namespaces": "openclinica xmlns:oc",
        },
        "choices": [],
        "survey": [
            {"type": "begin group", "name": "BADGRP", "label": ""},
            {"type": "text",        "name": "X",      "label": "X"},
        ],
    }


def _stray_end_group_form():
    """end_group with no matching open — a real imbalance, not an OC-8 phantom."""
    return {
        "form_id": "STRAY",
        "settings": {
            "form_title": "Stray",
            "form_id": "STRAY",
            "version": "1.0",
            "style": "pages",
            "namespaces": "openclinica xmlns:oc",
        },
        "choices": [],
        "survey": [
            {"type": "text",      "name": "X", "label": "X"},
            {"type": "end group", "name": "",  "label": ""},
        ],
    }


# ── Tests, parametrized over primary + mirror copies ────────────────────────

CHECKLIST_DIRS = [PRIMARY, MIRROR]


def test_groups_balanced_passes_for_oc8_repeating_form():
    """The phantom end_group between begin repeat / end repeat must not fail."""
    form = _mh_like_repeating_form()
    for scripts_dir in CHECKLIST_DIRS:
        mod = _import_checklist(scripts_dir)
        results = _qa_dict(form, mod)
        status, note = results["groups_balanced"]
        assert status == "PASS", (
            f"[{scripts_dir}] OC-8 repeating form misreported as FAIL "
            f"(note={note!r}). The phantom end_group between begin repeat "
            f"and end repeat is required by OpenClinica and must not be "
            f"counted as imbalance."
        )
        assert "phantom" in note.lower(), (
            f"[{scripts_dir}] PASS note should mention phantom for "
            f"discoverability (got: {note!r})"
        )


def test_groups_balanced_passes_for_plain_form():
    form = _flat_non_repeating_form()
    for scripts_dir in CHECKLIST_DIRS:
        mod = _import_checklist(scripts_dir)
        status, note = _qa_dict(form, mod)["groups_balanced"]
        assert status == "PASS", f"[{scripts_dir}] plain form failed: {note!r}"


def test_groups_balanced_fails_for_unclosed_group():
    form = _genuinely_unbalanced_form()
    for scripts_dir in CHECKLIST_DIRS:
        mod = _import_checklist(scripts_dir)
        status, note = _qa_dict(form, mod)["groups_balanced"]
        assert status == "FAIL", f"[{scripts_dir}] real imbalance missed: {note!r}"
        assert "unclosed" in note.lower(), (
            f"[{scripts_dir}] FAIL note should describe the unclosed group "
            f"(got: {note!r})"
        )


def test_groups_balanced_fails_for_stray_end_group():
    form = _stray_end_group_form()
    for scripts_dir in CHECKLIST_DIRS:
        mod = _import_checklist(scripts_dir)
        status, note = _qa_dict(form, mod)["groups_balanced"]
        assert status == "FAIL", f"[{scripts_dir}] stray end_group missed: {note!r}"
        assert "extra" in note.lower(), (
            f"[{scripts_dir}] FAIL note should mention extra close (got: {note!r})"
        )


def test_repeats_balanced_unchanged():
    """begin repeat / end repeat counts are themselves balanced in OC-8 forms."""
    form = _mh_like_repeating_form()
    for scripts_dir in CHECKLIST_DIRS:
        mod = _import_checklist(scripts_dir)
        status, _ = _qa_dict(form, mod)["repeats_balanced"]
        assert status == "PASS", f"[{scripts_dir}] repeats_balanced regressed"
