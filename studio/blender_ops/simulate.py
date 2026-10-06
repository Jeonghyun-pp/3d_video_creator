"""The `simulate` action: debris that falls and dust that drifts, baked at build time and kept inside the .blend.

Render workers render arbitrary frames (resume, look frames, CPU fallback), so nothing may be simulated at
render time. Debris is simulated once per set of inputs and kept as plain keyframes (CACHE below): a rebuild or a
revision with the same inputs gets the same trajectory, not a fresh run of the solver. Measured on 5.2 (tests/studio/blender_facts_smoke.py): a rigid-body point cache baked in memory and
a simulation-zone bake with bake_target PACKED both live in the saved .blend, give the same frame whatever order
frames are visited in, and repeat exactly run to run. So the build bakes here, before anything measures the scene
(clearance, rig guards, look, fidelity), and check_baked() refuses a version with a live or on-disk simulation.

kinds
  rigid_debris  `count` chunks (seeded sizes, shapes, poses) spawned in `region`, held kinematic until the action's
                start frame, then falling onto the action's targets (passive, final evaluated mesh - a road a
                reveal is cutting open lets debris through). Role 'simulated'.
  dust          `count` points in `region` drifting up and out with drag from the start frame (removed above `ceiling_z`,
                default the region top, so no speck drifts out of an opening into the sky), instanced as small
                spheres (Geometry Nodes simulation zone, PACKED). Role 'atmosphere': never in control passes,
                clay or camera ray casts.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random

import bmesh
import bpy

from scene_tools import curves, targets

ROLE = 'studio_scene_role'
PREFIX = 'StudioSim_'
OWNER = 'studio_simulation'   # the simulate action that made an object


def _region(p):
    (x0, y0, z0), (x1, y1, z1) = p['region']
    return (min(x0, x1), min(y0, y1), min(z0, z1)), (max(x0, x1), max(y0, y1), max(z0, z1))


def _chunk_meshes(seed, sizes):
    """A few irregular chunk shapes (shared by every piece of the same shape)."""
    meshes = []
    rng = random.Random(seed)
    for k, size in enumerate(sizes):
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=1, radius=size / 2)
        for v in bm.verts:  # chipped, not round
            v.co *= rng.uniform(0.75, 1.15)
        bmesh.ops.scale(bm, vec=(1.0, rng.uniform(0.6, 1.0), rng.uniform(0.5, 0.9)), verts=bm.verts)
        me = bpy.data.meshes.new(f'{PREFIX}chunk{k}')
        bm.to_mesh(me); bm.free()
        meshes.append(me)
    return meshes


def _material(name, rgb):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*rgb, 1)
    bsdf.inputs['Roughness'].default_value = 0.9
    m.diffuse_color = (*rgb, 1)
    return m


def _rigid_world(scene, frame_end):
    if scene.rigidbody_world is None:
        with bpy.context.temp_override(scene=scene):
            bpy.ops.rigidbody.world_add()
    world = scene.rigidbody_world
    world.point_cache.frame_start, world.point_cache.frame_end = 1, frame_end
    return world


def _add_body(obj, kind):
    bpy.context.view_layer.objects.active = obj
    with bpy.context.temp_override(active_object=obj, object=obj, selected_objects=[obj]):
        if obj.rigid_body is None:
            bpy.ops.rigidbody.object_add()
    obj.rigid_body.type = kind
    return obj.rigid_body


def rigid_debris(action, frames):
    scene = bpy.context.scene
    p, start = action['params'], action['start_frame']
    lo, hi = _region(p)
    rng = random.Random(int(p.get('seed', 0)))
    sizes = p.get('size_range', [0.2, 0.6])
    shapes = _chunk_meshes(int(p.get('seed', 0)), [sizes[0], (sizes[0] + sizes[1]) / 2, sizes[1]])
    material = _material(f'{PREFIX}debris', p.get('color_srgb', [0.42, 0.40, 0.37]))
    for me in shapes:
        if not me.materials:
            me.materials.append(material)
    _rigid_world(scene, frames)
    pieces = []
    for i in range(int(p['count'])):
        me = shapes[rng.randrange(len(shapes))]
        obj = bpy.data.objects.new(f"{PREFIX}{action['action_id']}.{i:03d}", me)
        scene.collection.objects.link(obj)
        obj.location = tuple(rng.uniform(lo[k], hi[k]) for k in range(3))
        obj.rotation_euler = tuple(rng.uniform(0, 2 * math.pi) for _ in range(3))
        obj[ROLE] = 'simulated'
        obj['studio_id'] = obj.name
        obj[OWNER] = action['action_id']
        body = _add_body(obj, 'ACTIVE')
        body.collision_shape, body.mass, body.friction = 'CONVEX_HULL', 2.0, 0.8
        body.kinematic = True  # held at its spawn pose until the action starts (keyed, so the bake sees it)
        body.keyframe_insert('kinematic', frame=start + 1)
        body.kinematic = False
        body.keyframe_insert('kinematic', frame=start + 2)
        pieces.append(obj)
    colliders = []
    for obj in targets(action):
        # Measured 2026-10-05: Bullet keeps a collider's shape from the start even with deform + animated set, so
        # debris rested on a road a reveal had already opened. Collide with what lies below an opening instead.
        if any(m.type == 'BOOLEAN' and m.name.startswith('StudioReveal_') for m in obj.modifiers):
            raise ValueError(f"SIMULATE: collider {obj.name} is cut by a reveal (its changing shape is not seen by the "
                             "rigid-body solver); spawn the debris over the opening and collide with what lies below")
        added = obj.rigid_body is None
        body = _add_body(obj, 'PASSIVE')
        body.collision_shape = 'MESH'
        colliders.append((obj, added))
    key = _input_key(action, frames, pieces, [c for c, _ in colliders], scene)
    track = _read_track(key)
    if track is None:   # first time these inputs are seen: simulate once, keep the trajectory
        cache = scene.rigidbody_world.point_cache
        with bpy.context.temp_override(scene=scene, point_cache=cache):
            bpy.ops.ptcache.free_bake()
            bpy.ops.ptcache.bake(bake=True)
        track = []
        for f in range(1, frames + 1):
            scene.frame_set(f)
            track.append([[list(o.matrix_world.translation), list(o.matrix_world.to_quaternion())] for o in pieces])
        _write_track(key, track)
    _key_track(scene, pieces, colliders, track)
    return {'action_id': action['action_id'], 'kind': 'rigid_debris', 'pieces': len(pieces), 'colliders': [c.name for c, _ in colliders],
            'baked': True, 'frames': [1, frames], 'input_key': key}   # computed now = simulation_cache/<key>.json in this version


def _input_key(action, frames, pieces, colliders, scene):
    """Everything the solver reads, at full precision: the action, the pieces (mesh, spawn pose, body settings), the
    colliders' evaluated meshes and poses, the world settings and the Blender version."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    world = scene.rigidbody_world
    parts = [json.dumps(action, sort_keys=True), frames, bpy.app.version_string,
             (world.substeps_per_frame, world.solver_iterations, world.time_scale, tuple(scene.gravity))]
    for obj in pieces + colliders:
        mesh = obj.evaluated_get(depsgraph).data
        body = obj.rigid_body
        parts.append((obj.name, [tuple(v.co) for v in mesh.vertices], [tuple(p.vertices) for p in mesh.polygons],
                      [tuple(r) for r in obj.matrix_world], body.type, body.mass, body.friction, body.restitution,
                      body.collision_shape, body.collision_margin))
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:24]


