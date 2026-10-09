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
from mathutils import Vector

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


# ---- streams (2026-10-08): particles, smoke and liquid from one simulation zone, PACKED like dust -------------------
# Points are seeded in `region` with a birth frame in [start, start + emit_frames], a velocity of `speed_mps` along
# `direction` spread by `spread`, then fall under `gravity` with `drag`, and die after `life_frames`. What the live
# points become is the kind: sparks / specks (instanced spheres, glowing with `glow`), smoke (points to a volume that
# grows with age), or liquid (points to a volume, meshed). One baked zone, no disk cache: the .blend holds it.

def _set_menu(node, name, value):
    socket = node.inputs.get(name)
    if socket is not None and hasattr(socket, 'default_value'):
        socket.default_value = value


def _stream_tree(name, p, kind, material, start, frames):
    from action_params import SIMULATE_KINDS
    # the declared defaults behind the shot's own params - a view, not a copy, so every read reaches the params as given
    # (action_params_smoke checks the table against what is read)
    from collections import ChainMap
    p = ChainMap(p, {k: v for k, v in SIMULATE_KINDS[kind].items() if v not in ('required', 'derived')})
    ng = bpy.data.node_groups.new(f'{PREFIX}{name}', 'GeometryNodeTree')
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n, link = ng.nodes, ng.links.new

    def vec(op, a, b=None, scale=None):
        node = n.new('ShaderNodeVectorMath'); node.operation = op
        link(a, node.inputs[0])
        if b is not None:
            link(b, node.inputs[1]) if not isinstance(b, tuple) else setattr(node.inputs[1], 'default_value', b)
        if scale is not None:
            link(scale, node.inputs['Scale']) if not isinstance(scale, (int, float)) else setattr(node.inputs['Scale'], 'default_value', scale)
        return node.outputs[0]

    def math_(op, a, b):
        node = n.new('ShaderNodeMath'); node.operation = op
        for i, v in enumerate((a, b)):
            link(v, node.inputs[i]) if not isinstance(v, (int, float)) else setattr(node.inputs[i], 'default_value', float(v))
        return node.outputs[0]

    def named(attr, dtype):
        node = n.new('GeometryNodeInputNamedAttribute'); node.data_type = dtype; node.inputs['Name'].default_value = attr
        return node.outputs['Attribute']

    def store(geo, attr, dtype, value, selection=None):
        node = n.new('GeometryNodeStoreNamedAttribute'); node.data_type = dtype; node.domain = 'POINT'
        node.inputs['Name'].default_value = attr
        link(geo, node.inputs['Geometry']); link(value, node.inputs['Value'])
        if selection is not None:
            link(selection, node.inputs['Selection'])
        return node.outputs[0]

    gi, go = n.new('NodeGroupInput'), n.new('NodeGroupOutput')
    points = n.new('GeometryNodeMeshToPoints'); link(gi.outputs[0], points.inputs['Mesh'])
    direction = Vector(p.get('direction', (0, 0, 1))).normalized() * float(p.get('speed_mps', 2.0))
    spread = float(p.get('spread', 0.3)) * float(p.get('speed_mps', 2.0))
    rv = n.new('FunctionNodeRandomValue'); rv.data_type = 'FLOAT_VECTOR'
    rv.inputs['Min'].default_value = tuple(direction[i] - spread for i in range(3))
    rv.inputs['Max'].default_value = tuple(direction[i] + spread for i in range(3))
    rv.inputs['Seed'].default_value = int(p.get('seed', 0)) + 3
    rb = n.new('FunctionNodeRandomValue'); rb.data_type = 'FLOAT'
    rb.inputs['Min'].default_value = float(start + 1)
    rb.inputs['Max'].default_value = float(start + 1 + int(p.get('emit_frames', max(1, frames - start))))
    rb.inputs['Seed'].default_value = int(p.get('seed', 0)) + 5
    seeded = store(store(points.outputs[0], 'vel', 'FLOAT_VECTOR', rv.outputs[0]), 'birth', 'FLOAT', rb.outputs[0])   # 5.2: one output, typed by data_type
    si, so = n.new('GeometryNodeSimulationInput'), n.new('GeometryNodeSimulationOutput'); si.pair_with_output(so)
    link(seeded, si.inputs['Geometry'])
    now = n.new('GeometryNodeInputSceneTime').outputs['Frame']
    alive = math_('GREATER_THAN', now, named('birth', 'FLOAT'))
    dt = si.outputs['Delta Time']
    gravity = n.new('FunctionNodeInputVector'); gravity.vector = tuple(p.get('gravity', (0, 0, -9.81)))
    accel = vec('SCALE', gravity.outputs[0], scale=dt)
    drag = math_('SUBTRACT', 1.0, math_('MULTIPLY', float(p.get('drag', 0.2)), dt))
    vel = vec('SCALE', vec('ADD', named('vel', 'FLOAT_VECTOR'), accel), scale=drag)
    moved = n.new('GeometryNodeSetPosition')
    link(store(si.outputs['Geometry'], 'vel', 'FLOAT_VECTOR', vel, alive), moved.inputs['Geometry'])
    link(alive, moved.inputs['Selection']); link(vec('SCALE', vel, scale=dt), moved.inputs['Offset'])
    age = math_('SUBTRACT', now, named('birth', 'FLOAT'))
    dead = n.new('GeometryNodeDeleteGeometry'); dead.domain = 'POINT'
    link(moved.outputs[0], dead.inputs['Geometry']); link(math_('GREATER_THAN', age, float(p.get('life_frames', 60))), dead.inputs['Selection'])
    link(dead.outputs[0], so.inputs['Geometry'])
    # what the live points become
    unborn = n.new('GeometryNodeDeleteGeometry'); unborn.domain = 'POINT'
    link(so.outputs['Geometry'], unborn.inputs['Geometry'])
    link(math_('LESS_THAN', n.new('GeometryNodeInputSceneTime').outputs['Frame'], named('birth', 'FLOAT')), unborn.inputs['Selection'])
    live = unborn.outputs[0]
    size = float(p.get('size_m', 0.03 if kind == 'particles' else 0.25))
    if kind == 'particles':
        speck = n.new('GeometryNodeMeshIcoSphere'); speck.inputs['Radius'].default_value = size; speck.inputs['Subdivisions'].default_value = 1
        inst = n.new('GeometryNodeInstanceOnPoints'); link(live, inst.inputs['Points']); link(speck.outputs[0], inst.inputs['Instance'])
        out = inst.outputs[0]
    else:
        grow = math_('ADD', size, math_('MULTIPLY', float(p.get('growth_m_per_s', 0.6 if kind == 'smoke' else 0.0)) / 30.0,
                                         math_('SUBTRACT', n.new('GeometryNodeInputSceneTime').outputs['Frame'], named('birth', 'FLOAT'))))
        volume = n.new('GeometryNodePointsToVolume'); link(live, volume.inputs['Points']); link(grow, volume.inputs['Radius'])
        _set_menu(volume, 'Resolution Mode', 'Size'); volume.inputs['Voxel Size'].default_value = float(p.get('voxel_m', size / 3))
        volume.inputs['Density'].default_value = float(p.get('density', 1.0))
        out = volume.outputs[0]
        if kind == 'liquid':
            mesh = n.new('GeometryNodeVolumeToMesh'); link(out, mesh.inputs['Volume'])
            _set_menu(mesh, 'Resolution Mode', 'Size'); mesh.inputs['Voxel Size'].default_value = float(p.get('voxel_m', size / 3))
            mesh.inputs['Threshold'].default_value = 0.08   # a low iso level joins neighbouring drops into one stream
            smooth = n.new('GeometryNodeSetShadeSmooth'); link(mesh.outputs[0], smooth.inputs['Geometry'])
            out = smooth.outputs[0]
    paint = n.new('GeometryNodeSetMaterial'); paint.inputs['Material'].default_value = material
    link(out, paint.inputs['Geometry']); link(paint.outputs[0], go.inputs[0])
    return ng


