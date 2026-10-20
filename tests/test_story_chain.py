"""The thread and the chain (2026-10-19, studio/story_core.chain_lint, studio/reel_flow.thread_lines): a story holds one
thing the viewer keeps hold of, every beat after the hook follows the last because of it (so) or against it (but), the
hook's question is answered late and the ending comes back to it; the thread stays on screen shot after shot."""
from pathlib import Path
import tempfile
import unittest

from studio import reel_flow, story, story_core
from studio.common import write_json
from studio.project import init_project


def beat(i, function, share, tension=0.5, **extra):
    return {'story_beat_id': i, 'function': function, 'what_happens': f'{i} happens', 'tension': tension, 'length_share': share, **extra}


def good():
    """A 30 s story told as a chain: press - but nothing shifts - so what is missing? - the crank turns - so the chain
    climbs - back to the press."""
    return {'logline': 'why the chain shifts only when it moves', 'thread': {'kind': 'moving', 'carrier': 'the chain', 'carrier_ids': ['bike/chain']},
            'stake': {'who': 'the rider', 'what_is_at_risk': 'a gear missed on the climb', 'closed_by': 'c'},
            'beats': [beat('h', 'hook', 0.1, 0.6, raises='does the lever shift the chain?',
                           on_screen={'event': 'the lever clicks and the chain stays put', 'kind': 'anomaly'}),
                      beat('s', 'setback', 0.2, 0.8, link='but', raises='what is missing?',
                           on_screen={'event': 'the rider stalls on the hill', 'kind': 'failure'}),
                      beat('t', 'turn', 0.2, 0.7, link='so', answers=['s']),
                      beat('p', 'climax', 0.3, 1.0, link='so', answers=['h']),
                      beat('c', 'close', 0.2, 0.3, link='so', answers=['h'], returns_to='h')]}


def codes(lines):
    return [line.split(':', 1)[0] for line in lines]


