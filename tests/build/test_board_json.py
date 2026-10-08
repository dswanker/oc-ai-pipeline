"""
Unit tests for _enforce_common_visit and _build_board_json.

Verifies that:
1. _enforce_common_visit ensures SE_COMMON exists and AE/CM/DV/AESAE
   forms are assigned to it (the original working behaviour).
2. SE_COMMON is in timepoint_csv.rows.
3. _build_board_json produces one Common list (SE_COMMON) with all
   common form cards under it.
4. Visit-based forms are unaffected.
"""
import os as _os_repo
_REPO_ROOT = _os_repo.path.dirname(_os_repo.path.dirname(_os_repo.path.dirname(_os_repo.path.abspath(__file__))))
import copy
import pytest
import sys

sys.path.insert(0, _REPO_ROOT)


@pytest.fixture(scope='session')
def pipeline_fns_board():
    import ast
    src = open(_os_repo.path.join(_REPO_ROOT, 'pipeline.py')).read()
    tree = ast.parse(src)
    snippets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
                '_enforce_common_visit', '_build_board_json'):
            snippets.append(ast.get_source_segment(src, node))
    ns = {}
    exec(compile('\n\n'.join(snippets), '<pipeline>', 'exec'), ns)
    return ns


@pytest.fixture
def spec():
    return {
        "forms": [
            {"form_id": "DOV",  "form_title": "Date of Visit",
             "visits_assigned": ["SE_SCREEN"], "survey": [], "choices": []},
            {"form_id": "DM",   "form_title": "Demographics",
             "visits_assigned": ["SE_SCREEN"], "survey": [], "choices": []},
            {"form_id": "AE",   "form_title": "Adverse Event",
             "visits_assigned": ["SE_COMMON"], "survey": [], "choices": []},
            {"form_id": "CM",   "form_title": "Concomitant Medications",
             "visits_assigned": ["SE_COMMON"], "survey": [], "choices": []},
            {"form_id": "DV",   "form_title": "Protocol Deviation",
             "visits_assigned": ["SE_COMMON"], "survey": [], "choices": []},
        ],
        "events": [
            {"event_oid": "SE_SCREEN", "event_title": "Screening",
             "event_type": "scheduled", "is_repeating": False},
        ],
        "timepoint_csv": {"rows": [
            {"event": "SE_SCREEN", "timepoint": "Screening",
             "type": "scheduled", "arm": "ALL"},
        ]},
        "form_placements": [],
    }


