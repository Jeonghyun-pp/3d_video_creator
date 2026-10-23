import math
import unittest

from studio.blender_ops import env_shell_core as core
from studio import world as world_mod

WORLD = {'kind': 'independent', 'place': 'open sea', 'time_light': {'time_of_day': 'dusk'}, 'shell': {'ground': 'sea'},
         'beats': [], 'elements': []}


class ResolveTest(unittest.TestCase):
    def test_world_shell_sky_from_light(self):
        self.assertEqual(core.resolve({}, WORLD), {'ground': 'sea', 'sky': 'dusk'})

    def test_shot_shell_wins(self):
        shot = {'scene': {'shell': {'ground': 'land', 'sky': 'day', 'ground_z_m': -2}}}
        self.assertEqual(core.resolve(shot, WORLD), {'ground': 'land', 'sky': 'day', 'ground_z_m': -2})

    def test_none_without_declaration_or_place(self):
        self.assertIsNone(core.resolve({}, {**WORLD, 'shell': None}))
        self.assertIsNone(core.resolve({}, {**WORLD, 'kind': 'none'}))

    def test_relief_rises_outside_the_place_and_rolls_round_it(self):
        self.assertEqual(core.relief_height(299.0, 1.0, 60.0, 300.0), 0.0)            # where the subject stands: flat
        heights = [core.relief_height(800.0, t * 0.1, 60.0, 300.0) for t in range(63)]
        self.assertTrue(0.3 * 60 <= min(heights) and max(heights) <= 60.0 and max(heights) - min(heights) > 10, (min(heights), max(heights)))
        self.assertLess(core.relief_height(320.0, 1.0, 60.0, 300.0), core.relief_height(700.0, 1.0, 60.0, 300.0))   # it rises gently
        shot = {'scene': {'shell': {'ground': 'land', 'relief': {'height_m': 60.0, 'start_m': 300.0}}}}
        self.assertEqual(core.resolve(shot, WORLD)['relief'], {'height_m': 60.0, 'start_m': 300.0})

    def test_unknown_kind_refused(self):
        with self.assertRaises(core.ShellInvalid):
            core.resolve({'scene': {'shell': {'ground': 'lava'}}}, WORLD)

    def test_unknown_kind_is_a_studio_error_on_the_host(self):
        from studio.common import StudioError
        with self.assertRaises(StudioError) as caught:
            world_mod.shell_for({'shot_id': 's1', 'scene': {'shell': {'ground': 'lava'}}}, WORLD)
        self.assertEqual(caught.exception.code, 'SHELL_INVALID')

    def test_suggest_from_place(self):
        self.assertEqual(core.suggest({'place': '대서양 해상, 무인선 주변', 'time_light': {'time_of_day': 'day'}}), {'ground': 'sea', 'sky': 'day'})
        self.assertEqual(core.suggest({'place': 'low earth orbit'}), {'ground': 'none', 'sky': 'space'})
        self.assertEqual(core.suggest({'place': 'a quiet town'})['ground'], 'land')

    def test_default_sky_is_day(self):
        self.assertEqual(core.sky_for({}), 'day')
        self.assertEqual(core.sky_for({'time_light': {'mood': 'quiet night'}}), 'night')


