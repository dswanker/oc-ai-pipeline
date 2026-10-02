"""
Unit tests for _enforce_common_visit and _build_board_json.

Verifies that:
1. _enforce_common_visit creates one SE_COMMON_{form_id} event per
   common form and removes the shared SE_COMMON.
2. Those events are synced into timepoint_csv.rows.
3. _build_board_json produces one Common list per common form.
4. Each common form card lands in the correct list.
5. Visit-based forms are unaffected.

No OC4 call, no study creation — fully offline.
"""
import copy
import pytest
import sys

sys.path.insert(0, '/Users/danswanker/oc-ai-pipeline')


# Functions provided via conftest.py fixtures: enforce_common_visit, build_board_json


# ── Fixture spec ─────────────────────────────────────────────────────────────
@pytest.fixture
def spec():
    return {
        "study_meta": {"protocol_number": "TEST-001"},
        "forms": [
            # Visit-based forms
            {"form_id": "DOV",  "form_title": "Date of Visit",
             "repeating": False, "visits_assigned": ["SE_SCREEN"],
             "survey": [], "choices": []},
            {"form_id": "DM",   "form_title": "Demographics",
             "repeating": False, "visits_assigned": ["SE_SCREEN"],
             "survey": [], "choices": []},
            # Common forms — assigned to shared SE_COMMON
            {"form_id": "AE",   "form_title": "Adverse Event",
             "repeating": True,  "visits_assigned": ["SE_COMMON"],
             "survey": [], "choices": []},
            {"form_id": "CM",   "form_title": "Concomitant Medications",
             "repeating": True,  "visits_assigned": ["SE_COMMON"],
             "survey": [], "choices": []},
            {"form_id": "DV",   "form_title": "Protocol Deviation",
             "repeating": True,  "visits_assigned": ["SE_COMMON"],
             "survey": [], "choices": []},
            {"form_id": "HOSP", "form_title": "Hospital Admission",
             "repeating": True,  "visits_assigned": ["SE_COMMON"],
             "survey": [], "choices": []},
        ],
        "events": [
            {"event_oid": "SE_SCREEN", "event_title": "Screening",
             "event_type": "scheduled", "is_repeating": False},
            {"event_oid": "SE_COMMON", "event_title": "Common Visit",
             "event_type": "common",    "is_repeating": True},
        ],
        "timepoint_csv": {
            "rows": [
                {"event": "SE_SCREEN", "timepoint": "Screening",    "type": "scheduled"},
                {"event": "SE_COMMON", "timepoint": "Common Visit",  "type": "common"},
            ]
        },
        "form_placements": [],
        "schedule_of_events": {},
    }


# ── _enforce_common_visit tests ───────────────────────────────────────────────
class TestEnforceCommonVisit:

    def test_creates_one_event_per_common_form(self, spec, enforce_common_visit):
        s = copy.deepcopy(spec)
        result = enforce_common_visit(s)
        event_oids = [e['event_oid'] for e in result['events']]
        assert 'SE_COMMON_AE'   in event_oids
        assert 'SE_COMMON_CM'   in event_oids
        assert 'SE_COMMON_DV'   in event_oids
        assert 'SE_COMMON_HOSP' in event_oids

    def test_removes_shared_se_common_event(self, spec, enforce_common_visit):
        s = copy.deepcopy(spec)
        result = enforce_common_visit(s)
        event_oids = [e['event_oid'] for e in result['events']]
        assert 'SE_COMMON' not in event_oids

    def test_visit_based_events_untouched(self, spec, enforce_common_visit):
        s = copy.deepcopy(spec)
        result = enforce_common_visit(s)
        event_oids = [e['event_oid'] for e in result['events']]
        assert 'SE_SCREEN' in event_oids

    def test_common_forms_reassigned_to_own_event(self, spec, enforce_common_visit):
        s = copy.deepcopy(spec)
        result = enforce_common_visit(s)
        forms = {f['form_id']: f for f in result['forms']}
        assert forms['AE']['visits_assigned']   == ['SE_COMMON_AE']
        assert forms['CM']['visits_assigned']   == ['SE_COMMON_CM']
        assert forms['DV']['visits_assigned']   == ['SE_COMMON_DV']
        assert forms['HOSP']['visits_assigned'] == ['SE_COMMON_HOSP']

    def test_visit_based_forms_visits_unchanged(self, spec, enforce_common_visit):
        s = copy.deepcopy(spec)
        result = enforce_common_visit(s)
        forms = {f['form_id']: f for f in result['forms']}
        assert forms['DOV']['visits_assigned'] == ['SE_SCREEN']
        assert forms['DM']['visits_assigned']  == ['SE_SCREEN']

    def test_common_events_added_to_timepoint_csv_rows(self, spec, enforce_common_visit):
        s = copy.deepcopy(spec)
        result = enforce_common_visit(s)
        tpt_oids = [r['event'] for r in result['timepoint_csv']['rows']]
        assert 'SE_COMMON_AE'   in tpt_oids
        assert 'SE_COMMON_CM'   in tpt_oids
        assert 'SE_COMMON_DV'   in tpt_oids
        assert 'SE_COMMON_HOSP' in tpt_oids

    def test_shared_se_common_removed_from_timepoint_csv(self, spec, enforce_common_visit):
        s = copy.deepcopy(spec)
        result = enforce_common_visit(s)
        tpt_oids = [r['event'] for r in result['timepoint_csv']['rows']]
        assert 'SE_COMMON' not in tpt_oids

    def test_idempotent(self, spec, enforce_common_visit):
        """Calling twice produces same result."""
        s = copy.deepcopy(spec)
        once  = enforce_common_visit(copy.deepcopy(s))
        twice = enforce_common_visit(copy.deepcopy(once))
        once_oids  = sorted(e['event_oid'] for e in once['events'])
        twice_oids = sorted(e['event_oid'] for e in twice['events'])
        assert once_oids == twice_oids

    def test_no_common_forms_is_noop(self, enforce_common_visit):
        """Spec with no common forms: events unchanged."""
        s = {
            "forms": [
                {"form_id": "DOV", "form_title": "DOV",
                 "visits_assigned": ["SE_SCREEN"],
                 "survey": [], "choices": []},
            ],
            "events": [
                {"event_oid": "SE_SCREEN", "event_title": "Screening",
                 "event_type": "scheduled", "is_repeating": False},
            ],
            "timepoint_csv": {"rows": [
                {"event": "SE_SCREEN", "timepoint": "Screening", "type": "scheduled"}
            ]},
            "form_placements": [],
        }
        result = enforce_common_visit(s)
        assert [e['event_oid'] for e in result['events']] == ['SE_SCREEN']


