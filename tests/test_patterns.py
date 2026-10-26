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

    def test_measure_shot_reads_the_declared_focus_not_the_whole_set(self):
        whole = {'bbox': [0.0, 0.0, 1.0, 1.0]}
        report = {'frames': [{'frame': 0, 'shapes': {'subject': whole, 'key:child/feet': {'bbox': [0.4, 0.55, 0.56, 0.66]}}},
                             {'frame': 59, 'shapes': {'subject': whole, 'key:child/feet': {'bbox': [0.42, 0.45, 0.58, 0.56]}}}]}
        got, about = patterns.measure_shot(report, {'screen': {'subject': ['child/feet']}, 'duration_frames': 60}, 30)
        self.assertEqual((about['source'], about['focus']), ('screen.subject', ['child/feet']))
        self.assertAlmostEqual(got['height_share'], 0.11, places=3)
        self.assertAlmostEqual(got['subject_travel'], 0.102, places=3)   # the box centre moved up and right
        self.assertEqual(got['len_s'], 2.0)
        whole_set, about = patterns.measure_shot(report, {'duration_frames': 60}, 30)
        self.assertEqual(about['source'], 'subject_class')
        self.assertIn('FOCUS_UNDECLARED', about['note'])

    def test_focus_order(self):
        self.assertEqual(patterns.focus_of({'key_parts': [{'id': 'a'}, {'id': 'b', 'source': 'rig'}]}), (['a'], 'key_parts'))
        self.assertEqual(patterns.focus_of({'screen': {'targets': [{'of': 'subject'}, {'of': 'gear'}]}}), (['gear'], 'screen.targets'))
        self.assertEqual(patterns.focus_of({}), ([], 'subject_class'))
        framed = {'frame': {'focus': ['foup', 'fab_worker']}, 'key_parts': [{'id': 'a'}]}   # the shot list's focus (2026-10-26)
        self.assertEqual(patterns.focus_of(framed), (['foup', 'fab_worker'], 'frame.focus'))
        self.assertEqual(patterns.focus_of({**framed, 'screen': {'subject': ['b']}}), (['b'], 'screen.subject'))

    def test_a_share_of_the_reel_is_never_compared(self):
        items = {'len_share': numeric(0.01, 0.013, 0.026, 0.045, 0.066)}
        self.assertEqual(patterns.compare(items, {'len_share': 0.2}), [])   # 5 shots vs 50: not comparable


class FramingTest(unittest.TestCase):
    """Too close, on the settled frames of the focus: cut at one edge, filling the frame, a part off screen."""
    def report(self, boxes, px=None):
        frames = []
        for n, box in enumerate(boxes):
            frames.append({'frame': n * 10, 'key_px': px[n] if px else {'a': 100, 'b': 100},
                           'shapes': {f'key:{k}': {'bbox': b} for k, b in box.items()}})
        return {'frames': frames}

    def judge(self, boxes, px=None):
        return patterns.framing(self.report(boxes, px), ['a', 'b'], 100)['found']

    def test_only_the_settled_frames_count(self):
        wide = {'a': [0.1, 0.3, 0.9, 0.5], 'b': [0.1, 0.5, 0.9, 0.7]}
        cut = {'a': [0.1, 0.3, 0.9, 0.5], 'b': [0.1, 0.5, 0.9, 1.0]}
        self.assertEqual(self.judge([cut] * 6 + [wide] * 5), {})              # cut while the camera moved: fine
        self.assertEqual(self.judge([wide] * 6 + [cut] * 5)['cut']['what'], ['bottom'])

    def test_a_band_across_the_frame_is_fine_and_four_edges_is_too_close(self):
        band = {'a': [0.0, 0.3, 1.0, 0.5], 'b': [0.0, 0.5, 1.0, 0.8]}         # touches left and right: runs across
        self.assertEqual(self.judge([band] * 11), {})
        full = {'a': [0.0, 0.0, 1.0, 0.5], 'b': [0.0, 0.5, 1.0, 1.0]}
        self.assertIn('fills_frame', self.judge([full] * 11))

    def test_a_part_off_screen_and_an_unmeasured_part(self):
        box = {'a': [0.1, 0.3, 0.9, 0.5]}
        found = self.judge([box] * 11, [{'a': 100, 'b': 0}] * 11)
        self.assertEqual(found['part_out']['what'], ['b'])
        unmeasured = patterns.framing(self.report([box] * 11, [{'a': 100}] * 11), ['a', 'b'], 100)
        self.assertEqual((unmeasured['found'], unmeasured['unmeasured']), ({}, ['b']))   # not measured is not "off screen"

    def test_focus_is_detail_note(self):
        shot = {'duration_frames': 30, 'screen': {'subject': ['f/eps'], 'details': [{'id': 'f/eps', 'min_px': 24}]}}
        _, about = patterns.measure_shot({'frames': []}, shot, 30)
        self.assertTrue(about['note'].startswith('FOCUS_IS_DETAIL'))
        shot['screen']['subject'] = ['f/eps', 'f/slab']
        self.assertNotIn('note', patterns.measure_shot({'frames': []}, shot, 30)[1])