def _read_track(key):
    folder = CACHE.get('read')
    path = Path(folder) / f'{key}.json' if folder else None
    return json.loads(path.read_text())['track'] if path is not None and path.is_file() else None


def _write_track(key, track):
    folder = CACHE.get('write')
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / f'{key}.json').write_text(json.dumps({'schema_version': 1, 'key': key, 'track': track}))


def _key_track(scene, pieces, colliders, track):
    """The trajectory becomes plain keyframes (one per frame, constant): the same scene whether it was just simulated
    or read from the cache, and nothing left for a render worker to simulate."""
    for obj in pieces + [c for c, added in colliders if added]:
        bpy.context.view_layer.objects.active = obj
        with bpy.context.temp_override(active_object=obj, object=obj, selected_objects=[obj]):
            bpy.ops.rigidbody.object_remove()
    if scene.rigidbody_world is not None and not scene.rigidbody_world.collection.objects:
        with bpy.context.temp_override(scene=scene):
            bpy.ops.rigidbody.world_remove()
    for i, obj in enumerate(pieces):   # written in bulk like camera_rig._key: the first key makes the curves, the rest are set
        obj.animation_data_clear()
        obj.rotation_mode = 'QUATERNION'
        obj.location, obj.rotation_quaternion = track[0][i][0], track[0][i][1]
        obj.keyframe_insert('location', frame=1)
        obj.keyframe_insert('rotation_quaternion', frame=1)
        series = {('location', k): [row[i][0][k] for row in track] for k in range(3)}
        series.update({('rotation_quaternion', k): [row[i][1][k] for row in track] for k in range(4)})
        for curve in curves(obj.animation_data.action):
            values = series[(curve.data_path, curve.array_index)]
            points = curve.keyframe_points
            points.add(len(values) - len(points))
            points.foreach_set('co', [c for f, v in enumerate(values) for c in (f + 1, v)])
            for point in points:
                point.interpolation = 'CONSTANT'
            curve.update()


