"""One edit grammar for every value of a shot (studio/shot_edit.py): existing values always reachable, new keys only
where the schema (with the branches that apply) or a declared-reads table declares them, lists grow and shrink."""
import json
import unittest

from studio.common import StudioError
from studio.shot_edit import edit_shot

BASE = {'camera': {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                   'move': {'type': 'slide', 'params': {'target': 'r'}, 'lens_mm': 50}},
        'scene': {'primitives': [{'id': 'box', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0], 'material': 'm'}]},
        'actions': [{'action_id': 'spin', 'type': 'drive', 'targets': [{'instance_id': 'r', 'part_id': 'sun'}], 'start_frame': 0,
                     'end_frame': 10, 'easing': 'linear', 'params': {'drives': [{'joint': 'j', 'rpm': 30}]}},
                    {'action_id': 'blow', 'type': 'explode', 'targets': [{'instance_id': 'r', 'part_id': 'sun'}], 'start_frame': 0,
                     'end_frame': 10, 'easing': 'linear', 'params': {'direction_source': 'asset', 'distance_m': 1.0}}],
        'titles': [{'title_id': 't1', 'text': 'A'}], 'graphics': []}


def content():
    return json.loads(json.dumps(BASE))


class ShotEditTest(unittest.TestCase):
    def test_existing_values_and_table_defaults(self):
        c = content()
        self.assertEqual(edit_shot(c, {'op': 'set', 'path': '/camera/move/params/span', 'factor': 2.5}), '/camera/move/params/span: 0.8 → 2.0')
        edit_shot(c, {'op': 'set', 'path': '/actions/0/params/drives/0/rpm', 'factor': 2})
        self.assertEqual(c['actions'][0]['params']['drives'][0]['rpm'], 60)
        edit_shot(c, {'op': 'set', 'path': '/scene/primitives/0/at', 'value': [1, 2, 3]})
        self.assertEqual(c['scene']['primitives'][0]['at'], [1, 2, 3])

    def test_missing_object_on_the_way_is_created_when_declared(self):
        c = content()
        edit_shot(c, {'op': 'set', 'path': '/camera/move/framing/horizon_v', 'value': 0.4})
        self.assertEqual(c['camera']['move']['framing'], {'horizon_v': 0.4})
        with self.assertRaises(StudioError):
            edit_shot(c, {'op': 'set', 'path': '/camera/move/nonsense/x', 'value': 1})

    def test_lists_grow_and_shrink(self):
        c = content()
        edit_shot(c, {'op': 'add', 'path': '/titles/-', 'value': {'title_id': 't2', 'text': 'B'}})
        edit_shot(c, {'op': 'remove', 'path': '/titles/0'})
        self.assertEqual([t['title_id'] for t in c['titles']], ['t2'])
        edit_shot(c, {'op': 'set', 'path': '/titles', 'value': []})
        self.assertEqual(c['titles'], [])
        with self.assertRaises(StudioError):
            edit_shot(c, {'op': 'set', 'path': '/titles/3/text', 'value': 'x'})

    def test_conditional_branch_declares_keys_for_its_type(self):
        c = content()
        edit_shot(c, {'op': 'set', 'path': '/actions/1/params/stagger_frames', 'value': 2})    # explode branch declares it
        self.assertEqual(c['actions'][1]['params']['stagger_frames'], 2)
        with self.assertRaises(StudioError) as caught:
            edit_shot(c, {'op': 'set', 'path': '/actions/1/params/marker_count', 'value': 2})  # a flow key, not explode's
        self.assertIn("nothing reads 'marker_count'", caught.exception.message)

    def test_refusals(self):
        c = content()
        for op, words in [({'op': 'set', 'path': '/narration/text', 'value': 'x'}, 'edit path must start'),
                          ({'op': 'set', 'path': '/render/x', 'value': 1}, 'not a value this shot declares'),
                          ({'op': 'set', 'path': 'camera/lens', 'value': 1}, 'JSON pointer'),
                          ({'op': 'set', 'path': '/camera/move/params/target', 'factor': 2}, 'no number to scale'),
                          ({'op': 'remove', 'path': '/camera/move/lens_end_mm'}, 'nothing to remove'),
                          ({'op': 'set', 'path': '/camera/move/params/spam', 'value': 1}, "nothing reads 'spam'"),
                          ({'op': 'nudge', 'path': '/camera/move/lens_mm'}, 'not one of')]:
            with self.assertRaises(StudioError, msg=op) as caught:
                edit_shot(c, op)
            self.assertIn(words, caught.exception.message, op)


if __name__ == '__main__':
    unittest.main()
