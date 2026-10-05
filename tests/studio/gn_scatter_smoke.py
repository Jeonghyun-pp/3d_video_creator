"""Scatter (Geometry Nodes instances): deterministic placement, sources hidden, instances seen by the engine.

Run inside Blender: blender -b --factory-startup --python tests/studio/gn_scatter_smoke.py
"""
import json
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import scatter as sc  # noqa: E402
import scene_geometry  # noqa: E402
from scene_roles import first_blocking_hit  # noqa: E402

checks = {}


def check(name, ok, detail=None):
    checks[name] = detail if detail is not None else ok
    assert ok, f'{name}: {detail}'


def reset():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for g in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(g)


def box(name, size, loc=(0, 0, 0)):
    me = bpy.data.meshes.new(name); bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts); bmesh.ops.translate(bm, vec=(0, 0, size[2] / 2), verts=bm.verts)
    bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(o); o.location = loc
    return o


def build(seed):
    reset()
    ground = box('ground', (40, 40, 0.1), (0, 0, -0.1))
    person = box('person', (0.5, 0.3, 1.75))
    crate = box('crate', (1, 1, 1))
    host = sc.scatter('crowd', [person, crate], surface=ground, density=0.05, seed=seed, scale=(0.9, 1.1))
    row = sc.scatter('row', [crate], points=[[x, 25, 0] for x in range(-10, 11, 5)], seed=seed)
    return ground, host, row


ground, host, row = build(7)
first = (sc.digest(host), sc.instance_count(host), sc.instance_count(row))
picked = {i.object.original.name for i in bpy.context.evaluated_depsgraph_get().object_instances if i.is_instance and i.parent and i.parent.original is host}
check('shared_source_kept_in_both', picked == {'person', 'crate'}, sorted(picked))
check('instances_created', first[1] > 20 and first[2] == 5, {'crowd': first[1], 'row': first[2]})
check('sources_not_rendered', all(o.get('studio_scene_role') == 'scatter_source' for o in bpy.data.objects if o.name in ('person', 'crate'))
      and bpy.context.view_layer.layer_collection.children[sc.SOURCES].exclude, True)
again = build(7)
check('deterministic', (sc.digest(again[1]), sc.instance_count(again[1])) == first[:2], first[:2])
other = build(8)
check('seed_changes_layout', sc.digest(other[1]) != first[0], True)

ground, host, row = build(7)
dg = bpy.context.evaluated_depsgraph_get()
boxes = scene_geometry.instance_boxes(dg, keep=lambda h: h is host)
check('instances_have_world_boxes', len(boxes) == sc.instance_count(host) and all(hi[2] > 0.5 for _h, _lo, hi in boxes), len(boxes))
# a ray straight down onto the 'row' instance at x=0 hits it as the scatter host
hit, loc, _n, _i, obj, _m = first_blocking_hit(bpy.context.scene, dg, Vector((0, 25, 10)), Vector((0, 0, -1)), 20)
check('ray_blocked_by_instance', hit and obj.get('studio_scene_role') == 'scatter_source' and abs(loc.z - 1.0) < 1e-4,
      {'obj': obj.name if hit else None})
verts, tris = scene_geometry.merged(dg, keep=lambda h: h is row)
check('merged_geometry_includes_instances', verts is not None and len(verts) == 5 * 8, None if verts is None else len(verts))

print('STUDIO_GN_SCATTER_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
