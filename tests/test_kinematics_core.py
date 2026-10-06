"""Mechanisms in pure Python: involute gears, a planetary layout whose teeth never overlap while it turns, and the
coupling solver (signs, ratios, refusals)."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
import gear_core as gears  # noqa: E402
import kinematics_core as kin  # noqa: E402


def inside(point, polygon):
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def placed(outline, centre, angle, shrink=0.995):
    c, s = math.cos(angle), math.sin(angle)
    return [(centre[0] + shrink * (x * c - y * s), centre[1] + shrink * (x * s + y * c)) for x, y in outline]


def overlap(a, b):
    return any(inside(p, b) for p in a) or any(inside(p, a) for p in b)


class GearTest(unittest.TestCase):
    def test_outline_is_a_gear(self):
        m, z = 0.002, 20
        outline = gears.gear_outline(m, z)
        radii = [math.hypot(x, y) for x, y in outline]
        self.assertAlmostEqual(max(radii), m * z / 2 + m, places=6)
        self.assertAlmostEqual(min(radii), m * z / 2 - 1.25 * m, places=6)
        with self.assertRaises(ValueError):
            gears.gear_outline(m, 6)

    def test_planetary_teeth_never_overlap_while_turning(self):
        m, zs, zp, zr, n = 0.002, 18, 27, 72, 3
        layout = gears.planetary_layout(m, zs, zp, zr, n)
        self.assertAlmostEqual(layout['ratio'], 0.2)
        sun, planet = gears.gear_outline(m, zs), gears.gear_outline(m, zp)
        ring = [gears.internal_tooth(m, zr)]
        coupling = {'id': 'pg', 'kind': 'planetary', 'sun': 's', 'carrier': 'c', 'planets': ['p0', 'p1', 'p2'],
                    'teeth': {'sun': zs, 'planet': zp, 'ring': zr}}
        for sun_deg in (0.0, 3.7, 11.0, 40.0):
            v = kin.solve([coupling], {'s': sun_deg})
            carrier = math.radians(v['c'])
            s_poly = placed(sun, (0, 0), math.radians(sun_deg))
            for i, p in enumerate(layout['planets']):
                theta = p['theta'] + carrier
                centre = (layout['centre_distance'] * math.cos(theta), layout['centre_distance'] * math.sin(theta))
                p_poly = placed(planet, centre, p['phase'] + carrier + math.radians(v[f'p{i}']))
                self.assertFalse(overlap(s_poly, p_poly), f'sun x planet {i} at sun {sun_deg} deg')
                for k in range(zr):
                    tooth = placed(ring[0], (0, 0), layout['ring_phase'] + 2 * math.pi * k / zr, shrink=1.0)
                    if math.dist(centre, (sum(x for x, _ in tooth) / len(tooth), sum(y for _, y in tooth) / len(tooth))) < m * (zp / 2 + 3):
                        self.assertFalse(overlap(p_poly, tooth), f'planet {i} x ring tooth {k} at sun {sun_deg} deg')

    def test_layout_refuses_impossible_sets(self):
        with self.assertRaises(ValueError):
            gears.planetary_layout(0.002, 18, 27, 70, 3)      # ring != sun + 2 planets
        with self.assertRaises(ValueError):
            gears.planetary_layout(0.002, 19, 27, 73, 3)      # (sun + ring) not divisible by 3
        with self.assertRaises(ValueError):
            gears.planetary_layout(0.002, 20, 13, 46, 3)      # an undercut planet collides with the ring


class CouplingTest(unittest.TestCase):
    def test_signs_and_ratios(self):
        couplings = [{'id': 'a', 'kind': 'gear', 'driver': 'm', 'driven': 'g', 'teeth': [20, 60]},
                     {'id': 'b', 'kind': 'belt', 'driver': 'g', 'driven': 'p', 'ratio': 0.5},
                     {'id': 'c', 'kind': 'rack', 'driver': 'p', 'driven': 'slide', 'radius_m': 0.05}]
        v = kin.solve(couplings, {'m': 90.0})
        self.assertAlmostEqual(v['g'], -30.0)
        self.assertAlmostEqual(v['p'], -15.0)
        self.assertAlmostEqual(v['slide'], math.radians(-15.0) * 0.05)

    def test_refusals(self):
        joints = [{'id': i} for i in ('a', 'b', 'c')]
        self.assertTrue(any('unknown kind' in p for p in kin.check(joints, [{'id': 'x', 'kind': 'cam', 'driver': 'a', 'driven': 'b'}])))
        twice = [{'id': 'x', 'kind': 'belt', 'driver': 'a', 'driven': 'c', 'ratio': 1}, {'id': 'y', 'kind': 'belt', 'driver': 'b', 'driven': 'c', 'ratio': 1}]
        self.assertTrue(any('driven by both' in p for p in kin.check(joints, twice)))
        cycle = [{'id': 'x', 'kind': 'belt', 'driver': 'a', 'driven': 'b', 'ratio': 1}, {'id': 'y', 'kind': 'belt', 'driver': 'b', 'driven': 'a', 'ratio': 1}]
        self.assertTrue(any('cycle' in p for p in kin.check(joints, cycle)))

    def test_drive_values(self):
        self.assertAlmostEqual(kin.drive_value({'rpm': 60}, 0.5, 2.0), 360.0)
        keyed = {'keys': [{'t': 0, 'value': 0}, {'t': 1, 'value': 720}]}
        self.assertAlmostEqual(kin.drive_value(keyed, 0.25, 4.0), 180.0)


class MechanismSpecTest(unittest.TestCase):
    def test_planetary_spec_lints_and_carries_the_layout(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from studio.mechanisms import planetary_spec
        from studio.subjects import lint_spec
        from studio.common import StudioError
        spec = planetary_spec('reducer', 0.002, 18, 27, 72, 3)
        self.assertEqual(lint_spec(spec)['errors'], [])
        self.assertEqual(kin.check(spec['joints'], spec['couplings']), [])
        layout = gears.planetary_layout(0.002, 18, 27, 72, 3)
        ring = next(b for b in spec['builders'] if b['part_id'] == 'ring_teeth')
        self.assertAlmostEqual(math.radians(ring['params']['start_deg']), layout['ring_phase'], places=5)
        planet = next(j for j in spec['joints'] if j['id'] == 'j_planet_2')
        self.assertEqual(planet['parent'], 'carrier')
        with self.assertRaises(StudioError):
            planetary_spec('bad', 0.002, 18, 27, 70, 3)


if __name__ == '__main__':
    unittest.main()
