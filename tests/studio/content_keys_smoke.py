"""Run inside Blender: graphics.build and graphics.build_screen on recording specs, one per kind and space; what each
read must equal its row in studio/blender_ops/content_keys.py (a key read but not in the row would be refused and
unreachable by words; a key in the row never read would be a silent no-op).
"""
from pathlib import Path
import json
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import content_keys as ck  # noqa: E402
import graphics  # noqa: E402


class Recording(dict):
    def __init__(self, data):
        super().__init__(data); self.read = set()

    def get(self, key, default=None):
        self.read.add(key); return super().get(key, default)

    def __getitem__(self, key):
        self.read.add(key); return super().__getitem__(key)

    def __contains__(self, key):
        self.read.add(key); return super().__contains__(key)


bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene; scene.render.fps = 30; scene.frame_end = 30
scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
for name, loc in (('a', (0, 0, 0)), ('b', (4, 0, 0))):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    obj = bpy.context.object; obj.name = name; obj['studio_id'] = 'test/' + name
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
cam.location = (2, -14, 3); cam.rotation_euler = (1.45, 0, 0)

common = {'start_frame': 0, 'end_frame': 30, 'color_srgb': [1, 0.2, 0.1], 'opacity': 0.9, 'draw_on_s': 0.3}
world = {
    'outline': {'kind': 'outline', 'target': 'test/a', 'radius_m': 0.03},
    'arrow': {'kind': 'arrow', 'anchors': ['test/a', 'test/b'], 'head_m': 0.5, 'radius_m': 0.03},
    'dimension': {'kind': 'dimension', 'anchors': ['test/a', 'test/b'], 'tick_m': 0.3, 'text': 'auto', 'radius_m': 0.03},
    'highlight': {'kind': 'highlight', 'anchors': ['test/a', 'test/b'], 'radius_m': 0.03},
    'draw_line': {'kind': 'draw_line', 'anchors': ['test/a', 'test/b'], 'radius_m': 0.03},
}
problems = {}
for kind, spec in world.items():
    rec = Recording({'graphic_id': kind, 'space': 'world', **common, **spec})
    graphics.build({'duration_frames': 30, 'graphics': [rec]})
    row = ck.graphic_reads(rec)
    if rec.read != row:
        problems[f'world/{kind}'] = {'read_not_in_row': sorted(rec.read - row), 'in_row_never_read': sorted(row - rec.read)}
screen = Recording({'graphic_id': 'pointer', 'kind': 'arrow', 'space': 'screen', **common, 'points_2d': [[0.2, 0.3], [0.45, 0.42]],
                    'shaft_frac': 0.008, 'head_ratio': 3.5, 'fade_frames': 6})
graphics.build_screen({'duration_frames': 30, 'graphics': [screen]})
row = ck.graphic_reads(screen)
if screen.read != row:
    problems['screen/arrow'] = {'read_not_in_row': sorted(screen.read - row), 'in_row_never_read': sorted(row - screen.read)}
assert not problems, json.dumps(problems, indent=1)
print('STUDIO_CONTENT_KEYS_SMOKE ' + json.dumps({'ok': True, 'kinds_checked': len(world) + 1}))
