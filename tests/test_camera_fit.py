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



def bunched(total):
    """A path whose flow sits mostly in its second half (the section dive: little moves until the camera nears it)."""
    n = 161
    g = [total * (0.2 * (i / (n - 1)) + 0.8 * (i / (n - 1)) ** 3) for i in range(n)]
    return {'u': [i / (n - 1) for i in range(n)], 'G': g}


class ReferenceTimingTest(unittest.TestCase):
    """camera fit --reference: our path spends its flow in the reference shot's shares over time (2026-10-08)."""
    def test_predicted_motion_follows_the_reference_shape(self):
        count = 181
        target = [18 - 10 * (j / 179) for j in range(180)]              # fast from the first frame, easing off
        timing = camera_fit.timing_from_motion(bunched(300.0), target, count, {'profile': 'points', 'scope': 'all'})
        points = timing['points']
        self.assertEqual((points[0], points[-1]), ([0.0, 0.0], [1.0, 1.0]))
        self.assertTrue(all(b[0] > a[0] and b[1] >= a[1] for a, b in zip(points, points[1:])))
        self.assertEqual(timing['scope'], 'all')
        series = camera_fit.predict(bunched(300.0), timing, count, 1.0)
        windows = lambda xs: camera_fit._windows(xs, 15)  # noqa: E731
        self.assertGreater(camera_fit._correlation(windows(series), windows(target)), 0.95)
        linear = camera_fit.predict(bunched(300.0), {'profile': 'linear'}, count, 1.0)
        self.assertLess(camera_fit._correlation(windows(linear), windows(target)), 0.0)   # linear on this path rushes late

    def test_a_still_stretch_of_the_reference_stays_still(self):
        count = 91
        target = [10.0] * 30 + [0.0] * 30 + [10.0] * 30
        series = camera_fit.predict(uniform(300.0), camera_fit.timing_from_motion(uniform(300.0), target, count), count, 1.0)
        self.assertLess(max(series[36:54]), 0.15 * max(series))

    def test_an_arrive_mark_is_held_and_the_shape_kept_around_it(self):
        count = 181
        target = [18 - 10 * (j / 179) for j in range(180)]
        timing = camera_fit.timing_from_motion(bunched(300.0), target, count, anchors=[(39, 0.4)])
        self.assertIn([round(39 / 180, 5), 0.4], timing['points'])
        arrive = [{'u': 0.4, 'not_before_s': 1.3}]
        self.assertEqual(camera_fit.arrive_penalty(timing, arrive, count, 30), 0.0)
        series = camera_fit.predict(bunched(300.0), timing, count, 1.0)
        after = camera_fit._windows(series[40:], 15)
        self.assertGreater(camera_fit._correlation(after, camera_fit._windows(target[40:], 15)), 0.9)

if __name__ == '__main__':
    unittest.main()