class GeometryTest(unittest.TestCase):
    def test_disc_reaches_true_horizon(self):
        for h in (10, 50, 600):
            radius, why = core.disc_radius(h, 1e9)
            self.assertEqual(why, 'horizon')
            self.assertAlmostEqual(radius, core.horizon_m(h))
            true_dip = math.degrees(math.sqrt(2 * h / core.EARTH_R_M))
            self.assertAlmostEqual(core.horizon_dip_deg(h, radius), true_dip, delta=0.01)   # where a real horizon sits

    def test_disc_reaches_far_cameras_and_clip(self):
        near, far = core.disc_radius(50, 1e9)[0], core.disc_radius(50, 1e9, reach_m=2000)[0]
        self.assertAlmostEqual(far - near, 2000)   # a camera 2 km out still sees ground to its own horizon
        self.assertGreaterEqual(core.disc_radius(0, 1e9)[0], core.RADIUS_MIN_M)
        radius, why = core.disc_radius(2000, 10_000)
        self.assertEqual((radius, why), (core.CLIP_SHARE * 10_000, 'clip'))

    def test_dome_holds_every_camera(self):
        dome, clip = core.dome_radius(400, 900)
        self.assertGreaterEqual(dome, core.DOME_MARGIN * 900)
        self.assertGreater(clip, dome)

    def test_ground_clears_author_levels(self):
        self.assertEqual(core.ground_height(0, [5.0, 12.0]), 0.0)
        self.assertAlmostEqual(core.ground_height(0, [0.0, 10.0]), -core.GROUND_CLEAR_MIN_M)
        self.assertAlmostEqual(core.ground_height(0, [0.0, -0.05, 3.0]), -0.1)   # a stack of levels near it is cleared too
        self.assertAlmostEqual(core.ground_height(0, [0.0, 10.0], clear=0.8), -0.8)

    def test_chain_of_drops_checks_every_level(self):
        # a deck at 0, a fender 0.9 under it, a hull bottom 1.8 under: a 1 m drop chains past all three
        z = core.ground_height(0.0, [0.0, -0.9, -1.8, -40.0], 1.0)
        self.assertTrue(all(abs(z - level) >= 1.0 - 1e-9 for level in (0.0, -0.9, -1.8)), z)
        self.assertAlmostEqual(z, -2.8)

    def test_levels_seen_from_the_cameras(self):
        box = [[-10, -10, -0.5], [10, 10, 0.0]]
        far = core.level_reach([(0, 0, 50)], [box])
        self.assertAlmostEqual(far, math.dist((0, 0, 50), (10, 10, -0.5)))
        self.assertEqual(core.level_reach([(0, 0, 50)], []), 0.0)
        # a near deck seen from 50 m wants a smaller drop than the disc centre 2 km off would
        self.assertLess(core.ground_clear(0.1, far)[0], core.ground_clear(0.1, 2000)[0])

    def test_clear_follows_depth_precision(self):
        self.assertEqual(core.ground_clear(0.1, 20), (core.GROUND_CLEAR_MIN_M, False))   # close and a sane near clip
        drop, short = core.ground_clear(0.05, 300)
        self.assertGreater(drop, core.GROUND_CLEAR_MIN_M)
        self.assertFalse(short)
        self.assertEqual(core.ground_clear(0.0005, 2000), (core.GROUND_CLEAR_MAX_M, True))   # capped, and said

    def test_author_ground(self):
        self.assertTrue(core.is_author_ground((-1500, -1500, -10), (1500, 1500, 0)))
        self.assertFalse(core.is_author_ground((-44, -23, -5), (44, 23, 5)))         # a ship deck is not the ground
        self.assertFalse(core.is_author_ground((-500, -500, 0), (500, 500, 300)))    # a block, not flat
        self.assertFalse(core.is_author_ground((-150, -1, 0), (150, 1, 300)))        # a tall card: not wide both ways
        self.assertFalse(core.is_author_ground((-2000, -2000, 800), (2000, 2000, 805)))   # a cloud deck high up: not level
        self.assertTrue(core.is_author_ground((-2000, -2000, 798), (2000, 2000, 800), ground_z=800))

    def test_sky_runs_from_haze_to_zenith_without_bands(self):
        horizon, zenith = core.SKIES['day'][1], core.SKIES['day'][0]
        self.assertEqual(core.sky_colour('day', 'sea', -0.5), core.GROUNDS['sea'][0])   # below the horizon: more of the ground
        self.assertEqual(core.sky_colour('day', 'sea', core.SKY_STOPS[0]), horizon)
        stops = core.SKY_STOPS
        middle = core.sky_colour('day', 'sea', (stops[1] + stops[2]) / 2)   # half way between two stops: half a step's turn
        self.assertEqual(middle, tuple(round(v + (w - v) * 1.5 / (len(stops) - 1), 4) for v, w in zip(horizon, zenith)))
        self.assertEqual(core.sky_colour('day', 'sea', 0.9), zenith)
        self.assertEqual(core.sky_colour('day', 'none', -0.5), horizon)   # no ground: the horizon colour below it too
        self.assertIsNone(core.sky_colour('none', 'sea', 0.2))
        # rings as fine as the dome's: no step from one ring to the next reads as a band, under any sky
        heights = [math.cos(math.pi * (k + 0.5) / core.DOME_RINGS) for k in range(core.DOME_RINGS)]
        for sky in (k for k, v in core.SKIES.items() if v[0] is not None):
            rows = [core.sky_colour(sky, 'sea', z) for z in sorted(z for z in heights if z >= core.SKY_STOPS[0])]
            self.assertLessEqual(max(max(abs(a - b) for a, b in zip(r, q)) for r, q in zip(rows, rows[1:])), core.SKY_STEP_MAX, sky)


