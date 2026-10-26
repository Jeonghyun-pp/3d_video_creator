"""v1's reel-flow rules read from v2's cuts (studio2/flow.py): the same rules (rule 1 the hook, rule 11 the key change,
rule 7 the pace, rule 3 the same picture), each fact read from the cuts layer's own fields - what changes and when, which
change is the key one, what the cut opens, its camera - and, made, from its located pictures. Why (2026-10-26, the dev
preview): v1's reel flow read fields v2 does not write and said HOOK_WEAK, KEY_CHANGE_UNPLANNED and REEL_FLOW_UNMEASURED
of a reel whose opening cut moves at 1.3 s.

The rhythm: lines run across cuts (v1's line spans), and the cuts layer reads the reference's pace as v1's shot list does."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from studio import decisions
from studio.common import read_json, write_json
from studio2.project import grammar_library
from tests import studio2_fixture as fx
from tests.decisions_fixture import settle

STORY = {'thread': {'kind': 'moving', 'carrier': 'the air', 'carrier_ids': ['air']}, 'beats': [{'story_beat_id': 'b1', 'function': 'hook', 'what_happens': 'air falls'},
                   {'story_beat_id': 'b2', 'function': 'climax', 'what_happens': 'a chip dies'}]}
LINES = [{**l, 'story_beat_id': b} for l, b in zip(fx.LINES, ('b1', 'b2'))]


def said(got):
    return got['errors'] + got['warnings']


def codes(lines):
    return [l.split(':', 1)[0] for l in lines]


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = fx.project(self.root)
        settle(self.path, ['script', 'story'], {'script': {'lines': deepcopy(LINES)}, 'story': deepcopy(STORY)})
        data = read_json(self.path / 'project.json')   # the cutaway reference, as studio2 project init chooses it
        data['pattern_library'] = grammar_library()
        write_json(self.path / 'project.json', data)

    def tearDown(self):
        self.tmp.cleanup()

    def body(self, key=True, hook=True):
        """c1 (the hook, 2.5 s) starts line l01, which runs on into c2; c2 says l02 after it, its chip dying the key change."""
        body = fx.body()
        body['cuts'][0]['role'] = 'hook'
        body['cuts'][0]['duration_s'] = 2.5
        body['cuts'][1]['line_ids'] = ['l01', 'l02']
        body['cuts'][1]['line_at_s'] = [0, 1.4]
        body['cuts'][1]['duration_s'] = 4.6
        body['cuts'][1]['departs'] = [{'item': 'establish_gap', 'why': 'a test cut stays close on purpose'}]
        if hook:
            body['cuts'][0]['change'] = {'kind': 'move', 'what': 'a dust speck falls with the air', 'at': [0.1, 0.6], 'parts': ['floor'],
                                         'element': {'what': 'a speck', 'path': 'down with the air', 'drawn': 'speck'}}
        body['cuts'][1]['change']['key'] = key
        for c in body['cuts']:
            c['carries'] = ['air']   # the story's thread is in every cut
        return body

    def test_plan_rows_read_the_cuts_fields(self):
        from studio2 import flow
        rows = flow.plan_rows(self.path, self.body())
        first, second = rows
        self.assertEqual(first['change_times'], [0.25])                # 0.1 of a 2.5 s cut
        self.assertTrue(first['opens'])
        self.assertTrue(second['key_show'])
        self.assertEqual(second['key_times'], [1.84])
        self.assertEqual(second['story_beats'], ['b1', 'b2'])
        self.assertEqual(first['handover'], None)

    def test_a_cut_hands_over_to_a_new_place_as_v1_reads_it(self):
        from studio2 import flow
        body = self.body()
        body['places'].append({'id': 'chip', 'words': 'a chip'})
        body['cuts'][1]['place'] = 'chip'
        first, second = flow.plan_rows(self.path, body)
        self.assertEqual(first['handover'], 'new_place')   # v1 reads a hand-over on the cut before the new place
        self.assertIsNone(second['handover'])

    def test_the_story_thread_is_read_from_what_a_cut_carries(self):
        body = self.body()
        for c in body['cuts']:
            c.pop('carries')
        warnings = said(decisions.lint(self.path, 'cuts', body))
        self.assertIn('THREAD_OFF_SCREEN', codes(warnings))
        for c in body['cuts']:
            c['carries'] = ['air']
        self.assertNotIn('THREAD_OFF_SCREEN', codes(said(decisions.lint(self.path, 'cuts', body))))
        body['cuts'][0]['carries'] = ['smoke']
        errors = decisions.lint(self.path, 'cuts', body)['errors']
        self.assertTrue(any(e.startswith('CUT_CARRIER_UNKNOWN: c1') for e in errors), errors)

    def test_the_hook_and_the_key_change_are_read_from_the_cuts(self):
        warnings = said(decisions.lint(self.path, 'cuts', self.body()))
        self.assertNotIn('HOOK_WEAK', codes(warnings))
        self.assertNotIn('KEY_CHANGE_UNPLANNED', codes(warnings))
        warnings = said(decisions.lint(self.path, 'cuts', self.body(key=False, hook=False)))
        self.assertIn('KEY_CHANGE_UNPLANNED', codes(warnings))
        self.assertIn('HOOK_WEAK', codes(warnings))
        self.assertTrue(any('change.key: true' in w for w in warnings if w.startswith('KEY_CHANGE_UNPLANNED')), warnings)

    def test_a_key_change_is_one_that_happens(self):
        body = self.body()
        body['cuts'][0]['change']['key'] = True
        body['cuts'][0]['change'] = {'kind': 'none', 'key': True}
        errors = decisions.lint(self.path, 'cuts', body)['errors']
        self.assertTrue(any(e.startswith('CHANGE_UNDECIDED: c1') for e in errors), errors)

    def test_a_line_runs_across_cuts(self):
        body = self.body()
        got = decisions.lint(self.path, 'cuts', body)
        self.assertFalse([e for e in got['errors'] if 'line' in e.lower()], got['errors'])
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        from studio.project import load_shot
        self.assertEqual(load_shot(self.path, 'c1')['narration']['continues_into'], ['c2'])

    def test_the_reference_pace_is_read_on_the_plan(self):
        body = self.body()
        for c in body['cuts']:
            c['duration_s'] = 6.0
        warnings = said(decisions.lint(self.path, 'cuts', body))
        self.assertIn('PACE_SLOW', codes(warnings))
        from studio2 import cuts
        rules = cuts.rules(self.path)
        self.assertIn('rules', rules)

    def test_a_made_cut_is_measured_from_its_located_pictures(self):
        from studio2 import flow
        body = self.body()
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        places = {'parts': [{'id': 'filters', 'box': [0.1, 0.05, 0.9, 0.2], 'point': [0.5, 0.1]},
                            {'id': 'floor', 'box': [0.1, 0.7, 0.9, 0.8], 'point': [0.5, 0.75]}],
                  'flows': [], 'element': None, 'focus': [0.5, 0.5]}
        row = flow.measured(body['cuts'][1], places, located_ok=True, row=flow.plan_rows(self.path, body)[1])
        self.assertAlmostEqual(row['height'], 0.1)   # the change's parts (floor): what the cut is about
        self.assertTrue(row['still'] is False)       # it pushes
        self.assertEqual(row['key_moments'][0]['hidden'], 0.0)
        self.assertAlmostEqual(row['key_moments'][0]['size'], 0.8 * 1080 / 1920, places=3)   # the long side, counted in heights

    def test_v1s_reel_flow_reads_a_composite_reel_through_v2(self):
        from studio import reel_flow
        body = self.body()
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        got = reel_flow.built(self.path)
        said = codes(got['warnings'] + got['errors'])
        self.assertNotIn('HOOK_WEAK', said)
        self.assertNotIn('KEY_CHANGE_UNPLANNED', said)
        self.assertNotIn('REEL_FLOW_UNMEASURED', said)   # not made yet: said as not made, not as unmeasured 3D
        self.assertIn('REEL_FLOW_UNMADE', said)

    def test_the_same_view_read_from_pictures_is_the_same_picture(self):
        from studio import reel_flow
        rows = [{'shot_id': f'c{n}', 'len_s': 3.0, 'height': 0.4, 'same_as': {}} for n in range(1, 5)]
        for a in ('c1', 'c2', 'c3'):
            rows[int(a[1]) - 1]['same_as'] = {b: True for b in ('c1', 'c2', 'c3') if b != a}
        said = reel_flow.lines(rows, None, (), {})
        self.assertIn('SAME_PICTURE', codes(said))
        self.assertIn('PICTURE_REPEATS', codes(said))
        self.assertTrue(all('opens or its scale step' in l or 'open or their scale step' in l for l in said), said)


if __name__ == '__main__':
    unittest.main()
