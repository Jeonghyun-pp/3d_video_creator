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

    def test_lint_catches_unknown_kit_args_targets_and_levels(self):
        self.shot['scene'] = {'kits': [{'id': 'city', 'kit': 'street', 'args': {'path': [[0, 0, 0], [0, 100, 0]], 'lanes': 9}}],
                              'levels': [{'level_id': 'L1', 'z': 0, 'rects': [[0, 0, 1, 1]]}]}
        self.shot['actions'] = [{'action_id': 'cut', 'type': 'reveal', 'targets': [{'instance_id': 'road', 'part_id': 'slab'}],
                                 'start_frame': 0, 'end_frame': 10, 'easing': 'linear', 'params': {}}]
        self.shot['fill_brief'] = {'levels': [{'level_id': 'L9', 'items': []}]}
        errors = ' | '.join(lint(self.project, self.shot)['errors'])
        self.assertIn("street takes no ['lanes']", errors)
        self.assertIn('targets road/slab', errors)
        self.assertIn("['L9'] are not declared", errors)


if __name__ == '__main__':
    unittest.main()
