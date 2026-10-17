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

    def test_default_sky_is_day(self):
        self.assertEqual(core.sky_for({}), 'day')
        self.assertEqual(core.sky_for({'time_light': {'mood': 'quiet night'}}), 'night')


class GeometryTest(unittest.TestCase):
    def test_disc_reaches_horizon(self):
        radius, why = core.disc_radius(50, 100_000)
        self.assertEqual(why, 'horizon')
        self.assertGreaterEqual(radius, core.HORIZON_K * 50)
        self.assertLessEqual(core.horizon_dip_deg(50, radius), 3.0)

    def test_disc_minimum_and_clip(self):
        self.assertEqual(core.disc_radius(1, 100_000)[0], core.RADIUS_MIN_M)
        radius, why = core.disc_radius(2000, 10_000)
        self.assertEqual((radius, why), (core.CLIP_SHARE * 10_000, 'clip'))

    def test_never_past_true_horizon(self):
        h = 500
        self.assertLessEqual(core.disc_radius(h, 1e9)[0], max(core.RADIUS_MIN_M, (2 * core.EARTH_R_M * h) ** 0.5) + 1e-6)

    def test_author_ground(self):
        self.assertTrue(core.is_author_ground((3000, 3000, 10)))
        self.assertFalse(core.is_author_ground((88, 46, 10)))      # a ship deck is not the ground
        self.assertFalse(core.is_author_ground((1000, 1000, 300)))  # a block, not flat

    def test_bands_lighter_at_horizon(self):
        colours = core.band_colours('day', 'sea')
        self.assertEqual(len(colours), len(core.SKY_RINGS) + 1)
        self.assertGreater(sum(colours[1]), sum(colours[3]))   # haze at the horizon, deeper blue overhead
        self.assertEqual([core.band_of(z) for z in (-0.5, 0.0, 0.2, 0.9)], [0, 1, 2, 3])
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
            (Path(root) / 'world.json').write_text(json.dumps({**WORLD, 'elements': [CREW, SHIP]}))
            rows = _stand_ins(root, [{'element': 'crew', 'at': [0, 0, 0]}, {'element': 'crew', 'at': [2, 0, 0]}])
            self.assertEqual([r['id'] for r in rows], ['crew', 'crew.1'])
            self.assertEqual((rows[0]['shape'], rows[0]['size'], rows[0]['scale_ref']), ('cylinder', [0.5, 0.5, 1.75], True))
            with self.assertRaises(StudioError):
                _stand_ins(root, [{'element': 'ship', 'at': [0, 0, 0]}])   # no blockout
            with self.assertRaises(StudioError):
                _stand_ins(root, [{'element': 'nobody', 'at': [0, 0, 0]}])


if __name__ == '__main__':
    unittest.main()