def _dust_tree(name, start, drift, grain, material, ceiling):
    ng = bpy.data.node_groups.new(f'{PREFIX}{name}', 'GeometryNodeTree')
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n, link = ng.nodes, ng.links.new
    gi, go = n.new('NodeGroupInput'), n.new('NodeGroupOutput')
    points = n.new('GeometryNodeMeshToPoints'); link(gi.outputs[0], points.inputs['Mesh'])
    si, so = n.new('GeometryNodeSimulationInput'), n.new('GeometryNodeSimulationOutput'); si.pair_with_output(so)
    link(points.outputs[0], si.inputs['Geometry'])
    # before the start frame the cloud waits; afterwards each point drifts by its own seeded velocity
    frame = n.new('GeometryNodeInputSceneTime')
    after = n.new('FunctionNodeCompare'); after.data_type = 'FLOAT'; after.operation = 'GREATER_THAN'
    link(frame.outputs['Frame'], after.inputs[0]); after.inputs[1].default_value = float(start + 1)
    velocity = n.new('FunctionNodeRandomValue'); velocity.data_type = 'FLOAT_VECTOR'
    velocity.inputs['Min'].default_value = (-drift, -drift, 0.2 * drift)
    velocity.inputs['Max'].default_value = (drift, drift, drift)
    velocity.inputs['Seed'].default_value = 11
    step = n.new('ShaderNodeVectorMath'); step.operation = 'SCALE'
    link(velocity.outputs[0], step.inputs[0]); link(si.outputs['Delta Time'], step.inputs['Scale'])
    move = n.new('GeometryNodeSetPosition')
    link(si.outputs['Geometry'], move.inputs['Geometry']); link(after.outputs[0], move.inputs['Selection'])
    link(step.outputs[0], move.inputs['Offset'])
    # a speck that rises past the ceiling is gone: it never drifts out of the opening into the sky (measured: s01
    # v0018 dust specks over the street - points kept rising 1.4 m past the region top within the shot)
    position = n.new('GeometryNodeInputPosition')
    split = n.new('ShaderNodeSeparateXYZ'); link(position.outputs[0], split.inputs[0])
    above = n.new('FunctionNodeCompare'); above.data_type = 'FLOAT'; above.operation = 'GREATER_THAN'
    link(split.outputs['Z'], above.inputs[0]); above.inputs[1].default_value = float(ceiling)
    drop = n.new('GeometryNodeDeleteGeometry'); drop.domain = 'POINT'
    link(move.outputs[0], drop.inputs['Geometry']); link(above.outputs[0], drop.inputs['Selection'])
    link(drop.outputs[0], so.inputs['Geometry'])
    speck = n.new('GeometryNodeMeshIcoSphere'); speck.inputs['Radius'].default_value = grain; speck.inputs['Subdivisions'].default_value = 1
    paint = n.new('GeometryNodeSetMaterial'); paint.inputs['Material'].default_value = material
    link(speck.outputs[0], paint.inputs['Geometry'])
    inst = n.new('GeometryNodeInstanceOnPoints')
    link(so.outputs['Geometry'], inst.inputs['Points']); link(paint.outputs[0], inst.inputs['Instance'])
    link(inst.outputs[0], go.inputs[0])
    return ng