class WorldTest(unittest.TestCase):
    def test_shell_missing_warned(self):
        body = {**WORLD, 'shell': None, 'elements': [{'id': 'sea', 'function': 'context', 'description': 'the sea', 'why': 'where it lands',
                                                      'owner': {'shape': 'generative', 'look': 'generative'}, 'requirements': ['far_background']}]}
        body.pop('shell')
        _errors, warnings = world_mod.lint('.', body, None)
        self.assertTrue(any(w.startswith('SHELL_MISSING') for w in warnings), warnings)
        _errors, warnings = world_mod.lint('.', {**body, 'shell': {'ground': 'sea'}}, None)
        self.assertFalse(any(w.startswith('SHELL_MISSING') for w in warnings), warnings)

    def test_held_under_a_lit_look(self):
        lit = {'render': {'look_preset': 'photoreal_exterior'}}
        self.assertIn('ENV_SHELL_HELD', world_mod.shell_for(lit, WORLD)['held'])
        self.assertNotIn('held', world_mod.shell_for({'render': {'look_preset': 'flat_stylized'}}, WORLD))
        # the look the build resolves: the style's look when the shot names none, and the style's light rig
        self.assertIn('held', world_mod.shell_for({'render': {}}, WORLD, {'look': {'preset': 'photoreal_exterior'}}))
        self.assertNotIn('held', world_mod.shell_for({'render': {}}, WORLD, {'look': {'preset': 'flat_stylized'}}))

    def test_shell_painted_in_generated_shots(self):
        sea = {'id': 'ship', 'function': 'set', 'description': 'a ship', 'why': 'w', 'owner': {'shape': 'blender', 'look': 'generative'},
               'ids': ['ship']}
        world = {**WORLD, 'elements': [sea]}
        shell = world_mod.shell_for({'render': {}}, world)
        self.assertIn('@background', world_mod.shot_plan(world, ['ship'], shell)['generate_only'])
        self.assertNotIn('@background', world_mod.shot_plan(world, ['ship'], None)['generate_only'])
        blender_world = {**WORLD, 'elements': [{**sea, 'owner': {'shape': 'blender', 'look': 'blender'}}]}
        self.assertEqual(world_mod.shot_plan(blender_world, ['ship'], shell)['generate_only'], [])   # a blender shot keeps it

    def test_context_void_counts_the_shell(self):
        report = {'frames': [{'opaque_share': 0.3}]}
        self.assertTrue(world_mod.context_void(report, {'render': {}}, {**WORLD, 'shell': None}, 0.4))
        self.assertEqual(world_mod.context_void(report, {'render': {}}, {**WORLD, 'shell': {'ground': 'sea', 'sky': 'day'}}, 0.4), [])
        self.assertTrue(world_mod.context_void(report, {'render': {}}, {**WORLD, 'shell': {'ground': 'none'}}, 0.4))


