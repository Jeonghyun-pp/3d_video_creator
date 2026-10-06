"""Frame probe verdicts from class pixel counts (blender_ops/frame_probe_core.py), no Blender."""
import unittest

from studio.blender_ops import frame_probe_core as core
from studio import gates


def row(frame, subject=4000, support=10000, keys=None, edges=None, near=None, total=36864):
    keys = keys or {}
    borders = {s: {'subject': False, 'key': [k for k, sides in (edges or {}).items() if s in sides]} for s in core.SIDES}
    r = core.metrics({'subject': subject, 'support': support, 'key': keys, 'background': total - subject - support - sum(keys.values())}, borders, total)
    return {'frame': frame, **r, 'near_cut_share': near}


class FrameProbeCoreTest(unittest.TestCase):
    def codes(self, rows, keys=(), role='explain', has_subject=True, count=30, exempt=()):
        return sorted(f['code'] for f in core.judge(rows, list(keys), role, has_subject, count, exempt)[0])

    def test_frames_are_even_plus_required_and_deterministic(self):
        self.assertEqual(core.pick_frames(30), core.pick_frames(30))
        self.assertEqual(len(core.pick_frames(30)), 8)
        self.assertEqual(len(core.pick_frames(600)), 12)
        self.assertIn(7, core.pick_frames(30, {7, 99}))
        self.assertNotIn(99, core.pick_frames(30, {7, 99}))
        self.assertTrue({0, 5, 10} <= core.required_frames([{'id': 'a', 'from_frame': 0, 'to_frame': 10}], 30))

    def test_a_good_frame_passes(self):
        self.assertEqual(self.codes([row(f, keys={'gear': 900}) for f in range(0, 30, 4)], [{'id': 'gear'}]), [])

    def test_hard_codes(self):
        self.assertEqual(self.codes([row(0, subject=0, support=100)]), ['FRAME_EMPTY', 'FRAME_SUBJECT_SMALL'])
        self.assertEqual(self.codes([row(0, subject=0, support=9000), row(1)]), [])   # out of frame for a while: the shot's choice
        self.assertEqual(self.codes([row(0, near=0.02)]), ['FRAME_NEAR_CLIP_CUT'])
        self.assertEqual(self.codes([row(0, subject=0, support=100)], exempt=[0]), [])   # a whip frame

    def test_key_part_role_decides_the_severity(self):
        hidden = [row(f, keys={'gear': 3}) for f in (0, 15, 29)]
        self.assertEqual(self.codes(hidden, [{'id': 'gear'}], role='explain'), ['KEY_PART_INVISIBLE'])
        failures = core.judge(hidden, [{'id': 'gear'}], 'mood', True, 30)[0]
        self.assertTrue(all(core.is_warning_by_role(f) for f in failures))
        # outside its window the part may hide
        self.assertEqual(self.codes([row(0, keys={'gear': 900}), row(29, keys={'gear': 0})], [{'id': 'gear', 'from_frame': 0, 'to_frame': 5}]), [])

    def test_soft_codes_are_softenable_and_the_rest_are_not(self):
        rows = [row(f, subject=300, support=33000, keys={'gear': 60}, edges={'gear': ['left']}) for f in range(0, 30, 4)]
        self.assertEqual(self.codes(rows, [{'id': 'gear'}]), ['FRAME_EDGE_CUT', 'FRAME_SUBJECT_SMALL', 'KEY_PART_SMALL'])
        implied = [{'id': 'gear', 'source': 'rig'}]
        self.assertEqual(self.codes(rows, implied), ['FRAME_SUBJECT_SMALL'])   # implied keys: only presence
        self.assertTrue(set(core.SOFTENABLE) <= set(gates.SOFTENABLE))
        self.assertFalse(set(core.HARD + ('KEY_PART_INVISIBLE',)) & set(gates.SOFTENABLE))
        look_first = gates.severity_map({'policy': {'strictness': 'look-first'}})
        self.assertTrue(all(not gates.is_error(c, look_first) for c in core.SOFTENABLE))
        self.assertTrue(all(gates.is_error(c, look_first) for c in core.HARD))


if __name__ == '__main__':
    unittest.main()
