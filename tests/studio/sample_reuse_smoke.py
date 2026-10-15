"""Blender: a part's sample points are drawn once and reused only while the part stands exactly as it was drawn
(camera_frame.sample, 2026-10-15: one failing moment drew the same 4000 points 846 times).

A crane hook on a cable, not a rocket (N+1):
- the same hook, unchanged, gives the same points, and the second draw is not redrawn (_draw is not called);
- the hook moved, turned, hidden from render, cut by a boolean, bent by a deform modifier or shown at another frame of
  its animation gives a new set, equal to a fresh draw;
- a narrowed draw (keep=) is never reused;
- a batch of rays remembers passes_through per object, and a see-through window set between batches is seen.
Run: blender --background --factory-startup --python tests/studio/sample_reuse_smoke.py
"""
from pathlib import Path
import json
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402

report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
scene = bpy.context.scene
bpy.ops.mesh.primitive_torus_add(major_radius=0.6, minor_radius=0.15, location=(0, 0, 3))
hook = bpy.context.object
hook.name = 'hook'
bpy.ops.mesh.primitive_cylinder_add(radius=0.03, depth=4, location=(0, 0, 5.5))
bpy.context.object.name = 'cable'

draws = []
real = camera_frame._draw


def counted(*args, **kwargs):
    draws.append(1)
    return real(*args, **kwargs)


camera_frame._draw = counted


def fresh(refs, **kw):
    camera_frame._SAMPLED.clear()
    return camera_frame.sample(refs, bpy.context.evaluated_depsgraph_get(), **kw)


def again(refs, **kw):
    return camera_frame.sample(refs, bpy.context.evaluated_depsgraph_get(), **kw)


first = fresh(['hook'], count=400)
n = len(draws)
check('unchanged_is_reused', again(['hook'], count=400) == first and len(draws) == n, len(draws))


def changed(name, change):
    before = again(['hook'], count=400)
    change()
    bpy.context.view_layer.update()
    after = again(['hook'], count=400)
    check(name, after != before and after == fresh(['hook'], count=400), {'first': after[0]})


changed('moved', lambda: setattr(hook, 'location', (0.5, 0, 3)))
changed('turned', lambda: setattr(hook, 'rotation_euler', (0.4, 0, 0)))
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.5, 0.6, 3))
box = bpy.context.object
box.hide_render = True


def cut():
    mod = hook.modifiers.new('cut', 'BOOLEAN')
    mod.object = box
changed('cut', cut)
changed('bent', lambda: hook.modifiers.new('bend', 'SIMPLE_DEFORM').__setattr__('deform_method', 'BEND'))
hook.keyframe_insert('location', frame=1)
hook.location = (2.0, 0, 3)
hook.keyframe_insert('location', frame=20)
scene.frame_set(1)
at_one = fresh(['hook'], count=400)
scene.frame_set(20)
check('another_frame', again(['hook'], count=400) != at_one, None)
scene.frame_set(1)
check('back_to_the_frame', again(['hook'], count=400) == at_one, None)
n = len(draws)
camera_frame.sample(['hook'], bpy.context.evaluated_depsgraph_get(), count=400, keep=lambda o: True)
camera_frame.sample(['hook'], bpy.context.evaluated_depsgraph_get(), count=400, keep=lambda o: True)
check('narrowed_never_reused', len(draws) == n + 2, len(draws) - n)
hidden_cable = bpy.data.objects['cable']
both = again(['hook', 'cable'], count=400)
hidden_cable.hide_render = True
check('render_flag_is_part_of_it', again(['hook', 'cable'], count=400) != both, None)
hidden_cable.hide_render = False

# a batch remembers passes_through; a window set between batches is seen by the next batch
depsgraph = bpy.context.evaluated_depsgraph_get()
eye, target = Vector((0, 0, 9)), Vector((0, 0, 3.6))
through = camera_frame.through_at(scene.frame_current)
blocked = camera_frame.walk(scene, depsgraph, eye, target, mine={'hook'}, through=through)
check('cable_blocks', not blocked[0] and blocked[1].name == 'cable', str(blocked))
hidden_cable[camera_frame.SEE_WINDOW] = [0, 10 ** 6]
seen = camera_frame.walk(scene, depsgraph, eye, target, mine={'hook'}, through=camera_frame.through_at(scene.frame_current))
check('next_batch_sees_the_window', seen[0], str(seen))
print('SAMPLE_REUSE_SMOKE ' + json.dumps(report, default=str)[:2000])
