import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
import camera_rig_core as core  # noqa: E402

VIEW = (36.0, 'AUTO', 1920, 1080)


def straight(n=60, speed=90.0, fps=30):
    return [(0.0, speed * f / fps, 10.0) for f in range(n)]


def s_curve(n=120, speed=90.0, fps=30):
    points = [(46 * math.sin(y / 97) + 13 * math.sin(y / 43 + .6), y, 8.0) for y in range(-50, 1600)]
    table = core.arc_length_table(points)
    return [core.eval_by_arclength(points, table, 200 + speed * f / fps) for f in range(n)]


class CameraRigCoreTest(unittest.TestCase):
    def test_look_rotation_matches_blender_track_convention(self):
        q = core.look_rotation((0, 0, 0), (0, 10, 0))
        forward = core.q_rotate(q, (0, 0, -1)); up = core.q_rotate(q, (0, 1, 0))
        for a, b in zip(forward, (0, 1, 0)): self.assertAlmostEqual(a, b, 9)
        for a, b in zip(up, (0, 0, 1)): self.assertAlmostEqual(a, b, 9)

    def test_chase_keeps_offset_on_constant_velocity(self):
        rig = {'type': 'chase', 'subject': 's', 'offset_keys': [{'frame': 0, 'offset_m': [3, 4, 20]}]}
        out = core.bake(rig, 30, 60, subject=straight(), view=VIEW)
        for frame, p in zip(out['frames'], out['subject']):
            for a, b in zip(core.sub(frame['location'], p), (3, -20, 4)):
                self.assertAlmostEqual(a, b, 6)

    def test_zero_phase_smoothing_does_not_lag_constant_velocity(self):
        line = straight(90)
        smoothed = core.zero_phase_smooth(line, 0.3, 30)
        self.assertLess(max(core.length(core.sub(a, b)) for a, b in zip(line, smoothed)), 1e-3)
        jitter = [(p[0] + (0.5 if i % 2 else -0.5), p[1], p[2]) for i, p in enumerate(line)]
        smoothed = core.zero_phase_smooth(jitter, 0.3, 30)
        self.assertLess(max(abs(p[0]) for p in smoothed[10:-10]), 0.05)

    def test_screen_anchor_full_weight_places_subject_exactly(self):
        rig = {'type': 'chase', 'subject': 's', 'offset_keys': [{'frame': 0, 'offset_m': [8, 8, 22]}],
               'screen_anchor': {'x': .52, 'y': .66, 'weight': 1.0}, 'lens_keys': [{'frame': 0, 'lens_mm': 24}]}
        out = core.bake(rig, 30, 120, subject=s_curve(), view=VIEW)
        tan_x, tan_y = core.view_tangents(24, *VIEW)
        for frame, p in zip(out['frames'], out['subject']):
            x, y, _ = core.project(frame['rotation'], frame['location'], p, tan_x, tan_y)
            self.assertAlmostEqual(x, .52, 4); self.assertAlmostEqual(y, .66, 4)

    def test_framing_holds_the_horizon_then_blends_to_the_target(self):
        # a dive: camera falls toward a point far below; framing keeps the horizon at v 0.40 until frame 20
        view = (36.0, 'AUTO', 1080, 1920)
        path = [(0.0, y, 22.0 - 0.3 * y) for y in range(0, 120)]
        rig = {'type': 'flythrough', 'path': 'p', 'look_target': 't', 'speed_mps': 30, 'lens_keys': [{'frame': 0, 'lens_mm': 24}],
               'framing': {'horizon_v': 0.40, 'release_frame': 20, 'blend_frames': 13}}
        target = [(0.0, 140.0, -34.0)] * 60
        frames = core.bake(rig, 30, 60, path=path, target=target, view=view)['frames']
        tan_y = core.view_tangents(24, *view)[1]
        v = [core.horizon_v(math.radians(core.pitch_deg(f['rotation'])), tan_y) for f in frames]
        self.assertTrue(all(abs(x - 0.40) < 1e-6 for x in v[:21]), v[:21])
        plain = core.bake({k: x for k, x in rig.items() if k != 'framing'}, 30, 60, path=path, target=target, view=view)['frames']
        for a, b in zip(frames[34:], plain[34:]):     # released: same camera as without framing
            self.assertAlmostEqual(core.pitch_deg(a['rotation']), core.pitch_deg(b['rotation']), 6)
        steps = [abs(b - a) for a, b in zip(v, v[1:])]
        self.assertLess(max(steps), 0.03)              # no jump at the release

    def test_horizon_formula_matches_projection(self):
        view = (36.0, 'AUTO', 1080, 1920)
        tan_x, tan_y = core.view_tangents(24, *view)
        for pitch in (-30.0, -8.0, 0.0, 12.0):
            eye = (0.0, 0.0, 10.0)
            q = core.look_rotation(eye, (0.0, 100.0, 10.0 + 100 * math.tan(math.radians(pitch))))
            far = core.project(q, eye, (0.0, 1e7, 10.0), tan_x, tan_y)[1]
            self.assertAlmostEqual(core.horizon_v(math.radians(pitch), tan_y), far, 5)
            self.assertAlmostEqual(core.framing_pitch(far, tan_y), math.radians(pitch), 6)

    def test_portrait_sensor_fit_uses_vertical_axis(self):
        tan_x, tan_y = core.view_tangents(18, 36, 'AUTO', 1080, 1920)
        self.assertAlmostEqual(tan_y, 1.0); self.assertAlmostEqual(tan_x, 1080 / 1920)

    def test_quaternion_hemisphere_is_continuous_through_full_turn(self):
        center = [(0.0, 0.0, 0.0)] * 240
        rig = {'type': 'orbit', 'subject': 's', 'orbit': {'radius_m': 10, 'height_m': 3, 'start_deg': 0, 'deg_per_s': 90}}
        frames = core.bake(rig, 30, 240, subject=center, view=VIEW)['frames']
        for a, b in zip(frames, frames[1:]):
            self.assertGreaterEqual(sum(x * y for x, y in zip(a['rotation'], b['rotation'])), 0)
        for frame in frames:
            d = frame['location']
            self.assertAlmostEqual(math.hypot(d[0], d[1]), 10, 6); self.assertAlmostEqual(d[2], 3, 9)

    def test_pitch_limit_clamps_top_down_view(self):
        rig = {'type': 'chase', 'subject': 's', 'offset_keys': [{'frame': 0, 'offset_m': [0, 60, 1]}], 'pitch_limit_deg': 50}
        frames = core.bake(rig, 30, 30, subject=straight(30), view=VIEW)['frames']
        self.assertTrue(all(core.pitch_deg(f['rotation']) >= -50.0001 for f in frames))

    def test_roll_follows_coordinated_bank_and_is_clamped(self):
        rig = {'type': 'chase', 'subject': 's', 'offset_keys': [{'frame': 0, 'offset_m': [0, 4, 15]}], 'roll': {'follow_bank': .5, 'max_deg': 10}}
        out = core.bake(rig, 30, 120, subject=s_curve(), view=VIEW)
        rolls = [f['roll_deg'] for f in out['frames']]
        self.assertLessEqual(max(abs(r) for r in rolls), 10 + 1e-9)
        self.assertGreater(max(abs(r) for r in rolls), 1)
        self.assertGreater(max(abs(b) for b in out['bank_deg']), 20)

    def test_keys_ease_between_and_hold_outside(self):
        keys = [{'frame': 10, 'lens_mm': 20}, {'frame': 20, 'lens_mm': 30}]
        self.assertEqual(core.sample_keys(keys, 0, 'lens_mm', 35), 20)
        self.assertAlmostEqual(core.sample_keys(keys, 15, 'lens_mm', 35), 25)
        self.assertEqual(core.sample_keys(keys, 99, 'lens_mm', 35), 30)

    def test_flythrough_moves_along_path_at_speed(self):
        path = [(0.0, float(y), 2.0) for y in range(0, 500)]
        rig = {'type': 'flythrough', 'path': 'p', 'speed_mps': 25, 'look_ahead_m': 10}
        out = core.bake(rig, 30, 60, path=path, view=VIEW)
        self.assertAlmostEqual(out['subject'][30][1] - out['subject'][0][1], 25, 6)
        forward = core.q_rotate(out['frames'][10]['rotation'], (0, 0, -1))
        self.assertGreater(forward[1], .99)

    def test_shake_and_bake_are_deterministic(self):
        rig = {'type': 'chase', 'subject': 's', 'offset_keys': [{'frame': 0, 'offset_m': [2, 3, 12]}],
               'shake': {'amp_deg': .4, 'freq_hz': 3, 'seed': 7}, 'smoothing': {'position_s': .2}}
        digest = lambda: hashlib.sha256(json.dumps(core.bake(rig, 30, 90, subject=s_curve(90), view=VIEW)['frames']).encode()).hexdigest()
        self.assertEqual(digest(), digest())

    def test_procedural_overrides_win(self):
        rig = {'type': 'procedural', 'subject': 's', 'script': 'camera_rigs/x.py'}
        overrides = [{'offset_m': (0, 1, 5), 'lens_mm': 18, 'roll_deg': 3}] * 10
        frames = core.bake(rig, 30, 10, subject=straight(10), view=VIEW, overrides=overrides)['frames']
        self.assertEqual(frames[0]['lens'], 18); self.assertAlmostEqual(frames[0]['roll_deg'], 3)