class TestEnforceCommonVisit:

    def test_creates_se_common_event(self, spec, pipeline_fns_board):
        s = copy.deepcopy(spec)
        pipeline_fns_board['_enforce_common_visit'](s)
        oids = [e['event_oid'] for e in s['events']]
        assert 'SE_COMMON' in oids

    def test_se_common_is_repeating(self, spec, pipeline_fns_board):
        s = copy.deepcopy(spec)
        pipeline_fns_board['_enforce_common_visit'](s)
        ev = next(e for e in s['events'] if e['event_oid'] == 'SE_COMMON')
        assert ev['is_repeating'] is True
        assert ev['event_type'] == 'common'

    def test_common_forms_assigned_to_se_common(self, spec, pipeline_fns_board):
        s = copy.deepcopy(spec)
        pipeline_fns_board['_enforce_common_visit'](s)
        forms = {f['form_id']: f for f in s['forms']}
        assert forms['AE']['visits_assigned'] == ['SE_COMMON']
        assert forms['CM']['visits_assigned'] == ['SE_COMMON']
        assert forms['DV']['visits_assigned'] == ['SE_COMMON']

    def test_visit_based_forms_unchanged(self, spec, pipeline_fns_board):
        s = copy.deepcopy(spec)
        pipeline_fns_board['_enforce_common_visit'](s)
        forms = {f['form_id']: f for f in s['forms']}
        assert forms['DOV']['visits_assigned'] == ['SE_SCREEN']
        assert forms['DM']['visits_assigned']  == ['SE_SCREEN']

    def test_se_common_in_timepoint_csv(self, spec, pipeline_fns_board):
        s = copy.deepcopy(spec)
        pipeline_fns_board['_enforce_common_visit'](s)
        tpt_oids = [r['event'] for r in s['timepoint_csv']['rows']]
        assert 'SE_COMMON' in tpt_oids

    def test_idempotent(self, spec, pipeline_fns_board):
        s = copy.deepcopy(spec)
        pipeline_fns_board['_enforce_common_visit'](s)
        pipeline_fns_board['_enforce_common_visit'](s)
        oids = [e['event_oid'] for e in s['events']]
        assert oids.count('SE_COMMON') == 1

    def test_no_common_forms_is_noop(self, pipeline_fns_board):
        s = {
            "forms": [{"form_id": "DOV", "form_title": "DOV",
                        "visits_assigned": ["SE_SCREEN"],
                        "survey": [], "choices": []}],
            "events": [{"event_oid": "SE_SCREEN", "event_title": "Screening",
                        "event_type": "scheduled", "is_repeating": False}],
            "timepoint_csv": {"rows": [
                {"event": "SE_SCREEN", "timepoint": "Screening", "type": "scheduled"}
            ]},
            "form_placements": [],
        }
        pipeline_fns_board['_enforce_common_visit'](s)
        oids = [e['event_oid'] for e in s['events']]
        assert 'SE_COMMON' not in oids


class TestBuildBoardJson:

    @pytest.fixture
    def board(self, spec, pipeline_fns_board):
        s = copy.deepcopy(spec)
        pipeline_fns_board['_enforce_common_visit'](s)
        return pipeline_fns_board['_build_board_json'](s)

    def test_has_one_common_list(self, board):
        common = [l for l in board['lists'] if l['eventOcoid'] == 'SE_COMMON']
        assert len(common) == 1
        assert common[0]['type'] == 'Common'

    def test_se_screen_list_is_visit_based(self, board):
        lists = {l['eventOcoid']: l for l in board['lists']}
        assert lists['SE_SCREEN']['type'] == 'Visit-Based'

    def test_ae_card_in_se_common(self, board):
        lists = {l['eventOcoid']: l['_id'] for l in board['lists']}
        ae_card = next((c for c in board['cards']
                        if c.get('formOcoid') == 'F_AE'), None)
        assert ae_card is not None
        assert ae_card['listId'] == lists['SE_COMMON']

    def test_cm_card_in_se_common(self, board):
        lists = {l['eventOcoid']: l['_id'] for l in board['lists']}
        cm_card = next((c for c in board['cards']
                        if c.get('formOcoid') == 'F_CM'), None)
        assert cm_card is not None
        assert cm_card['listId'] == lists['SE_COMMON']

    def test_dv_card_in_se_common(self, board):
        lists = {l['eventOcoid']: l['_id'] for l in board['lists']}
        dv_card = next((c for c in board['cards']
                        if c.get('formOcoid') == 'F_DV'), None)
        assert dv_card is not None
        assert dv_card['listId'] == lists['SE_COMMON']

    def test_all_forms_have_cards(self, spec, pipeline_fns_board, board):
        s = copy.deepcopy(spec)
        pipeline_fns_board['_enforce_common_visit'](s)
        expected = {f'F_{f["form_id"]}' for f in s['forms']}
        actual = {c['formOcoid'] for c in board['cards']}
        assert expected == actual

    def test_common_cards_not_required(self, board):
        for card in board['cards']:
            if card.get('formOcoid') in ('F_AE', 'F_CM', 'F_DV'):
                assert not card.get('required', False)

    def test_visit_based_cards_required(self, board):
        for card in board['cards']:
            if card.get('formOcoid') in ('F_DOV', 'F_DM'):
                assert card.get('required', False)
