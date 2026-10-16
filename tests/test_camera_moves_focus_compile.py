"""Exercise compiler wiring with a deterministic focus solve; real Blender previews remain separate."""
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

OPS = Path(__file__).resolve().parents[1] / 'studio/blender_ops'
sys.path.insert(0, str(OPS))
import camera_moves_core as core
import camera_rig_core as rig_core


def module(name, **members):
    result = ModuleType(name)
    result.__dict__.update(members)
    return result


class FocusCompileTest(unittest.TestCase):
    def setUp(self):
        self.frame = 1
        self.aims = {}
        scene = SimpleNamespace(frame_set=lambda f: setattr(self, 'frame', f))
        stubs = {
            'bpy': module('bpy', context=SimpleNamespace(scene=scene)),
            'numpy': module('numpy'),
            'mathutils': module('mathutils', Vector=None),
            'mathutils.bvhtree': module('mathutils.bvhtree', BVHTree=None),
            'scene_roles': module('scene_roles', counts=None),
            'scene_geometry': module('scene_geometry'),
            'scene_tools': module('scene_tools', anchor_for=None, object_by_id=None),
            'scene_index': module('scene_index', resolve_ref=lambda ref, index=None: None),
            'camera_frame': module('camera_frame', solve_focus=self.solve),
        }
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location('focus_compile_under_test', OPS / 'camera_moves.py')
            self.moves = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.moves)
        self.modules = patch.dict(sys.modules, {'camera_frame': stubs['camera_frame']})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.geo = {'points': {'top': (0, 0, 35), 'bottom': (0, 0, 3)},
                    'boxes': {'tower': ((-1.85, -1.85, 0), (1.85, 1.85, 41))}}
        self.moves.resolve = lambda params, frame=1: self.geo
        self.moves._polyline = lambda name, path: None
        self.moves._aim_empty = lambda name, points: self.aims.update({name: points})
        self.moves._sample_points = self.samples
        self.moves._support = lambda frame, skip, cache=None: None      # no support in these scenes unless a test puts one in
        self.moves._floor_objects = lambda: []
        self.moves._atmosphere_objects = lambda: []                  # no fog or plume volume either

    def samples(self, ref, count):
        point = core._ref(self.geo, ref)
        return [core._add(point, (0, 0, f * 0.1)) if ref == 'tower' else point for f in range(count)]

    def solve(self, job, focus, refs, start, view, frame, lens, details):
        self.solve_frame, self.solve_start = frame - 1, start
        # A composition offset that the compiler must carry into the real rig.
        aim = core._add(core._ref(self.geo, 'tower'), (0.5, 0, -1))
        # Keep this fake solve physically admissible; pitch limiting is tested separately.
        x, y = start[0] + 2, start[1] - 3
        horizontal = math.hypot(aim[0] - x, aim[1] - y)
        position = (x, y, aim[2] - horizontal * math.tan(math.radians(15)))
        return {'position': position, 'aim': aim, 'achieved': {}}

    def compile(self, kind, params, **extra):
        shot = {'shot_id': 'focus_test', 'duration_frames': 61, 'screen': {'subject': ['tower']},
                'camera': {'move': {'type': kind, 'params': {'target': 'tower', **params},
                                   'focus': {'height_share': 0.6, 'centre': [0.5, 0.55]},
                                   'timing': {'profile': 'burst_settle'}, **extra}}}
        return self.moves.compile_move({'shot': shot, 'fps': 30, 'output_size': [1080, 1920]})

    def test_orbit_uses_solved_composition_and_tracks_animated_subject(self):
        for kind in ('turntable', 'orbit_reveal'):
            with self.subTest(kind=kind):
                rig, report = self.compile(kind, {'sweep_deg': 40})
                self.assertEqual(rig['aim_keys'], [{'frame': 0, 'blend': 1.0}])
                centers = self.samples('tower', 61)
                out = rig_core.bake(rig, 30, 61, subject=centers, target=self.aims[rig['look_target']],
                                    view=(36, 'AUTO', 1080, 1920), default_lens=24)
                hero = report['focus']['frame']
                self.assertEqual(hero, self.solve_frame)
                self.assertLess(math.dist(out['frames'][hero]['location'], report['focus']['position']), 1e-9)
                self.assertLess(math.dist(out['frames'][hero]['aim'], report['focus']['aim']), 1e-9)
                self.assertEqual(rig['guard_target'], 'tower')

    def test_crane_retains_vertical_travel_and_solves_at_actual_timed_frame(self):
        rig, report = self.compile('crane', {'from_h': 2, 'to_h': 40})
        points = report['waypoints']
        self.assertEqual(core._sub(points[-1], points[0]), (0, 0, 38))
        path = core.catmull_rom(points)
        out = rig_core.bake(rig, 30, 61, target=self.aims[rig['look_target']] * 61,
                            path=path, view=(36, 'AUTO', 1080, 1920), default_lens=24)
        self.assertLess(math.dist(out['frames'][report['focus']['frame']]['location'], report['focus']['position']), 0.001)

    def test_pull_starts_at_solved_pose_and_tilt_keeps_eye_fixed(self):
        _, pull = self.compile('pull_back', {'span': 0.3})
        self.assertEqual(pull['focus']['frame'], 0)
        self.assertLess(math.dist(pull['waypoints'][0], pull['focus']['position']), 0.001)
        rig, tilt = self.compile('tilt', {'aim_from': 'top', 'aim_to': 'bottom'})
        self.assertTrue(all(math.dist(p, tilt['waypoints'][0]) < 1e-9 for p in tilt['waypoints']))
        aims = self.aims[rig['look_target']]
        self.assertEqual(aims[0], self.geo['points']['top'])
        self.assertEqual(aims[-1], self.geo['points']['bottom'])
        self.assertEqual(core._sub(aims[-1], aims[0]), (0, 0, -32))
        self.assertLess(math.dist(aims[tilt['focus']['frame']], tilt['focus']['aim']), 1e-9)
        self.assertEqual(rig['guard_target'], 'tower')

    def test_tilt_refuses_an_ignored_look_target(self):
        with self.assertRaisesRegex(ValueError, 'omit.*look_target'):
            self.compile('tilt', {'aim_from': 'top', 'aim_to': 'bottom'}, look_target='top')

    def test_new_moves_refuse_a_union_target_that_cannot_be_guarded(self):
        for kind in ('tilt', 'pull_back'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'visibility guard'):
                self.compile(kind, {'target': ['top', 'bottom'], 'aim_from': 'top', 'aim_to': 'bottom'})

    def test_macro_seed_uses_geometry_at_end_pose_but_keeps_opening_waypoint(self):
        def translated(params, frame=1):
            delta = (0, 0, -(frame - 1) * 0.3)
            return {'points': {k: core._add(v, delta) for k, v in self.geo['points'].items()},
                    'boxes': {k: tuple(core._add(v, delta) for v in box) for k, box in self.geo['boxes'].items()}}
        self.moves.resolve = translated
        params = {'target': 'tower', 'detail': 'bottom', 'detail_size_m': 2, 'elevation_deg': 35}
        _, report = self.compile('macro_push', params)
        opening = core.plan(report['move'], translated(params, 1))['waypoints'][0]
        ending_seed = core.plan(report['move'], translated(params, 61))['waypoints'][-1]
        self.assertLess(math.dist(report['waypoints'][0], opening), 0.001)
        self.assertLess(math.dist(self.solve_start, ending_seed), 1e-9)

    def test_focused_macro_follows_translation_and_keeps_solved_composition(self):
        rig, report = self.compile('macro_push', {'detail': 'bottom', 'detail_size_m': 2})
        self.assertEqual(rig['type'], 'follow')
        self.assertNotIn('path', rig)
        self.assertNotIn('distance_m', rig['timing'])
        targets = self.aims[rig['look_target']]
        subjects = self.samples('tower', 61)
        out = rig_core.bake(rig, 30, 61, subject=subjects, target=targets,
                            view=(36, 'AUTO', 1080, 1920), default_lens=24)
        self.assertLess(math.dist(out['frames'][-1]['location'], report['focus']['position']), 1e-9)
        self.assertLess(math.dist(out['frames'][-1]['aim'], report['focus']['aim']), 1e-9)
        self.assertEqual(core._sub(targets[-1], targets[0]), core._sub(subjects[-1], subjects[0]))
        self.assertEqual(rig['guard_target'], 'tower')

    def test_followed_macro_consumes_explicit_travel_distance(self):
        rig, report = self.compile('macro_push', {'detail': 'bottom', 'detail_size_m': 2},
                                   timing={'profile': 'ease_in_out', 'distance_m': 5})
        self.assertAlmostEqual(report['tracking']['relative_push_m'], 5, places=4)

    def test_followed_macro_keeps_an_explicit_visibility_target(self):
        rig, _ = self.compile('macro_push', {'detail': 'bottom', 'detail_size_m': 2}, look_target='top')
        self.assertEqual(rig['look_target'], 'top')
        self.assertEqual(rig['guard_target'], 'top')

    def test_static_focused_macro_keeps_its_existing_path(self):
        self.moves._sample_points = lambda ref, count: [core._ref(self.geo, ref)] * count
        rig, report = self.compile('macro_push', {'detail': 'bottom', 'detail_size_m': 2})
        self.assertEqual(rig['type'], 'flythrough')
        self.assertIn('path', rig)
        self.assertNotIn('tracking', report)

    # A2: a camera never ends up under what the subject stands on (scene_roles 'floor': a deck, a table, the ground).
    # The poses themselves are refused by the intent solve (test_frame_intent); the joining of a shot's poses and the
    # support margin are shared by every frame move.

    def test_the_margin_scales_with_the_subject_not_with_the_clearance(self):
        # a plume or sea clearance (guards.min_clearance_m) is not a floor margin; the margin is a quarter of the framed
        # subject's height, between 0.05 and 0.5 m: a 0.2 m product on a bench may be seen from 0.1 m over the bench
        self.assertEqual(self.moves._support_margin(((0, 0, 0), (1, 1, .2))), .05)
        self.assertEqual(self.moves._support_margin(((0, 0, 0), (1, 1, 1))), .25)
        self.assertEqual(self.moves._support_margin(((0, 0, 0), (1, 1, 24))), .5)

    def test_the_path_between_the_pictures_stays_above_the_support(self):
        # a bollard row between the two poses of a lighthouse on a quay: its top (30 m) stands over the middle of the
        # straight path at 21 m - the joining refuses it, said between the pictures
        def floor(position, from_z=None):
            return 30.0 if abs(position[0]) < 10 else 20.0
        self.geo = {'points': {'lighthouse': (0, 0, 0)}, 'boxes': {'lighthouse': ((-2, -2, 0), (2, 2, 24))}}
        self.moves._support = lambda frame, skip, cache=None: floor
        self.moves._through_path = lambda *a, **k: None
        self.moves._keep_out = lambda *a, **k: None
        ctx = {'p': {'target': 'lighthouse', 'path': 'straight', 'follow': 0, 'pace': 'timing'}, 'target': 'lighthouse',
               'aspect': 9 / 16, 'refs': ['lighthouse'], 'keep_ids': [], 'keep_m': 0.0, 'cache': {}, 'mine': set(), 'guards': {}}
        keys = [{'frame': f, 'eye': (x, -40.0, 21.0), 'aim': (0.0, 0.0, 12.0), 'lens': 35.0, 'roll': 0.0, 'axis': None}
                for f, x in ((0, -20.0), (30, 20.0))]
        rows = [{'at': 0.0, 'frame': 0}, {'at': 1.0, 'frame': 30}]
        job = {'shot': {'shot_id': 'harbour', 'duration_frames': 31}, 'fps': 30, 'output_size': [1080, 1920]}
        still = [(0.0, 0.0, 12.0)] * 31
        with patch.dict(sys.modules, {'camera_frame': module('camera_frame', blocked_share=lambda *a, **k: 0.0)}), \
                self.assertRaises(ValueError) as caught:
            self.moves._join_frames(job, ctx, keys, rows, [], still, [(0.0, 0.0, 0.0)] * 31, {}, {'timing': {'profile': 'linear'}})
        self.assertTrue(str(caught.exception).startswith('FRAME_BELOW_SUPPORT'), str(caught.exception)[:200])
        self.assertIn('between the pictures', str(caught.exception))