def dust(action, frames):
    scene = bpy.context.scene
    p, start = action['params'], action['start_frame']
    lo, hi = _region(p)
    rng = random.Random(int(p.get('seed', 0)))
    mesh = bpy.data.meshes.new(f"{PREFIX}{action['action_id']}.points")
    mesh.from_pydata([tuple(rng.uniform(lo[k], hi[k]) for k in range(3)) for _ in range(int(p['count']))], [], [])
    host = bpy.data.objects.new(f"{PREFIX}{action['action_id']}", mesh)
    scene.collection.objects.link(host)
    host[ROLE] = 'atmosphere'
    host['studio_id'] = host.name
    host[OWNER] = action['action_id']
    material = _material(f'{PREFIX}dust', p.get('color_srgb', [0.62, 0.58, 0.52]))
    modifier = host.modifiers.new('dust', 'NODES')
    ceiling = float(p.get('ceiling_z', hi[2]))
    modifier.node_group = _dust_tree(action['action_id'], start, float(p.get('drift_mps', 0.6)), float(p.get('grain_m', 0.03)), material, ceiling)
    bake = modifier.bakes[0]
    bake.bake_target = 'PACKED'
    bake.use_custom_simulation_frame_range, bake.frame_start, bake.frame_end = True, 1, frames
    bpy.context.view_layer.objects.active = host
    with bpy.context.temp_override(active_object=host, object=host, selected_objects=[host], selected_editable_objects=[host]):
        result = bpy.ops.object.geometry_node_bake_single(session_uid=host.session_uid, modifier_name=modifier.name, bake_id=bake.bake_id)
    return {'action_id': action['action_id'], 'kind': 'dust', 'points': int(p['count']), 'baked': 'FINISHED' in result, 'ceiling_z': ceiling,
            'bake_target': bake.bake_target, 'frames': [bake.frame_start, bake.frame_end]}


KINDS = {'rigid_debris': rigid_debris, 'dust': dust}
# Where a debris trajectory computed earlier for the same inputs is read, and where a new one is written (set by
# generate.generate from the job: read the project's cache, write into the build output; the host keeps it after a
# passing build). Measured 2026-10-07: identical inputs to the bake (every mesh and matrix at full precision) still
# gave a different trajectory now and then under load, so a trajectory is computed once per input and reused.
CACHE = {'read': None, 'write': None}


def clear(action_id):
    """Remove what an earlier application of this action made (a camera move re-applies cue-bound actions when a
    clearance repair moved the cues): every object tagged with the action, and the data only they used."""
    for obj in [o for o in bpy.data.objects if o.get(OWNER) == action_id]:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh) and data.name.startswith(f'{PREFIX}{action_id}'):
            bpy.data.meshes.remove(data)
    group = bpy.data.node_groups.get(f'{PREFIX}{action_id}')
    if group is not None and group.users == 0:
        bpy.data.node_groups.remove(group)


def apply(shot):
    """Bake every `simulate` action of the shot (frames already resolved). Returns report rows. Applying again
    replaces the earlier result."""
    rows = []
    for action in shot['actions']:
        if action['type'] != 'simulate':
            continue
        clear(action['action_id'])
        kind = action['params']['kind']
        if kind not in KINDS:
            raise ValueError(f"SIMULATE: unknown kind {kind!r} (known: {sorted(KINDS)})")
        rows.append(KINDS[kind](action, shot['duration_frames']))
    bpy.context.scene.frame_set(1)
    return rows


def check_baked(scene):
    """Problems that would let a render worker simulate (or read a cache the fingerprint does not see)."""
    problems = []
    world = scene.rigidbody_world
    if world is not None and world.enabled and any(o.rigid_body and o.rigid_body.type == 'ACTIVE' for o in scene.objects):
        cache = world.point_cache
        if not cache.is_baked:
            problems.append('rigid body world is not baked')
        if cache.use_disk_cache:
            problems.append('rigid body cache is on disk (not inside the .blend)')
    for obj in scene.objects:
        for modifier in obj.modifiers:
            if modifier.type == 'NODES':
                for bake in getattr(modifier, 'bakes', ()):
                    if bake.bake_target != 'PACKED':
                        problems.append(f'{obj.name}: simulation bake target {bake.bake_target} (must be PACKED)')
    return problems
