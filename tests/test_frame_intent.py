"""A frame target kept by its picture's intent (move.params.solve 'intent'): size class, side of level and lens class
from the frames[] row alone (camera_moves_core.intent_of), and views placed, probed and judged rather than solved
(camera_moves._solve_intent). A lighthouse on a harbour wall, not the reel the rule came from (N+1)."""
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

OPS = Path(__file__).resolve().parents[1] / 'studio/blender_ops'
sys.path.insert(0, str(OPS))
import camera_moves_core as core  # noqa: E402

from studio import frame_targets, storyboard_brief  # noqa: E402

BOX = ((-2.0, -2.0, 0.0), (2.0, 2.0, 30.0))


def row(height=0.5, elevation=10.0, lens=35.0, **extra):
    return {'at': 1.0, 'height_share': height, 'centre': [0.5, 0.5], 'view': {'elevation_deg': elevation, 'azimuth_deg': 0.0},
            'lens_mm': lens, **extra}


class IntentOfTest(unittest.TestCase):
    def test_size_class_and_its_band(self):
        self.assertEqual(core.size_class(0.1), ('원경', [0.0, 0.15]))
        self.assertEqual(core.size_class(0.5), ('꽉 참', [0.45, 0.9]))
        self.assertEqual(core.size_class(1.4), ('클로즈업', [0.9, 2.0]))
        self.assertEqual(core.size_class(9.0), ('익스트림 클로즈업', [2.0, core.SIZE_TOP]))   # the open top is capped
        self.assertEqual(storyboard_brief.size_class(1.4), '클로즈업')                         # one table, the brief's words

    def test_the_band_is_the_pictures_elevation_on_its_side_of_level(self):
        tol = core.INTENT_ELEVATION_TOL
        self.assertEqual(core.elevation_band(3.0), ('level', [3.0 - tol, 3.0 + tol]))
        self.assertEqual(core.elevation_band(30.0), ('down', [30.0 - tol, 30.0 + tol]))
        self.assertEqual(core.elevation_band(-65.0), ('up', [-65.0 - tol, -65.0 + tol]))
        self.assertEqual(core.elevation_band(80.0), ('down', [80.0 - tol, core.STEEP_DEG]))
        self.assertEqual(core.elevation_band(8.0), ('down', [-core.LEVEL_DEG, 8.0 + tol]))   # never into looking up
        # either side of the level class's edge the band barely moves (2026-10-15: -5.58 kept up to -5, -4.9 up to +12)
        up, level = core.elevation_band(-5.6), core.elevation_band(-4.9)
        self.assertEqual((up[0], level[0]), ('up', 'level'))
        self.assertEqual(up[1], [-5.6 - tol, core.LEVEL_DEG])
        self.assertLess(abs(up[1][0] - level[1][0]), 1.0)
        self.assertLessEqual(level[1][1] - up[1][1], tol - core.LEVEL_DEG + 1.0)

    def test_lens_class_and_the_next_wider(self):
        self.assertEqual([core.lens_class(mm) for mm in (14, 24, 30, 45, 85)], ['wide', 'wide', 'normal', 'normal', 'tele'])
        self.assertEqual([core.wider_lens(mm) for mm in (70, 50, 35, 24, 14)], [35.0, 35.0, 24.0, 18.0, None])
        self.assertEqual(frame_targets.LENS_MM, core.LENS_CLASSES)

    def test_an_older_row_gives_the_same_intent(self):
        new = row(height=1.4, elevation=-20.0, cut_edges=['bottom'], must_see=['lighthouse/lamp'], tilt_deg=8.0)
        old = {k: v for k, v in new.items() if k not in ('cut_edges',)}
        a, b = frame_targets.intent_of(new), frame_targets.intent_of(old)
        self.assertEqual({k: a[k] for k in ('size', 'elevation', 'lens', 'must_see', 'tilt_deg')},
                         {k: b[k] for k in ('size', 'elevation', 'lens', 'must_see', 'tilt_deg')})
        self.assertEqual((a['size']['class'], a['elevation']['class'], a['lens']['class']), ('클로즈업', 'up', 'normal'))
        self.assertEqual(frame_targets.intent_of({**old, 'lens_mm': None}, 18.0)['lens'], {'class': 'wide', 'mm': 18.0})

    def test_views_stay_in_the_band(self):
        for elevation in (2.0, 30.0, -5.6, -65.0):
            band = core.elevation_band(elevation)[1]
            views = core.intent_views(core.intent_of(row(elevation=elevation)), (elevation, 0.0))
            self.assertTrue(views and all(band[0] <= v['view'][0] <= band[1] for v in views), (elevation, band))
            self.assertEqual(views[0]['offset'], {'azimuth_deg': 0.0, 'elevation_deg': 0.0})   # the picture's own view first

    def test_departures_are_said(self):
        intent = core.intent_of(row(height=0.5, lens=35.0))
        seen = lambda h, whole=False: {'height_share': h, 'whole_in_frame': whole}  # noqa: E731
        self.assertEqual(core.intent_departures(intent, {'azimuth_deg': 30.0, 'elevation_deg': 0.0}, 35.0, seen(0.6)), [])
        said = core.intent_departures(intent, {'azimuth_deg': 330.0, 'elevation_deg': 0.0}, 24.0, seen(1.2))
        self.assertFalse(any('turned' in s for s in said))                       # 330 is 30 the short way round
        self.assertTrue(any('wide lens' in s for s in said) and any('클로즈업' in s for s in said), said)
        far = core.intent_departures(intent, {'azimuth_deg': -150.0, 'elevation_deg': 0.0}, 35.0, None)
        self.assertEqual(len(far), 1)
        self.assertIn('turned 150', far[0])
        # a cut picture: its extrapolated height is not compared, a pose showing the subject whole is said
        cut = core.intent_of(row(height=3.0, cut_edges=['bottom']))
        self.assertEqual(core.intent_departures(cut, {'azimuth_deg': 0.0, 'elevation_deg': 0.0}, 35.0, seen(0.7)), [])
        self.assertIn('whole subject', core.intent_departures(cut, {'azimuth_deg': 0.0, 'elevation_deg': 0.0}, 35.0, seen(0.7, True))[0])

    def test_placed_pose_puts_the_box_centre_where_the_picture_has_it(self):
        import camera_frame_core as framing
        fr = row(height=0.4, elevation=0.0, lens=35.0)
        fr['centre'] = [0.3, 0.7]
        eye, aim = core.framed_pose(BOX, fr, (0.0, 0.0), 35.0, 9 / 16)
        screen, _ = framing.project([core.centre(BOX)], eye, framing.basis(0.0, 0.0), framing.tangents(35.0, 9 / 16))
        self.assertAlmostEqual(screen[0][0], 0.3, places=6)
        self.assertAlmostEqual(screen[0][1], 0.7, places=6)

    def test_score_prefers_the_picture_then_the_lens_then_the_margin(self):
        straight = core.intent_score({'azimuth_deg': 0.0, 'elevation_deg': 0.0}, False, 1.0)
        turned = core.intent_score({'azimuth_deg': 20.0, 'elevation_deg': 0.0}, False, 1.0)
        wider = core.intent_score({'azimuth_deg': 0.0, 'elevation_deg': 0.0}, True, 1.0)
        roomy = core.intent_score({'azimuth_deg': 0.0, 'elevation_deg': 0.0}, False, 3.0)
        self.assertLess(straight, wider)
        self.assertLess(wider, turned)
        self.assertLess(roomy, straight)

    def test_a_move_naming_a_solve_is_refused_as_unread(self):
        # the exact search was removed (2026-10-17): a frame_to_frame move has one solve, and a params.solve left over
        # from before (exact or intent) is a value nothing reads - refused by shot validation, never silently ignored
        self.assertNotIn('solve', core.PARAMS['frame_to_frame'])
        for solve in ('exact', 'intent'):
            self.assertEqual(core.unknown_params({'type': 'frame_to_frame', 'params': {'target': 'lighthouse', 'solve': solve}}), ['solve'])


