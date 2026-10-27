"""A flow runs when the cut says it runs: its window (`at`, a share of the cut, the whole cut when left out) says when it
is drawn, and it is located, judged and asked for room on the picture the cut shows when it starts. Why (2026-10-28, the
canal proof's c05): the lake's water bursts into the wall's tunnel only once the valve lifts (the change); its way was
located and judged on the first picture, where the valve still shut the tunnel - the judge called it off - and the preview
would have drawn water streaming through closed steel from the cut's first frame. A flow the change lets loose (a valve,
a gate, a dam, a switch) runs over the picture after it; a flow that runs all along (air falling through a room) is
unchanged."""
from pathlib import Path
import tempfile
import unittest

from studio import decisions
from studio.common import write_json
from tests import studio2_fixture as fx
from tests.test_studio2_foundation import FakeCodex
from tests.test_studio2_pictures import answer_all, marks_ok


def released(at=(0.5, 1.0)):
    """c1: a gate lifts over 0.1-0.5 of the cut and the air rushes through after it (the flow's window `at`)."""
    body = fx.body()
    cut = body['cuts'][0]
    cut['change'] = {'kind': 'state', 'what': 'the floor gate lifts open', 'at': [0.1, 0.5], 'parts': ['floor']}
    cut['frames']['last'] = {'shows': 'the same bay, the floor gate lifted open'}
    cut['cutaway']['camera'] = {'move': 'still'}
    if at is not None:
        cut['cutaway']['flow'][0]['at'] = list(at)
    return body


class Recorder:
    """Marks that keep each flow's alpha (motion.draw's flows) and draw nothing."""
    def __init__(self, w=108, h=192):
        self.w, self.h, self.flows = w, h, []

    def flow(self, pts, t, colour, alpha=1.0, width=None):
        self.flows.append(alpha)

    def __getattr__(self, name):
        return lambda *a, **k: None


class FlowWindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = fx.project(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def picked(self, body):
        from studio2 import checks, frames
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        cut = body['cuts'][0]
        for frame in frames.needed(cut):
            frames.make(self.path, 'c1', frame, runner=FakeCodex(self.root, ['image']))
            checks.check(self.path, 'c1', frame, reader=answer_all(), matcher=answer_all())
            frames.pick(self.path, 'c1', frame, 1, 'a test pick: it shows what the cut needs')
        return cut

    def test_a_window_that_ends_before_it_starts_is_refused(self):
        errors = decisions.lint(self.path, 'cuts', released(at=(0.8, 0.3)))['errors']
        self.assertTrue(any(e.startswith('CUT_FLOW_WINDOW: c1') for e in errors), errors)
        self.assertFalse([e for e in decisions.lint(self.path, 'cuts', released())['errors'] if e.startswith('CUT_FLOW_WINDOW')])

    def test_a_flow_runs_over_the_picture_shown_when_it_starts(self):
        from studio2 import frames
        cut = released()['cuts'][0]
        self.assertEqual(frames.flow_picture(cut, 0), 'last')               # starts once the gate is open
        self.assertEqual(frames.flow_picture(released(at=None)['cuts'][0], 0), 'first')   # all along: the first, as before
        self.assertEqual(frames.flow_picture(released(at=(0.0, 0.4))['cuts'][0], 0), 'first')

    def test_room_for_a_released_flow_is_asked_of_the_picture_it_runs_over(self):
        from studio2 import checks
        rows = checks.table()['rows']
        cut = released()['cuts'][0]
        ids = lambda frame: [q['id'] for q in checks.questions(cut, frame, rows, False)]   # noqa: E731
        self.assertNotIn('flow_room:0', ids('first'))
        self.assertIn('flow_room:0', ids('last'))
        steady = released(at=None)['cuts'][0]
        self.assertIn('flow_room:0', [q['id'] for q in checks.questions(steady, 'first', rows, False)])
        self.assertNotIn('flow_room:0', [q['id'] for q in checks.questions(steady, 'last', rows, False)])

    def test_a_released_flow_is_located_and_judged_on_the_last_picture(self):
        from studio2 import locate, motion
        cut = self.picked(released())
        asked = {}

        def reader(images, prompt, schema, target):
            frame = Path(images[0]).stem.split('_')[0]
            asked[frame] = schema['properties']['flows']['minItems']
            write_json(target, {'answer': {'parts': [{'id': 'filters', 'box': [0.2, 0.2, 0.5, 0.4], 'point': [0.35, 0.3], 'shown': True},
                                                     {'id': 'floor', 'box': [0.2, 0.5, 0.5, 0.7], 'point': [0.35, 0.6], 'shown': True}],
                                           'flows': [{'index': 0, 'ways': [[[0.5, 0.1], [0.5, 0.5], [0.55, 0.9]]]}] * asked[frame],
                                           'element': None, 'focus': [0.5, 0.45]}})
        row = locate.locate(self.path, 'c1', reader=reader, checker=marks_ok)
        self.assertEqual(asked, {'first': 0, 'last': 1})
        self.assertEqual(row['places']['flows'], [])
        self.assertEqual([f['index'] for f in row['edits']['last']['flows']], [0])
        self.assertTrue(any(m['mark'] == 'last flow:0' for m in row['check']), row['check'])
        plans = motion.plans_of(self.path, cut, locate.places_of(self.path, cut), 108, 192, 120)
        self.assertEqual(len(plans['first']['flows']), 1)                    # every picture's plan carries it
        self.assertEqual(len(plans['last']['flows']), 1)

    def test_a_flow_is_drawn_only_in_its_window(self):
        from studio2 import locate, motion
        cut = self.picked(released())

        def reader(images, prompt, schema, target):
            n = schema['properties']['flows']['minItems']
            write_json(target, {'answer': {'parts': [{'id': 'filters', 'box': [0.2, 0.2, 0.5, 0.4], 'point': [0.35, 0.3], 'shown': True},
                                                     {'id': 'floor', 'box': [0.2, 0.5, 0.5, 0.7], 'point': [0.35, 0.6], 'shown': True}],
                                           'flows': [{'index': 0, 'ways': [[[0.5, 0.1], [0.5, 0.5], [0.55, 0.9]]]}] * n,
                                           'element': None, 'focus': [0.5, 0.45]}})
        locate.locate(self.path, 'c1', reader=reader, checker=marks_ok)
        plans = motion.plans_of(self.path, cut, locate.places_of(self.path, cut), 108, 192, 120)   # 4 s: the window 2-4 s
        alpha = lambda t: max(motion.draw(Recorder(), motion.plan_at(plans, t), t).flows or [0.0])   # noqa: E731
        self.assertEqual(alpha(1.0), 0.0)
        self.assertGreater(alpha(3.5), 0.5)
        steady = self.root / 'steady'
        self.path = fx.project(steady)
        cut = self.picked(released(at=None))
        locate.locate(self.path, 'c1', reader=reader, checker=marks_ok)
        plans = motion.plans_of(self.path, cut, locate.places_of(self.path, cut), 108, 192, 120)
        self.assertGreater(max(motion.draw(Recorder(), motion.plan_at(plans, 1.0), 1.0).flows), 0.5)   # all along: drawn early


class ChangedPartTest(unittest.TestCase):
    """A part the change acts on is judged on an edit as it is once the change has happened. Why (2026-10-28, the canal
    proof's c13): the chamber's water drains away (the change); on the last picture its dot sat on the drained water and
    the judge called it off because the part's words describe the water before (full to the brim)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = fx.project(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_judge_reads_a_changed_part_on_an_edit_as_after_the_change(self):
        from studio2 import checks, frames, locate
        body = released(at=None)
        decisions.write(self.path, 'cuts', body, agent_note=fx.TEST_NOTE)
        for frame in frames.needed(body['cuts'][0]):
            frames.make(self.path, 'c1', frame, runner=FakeCodex(self.root, ['image']))
            checks.check(self.path, 'c1', frame, reader=answer_all(), matcher=answer_all())
            frames.pick(self.path, 'c1', frame, 1, 'a test pick: it shows what the cut needs')
        asked = {}

        def checker(images, prompt, schema, target):
            asked[Path(images[0]).stem] = prompt
            marks_ok(images, prompt, schema, target)

        def reader(images, prompt, schema, target):
            n = schema['properties']['flows']['minItems']
            write_json(target, {'answer': {'parts': [{'id': 'filters', 'box': [0.2, 0.2, 0.5, 0.4], 'point': [0.35, 0.3], 'shown': True},
                                                     {'id': 'floor', 'box': [0.2, 0.5, 0.5, 0.7], 'point': [0.35, 0.6], 'shown': True}],
                                           'flows': [{'index': 0, 'ways': [[[0.5, 0.1], [0.5, 0.5], [0.55, 0.9]]]}] * n,
                                           'element': None, 'focus': [0.5, 0.45]}})
        locate.locate(self.path, 'c1', reader=reader, checker=checker)
        after = 'as it is once this has happened: the floor gate lifts open'
        self.assertNotIn(after, asked['overlay'])                      # the first picture: before the change
        last = asked['overlay_last']
        self.assertIn(after, last.split('part:floor')[1].split(';')[0])    # the changed part, on the edit
        self.assertNotIn(after, last.split('part:filters')[1].split(';')[0])   # a part the change leaves alone


if __name__ == '__main__':
    unittest.main()