class ScaleFlowTest(unittest.TestCase):
    USUAL = {'closer': {'share': 0.37}, 'same': {'share': 0.35}, 'wider': {'share': 0.28}}

    def test_three_shots_at_one_size_is_flat_and_a_breath_is_not(self):
        flat = patterns.scale_flow([0.6, 0.62, 0.61, 0.9], self.USUAL)
        self.assertEqual(flat['steps'], ['same', 'same', 'closer'])
        self.assertTrue(flat['warnings'][0].startswith('SCALE_FLOW_FLAT'))
        varied = patterns.scale_flow([0.3, 0.6, 0.6, 0.95, 0.5], self.USUAL)
        self.assertEqual(varied['warnings'], [])
        self.assertEqual(varied['mix'], {'closer': 0.5, 'same': 0.25, 'wider': 0.25})

    def test_unmeasured_shots_break_no_run_and_a_same_heavy_library_is_not_flagged(self):
        self.assertEqual(patterns.scale_flow([0.6, None, 0.6, 0.6], self.USUAL)['warnings'], [])
        self.assertEqual(patterns.scale_flow([0.6, 0.6, 0.6], {'same': {'share': 0.7}})['warnings'], [])

    def test_study_rows_read_the_step_from_the_shot_before(self):
        label = lambda h: {'composition': {'A': {'center_x': 0.5, 'center_y': 0.5, 'height_share': h, 'width_share': 0.5, 'area_share': h / 2,
                                                  'edge_margin': 0.1, 'thirds_distance': 0.2}}, 'anchor': {}, 'role': 'explain',
                           'view_height': 'eye', 'change_kind': 'none', 'reveal_method': 'none', 'reveal_panel': 'A',
                           'scale_anchor': 'none', 'depth_layers': 2, 'subject_count': 1, 'graphics': [],
                           'subtitle': {'present': False}, 'void': {}}
        shot = lambda i, h: {'shot': i, 'len_s': 1.0, 'len_share': 0.1, 'position': i / 10, 'camera': {'axis': 'still'},
                             'visual_change_rate': 0.0, 'labels': [label(h)] if h else [], 'camera_move': {'move': 'static'}}
        grammar = {'canvas': {'layout': 'full'}, 'shots': [shot(0, 0.3), shot(1, 0.6), shot(2, None), shot(3, 0.5)]}
        rows = patterns.shot_rows(grammar, 'g', 'r')
        self.assertEqual([r.get('scale_step') for r in rows], [None, 'closer', None, None])   # no step across an unread shot


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