class ChainTest(unittest.TestCase):
    def test_a_chain_is_quiet(self):
        self.assertEqual(story_core.lint_story(good(), 30, story_core.load_axes()), ([], []))

    def test_no_thread_no_link_and_an_unanswered_hook_stop(self):
        body = good()
        body.pop('thread')
        body['beats'][2].pop('link')
        for b in body['beats']:
            b.pop('answers', None)
        errors, _ = story_core.lint_story(body, 30)
        self.assertEqual(sorted(set(codes(errors))), ['HOOK_UNANSWERED', 'STORY_LINK_MISSING', 'STORY_THREAD_MISSING'])

    def test_a_list_of_ands_stops(self):
        body = good()
        body['beats'] = [body['beats'][0]] + [beat(f'x{n}', 'explain', 0.15, link='and') for n in range(4)] + \
            [beat('p', 'climax', 0.25, 1.0, link='so', answers=['h'])]
        errors, _ = story_core.lint_story(body, 30)
        self.assertIn('STORY_LIST', codes(errors))   # four in a row, over the study set's run
        body['beats'][2]['link'] = 'but'
        errors, _ = story_core.lint_story(body, 30)
        self.assertNotIn('STORY_LIST', codes(errors))   # broken by a but: 3 of 5 are 'and', a run of 2

    def test_answers_name_earlier_beats_and_a_thread_of_a_known_kind(self):
        body = good()
        body['beats'][1]['answers'] = ['p']
        body['thread']['kind'] = 'vibes'
        body['direction'] = {'thread': 'time'}
        errors, _ = story_core.lint_story(body, 30, {'thread': story_core.load_axes()['thread']})
        self.assertTrue({'STORY_ANSWERS_UNKNOWN', 'STORY_THREAD_UNKNOWN', 'STORY_THREAD_DIFFERS'} <= set(codes(errors)))

    def test_an_early_answer_a_dangling_question_an_open_ending_and_no_doubt_are_said(self):
        body = good()
        body['beats'][1]['link'] = 'so'                 # no 'but' in 60 s
        body['beats'][2]['answers'] = ['h']             # the hook answered at 0.4 ... then early with a longer hook share
        body['beats'][0]['length_share'] = 0.02; body['beats'][1]['length_share'] = 0.08; body['beats'][3]['length_share'] = 0.5
        body['beats'][4]['answers'] = []                # the ending does not come back
        _, warnings = story_core.lint_story(body, 60)
        self.assertTrue({'HOOK_ANSWERED_EARLY', 'QUESTION_DANGLING', 'STORY_CLOSE_OPEN', 'STORY_NO_BUT'} <= set(codes(warnings)), warnings)

    def test_a_story_approved_before_the_chain_stands_and_is_told(self):
        old = {'logline': 'x y z', 'beats': [beat('h', 'hook', 0.1), beat('p', 'climax', 0.9, 0.9)]}
        self.assertTrue(story_core.chain_unset(old))
        self.assertFalse(story_core.chain_unset(good()))
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(init_project('chain', {'request': 'a chain', 'shots': [{'shot_id': 's1', 'frame_count': 30}]}, tmp)['project_path'])
            story.materialize(project, old, 'user', 'old pick')
            report = story.check(project)
            self.assertEqual(report['errors'], [])
            self.assertIn('STORY_CHAIN_UNSET', codes(report['warnings']))
            self.assertIn('STORY_CHAIN_UNSET', codes(story.missing(project)))
            story.materialize(project, good(), 'user', 'new pick')
            self.assertEqual(story.missing(project), [])
            self.assertEqual(story.body_of(story.load(project))['thread']['kind'], 'moving')

    def test_adopting_or_approving_again_keeps_a_story_settled_before_the_chain(self):
        from studio import decisions
        old = {'logline': 'x y z', 'beats': [beat('h', 'hook', 0.1), beat('p', 'climax', 0.9, 0.9)]}
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(init_project('chain', {'request': 'a chain', 'shots': [{'shot_id': 's1', 'frame_count': 30}]}, tmp)['project_path'])
            story.materialize(project, old, 'user', 'old pick')
            decisions.adopt(project)
            report = decisions.lint(project, 'story', decisions.envelope(project, 'story')['body'])
            self.assertEqual(report['errors'], [])
            self.assertIn('STORY_CHAIN_UNSET', codes(report['warnings']))
            changed = {**old, 'logline': 'a new logline'}   # a changed body is a re-plan: the chain is asked for
            self.assertIn('STORY_THREAD_MISSING', codes(decisions.lint(project, 'story', changed)['errors']))

    def test_the_stake_and_the_screen(self):
        """S1-S5 (2026-10-20): the hook seen, a but seen, no tour, a stake closed at the end, the close back at the hook."""
        body = good()
        body['beats'][0]['on_screen'] = {'event': 'does the lever shift the chain?', 'kind': 'anomaly'}   # a line, not an event
        errors, _ = story_core.lint_story(body, 30)
        self.assertIn('HOOK_UNSEEN', codes(errors))
        body = good()
        body['beats'][1].pop('on_screen')
        body.pop('stake')
        body['beats'][4].pop('returns_to')
        errors, warnings = story_core.lint_story(body, 30)
        self.assertEqual(errors, [])
        self.assertTrue({'TURN_SPOKEN_ONLY', 'STAKE_MISSING', 'CLOSE_NOT_RETURNING'} <= set(codes(warnings)), warnings)
        body = good()
        body['stake']['closed_by'] = 'p'
        body['beats'][2]['returns_to'] = 'c'
        errors, warnings = story_core.lint_story(body, 30)
        self.assertIn('STAKE_NOT_CLOSED', codes(warnings))
        self.assertIn('STORY_RETURNS_UNKNOWN', codes(errors))

    def test_a_tour_is_said_past_the_study_sets_run(self):
        body = good()
        run = story_core.rule('TOUR_RUN_MAX') + 1
        middle = [beat(f'x{n}', 'explain', 0.5 / run, link='so') for n in range(run)]
        body['beats'] = body['beats'][:2] + middle + [beat('p', 'climax', 0.1, 1.0, link='so', answers=['h']),
                                                     beat('c', 'close', 0.1, 0.3, link='so', answers=['h'], returns_to='h')]
        self.assertIn('STORY_TOUR', codes(story_core.lint_story(body, 30)[1]))
        body['beats'][3]['raises'] = 'and then?'
        self.assertNotIn('STORY_TOUR', codes(story_core.lint_story(body, 30)[1]))

    def test_a_story_settled_with_the_chain_but_before_the_stake_stands(self):
        from studio import decisions
        chained = good()
        chained.pop('stake')
        for b in chained['beats']:
            b.pop('on_screen', None); b.pop('returns_to', None)
        self.assertEqual(story_core.unset_groups(chained), {'stake'})
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(init_project('chain', {'request': 'a chain', 'shots': [{'shot_id': 's1', 'frame_count': 30}]}, tmp)['project_path'])
            story.materialize(project, chained, 'user', 'old pick')
            self.assertEqual(codes(story.missing(project)), ['STORY_STAKE_UNSET'])
            report = story.check(project)
            self.assertEqual(report['errors'], [])
            self.assertNotIn('STAKE_MISSING', codes(report['warnings']))
            decisions.adopt(project)
            report = decisions.lint(project, 'story', decisions.envelope(project, 'story')['body'])
            self.assertEqual(report['errors'], [])
            self.assertIn('STORY_STAKE_UNSET', codes(report['warnings']))
            changed = {**chained, 'logline': 'a new logline'}   # a re-plan answers to the stake too
            self.assertIn('HOOK_UNSEEN', codes(decisions.lint(project, 'story', changed)['errors']))

    def test_an_unsaid_but_is_said(self):
        lines = [{'line_id': 'l1', 'text': 'a', 'story_beat_id': 'h'}, {'line_id': 'l2', 'text': 'b', 'story_beat_id': 'p'}]
        _, warnings = story_core.script_lines(good(), lines)
        self.assertIn('STORY_LINK_UNSAID', codes(warnings))   # s turns against the hook and has no line
        self.assertIn('STORY_BEAT_UNSAID', codes(warnings))   # t is a so beat: the picture may tell it

    def test_the_thread_axis_is_drawn_and_an_old_axis_value_in_the_taste_is_ignored(self):
        axes = story_core.load_axes()
        self.assertIn('thread', axes)
        self.assertNotIn('journey_through_parts', story_core.drawable(axes['arc']))   # retired: never drawn ...
        drawn = story_core.pick_directions(axes, 3, preference={'arc': {'journey_through_parts': 3.0}}, seed=1)
        self.assertEqual(len({d['axes']['thread'] for d in drawn}), 3)
        self.assertTrue(all(d['axes']['arc'] != 'journey_through_parts' for d in drawn))
        old = {**good(), 'direction': {'hook': 'myth', 'arc': 'journey_through_parts', 'angle': 'observer', 'tone': 'calm'}}
        self.assertEqual(story_core.lint_story(old, 30, axes)[0], [])   # ... and still read where it was drawn
        for value in axes['thread']['values'].values():
            self.assertTrue(value['means'] and value['orders_by'] and value['devices'])


