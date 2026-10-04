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


if __name__ == '__main__':
    unittest.main()
