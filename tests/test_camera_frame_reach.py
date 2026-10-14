"""A4 (2026-10-14): the framing solve reaches a read picture from every view the picture allows, or says exactly why not.

The failures it answers, in neutral scenes: a whole box past where the subject's long axis vanishes (no distance shows
it), an axis with no edge never placed, a size swinging between two depths, a step that fled from points behind the
camera or travelled 100 m, a pose under the support, a lying body's lean read as a roll. A target without cut_edges
(no picture read) solves exactly as before - the mechanisms engage only where the old step fails.
"""
import math
import random
import unittest

from studio.blender_ops import camera_frame_core as core

ASPECT = 9 / 16
TANS24 = core.tangents(24, ASPECT)


def surface(lo, hi, n=4000, seed=3):
    """Points spread evenly over a box's six faces (a stand-in for camera_frame.sample)."""
    rng = random.Random(seed)
    size = [hi[i] - lo[i] for i in range(3)]
    faces = []
    for axis in range(3):
        a, b = [i for i in range(3) if i != axis]
        for at in (lo[axis], hi[axis]):
            faces.append((size[a] * size[b], axis, at, a, b))
    total = sum(f[0] for f in faces)
    out = []
    for area, axis, at, a, b in faces:
        for _ in range(max(1, round(n * area / total))):
            p = [0.0, 0.0, 0.0]
            p[axis], p[a], p[b] = at, lo[a] + rng.random() * size[a], lo[b] + rng.random() * size[b]
            out.append(tuple(p))
    return out


def axis_of(points, direction):
    t = core.axis_extent(points, direction)
    n = len(points)
    return {'dir': direction, 't': t, 'origin': tuple(sum(p[i] for p in points) / n for i in range(3))}


def screen_of(points, result, view, lens=24, aspect=ASPECT, roll=0.0):
    pts, _ = core.project(points, result['position'], core.basis(*view, roll_deg=roll), core.tangents(lens, aspect))
    return pts


