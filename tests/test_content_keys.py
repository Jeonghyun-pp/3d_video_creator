"""Content keys by kind (studio/blender_ops/content_keys.py) agree with the code: title animation and fill layouts are
run on recording dicts per anim / layout, and the modules are scanned for every key they read; validation refuses keys
the kind ignores. Graphics are checked in Blender (tests/studio/content_keys_smoke.py)."""
import ast
from pathlib import Path
import unittest

from studio.blender_ops import content_keys as ck
from studio.blender_ops import env_fill_core
from studio import titles

ROOT = Path(__file__).resolve().parents[1]


class Recording(dict):
    def __init__(self, data):
        super().__init__(data); self.read = set()

    def get(self, key, default=None):
        self.read.add(key); return super().get(key, default)

    def __getitem__(self, key):
        self.read.add(key); return super().__getitem__(key)

    def __contains__(self, key):
        self.read.add(key); return super().__contains__(key)


def keys_read(files, names):
    """Constant keys read as name[...] or name.get(...) for any of `names` in the files."""
    out = set()
    for file in files:
        for node in ast.walk(ast.parse((ROOT / file).read_text())):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in names and isinstance(node.slice, ast.Constant):
                out.add(node.slice.value)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in names and node.args and isinstance(node.args[0], ast.Constant)):
                out.add(node.args[0].value)
    return out


class ContentKeysTest(unittest.TestCase):
    def test_titles(self):
        everything = {'title_id': 't', 'text': 'A', 'start_frame': 0, 'end_frame': 30, 'fade_in_frames': 3, 'fade_out_frames': 3,
                      'scale_curve': [1.2, 1.1, 1.0]}
        held, receding = Recording({**everything, 'anim': 'hold'}), Recording({**everything, 'anim': 'recede'})
        for title in (held, receding):
            titles.state(title, 10)
        self.assertNotIn('scale_curve', held.read)
        self.assertIn('scale_curve', receding.read)
        self.assertEqual(keys_read(['studio/titles.py'], {'title'}), ck.TITLE_ALWAYS | ck.TITLE_BY_ANIM['recede'])

    def test_fill_layouts(self):
        everything = {'item_id': 'i', 'role': 'subject', 'element': 'bench', 'count': 4, 'pitch_m': 3.0, 'at': 0.4, 'edge': 'both',
                      'density_per_100m2': 2.0, 'facing': 'inward'}
        for layout, row in ck.FILL_ITEM_BY_LAYOUT.items():
            item = Recording({**everything, 'layout': layout})
            env_fill_core.level_layout((0, 0, 20, 8), item, seed=1, name='t')
            layout_keys = set().union(*ck.FILL_ITEM_BY_LAYOUT.values())
            self.assertEqual(item.read & layout_keys, row, layout)
        files = ['studio/blender_ops/env_fill_core.py', 'studio/blender_ops/fill_brief.py', 'studio/fill.py']
        self.assertLessEqual(ck.FILL_ITEM_ALWAYS | set().union(*ck.FILL_ITEM_BY_LAYOUT.values()), keys_read(files, {'item'}))

    def test_validation_refuses_keys_the_kind_ignores(self):
        from studio.common import StudioError
        from studio.project import default_shot, validate_shot
        shot = default_shot('s', 30, {'request': 'x'})
        shot['graphics'] = [{'graphic_id': 'g', 'kind': 'outline', 'target': 'a', 'head_m': 0.5}]
        with self.assertRaises(StudioError) as caught:
            validate_shot(shot)
        self.assertIn('graphics/g/head_m', caught.exception.message)
        shot['graphics'] = []
        shot['titles'] = [{'title_id': 't', 'text': 'A', 'start_frame': 0, 'end_frame': 10, 'scale_curve': [1.0, 1.0]}]
        with self.assertRaises(StudioError) as caught:
            validate_shot(shot)
        self.assertIn('titles/t/scale_curve (anim hold', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
