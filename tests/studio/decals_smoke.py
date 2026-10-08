"""Decals: words on a surface are drawn in the project font, sit on the card where the shot puts them, follow the object
they are on, stay out of the control passes, and are kept from Blender in a generated take (2026-10-08, archcut3's
red tag). Run: .venv/bin/python tests/studio/decals_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import blender_binary, read_json, write_json
from studio.generative.keep import build_keep_masks
from studio.project import init_project, shot_path

SCENE = {'world': {'kind': 'blockout', 'color': [0.3, 0.3, 0.35], 'strength': 0.8, 'samples': 16},
         'materials': {'c': {'color': [0.6, 0.6, 0.58]}},
         'primitives': [{'id': 'column', 'shape': 'box', 'size': [1, 1, 4], 'at': [0, 0, 2], 'material': 'c'}],
         'decals': [{'id': 'tag', 'text': '현장\n점검', 'at': [0, -0.51, 2.2], 'normal': [0, -1, 0], 'size_m': 0.6, 'on': 'column',
                     'weight': 'ExtraBold'}]}
CHECK = r'''
import bpy, json, sys
sys.path.insert(0, sys.argv[-2])
from scene_roles import counts
tag = bpy.data.objects['tag']
out = {'role': tag.get('studio_scene_role'), 'control': counts(tag, 'control'), 'clay': counts(tag, 'clay'), 'parent': tag.parent.name if tag.parent else None,
       'image': [n.image.name for n in tag.active_material.node_tree.nodes if n.type == 'TEX_IMAGE'][0], 'z': round(tag.matrix_world.translation.z, 3)}
open(sys.argv[-1], 'w').write(json.dumps(out))
'''

checks = []
with tempfile.TemporaryDirectory(prefix='decals-smoke-') as root:
    p = Path(init_project('decals', {'request': 'decal smoke', 'shots': [{'shot_id': 's', 'frame_count': 12}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'screen': {'subject': ['column']},
                 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
                            'keys': [{'frame': 0, 'location': [0, -6, 2.2], 'target': [0, 0, 2.0]}, {'frame': 11, 'location': [0, -6, 2.2], 'target': [0, 0, 2.0]}]}})
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    drawn = sorted((p / 'decals').glob('*.png'))
    assert len(drawn) == 1, drawn
    with Image.open(drawn[0]) as image:
        assert image.getpixel((5, 5))[:3] == (219, 26, 20) and image.width == 240, (image.getpixel((5, 5)), image.size)   # red card, 0.6 m x 400 px/m
    version = p / 'shots/s/versions' / built['scene_version']
    out = Path(root) / 'out.json'; check = Path(root) / 'check.py'; check.write_text(CHECK)
    subprocess.run([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(version / 'scene.blend'), '--python-exit-code', '1',
                    '--python', str(check), '--', str(ROOT / 'studio/blender_ops'), str(out)], check=True, capture_output=True, timeout=600)
    m = json.loads(out.read_text())
    assert m['role'] == 'decal' and not m['control'] and not m['clay'] and m['parent'] == 'column' and abs(m['z'] - 2.2) < 1e-3, m
    checks.append('drawn_in_the_project_font_on_its_column_out_of_control_passes')
    masks = build_keep_masks(p, 's')   # no screen.keep: the decal alone is kept
    assert masks['parts'] == ['tag'], masks['parts']
    white = sum(1 for v in Image.open(masks['pattern'] % 5).convert('L').get_flattened_data() if v > 127)
    assert white > 1000, white
    checks.append(f'kept_in_generated_takes ({white} px)')
print('STUDIO_DECALS_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
