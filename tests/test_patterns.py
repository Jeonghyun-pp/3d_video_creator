"""Pattern library strength: an item supported across sample groups and stable over halves is strong; one carried by a
single group, or one that moves when a group is left out, is weak with the reason; the shareable file has no per-group values."""
import random
import unittest

from studio import patterns


def rows(per_group, value_of):
    out = []
    for a in range(7):
        for reel in range(6):
            for shot in range(per_group):
                out.append({'group': f'g{a}', 'reel': f'g{a}c{reel}', 'x': value_of(a, reel, shot)})
    return out


class StrengthTest(unittest.TestCase):
    def test_shared_and_stable_is_strong(self):
        rng = random.Random(1)
        item = patterns.numeric_item(rows(5, lambda a, r, s: 0.7 + rng.uniform(-0.2, 0.2)), 'x', random.Random(2))
        self.assertEqual(item['strength'], 'strong')
        self.assertAlmostEqual(item['median'], 0.7, delta=0.05)

    def test_one_group_carrying_it_is_weak(self):
        data = [r for r in rows(5, lambda a, r, s: 1.0 + 0.01 * s) if r['group'] in ('g0', 'g1')]
        item = patterns.numeric_item(data, 'x', random.Random(2))
        self.assertEqual(item['strength'], 'weak')
        self.assertIn('support in 2 groups', item['weak_because'])

    def test_a_group_far_from_the_rest_moves_it(self):
        rng = random.Random(3)
        data = rows(5, lambda a, r, s: (5.0 if a < 3 else 0.5) + rng.uniform(-0.05, 0.05))
        item = patterns.numeric_item(data, 'x', random.Random(2))
        self.assertIn('one group moves it', item.get('weak_because', []))

    def test_categorical_shares(self):
        data = rows(5, lambda a, r, s: 'push' if s < 2 else 'static')
        item = patterns.categorical_item(data, 'x', random.Random(2))
        self.assertAlmostEqual(item['values']['static']['share'], 0.6)
        self.assertEqual(item['values']['static']['strength'], 'strong')

    def test_shareable_drops_per_group_values(self):
        data = rows(5, lambda a, r, s: 'push' if s < 2 else 'static')
        items = {'x': patterns.categorical_item(data, 'x', random.Random(2)),
                 'y': patterns.numeric_item(rows(5, lambda a, r, s: 0.5 + 0.01 * s), 'x', random.Random(2))}
        text = str(patterns.shareable(items))
        self.assertNotIn('per_group', text)
        self.assertNotIn('g0', text)


if __name__ == '__main__':
    unittest.main()
