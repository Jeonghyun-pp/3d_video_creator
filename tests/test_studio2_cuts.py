"""The cuts layer (studio2/cuts.py): a row of v1's ladder bound to the script, settled like any layer, whose lint holds
what follows from what a cut writes - labels name shown parts, scale steps chain, a state change has a differing last
picture, the cut is as long as its lines - and whose values are rows of the cutaway grammar."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import decisions
from studio.common import StudioError, read_json
from studio.project import init_project, load_project, load_shot
from tests import studio2_fixture as fx


def lint(path, body):
    decisions.validate_body('cuts', body)
    return decisions.lint(path, 'cuts', body)


class CutsLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = fx.project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_cuts_is_a_ladder_layer_bound_to_the_script(self):
        self.assertEqual(decisions.LAYERS['cuts'], ('script',))
        self.assertEqual(decisions.parents_of(self.path, 'cuts'), ['script'])

    def test_a_clean_body_settles_and_becomes_shots(self):
        report = lint(self.path, fx.body())
        self.assertEqual(report['errors'], [])
        got = decisions.write(self.path, 'cuts', fx.body(), agent_note=fx.TEST_NOTE)
        self.assertEqual(got['status'], 'decided')
        self.assertEqual(decisions.state(self.path, 'cuts')['state'], 'decided')
        self.assertEqual([e['shot_id'] for e in load_project(self.path)['shots']], ['c1', 'c2'])
        shot = load_shot(self.path, 'c2')
        self.assertEqual(shot['route']['mode'], 'composite')
        self.assertEqual(shot['narration']['text'], fx.LINES[1]['text'])
        self.assertEqual(shot['direction']['show_how']['point'], 'air falls from the ceiling through the floor')
        self.assertEqual(shot['duration_frames'], 120)
        self.assertIn('the centre chip goes dark', shot['direction']['action'])

    def test_a_script_change_makes_the_cuts_stale(self):
        decisions.write(self.path, 'cuts', fx.body(), agent_note=fx.TEST_NOTE)
        env = read_json(self.path / 'decisions' / 'script.json')
        env['body']['lines'][0]['text'] = '공기는 천장에서 바닥으로 떨어집니다.'
        env['decided']['body_sha256'] = decisions.body_sha256(env['body'])
        (self.path / 'decisions' / 'script.json').write_text(json.dumps(env, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(decisions.state(self.path, 'cuts')['state'], 'stale')
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.path, 'cut_pictures')
        self.assertEqual(caught.exception.code, 'DECISION_STALE')

    def test_pictures_wait_for_the_cuts(self):
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.path, 'cut_pictures')
        self.assertEqual(caught.exception.code, 'DECISION_MISSING')

    def test_a_v1_project_is_not_asked_for_cuts(self):
        other = Path(init_project('v1p', {'request': 'v1'}, self.tmp.name)['project_path'])
        self.assertNotIn('cuts', [r['layer'] for r in decisions.status(other)['layers']])

    def test_the_sheet_shows_each_cut(self):
        got = decisions.write(self.path, 'cuts', fx.body(), agent_note=fx.TEST_NOTE)
        sheet = Path(got['sheet']).read_text(encoding='utf-8')
        self.assertIn('| c2 |', sheet)
        self.assertIn('필터', sheet)

    # --- the lints ---------------------------------------------------------------------------------------------
    def errors(self, body):
        return lint(self.path, body)['errors']

    def test_a_label_names_a_part_the_cut_shows(self):
        body = fx.body()
        body['cuts'][0]['cutaway']['labels'].append({'part': 'worker', 'word': '사람'})
        self.assertTrue(any(e.startswith('CUT_LABEL_UNSHOWN: c1') for e in self.errors(body)), self.errors(body))

    def test_scale_steps_chain(self):
        body = fx.body()
        body['cuts'][1]['cutaway']['scale_step'] = 'wider'   # room -> part is closer
        self.assertTrue(any(e.startswith('SCALE_STEP_BROKEN: c2') for e in self.errors(body)))
        body = fx.body()
        body['cuts'][0]['cutaway']['scale_step'] = 'same'
        self.assertTrue(any(e.startswith('SCALE_STEP_BROKEN: c1') for e in self.errors(body)))

    def test_a_state_change_has_a_last_picture_that_differs(self):
        body = fx.body()
        body['cuts'][1]['frames']['last'] = deepcopy(body['cuts'][1]['frames']['first'])
        self.assertTrue(any(e.startswith('CHANGE_FRAMES_SAME: c2') for e in self.errors(body)))
        del body['cuts'][1]['frames']['last']
        self.assertTrue(any(e.startswith('CHANGE_FRAMES_SAME: c2') for e in self.errors(body)))

    def test_a_moving_thing_has_its_path(self):
        body = fx.body()
        body['cuts'][0]['change'] = {'kind': 'move', 'what': 'a speck falls', 'at': [0.2, 0.8]}
        self.assertTrue(any(e.startswith('CHANGE_UNDECIDED: c1') for e in self.errors(body)))

    def test_the_cut_is_as_long_as_its_lines(self):
        body = fx.body()
        body['cuts'][0]['duration_s'] = 1.2
        self.assertTrue(any(e.startswith('SHOT_TOO_SHORT: c1') for e in self.errors(body)), self.errors(body))

    def test_every_line_is_in_a_cut(self):
        body = fx.body()
        body['cuts'][1]['line_ids'] = []
        self.assertIn('script line l02 is in no cut', self.errors(body))

    def test_a_value_is_a_row_of_the_grammar(self):
        body = fx.body()
        body['cuts'][0]['cutaway']['opens'] = 'peel_away'
        self.assertTrue(any(e.startswith("CUT_VALUE_UNKNOWN: c1 opens 'peel_away'") for e in self.errors(body)))
        from studio2 import grammar   # a new kind of opening is a row of the table, nothing else (N+1)
        table = grammar.load()
        table['fields']['opens']['values']['peel_away'] = {'means': 'the skin peeled back', 'prompt': 'its skin peeled back'}
        with mock.patch('studio2.grammar.load', return_value=table):
            self.assertFalse(any('peel_away' in e for e in self.errors(body)))

    def test_a_proof_names_a_row_of_its_cut(self):
        body = fx.body()
        body['cuts'][0]['show_how']['proof'] = ['label/floor']   # floor is shown but not labelled
        self.assertTrue(any(e.startswith('SHOW_HOW_UNDECIDED: c1') for e in self.errors(body)))
        body = fx.body()
        body['point']['proof']['rows'] = ['c9/change']
        self.assertTrue(any(e.startswith('POINT_UNDECIDED') for e in self.errors(body)))

    def test_a_way_is_a_row_of_the_showing_ways(self):
        body = fx.body()
        body['cuts'][0]['show_how']['way']['id'] = 'fly_around'
        self.assertTrue(any(e.startswith('WAY_UNKNOWN: c1') for e in self.errors(body)))

    def test_a_push_says_how_much(self):
        body = fx.body()
        del body['cuts'][0]['cutaway']['camera']['amount']
        self.assertTrue(any(e.startswith('CAMERA_UNDECIDED: c1') for e in self.errors(body)))

    def test_errors_keep_it_a_draft(self):
        body = fx.body()
        body['cuts'][0]['cutaway']['opens'] = 'peel_away'
        got = decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        self.assertEqual(got['status'], 'proposed')
        self.assertEqual(decisions.state(self.path, 'cuts')['state'], 'proposed')

    # --- the plan reader --------------------------------------------------------------------------------------
    def test_the_plan_reader_reads_the_cuts_and_its_findings_come_back(self):
        from studio2 import cuts
        decisions.write(self.path, 'cuts', fx.body(), agent_note=fx.TEST_NOTE)
        seen = {}

        def reader(images, prompt, schema, target):
            seen['prompt'] = prompt
            from studio.common import write_json
            write_json(target, {'answer': {'cuts': [
                {'shot_id': 'c1', 'derived': True, 'derived_why': 'the section shows the air', 'stronger': {'better': False, 'what': '', 'why': ''}, 'note': ''},
                {'shot_id': 'c2', 'derived': False, 'derived_why': 'the push is decoration', 'stronger': {'better': False, 'what': '', 'why': ''}, 'note': ''}],
                'proof_carried': {'holds': True, 'why': 'yes'}, 'opening_done': {'holds': True, 'why': 'yes'}}})
        got = cuts.read(self.path, reader=reader)
        self.assertEqual(got['findings'], {'c2': ['PLAN_NOT_DERIVED']})
        self.assertIn('flow/0: filtered air drawn blue', seen['prompt'])
        self.assertIn('the chips lit, the centre one dark', seen['prompt'])
        body = fx.body()   # a moving thing is on screen as the reader is told it: drawn from the start, moved in its window
        body['cuts'][0]['change'] = {'kind': 'move', 'what': 'the box is lifted', 'at': [0.3, 0.7],
                                     'element': {'what': 'a wafer box', 'path': 'from the shelf up to the rail', 'drawn': 'sprite'}}
        words = '\n'.join(cuts.chain(cuts.as_plan(body)))
        self.assertIn('a wafer box: in the picture from the first frame at the start of its path, moved from the shelf up to the rail', words)
        body['cuts'][0]['change']['element'].update({'drawn': 'speck', 'at': [0.1, 0.4]})
        self.assertIn('a wafer box: appears and moves from the shelf up to the rail during [0.1, 0.4] of the cut', '\n'.join(cuts.chain(cuts.as_plan(body))))
        warnings = lint(self.path, fx.body())['warnings']
        self.assertTrue(any(w.startswith('PLAN_NOT_DERIVED: c2') for w in warnings), warnings)
        body = fx.body()   # answered: departed with why
        body['cuts'][1]['departs'] = [{'item': 'plan_not_derived', 'why': 'the push lands on the chip before it goes dark'}]
        self.assertFalse(any(w.startswith('PLAN_NOT_DERIVED: c2') for w in lint(self.path, body)['warnings']))


if __name__ == '__main__':
    unittest.main()
