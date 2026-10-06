"""Declarative scenes, host side (studio/layout.py): set merge, repeat/mirror expansion, yielding to the fill brief,
pinned and edited exemplars, and lint before Blender runs."""
from pathlib import Path
import tempfile
import unittest

from studio.common import read_json, write_json
from studio.layout import lint, resolve
from studio.project import init_project, shot_path


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('layout', {'request': 'layout', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        self.shot = read_json(shot_path(self.project, 's'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_merge_by_id_and_expansion(self):
        (self.project / 'sets').mkdir()
        write_json(self.project / 'sets/hall.json', {'materials': {'c': {'color': [0.5, 0.5, 0.5]}},
                                                    'primitives': [{'id': 'col', 'shape': 'box', 'size': [1, 1, 3], 'at': [2, 0, 0], 'material': 'c',
                                                                    'repeat': {'counts': [1, 3, 2], 'pitch_m': [0, 9, -7]}, 'mirror_x': True,
                                                                    'level_by_z': ['A', 'B'], 'yields_to_fill': 'column'},
                                                                   {'id': 'floor', 'shape': 'box', 'size': [10, 10, 1], 'at': [0, 0, -1], 'material': 'c'}]})
        self.shot['scene'] = {'use': 'sets/hall.json', 'primitives': [{'id': 'floor', 'shape': 'box', 'size': [20, 20, 1], 'at': [0, 0, -1], 'material': 'c'}]}
        self.shot['fill_brief'] = {'levels': [{'level_id': 'B', 'items': [{'role': 'subject', 'element': 'rebar_column'}]}]}
        result = resolve(self.project, self.shot)
        prims = {p['id']: p for p in result['scene']['primitives']}
        self.assertEqual(prims['floor']['size'], [20, 20, 1])                                       # the shot replaced the set's entry
        self.assertEqual(sorted(i for i in prims if i.startswith('col')),
                         ['col.0.0', 'col.0.0.m', 'col.1.0', 'col.1.0.m', 'col.2.0', 'col.2.0.m'])  # level B yields to the brief
        self.assertEqual((prims['col.2.0']['at'], prims['col.2.0.m']['at']), ([2, 18, 0], [-2, 18, 0]))
        self.assertEqual(result['yielded'], ['col@B'])
        reordered = dict(reversed(list(self.shot['scene'].items())))
        self.assertEqual(resolve(self.project, {**self.shot, 'scene': reordered})['layout_sha256'], result['layout_sha256'])

    def test_exemplars_are_pinned_and_edited_as_data(self):
        self.shot['scene'] = {'instances': [{'id': 'rc', 'exemplar': 'rebar_column', 'at': [0, 0, 0], 'edits': [{'op': 'drop_parts', 'parts': ['ties']}]}]}
        instance = resolve(self.project, self.shot)['scene']['instances'][0]
        self.assertEqual(instance['exemplar'], 'rebar_column@v001')
        self.assertNotIn('ties', [b['part_id'] for b in instance['spec']['builders']])
        self.assertTrue(any('pinned to their latest' in w for w in lint(self.project, self.shot)['warnings']))

    def test_nothing_unread_in_scene_data(self):
        from studio.layout import scene_unread
        scene = {'kits': [{'id': 'city', 'kit': 'street', 'args': {'path': [[0, 0], [10, 0]], 'overrides': {'tree_pitch_m': 9, 'tree_pich_m': 9}}}],
                 'section': {'id': 'sec', 'box': 'b', 'options': {'soil_m': 50, 'soill_m': 50}},
                 'materials': {'steel': {'catalog': 'galvanized_steel', 'catalog_overrides': {'base_color': [1, 0, 0], 'colour': [1, 0, 0]}},
                               'flat': {'color': [1, 1, 1], 'catalog_overrides': {'base_color': [1, 0, 0]}}}}
        found = scene_unread(scene)
        self.assertIn('scene/kits/city/args/overrides/tree_pich_m', found)
        self.assertIn('scene/section/options/soill_m', found)
        self.assertIn('scene/materials/steel/catalog_overrides/colour', found)
        self.assertTrue(any(f.startswith('scene/materials/flat/catalog_overrides') for f in found))
        self.assertFalse(any(f.endswith(('tree_pitch_m', '/soil_m', '/base_color')) for f in found), found)
        self.shot['scene'] = {'materials': {'c': {'color': [1, 1, 1]}},
                              'primitives': [{'id': 'p', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0], 'material': 'missing'}]}
        report = lint(self.project, self.shot)
        self.assertIn('primitive material missing is not defined in scene.materials (it would fall back to grey)', report['errors'])
        self.assertIn('scene.materials c is used by no primitive', report['warnings'])

    def test_exemplar_edits_reach_declared_values_only(self):
        from studio.common import StudioError
        base = {'id': 'rc', 'exemplar': 'rebar_column@v001', 'at': [0, 0, 0]}
        for edits, words in [([{'op': 'drop_parts', 'parts': ['no_such_part']}], 'drop_parts names parts it does not have'),
                             ([{'op': 'set', 'path': '/builders/0/nonsense', 'value': 1}], 'not a value this rc spec declares'),
                             ([{'op': 'set', 'path': '/builders/99/params', 'value': {}}], 'no item 99')]:
            self.shot['scene'] = {'instances': [{**base, 'edits': edits}]}
            with self.assertRaises(StudioError) as caught:
                resolve(self.project, self.shot)
            self.assertIn(words, caught.exception.message)

    def test_backdrop_view_and_prompt_follow_the_relation(self):
        from studio.generative.backdrop import compose_prompt
        relation = {'support': {'kind': 'bench'}, 'view': {'elevation_deg': 30}, 'light': {'key_side': 'right', 'key_kelvin': 3200, 'fill_kelvin': 6500}}
        self.shot['scene'] = {'backdrop': {'relation': relation}}
        self.shot['camera'] = {**self.shot['camera'], 'movement': 'rig', 'move': {'type': 'turntable', 'params': {'target': 'x', 'elevation_deg': 60}}}
        self.assertTrue(any('looks down 60' in w for w in lint(self.project, self.shot)['warnings']))
        prompt = compose_prompt(self.shot, 'a bakery kitchen with ovens', '9:16')
        self.assertIn('workbench height, looking down about 30 degrees', prompt)
        self.assertIn('brightest light from the right, about 3200 K; softer light from the left, about 6500 K', prompt)
        self.assertIn('a bakery kitchen with ovens.', prompt); self.assertIn('no text', prompt)

    def test_backdrop_image_warns_on_a_wide_orbit(self):
        (self.project / 'backdrops').mkdir(); (self.project / 'backdrops/b.png').write_bytes(b'x')
        self.shot['scene'] = {'backdrop': {'image': 'backdrops/b.png'}}
        self.shot['camera'] = {**self.shot['camera'], 'movement': 'rig', 'move': {'type': 'turntable', 'params': {'target': 'x', 'sweep_deg': 120}}}
        self.assertTrue(any('backdrop image is one view' in w for w in lint(self.project, self.shot)['warnings']))
        self.shot['camera']['move']['params']['sweep_deg'] = 20
        self.assertFalse(any('backdrop image is one view' in w for w in lint(self.project, self.shot)['warnings']))

    def test_backdrop_prompt_takes_the_place_from_the_approved_brief(self):
        from studio import decisions
        from studio.common import read_json as rj
        from studio.generative.backdrop import review
        decisions.propose(self.project, 'brief', {'topic': 'gears', 'audience': 'anyone', 'length_s': 10, 'key_message': 'they mesh',
                                                  'subject_mode': 'schematic', 'place': 'a quiet watchmaker workshop with brass tools'})
        decisions.approve(self.project, 'brief', '이 브리프로 승인', 'r01')
        self.shot['scene'] = {'backdrop': {'relation': {'view': {'elevation_deg': 20}}}}
        write_json(shot_path(self.project, 's'), self.shot)
        made = review(self.project, 'place', shot_id='s', count=1)
        prompt = rj(self.project / 'reviews' / f"backdrop_{made['review_id']}" / 'review.json')['request']['prompt']
        self.assertIn('a quiet watchmaker workshop with brass tools.', prompt)
        self.assertIn('looking down about 20 degrees', prompt)

    def test_lint_catches_unknown_kit_args_targets_and_levels(self):
        self.shot['scene'] = {'kits': [{'id': 'city', 'kit': 'street', 'args': {'path': [[0, 0, 0], [0, 100, 0]], 'lanes': 9}}],
                              'levels': [{'level_id': 'L1', 'z': 0, 'rects': [[0, 0, 1, 1]]}]}
        self.shot['actions'] = [{'action_id': 'cut', 'type': 'reveal', 'targets': [{'instance_id': 'road', 'part_id': 'slab'}],
                                 'start_frame': 0, 'end_frame': 10, 'easing': 'linear', 'params': {}}]
        self.shot['fill_brief'] = {'levels': [{'level_id': 'L9', 'items': []}]}
        errors = ' | '.join(lint(self.project, self.shot)['errors'])
        self.assertIn('nothing reads scene/kits/city/args/lanes', errors)
        self.assertIn('targets road/slab', errors)
        self.assertIn("['L9'] are not declared", errors)


class LayoutRevisionTest(unittest.TestCase):
    def test_a_scene_change_builds_fresh_from_data(self):
        from unittest import mock
        from studio import blender
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(init_project('rev', {'request': 'rev', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, tmp)['project_path'])
            shot = read_json(shot_path(project, 's'))
            change = Path(tmp) / 'change.json'
            scene = {'primitives': [{'id': 'b', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0], 'material': 'c'}]}
            write_json(change, {'base_revision': shot['revision'], 'scope': 'scene', 'targets': [], 'change': {'scene': scene}, 'preserve': []})
            with mock.patch.object(blender, 'build_shot', return_value={'scene_version': 'v0001'}) as build:
                blender.revise_shot(project, 's', change)
            args = build.call_args.args
            self.assertEqual((args[2], args[3], args[4]['scene']), (None, None, scene))   # no script, no base, the new scene


if __name__ == '__main__':
    unittest.main()