def grammar(lengths, sha):
    t, shots = 0.0, []
    for n, l in enumerate(lengths):
        shots.append({'shot': n, 'len_s': l, 'len_share': l / sum(lengths), 'position': t / sum(lengths),
                      'camera': {'axis': 'still', 'rate_per_s': {'x': 0.0, 'y': 0.0, 'scale': 0.0}}, 'visual_change_rate': 0.0,
                      'labels': [], 'camera_envelope': {}})
        t += l
    return {'video_sha256': sha, 'labeller': {'runs': []}, 'media': {'width': 1080, 'height': 1920, 'duration': sum(lengths)},
            'canvas': {'layout': 'full'}, 'shots': shots}


class ReelLevelTest(unittest.TestCase):
    """A style library groups by reel (one group a reel): a reel gives one first cut, one length, one pace - support
    counts a group of fewer reels than MIN_PER_GROUP by a value from each of its reels (2026-10-26, cut length plan R0b)."""

    def test_one_value_a_reel_is_supported_when_every_group_is_one_reel(self):
        rng = random.Random(4)
        data = [{'group': f'r{n}', 'reel': f'r{n}', 'x': 6.0 + rng.uniform(-0.3, 0.3)} for n in range(40)]
        item = patterns.numeric_item(data, 'x', random.Random(2))
        self.assertEqual(item['strength'], 'strong', item.get('weak_because'))
        self.assertEqual(item['groups'], 40)

    def test_a_role_seen_in_one_reel_of_a_group_of_many_still_needs_three(self):
        rng = random.Random(5)
        everything = rows(5, lambda a, r, s: 0.7)
        reels = patterns.reels_by_group(everything)
        part = [{'group': f'g{a}', 'reel': f'g{a}c0', 'x': 0.7 + rng.uniform(-0.1, 0.1)} for a in range(7)]
        item = patterns.numeric_item(part, 'x', random.Random(2), reels)
        self.assertEqual(item['strength'], 'weak')
        self.assertIn('support in 0 groups', item['weak_because'])

    def test_build_writes_the_reels_length_cuts_and_pace(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            measured, manifest = Path(tmp) / 'measured', Path(tmp) / 'manifest.json'
            entries = []
            for n in range(6):
                lengths = [2.0 + 0.1 * n, 4.0, 1.5, 3.0 + 0.1 * n]
                folder = measured / f'c{n}'; folder.mkdir(parents=True)
                (folder / 'grammar.json').write_text(json.dumps(grammar(lengths, f'sha{n}')))
                text = '가' * int(9.5 * sum(lengths))
                (folder / 'speech.json').write_text(json.dumps({'text': text, 'segments': [
                    {'words': [{'start': 0.0, 'end': sum(lengths), 'word': text}]}]}))
                entries.append({'reel': f'c{n}', 'group': f'c{n}'})
            manifest.write_text(json.dumps(entries))
            with mock.patch.object(patterns, 'LIBRARY', Path(tmp) / 'lib'), mock.patch.object(patterns, 'LOCAL', Path(tmp) / 'local'):
                patterns.build(measured, manifest, 'style', out=Path(tmp) / 'out')
                library = json.loads((Path(tmp) / 'lib' / 'style.json').read_text())
        reel = library['reel']
        self.assertEqual(reel['reels'], 6)
        self.assertEqual(reel['items']['cuts']['median'], 4)
        self.assertAlmostEqual(reel['items']['chars_per_s']['median'], 9.5, delta=0.2)
        self.assertAlmostEqual(reel['items']['mean_len_s']['median'], reel['items']['duration_s']['median'] / 4, delta=0.05)
        self.assertEqual(reel['items']['duration_s']['groups'], 6)   # one reel a group supports with its one value
        self.assertNotIn('per_group', json.dumps(reel))
        self.assertNotIn('reel', library['sections'])   # a reel's numbers are no role
        held = reel['items']['holds_long']   # each reel holds an eighth or more of its cuts at the set's long cut (4.0 s here)
        self.assertEqual(held['at_s'], library['sections']['all']['items']['len_s']['p75'])
        self.assertEqual(held['values']['True']['share'], 1.0)
        self.assertIn('median_under_long', reel['items'])


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