CREW = {'id': 'crew', 'function': 'agent', 'description': 'a crew member', 'why': 'how big it is',
        'owner': {'shape': 'blender', 'look': 'generative'}, 'blockout': {'kind': 'cylinder', 'size_m': [0.5, 0.5, 1.75], 'scale_ref': True}}
SHIP = {'id': 'ship', 'function': 'set', 'description': 'the landing ship', 'why': 'where it lands',
        'owner': {'shape': 'blender', 'look': 'generative'}}


class StandInTest(unittest.TestCase):
    def test_blockout_size_missing(self):
        body = {**WORLD, 'elements': [SHIP, CREW]}
        _e, warnings = world_mod.lint('.', body, None)
        self.assertTrue(any(w.startswith('BLOCKOUT_SIZE_MISSING: ship') for w in warnings), warnings)
        sized = {**SHIP, 'blockout': {'kind': 'box', 'size_m': [88, 46, 10]}}
        _e, warnings = world_mod.lint('.', {**body, 'elements': [sized, CREW]}, None)
        self.assertFalse(any(w.startswith('BLOCKOUT_SIZE_MISSING') for w in warnings), warnings)

    def test_blockout_is_a_built_region(self):
        sized = {**SHIP, 'blockout': {'kind': 'box', 'size_m': [88, 46, 10]}}
        plan = world_mod.shot_plan({**WORLD, 'elements': [sized]}, ['ship'])
        self.assertEqual((plan['generate_only'], plan['unbuilt']), (['ship'], []))
        self.assertEqual(world_mod.shot_plan({**WORLD, 'elements': [SHIP]}, ['ship'])['unbuilt'], ['ship'])

    def test_scale_ref_lines(self):
        shot = {'shot_id': 's1', 'handover': {'kind': 'new_place'}}
        world = {**WORLD, 'elements': [CREW]}
        say = world_mod.scale_ref_lines(shot, {'summary': {'scale_refs': {}}}, world)
        self.assertTrue(say and 'crew' in say[0], say)
        self.assertEqual(world_mod.scale_ref_lines(shot, {'summary': {'scale_refs': {'crew': 60}}}, world), [])
        self.assertEqual(world_mod.scale_ref_lines({'shot_id': 's1'}, {'summary': {'scale_refs': {}}}, world), [])   # not a new place
        departed = {**shot, 'screen': {'pattern_departs': [{'item': 'scale_ref_missing', 'why': 'a cold open'}]}}
        self.assertEqual(world_mod.scale_ref_lines(departed, {'summary': {'scale_refs': {}}}, world), [])
        self.assertEqual(world_mod.scale_ref_lines(shot, {'summary': {}}, world), [])   # measured before references were read
        # a subject larger than every size a viewer knows, seen wide (2026-10-20, canal: a 970 m lock read as a toy)
        wide = lambda span, h: {'summary': {'scale_refs': {}, 'subject_span_m': span},  # noqa: E731
                                'frames': [{'shapes': {'subject': {'bbox': [0.1, 0.4, 0.9, 0.4 + h]}}} for _ in range(3)]}
        plain = {'shot_id': 's2'}
        self.assertTrue(world_mod.scale_ref_lines(plain, wide(970.0, 0.2), world))
        self.assertIn('970 m', world_mod.scale_ref_lines(plain, wide(970.0, 0.2), world)[0])
        self.assertEqual(world_mod.scale_ref_lines(plain, wide(970.0, 0.8), world), [])   # close on it: its parts give the size
        self.assertEqual(world_mod.scale_ref_lines(plain, wide(8.0, 0.2), world), [])     # a thing of a known size