def median(values):
    values = sorted(values)
    return values[len(values) // 2]


CHIMNEY = surface((-3, -3, 0), (3, 3, 60))          # a 6 x 6 x 60 m chimney standing on z = 0
UP = (0.0, 0.0, 1.0)


def chimney_picture(segment=True):
    """Looking up 30 deg at the chimney at 24 mm: its foot at 0.85 of the frame, its top far past the frame's top
    (whole box top -4.0) and its sides past both side edges - a close, low picture."""
    target = {'height_share': 4.85, 'centre': [0.5, -1.575], 'width_share': 1.3, 'edges': {'bottom': 0.85},
              'cut_edges': ['left', 'right', 'top'], 'band_ok': True, 'axis': axis_of(CHIMNEY, UP)}
    if segment:
        target['segment'] = {'screen_axis': 'y', 'cut_at': {'top': round(4.0 / 4.85, 4)}}
    return target


class ReachTest(unittest.TestCase):
    def test_vanishing_point_is_where_a_far_point_lands(self):
        view = (-30, 20)
        frame_basis = core.basis(*view)
        d = core._norm((0.3, 0.2, 1.0))
        vp = core.vanishing_point(d, frame_basis, TANS24)
        far, _ = core.project([core._mul(d, 1e7)], (0.0, -50.0, 0.0), frame_basis, TANS24)
        self.assertAlmostEqual(vp[0], far[0][0], places=4)
        self.assertAlmostEqual(vp[1], far[0][1], places=4)
        self.assertIsNone(core.vanishing_point(frame_basis[1], frame_basis, TANS24))   # across the view: none

    def test_a_close_low_chimney_is_placed_by_where_the_frame_cuts_it(self):
        view = (-30, 0)
        target = chimney_picture()
        self.assertIsNotNone(core.whole_reachable(target, core.basis(*view), TANS24))   # the whole box spans the VP
        with self.assertRaises(ValueError):   # the old solve: the whole box at any distance - never reached
            core.solve(lambda p: CHIMNEY, (0, -40, 2), view, {k: v for k, v in target.items() if k not in ('cut_edges', 'segment', 'axis', 'band_ok')},
                       24, ASPECT)
        result = core.solve(lambda p: CHIMNEY, (0, -40, 2), view, target, 24, ASPECT)
        self.assertEqual(result['mode'], 'segment')
        screen = screen_of(CHIMNEY, result, view)
        foot = core._quantile(sorted(p[1] for p in screen if 0 <= p[0] <= 1), 1 - core.TRIM)
        self.assertAlmostEqual(foot, 0.85, delta=core.PLACE_TOL + 0.005)
        cut_z = 60 * (1 - 4.0 / 4.85)                      # the frame's top crosses the chimney here
        at_cut = [p for p in CHIMNEY if abs(p[2] - cut_z) <= 1.2]
        # the cut stands where the size and the foot's place leave it: within the size's tolerance of the frame's top
        self.assertAlmostEqual(median(s[1] for s in screen_of(at_cut, result, view)), 0.0, delta=core.SIZE_TOL * 0.85 + 0.01)
        self.assertAlmostEqual(median(s[0] for s in screen_of(at_cut, result, view)), 0.5, delta=core.PLACE_TOL + 0.01)
        self.assertIn('place_whole', result['engaged'])    # no side edge: across, the whole box's centre places it

    def test_out_of_reach_without_a_station_says_where_the_axis_vanishes(self):
        view = (-30, 0)
        with self.assertRaises(core.FrameSolveError) as caught:
            core.solve(lambda p: CHIMNEY, (0, -40, 2), view, chimney_picture(segment=False), 24, ASPECT)
        error = caught.exception
        self.assertEqual(error.code, 'FRAME_UNREACHABLE')
        self.assertTrue(str(error).startswith('FRAME_UNREACHABLE'))
        vp = core.vanishing_point(UP, core.basis(*view), TANS24)
        self.assertAlmostEqual(error.data['vanishing_point'][1], vp[1], places=3)
        self.assertEqual(error.data['whole'], [-4.0, 0.85])

    def test_a_reachable_whole_box_solves_exactly_as_before(self):
        """A tower seen from above (+15 deg) cut at the top and left: the whole box is in reach, so
        the picture's data changes nothing - same pose, mode whole, nothing engaged."""
        tower = surface((-2, -2, 0), (2, 2, 40))
        view = (15.0, 10)
        legacy = {'height_share': 1.6, 'centre': [0.45, 0.1], 'edges': {'right': 0.75, 'bottom': 0.9}}
        picture = {**legacy, 'cut_edges': ['left', 'top'], 'width_share': 0.8, 'axis': axis_of(tower, UP),
                   'segment': {'screen_axis': 'y', 'cut_at': {'top': 0.4375}}, 'band_ok': False}
        self.assertIsNone(core.whole_reachable(picture, core.basis(*view), TANS24))
        old = core.solve(lambda p: tower, (0, -60, 30), view, legacy, 24, ASPECT)
        new = core.solve(lambda p: tower, (0, -60, 30), view, picture, 24, ASPECT)
        self.assertEqual((new['mode'], new['engaged']), ('whole', []))
        self.assertEqual(new['position'], old['position'])
        self.assertEqual(new['achieved'], old['achieved'])

    def test_a_tower_cut_on_all_four_sides(self):
        """A low close view of a tower past all four frame edges: two stations give the size and the place along it,
        the whole box's centre the place across (before: no edges, so the plain solve sized the clipped view)."""
        tower = surface((-2, -2, 0), (2, 2, 40))
        view = (-45, 0)
        # the frame shows the tower from 30 % to 70 % of its length (from the top): whole box from -0.75 to 1.75
        target = {'height_share': 2.5, 'centre': [0.5, 0.5], 'width_share': 1.4, 'cut_edges': ['bottom', 'left', 'right', 'top'],
                  'band_ok': True, 'axis': axis_of(tower, UP), 'segment': {'screen_axis': 'y', 'cut_at': {'top': 0.3, 'bottom': 0.7}}}
        self.assertIsNotNone(core.whole_reachable(target, core.basis(*view), TANS24))
        result = core.solve(lambda p: tower, (0, -30, 5), view, target, 24, ASPECT)
        self.assertEqual(result['mode'], 'segment')
        for share, edge in ((0.3, 0.0), (0.7, 1.0)):
            ring = [p for p in tower if abs(p[2] - 40 * (1 - share)) <= 0.8]
            self.assertAlmostEqual(median(s[1] for s in screen_of(ring, result, view)), edge, delta=core.PLACE_TOL + 0.01)
        self.assertIn('place_whole', result['engaged'])

    def test_an_axis_without_an_edge_is_placed_by_the_whole_centre(self):
        """A mast seen from above, cut at the top and bottom, its left side drawn at 0.3: the whole box (in reach) is
        centred where the picture puts it (2026-10-14: the axis without an edge was never placed - a leg the picture showed left the
        frame)."""
        mast = surface((-1, -1, 0), (1, 1, 30))
        view = (15, 0)
        target = {'height_share': 1.5, 'centre': [0.45, 0.2], 'width_share': 0.3, 'edges': {'left': 0.3},
                  'cut_edges': ['bottom', 'right', 'top'], 'axis': axis_of(mast, UP), 'band_ok': False}
        self.assertIsNone(core.whole_reachable(target, core.basis(*view), TANS24))
        legacy = {k: target[k] for k in ('height_share', 'centre', 'edges')}
        old = core.solve(lambda p: mast, (0, -40, 30), view, legacy, 24, ASPECT)
        span = sorted(p[1] for p in screen_of(mast, old, view))
        self.assertGreater(abs((span[0] + span[-1]) / 2 - 0.2), 0.05)   # before: wherever the start left it
        result = core.solve(lambda p: mast, (0, -40, 30), view, target, 24, ASPECT)
        span = sorted(p[1] for p in screen_of(mast, result, view))
        self.assertAlmostEqual((core._quantile(span, core.TRIM) + core._quantile(span, 1 - core.TRIM)) / 2, 0.2, delta=core.PLACE_TOL + 0.005)
        self.assertEqual(result['engaged'], ['place_whole'])

    def test_a_crane_boom_rising_away(self):
        """A 50 m boom raised 40 deg, seen level from beside its foot (0, -60): the picture puts the foot at 0.9 and its
        left side at 0.3 and runs the boom out of the top and the right - its whole box spans where the boom vanishes, so
        the frame's top is placed where it crosses the boom (40 % of its length from the far end)."""
        d = core._norm((math.cos(math.radians(40)), 0.0, math.sin(math.radians(40))))
        rng = random.Random(5)
        side = core._norm(core._cross(d, (0, 1, 0)))
        other = core._cross(d, side)
        boom = []
        for _ in range(3000):
            s, a = rng.random() * 50, rng.random() * 2 * math.pi
            boom.append(tuple(d[i] * s + 0.6 * (side[i] * math.cos(a) + other[i] * math.sin(a)) + (0, 0, 2)[i] for i in range(3)))
        view = (0, -60)
        frame_basis = core.basis(*view)
        target = {'height_share': 1.5, 'centre': [0.75, 0.15], 'width_share': 0.9, 'edges': {'left': 0.3, 'bottom': 0.9},
                  'cut_edges': ['right', 'top'], 'axis': axis_of(boom, d), 'band_ok': False,
                  'segment': {'screen_axis': 'y', 'cut_at': {'top': 0.4}}}
        self.assertIsNotNone(core.whole_reachable(target, frame_basis, TANS24))
        start = core._sub((10, 0, 10), core._mul(frame_basis[0], 40))
        result = core.solve(lambda p: boom, start, view, target, 24, ASPECT)
        self.assertEqual(result['mode'], 'segment')
        self.assertAlmostEqual(result['achieved']['stations']['top'], 0.0, delta=core.SIZE_TOL * 0.9 + 0.005)
        self.assertAlmostEqual(result['achieved']['box'][3], 0.9, delta=core.PLACE_TOL)
        self.assertAlmostEqual(result['achieved']['box'][0], 0.3, delta=core.PLACE_TOL)
        at_cut = [p for p in boom if abs(core._dot(p, d) - (core._dot((0, 0, 2), d) + 0.6 * 50)) <= 1.0]
        self.assertAlmostEqual(median(s[1] for s in screen_of(at_cut, result, view)), 0.0, delta=core.SIZE_TOL * 0.9 + 0.01)

    def test_a_long_barge_drawn_whole_is_not_a_band(self):
        """A 20:1 barge the picture shows whole: it fits the frame's width (2026-10-14: a hull ran off both sides as a
        'band' - the picture did not cut it)."""
        barge = surface((-40, -2, 0), (40, 2, 4))
        view = (15, 30)
        legacy = {'height_share': 0.14, 'centre': [0.5, 0.5]}
        old = core.solve(lambda p: barge, (0, -150, 40), view, legacy, 35, ASPECT)
        self.assertTrue(old['achieved']['band'])
        self.assertGreater(old['achieved']['width_share'], 1.0)
        result = core.solve(lambda p: barge, (0, -150, 40), view, {**legacy, 'cut_edges': [], 'width_share': 0.9, 'band_ok': False}, 35, ASPECT)
        self.assertFalse(result['achieved']['band'])
        self.assertLessEqual(result['achieved']['width_share'], 1 - 2 * core.MARGIN + 0.01)
        self.assertEqual(result['limited_by'], 'width')

    def test_a_layer_cut_at_both_sides_stays_a_band(self):
        lo, hi = (-2.5, -2.5, 3.0), (2.5, 2.5, 3.35)
        pts = [p for p in surface(lo, hi) if abs(p[1] - lo[1]) < 1e-9 or abs(p[2] - hi[2]) < 1e-9]
        target = {'height_share': 0.65, 'centre': [0.5, 0.4], 'cut_edges': ['left', 'right'], 'band_ok': True}
        result = core.solve(lambda p: pts, (0, -12, 6), (20, -16), target, 50, ASPECT)
        self.assertTrue(result['achieved']['band'])
        self.assertAlmostEqual(result['achieved']['height_share'], 0.65, delta=0.65 * core.SIZE_TOL)

    def test_a_size_swinging_over_a_flange_is_bracketed(self):
        """A mast whose wide flange shows only from nearer than 30 m (a deck edge hides it beyond): the size asked
        lies in the jump. Before: 16 steps swinging between two depths, a generic failure. Now the swing is bracketed
        and named."""
        mast = surface((-0.5, -0.5, 0), (0.5, 0.5, 20), n=1500)
        flange = surface((-6, -6, -3), (6, 6, -2.5), n=800)
        view = (0, 0)

        def visible(position):
            return mast + (flange if -position[1] < 30 else [])
        target = {'height_share': 0.7, 'centre': [0.5, 0.5]}   # the mast alone shows 0.7 only nearer than 30 m
        with self.assertRaises(ValueError):
            core.solve(visible, (0, -28, 9), view, target, 35, ASPECT)
        trace = []
        with self.assertRaises(core.FrameSolveError) as caught:
            core.solve(visible, (0, -28, 9), view, {**target, 'cut_edges': [], 'band_ok': False}, 35, ASPECT, trace=trace)
        self.assertEqual(caught.exception.code, 'FRAME_SIZE_CYCLE')
        lo, hi = sorted(caught.exception.data['bracket'])
        self.assertLess(hi - lo, core.CYCLE_WIDTH * 40)
        self.assertTrue(lo <= -30 + 0.5 and hi >= -30 - 0.5, (lo, hi))   # around the depth where the flange appears
        self.assertTrue(trace and all('step' in row for row in trace))

    def test_points_behind_the_camera_are_clipped_not_fled(self):
        """A beam running past the camera into the distance, its strip cut at the left and right: a few of its points
        behind the camera made every step back off by the whole depth, and the solve failed; the second pass clips them
        (the near plane) and solves where it stands."""
        beam = surface((-500, 9.5, -0.5), (20, 10.5, 0.5), n=8000)
        view = (0, 50)
        target = {'height_share': 0.1, 'centre': [0.5, 0.5], 'edges': {'top': 0.45, 'bottom': 0.55}, 'cut_edges': ['left', 'right'], 'band_ok': True}
        legacy = {k: target[k] for k in ('height_share', 'centre', 'edges')}
        with self.assertRaises(ValueError):
            core.solve(lambda p: beam, (0, 0, 0), view, legacy, 24, ASPECT)
        trace = []
        result = core.solve(lambda p: beam, (0, 0, 0), view, target, 24, ASPECT, trace=trace)
        self.assertIn('clip', result['engaged'])
        self.assertTrue(any(r['pass'] == 'first' and r.get('behind') for r in trace))   # the first pass backed off
        box = result['achieved']['box']
        self.assertAlmostEqual(box[3] - box[1], 0.1, delta=0.1 * core.SIZE_TOL)
        _, behind = core.project(beam, result['position'], core.basis(*view), TANS24)
        self.assertGreater(behind, 0)                                   # solved with some of it behind the camera
        self.assertLessEqual(behind / len(beam), core.BEHIND_MAX)

    def test_a_rescue_step_travels_at_most_half_the_depth(self):
        crate = surface((-1, -1, 0), (1, 1, 2))
        view = (10, 0)
        target = {'height_share': 0.4, 'centre': [0.5, 0.5], 'cut_edges': [], 'band_ok': False}
        first = core.solve(lambda p: crate, (0, -400, 70), view, target, 35, ASPECT)
        self.assertEqual(first['engaged'], [])     # a solve that works on the first pass: the old steps, nothing engaged
        trace = []
        result = core._solve_plain(lambda p: crate, (0, -400, 70), view, target, core.basis(*view), core.tangents(35, ASPECT), ASPECT,
                                   None, trace, rescue=True)
        self.assertIn('cap', result['engaged'])
        measured = [r for r in trace if 'depth' in r]
        for a, b in zip(measured, measured[1:]):
            self.assertLessEqual(core._length(core._sub(b['position'], a['position'])), core.TRAVEL_MAX * a['depth'] + 1e-4)
        self.assertAlmostEqual(result['achieved']['height_share'], 0.4, delta=0.4 * core.SIZE_TOL)

    def test_a_pose_under_the_support_is_refused_not_lifted(self):
        """A crate on a table top at z 1 m, the picture looking up at it: every pose is under the table."""
        crate = surface((-0.2, -0.2, 1.0), (0.2, 0.2, 1.4))
        view = (-20, 0)
        floor = lambda position: (1.0, 0.1)   # noqa: E731 - the table top, 0.1 m margin
        target = {'height_share': 0.4, 'centre': [0.5, 0.5], 'cut_edges': [], 'band_ok': False}
        with self.assertRaises(core.FrameSolveError) as caught:
            core.solve(lambda p: crate, (0, -4, 2.5), view, target, 35, ASPECT, floor=floor)
        self.assertEqual(caught.exception.code, 'FRAME_BELOW_SUPPORT')
        self.assertEqual((caught.exception.data['top'], caught.exception.data['margin']), (1.0, 0.1))
        self.assertLess(caught.exception.data['z'], 1.1)     # the pose as reached: never lifted onto the table
        # from above the table the same crate solves, nothing engaged
        result = core.solve(lambda p: crate, (0, -4, 2.5), (20, 0), target, 35, ASPECT, floor=floor)
        self.assertGreater(result['position'][2], 1.1)

    def test_everything_behind_the_camera_is_a_frame_error(self):
        """Every point behind the camera at every step (a focus that follows the camera): the solve says so, it does
        not break on a measure it never made (UnboundLocalError on m / box before)."""
        forward = core.basis(0, 0)[0]
        behind = lambda position: [core._sub(position, core._mul(forward, 1.0))]   # noqa: E731
        for target in ({'height_share': 0.5, 'centre': [0.5, 0.5]}, {'height_share': 0.5, 'centre': [0.5, 0.5], 'edges': {'left': 0.2}}):
            with self.subTest(target=target), self.assertRaises(core.FrameSolveError) as caught:
                core.solve(behind, (0, -5, 0), (0, 0), target, 35, ASPECT)
            self.assertEqual(caught.exception.code, 'FRAME_UNSOLVED')

    def test_a_lying_body_leans_by_where_the_camera_stands(self):
        """A ship lying along x, the picture leaning it -73 deg at 15 deg elevation: the azimuth that shows that lean
        unrolled, nearest the side the shot holds."""
        axis = (1.0, 0.0, 0.0)
        az = core.azimuth_for_tilt(15, axis, -73, near_deg=0.0)
        forward = core.basis(15, az)[0]
        self.assertAlmostEqual(core.screen_tilt_deg(core._mul(forward, -1.0), (0, 0, 0), axis), -73, delta=0.5)
        other = core.azimuth_for_tilt(15, axis, -73, near_deg=180.0)
        self.assertAlmostEqual(abs((other - az + 180) % 360 - 180), 180, delta=1.0)   # the same lean from the other side
        self.assertIsNone(core.azimuth_for_tilt(0.0, axis, -30))                       # level, a lying axis never leans

    def test_a_long_band_from_a_frame_written_before_solves_as_before(self):
        """Review 2026-10-14: a 120 m slab framed by its bottom edge only, from a frame written before the picture's width
        was kept: its cut is rebuilt (frame_segment), and the axis across - its span centre a guess under perspective -
        is not steered; the pose is the legacy one, nothing engaged."""
        from studio.blender_ops.camera_moves_core import frame_segment, frame_seed
        slab = surface((-60, -3, -3), (60, 3, 3))
        frame = {'height_share': 1.2, 'centre': [0.5, 0.3], 'edges': {'bottom': 0.9}}
        view = (10, 0)
        start = frame_seed(((-60, -3, -3), (60, 3, 3)), {**frame, 'view': {'elevation_deg': 10, 'azimuth_deg': 0}}, 35, ASPECT)[0]
        legacy = core.solve(lambda p: slab, start, view, frame, 35, ASPECT)
        picture = core.solve(lambda p: slab, start, view, {**frame, **frame_segment(frame)}, 35, ASPECT)
        self.assertEqual((picture['position'], picture['engaged'], picture['pass']), (legacy['position'], [], 'first'))

    def test_what_stepping_cannot_change_is_not_rescued(self):
        trace = []
        with self.assertRaises(core.FrameSolveError) as caught:
            core.solve(lambda p: [], (0, -5, 1), (0, 0), {'height_share': 0.5, 'centre': [0.5, 0.5], 'cut_edges': []}, 50, ASPECT, trace=trace)
        self.assertFalse(caught.exception.rescuable)
        with self.assertRaises(core.FrameSolveError) as caught:
            core.solve(lambda p: CHIMNEY, (0, -40, 2), (-30, 0), {'centre': [0.5, 0.5], 'edges': {'bottom': 0.85}, 'cut_edges': ['left', 'right', 'top']},
                       24, ASPECT, trace=trace)
        self.assertIn('nothing gives its size', str(caught.exception))
        self.assertFalse(any(row['pass'] == 'rescue' for row in trace))
