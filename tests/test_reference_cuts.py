"""Shot boundaries against a reference's measured cuts: a plain cut where the reference camera runs on is refused
(archcut3, 2026-10-08: a cut at 93 f the reference does not have made the view jump), a declared transition is said,
a reference cut we lack is warned, and a timeline a few frames longer keeps the cut places."""
import json
import tempfile
import unittest
from pathlib import Path

from studio.common import StudioError
from studio.qa_motion import cut_motion
from studio.references import check_cuts, cut_problems


def project(*counts, fps=30):
    shots, start = [], 0
    for i, n in enumerate(counts):
        shots.append({'shot_id': f's{i + 1:02d}', 'start_frame': start, 'frame_count': n})
        start += n
    return {'output': {'fps': fps}, 'shots': shots}


class ReferenceCutsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        # archcut3's reference: one camera for 6 s, a short transition 181-196 f, then the next shot
        (self.path / 'reference_cuts.json').write_text(json.dumps({'duration_s': 293 / 30, 'cuts_s': [181 / 30, 196 / 30],
                                                                   'cut_spans_s': [[181 / 30, 196 / 30]]}))

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, data, transitions=None):
        return cut_problems(self.path, data, lambda sid: {'transition': (transitions or {}).get(sid)} if (transitions or {}).get(sid) else {})

    def test_archcut3_shot_list_is_refused(self):
        errors, _ = self.problems(project(93, 88, 112))
        self.assertEqual([(e['code'], e['shot_id']) for e in errors], [('REFERENCE_CUT_MISMATCH', 's02')])
        with self.assertRaises(StudioError) as error:
            check_cuts(self.path, project(93, 88, 112), lambda sid: {})
        self.assertEqual(error.exception.code, 'REFERENCE_CUT_MISMATCH')

    def test_one_continuous_shot_passes(self):
        errors, warnings = self.problems(project(181, 112))
        self.assertEqual((errors, warnings), ([], []))
        self.assertEqual(self.problems(project(196, 97)), ([], []))        # anywhere inside the transition span

    def test_declared_transition_is_said_not_refused(self):
        errors, warnings = self.problems(project(93, 88, 112), {'s02': {'kind': 'dissolve'}})
        self.assertEqual(errors, [])
        self.assertTrue(any(w.startswith('REFERENCE_CUT_DIFFERS') for w in warnings))

    def test_missing_reference_cut_warns(self):
        errors, warnings = self.problems(project(293))
        self.assertEqual(errors, [])
        self.assertTrue(any(w.startswith('REFERENCE_CUT_MISSING') for w in warnings))

    def test_a_slightly_longer_timeline_keeps_the_cut(self):
        self.assertEqual(self.problems(project(184, 114))[0], [])           # 298 f against 293: the cut scales to 184 f

    def test_no_reference_no_check(self):
        (self.path / 'reference_cuts.json').unlink()
        self.assertEqual(self.problems(project(93, 88, 112)), ([], []))


class CutMotionTest(unittest.TestCase):
    shots = [{'shot_id': 'a', 'start_frame': 0}, {'shot_id': 'b', 'start_frame': 20}]

    def test_fast_into_still_is_a_jolt(self):
        diffs = [30.0] * 19 + [80.0] + [0.5] * 20          # archcut3 s01 -> s02: 32.7 -> 0.56
        row = cut_motion(diffs, self.shots)[0]
        self.assertTrue(row['warnings'] and row['warnings'][0].startswith('CUT_MOTION_JUMP'))

    def test_matched_motion_and_still_cuts_pass(self):
        self.assertEqual(cut_motion([10.0] * 19 + [80.0] + [9.0] * 20, self.shots)[0]['warnings'], [])
        self.assertEqual(cut_motion([0.1] * 19 + [80.0] + [0.4] * 20, self.shots)[0]['warnings'], [])   # both still

    def test_a_hold_cut_into_a_burst_is_grammar(self):
        """Hold, then cut into a shot that starts with a burst: the reference reel's own cuts (9 of 16)."""
        self.assertEqual(cut_motion([0.4] * 19 + [80.0] + [15.0] * 20, self.shots)[0]['warnings'], [])

    def test_declared_transition_is_not_judged(self):
        shots = [self.shots[0], {**self.shots[1], 'transition': 'whip'}]
        self.assertEqual(cut_motion([30.0] * 19 + [80.0] + [0.5] * 20, shots)[0]['warnings'], [])


if __name__ == '__main__':
    unittest.main()