class DwellTest(unittest.TestCase):
    SPEC = {'profile': 'points', 'points': [[0, 0], [0.25, 0.18], [0.52, 0.46], [0.8, 0.82], [1, 1]]}

    def test_no_dwell_is_the_profile_itself(self):
        a, b = core.timing_curve(self.SPEC), core._profile_curve(self.SPEC)
        self.assertTrue(all(a(i / 50) == b(i / 50) for i in range(51)))

    def test_dwell_lingers_at_u_and_stays_monotone(self):
        for profile in (self.SPEC, {'profile': 'ease_in_out'}, {'profile': 'linear'}, {'profile': 'burst_settle'}):
            curve = core.timing_curve({**profile, 'dwell': [{'u': 0.46, 'frac': 0.15}]})
            vals = [curve(i / 200) for i in range(201)]
            self.assertTrue(all(b >= a - 1e-12 for a, b in zip(vals, vals[1:])), profile)
            self.assertAlmostEqual(vals[0], 0.0, 9); self.assertAlmostEqual(vals[-1], 1.0, 9)
            near = [v for v in vals if 0.459 <= v <= 0.461 + core.DWELL_DRIFT]
            self.assertGreaterEqual(len(near), 0.1 * 200, profile)   # most of the 15 % sits at u

    def test_two_dwells(self):
        curve = core.timing_curve({**self.SPEC, 'dwell': [{'u': 0.3, 'frac': 0.1}, {'u': 0.8, 'frac': 0.1}]})
        vals = [curve(i / 200) for i in range(201)]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(vals, vals[1:])))
        self.assertGreaterEqual(sum(1 for v in vals if 0.3 <= v <= 0.305), 12)
        self.assertGreaterEqual(sum(1 for v in vals if 0.8 <= v <= 0.805), 12)


