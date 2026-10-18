"""The story before the script (studio/story_core.py, studio/story.py, the story layer in studio/decisions.py): beats
with a function, a tension and a share; lines name their beat; shots follow the beats; a ladder adopted before the
story goes on without one."""
from pathlib import Path
import json
import tempfile
import unittest

from studio import decisions, story, story_core
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, load_shot

# a two-beat story other tests settle before their script (the story layer sits between facts and script)
STORY = {'logline': 'a small part does a big job', 'beats': [
    {'story_beat_id': 'hook', 'function': 'hook', 'what_happens': 'the result, first', 'tension': 0.5, 'length_share': 0.2},
    {'story_beat_id': 'peak', 'function': 'climax', 'what_happens': 'how it is done', 'tension': 0.9, 'length_share': 0.8}]}


def told(script, beat='peak'):
    """The script with every line in one beat of STORY."""
    return {**script, 'lines': [{**line, 'story_beat_id': beat} for line in script['lines']]}


def beat(i, function, share, tension=0.5):
    return {'story_beat_id': i, 'function': function, 'what_happens': f'{i} happens', 'tension': tension, 'length_share': share}


class StoryRulesTest(unittest.TestCase):
    def test_a_story_opens_on_a_hook_and_turns_somewhere(self):
        flat = {'logline': 'x y z', 'beats': [beat('a', 'setup', 0.5), beat('b', 'explain', 0.5)]}
        errors, warnings = story_core.lint_story(flat, 30)
        self.assertTrue(any(e.startswith('STORY_HOOK_FIRST') for e in errors))
        self.assertTrue(any(e.startswith('STORY_NO_TURN') for e in errors))
        self.assertTrue(any(w.startswith('STORY_TENSION_FLAT') for w in warnings))

    def test_a_long_hook_and_shares_that_are_not_one_reel_are_errors(self):
        body = {'logline': 'x y z', 'beats': [beat('a', 'hook', 0.3), beat('b', 'climax', 0.6, 0.9)]}
        errors, _ = story_core.lint_story(body, 30)
        self.assertTrue(any(e.startswith('STORY_HOOK_LONG') for e in errors))     # 9 s of 30
        self.assertTrue(any(e.startswith('STORY_SHARES') for e in errors))        # 0.9 of the reel

    def test_an_early_climax_and_a_lecture_are_said(self):
        body = {'logline': 'x y z', 'beats': [beat('a', 'hook', 0.05, 0.2), beat('b', 'climax', 0.1, 0.9), beat('c', 'explain', 0.2),
                                             beat('d', 'explain', 0.2), beat('e', 'explain', 0.2), beat('f', 'close', 0.25)]}
        errors, warnings = story_core.lint_story(body, 40)
        self.assertEqual(errors, [])
        self.assertTrue(any(w.startswith('STORY_CLIMAX_PLACE') for w in warnings))
        self.assertTrue(any(w.startswith('STORY_EXPLAIN_RUN') for w in warnings))

    def test_a_direction_names_known_axis_values(self):
        axes = story_core.load_axes()
        body = {**STORY, 'direction': {'hook': 'failure', 'arc': 'problem_solution', 'angle': 'observer', 'tone': 'loud'}}
        errors, _ = story_core.lint_story(body, 30, axes)
        self.assertTrue(any('STORY_AXIS_UNKNOWN' in e and 'tone=loud' in e for e in errors))

    def test_lines_name_beats_in_the_storys_order(self):
        lines = [{'line_id': 'l1', 'text': 'aaaa', 'story_beat_id': 'peak'}, {'line_id': 'l2', 'text': 'bbbb', 'story_beat_id': 'hook'},
                 {'line_id': 'l3', 'text': 'cccc'}]
        errors, warnings = story_core.script_lines(STORY, lines)
        self.assertTrue(any(e.startswith('STORY_ORDER') for e in errors))
        self.assertTrue(any(e.startswith('STORY_LINE_UNPLACED') for e in errors))
        errors, warnings = story_core.script_lines(STORY, [{'line_id': 'l1', 'text': 'a' * 40, 'story_beat_id': 'peak'}])
        self.assertEqual(errors, [])
        self.assertTrue(any(w.startswith('STORY_BEAT_UNSAID') for w in warnings))   # the hook is left to the picture