def _stream_material(kind, p):
    name = f"{PREFIX}{kind}_{'_'.join(str(round(c, 3)) for c in p.get('color_srgb', ()))}_{p.get('glow', 0)}"
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    rgb = tuple(p.get('color_srgb', {'smoke': (0.55, 0.55, 0.55), 'liquid': (0.6, 0.75, 0.85), 'particles': (1.0, 0.6, 0.2)}[kind]))
    if kind == 'smoke':
        nt.nodes.clear()
        out = nt.nodes.new('ShaderNodeOutputMaterial'); vol = nt.nodes.new('ShaderNodeVolumePrincipled')
        vol.inputs['Color'].default_value = (*rgb, 1)
        # wisps, not a ball: the density is the volume's own times a cloudy noise that thins toward nothing
        noise = nt.nodes.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value = 3.0; noise.inputs['Detail'].default_value = 6.0
        ramp = nt.nodes.new('ShaderNodeMapRange'); ramp.inputs['From Min'].default_value = 0.45; ramp.inputs['From Max'].default_value = 0.75
        ramp.inputs['To Max'].default_value = float(p.get('density', 2.0))
        nt.links.new(noise.outputs['Fac'], ramp.inputs['Value']); nt.links.new(ramp.outputs['Result'], vol.inputs['Density'])
        nt.links.new(vol.outputs[0], out.inputs['Volume'])
    else:
        bsdf = nt.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value = (*rgb, 1)
        if kind == 'liquid':
            bsdf.inputs['Transmission Weight'].default_value = 0.9; bsdf.inputs['Roughness'].default_value = 0.05; bsdf.inputs['IOR'].default_value = 1.33
        if p.get('glow'):
            bsdf.inputs['Emission Color'].default_value = (*rgb, 1); bsdf.inputs['Emission Strength'].default_value = float(p['glow'])
    m.diffuse_color = (*rgb, 1)
    return m


