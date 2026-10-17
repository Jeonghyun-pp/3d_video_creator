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

    def test_unknown_kind_refused(self):
        with self.assertRaises(ValueError):
            core.resolve({'scene': {'shell': {'ground': 'lava'}}}, WORLD)

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

    def test_bands_lighter_at_horizon(self):
        colours = core.band_colours('day', 'sea')
        self.assertEqual(len(colours), len(core.SKY_RINGS) + 1)
        self.assertGreater(sum(colours[1]), sum(colours[-1]))   # haze at the horizon, deeper blue overhead
        self.assertEqual(colours[0], core.GROUNDS['sea'][0])     # below the horizon: more of the ground
        self.assertEqual([core.band_of(z) for z in (-0.5, 0.0, 0.2, 0.9)], [0, 1, 4, 6])
        self.assertIsNone(core.band_colours('none', 'sea'))


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


if __name__ == '__main__':
    unittest.main()
