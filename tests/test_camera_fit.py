from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import camera_fit  # noqa: E402
from studio.motion_style import judge, shot_envelope  # noqa: E402

STYLE = {'features': {
    'burst_share': {'p25': 0.45, 'median': 0.55, 'p75': 0.7}, 'peak_t': {'p25': 0.0, 'median': 0.05, 'p75': 0.15},
    'decay_half_s': {'p25': 0.25, 'median': 0.5, 'p75': 1.0}, 'hold_frac': {'p25': 0.2, 'median': 0.4, 'p75': 0.55},
    'mean_mad': {'p25': 4.0, 'median': 6.0, 'p75': 7.5}, 'p95_mad': {'p25': 10, 'median': 14, 'p75': 20},
    'head_whip': {'p25': 1.3, 'median': 1.7, 'p75': 3.0}}}


def uniform(total):
    n = 161
    return {'u': [i / (n - 1) for i in range(n)], 'G': [total * i / (n - 1) for i in range(n)]}


class PredictTest(unittest.TestCase):
    def test_linear_timing_on_uniform_flow_is_flat(self):
        series = camera_fit.predict(uniform(300.0), {'profile': 'linear'}, 100, 1.0)
        self.assertEqual(len(series), 99)
        self.assertAlmostEqual(sum(series), 300.0, places=6)
        self.assertLess(max(series) - min(series), 1e-6)

    def test_burst_moves_motion_to_the_head(self):
        series = camera_fit.predict(uniform(300.0), {'profile': 'burst_settle', 'burst_frac': 0.25, 'burst_share': 0.65}, 100, 1.0)
        self.assertGreater(sum(series[:25]) / sum(series), 0.6)


class FitTest(unittest.TestCase):
    def test_fit_moves_a_constant_speed_shot_into_the_style(self):
        profile, frames = uniform(600.0), 100
        before = shot_envelope(camera_fit.predict(profile, {'profile': 'linear'}, frames, 1.0))
        timing, objective, evals = camera_fit.fit(profile, STYLE, frames, {'profile': 'burst_settle', 'burst_frac': 0.3, 'burst_share': 0.6, 'hold_frac': 0.2})
        after = shot_envelope(camera_fit.predict(profile, timing, frames, 1.0))
        shape = ('burst_share', 'hold_frac', 'peak_t')
        self.assertFalse(all(judge(STYLE, before)[f]['ok'] for f in shape))
        self.assertTrue(all(judge(STYLE, after)[f]['ok'] for f in shape), {f: after[f] for f in shape})
        self.assertLessEqual(evals, camera_fit.MAX_EVALS + 4)
        for name, (lo, hi) in camera_fit.BOUNDS.items():
            self.assertTrue(lo <= timing[name] <= hi)


if __name__ == '__main__':
    unittest.main()
