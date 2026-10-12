"""Screen grammar core: canvas bands found on the picture rows only, composition through the engine's own screen
metrics, camera axes from typical (median) steps over new pictures only, and T2 + T3 combined by compatibility."""
import math
import unittest

from studio import grammar_core as g
from studio.blender_ops.screen_core import METRICS


def mask(w, h, rows):
    return [y in rows for y in range(h) for _ in range(w)]


def pair(tx=0.0, ty=0.0, ds=0.0, rot=0.0, residual=0.0001):
    return {'ok': True, 'tx': tx, 'ty': ty, 'ds': ds, 'rot': rot, 'residual': residual}


class CanvasTest(unittest.TestCase):
    def test_full_bleed_and_window(self):
        self.assertEqual(g.canvas(mask(40, 80, range(80)), 40, 80)['layout'], 'full_bleed')
        window = g.canvas(mask(40, 80, range(26, 54)), 40, 80)   # a picture band between static title/subtitle bands
        self.assertEqual(window['layout'], 'window')
        self.assertEqual(window['content_rect'], [0.0, 0.325, 1.0, 0.675])
        self.assertEqual(len(window['bands']), 2)

    def test_box_in_canvas_to_content(self):
        self.assertEqual(g.to_content([0.25, 0.4, 0.75, 0.6], [0.0, 0.3, 1.0, 0.7]), [0.25, 0.25, 0.75, 0.75])


class CompositionTest(unittest.TestCase):
    def test_same_numbers_as_screen_targets(self):
        box = [0.2, 0.3, 0.6, 0.9]
        shape = {'bbox': box, 'centroid': [0.4, 0.6], 'share': 0.24}
        got = g.composition(box)
        for name, (fn, _) in METRICS.items():
            if fn is not None and name != 'ui_overlap':
                self.assertAlmostEqual(got[name], fn(shape), places=4)
        self.assertIsNone(g.composition(None))


class CameraTest(unittest.TestCase):
    def test_axes(self):
        self.assertEqual(g.camera([pair()] * 30, 30)['axis'], 'still')
        self.assertEqual(g.camera([pair(tx=-0.005)] * 30, 30)['axis'], 'x')
        self.assertEqual(g.camera([pair(ds=0.004)] * 30, 30)['axis'], 'scale_in')
        self.assertEqual(g.camera([pair(ds=-0.004)] * 30, 30)['axis'], 'scale_out')
        self.assertEqual(g.camera([pair(tx=0.004, ds=0.004)] * 30, 30)['axis'], 'compound')

    def test_a_picture_that_never_changes_is_still(self):
        self.assertEqual(g.camera([{'ok': False, 'duplicate': True}] * 60, 30)['axis'], 'still')
        self.assertEqual(g.camera([{'ok': False}] * 60, 30)['axis'], 'unknown')   # changing but unmeasurable stays unknown

    def test_one_wild_frame_does_not_move_a_still_shot(self):
        frames = [pair()] * 29 + [pair(rot=0.5, ds=0.3)]   # a flash or a particle burst
        self.assertEqual(g.camera(frames, 30)['axis'], 'still')

    def test_repeated_frames_do_not_hide_motion(self):
        """24 fps material delivered at 60: most pairs repeat the picture; motion is read from the new pictures."""
        frames = []
        for i in range(60):
            frames.append(pair(tx=-0.006) if i % 5 in (0, 2) else {'ok': False, 'duplicate': True})
        cam = g.camera(frames, 60)
        self.assertEqual(cam['axis'], 'x')
        self.assertAlmostEqual(cam['new_pictures_per_s'], 24.0)
        self.assertAlmostEqual(cam['rate_per_s']['x'], -0.144, places=3)

    def test_t3_names_the_move_t2_allows(self):
        self.assertEqual(g.camera_move({'axis': 'scale_in'}, ['push', 'push']), {'move': 'push', 'axis': 'scale_in', 'agree': True})
        self.assertEqual(g.camera_move({'axis': 'scale_in'}, ['zoom', 'pan']), {'move': 'zoom', 'axis': 'scale_in', 'agree': False})
        # runs agree on a name the axis does not allow: the name stands (a visual judgement), flagged
        self.assertEqual(g.camera_move({'axis': 'scale_in'}, ['compound', 'compound']), {'move': 'compound', 'axis': 'scale_in', 'agree': False})
        self.assertEqual(g.camera_move({'axis': 'x'}, [])['agree'], None)


class BoundaryTest(unittest.TestCase):
    def test_cut_frames_close_together_are_one_cut_at_the_peak(self):
        self.assertEqual(g.cut_clusters([(1.0, 0.3), (1.1, 0.6), (1.2, 0.4), (5.0, 0.27)]),
                         [{'t': 1.1, 'score': 0.6}, {'t': 5.0, 'score': 0.27}])

    def test_a_morph_is_a_hump_a_fast_rigid_move_is_not(self):
        calm = {'ok': True, 'mad': 3.0, 'residual': 0.0005, 'tx': 0, 'ty': 0, 'ds': 0, 'rot': 0}
        morph = {**calm, 'mad': 18.0, 'residual': 0.03}          # the picture changes and no camera motion explains it
        whip = {**calm, 'mad': 18.0, 'residual': 0.0006, 'tx': 0.05}  # the picture changes because the camera moved
        humps = g.transition_humps([calm] * 60 + [morph] * 8 + [calm] * 60, 30)
        self.assertEqual(len(humps), 1)
        self.assertTrue(2.0 <= humps[0]['t'] <= 2.3)
        self.assertEqual(g.transition_humps([calm] * 60 + [whip] * 8 + [calm] * 60, 30), [])

    def test_a_hump_at_a_cut_is_that_cut(self):
        found = g.boundaries([{'t': 2.0, 'score': 0.5}], [{'start': 1.9, 't': 2.1, 'end': 2.3}, {'start': 5.0, 't': 5.4, 'end': 5.9}])
        self.assertEqual([(b['t'], b['kind']) for b in found], [(2.0, 'cut'), (5.4, 'transition')])