class TimingTest(unittest.TestCase):
    def test_burst_settle_shape(self):
        u = core.timing_curve({'profile': 'burst_settle', 'burst_frac': 0.25, 'burst_share': 0.65, 'hold_frac': 0.25, 'drift': 0.01})
        values = [u(i / 200) for i in range(201)]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(values, values[1:])))
        self.assertAlmostEqual(u(0.25), 0.65, delta=0.02)
        self.assertGreaterEqual(u(0.76), 0.99 - 1e-9)
        self.assertEqual((u(0), u(1)), (0.0, 1.0))
        self.assertGreater(u(0.1) / 0.1, 2.0)        # the head rushes: average speed well above linear
        with self.assertRaises(ValueError):
            core.timing_curve({'burst_frac': 0.8, 'hold_frac': 0.3})

    def test_points_and_classics(self):
        u = core.timing_curve({'profile': 'points', 'points': [[0, 0], [0.5, 0.8], [1, 1]]})
        self.assertAlmostEqual(u(0.5), 0.8)
        self.assertEqual(core.timing_curve({'profile': 'linear'})(0.3), 0.3)
        self.assertAlmostEqual(core.timing_curve({'profile': 'ease_in_out'})(0.5), 0.5)
        with self.assertRaises(ValueError):
            core.timing_curve({'profile': 'points', 'points': [[0, 0.5], [1, 0.2]]})

    def test_timed_flythrough_and_orbit(self):
        path = [(0.0, float(y), 2.0) for y in range(0, 300)]
        rig = {'type': 'flythrough', 'path': 'p', 'look_ahead_m': 10,
               'timing': {'profile': 'burst_settle', 'burst_frac': 0.25, 'burst_share': 0.6, 'hold_frac': 0.25, 'drift': 0.0, 'distance_m': 100}}
        out = core.bake(rig, 30, 121, path=path, view=VIEW)
        ys = [f['location'][1] for f in out['frames']]
        self.assertAlmostEqual(ys[30] - ys[0], 60.0, delta=2.0)        # 60 % of 100 m by a quarter of the shot
        self.assertAlmostEqual(ys[-1] - ys[0], 100.0, delta=1e-6)
        self.assertLess(ys[-1] - ys[90], 1e-6)                          # held still over the last quarter
        orbit = {'type': 'orbit', 'subject': 's', 'orbit': {'radius_m': 10, 'height_m': 3, 'start_deg': 0, 'deg_per_s': 999},
                 'sweep_deg': 90, 'timing': {'profile': 'linear'}}
        frames = core.bake(orbit, 30, 31, subject=[(0, 0, 0)] * 31, view=VIEW)['frames']
        end = frames[-1]['location']
        self.assertAlmostEqual(math.degrees(math.atan2(end[1], end[0])), 90.0, places=4)

    def test_keys_ride_the_progress(self):
        path = [(0.0, float(y), 2.0) for y in range(0, 300)]
        rig = {'type': 'flythrough', 'path': 'p', 'lens_keys': [{'frame': 0, 'lens_mm': 18}, {'frame': 120, 'lens_mm': 30, 'ease': 'linear'}],
               'timing': {'profile': 'burst_settle', 'burst_frac': 0.25, 'burst_share': 0.6, 'hold_frac': 0.25, 'drift': 0.0, 'distance_m': 100, 'scope': 'all'}}
        lenses = [f['lens'] for f in core.bake(rig, 30, 121, path=path, view=VIEW)['frames']]
        self.assertAlmostEqual(lenses[30], 18 + 12 * 0.6, delta=0.5)


if __name__ == '__main__':
    unittest.main()