def _stream(kind):
    def run(action, frames):
        scene = bpy.context.scene
        p, start = action['params'], action['start_frame']
        lo, hi = _region(p)
        rng = random.Random(int(p.get('seed', 0)))
        mesh = bpy.data.meshes.new(f"{PREFIX}{action['action_id']}.points")
        mesh.from_pydata([tuple(rng.uniform(lo[k], hi[k]) for k in range(3)) for _ in range(int(p['count']))], [], [])
        host = bpy.data.objects.new(f"{PREFIX}{action['action_id']}", mesh)
        scene.collection.objects.link(host)
        host[ROLE] = 'atmosphere' if kind in ('smoke', 'particles') else 'simulated'
        host['studio_id'] = host.name
        host[OWNER] = action['action_id']
        modifier = host.modifiers.new(kind, 'NODES')
        modifier.node_group = _stream_tree(action['action_id'], p, kind, _stream_material(kind, p), start, frames)
        bake = modifier.bakes[0]
        bake.bake_target = 'PACKED'
        bake.use_custom_simulation_frame_range, bake.frame_start, bake.frame_end = True, 1, frames
        bpy.context.view_layer.objects.active = host
        with bpy.context.temp_override(active_object=host, object=host, selected_objects=[host], selected_editable_objects=[host]):
            result = bpy.ops.object.geometry_node_bake_single(session_uid=host.session_uid, modifier_name=modifier.name, bake_id=bake.bake_id)
        return {'action_id': action['action_id'], 'kind': kind, 'points': int(p['count']), 'baked': 'FINISHED' in result}
    return run


def cloth(action, frames):
    """A cloth sheet (`target_object_id`, a mesh) under gravity from the start frame, pinned along its top edge when
    `pin` is 'top', colliding with `collide_object_ids`; the point cache is baked in memory and saved in the .blend."""
    scene = bpy.context.scene
    p, start = action['params'], action['start_frame']
    sheet = next((o for o in scene.objects if o.get('studio_id') == p['target_object_id']), None)
    if sheet is None or sheet.type != 'MESH':
        raise ValueError(f"SIMULATE: cloth target {p['target_object_id']!r} is not a mesh in the scene")
    if p.get('subdivide', 0):
        bm = bmesh.new(); bm.from_mesh(sheet.data)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=int(p['subdivide']), use_grid_fill=True)
        bm.to_mesh(sheet.data); bm.free()
    if p.get('pin') == 'top':
        top = max(v.co.z for v in sheet.data.vertices)
        group = sheet.vertex_groups.new(name='studio_pin')
        group.add([v.index for v in sheet.data.vertices if v.co.z > top - 1e-4], 1.0, 'REPLACE')
    modifier = sheet.modifiers.new('cloth', 'CLOTH')
    settings = modifier.settings
    settings.quality = int(p.get('quality', 5))
    if p.get('pin') == 'top':
        settings.vertex_group_mass = 'studio_pin'
    for ident in p.get('collide_object_ids', []):
        other = next((o for o in scene.objects if o.get('studio_id') == ident), None)
        if other is not None and other.type == 'MESH' and not any(m.type == 'COLLISION' for m in other.modifiers):
            other.modifiers.new('collision', 'COLLISION')
    cache = modifier.point_cache
    cache.frame_start, cache.frame_end = max(1, start + 1), frames
    cache.use_disk_cache = False
    sheet[ROLE] = 'simulated'
    sheet[OWNER] = action['action_id']
    with bpy.context.temp_override(scene=scene, active_object=sheet, object=sheet, point_cache=cache):
        bpy.ops.ptcache.bake(bake=True)
    return {'action_id': action['action_id'], 'kind': 'cloth', 'vertices': len(sheet.data.vertices), 'baked': cache.is_baked}


KINDS = {'rigid_debris': rigid_debris, 'dust': dust, 'particles': _stream('particles'), 'smoke': _stream('smoke'),
         'liquid': _stream('liquid'), 'cloth': cloth}
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
            if modifier.type == 'CLOTH':
                cache = modifier.point_cache
                if not cache.is_baked:
                    problems.append(f'{obj.name}: cloth is not baked')
                if cache.use_disk_cache:
                    problems.append(f'{obj.name}: cloth cache is on disk (not inside the .blend)')
            if modifier.type == 'NODES':
                for bake in getattr(modifier, 'bakes', ()):
                    if bake.bake_target != 'PACKED':
                        problems.append(f'{obj.name}: simulation bake target {bake.bake_target} (must be PACKED)')
    return problems