def module(name, **members):
    result = ModuleType(name)
    result.__dict__.update(members)
    return result


class SolveIntentTest(unittest.TestCase):
    """camera_moves._solve_intent on stubs: the probe says what each placed view would see."""

    def setUp(self):
        scene = SimpleNamespace(frame_set=lambda f: None)
        self.wall = lambda view, lens: 0.0          # blocked share of the focus from a placed view
        self.lamp = lambda view, lens: 60.0         # the lamp's px from it (probe) ...
        self.shown = None                           # ... and as the full measure finds it (None: the same)
        self.full_blocked = lambda n: 0.0           # the full blocked share of the n-th pose judged
        self.seen_h = 0.5                           # the height a judged pose shows the tower at
        self.calls = {'probe': 0, 'solve': [], 'blocked': 0, 'views': []}
        self.cuts_at = lambda position: []          # the frame edges the subject runs past from a pose (projection)
        self.box_at = lambda position: None         # the subject's box in frame from a pose (probe seen_box)
        self.refine_shift = 0.0                     # how far (x) the size refine moves the camera
        frame = module('camera_frame', solve_focus=self.solve, blocked_share=self.blocked_share, shown_px=self.shown_px,
                       diagnose_part=self.diagnose, probe=self.probe, HIDDEN_SHARE=0.5,
                       probe_set=lambda refs, own, parts, frame, share=1.0: {'stub': True, 'focus': []}, PROBE_SCORE=0.5,
                       cut_sides=lambda points, position, view, lens, aspect, roll_deg=0.0: self.cuts_at(position),
                       seen_height=lambda prepared, position, view, lens, aspect, roll_deg=0.0: {'height_share': self.seen_h,
                                                                                                  'centre': [0.5, 0.5],
                                                                                                  'whole_in_frame': self.seen_h < 0.9})
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
            'gate_policy': module('gate_policy', is_error=lambda code: True),
        }
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location('intent_under_test', OPS / 'camera_moves.py')
            self.moves = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.moves)
        self.modules = patch.dict(sys.modules, {'camera_frame': frame, 'gate_policy': stubs['gate_policy']})
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

    def probe(self, refs, own, parts, position, view, lens_mm, output_size, frame, roll_deg=0.0, prepared=None, **_):
        self.calls['probe'] += 1
        self.calls['views'].append(tuple(view))
        self.calls.setdefault('positions', []).append(tuple(position))
        self.calls.setdefault('rolls', []).append(roll_deg)
        blocked = self.wall(tuple(view), lens_mm)
        return {'blocked_share': blocked, 'must_see_px': {p: self.lamp(tuple(view), lens_mm) for p in parts},
                'blockers': {'harbour_wall': blocked} if blocked else {}, 'part_blockers': {p: {} for p in parts},
                'in_frame': {p: 1.0 for p in parts}, 'seen_box': self.box_at(position), 'cut_sides': self.cuts_at(position)}

    def solve(self, job, focus, refs, start, view, frame, lens, details=(), roll_deg=0.0, passes=None, tol=None, samples=None, **_):
        self.calls['solve'].append({'view': tuple(view), 'lens': lens, 'tol': tol, 'samples': samples})
        return {'position': [start[0] + self.refine_shift, *start[1:]], 'aim': [0.0, 0.0, 15.0], 'achieved': {'height_share': focus['height_share']},
                'mode': 'plain', 'steps': 2}

    def blocked_share(self, refs, own, position, aim, lens, aspect, frame, roll_deg=0.0):
        self.calls['blocked'] += 1
        return self.full_blocked(self.calls['blocked'])

    def shown_px(self, ident, position, view, lens, size, frame, roll_deg=0.0):
        return (self.shown or self.lamp)(tuple(view), lens)

    def diagnose(self, ident, own, position, view, lens, size, frame, roll_deg=0.0, shown=(), exposable=None):
        return {'hidden_share': 1.0, 'on_screen': 1.0, 'own_shell': True, 'px_if_open': 60.0, 'screen_centre': [0.5, 0.5],
                'occluder_parts': {'lighthouse/glass_housing': 1.0}}

    def frame(self, elevation=10.0, lens=35.0, must_see=('lighthouse/lamp',), height=0.5, **extra):
        fr = {'at': 1.0, 'height_share': height, 'centre': [0.5, 0.5], 'view': {'elevation_deg': elevation, 'azimuth_deg': 0.0},
              'lens_mm': lens, 'must_see': list(must_see), **extra}
        move = {'type': 'frame_to_frame', 'params': {'target': 'lighthouse'}, 'frames': [fr]}
        job = {'shot': {'shot_id': 'harbour', 'duration_frames': 48, 'camera': {'move': move}}, 'output_size': [1080, 1920]}
        ctx = {'p': move['params'], 'target': 'lighthouse', 'aspect': 9 / 16, 'refs': ['lighthouse'], 'framed': 'lighthouse',
               'keep_ids': [], 'keep_m': 0.0, 'cache': {}, 'mine': set(), 'min_px': {'lighthouse/lamp': 32}}
        return job, move, fr, ctx

    def test_a_pick_with_its_pose_is_probed_where_it_stood_not_placed_again(self):
        # the moment's size may read differently now (a picture painted over that pose): the pick is its eye and aim,
        # kept there through the full judge (no size refine moves it) with its own roll
        self.refine_shift = 5.0
        eye, aim = (0.0, -40.0, 22.053), (0.0, 0.0, 15.0)   # looks along elevation 10, azimuth 0
        job, move, fr, ctx = self.frame(chosen={'view': [10.0, 0.0], 'lens_mm': 35.0, 'eye': list(eye), 'aim': list(aim),
                                                 'roll_deg': 3.0, 'by': 'agent'})
        _key, row_, _w = self.moves.solve_frame(job, move, fr, ctx)
        self.assertEqual(self.calls['positions'][0], eye)
        self.assertEqual(self.calls['rolls'][0], 3.0)
        self.assertEqual([round(v, 3) for v in row_['position']], list(eye))   # not re-solved to the size band
        self.assertIn('seeded', row_['engaged'])

    def test_a_stale_recorded_pose_is_placed_again_from_its_view_and_said(self):
        eye = (25.0, -30.0, 7.9)   # looks from well off the recorded view
        job, move, fr, ctx = self.frame(chosen={'view': [10.0, 0.0], 'lens_mm': 35.0, 'eye': list(eye), 'aim': [0.0, 0.0, 15.0], 'by': 'agent'})
        _key, row_, _w = self.moves.solve_frame(job, move, fr, ctx)
        self.assertNotEqual(self.calls['positions'][0], eye)
        self.assertEqual(row_['view'], [10.0, 0.0])
        self.assertTrue(any('stale' in d for d in row_['departed']), row_['departed'])

    def test_a_part_at_its_minimum_is_thin_within_one_pixel_of_the_builds_check(self):
        # the build reads the part on an id pass 256 px tall: the camera keeps half of its pixels above the minimum
        self.lamp = lambda view, lens: 34.0      # over 32 but within 0.5 * 1920 / 256 = 3.75 px of it, from every view
        with self.assertRaises(ValueError):
            self.moves.solve_frame(*self.frame())
        self.lamp = lambda view, lens: 40.0
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertGreaterEqual(row_['must_see_px']['lighthouse/lamp'], 32 + 0.5 * 1920 / 256)

    def test_an_agents_pin_in_a_joined_shot_keeps_other_poses_to_fall_back_on(self):
        job, move, fr, ctx = self.frame(chosen={'view': [10.0, 0.0], 'lens_mm': 35.0, 'eye': [0.0, -40.0, 22.053], 'aim': [0.0, 0.0, 15.0],
                                                 'by': 'agent'})
        _key, row_, _w = self.moves.solve_frame(job, move, fr, {**ctx, 'keep': 4})
        self.assertTrue(row_.get('alternatives'))   # the sweep ran beside the pin ...
        self.assertEqual((row_['view'], row_['lens_mm']), ([10.0, 0.0], 35.0))   # ... and the pin is still the pose
        job, move, fr, ctx = self.frame(chosen={'view': [10.0, 0.0], 'lens_mm': 35.0, 'eye': [0.0, -40.0, 22.053], 'aim': [0.0, 0.0, 15.0],
                                                 'by': 'user', 'user_words': 'this one'})
        _key, row_, _w = self.moves.solve_frame(job, move, fr, {**ctx, 'keep': 4})
        self.assertFalse(row_.get('alternatives'))  # a user's pick is the moment's only pose

    def test_the_pictures_own_view_and_lens_when_nothing_stands_in_front(self):
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertEqual((row_['offset_from_picture'], row_['lens_mm']), ({'azimuth_deg': 0.0, 'elevation_deg': 0.0}, 35.0))
        self.assertEqual(row_['engaged'][0], 'intent')
        self.assertEqual(row_['intent']['size']['class'], '꽉 참')
        # every placed view probed; judged best first until one passes - the first does - refined on fewer points
        # within its size band; the INTENT_TOP best listed for the shot's joining
        views = len(core.intent_views(core.intent_of(self.frame()[2]), (10.0, 0.0)))
        self.assertEqual(self.calls['probe'], views * 2 + 1)   # + the chosen pose probed again (probe_at_pose)
        # placed within its size class already: not refined (a height-only refine would undo a width fit)
        self.assertEqual((len(self.calls['solve']), row_['probe']['judged']), (0, 1))
        self.assertEqual(len(row_['candidates']), self.moves.INTENT_TOP)

    def test_a_placed_pose_outside_its_size_class_is_refined_to_it(self):
        self.seen_h = 0.2   # the picture's 꽉 참 (0.45-0.9): the placed pose shows it smaller
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertEqual(len(self.calls['solve']), 1)
        self.assertTrue(all(c['samples'] == self.moves.INTENT_SAMPLES and c['tol']['size'] > 0.03 for c in self.calls['solve']))

    def test_a_wall_in_front_turns_the_camera_within_the_band(self):
        self.wall = lambda view, lens: 0.8 if abs(view[1]) < 30 else 0.0
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertGreaterEqual(abs(row_['offset_from_picture']['azimuth_deg']), 30)
        self.assertGreaterEqual(row_['view'][0], core.LEVEL_DEG)   # a looking-down picture still looks down

    def test_a_thin_lamp_takes_the_wider_lens(self):
        self.lamp = lambda view, lens: 40.0 if lens < 35 else 10.0
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertEqual(row_['lens_mm'], 24.0)

    def test_a_wall_only_the_widest_rungs_clear_goes_down_the_ladder(self):
        # the wall hides the tower at 35 and 24 mm from every side; at 18 mm the camera stands in front of it
        self.wall = lambda view, lens: 0.8 if lens > 18 else 0.0
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertEqual(row_['lens_mm'], 18.0)
        self.assertEqual(row_['engaged'][:2], ['intent', 'widen'])
        views = len(core.intent_views(core.intent_of(self.frame()[2]), (10.0, 0.0)))
        self.assertEqual(self.calls['probe'], views * 4 + 1)   # 35, 24, then the ladder's 18, 14; + the chosen again

    def test_a_part_seen_only_between_the_steps_is_found_around_the_nearest_miss(self):
        # the lamp shows only from 35 deg round (between the 30 and 60 deg steps); from 30 it nearly does
        self.lamp = lambda view, lens: 60.0 if abs(view[1] - 35.0) < 1 else 25.0 if abs(view[1] - 30.0) < 1 else 5.0
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertEqual(row_['view'][1], 35.0)
        self.assertIn('local', row_['engaged'])
        views = len(core.intent_views(core.intent_of(self.frame()[2]), (10.0, 0.0)))
        local = self.moves.INTENT_LOCAL_SEEDS * len(self.moves.LOCAL_STEPS['azimuth_deg']) * len(self.moves.LOCAL_STEPS['elevation_deg']) * 2
        self.assertLessEqual(self.calls['probe'], views * 4 * 2 + local)   # grid (with closer retries) + the neighbourhood
        self.assertLessEqual(self.calls['blocked'], 1)                      # only the best is judged in full

    def test_an_enclosed_lamp_is_the_shot_lists_opening(self):
        self.lamp = lambda view, lens: 0.0
        with self.assertRaises(ValueError) as failed:
            self.moves.solve_frame(*self.frame())
        self.assertTrue(str(failed.exception).startswith('SHOW_NEEDS_OPENING'), str(failed.exception)[:200])
        self.assertEqual(failed.exception.report['code'], 'SHOW_NEEDS_OPENING')

    def test_every_pose_under_the_support_says_so(self):
        self.moves._support = lambda frame, skip, cache=None: (lambda position, from_z: 1e6)
        with self.assertRaises(ValueError) as failed:
            self.moves.solve_frame(*self.frame())
        self.assertEqual(failed.exception.report['code'], 'FRAME_BELOW_SUPPORT')
        self.assertEqual(self.calls['probe'], 0)   # refused before any ray

    def test_a_cut_picture_keeps_its_placed_pose(self):
        _key, row_, _w = self.moves.solve_frame(*self.frame(cut_edges=['bottom']))
        self.assertEqual(self.calls['solve'], [])
        self.assertIn('placed', row_['engaged'])

    def test_a_refine_that_cuts_what_the_placed_pose_kept_whole_is_undone(self):
        self.seen_h, self.refine_shift = 0.2, 100.0           # placed too small: refined, and the refine runs it off the left
        self.cuts_at = lambda position: ['left'] if position[0] > 50 else []
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertEqual(len(self.calls['solve']), 1)
        self.assertLess(row_['position'][0], 50)              # the placed pose stands

    def test_a_close_up_aimed_at_its_part_keeps_the_thing_it_belongs_to(self):
        # a cut picture whose own framing shows nothing (the lamp above the frame): aimed at the lamp the tower is a sliver,
        # half way back to the tower it keeps most of what the picture shows - both probed, the owner rule chooses
        lamp = ((-0.5, -0.5, 29.0), (0.5, 0.5, 30.0))
        self.moves.resolve_ref = lambda ref, index=None: {'box': lamp} if ref == 'lighthouse/lamp' else None
        first = set()

        def wall(view, lens):
            if (view, lens) not in first:   # each view's own framing: nothing of the tower in frame
                first.add((view, lens))
                return None
            return 0.0
        self.wall = wall
        self.box_at = lambda position: [0.45, 0.0, 0.55, 0.12] if position[2] > 26 else [0.3, 0.0, 0.7, 0.8]
        job, move, fr, ctx = self.frame(cut_edges=['bottom'], picture={'concept_id': 'c1'})
        job['picture_layout'] = {'c1': {'subject': {'box': [0.3, 0.1, 0.7, 1.0], 'cut_edges': ['bottom']}, 'context': [], 'unnamed': []}}
        _key, row_, _w = self.moves.solve_frame(job, move, fr, ctx)
        self.assertEqual(row_['placement']['aim'], 'between')
        self.assertFalse(row_.get('picture_terms', {}).get('owner_short'))

    def test_without_a_picture_the_layout_adds_nothing(self):
        _key, row_, _w = self.moves.solve_frame(*self.frame())   # no picture concept id: no layout
        self.assertFalse(row_.get('picture_terms'))

    def test_a_lying_body_leans_by_where_the_camera_stands(self):
        """A barge lying along x, its picture leaning it -73 deg from 15 deg up: the camera stands where the barge shows
        that lean unrolled (camera_frame_core.azimuth_for_tilt) - not a 73 deg roll, and no FRAME_TILT_UNREACHED."""
        from studio.blender_ops import camera_frame_core as framing
        self.moves._axis = lambda target, name: (1.0, 0.0, 0.0)
        self.moves._long_axis = lambda target: 'x'
        _key, row_, warnings = self.moves.solve_frame(*self.frame(elevation=15.0, tilt_deg=-73))
        want = framing.azimuth_for_tilt(15, (1.0, 0.0, 0.0), -73, 0.0)
        self.assertEqual((row_['azimuth_from'], row_['roll_deg']), ('tilt', 0.0))
        self.assertAlmostEqual(row_['picture_view'][1], want, places=3)   # the view the picture's lean implies
        self.assertFalse([w for w in warnings if w.startswith('FRAME_TILT_UNREACHED')])

    def test_the_band_holds_through_the_local_search(self):
        # the lamp shows only from 28 deg up - just past the band of a 10 deg picture; from 22 it nearly does
        band = core.elevation_band(10.0)[1]
        self.lamp = lambda view, lens: 60.0 if view[0] > band[1] + 1 else 25.0 if view[0] > 20 else 5.0
        with self.assertRaises(ValueError):
            self.moves.solve_frame(*self.frame())
        self.assertTrue(all(band[0] - 1e-6 <= v[0] <= band[1] + 1e-6 for v in self.calls['views']))

    def test_a_far_side_turn_is_said(self):
        self.wall = lambda view, lens: 0.8 if abs(core.wrap_deg(view[1])) < 100 else 0.0
        _key, row_, warnings = self.moves.solve_frame(*self.frame())
        self.assertGreater(abs(core.wrap_deg(row_['offset_from_picture']['azimuth_deg'])), core.INTENT_TURN_MAX)
        self.assertTrue(any('turned' in d for d in row_['departed']), row_['departed'])
        self.assertTrue(any(w.startswith('FRAME_INTENT_DEPARTED') for w in warnings), warnings)

    def test_the_fourth_best_is_judged_when_three_fail_in_full(self):
        self.full_blocked = lambda n: 0.9 if n <= 3 else 0.0
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertEqual(row_['probe']['judged'], 4)
        self.assertEqual(self.calls['blocked'], 4)

    def test_a_slightly_small_part_comes_closer_and_is_measured_in_full(self):
        # the probe passes the lamp everywhere but always reads it thin closer in (coarser); the full measure reads 28 px
        # at the placed size (within the slack of 32) and 40 px one step closer: the closer pose is judged in full
        self.lamp = lambda view, lens: 30.0 if self.calls['blocked'] == 0 else 20.0   # thin to the probe once judged
        self.shown = lambda view, lens: 28.0 if self.calls['blocked'] <= 1 else 40.0
        _key, row_, _w = self.moves.solve_frame(*self.frame(height=0.5))
        self.assertGreater(row_['placement']['closer'], 1.0)
        self.assertEqual(row_['must_see_px']['lighthouse/lamp'], 40.0)
        self.assertEqual(row_['probe']['judged'], 2)

    def test_a_placed_pose_is_measured_for_its_size(self):
        # a whole picture (0.5, 꽉 참) placed and measured at another size class is said; at its own it is not
        self.seen_h = 2.5
        _key, row_, warnings = self.moves.solve_frame(*self.frame())
        self.assertEqual(row_['seen']['height_share'], 2.5)
        self.assertTrue(any('익스트림 클로즈업' in d for d in row_['departed']), row_['departed'])
        self.seen_h = 0.6
        _key, row_, _w = self.moves.solve_frame(*self.frame())
        self.assertFalse(row_.get('departed'))
        # a cut picture keeps its placed pose; measured whole in the frame it is said
        self.seen_h = 0.6
        _key, row_, _w = self.moves.solve_frame(*self.frame(cut_edges=['bottom']))
        self.assertEqual(row_['achieved']['height_share'], 0.6)
        self.assertTrue(any('whole subject' in d for d in row_['departed']), row_['departed'])


