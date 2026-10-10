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



def numeric(p10, p25, med, p75, p90, strength='strong'):
    return {'kind': 'numeric', 'p10': p10, 'p25': p25, 'median': med, 'p75': p75, 'p90': p90, 'n': 100, 'strength': strength}


class CompareTest(unittest.TestCase):
    ITEMS = {'height_share': numeric(0.35, 0.5, 0.7, 0.9, 1.0), 'center_y': numeric(0.4, 0.5, 0.52, 0.6, 0.65, 'weak'),
             'camera_axis': {'kind': 'categorical', 'n': 100, 'values': {'still': {'share': 0.4, 'strength': 'strong'},
                                                                         'x': {'share': 0.05, 'strength': 'weak'}}}}

    def test_positions_and_what_counts_as_off(self):
        rows = {r['item']: r for r in patterns.compare(self.ITEMS, {'height_share': 0.1, 'center_y': 0.95, 'camera_axis': 'still'})}
        self.assertEqual((rows['height_share']['position'], rows['height_share']['off']), ('outside', True))
        self.assertEqual((rows['center_y']['position'], rows['center_y']['off']), ('outside', False))   # weak: shown, not off
        self.assertEqual((rows['camera_axis']['position'], rows['camera_axis']['off']), ('common', False))
        edge = patterns.compare(self.ITEMS, {'height_share': 0.4})[0]
        self.assertEqual((edge['position'], edge['off']), ('edge', False))

    def test_a_declared_departure_is_not_off(self):
        row = patterns.compare(self.ITEMS, {'height_share': 0.1}, [{'item': 'height_share', 'why': 'a wide establishing view on purpose'}])[0]
        self.assertFalse(row['off'])
        self.assertIn('wide', row['declared_depart'])
        self.assertTrue(patterns.off_lines({'section': 'explain', 'library': 'l', 'rows': patterns.compare(self.ITEMS, {'height_share': 0.1})})[0]
                        .startswith('SCREEN_PATTERN_OFF'))

    def test_measure_shot_uses_the_screen_metrics_on_the_subject_box(self):
        report = {'frames': [{'shapes': {'subject': {'bbox': [0.2, 0.3, 0.6, 0.9]}}}, {'shapes': {'subject': {'px': 0}}}]}
        got = patterns.measure_shot(report, 60, 30)
        self.assertEqual((got['height_share'], got['center_y'], got['len_s']), (0.6, 0.6, 2.0))
        self.assertEqual(patterns.measure_shot({}, 30, None), {})

    def test_a_share_of_the_reel_is_never_compared(self):
        items = {'len_share': numeric(0.01, 0.013, 0.026, 0.045, 0.066)}
        self.assertEqual(patterns.compare(items, {'len_share': 0.2}), [])   # 5 shots vs 50: not comparable


class RolesTest(unittest.TestCase):
    def test_one_vocabulary_with_older_names_mapped(self):
        self.assertEqual(patterns.role_of('detail'), 'explain')
        self.assertEqual(patterns.role_of('outro'), 'close')
        self.assertIsNone(patterns.role_of('transition'))
        self.assertEqual(patterns.role_of('reveal'), 'reveal')

    def test_schemas_carry_the_same_roles(self):
        from studio.common import REPO, read_json
        from studio import screen_grammar
        decision = read_json(REPO / 'schemas/studio-v1/decision.schema.json')['$defs']['shotlist']['properties']['shots']['items']['properties']['role']['enum']
        shot = read_json(REPO / 'schemas/studio-v1/shot.schema.json')['properties']['screen']['properties']['role']['enum']
        self.assertEqual(set(decision), {*patterns.ROLES, *patterns.LEGACY_ROLES})
        self.assertEqual(tuple(shot), patterns.ROLES)
        self.assertIs(screen_grammar.ROLES, patterns.ROLES)

    def test_show_reads_a_section_and_says_when_there_are_no_pictures(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        library = {'sections': {'all': {'shots': 10, 'items': {}},
                                'explain': {'shots': 5, 'groups': 4, 'items': {'height_share': numeric(0.35, 0.5, 0.7, 0.9, 1.0),
                                                                               'center_y': {**numeric(0.4, 0.5, 0.52, 0.6, 0.65, 'weak'), 'weak_because': ['halves disagree']}}}}}
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / 'lib'; lib.mkdir(); (lib / 'only.json').write_text(__import__('json').dumps(library))
            with mock.patch.object(patterns, 'LIBRARY', lib), mock.patch.object(patterns, 'LOCAL', Path(tmp) / 'local'):
                shown = patterns.show('detail')
                self.assertEqual((shown['library'], shown['section']), ('only', 'explain'))
                self.assertIn('height_share', shown['strong'])
                self.assertEqual(shown['weak']['center_y']['weak_because'], ['halves disagree'])
                self.assertIn('numbers only', shown['note'])
                self.assertEqual(patterns.show('transition')['section'], 'all')
                (lib / 'second.json').write_text('{}')
                with self.assertRaises(patterns.StudioError):
                    patterns.show('explain')   # two libraries and no choice: ask



class TasteTest(unittest.TestCase):
    def test_taste_is_written_in_pattern_items_and_marked_beside_the_pattern(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(patterns, 'TASTE', Path(tmp) / 'taste.json'):
            with self.assertRaises(patterns.StudioError):
                patterns.taste_add('hook', 'jump_close', 'higher', 'the jump itself, close', '점프 장면이 역동적이면 좋겠어')   # not an item
            with self.assertRaises(patterns.StudioError):
                patterns.taste_add('hook', 'height_share', 'higher', 'the event close', 'ok')   # not the user's words
            patterns.taste_add('hook', 'height_share', 'higher', 'the key event shown close', '점프하는 장면의 역동성')
            patterns.taste_add('hook,consequence', 'change_kind', 'object_motion', 'the event moves on screen', '점프하는 장면의 역동성')
            items = {'height_share': numeric(0.35, 0.5, 0.63, 0.83, 0.95)}
            hook = patterns.taste_for('hook', items)
            self.assertEqual([t['id'] for t in hook], ['t01', 't02'])
            self.assertEqual(hook[0]['aim'], [0.63, 0.95])
            self.assertEqual(patterns.taste_for('explain', items), [])
            rows = {r['item']: r for r in patterns.compare(items, {'height_share': 0.5}, taste=hook)}
            self.assertEqual(rows['height_share']['taste'][0]['met'], False)
            self.assertFalse(rows['height_share']['off'])   # taste never makes a warning
            patterns.taste_remove('t01')
            self.assertEqual([e['id'] for e in patterns.taste_entries()], ['t02'])


if __name__ == '__main__':
    unittest.main()