class DirectionsTest(unittest.TestCase):
    def test_three_directions_differ_on_every_axis_they_can(self):
        axes = story_core.load_axes()
        drawn = story_core.pick_directions(axes, 3, seed=1)
        self.assertEqual([d['role'] for d in drawn], ['taste', 'contrast', 'explore'])
        for i, a in enumerate(drawn):
            for b in drawn[i + 1:]:
                self.assertEqual(story_core.distance(a['axes'], b['axes']), len(axes))   # every axis has 3+ values

    def test_the_taste_slot_follows_the_picks_and_the_explore_slot_does_not(self):
        axes = story_core.load_axes()
        liked = {'hook': {'failure': 3.0}, 'tone': {'tense': 2.0}}
        drawn = story_core.pick_directions(axes, 3, preference=liked, seed=2)
        self.assertEqual((drawn[0]['axes']['hook'], drawn[0]['axes']['tone']), ('failure', 'tense'))
        self.assertNotEqual(drawn[2]['axes']['hook'], 'failure')
        used = [drawn[0]['axes'], drawn[1]['axes']]
        again = story_core.pick_directions(axes, 3, preference=liked, used=used, seed=3)
        self.assertTrue(all(d['axes'] not in used for d in again))   # every slot moves away from earlier rounds
        self.assertEqual(again[0]['axes']['hook'], 'failure')           # ... the taste slot still on the taste
        before = {(k, v) for u in used for k, v in u.items()}
        fresh = {(k, v) for d in again for k, v in d['axes'].items()} - before
        unused = {(k, v) for k in axes for v in axes[k]['values']} - before
        self.assertGreaterEqual(len(fresh), len(unused) - len(liked))   # the round goes where none went yet (bar the taste it keeps)
        rounds, seen = [], []
        for n in range(3):   # a fixed taste does not draw one taste slot round after round
            rnd = story_core.pick_directions(axes, 3, preference=liked, used=seen, seed=n)
            rounds.append(rnd[0]['axes']); seen += [d['axes'] for d in rnd]
        self.assertEqual(len({tuple(sorted(a.items())) for a in rounds}), 3)
        said_no = {'tone': {'calm': -1.0}}
        self.assertNotEqual(story_core.pick_directions(axes, 3, preference=said_no, seed=1)[1]['axes']['tone'], 'calm')

    def test_two_directions_with_one_shape_are_one_idea(self):
        a = {'slot': 1, 'body': {**STORY, 'direction': {'hook': 'failure'}}}
        b = {'slot': 2, 'body': {**STORY, 'direction': {'hook': 'failure'}}}
        c = {'slot': 3, 'body': {**STORY, 'direction': {'hook': 'question'}}}
        self.assertEqual(len(story_core.stories_one_idea([a, b, c])), 1)