class JoinTest(unittest.TestCase):
    """A shot joined from its moments' judged poses (core.joint_rank) and the intent path's shape."""

    def pose(self, score, azimuth, distance=20.0, lens=35.0):
        return {'score': score, 'azimuth_deg': azimuth, 'distance_m': distance, 'lens_mm': lens}

    def test_a_smaller_turn_wins_when_the_scores_are_close(self):
        # the first moment's best stands 120 deg from the second's; its runner-up, 0.5 worse, only 20
        moments = [[self.pose(1.0, -60.0), self.pose(1.5, 40.0)], [self.pose(0.0, 60.0)]]
        (cost, picks), *rest = core.joint_rank(moments, [2.0])
        self.assertEqual(picks, [1, 0])
        self.assertTrue(rest and rest[0][1] == [0, 0] and rest[0][0] > cost)   # the next best is kept for a failed path

    def test_the_big_turn_is_taken_when_only_it_passes(self):
        moments = [[self.pose(1.0, -60.0)], [self.pose(0.0, 60.0)]]
        self.assertEqual(core.joint_rank(moments, [2.0])[0][1], [0, 0])

    def test_turning_is_costed_per_second_the_short_way_and_with_distance_and_lens(self):
        a = self.pose(0.0, 170.0)
        self.assertAlmostEqual(core.join_cost(a, self.pose(0.0, -170.0), 1.0), core.JOIN_WEIGHTS['turn_deg_per_s'] * 20)
        self.assertLess(core.join_cost(a, self.pose(0.0, 90.0), 4.0), core.join_cost(a, self.pose(0.0, 90.0), 1.0))
        self.assertGreater(core.join_cost(a, self.pose(0.0, 170.0, distance=60.0), 1.0), 0.0)
        self.assertAlmostEqual(core.join_cost(a, self.pose(0.0, 170.0, lens=18.0), 1.0), 2 * core.JOIN_WEIGHTS['lens_step'])

    def test_one_moment_is_its_own_winner(self):
        self.assertEqual(core.joint_rank([[self.pose(0.5, 0.0), self.pose(0.2, 90.0)]], []), [(0.2, [1]), (0.5, [0])])

    def test_the_centripetal_path_does_not_overshoot_unevenly_spaced_poses(self):
        keys = [{'frame': i * 10, 'eye': (x, 0.0, 1.0), 'aim': (0.0, 0.0, 0.0), 'lens': lens}
                for i, (x, lens) in enumerate(((0.0, 50.0), (0.5, 14.0), (10.0, 35.0), (10.5, 24.0)))]
        progress = [i / 100 for i in range(301)]
        uniform = [p['eye'][0] for p in core.frame_path(keys, 301, 'straight', False, progress=progress)]
        path = core.frame_path(keys, 301, 'straight', False, progress=progress, shape='centripetal')
        xs = [p['eye'][0] for p in path]
        self.assertTrue(min(uniform) < 0.0 or max(uniform) > 10.5)              # the uniform curve swings past its ends
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(xs, xs[1:])))         # the centripetal one goes straight on
        self.assertAlmostEqual(min(xs), 0.0)
        lenses = [p['lens'] for p in path]
        for i in range(3):   # the lens never past either of its keys
            span = lenses[i * 100:(i + 1) * 100 + 1]
            lo, hi = sorted((keys[i]['lens'], keys[i + 1]['lens']))
            self.assertTrue(all(lo - 1e-9 <= v <= hi + 1e-9 for v in span), (i, min(span), max(span)))

    def test_an_orbit_is_a_turn_with_the_distance_kept(self):
        self.assertEqual(core.intent_path([0.0, 60.0], [20.0, 22.0]), 'arc')
        self.assertEqual(core.intent_path([0.0, 60.0], [20.0, 60.0]), 'straight')   # a push in that turns: a line
        self.assertEqual(core.intent_path([0.0, 10.0], [20.0, 20.0]), 'straight')
        self.assertEqual(core.intent_path([170.0, -170.0], [20.0, 20.0]), 'straight')   # 20 the short way round


