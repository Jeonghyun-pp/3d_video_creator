"""Shot field layers (studio/project.py SHOT_FIELD_LAYER): every field of the shot schema has exactly one layer, and every
content field is reachable through the one edit grammar (workbench set_shot_value, storyboard revise) - 2026-10-07:
concealed_parts and labels were out of its reach, and storyboard revise refused render and key_parts edits."""
import tempfile
import unittest
from pathlib import Path

from studio.common import StudioError, write_json
from studio.project import SHOT_CONTENT, SHOT_FIELD_LAYER, init_project, load_schema, load_shot, validate_shot
from studio.shot_edit import edit_shot


class ShotLayerTest(unittest.TestCase):
    def test_every_schema_field_has_exactly_one_layer(self):
        fields = list(load_schema('shot')['properties'])
        layered = [f for names in SHOT_FIELD_LAYER.values() for f in names]
        self.assertEqual(sorted(layered), sorted(set(layered)), 'a field in two layers')
        self.assertEqual(set(fields), set(layered), 'classify the new shot field in project.SHOT_FIELD_LAYER')

    def project(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(init_project('layers', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 30,
                                 'labels': [{'label_id': 'l', 'text': 'cam', 'anchor': 'a', 'start_frame': 0, 'end_frame': 20, 'slot': 'upper_left', 'occlusion_policy': 'hide'}]}]},
                                 tmp.name)['project_path'])
        return path

    def test_content_fields_take_the_edit_grammar(self):
        shot = load_shot(self.project(), 's')
        content = {k: shot.get(k) for k in SHOT_CONTENT}
        for op in ({'op': 'add', 'path': '/concealed_parts/-', 'value': {'id': 'valve'}},
                   {'op': 'set', 'path': '/labels/0/text', 'value': 'camshaft'},
                   {'op': 'add', 'path': '/key_parts/-', 'value': {'id': 'piston'}},
                   {'op': 'set', 'path': '/render/look_preset', 'value': 'flat_stylized'}):
            edit_shot(content, op)
        validate_shot({**shot, **{k: v for k, v in content.items() if v is not None}})
        self.assertEqual(content['concealed_parts'], [{'id': 'valve'}])
        self.assertEqual(content['labels'][0]['text'], 'camshaft')
        with self.assertRaises(StudioError):   # a decision-layer field stays with its own command
            edit_shot(content, {'op': 'set', 'path': '/duration_frames', 'value': 60})

    def test_storyboard_change_reaches_revise_for_every_content_field(self):
        from studio.blender import revise_shot
        from studio.storyboard import apply_ops
        path = self.project()
        shot = load_shot(path, 's')
        change, _ = apply_ops(shot, [{'op': 'set', 'path': '/render/look_preset', 'value': 'flat_stylized'},
                                     {'op': 'add', 'path': '/concealed_parts/-', 'value': {'id': 'valve'}},
                                     {'op': 'add', 'path': '/key_parts/-', 'value': {'id': 'piston'}}])
        self.assertTrue({'render', 'concealed_parts', 'key_parts'} <= set(change))
        file = path / 'change.json'
        write_json(file, {'base_revision': shot['revision'], 'scope': 'scene', 'targets': [], 'preserve': [],
                          'change': {k: v for k, v in change.items() if k != 'scene'}})
        with self.assertRaises(StudioError) as caught:   # past the scope check; it stops only because nothing is built yet
            revise_shot(path, 's', file)
        self.assertNotIn('Unexpected fields', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
