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


if __name__ == '__main__':
    unittest.main()
