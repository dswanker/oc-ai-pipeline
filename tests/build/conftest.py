"""
Shared fixtures for build tests.
Extracts functions from pipeline.py by compiling the relevant section
into a shared namespace so inner calls (e.g. _apply_crf_standards calling
_parse_crf_standards_questions) resolve correctly.
"""
import sys
import re
import pytest

_PIPELINE_PATH = '/Users/danswanker/oc-ai-pipeline/pipeline.py'

# Functions to extract — order matters: callees before callers
_FN_NAMES = [
    '_parse_crf_standards_questions',
    '_parse_crf_standards_choices',
    '_crf_variable_type_to_xlsform',
    '_apply_crf_standards',
    '_enforce_common_visit',
    '_build_board_json',
]

def _build_namespace():
    import ast
    src = open(_PIPELINE_PATH).read()
    tree = ast.parse(src)
    # collect all four function bodies in one string so they share a namespace
    snippets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _FN_NAMES:
            snippets.append(ast.get_source_segment(src, node))
    combined = '\n\n'.join(snippets)
    ns = {}
    exec(compile(combined, '<pipeline_fns>', 'exec'), ns)  # noqa: S102
    return ns

_NS = _build_namespace()


@pytest.fixture(scope='session')
def pipeline_fns():
    return _NS

@pytest.fixture(scope='session')
def parse_questions(pipeline_fns):
    return pipeline_fns['_parse_crf_standards_questions']

@pytest.fixture(scope='session')
def enforce_common_visit(pipeline_fns):
    return pipeline_fns['_enforce_common_visit']

@pytest.fixture(scope='session')
def build_board_json(pipeline_fns):
    return pipeline_fns['_build_board_json']

@pytest.fixture(scope='session')
def parse_choices(pipeline_fns):
    return pipeline_fns['_parse_crf_standards_choices']

@pytest.fixture(scope='session')
def apply_crf(pipeline_fns):
    return pipeline_fns['_apply_crf_standards']
