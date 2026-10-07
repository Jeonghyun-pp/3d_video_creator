"""Pure parts of the shape kits: smooth sweep paths (path_core) and the subdivision cage lint (builder_params)."""
import math
import unittest

from studio.blender_ops.builder_params import cage_problems
from studio.blender_ops.path_core import catmull_rom, scale_at

CUBE = [[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)]
FACES = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]]


class PathCoreTest(unittest.TestCase):
    def test_passes_through_every_point_and_is_smooth(self):
        pts = [(0, 0, 0), (1, 0, 0), (1.5, 1, 0), (1.5, 2, 0.5), (0.5, 3, 0.5)]
        out = catmull_rom(pts, samples=10)
        self.assertEqual(len(out), 4 * 10 + 1)
        for k, p in enumerate(pts):
            self.assertEqual(out[k * 10], tuple(float(c) for c in p))
        turns = []   # direction change between consecutive samples stays small: no kinks at the given points
        for a, b, c in zip(out, out[1:], out[2:]):
            u = [y - x for x, y in zip(a, b)]; v = [y - x for x, y in zip(b, c)]
            cos = sum(x * y for x, y in zip(u, v)) / (math.hypot(*u) * math.hypot(*v))
            turns.append(math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
        self.assertLess(max(turns), 25.0)

    def test_closed_loop_and_straight_line(self):
        ring = catmull_rom([(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)], samples=8, closed=True)
        self.assertEqual(len(ring), 32)
        radii = [round(math.hypot(p[0], p[1]), 9) for p in ring]
        self.assertEqual(radii[:8] * 4, radii)   # the same arc between every pair: closed and symmetric, no seam
        self.assertTrue(all(0.85 < r <= 1.0 for r in radii))
        line = catmull_rom([(0, 0, 0), (1, 0, 0), (3, 0, 0)], samples=4)
        self.assertTrue(all(abs(p[1]) < 1e-12 and abs(p[2]) < 1e-12 for p in line))
        self.assertEqual([round(p[0], 9) for p in line], sorted(round(p[0], 9) for p in line))   # no overshoot back
        with self.assertRaises(ValueError):
            catmull_rom([(0, 0, 0), (0, 0, 0), (1, 0, 0)])

    def test_scale_keys(self):
        self.assertEqual(scale_at(0.8, 0.3), 0.8)
        keys = [[0, 1.0], [0.5, 2.0], [1, 0.5]]
        self.assertEqual([scale_at(keys, u) for u in (-1, 0, 0.25, 0.5, 0.75, 1, 2)], [1.0, 1.0, 1.5, 2.0, 1.25, 0.5, 0.5])


class CageLintTest(unittest.TestCase):
    def test_closed_open_and_flipped_cages(self):
        self.assertEqual(cage_problems(CUBE, FACES), [])
        self.assertIn('not closed', cage_problems(CUBE, FACES[:-1])[0])
        self.assertIn('orientation', cage_problems(CUBE, [FACES[0][::-1]] + FACES[1:])[0])
        self.assertIn('distinct', cage_problems(CUBE, FACES[:-1] + [[1, 5, 9]])[0])

    def test_spec_lint_checks_cages_wherever_they_are(self):
        from studio.common import read_json
        from studio.subjects import lint_spec
        from pathlib import Path
        spec = read_json(Path(__file__).resolve().parent / 'fixtures/winch_spec/subjects/winch/spec.json')
        cover = {'builder': 'subd', 'params': {'verts': CUBE, 'faces': FACES[:-1], 'levels': 2}}
        spec['builders'].append({'part_id': 'cover', 'builder': 'subd', 'params': {'verts': CUBE, 'faces': FACES}})
        spec['builders'].append({'part_id': 'caps', 'builder': 'array', 'params': {'count': 2, 'axis': 'z', 'center': [0, 0, 0], 'item': cover}})
        errors = [e for e in lint_spec(spec)['errors'] if 'subd cage' in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn('caps/params/item', errors[0])


if __name__ == '__main__':
    unittest.main()
