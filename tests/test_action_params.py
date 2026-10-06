"""The action params table (studio/blender_ops/action_params.py) agrees with the shot schema type by type, and shot
validation refuses params nothing reads. That the Blender readers read exactly these rows is checked by
tests/studio/action_params_smoke.py."""
import unittest

from studio.blender_ops import action_params as ap
from studio.common import StudioError
from studio.shot_edit import _expand, schema


def schema_keys(kind, sub=()):
    root = schema('shot')
    item = root['properties']['actions']['items']
    nodes = [b for b in _expand(root, item, {'type': kind, 'params': {}}) if 'params' in b.get('properties', {})]
    keys = set()
    for node in nodes:
        params = node['properties']['params']
        for key in sub:
            params = params.get('properties', {}).get(key, {}).get('items', {})
        keys |= set(params.get('properties', {}))
    return keys


class ActionParamsTest(unittest.TestCase):
    def test_rows_equal_the_schema_by_type(self):
        self.assertEqual(set(ap.ACTION_PARAMS), set(schema('shot')['properties']['actions']['items']['properties']['type']['enum']))
        for kind in ap.ACTION_PARAMS:
            row = set(ap.reads({'type': kind, 'params': {}}))
            self.assertEqual(row, schema_keys(kind), kind)
        for (kind, *sub), row in ap.ITEMS.items():
            self.assertEqual(set(row), schema_keys(kind, sub), (kind, sub))

    def test_simulate_kind_narrows_the_row(self):
        self.assertIn('drift_mps', ap.reads({'type': 'simulate', 'params': {'kind': 'dust'}}))
        self.assertEqual(ap.unread({'type': 'simulate', 'params': {'kind': 'dust', 'size_range': [0.1, 0.2]}}), ['params/size_range'])
        self.assertEqual(ap.unread({'type': 'drive', 'params': {'drives': [{'joint': 'j', 'rpm': 1, 'rmp': 2}]}}), ['params/drives/0/rmp'])

    def test_validation_refuses_unread_params(self):
        from studio.project import default_shot, validate_shot
        shot = default_shot('s', 30, {'request': 'x'})
        shot['actions'] = [{'action_id': 'd', 'type': 'simulate', 'targets': [{'instance_id': 'a', 'part_id': 'b'}], 'start_frame': 0,
                            'end_frame': 10, 'easing': 'linear', 'params': {'kind': 'dust', 'region': [[0, 0, 0], [1, 1, 1]], 'count': 5, 'size_range': [0.1, 0.2]}}]
        with self.assertRaises(StudioError) as caught:
            validate_shot(shot)
        self.assertIn('nothing reads', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