class StoryLayerTest(unittest.TestCase):
    BRIEF = {'topic': 'a joint', 'audience': 'adults', 'length_s': 10, 'key_message': 'gears trade speed for force', 'subject_mode': 'schematic'}
    FACTS = {'sources': [{'source_id': 's', 'title': 't'}], 'claims': [{'claim_id': 'c', 'text': 'gears multiply torque', 'source_ids': ['s'], 'about': []}]}
    SCRIPT = {'lines': [{'line_id': 'l1', 'text': '관절 안에는 기어가 있습니다.', 'illustrative': 'opening', 'story_beat_id': 'hook'},
                        {'line_id': 'l2', 'text': '기어는 힘을 키웁니다.', 'claim_ids': ['c'], 'story_beat_id': 'peak'}]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('story', {'request': 'joint', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])

    def tearDown(self):
        self.tmp.cleanup()

    def settle(self, layer, body):
        return decisions.approve(self.project, layer, '좋아요 이대로 가요', decisions.propose(self.project, layer, body)['rev'])

    def test_the_script_waits_for_the_story_and_the_story_reaches_story_json(self):
        self.settle('brief', self.BRIEF); self.settle('facts', self.FACTS)
        rev = decisions.propose(self.project, 'script', self.SCRIPT)['rev']
        with self.assertRaises(StudioError) as caught:
            decisions.approve(self.project, 'script', '좋아요 이대로 가요', rev)
        self.assertEqual(caught.exception.code, 'DECISION_UNAPPROVED')
        result = self.settle('story', STORY)
        self.assertEqual(result['materialized'], ['story.json'])
        self.assertEqual(story.body_of(story.load(self.project))['beats'], STORY['beats'])
        self.assertEqual(story.check(self.project)['errors'], [])
        report = decisions.propose(self.project, 'script', {'lines': [{**self.SCRIPT['lines'][0], 'story_beat_id': 'nowhere'}]})
        self.assertTrue(any(e.startswith('STORY_BEAT_UNKNOWN') for e in report['errors']))
        self.settle('script', self.SCRIPT)
        write_json(self.project / 'story.json', {**read_json(self.project / 'story.json'), 'logline': 'changed by hand'})
        self.assertEqual(decisions.drift(self.project, 'story'), ['story.json'])

    def test_shots_carry_their_story_beats_and_follow_them(self):
        from PIL import Image
        from studio.concept import add_beat
        self.settle('brief', self.BRIEF); self.settle('facts', self.FACTS); self.settle('story', STORY); self.settle('script', self.SCRIPT)
        image = Path(self.tmp.name) / 'b.png'
        Image.new('RGB', (90, 160), (120, 120, 120)).save(image)
        picture = add_beat(self.project, 'b1', image, 'the joint')['picture']
        self.settle('world', {'kind': 'none', 'beats': [{'beat_id': 'b1', 'line_ids': ['l1', 'l2'], 'pictures': [picture]}],
                              'elements': [{'id': 'joint', 'function': 'subject', 'description': 'the joint', 'owner': {'shape': 'blender', 'look': 'blender'},
                                            'why': 'it is the topic'}]})
        shots = {'shots': [{'shot_id': 'a', 'purpose': 'the result', 'line_ids': ['l2'], 'duration_s': 5, 'elements': ['joint']},
                           {'shot_id': 'b', 'purpose': 'the opening', 'line_ids': ['l1'], 'duration_s': 5, 'elements': ['joint']}]}
        report = decisions.propose(self.project, 'shotlist', shots)
        self.assertTrue(any('SHOTS_OUT_OF_ORDER' in e for e in report['errors']))
        named = {'shots': [{**shots['shots'][1], 'story_beat_ids': ['peak']}, shots['shots'][0]]}
        self.assertTrue(any(e.startswith('STORY_BEAT_MISMATCH') for e in decisions.propose(self.project, 'shotlist', named)['errors']))
        self.settle('shotlist', {'shots': [shots['shots'][1], shots['shots'][0]]})
        self.assertEqual((load_shot(self.project, 'b')['story_beat_ids'], load_shot(self.project, 'a')['story_beat_ids']), (['hook'], ['peak']))

    def test_a_ladder_adopted_before_the_story_goes_on_without_one(self):
        self.settle('brief', self.BRIEF); self.settle('facts', self.FACTS)
        ladder = self.project / 'decisions' / 'ladder.json'
        write_json(ladder, {**read_json(ladder), 'layers': ['brief', 'facts', 'script', 'world', 'shotlist', 'look', 'cut']})
        self.settle('script', {'lines': [{k: v for k, v in l.items() if k != 'story_beat_id'} for l in self.SCRIPT['lines']]})
        rows = {r['layer']: r['state'] for r in decisions.status(self.project)['layers']}
        self.assertEqual((rows['story'], rows['script']), ('not_in_ladder', 'approved'))
        self.assertNotIn('story', decisions.status(self.project)['next'])
        self.assertTrue(story.missing(self.project)[0].startswith('STORY_MISSING'))
        self.assertTrue(decisions.status(self.project)['warnings'][0].startswith('STORY_MISSING'))   # said where the user looks

    def test_a_strict_policy_makes_the_storys_taste_findings_errors(self):
        self.settle('brief', self.BRIEF); self.settle('facts', self.FACTS)
        flat = {**STORY, 'beats': [{**b, 'tension': 0.5} for b in STORY['beats']]}
        self.assertTrue(any(w.startswith('STORY_TENSION_FLAT') for w in decisions.propose(self.project, 'story', flat)['warnings']))
        data = read_json(self.project / 'project.json'); data['policy'] = {'strictness': 'all-strict'}
        write_json(self.project / 'project.json', data)
        self.assertTrue(any(e.startswith('STORY_TENSION_FLAT') for e in decisions.propose(self.project, 'story', flat)['errors']))
        data['policy'] = {'strictness': 'all-strict', 'gates': {'STORY_TENSION_FLAT': 'warn'}}
        write_json(self.project / 'project.json', data)
        self.assertFalse(any(e.startswith('STORY_TENSION_FLAT') for e in decisions.propose(self.project, 'story', flat)['errors']))

    def test_adopting_keeps_a_picked_story_and_a_two_shot_draft_peaks(self):
        two = Path(init_project('two', {'request': 'two', 'shots': [{'shot_id': 'a', 'frame_count': 30}, {'shot_id': 'b', 'frame_count': 30}]},
                                self.tmp.name)['project_path'])
        decisions.adopt(two)
        draft = decisions.envelope(two, 'story')
        self.assertEqual([b['function'] for b in draft['body']['beats']], ['hook', 'climax'])
        self.assertFalse(any(e.startswith('STORY_NO_TURN') for e in decisions.lint(two, 'story', draft['body'])['errors']))
        story.materialize(self.project, STORY, 'user', '1안 좋아요 이걸로')
        decisions.adopt(self.project)
        self.assertEqual(decisions.envelope(self.project, 'story')['body']['beats'], STORY['beats'])   # the pick, not a draft


if __name__ == '__main__':
    unittest.main()


def written(axes, function_run=('hook', 'setup', 'setback', 'climax', 'close')):
    shares = [0.08] + [round((1 - 0.08) / (len(function_run) - 1), 4)] * (len(function_run) - 1)
    shares[-1] = round(1 - sum(shares[:-1]), 4)
    tension = [0.5, 0.3, 0.6, 1.0, 0.4]
    return {'logline': 'a booster comes home', 'direction': axes,
            'beats': [{'story_beat_id': f'b{n}', 'function': f, 'what_happens': f'{f} happens', 'tension': tension[n % 5],
                       'length_share': shares[n], 'picture_idea': f'the {f}'} for n, f in enumerate(function_run)]}


class DirectionFlowTest(unittest.TestCase):
    """story directions -> add (painted by a stub) -> sheet -> pick, off the ladder and on it; the pick reaches the taste."""

    def setUp(self):
        from unittest import mock
        from studio import patterns, taste_picks
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('dirs', {'request': 'how a booster lands', 'shots': [{'shot_id': 's01', 'frame_count': 30}]},
                                         self.tmp.name)['project_path'])
        for module in (patterns, taste_picks):
            patcher = mock.patch.object(module, 'TASTE', Path(self.tmp.name) / 'taste.json')
            patcher.start(); self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def paint(self, cmd, cwd, timeout):
        from PIL import Image
        out = Path(cwd) / 'made.png'
        Image.new('RGB', (90, 160), (30, 60, 90)).save(out)
        return {'returncode': 0, 'stdout': '{"image": "%s"}\n' % out, 'stderr': '', 'timed_out': False}

    def write_all(self, rnd):
        for s in rnd['slots']:
            story.add(self.project, rnd['round'], s['slot'], written(s['axes']), prose='먼저 결과를 보여주고 거꾸로 간다.',
                      paint=True, runner=self.paint)

    def test_three_drawn_directions_are_briefed_written_shown_and_one_is_picked(self):
        rnd = story.directions(self.project)
        self.assertEqual(len(rnd['slots']), 3)
        self.assertIn('First write the story freely', rnd['slots'][0]['brief'])
        with self.assertRaises(StudioError):   # a body written to another draw is refused
            story.add(self.project, rnd['round'], 1, written(rnd['slots'][1]['axes']))
        self.write_all(rnd)
        page = story.sheet(self.project)
        html = Path(page['sheet']).read_text()
        self.assertIn('번호와 한 줄 이유', html)
        self.assertIn('d1.png', html)
        self.assertEqual(page['warnings'], [])   # one run of beats, but each opens its own way: three stories
        with self.assertRaises(StudioError):
            story.pick(self.project, rnd['round'], 2, 'ok')
        result = story.pick(self.project, rnd['round'], 2, '2안이 제일 긴장감 있어요')
        self.assertEqual(story.load(self.project)['user_words'], '2안이 제일 긴장감 있어요')
        self.assertFalse(decisions.adopted(self.project))   # off the ladder it stays off
        env = decisions.envelope(self.project, 'story')   # kept where every layer is, decided in the user's words
        self.assertEqual((decisions.state(self.project, 'story')['state'], env['decided']['by'], env['decided']['user_words']),
                         ('decided', 'user', '2안이 제일 긴장감 있어요'))
        from studio import taste_picks
        row = taste_picks.picks('story')[0]
        self.assertEqual((row['chosen'], len(row['rejected'])), (rnd['slots'][1]['axes'], 2))
        with self.assertRaises(StudioError):
            story.pick(self.project, rnd['round'], 1, '역시 1안으로 할게요')   # a round is picked once
        again = story.directions(self.project)
        self.assertNotEqual(again['round'], rnd['round'])
        self.assertEqual(result['picked'], 2)

    def test_on_the_ladder_the_pick_is_the_story_layers_approval(self):
        decisions.approve(self.project, 'brief', '좋아요 이대로 가요', decisions.propose(self.project, 'brief', StoryLayerTest.BRIEF)['rev'])
        decisions.approve(self.project, 'facts', '좋아요 이대로 가요', decisions.propose(self.project, 'facts', StoryLayerTest.FACTS)['rev'])
        rnd = story.directions(self.project)
        self.write_all(rnd)
        with self.assertRaises(StudioError):   # picked from a sheet the user saw: not before it is made
            story.pick(self.project, rnd['round'], 1, '1안으로 갈게요 좋네요')
        story.sheet(self.project)
        story.pick(self.project, rnd['round'], 1, '1안으로 갈게요 좋네요')
        env = decisions.envelope(self.project, 'story')
        self.assertEqual((env['status'], env['approval']['user_words']), ('approved', '1안으로 갈게요 좋네요'))
        self.assertEqual(story.load(self.project)['beats'], env['body']['beats'])

    def test_a_failed_pick_on_the_ladder_leaves_nothing_proposed(self):
        from unittest import mock
        decisions.approve(self.project, 'brief', '좋아요 이대로 가요', decisions.propose(self.project, 'brief', StoryLayerTest.BRIEF)['rev'])
        decisions.approve(self.project, 'facts', '좋아요 이대로 가요', decisions.propose(self.project, 'facts', StoryLayerTest.FACTS)['rev'])
        rnd = story.directions(self.project)
        self.write_all(rnd)
        story.sheet(self.project)
        with mock.patch.object(decisions, 'approve', side_effect=StudioError('BUSY', 'held')):
            with self.assertRaises(StudioError):
                story.pick(self.project, rnd['round'], 1, '1안으로 갈게요 좋네요')
        self.assertIsNone(decisions.envelope(self.project, 'story'))   # no proposed story for the script to wait on
        self.assertIsNone(story.load(self.project))
        self.assertIn('story_pick_undone', (self.project / 'decisions' / 'log.jsonl').read_text())

    def test_a_ladder_adopted_without_a_story_keeps_its_shape_unless_migrated(self):
        decisions.approve(self.project, 'brief', '좋아요 이대로 가요', decisions.propose(self.project, 'brief', StoryLayerTest.BRIEF)['rev'])
        decisions.approve(self.project, 'facts', '좋아요 이대로 가요', decisions.propose(self.project, 'facts', StoryLayerTest.FACTS)['rev'])
        ladder = self.project / 'decisions' / 'ladder.json'
        data = json.loads(ladder.read_text()); data['layers'] = [l for l in data['layers'] if l != 'story']; ladder.write_text(json.dumps(data))
        rnd = story.directions(self.project)
        self.write_all(rnd)
        story.sheet(self.project)
        with self.assertRaises(StudioError):
            story.pick(self.project, rnd['round'], 1, '1안으로 갈게요 좋네요')
        self.assertIsNone(decisions.envelope(self.project, 'story'))
        story.pick(self.project, rnd['round'], 1, '1안으로 갈게요 좋네요', migrate=True)
        self.assertEqual(decisions.state(self.project, 'story')['state'], 'approved')

    def test_a_delegated_run_cannot_pick_the_story(self):
        decisions.delegate(self.project, '이번 영상은 맡길게요', [])
        rnd = story.directions(self.project)
        self.write_all(rnd)
        story.sheet(self.project)
        with self.assertRaises(StudioError):   # the delegation's own words are the run, not the user picking
            story.pick(self.project, rnd['round'], 1, '이번 영상은 맡길게요')
        story.pick(self.project, rnd['round'], 1, '1안 좋아요 이걸로')
