"""Blender facts the engine's scatter / simulation design rests on (re-run after a Blender upgrade).

Measured 2026-10-05 on 5.2.2:
  - a Geometry Nodes host whose output is only instances has a zero bound_box and an empty to_mesh();
    depsgraph.object_instances lists each instance with its world matrix;
  - scene.ray_cast hits instances and returns the *host* object with the *instance* matrix (Object Info
    instances); Collection Info instances come back as their *source* object (scatter.py, scene_roles);
  - a simulation-zone bake with bake_target PACKED lives inside the .blend (frames are exact after reload,
    in any order); a rigid-body point cache baked in memory is deterministic run to run.
Run inside Blender: blender -b --factory-startup --python tests/studio/blender_facts_smoke.py
"""
import json

import bmesh
import bpy
from mathutils import Vector

checks = {}


def check(name, ok, detail=None):
    checks[name] = detail if detail is not None else ok
    assert ok, f'{name}: {detail}'


def reset():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)


def mesh(name, build):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new(); build(bm); bm.to_mesh(me); bm.free()
    return me


scene = bpy.context.scene
reset()
# 1. GN instances: invisible to bound_box / to_mesh, visible to object_instances and ray_cast (host + instance matrix)
src = bpy.data.collections.new('src'); scene.collection.children.link(src)
cube = bpy.data.objects.new('cube', mesh('cube', lambda bm: bmesh.ops.create_cube(bm, size=1.0))); src.objects.link(cube)
bpy.context.view_layer.layer_collection.children['src'].exclude = True
host = bpy.data.objects.new('host', mesh('host', lambda bm: bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=10)))
scene.collection.objects.link(host)
ng = bpy.data.node_groups.new('facts_scatter', 'GeometryNodeTree')
ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
gi, go = ng.nodes.new('NodeGroupInput'), ng.nodes.new('NodeGroupOutput')
pts, inst, info = ng.nodes.new('GeometryNodeMeshToPoints'), ng.nodes.new('GeometryNodeInstanceOnPoints'), ng.nodes.new('GeometryNodeObjectInfo')
info.inputs['Object'].default_value = cube
ng.links.new(gi.outputs[0], pts.inputs['Mesh']); ng.links.new(pts.outputs[0], inst.inputs['Points'])
ng.links.new(info.outputs['Geometry'], inst.inputs['Instance']); ng.links.new(inst.outputs[0], go.inputs[0])
host.modifiers.new('scatter', 'NODES').node_group = ng
dg = bpy.context.evaluated_depsgraph_get()
ev = host.evaluated_get(dg)
m = ev.to_mesh(); verts = len(m.vertices); ev.to_mesh_clear()
check('instances_not_in_bound_box_or_to_mesh', max(abs(c) for v in ev.bound_box for c in v) < 1e-9 and verts == 0, {'verts': verts})
instances = [i for i in dg.object_instances if i.is_instance and i.object.original is host]
check('object_instances_list_instances', len(instances) == 4, len(instances))
hit, loc, _n, _i, obj, matrix = scene.ray_cast(dg, Vector((10, 10, 5)), Vector((0, 0, -1)))
check('ray_cast_hits_instance_as_host', hit and obj.original is host and (matrix.translation - Vector((10, 10, 0))).length < 1e-6,
      {'obj': obj.name if hit else None, 'matrix_t': list(matrix.translation) if hit else None})

