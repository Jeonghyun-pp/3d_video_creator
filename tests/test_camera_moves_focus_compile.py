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
                    'boxes': {'rocket': ((-1.85, -1.85, 0), (1.85, 1.85, 41))}}
        self.moves.resolve = lambda params, frame=1: self.geo
        self.moves._polyline = lambda name, path: None
        self.moves._aim_empty = lambda name, points: self.aims.update({name: points})
        self.moves._sample_points = self.samples
        self.moves._support = lambda frame, skip, cache=None: None      # no support in these scenes unless a test puts one in
        self.moves._floor_objects = lambda: []

    def samples(self, ref, count):
        point = core._ref(self.geo, ref)
        return [core._add(point, (0, 0, f * 0.1)) if ref == 'rocket' else point for f in range(count)]

    def solve(self, job, focus, refs, start, view, frame, lens, details):
        self.solve_frame, self.solve_start = frame - 1, start
        # A composition offset that the compiler must carry into the real rig.
        aim = core._add(core._ref(self.geo, 'rocket'), (0.5, 0, -1))
        # Keep this fake solve physically admissible; pitch limiting is tested separately.
        x, y = start[0] + 2, start[1] - 3
        horizontal = math.hypot(aim[0] - x, aim[1] - y)
        position = (x, y, aim[2] - horizontal * math.tan(math.radians(15)))
        return {'position': position, 'aim': aim, 'achieved': {}}

    def compile(self, kind, params, **extra):
        shot = {'shot_id': 'focus_test', 'duration_frames': 61, 'screen': {'subject': ['rocket']},
                'camera': {'move': {'type': kind, 'params': {'target': 'rocket', **params},
                                   'focus': {'height_share': 0.6, 'centre': [0.5, 0.55]},
                                   'timing': {'profile': 'burst_settle'}, **extra}}}
        return self.moves.compile_move({'shot': shot, 'fps': 30, 'output_size': [1080, 1920]})

    def test_frame_pictures_solve_and_check_visibility_with_the_baked_roll(self):
        from studio.blender_ops import camera_frame_core as framing
        from tests.test_camera_frame_core import box_points
        points = box_points((-1.4, -.7, 20), (1.4, .7, 21))
        calls = []
        def solve(job, focus, refs, start, view, frame, lens, details, roll_deg=0):
            calls.append(('solve', roll_deg))
            return framing.solve(lambda p: points, start, view, focus, lens, 9/16, roll_deg=roll_deg)
        def blocked(*args, **kwargs):
            calls.append(('blocked', kwargs.get('roll_deg', 0)))
            return 0
        def shown(*args, **kwargs):
            calls.append(('shown', kwargs.get('roll_deg', 0)))
            return 30
        class Axis(tuple):
            def normalized(self): return self
        class Matrix:
            def to_3x3(self): return self
            def __matmul__(self, axis): return Axis(axis)
        root = SimpleNamespace(name='rocket', matrix_world=Matrix())
        self.moves.resolve_ref = lambda ref, index=None: {'roots': [root], 'objects': [root]}
        self.moves.Vector = tuple
        move = {'type': 'frame_to_frame', 'params': {'target': 'rocket', 'follow': 0, 'pace': 'timing'},
                'frames': [{'at': at, 'height_share': .16, 'centre': [.665, .78],
                            'view': {'elevation_deg': 12, 'azimuth_deg': -20}, 'lens_mm': 35,
                            'tilt_deg': -18, 'must_see': ['rocket']} for at in (0, 1)]}
        shot = {'shot_id': 'rolled', 'duration_frames': 3, 'camera': {'move': move},
                'screen': {'details': [{'id': 'rocket', 'min_px': 24}]}}
        with patch.dict(sys.modules, {'camera_frame': module('camera_frame', solve_focus=solve, blocked_share=blocked, shown_px=shown)}):
            rig, report = self.moves.compile_move({'shot': shot, 'fps': 30, 'output_size': [1080, 1920]})
        self.assertTrue(calls)
        self.assertEqual({kind for kind, _ in calls}, {'solve', 'blocked', 'shown'})
        for kind, roll in calls:
            self.assertAlmostEqual(roll, -18, places=4, msg=kind)
        self.assertTrue(all(abs(k['roll_deg'] + 18) < .001 for k in rig['roll_keys']))
        for row in report['frames']:
            self.assertAlmostEqual(row['achieved']['centre'][0], .665, delta=framing.PLACE_TOL)
            self.assertAlmostEqual(row['achieved']['centre'][1], .78, delta=framing.PLACE_TOL)

    def test_orbit_uses_solved_composition_and_tracks_animated_subject(self):
        for kind in ('turntable', 'orbit_reveal'):
            with self.subTest(kind=kind):
                rig, report = self.compile(kind, {'sweep_deg': 40})
                self.assertEqual(rig['aim_keys'], [{'frame': 0, 'blend': 1.0}])
                centers = self.samples('rocket', 61)
                out = rig_core.bake(rig, 30, 61, subject=centers, target=self.aims[rig['look_target']],
                                    view=(36, 'AUTO', 1080, 1920), default_lens=24)
                hero = report['focus']['frame']
                self.assertEqual(hero, self.solve_frame)
                self.assertLess(math.dist(out['frames'][hero]['location'], report['focus']['position']), 1e-9)
                self.assertLess(math.dist(out['frames'][hero]['aim'], report['focus']['aim']), 1e-9)
                self.assertEqual(rig['guard_target'], 'rocket')

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
        self.assertEqual(rig['guard_target'], 'rocket')

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
        params = {'target': 'rocket', 'detail': 'bottom', 'detail_size_m': 2, 'elevation_deg': 35}
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
        subjects = self.samples('rocket', 61)
        out = rig_core.bake(rig, 30, 61, subject=subjects, target=targets,
                            view=(36, 'AUTO', 1080, 1920), default_lens=24)
        self.assertLess(math.dist(out['frames'][-1]['location'], report['focus']['position']), 1e-9)
        self.assertLess(math.dist(out['frames'][-1]['aim'], report['focus']['aim']), 1e-9)
        self.assertEqual(core._sub(targets[-1], targets[0]), core._sub(subjects[-1], subjects[0]))
        self.assertEqual(rig['guard_target'], 'rocket')

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

    # camera_moves._pose and the frame_to_frame view search keep why each lens and view failed (2026-10-14: s21/s23
    # lost the 24 mm reason behind the 14 mm one; a must-see part was only diagnosed when the picture's own view
    # reached the must-see check, so an offset view's hidden part fell through to FRAME_UNSOLVED).

    def frames_shot(self, must_see=()):
        """A lighthouse on a pier (not the rocket): its lamp inside the shell is what a moment must show."""
        self.geo = {'points': {'lighthouse': (0, 0, 0)}, 'boxes': {'lighthouse': ((-2, -2, 0), (2, 2, 24))}}
        frames = [{'at': at, 'height_share': .4, 'centre': [.5, .5], 'view': {'elevation_deg': 10, 'azimuth_deg': 0},
                   'lens_mm': 35, **({'must_see': list(must_see)} if must_see else {})} for at in (0, 1)]
        move = {'type': 'frame_to_frame', 'params': {'target': 'lighthouse', 'follow': 0, 'pace': 'timing'}, 'frames': frames}
        return {'shot': {'shot_id': 'harbour', 'duration_frames': 3, 'camera': {'move': move},
                         'screen': {'details': [{'id': i, 'min_px': 20} for i in must_see]}}, 'fps': 30, 'output_size': [1080, 1920]}

    def fake(self, solve, diagnose=None, shown=30):
        return module('camera_frame', solve_focus=solve, blocked_share=lambda *a, **k: 0.0, shown_px=lambda *a, **k: shown,
                      diagnose_part=diagnose or (lambda *a, **k: self.fail('no diagnosis expected')), HIDDEN_SHARE=0.5)

    def test_every_lens_reason_is_kept(self):
        def solve(job, focus, refs, start, view, frame, lens, details, roll_deg=0):
            if lens == 35:
                raise ValueError('FRAME_UNSOLVED: the focus runs off the frame at 35')
            return {'position': (0.0, -40.0 - lens, 20.0), 'aim': (0.0, 0.0, 20.0), 'achieved': {}}
        self.moves._keep_out = lambda ids, frame, position, m, *a: 'inside or below [\'pier\'] by 1.00 m' if position[1] == -64.0 else None
        job = self.frames_shot()
        job['shot']['camera']['move']['guards'] = {'clearance_ids': ['pier'], 'min_clearance_m': 0.5}
        with patch.dict(sys.modules, {'camera_frame': self.fake(solve)}):
            _rig, report = self.moves.compile_move(job)
        row = report['frames'][0]
        self.assertEqual(row['lens_mm'], 18.0)
        self.assertEqual([(r['lens'], r['code']) for r in row['lens_rejected']], [(35, 'FRAME_UNSOLVED'), (24.0, 'KEEP_OUT')])
        self.assertIn('runs off the frame', row['lens_rejected'][0]['why'])
        # no lens works anywhere: the final message names every lens of the picture's view
        def never(job, focus, refs, start, view, frame, lens, details, roll_deg=0):
            raise ValueError(f'FRAME_UNSOLVED: nothing at {lens} mm')
        with patch.dict(sys.modules, {'camera_frame': self.fake(never)}), self.assertRaises(ValueError) as caught:
            self.moves.compile_move(self.frames_shot())
        for lens in (35, 24.0, 18.0, 14.0):
            self.assertIn(f'nothing at {lens} mm', str(caught.exception))

    def test_diagnosis_runs_on_the_first_candidate_that_reaches_must_see(self):
        calls = []
        def solve(job, focus, refs, start, view, frame, lens, details, roll_deg=0):
            if tuple(view) == (10, 0):   # the picture's own view never solves: the next view is the first to judge the part
                raise ValueError('FRAME_UNSOLVED: no pose at the picture view')
            return {'position': (0.0, -40.0, 20.0), 'aim': (0.0, 0.0, 20.0), 'achieved': {}}
        def diagnose(ref, own, position, view, lens, size, frame, roll_deg=0, shown=()):
            calls.append((ref, tuple(view)))
            return {'on_screen': 1.0, 'occluders': {'lighthouse/shell': 1.0}, 'own_shell': True, 'hidden_share': 1.0,
                    'px': 0.0, 'px_if_open': 30.0, 'screen_centre': [.5, .5]}
        with patch.dict(sys.modules, {'camera_frame': self.fake(solve, diagnose, shown=0)}), self.assertRaises(ValueError) as caught:
            self.moves.compile_move(self.frames_shot(must_see=['lighthouse/lamp']))
        self.assertTrue(calls)
        self.assertNotEqual(calls[0][1], (10, 0))
        self.assertIn('SHOW_NEEDS_OPENING', str(caught.exception))

    # A2: a camera never ends up under what the subject stands on (scene_roles 'floor': a deck, a table, the ground).
    # 2026-10-14: the 24 mm step of s21/s23 went under the rocket's deck and nothing refused it. A pose under the
    # support is refused with FRAME_BELOW_SUPPORT and its reason kept - never lifted onto the support.

    def under_a_quay(self, top=20.0):
        """The lighthouse's quay top at `top` everywhere: floor(position, from_z) -> top."""
        calls = []
        def floor(position, from_z=None):
            calls.append((tuple(position), from_z))
            return top
        self.moves._support = lambda frame, skip, cache=None: floor
        return calls

    def test_a_pose_under_the_support_is_refused_not_lifted(self):
        calls = self.under_a_quay()
        def solve(job, focus, refs, start, view, frame, lens, details, roll_deg=0):
            return {'position': (0.0, -40.0, 20.2 if lens >= 24 else 21.0), 'aim': (0.0, 0.0, 12.0), 'achieved': {}}
        with patch.dict(sys.modules, {'camera_frame': self.fake(solve)}):
            _rig, report = self.moves.compile_move(self.frames_shot())
        row = report['frames'][0]
        self.assertEqual(row['lens_mm'], 18.0)
        self.assertEqual(row['position'], (0.0, -40.0, 21.0))   # the solved pose as it was: nothing moved it up
        self.assertEqual([(r['lens'], r['code']) for r in row['lens_rejected']], [(35, 'FRAME_BELOW_SUPPORT'), (24.0, 'FRAME_BELOW_SUPPORT')])
        self.assertIn('support', row['lens_rejected'][0]['why'])
        # the column is searched from the subject's middle (z 12) or the camera, whichever is higher: a support above
        # the subject (a street over a station hall) is a ceiling, not its floor
        self.assertTrue(calls and all(from_z == max(p[2], 12.0) for p, from_z in calls if from_z is not None))

    def test_every_view_under_the_support_says_so(self):
        self.under_a_quay()
        def solve(job, focus, refs, start, view, frame, lens, details, roll_deg=0):
            return {'position': (0.0, -40.0, 20.2), 'aim': (0.0, 0.0, 12.0), 'achieved': {}}
        with patch.dict(sys.modules, {'camera_frame': self.fake(solve)}), self.assertRaises(ValueError) as caught:
            self.moves.compile_move(self.frames_shot())
        self.assertTrue(str(caught.exception).startswith('FRAME_BELOW_SUPPORT'), str(caught.exception)[:200])

    def test_the_margin_scales_with_the_subject_not_with_the_clearance(self):
        # a plume or sea clearance (guards.min_clearance_m) is not a floor margin; the margin is a quarter of the framed
        # subject's height, between 0.05 and 0.5 m: a 0.2 m product on a bench may be seen from 0.1 m over the bench
        self.under_a_quay(top=0.0)
        def solve(job, focus, refs, start, view, frame, lens, details, roll_deg=0):
            return {'position': (0.0, -0.4, 0.1), 'aim': (0.0, 0.0, 0.1), 'achieved': {}}
        job = self.frames_shot()
        self.geo = {'points': {'lighthouse': (0, 0, 0)}, 'boxes': {'lighthouse': ((-.1, -.1, 0), (.1, .1, .2))}}
        job['shot']['camera']['move']['guards'] = {'min_clearance_m': 2.0}
        with patch.dict(sys.modules, {'camera_frame': self.fake(solve)}):
            _rig, report = self.moves.compile_move(job)
        self.assertEqual(report['frames'][0]['lens_mm'], 35)
        self.assertEqual(self.moves._support_margin(((0, 0, 0), (1, 1, .2))), .05)
        self.assertEqual(self.moves._support_margin(((0, 0, 0), (1, 1, 1))), .25)
        self.assertEqual(self.moves._support_margin(((0, 0, 0), (1, 1, 24))), .5)

    def test_the_path_between_the_pictures_stays_above_the_support(self):
        # a bollard row between the two poses: its top (30 m) stands over the middle of the straight path at 21 m
        def floor(position, from_z=None):
            return 30.0 if abs(position[0]) < 10 else 20.0
        self.moves._support = lambda frame, skip, cache=None: floor
        def solve(job, focus, refs, start, view, frame, lens, details, roll_deg=0):
            return {'position': (-20.0 if frame == 1 else 20.0, -40.0, 21.0), 'aim': (0.0, 0.0, 12.0), 'achieved': {}}
        job = self.frames_shot()
        job['shot']['duration_frames'] = 31
        job['shot']['camera']['move']['params']['path'] = 'straight'
        with patch.dict(sys.modules, {'camera_frame': self.fake(solve)}), self.assertRaises(ValueError) as caught:
            self.moves.compile_move(job)
        self.assertTrue(str(caught.exception).startswith('FRAME_BELOW_SUPPORT'), str(caught.exception)[:200])
        self.assertIn('between the pictures', str(caught.exception))