class FlowTest(unittest.TestCase):
    def test_scale_step(self):
        self.assertEqual(g.scale_step(0.3, 0.6)['step'], 'closer')
        self.assertEqual(g.scale_step(0.6, 0.3)['step'], 'wider')
        self.assertEqual(g.scale_step(0.5, 0.55)['step'], 'same')
        self.assertIsNone(g.scale_step(None, 0.5))


class FeelTest(unittest.TestCase):
    """2026-10-12: the same screen speed can feel unlike - where the camera heads, what streaks past, the subject against
    the world, the rhythm."""
    def pair(self, k, centre, aspect=16 / 9, **extra):
        # a push by k about `centre` (content shares): d = k (p - p0), so t = -k p0 in width units
        p0 = (centre[0] - 0.5, (centre[1] - 0.5) * aspect)
        return {'ok': True, 'tx': -k * p0[0], 'ty': -k * p0[1], 'ds': math.log1p(k), 'rot': 0.0, **extra}

    def test_heading_is_the_fixed_point_of_the_push(self):
        x, y = g.heading(self.pair(0.01, (0.7, 0.3)), 16 / 9)
        self.assertAlmostEqual(x, 0.7, places=6)
        self.assertAlmostEqual(y, 0.3, places=6)
        self.assertIsNone(g.heading({'ok': True, 'tx': 0.01, 'ty': 0.0, 'ds': 0.0, 'rot': 0.0}, 16 / 9))   # a slide grows about nothing

    def test_heading_on_the_explained_part(self):
        frames = [self.pair(0.01, (0.7, 0.3), feel={'part_box': [0.6, 0.2, 0.8, 0.4]}) for _ in range(5)]
        feel = g.feel(frames, 30, {'axis': 'scale_in', 'rate_per_s': {'roll_deg': 0.0}})
        self.assertEqual(feel['heading_on'], 1.0)
        self.assertAlmostEqual(feel['heading_off'], math.hypot(0.2, 0.2), places=3)
        off = g.feel(frames, 30, {'axis': 'scale_in'}, box=[0.0, 0.6, 0.3, 1.0])   # a given box is used only where a pair has none
        self.assertEqual(off['heading_on'], 1.0)
        frames = [self.pair(0.01, (0.7, 0.3)) for _ in range(5)]
        self.assertEqual(g.feel(frames, 30, {'axis': 'scale_in'}, box=[0.0, 0.6, 0.3, 1.0])['heading_on'], 0.0)

    def test_subject_against_the_world_and_riding_along(self):
        against = [{'ok': True, 'tx': 0, 'ty': 0, 'ds': 0, 'rot': 0, 'feel': {'camera_vel': [0, 0, 2.0], 'subject_vel': [0, 0, -30.0]}}] * 3
        self.assertEqual(g.feel(against, 30, {'axis': 'y'})['counter'], -1.0)
        parked = [{'ok': True, 'tx': 0, 'ty': 0, 'ds': 0, 'rot': 0, 'feel': {'camera_vel': [0, 0, 2.0], 'subject_vel': [0, 0, 0.0]}}] * 3
        self.assertIsNone(g.feel(parked, 30, {'axis': 'y'})['counter'])
        riding = [{'ok': True, 'tx': 0, 'ty': 0, 'ds': 0, 'rot': 0, 'feel': {'subject_flow': [0.001, 0], 'world_flow': [0.02, 0]}}] * 3
        self.assertEqual(g.feel(riding, 30, {'axis': 'x'})['pinned'], 0.05)

    def test_rhythm(self):
        self.assertEqual(g.rhythm({'peak_t': 0.1, 'hold_frac': 0.4}, 'scale_in'), 'burst_settle')
        self.assertEqual(g.rhythm({'peak_t': 0.8, 'hold_frac': 0.0}, 'x'), 'build')
        self.assertEqual(g.rhythm({'peak_t': 0.4, 'hold_frac': 0.1}, 'x'), 'even')
        self.assertEqual(g.rhythm(None, 'still'), 'hold')

    def test_pixels_give_no_scene_values(self):
        frames = [{'ok': True, 'tx': 0.01, 'ty': 0, 'ds': 0, 'rot': 0, 'streak': 0.1, 'lost': 0.02}] * 4
        feel = g.feel(frames, 30, {'axis': 'x'})
        self.assertEqual(feel['streak'], 0.1)
        for key in ('near', 'counter', 'pinned', 'eye_rate', 'pitch_deg', 'edges_per_s'):
            self.assertIsNone(feel[key], key)


if __name__ == '__main__':
    unittest.main()
