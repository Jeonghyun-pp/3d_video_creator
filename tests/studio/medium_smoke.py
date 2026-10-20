"""Run inside Blender (headless): a medium the world declares (world element `medium`) is see-through over the whole shot
- no ray stops at it from the first frame to the last, and the clay draws it see-through - so the camera frames what is
in it as the picture does (2026-10-20, canal: the chamber's water boxes were solid to the camera and hid the hull).
"""
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import show_core  # noqa: E402
import show_scene  # noqa: E402
from mathutils import Vector  # noqa: E402


def box(name, location, size):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.scale = size
    obj.name = obj['studio_id'] = name
    return obj


bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 48
water = bpy.data.objects.new('water', None)            # the element's root, its pieces under it (as the canal built it)
scene.collection.objects.link(water)
water['studio_id'] = 'water'
pieces = [box(f'water/a{n}', (0.0, 4.0 * n, 0.0), (3.0, 3.0, 2.0)) for n in range(2)]
for p in pieces:
    p.parent = water
hull = box('hull', (0.0, 0.0, -0.5), (1.0, 1.0, 0.5))  # inside the first piece
job = {'media': ['water'], 'shot': {'duration_frames': 48}}
rows = show_scene.media(job)
assert rows and sorted(rows[0]['objects']) == ['water/a0', 'water/a1'], rows
scene.frame_set(1)
depsgraph = bpy.context.evaluated_depsgraph_get()
origin, target = Vector((0.0, -10.0, -0.5)), Vector((0.0, -0.5, -0.5))   # a ray from the side through the water to the hull
for frame in (1, 24, 48):
    assert all(camera_frame.passes_through(p, frame) for p in pieces), frame
reached, first = camera_frame.walk(scene, depsgraph, origin, target)
assert reached, getattr(first, 'name', None)   # the water stops no ray: the hull is seen in it
assert not camera_frame.passes_through(hull, 1)
drawn = show_scene.see_through(job)                    # after the look: faded from the first frame
assert drawn and drawn[0]['how'] == 'medium', drawn
scene.frame_set(24)
for p in pieces:
    alpha = [s.material.diffuse_color[3] for s in p.material_slots if s.material is not None]
    assert alpha and all(abs(a - (1.0 - show_core.MEDIUM['alpha'])) < 1e-6 for a in alpha), (p.name, alpha)
assert show_scene.media({'media': [], 'shot': {}}) == [] and show_scene.media({'shot': {}}) == []
print('STUDIO_MEDIUM_SMOKE', {'ok': True, 'objects': rows[0]['objects']})
