"""A frame target's lens ladder goes past a hidden focus and a thin part when no view solves (camera_moves._pose widen):
whether a moment solves does not depend on the lens its picture starts from. A lighthouse behind a harbour wall, not
the reel the rule came from (N+1): from the picture's 35 mm the wall hides the lamp; a wider lens stands closer, in
front of the wall."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

OPS = Path(__file__).resolve().parents[1] / 'studio/blender_ops'
sys.path.insert(0, str(OPS))
import camera_moves_core as core  # noqa: E402

BOX = ((-2.0, -2.0, 0.0), (2.0, 2.0, 30.0))


def module(name, **members):
    result = ModuleType(name)
    result.__dict__.update(members)
    return result


class WidenTest(unittest.TestCase):
    def setUp(self):
        scene = SimpleNamespace(frame_set=lambda f: None)
        self.blocked = lambda lens: 0.0
        self.px = lambda lens: 100
        self.calls = []
        frame = module('camera_frame', solve_focus=self.solve, blocked_share=self.blocked_share, shown_px=self.shown_px,
                       diagnose_part=self.diagnose, HIDDEN_SHARE=0.5)
        stubs = {
            'bpy': module('bpy', context=SimpleNamespace(scene=scene)),
            'numpy': module('numpy'),
            'mathutils': module('mathutils', Vector=None),
            'mathutils.bvhtree': module('mathutils.bvhtree', BVHTree=None),
            'scene_roles': module('scene_roles', counts=None),
            'scene_geometry': module('scene_geometry'),
            'scene_tools': module('scene_tools', anchor_for=None, object_by_id=None),
            'scene_index': module('scene_index', resolve_ref=lambda ref, index=None: None),
            'camera_frame': frame,
        }
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location('widen_under_test', OPS / 'camera_moves.py')
            self.moves = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.moves)
        self.modules = patch.dict(sys.modules, {'camera_frame': frame})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        m = self.moves
        m.resolve = lambda params, frame=1: {'boxes': {'lighthouse': BOX}, 'points': {}}
        m._framed_spans = lambda refs: []
        m._long_axis = lambda target: None
        m._axis = lambda target, name: None
        m._support = lambda frame, skip, cache=None: None
        m._keep_out = lambda *a, **k: None
        m._in_atmosphere = lambda *a, **k: None

    # the scene: a pose per lens and view; what the wall hides and how thick the lamp shows depend on the lens only
    def solve(self, job, focus, refs, start, view, frame, lens, details, roll_deg=0.0, passes=None, **_):
        self.calls.append(('solve', lens, tuple(view)))
        self.height = focus.get('height_share', 0.5)
        return {'position': (round(lens, 3), round(view[1], 3), round(view[0], 3)), 'aim': (0.0, 0.0, 15.0),
                'achieved': {}, 'engaged': [], 'pass': 'first'}

    def blocked_share(self, refs, own, position, aim, lens, aspect, frame, roll_deg=0.0):
        return self.blocked(lens)

    def shown_px(self, ident, position, view, lens, size, frame, roll_deg=0.0):
        return self.px(lens)

    def diagnose(self, ident, own, position, view, lens, size, frame, roll_deg=0.0, shown=(), exposable=None):
        return {'hidden_share': 0.0, 'on_screen': 1.0, 'own_shell': False, 'px_if_open': self.px(lens), 'screen_centre': [0.5, 0.5]}

    def frame(self, lens, must_see=()):
        fr = {'at': 1.0, 'height_share': 0.5, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 10.0, 'azimuth_deg': 0.0},
              'lens_mm': lens, 'must_see': list(must_see)}
        move = {'type': 'frame_to_frame', 'params': {'target': 'lighthouse', 'solve': 'exact'}, 'frames': [fr]}   # the exact search
        job = {'shot': {'shot_id': 'harbour', 'duration_frames': 48, 'camera': {'move': move}}, 'output_size': [1080, 1920]}
        ctx = {'p': move['params'], 'target': 'lighthouse', 'aspect': 9 / 16, 'refs': ['lighthouse'], 'framed': 'lighthouse',
               'keep_ids': [], 'keep_m': 0.0, 'cache': {}, 'mine': set(), 'min_px': {'lighthouse/lamp': 32}}
        return job, move, fr, ctx

    def test_a_wall_in_front_at_the_pictures_lens_solves_wider_whatever_the_start(self):
        self.blocked = lambda lens: 0.6 if lens > 24 else 0.0
        _key, row, _w = self.moves.solve_frame(*self.frame(35.0))
        self.assertEqual(row['lens_mm'], 24.0)
        self.assertEqual(row['offset_from_picture'], {'azimuth_deg': 0.0, 'elevation_deg': 0.0})   # the picture's own view
        self.assertIn('widen', row['engaged'])
        self.assertEqual([(r['lens'], r['code']) for r in row['lens_rejected']], [(35.0, 'FOCUS_BLOCKED')])
        _key, started, _w = self.moves.solve_frame(*self.frame(24.0))
        self.assertEqual((started['lens_mm'], started['position'], started['engaged']), (24.0, row['position'], []))

    def test_a_part_too_thin_at_the_pictures_lens_shows_wider(self):
        self.px = lambda lens: 20 if lens > 24 else 40
        _key, row, _w = self.moves.solve_frame(*self.frame(35.0, ['lighthouse/lamp']))
        self.assertEqual((row['lens_mm'], row['offset_from_picture']), (24.0, {'azimuth_deg': 0.0, 'elevation_deg': 0.0}))
        self.assertIn('widen', row['engaged'])

    def test_wider_and_closer_together_when_neither_alone_is_enough(self):
        # the lamp reads only at 24 mm framed bigger than the picture: the wide sweep asks again why it is thin
        self.px = lambda lens: 40 if lens <= 24 and self.height > 0.5 else 20
        _key, row, _w = self.moves.solve_frame(*self.frame(35.0, ['lighthouse/lamp']))
        self.assertEqual(row['lens_mm'], 24.0)
        self.assertIn('widen', row['engaged'])

    def test_a_moment_that_solves_at_its_lens_is_never_widened(self):
        self.blocked = lambda lens: 0.6 if lens > 24 else 0.0
        _key, row, _w = self.moves.solve_frame(*self.frame(18.0))
        self.assertEqual((row['lens_mm'], row['engaged']), (18.0, []))
        self.assertFalse([c for c in self.calls if c[1] != 18.0])   # one sweep, one lens

    def test_no_lens_thick_enough_diagnoses_the_first_thin_pose_and_lists_every_lens(self):
        self.px = lambda lens: 20
        job, move, fr, ctx = self.frame(35.0, ['lighthouse/lamp'])
        solved, why = self.moves._pose(job, fr, (10.0, 0.0), ['lighthouse'], ['lighthouse'], BOX, 9 / 16, 47, 35.0, [], 0.0,
                                       ['lighthouse/lamp'], ctx['min_px'], diagnose=True, widen=True)
        self.assertIsNone(solved)
        self.assertEqual((why['kind'], why['lens_mm']), ('small', 35.0))
        self.assertEqual([r['lens'] for r in why['lens_rejected']], [35.0, *core.FLOOR_LENSES])
        with self.assertRaises(ValueError) as failed:
            self.moves.solve_frame(job, move, fr, ctx)
        self.assertEqual(failed.exception.report['lenses_tried'], sorted({35.0, *core.FLOOR_LENSES}))
        self.assertEqual(failed.exception.report['lenses_at_picture'], sorted({35.0, *core.FLOOR_LENSES}))

    def test_the_lenses_tried_at_the_pictures_view_are_its_own(self):
        # the wall hides the lamp from the picture's view at 35 mm (its sweep stops there); the next view cannot be
        # reached at 35 and solves at 24: 24 was never tried from the picture's view, so it stays a lens cheat
        picture = (10.0, 0.0)
        self.blocked = lambda lens: 0.6
        real = self.solve

        def solve(job, focus, refs, start, view, frame, lens, details, roll_deg=0.0, passes=None, **k):
            if tuple(view) != picture and lens > 24:
                raise ValueError('FRAME_UNREACHABLE: stub')
            if tuple(view) != picture:
                self.blocked = lambda lens: 0.0
            else:
                self.blocked = lambda lens: 0.6
            return real(job, focus, refs, start, view, frame, lens, details, roll_deg=roll_deg, passes=passes, **k)
        sys.modules['camera_frame'].solve_focus = solve
        for azimuth in (0.0, 12.34567):   # a view the views round (a lying subject's azimuth from its lean) is still its own
            with self.subTest(azimuth=azimuth):
                picture = (10.0, round(azimuth, 3))
                job, move, fr, ctx = self.frame(35.0)
                fr['view']['azimuth_deg'] = azimuth
                _key, row, _w = self.moves.solve_frame(job, move, fr, ctx)
                self.assertTrue(any(row['offset_from_picture'].values()))
                self.assertEqual((row['lens_mm'], row['lenses_at_picture']), (24.0, [35.0]))

if __name__ == '__main__':
    unittest.main()