# ── _build_board_json tests ───────────────────────────────────────────────────
class TestBuildBoardJson:

    @pytest.fixture
    def board(self, spec, pipeline_fns):
        """Run enforce then build on the fixture spec."""
        s = copy.deepcopy(spec)
        pipeline_fns['_enforce_common_visit'](s)
        return pipeline_fns['_build_board_json'](s)

    def test_returns_dict_with_lists_and_cards(self, board):
        assert isinstance(board, dict)
        assert 'lists' in board
        assert 'cards' in board

    def test_one_common_list_per_common_form(self, board):
        list_types = {l['eventOcoid']: l['type'] for l in board['lists']}
        assert list_types.get('SE_COMMON_AE')   == 'Common'
        assert list_types.get('SE_COMMON_CM')   == 'Common'
        assert list_types.get('SE_COMMON_DV')   == 'Common'
        assert list_types.get('SE_COMMON_HOSP') == 'Common'

    def test_no_shared_se_common_list(self, board):
        list_oids = [l['eventOcoid'] for l in board['lists']]
        assert 'SE_COMMON' not in list_oids

    def test_screening_list_is_visit_based(self, board):
        lists = {l['eventOcoid']: l for l in board['lists']}
        assert lists['SE_SCREEN']['type'] == 'Visit-Based'

    def test_ae_card_in_se_common_ae_list(self, board):
        lists = {l['eventOcoid']: l['_id'] for l in board['lists']}
        ae_card = next(
            (c for c in board['cards']
             if c.get('formOcoid') == 'F_AE'),
            None
        )
        assert ae_card is not None, "No F_AE card found"
        assert ae_card['listId'] == lists['SE_COMMON_AE'], \
            f"F_AE card is in wrong list: {ae_card['listId']}"

    def test_cm_card_in_se_common_cm_list(self, board):
        lists = {l['eventOcoid']: l['_id'] for l in board['lists']}
        cm_card = next(
            (c for c in board['cards'] if c.get('formOcoid') == 'F_CM'), None
        )
        assert cm_card is not None
        assert cm_card['listId'] == lists['SE_COMMON_CM']

    def test_dov_card_in_screening_list(self, board):
        lists = {l['eventOcoid']: l['_id'] for l in board['lists']}
        dov_card = next(
            (c for c in board['cards'] if c.get('formOcoid') == 'F_DOV'), None
        )
        assert dov_card is not None
        assert dov_card['listId'] == lists['SE_SCREEN']

    def test_all_forms_have_cards(self, spec, board, pipeline_fns):
        """Every form in the spec has at least one card in the board."""
        s = copy.deepcopy(spec)
        pipeline_fns['_enforce_common_visit'](s)
        expected_oids = {f'F_{f["form_id"]}' for f in s['forms']}
        actual_oids   = {c['formOcoid'] for c in board['cards']}
        missing = expected_oids - actual_oids
        assert not missing, f"Forms missing from board cards: {missing}"

    def test_common_cards_are_not_required(self, board):
        """Common/repeating cards should not be marked required."""
        common_oids = {'F_AE', 'F_CM', 'F_DV', 'F_HOSP'}
        for card in board['cards']:
            if card.get('formOcoid') in common_oids:
                assert not card.get('required', False), \
                    f"{card['formOcoid']} card should not be required"

    def test_visit_based_cards_are_required(self, board):
        """Visit-based cards should be required by default."""
        vb_oids = {'F_DOV', 'F_DM'}
        for card in board['cards']:
            if card.get('formOcoid') in vb_oids:
                assert card.get('required', False), \
                    f"{card['formOcoid']} card should be required"
