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


class DeclaredProfileTest(unittest.TestCase):
    """archcut3 s02/s03: a constant push and a slow retreat were declared linear; the fit proposed burst/settle anyway."""
    def run_fit(self, timing, family=None):
        import tempfile
        from unittest import mock
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(tmp.name)
        (path / 'shots/s02/versions/v0001').mkdir(parents=True)
        shot = {'shot_id': 's02', 'scene_version': 'v0001', 'duration_frames': 88, 'revision': 1,
                'camera': {'move': {'style': 'archcutaway', 'timing': timing}}}
        report = {'rig': {'timing': timing}, 'mark_progress': {}}
        with mock.patch('studio.project.project_dir', return_value=path), mock.patch('studio.project.load_shot', return_value=shot), \
                mock.patch('studio.project.load_project', return_value={'output': {'fps': 30}}), \
                mock.patch.object(camera_fit, 'read_json', return_value=report), mock.patch.object(camera_fit, 'load', return_value=STYLE), \
                mock.patch.object(camera_fit, '_probe', return_value=uniform(600.0)), mock.patch.object(camera_fit, '_calibration', return_value=1.0):
            return camera_fit.camera_fit(path, 's02', family=family)

    def test_a_declared_linear_rhythm_is_scored_not_replaced(self):
        out = self.run_fit({'profile': 'linear'})
        self.assertEqual((out['family'], out['evals'], out['timing']['profile']), ('linear', 0, 'linear'))
        self.assertTrue(any(h.startswith('DECLARED_PROFILE_KEPT') for h in out['hints']))

    def test_the_burst_family_is_searched_when_asked_or_undeclared(self):
        self.assertEqual(self.run_fit({'profile': 'linear'}, family='burst_settle')['timing']['profile'], 'burst_settle')
        self.assertEqual(self.run_fit({'profile': 'burst_settle', 'burst_frac': 0.3})['family'], 'burst_settle')


if __name__ == '__main__':
    unittest.main()
