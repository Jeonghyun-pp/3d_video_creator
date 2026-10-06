"""The decision ladder (studio/decisions.py): brief -> facts -> script -> shotlist -> look, each approved on the sheet
the user saw, bound to its parents, projected into the contracts, and gating the expensive steps."""
from pathlib import Path
import tempfile
import unittest

from studio import decisions
from studio.common import StudioError, read_json, write_json
from studio.facts import check as facts_check
from studio.project import init_project, load_project, load_shot

BRIEF = {'topic': 'how a robot arm joint turns', 'audience': 'curious adults', 'length_s': 10, 'key_message': 'gears trade speed for force',
         'subject_mode': 'schematic'}
FACTS = {'sources': [{'source_id': 'iso', 'title': 'ISO 6336', 'citation': 'ISO 6336-1:2019'}],
         'claims': [{'claim_id': 'ratio', 'text': 'a planetary stage multiplies torque', 'source_ids': ['iso']}]}
SCRIPT = {'lines': [{'line_id': 'l1', 'text': '로봇 팔 관절 안에는 기어가 있습니다.', 'illustrative': 'opening framing'},
                    {'line_id': 'l2', 'text': '유성 기어는 힘을 키웁니다.', 'claim_ids': ['ratio']}]}
SHOTS = {'shots': [{'shot_id': 'hero', 'purpose': 'the arm at work', 'line_ids': ['l1'], 'duration_s': 4, 'route_features': ['simple_hard_surface']},
                   {'shot_id': 'gears', 'purpose': 'inside the joint', 'line_ids': ['l2'], 'duration_s': 6, 'route_features': ['exact_motion']}]}


class LadderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('ladder', {'request': 'robot arm', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])

    def tearDown(self):
        self.tmp.cleanup()

    def settle(self, layer, body, words='좋아요 이대로 가요'):
        rev = decisions.propose(self.project, layer, body)['rev']
        return decisions.approve(self.project, layer, words, rev)

    def test_a_project_without_the_ladder_is_not_gated(self):
        decisions.require(self.project, 'build')
        self.assertFalse(decisions.adopted(self.project))

    def test_approve_only_the_latest_sheet_in_the_users_words(self):
        first = decisions.propose(self.project, 'brief', BRIEF)
        self.assertTrue(decisions.adopted(self.project) and Path(first['sheet']).is_file())
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'build')
        self.assertEqual(caught.exception.code, 'DECISION_UNAPPROVED')
        second = decisions.revise(self.project, 'brief', '30초 말고 10초로 해줘', ops=[{'op': 'set', 'path': '/length_s', 'value': 10}])
        with self.assertRaises(StudioError) as caught:
            decisions.approve(self.project, 'brief', '좋아요 진행해요', first['rev'])        # an older sheet
        self.assertEqual(caught.exception.code, 'DECISION_STALE')
        with self.assertRaises(StudioError):
            decisions.approve(self.project, 'brief', 'User: approved', second['rev'])       # an agent's wrapper
        decisions.approve(self.project, 'brief', '좋아요 진행해요', second['rev'])
        project = load_project(self.project)
        self.assertEqual((project['brief']['key_message'], project['output']['target_seconds']), (BRIEF['key_message'], 10))
        with self.assertRaises(StudioError):                                               # a path outside the contract
            decisions.revise(self.project, 'brief', '청중 바꿔줘', ops=[{'op': 'set', 'path': '/budget', 'value': 5}])

    def test_the_ladder_projects_shots_and_gates_until_every_layer_is_settled(self):
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('script', SCRIPT)
        result = self.settle('shotlist', SHOTS)
        self.assertEqual(result['materialized']['shots'], ['hero', 'gears'])
        self.assertEqual(result['materialized']['removed_from_timeline'], ['draft'])
        gears = load_shot(self.project, 'gears')
        self.assertEqual((gears['duration_frames'], gears['narration']['sentence_claims']), (180, [{'claim_ids': ['ratio']}]))
        self.assertEqual(facts_check(self.project), [])
        decisions.require(self.project, 'build')
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'render_look')                                  # the look is not settled
        self.assertEqual(caught.exception.code, 'DECISION_UNAPPROVED')
        self.settle('look', {'preset': 'photoreal_product'})
        self.assertEqual(load_shot(self.project, 'hero')['render']['look_preset'], 'photoreal_product')
        decisions.require(self.project, 'render_look')

        decisions.revise(self.project, 'facts', '출처 하나 더 넣어줘',                         # a parent changes ...
                         ops=[{'op': 'add', 'path': '/sources/-', 'value': {'source_id': 'kss', 'title': 'KS B ISO 6336'}}])
        state = decisions.state(self.project, 'shotlist')
        self.assertEqual(state['state'], 'stale'); self.assertIn('script', state['reason'])  # ... every layer below is stale
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'build')
        self.assertEqual(caught.exception.code, 'DECISION_STALE')

    def test_a_fill_brief_is_bound_to_the_shot_list_it_fills(self):
        from studio.fill import approve as fill_approve, propose as fill_propose, require_approved
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('script', SCRIPT); self.settle('shotlist', SHOTS)
        brief = {'status': 'proposed', 'topic': 'gears', 'request_trace': [], 'levels': [{'level_id': 'L1', 'items': [
            {'item_id': 'g', 'role': 'subject', 'element': 'winch', 'layout': 'cluster', 'count': 1, 'why': 'the gears', 'source': 'agent'}]}]}
        draft = self.project / 'fill.json'; write_json(draft, brief)
        fill_propose(self.project, 'gears', draft); fill_approve(self.project, 'gears', '좋아요 이대로')
        require_approved(load_shot(self.project, 'gears'), self.project)
        decisions.revise(self.project, 'shotlist', '두 번째 컷을 7초로', ops=[{'op': 'set', 'path': '/shots/1/duration_s', 'value': 7}])
        with self.assertRaises(StudioError) as caught:
            require_approved(load_shot(self.project, 'gears'), self.project)
        self.assertEqual(caught.exception.code, 'FILL_BRIEF_STALE')

    def test_a_hand_edit_after_approval_is_drift(self):
        self.settle('brief', BRIEF); self.settle('facts', FACTS)
        project = read_json(self.project / 'project.json'); project['brief']['key_message'] = 'something else'
        write_json(self.project / 'project.json', project)
        self.assertEqual(decisions.drift(self.project, 'brief'), ['project.brief.key_message'])

    def test_deleting_the_ladder_does_not_turn_the_gates_off(self):
        import shutil
        decisions.propose(self.project, 'brief', BRIEF)
        self.assertIn('decision_ladder', load_project(self.project))
        (self.project / 'decisions' / 'ladder.json').unlink()
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'build')
        self.assertEqual(caught.exception.code, 'DECISION_DRIFT')
        shutil.rmtree(self.project / 'decisions')     # the whole folder: project.json still remembers
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'render')
        self.assertEqual(caught.exception.code, 'DECISION_DRIFT')
        self.assertIn('ladder_missing', decisions.status(self.project))

    def test_lint_refuses_numbers_said_without_a_source(self):
        self.settle('brief', {**BRIEF, 'subject_mode': 'specific_real'})
        report = decisions.propose(self.project, 'facts', {**FACTS, 'claims': [{'claim_id': 'r', 'text': 'ratio 4.5', 'source_ids': ['iso']}]})
        self.assertTrue(any('2 independent sources' in e for e in report['errors']))
        self.settle('facts', FACTS)
        report = decisions.propose(self.project, 'script', {'lines': [{'line_id': 'l1', 'text': '기어비는 4.5입니다.', 'illustrative': 'x y z'}]})
        self.assertTrue(any('number said aloud' in e for e in report['errors']))
        with self.assertRaises(StudioError) as caught:
            decisions.approve(self.project, 'script', '좋아요 진행', report['rev'])
        self.assertEqual(caught.exception.code, 'DECISION_INVALID')

    def test_adopt_drafts_every_layer_from_an_existing_project(self):
        from studio.project import from_example
        target = Path(self.tmp.name) / 'samsung'
        from_example('samsung_cutaway', target)
        adopted = decisions.adopt(target)
        self.assertEqual(adopted['adopted'], ['brief', 'facts', 'script', 'shotlist'])
        self.assertEqual({r['layer']: r['state'] for r in decisions.status(target)['layers']}['shotlist'], 'proposed')



class DecisionEditPathTest(unittest.TestCase):
    def test_bad_path_is_a_studio_error(self):
        from studio.common import StudioError
        from studio.decisions import apply_ops
        with self.assertRaises(StudioError) as caught:
            apply_ops({'topic': 'x'}, [{'op': 'set', 'path': '/beats/0/text', 'value': 'y'}])
        self.assertEqual(caught.exception.code, 'INPUT_INVALID')


if __name__ == '__main__':
    unittest.main()