class KeptIntentTest(unittest.TestCase):
    def test_what_a_solved_row_gave_up_of_its_intent(self):
        self.assertEqual(core.kept_intent({'departed': [], 'warnings': []}), [])
        self.assertEqual(core.kept_intent({}), [])
        row = {'departed': ['turned 60 deg round the subject (more than 45)'],
               'warnings': ['FRAME_INTENT_DEPARTED: s t=1.0: turned 60 deg round the subject (more than 45)',
                            'FRAME_TILT_UNREACHED: s t=1.0: the picture leans the subject 20 deg']}
        lost = core.kept_intent(row)
        self.assertEqual(len(lost), 2)                                        # the departure once, the lean warning
        self.assertTrue(lost[0].startswith('turned') and lost[1].startswith('FRAME_TILT_UNREACHED'))


class PlainToleranceIdentityTest(unittest.TestCase):
    """The exact solve is the intent's tolerance at its constants: solve(tol=None) and solve(tol=SIZE_TOL, PLACE_TOL,
    MAX_STEPS) give the same pose on the same inputs (camera_frame_core._solve_plain)."""

    def test_no_tol_is_the_constants(self):
        import camera_frame_core as framing
        points = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (0.0, 3.0, 6.0, 9.0)]
        visible = lambda position: points  # noqa: E731
        args = (visible, (0.0, -30.0, 4.5), (5.0, 0.0), {'height_share': 0.5, 'centre': [0.45, 0.55]}, 35.0, 9 / 16)
        plain = framing.solve(*args)
        same = framing.solve(*args, tol={'size': framing.SIZE_TOL, 'place': framing.PLACE_TOL, 'steps': framing.MAX_STEPS})
        self.assertEqual(plain, same)
        self.assertEqual(plain, framing.solve(*args, tol=None))
        loose = framing.solve(*args, tol={'size': 0.2, 'place': 0.1, 'steps': 3})
        self.assertLessEqual(loose['steps'], plain['steps'])

if __name__ == '__main__':
    unittest.main()
