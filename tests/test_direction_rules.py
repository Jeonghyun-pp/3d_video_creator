"""The 13 direction rules (studio/reel_flow.py RULES, 2026-10-19): a reel told as a story opens with a cut and something
happening, marks its key change and shows it large and unhidden (these three stop), and answers every other direction
finding - fixed, or departed with why - before its shot list is settled or a coloured render starts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio import decisions, gates, jobs, reel_flow
from studio.blender_ops.frame_probe_core import hidden_share
from studio.common import CODE, StudioError, read_json, write_json
from studio.project import init_project

STORY = {'logline': 'a lever moves a chain', 'thread': {'kind': 'moving', 'carrier': 'the chain'}, 'beats': [
    {'story_beat_id': 'h', 'function': 'hook', 'what_happens': 'the chain jumps', 'tension': 0.6, 'length_share': 0.2,
     'raises': 'what moved it?', 'on_screen': {'event': 'the chain jumps', 'kind': 'anomaly'}},
    {'story_beat_id': 'p', 'function': 'climax', 'what_happens': 'it climbs a tooth', 'tension': 1.0, 'length_share': 0.6,
     'link': 'so', 'answers': ['h']},
    {'story_beat_id': 'c', 'function': 'close', 'what_happens': 'it runs on', 'tension': 0.3, 'length_share': 0.2,
     'link': 'so', 'answers': ['h']}]}


def row(shot_id, len_s, role=None, **extra):
    return {'shot_id': shot_id, 'len_s': len_s, 'role': role, **extra}


def good_rows():
    """A reel that keeps every rule: a fast hook where the chain moves, the climax cut close in two, an ending held."""
    return [row('a', 1.4, 'hook', change_kind='object_motion', story_beats=['h']),
            row('b', 1.8, 'establish', story_beats=['h']),
            row('c', 1.6, 'reveal', story_beats=['p'], key_show=True),
            row('d', 1.2, 'explain', story_beats=['p']),
            row('e', 2.6, 'close', story_beats=['c'], scale_step='wider')]


class RulesTest(unittest.TestCase):
    def test_a_reel_that_keeps_the_rules_is_quiet(self):
        self.assertEqual(reel_flow.lines(good_rows(), STORY), [])

    def test_every_cut_has_a_job_in_the_story(self):
        """Rule 15 (2026-10-20): a cut that tells no beat is decoration, whatever it shows."""
        self.assertEqual(reel_flow.lines(good_rows(), STORY), [])
        loose = good_rows()
        loose[3] = {**loose[3], 'story_beats': []}
        self.assertTrue(any(l.startswith('CUT_UNTOLD') and ' d ' in l for l in reel_flow.lines(loose, STORY)))
        loose[3]['departs'] = ['cut_untold']
        self.assertFalse(any(l.startswith('CUT_UNTOLD') for l in reel_flow.lines(loose, STORY)))
        self.assertIn('DIRECTION_UNRESOLVED', reel_flow.unresolved(['CUT_UNTOLD: x'])[0])

    def test_the_hook_cuts_and_something_happens_in_it(self):
        slow = [row('a', 3.5, 'hook', story_beats=['h']), *good_rows()[2:]]
        found = [l for l in reel_flow.lines(slow, STORY) if l.startswith('HOOK_WEAK')]
        self.assertEqual(len(found), 1)
        self.assertIn('no cut in the first 3 s', found[0]); self.assertIn('nothing is said to happen', found[0])
        explained = [{**good_rows()[0], 'role': 'explain'}, *good_rows()[1:]]
        self.assertIn('not a hook', reel_flow.lines(explained, STORY)[0])
        self.assertEqual([l for l in reel_flow.lines(slow) if l.startswith('HOOK_WEAK')], [])   # no story: no reel to open

    def test_a_small_hook_is_weak_when_built(self):
        with mock.patch.object(reel_flow, '_hook_height_floor', return_value=0.46):
            small = [{**good_rows()[0], 'height': 0.3}, *good_rows()[1:]]
            self.assertIn("under the study set's hook", reel_flow.lines(small, STORY)[0])

    def test_the_key_change_is_planned_then_shown_large_and_unhidden(self):
        unmarked = [{**r, 'key_show': False} for r in good_rows()]
        self.assertTrue(reel_flow.lines(unmarked, STORY)[0].startswith('KEY_CHANGE_UNPLANNED'))
        moment = lambda size, hidden: [{**r, 'key_moments': [{'at': 0.5, 'parts': ['chain'], 'size': size, 'hidden': hidden}]}  # noqa: E731
                                       if r['shot_id'] == 'c' else r for r in good_rows()]
        self.assertEqual(reel_flow.lines(moment(0.4, 0.1), STORY), [])
        small = reel_flow.lines(moment(0.1, 0.1), STORY)
        self.assertTrue(small[0].startswith('KEY_CHANGE_UNSHOWN') and 'frame height across' in small[0])
        self.assertIn('hidden in front', reel_flow.lines(moment(0.4, 0.6), STORY)[0])
        self.assertIn('not measured', reel_flow.lines(moment(None, None), STORY)[0])

    def test_the_stops_cannot_be_departed(self):
        for code in reel_flow.BLOCKING:
            self.assertNotIn(code, reel_flow.DEPARTS)
            self.assertEqual(gates.SOFTENABLE[code][0], 'broken')   # an error unless a look-first policy says otherwise
        self.assertEqual(gates.SOFTENABLE['DIRECTION_UNRESOLVED'][0], 'broken')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(StudioError):
            project = init_project('d', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, tmp)['project_path']
            reel_flow.depart(project, 'HOOK_WEAK', 'a reason long enough', agent_note='because')

    def test_every_rule_names_findings_the_gates_know(self):
        self.assertEqual([r[0] for r in reel_flow.RULES], list(range(1, len(reel_flow.RULES) + 1)))
        for _, _, codes, _ in reel_flow.RULES:
            for code in codes:
                self.assertIn(code, gates.SOFTENABLE, code)
        schema = read_json(CODE / 'schemas/studio-v1/project.schema.json')
        self.assertEqual(set(schema['properties']['policy']['properties']['gates']['propertyNames']['enum']), set(gates.SOFTENABLE))
        out = reel_flow.rules()
        self.assertEqual(len(out['rules']), len(reel_flow.RULES))
        self.assertIn('KEY_SIZE_MIN', out['thresholds'])

    def test_resting_cameras_leave_the_climax_out_and_height_does_not_jolt(self):
        built = lambda r, **m: {**r, 'height': 0.5, 'env': 0.2, **m}  # noqa: E731
        rows = [built(r, still=False) for r in good_rows()]
        rows[2]['still'] = rows[3]['still'] = True   # the climax rests, nothing else does
        self.assertTrue(any(l.startswith('STATIC_SHARE_LOW') for l in reel_flow.lines(rows, STORY)))
        rows = [built(r, still=True) for r in good_rows()]
        rows[0]['view_end'], rows[1]['view_start'] = [70.0, 0.0], [-5.0, 0.0]
        self.assertTrue(any(l.startswith('HEIGHT_FLIP') for l in reel_flow.lines(rows, STORY)))
        rows[1]['departs'] = ['height_flip']
        self.assertFalse(any(l.startswith('HEIGHT_FLIP') for l in reel_flow.lines(rows, STORY)))

    def test_lengths_follow_the_words_and_the_ending_holds(self):
        rows = good_rows()
        rows[1] = {**rows[1], 'len_s': 6.0, 'spoken_s': 2.0}
        self.assertTrue(any(l.startswith('SHOT_HOLDS_UNSPOKEN') for l in reel_flow.lines(rows, STORY)))
        rows[1] = {**rows[1], 'opens': True, 'spoken_s': 3.4}   # an opening holds no silence either (2026-10-20, the study set)
        self.assertTrue(any(l.startswith('SHOT_HOLDS_UNSPOKEN') for l in reel_flow.lines(rows, STORY)))
        rows[1] = {**rows[1], 'spoken_s': 4.6}   # the voice goes on over it
        self.assertFalse(any(l.startswith('SHOT_HOLDS_UNSPOKEN') for l in reel_flow.lines(rows, STORY)))
        short_end = [*good_rows()[:-1], {**good_rows()[-1], 'len_s': 0.9}]
        self.assertTrue(any(l.startswith('CLOSE_SHORT') for l in reel_flow.lines(short_end, STORY)))

    def test_the_climax_is_cut_and_closest(self):
        one = [r for r in good_rows() if r['shot_id'] != 'd']
        self.assertTrue(any(l.startswith('CLIMAX_ONE_SHOT') for l in reel_flow.lines(one, STORY)))
        heights = {'a': 0.5, 'b': 0.3, 'c': 0.35, 'd': 0.4, 'e': 0.6}
        rows = [{**r, 'height': heights[r['shot_id']], 'env': 0.3} for r in good_rows()]
        self.assertTrue(any(l.startswith('CLIMAX_NOT_CLOSEST') for l in reel_flow.lines(rows, STORY)))
        rows[2]['height'] = 0.8
        self.assertFalse(any(l.startswith('CLIMAX_NOT_CLOSEST') for l in reel_flow.lines(rows, STORY)))

    def test_hidden_small_and_flat_parts_are_said(self):
        rows = [{**r, 'height': 0.5, 'env': 0.2} for r in good_rows()]
        rows[1].update({'hidden': {'pulley': 0.6}, 'contrast': {'chain': 0.4}})
        rows[3].update({'height': 0.1, 'env': 0.05})
        found = reel_flow.lines(rows, STORY)
        for code in ('PART_OCCLUDED', 'PART_LOW_CONTRAST', 'UNDERFILL_NO_SCALE'):
            self.assertTrue(any(l.startswith(code) for l in found), code)
        rows[1]['departs'] = ['part_occluded', 'part_low_contrast']
        rows[3]['departs'] = ['underfill_no_scale']
        self.assertEqual(reel_flow.lines(rows, STORY), [])

    def test_open_findings_need_an_answer(self):
        self.assertEqual(reel_flow.unresolved([]), [])
        left = reel_flow.unresolved(['PACE_SLOW: x', 'PACE_SLOW: y', 'ROLE_RUN: z', 'HOOK_WEAK: stops on its own'])
        self.assertTrue(left[0].startswith('DIRECTION_UNRESOLVED: 3 direction findings') and 'PACE_SLOW x2' in left[0])

    def test_hidden_share_counts_a_see_through_skin_by_its_opacity(self):
        self.assertEqual(hidden_share(100, 100), 0.0)
        self.assertEqual(hidden_share(100, 40), 0.6)
        self.assertEqual(hidden_share(100, 40, seen=100, alpha=1.0), 0.0)   # a fully clear skin hides nothing
        self.assertEqual(hidden_share(100, 40, seen=100, alpha=0.5), 0.3)   # a half-clear one hides half of what it covers
        self.assertIsNone(hidden_share(0, 0))

    def test_an_outline_stands_apart_from_a_different_colour_only(self):
        from studio.critique import _lab
        inside = {(x, y) for x in range(20, 40) for y in range(20, 40)}

        def apart(fill, around):
            picture = Image.new('RGB', (60, 60), around)
            picture.paste(fill, (20, 20, 40, 40))
            return reel_flow.outline_apart(_lab(picture), inside)
        self.assertEqual(apart((200, 30, 30), (30, 30, 200)), 1.0)
        self.assertEqual(apart((235, 235, 235), (240, 240, 240)), 0.0)


class BuiltTest(unittest.TestCase):
    """What a built shot's probe report says about its key change (review 2026-10-19: a crash on any key row, a part
    never measured read as 0.0, an old report passing rule 12 unmeasured, a parent counting its own parts as in front)."""
    SHOT = {'duration_frames': 30, 'show': [{'at': 0.5, 'parts': ['bike'], 'how': 'exterior', 'why': 'the cage pushes', 'key': True}]}

    def report(self, occlusion=True, parts=('bike', 'bike/chain')):
        frame = {'frame': 15, 'shapes': {'key:bike': {'bbox': [0.2, 0.3, 0.6, 0.5]}, 'key:bike/chain': {'bbox': [0.3, 0.45, 0.5, 0.7]}}}
        if occlusion:
            frame['occlusion'] = {'bike': {'alone': 1000, 'hidden': 0.2}}
        return {'summary': {'key_parts': list(parts), 'size_px': [1080, 1920]}, 'frames': [{'frame': 0, 'shapes': {}}, frame]}

    def test_a_key_moment_is_read_at_its_frame_with_the_parts_inside_it(self):
        m = reel_flow.key_moments(self.SHOT, self.report())[0]
        self.assertEqual((m['frame'], m['hidden']), (15, 0.2))
        self.assertAlmostEqual(m['size'], 0.4)   # bike and its chain together: 0.30 to 0.70 of the height

    def test_what_was_not_measured_says_so(self):
        for report in (self.report(occlusion=False), self.report(parts=('bike/chain',))):
            m = reel_flow.key_moments(self.SHOT, report)[0]
            self.assertTrue(m['size'] is None or m['hidden'] is None)
            rows = [{**r, 'key_moments': [m]} if r['shot_id'] == 'c' else r for r in good_rows()]
            self.assertIn('not measured', reel_flow.lines(rows, STORY)[0])

    def test_a_key_moment_late_in_the_opening_shot_is_no_hook(self):
        rows = [{**good_rows()[0], 'change_kind': None, 'key_times': [4.5], 'len_s': 2.0}, *good_rows()[1:]]
        self.assertTrue(any(l.startswith('HOOK_WEAK') for l in reel_flow.lines(rows, STORY)))
        rows[0]['key_times'] = [0.8]
        self.assertFalse(any(l.startswith('HOOK_WEAK') for l in reel_flow.lines(rows, STORY)))


class ShotListTest(unittest.TestCase):
    BRIEF = {'topic': 'a derailleur', 'audience': 'adults', 'length_s': 8, 'key_message': 'the chain is pushed over', 'subject_mode': 'schematic'}
    FACTS = {'sources': [{'source_id': 's', 'title': 't'}], 'claims': [{'claim_id': 'c', 'text': 'the cage pushes the chain', 'source_ids': ['s'], 'about': []}]}
    SCRIPT = {'lines': [{'line_id': 'l1', 'text': '체인이 옆으로 넘어갑니다.', 'illustrative': 'hook', 'story_beat_id': 'h'},
                        {'line_id': 'l2', 'text': '케이지가 밀어 줍니다.', 'claim_ids': ['c'], 'story_beat_id': 'p'},
                        {'line_id': 'l3', 'text': '이제 새 톱니입니다.', 'illustrative': 'close', 'story_beat_id': 'c'}]}

    def setUp(self):
        from studio.concept import add_beat
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('dir', {'request': 'derailleur', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        for layer, body in (('brief', self.BRIEF), ('facts', self.FACTS), ('story', STORY), ('script', self.SCRIPT)):
            self.settle(layer, body)
        image = Path(self.tmp.name) / 'b.png'
        Image.new('RGB', (90, 160), (120, 120, 120)).save(image)
        picture = add_beat(self.project, 'b1', image, 'the chain')['picture']
        self.settle('world', {'kind': 'none', 'beats': [{'beat_id': 'b1', 'line_ids': ['l1', 'l2', 'l3'], 'pictures': [picture]}],
                              'elements': [{'id': 'chain', 'function': 'subject', 'description': 'the chain',
                                            'owner': {'shape': 'blender', 'look': 'blender'}, 'why': 'it is the topic'}]})

    def tearDown(self):
        self.tmp.cleanup()

    def settle(self, layer, body):
        return decisions.approve(self.project, layer, '좋아요 이대로 가요', decisions.propose(self.project, layer, body)['rev'])

    def shots(self, **change):
        show = [{'at': 0.5, 'parts': ['chain'], 'how': 'exterior', 'why': 'the chain climbs', 'key': True}]
        shots = [{'shot_id': 'a', 'purpose': 'the jump', 'line_ids': ['l1'], 'duration_s': 2.0, 'role': 'hook', 'elements': ['chain'],
                  'pattern': {'carried': [{'item': 'change_kind', 'value': 'object_motion', 'why': 'the chain moves at once'}]}},
                 {'shot_id': 'b', 'purpose': 'the push', 'line_ids': ['l2'], 'duration_s': 2.5, 'role': 'reveal', 'elements': ['chain'], 'show': show},
                 {'shot_id': 'c', 'purpose': 'it runs', 'line_ids': ['l3'], 'duration_s': 3.0, 'role': 'close', 'elements': ['chain'],
                  'direction': {'scale_step': 'wider'}}]
        for shot in shots:
            shot.update(change.get(shot['shot_id'], {}))
        from tests.decisions_fixture import moved
        return {'shots': [moved(s) for s in shots]}

    def test_open_findings_keep_the_list_a_draft_until_answered(self):
        body = self.shots()
        report = decisions.propose(self.project, 'shotlist', body)
        self.assertTrue(any(e.startswith('DIRECTION_UNRESOLVED') and 'CLIMAX_ONE_SHOT' in e for e in report['errors']), report['errors'])
        sheet = Path(report['sheet']).read_text()
        self.assertIn('**Direction (studio reel rules):**', sheet); self.assertIn('**open**: CLIMAX_ONE_SHOT', sheet)
        short = self.shots(b={'pattern': {'departs': [{'item': 'climax_one_shot', 'why': 'one take'}]}})
        with self.assertRaises(StudioError):   # a departure says why in a sentence (the schema's 12 characters)
            decisions.propose(self.project, 'shotlist', short)
        answered = self.shots(b={'pattern': {'departs': [{'item': 'climax_one_shot', 'why': 'the climb is one continuous motion, cut it breaks'}]}})
        report = decisions.propose(self.project, 'shotlist', answered)
        self.assertEqual([e for e in report['errors'] if 'DIRECTION' in e], [])
        sheet = Path(report['sheet']).read_text()
        self.assertIn('departed: CLIMAX_ONE_SHOT', sheet); self.assertIn('**Departed on purpose (read these):**', sheet)

    def test_the_stops_hold_on_the_shot_list(self):
        unmarked = self.shots(b={'show': [{'at': 0.5, 'parts': ['chain'], 'how': 'exterior', 'why': 'the chain climbs'}]})
        errors = decisions.propose(self.project, 'shotlist', unmarked)['errors']
        self.assertTrue(any(e.startswith('KEY_CHANGE_UNPLANNED') for e in errors))
        slow = self.shots(a={'duration_s': 3.5, 'pattern': {}})
        self.assertTrue(any(e.startswith('HOOK_WEAK') for e in decisions.propose(self.project, 'shotlist', slow)['errors']))

    def test_a_line_runs_across_a_cut_into_the_shots_right_after(self):
        """2026-10-19: one line per shot made each shot as long as its sentence; a line now runs on over the cut."""
        shots = [{'shot_id': 'a', 'line_ids': ['l1', 'l2'], 'duration_s': 2.0}, {'shot_id': 'b', 'line_ids': ['l2'], 'duration_s': 1.5},
                 {'shot_id': 'c', 'line_ids': ['l2', 'l3'], 'duration_s': 3.0, 'line_at_s': [0, 1.5]}]
        self.assertEqual(decisions.span_lines(shots), [])
        self.assertEqual(decisions.continues_into(shots), {'a': ['b', 'c']})
        self.assertEqual(decisions.own_lines(shots), {'a': [('l1', None), ('l2', None)], 'b': [], 'c': [('l3', 1.5)]})
        texts = {'l1': '가' * 7, 'l2': '나' * 21, 'l3': '다' * 7}   # 1 s, 3 s, 1 s at 7 characters a second
        self.assertEqual(decisions.speech_occupancy(shots, texts), {'a': 2.0, 'b': 1.5, 'c': 1.5})
        apart = [shots[0], {'shot_id': 'x', 'line_ids': [], 'duration_s': 1.0}, {**shots[1], 'line_ids': ['l2']}]
        self.assertIn('not one after another', decisions.span_lines(apart)[0])
        late = [shots[0], {**shots[1], 'line_ids': ['l3', 'l2']}]
        self.assertTrue(any('first line, at 0' in e for e in decisions.span_lines(late)))
        early = [{**shots[0], 'line_ids': ['l2', 'l1']}, shots[1]]
        self.assertTrue(any('not its last line' in e for e in decisions.span_lines(early)))
        crowded = [shots[0], {**shots[1], 'line_ids': ['l2', 'l3']}, {**shots[2], 'line_ids': ['l2']}]   # l3 said while l2 still runs
        self.assertTrue(any('no other line fits' in e for e in decisions.span_lines(crowded)))
        unplaced = [shots[0], {**shots[1], 'line_ids': ['l2', 'l3']}]   # where does b's own line start?
        self.assertTrue(any('give line_at_s' in e for e in decisions.span_lines(unplaced)))
        alone = [{'shot_id': 'a', 'line_ids': ['l2'], 'duration_s': 2.0}, {'shot_id': 'b', 'line_ids': ['l3'], 'duration_s': 2.0}]
        self.assertEqual(decisions.speech_occupancy(alone, texts)['b'], 1.0)   # a line too long for its shot is a's floor, not b's words

    def test_a_shot_list_says_a_line_across_a_cut_and_the_shot_carries_it(self):
        split = self.shots(a={'line_ids': ['l1', 'l2'], 'duration_s': 2.0},
                           b={'line_ids': ['l2'], 'duration_s': 1.8, 'line_at_s': [0]})
        report = decisions.propose(self.project, 'shotlist', split)
        self.assertFalse([e for e in report['errors'] if 'line' in e.lower() and 'shot' in e.lower() and 'DIRECTION' not in e], report['errors'])
        short = self.shots(a={'line_ids': ['l1', 'l2'], 'duration_s': 2.0}, b={'line_ids': ['l2'], 'duration_s': 0.2})
        self.assertTrue(any(e.startswith('LINE_OUTRUNS_SHOTS') for e in decisions.propose(self.project, 'shotlist', short)['errors']))
        from studio.project import load_shot
        decisions._materialize_shotlist(self.project, split)
        a, b = load_shot(self.project, 'a'), load_shot(self.project, 'b')
        self.assertEqual((a['narration']['continues_into'], b['narration']['text']), (['b'], ''))
        self.assertIn('runs on over it', b['narration']['silent'])
        self.assertTrue(a['narration']['text'].endswith('케이지가 밀어 줍니다.'))

    def test_a_coloured_render_waits_for_the_answers(self):
        with mock.patch('studio.decisions.envelope', return_value={'status': 'proposed'}), \
                mock.patch('studio.reel_flow.built', return_value={'errors': ['DIRECTION_UNRESOLVED: 1 direction findings are open (ROLE_RUN)']}):
            with self.assertRaises(StudioError) as caught:
                jobs._require_direction(self.project)
        self.assertEqual(caught.exception.code, 'DIRECTION_UNRESOLVED')
        with mock.patch('studio.decisions.envelope', return_value={'status': 'proposed'}), \
                mock.patch('studio.reel_flow.built', return_value={'errors': []}):
            jobs._require_direction(self.project)
        with mock.patch('studio.decisions.envelope', return_value=None), \
                mock.patch('studio.reel_flow.built', side_effect=AssertionError('no shot list, no reel to read')):
            jobs._require_direction(self.project)


if __name__ == '__main__':
    unittest.main()