class LayoutStandInTest(unittest.TestCase):
    def test_resolve_and_refuse(self):
        import json
        import tempfile
        from pathlib import Path
        from studio.common import StudioError
        from studio.layout import _stand_ins
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'project.json').write_text('{}')
            linked = {**CREW, 'id': 'pad', 'ids': ['pad_mesh']}
            (Path(root) / 'world.json').write_text(json.dumps({**WORLD, 'elements': [CREW, SHIP, linked]}))
            rows = _stand_ins(root, [{'element': 'crew', 'at': [0, 0, 0]}, {'element': 'crew', 'at': [2, 0, 0]}])
            self.assertEqual([r['id'] for r in rows], ['crew', 'crew.1'])
            self.assertEqual((rows[0]['shape'], rows[0]['size'], rows[0]['scale_ref']), ('cylinder', [0.5, 0.5, 1.75], True))
            with self.assertRaises(StudioError):
                _stand_ins(root, [{'element': 'ship', 'at': [0, 0, 0]}])   # no blockout
            with self.assertRaises(StudioError):
                _stand_ins(root, [{'element': 'nobody', 'at': [0, 0, 0]}])
            with self.assertRaises(StudioError):
                _stand_ins(root, [{'element': 'pad', 'at': [0, 0, 0]}])   # the author builds it (linked ids)
            # a cut that only shows the crew leaves it to the model in words (screen.described): no stand-in to copy
            self.assertEqual(_stand_ins(root, [{'element': 'crew', 'at': [0, 0, 0]}], ['crew']), [])


class UnderTest(unittest.TestCase):
    """2026-10-23, submarine r3: 100 m down, every camera saw the sea's disc from under and the dome's ground band - a
    horizon and a floor. A world films under its water when it says so (shell.camera: under)."""

    def test_a_shell_says_where_its_cameras_stand_only_when_declared(self):
        self.assertNotIn('camera', core.resolve({}, {'shell': {'ground': 'sea'}}))     # as before: nothing new in the job
        self.assertEqual(core.resolve({}, {'shell': {'ground': 'sea', 'camera': 'under'}})['camera'], 'under')
        over = core.resolve({'scene': {'shell': {'ground': 'sea', 'camera': 'over'}}}, {'shell': {'ground': 'sea', 'camera': 'under'}})
        self.assertEqual(over['camera'], 'over')                                            # a shot on the surface says so
        with self.assertRaises(core.ShellInvalid):
            core.resolve({}, {'shell': {'ground': 'land', 'camera': 'under'}})              # no one films from under a field
        with self.assertRaises(core.ShellInvalid):
            core.resolve({}, {'shell': {'ground': 'sea', 'camera': 'sideways'}})

    def test_the_water_meets_its_ceiling_without_a_line_and_darkens_below(self):
        row = core.UNDER['sea']
        self.assertEqual(core.under_colour('sea', 0.0), tuple(row['ceiling']))
        self.assertEqual(core.under_colour('sea', 0.2), tuple(row['ceiling']))             # over the surface: the ceiling
        self.assertEqual(core.under_colour('sea', -1.0), tuple(row['deep']))
        shades = [sum(core.under_colour('sea', h)) for h in (0.0, -0.02, -0.06, -0.15, -0.3, -0.5)]
        self.assertEqual(shades, sorted(shades, reverse=True))                              # darker all the way down
        self.assertLess(abs(sum(core.under_colour('sea', -0.001)) - sum(row['ceiling'])), 0.02)   # no step at the surface

    def test_a_camera_keeps_under_the_surface(self):
        shell = {'ground': 'sea', 'camera': 'under'}
        self.assertIsNone(core.out_of_space(shell, -100.0))
        self.assertTrue(core.out_of_space(shell, -0.2).startswith('CAMERA_OUT_OF_SPACE'))
        self.assertIsNone(core.out_of_space({'ground': 'sea'}, 50.0))                      # over: as before
        self.assertGreater(core.under_radius(100.0, 50.0), 100.0 * core.UNDER_SIGHT)


if __name__ == '__main__':
    unittest.main()