def row(shot_id, parts=(), **extra):
    return {'shot_id': shot_id, 'len_s': 2.0, 'parts': list(parts), **extra}


THREAD = {'beats': [], 'thread': {'kind': 'moving', 'carrier': 'the chain', 'carrier_ids': ['bike/chain']}}


class ThreadOnScreenTest(unittest.TestCase):
    def test_planned_shots_hold_the_thread_or_say_so(self):
        rows = [row('a', ['bike/chain']), row('b', ['bike/lever']), row('c', ['bike']), row('d', ['bike/chain/link']),
                row('e', ['bike/chain'])]
        self.assertEqual(reel_flow.thread_lines(rows, THREAD), [])   # 1 of 5 off; the whole bike shows its chain, a link is of it
        rows = [row('a', ['bike/chain'])] + [row(x, ['bike/cage']) for x in 'bcde']
        self.assertIn('4 of 5', reel_flow.thread_lines(rows, THREAD)[0])
        for r in rows[1:]:
            r['departs'] = ['thread_off_screen']
        self.assertEqual(reel_flow.thread_lines(rows, THREAD), [])   # departed shots are neither counted nor in a run
        self.assertEqual(reel_flow.thread_lines(rows, {'beats': []}), [])   # no thread named, nothing to hold

    def test_a_hand_over_passes_the_thread(self):
        rows = [row('a', ['pipe/water'], thread_to=['pipe/air'])] + [row(x, ['pipe/air']) for x in 'bcd']
        story_ = {'beats': [], 'thread': {'kind': 'moving', 'carrier': 'water', 'carrier_ids': ['pipe/water']}}
        self.assertEqual(reel_flow.thread_lines(rows, story_), [])
        rows[0].pop('thread_to')
        self.assertTrue(reel_flow.thread_lines(rows, story_))

    def test_a_world_element_carries_the_planned_thread_but_not_a_whole_one(self):
        self.assertTrue(reel_flow.thread_seen({'parts': [], 'elements': ['bike/chain']}, ['bike/chain']))
        self.assertFalse(reel_flow.thread_seen({'parts': [], 'elements': ['bike']}, ['bike/chain']))

    def test_built_shots_are_read_on_their_frames_and_unmeasured_is_unknown(self):
        self.assertTrue(reel_flow.thread_seen({'visible': {'bike/chain': True}}, ['bike/chain']))
        self.assertFalse(reel_flow.thread_seen({'visible': {'bike/chain': False}}, ['bike/chain']))
        self.assertFalse(reel_flow.thread_seen({'visible': {'bike/cage': True}}, ['bike/chain']))
        self.assertIsNone(reel_flow.thread_seen({'visible': None}, ['bike/chain']))
        self.assertIsNone(reel_flow.thread_seen({'visible': {'bike/chain': None}}, ['bike/chain']))
        self.assertIsNone(reel_flow.thread_seen({'visible': {}, 'elements': ['bike/chain']}, ['bike/chain']))
        rows = [row('a', visible={'bike/chain': True})] + [row(x, visible=None) for x in 'bcde']
        self.assertEqual(reel_flow.thread_lines(rows, THREAD), [])   # unknown is not counted and breaks the run

    def test_part_visible_reads_pixels_and_what_is_in_front(self):
        shot = {'show': [{'at': 'start', 'parts': ['bike/chain', 'bike/cage'], 'how': 'exterior'}]}
        frames = [{'shapes': {'key:bike/chain': {'px': 40}, 'key:bike/cage': {'px': 0}}} for _ in range(4)]
        report = {'summary': {'key_parts': ['bike/chain', 'bike/cage']}, 'frames': frames}
        out = reel_flow.part_visible(shot, report, {'bike/chain': 0.2})
        self.assertEqual(out, {'bike/cage': False, 'bike/chain': True})
        self.assertFalse(reel_flow.part_visible(shot, report, {'bike/chain': 0.9})['bike/chain'])
        self.assertEqual(reel_flow.part_visible(shot, {'frames': []}, {}), {'bike/cage': None, 'bike/chain': None})
        unclassed = {'summary': {'key_parts': ['bike/chain']}, 'frames': frames}   # the probe never classed the cage
        self.assertIsNone(reel_flow.part_visible(shot, unclassed, {})['bike/cage'])
        whole = {'show': [{'at': 'start', 'parts': ['bike'], 'how': 'exterior'}]}   # a whole read through its own classes
        self.assertTrue(reel_flow.part_visible(whole, unclassed, {})['bike'])

    def test_rule_14_and_its_devices(self):
        self.assertIn(14, [r[0] for r in reel_flow.RULES])
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(init_project('chain', {'request': 'a chain', 'shots': [{'shot_id': 's1', 'frame_count': 30}]}, tmp)['project_path'])
            story.materialize(project, good(), 'user', 'pick')
            out = reel_flow.rules(project)
            self.assertEqual(out['thread']['kind'], 'moving')
            self.assertTrue(out['thread']['devices'])

    def test_a_thread_none_of_whose_devices_is_on_screen_is_said(self):
        """2026-10-20 (canal): water was the thread and its way through the culverts the explanation; no shot drew it
        moving. A device a shot field makes (library/story_axes.json) is looked for in the built shots."""
        moving = {'thread': {'kind': 'moving', 'carrier': 'water', 'carrier_ids': ['water']}}
        still = {'show': [{'at': 'end', 'parts': ['water'], 'how': 'exterior'}], 'camera': {'move': {'params': {'follow': 0.5}}}}
        found = reel_flow.device_lines(moving, [still, still])
        self.assertTrue(found and found[0].startswith('THREAD_DEVICE_UNUSED') and 'a flow arrow on its path' in found[0], found)
        flowing = {**still, 'graphics': [{'graphic_id': 'w', 'kind': 'flow', 'anchors': ['a', 'b']}]}
        self.assertEqual(reel_flow.device_lines(moving, [still, flowing]), [])
        opened = {**still, 'show': [{'at': 'end', 'parts': ['water'], 'how': 'section'}]}
        self.assertEqual(reel_flow.device_lines(moving, [opened]), [])
        riding = {**still, 'camera': {'move': {'params': {'follow': 1.0}}}}
        self.assertEqual(reel_flow.device_lines(moving, [riding]), [])
        self.assertEqual(reel_flow.device_lines(moving, [still], silenced={'THREAD_DEVICE_UNUSED'}), [])
        self.assertEqual(reel_flow.device_lines({}, [still]), [])   # a story with no thread
        for value in story_core.load_axes()['thread']['values'].values():   # a field rule is one of the kind's devices
            for words, rule in (value.get('device_shots') or {}).items():
                self.assertIn(words, value['devices'])
                self.assertTrue(rule['path'] and ({'in', 'min', 'any'} & set(rule)))


if __name__ == '__main__':
    unittest.main()