# 2. Simulation zone bake, PACKED: in-session frames, any order
reset()
scene.frame_start, scene.frame_end = 1, 30
dust = bpy.data.objects.new('dust', mesh('pts', lambda bm: bmesh.ops.create_grid(bm, x_segments=3, y_segments=3, size=1)))
scene.collection.objects.link(dust); dust.location.z = 5
sim = bpy.data.node_groups.new('facts_sim', 'GeometryNodeTree')
sim.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
sim.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
n, l = sim.nodes, sim.links
gi, go = n.new('NodeGroupInput'), n.new('NodeGroupOutput')
si, so = n.new('GeometryNodeSimulationInput'), n.new('GeometryNodeSimulationOutput'); si.pair_with_output(so)
setp = n.new('GeometryNodeSetPosition'); off = n.new('FunctionNodeInputVector'); off.vector = (0, 0, -0.1)
l.new(gi.outputs[0], si.inputs['Geometry']); l.new(si.outputs['Geometry'], setp.inputs['Geometry'])
l.new(off.outputs[0], setp.inputs['Offset']); l.new(setp.outputs[0], so.inputs['Geometry']); l.new(so.outputs['Geometry'], go.inputs[0])
mod = dust.modifiers.new('sim', 'NODES'); mod.node_group = sim
for bake in mod.bakes:
    bake.bake_target = 'PACKED'
bpy.context.view_layer.objects.active = dust
with bpy.context.temp_override(active_object=dust, object=dust, selected_objects=[dust], selected_editable_objects=[dust]):
    bpy.ops.object.geometry_node_bake_single(session_uid=dust.session_uid, modifier_name=mod.name, bake_id=mod.bakes[0].bake_id)


def zmin(obj, frame):
    scene.frame_set(frame)
    e = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = e.to_mesh(); z = min((obj.matrix_world @ v.co).z for v in me.vertices); e.to_mesh_clear()
    return round(z, 4)


forward = [zmin(dust, f) for f in (1, 10, 20, 30)]
jumped = [zmin(dust, f) for f in (30, 10, 20, 1)]
check('sim_zone_packed_bake_random_access', forward == [4.9, 4.0, 3.0, 2.0] and jumped == [2.0, 4.0, 3.0, 4.9] and mod.bakes[0].bake_target == 'PACKED',
      {'forward': forward, 'jumped': jumped})

# 3. Rigid body, memory point cache: baked, deterministic across two bakes
results = []
for run in range(2):
    reset()
    if scene.rigidbody_world:
        with bpy.context.temp_override(scene=scene):
            bpy.ops.rigidbody.world_remove()
    scene.frame_start, scene.frame_end = 1, 40
    floor = bpy.data.objects.new('floor', mesh('floor', lambda bm: (bmesh.ops.create_cube(bm, size=1.0), bmesh.ops.scale(bm, vec=(10, 10, 1), verts=bm.verts))))
    scene.collection.objects.link(floor); floor.location.z = -0.5
    rocks = []
    for i in range(5):
        r = bpy.data.objects.new(f'rock{i}', mesh(f'rock{i}', lambda bm: (bmesh.ops.create_cube(bm, size=1.0), bmesh.ops.scale(bm, vec=(0.4, 0.4, 0.4), verts=bm.verts))))
        scene.collection.objects.link(r); r.location = (0.3 * i, 0.2 * i, 3 + i); rocks.append(r)
    with bpy.context.temp_override(scene=scene):
        bpy.ops.rigidbody.world_add()
    for o in [floor] + rocks:
        bpy.context.view_layer.objects.active = o
        with bpy.context.temp_override(active_object=o, object=o):
            bpy.ops.rigidbody.object_add()
    floor.rigid_body.type = 'PASSIVE'
    cache = scene.rigidbody_world.point_cache
    cache.frame_start, cache.frame_end = 1, 40
    with bpy.context.temp_override(scene=scene, point_cache=cache):
        bpy.ops.ptcache.bake(bake=True)
    scene.frame_set(40)
    results.append([tuple(round(x, 5) for x in r.matrix_world.translation) for r in rocks])
    check(f'rigid_body_memory_cache_baked_{run}', cache.is_baked and not cache.use_disk_cache, {'baked': cache.is_baked})
check('rigid_body_deterministic', results[0] == results[1], results[0][:2])

print('STUDIO_BLENDER_FACTS_SMOKE ' + json.dumps({'ok': True, 'blender': bpy.app.version_string, 'checks': checks}, default=str))
