"""A part a shot must show that reads a little under its min_px is short, not missing (user, 2026-10-17: "사소한건 그냥
넘어가도 될것같고"): the build's detail check says MUST_SEE_SHORT (taste) within frame_probe_core.SHORTFALL_SHARE and keeps
DETAIL_NOT_SHOWN (broken) below it. A kettle's spout, not a rocket (N+1)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
import frame_probe_core as core  # noqa: E402
from studio.gates import SOFTENABLE  # noqa: E402


def row(frame, thick_share):
    # a spout one cell wide on a 144 x 256 grid, thick_share of the frame height tall
    return {'frame': frame, 'opaque_share': 0.5, 'subject_share': 0.3, 'support_share': 0.2, 'key_px': {'kettle/spout': 40},
            'key_share': {'kettle/spout': 0.01}, 'key_edges': {}, 'shapes': {
                'key:kettle/spout': {'px': 40, 'share': 0.01, 'col_v': thick_share, 'bbox': [0.4, 0.4, 0.6, 0.4 + thick_share]}}}


class VerdictTest(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(core.px_verdict(32.0, 32), 'ok')
        self.assertEqual(core.px_verdict(28.8, 32), 'short')     # 0.9 x
        self.assertEqual(core.px_verdict(22.4, 32), 'thin')      # 0.7 x
        self.assertEqual(core.px_verdict(0.0, 0), 'ok')          # no minimum declared

    def test_the_build_check_says_short_and_refuses_thin(self):
        detail = {'id': 'kettle/spout', 'min_px': 32, 'why': 'it pours'}
        for share, code in ((0.9 * 32 / 1920, 'MUST_SEE_SHORT'), (0.7 * 32 / 1920, 'DETAIL_NOT_SHOWN'), (1.1 * 32 / 1920, None)):
            failures, _ = core.judge([row(0, share)], [{'id': 'kettle/spout', 'source': 'details'}], 'explain', True, 24,
                                     details=[detail])
            codes = [f['code'] for f in failures if f.get('part') == 'kettle/spout']
            self.assertEqual(codes, [code] if code else [], (share, failures))

    def test_short_is_taste_and_in_every_list(self):
        self.assertEqual(SOFTENABLE['MUST_SEE_SHORT'][0], 'taste')
        self.assertIn('MUST_SEE_SHORT', core.SOFTENABLE)


if __name__ == '__main__':
    unittest.main()
