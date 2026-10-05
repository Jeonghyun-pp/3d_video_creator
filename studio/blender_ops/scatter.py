"""Scatter: background detail (people, trees, rubble, fixtures) as Geometry Nodes instances.

scatter(name, sources, surface=..., density=...) or scatter(name, sources, points=[...]) builds one host object
(studio_scene_role 'scatter') whose node group places instances of the sources - objects or whole collections
(a worker made of parts) - with fixed seeds: same arguments, same instances, every build. Sources move to an
excluded collection (role 'scatter_source'), so only instances render. Instances are seen by everything that
asks scene_geometry (clearance, control depth range, lit bounds) and by ray casts (as their host).

Background only: subjects that fidelity measures are built from specs (subject_fidelity.md). Do not add a
boolean or bevel after the host's node modifier - an ordinary modifier realizes every instance.
"""
from __future__ import annotations

import hashlib
import json
import math

import bmesh
import bpy

ROLE = 'studio_scene_role'
SOURCES = 'StudioScatterSources'


def _source_root():
    root = bpy.data.collections.get(SOURCES)
    if root is None:
        root = bpy.data.collections.new(SOURCES)
        bpy.context.scene.collection.children.link(root)
    for layer in bpy.context.scene.view_layers:  # sources never render themselves, only as instances
        child = layer.layer_collection.children.get(SOURCES)
        if child is not None:
            child.exclude = True
    return root


def _collect(name, sources):
    """One child collection per source (an object, or a collection whose objects form one instance)."""
    group = bpy.data.collections.new(f'scatter_src.{name}')
    _source_root().children.link(group)
    for i, source in enumerate(sources):
        item = bpy.data.collections.new(f'scatter_src.{name}.{i}')
        group.children.link(item)
        objects = list(source.all_objects) if isinstance(source, bpy.types.Collection) else [source, *source.children_recursive]
        for obj in objects:
            if obj.get(ROLE) != 'scatter_source':  # first use: leave the scene; reuse by another scatter: just link
                for col in list(obj.users_collection):
                    col.objects.unlink(obj)
            item.objects.link(obj)
            if obj.get(ROLE) not in (None, 'scatter_source'):   # keep what the part is (a lamp head stays a light fixture)
                obj['studio_source_role'] = obj[ROLE]
            obj[ROLE] = 'scatter_source'
        if isinstance(source, bpy.types.Object):  # instance origin = the source's own origin
            item.instance_offset = source.matrix_world.translation.copy()
        else:
            roots = [o for o in objects if o.parent is None]
            item.instance_offset = roots[0].matrix_world.translation.copy() if roots else (0, 0, 0)
    for layer in bpy.context.scene.view_layers:
        layer.update()
    return group


POINT_ATTRIBUTES = {'rot_z': 'FLOAT', 'scale': 'FLOAT', 'scale_xyz': 'FLOAT_VECTOR', 'source_index': 'INT'}   # per-point overrides of the random draws


def _points_host(name, points, attributes=None):
    mesh = bpy.data.meshes.new(f'scatter.{name}')
    mesh.from_pydata([tuple(p) for p in points], [], [])
    for key, values in (attributes or {}).items():
        if key not in POINT_ATTRIBUTES:
            raise ValueError(f'SCATTER: unknown point attribute {key!r} (known: {sorted(POINT_ATTRIBUTES)})')
        if len(values) != len(points):
            raise ValueError(f'SCATTER: attribute {key} has {len(values)} values for {len(points)} points')
        attr = mesh.attributes.new(f'studio_{key}', POINT_ATTRIBUTES[key], 'POINT')
        if POINT_ATTRIBUTES[key] == 'FLOAT_VECTOR':
            attr.data.foreach_set('vector', [float(c) for v in values for c in v])
        else:
            attr.data.foreach_set('value', [int(v) for v in values] if POINT_ATTRIBUTES[key] == 'INT' else [float(v) for v in values])
    return mesh


def _tree(name, mode, seed, density, scale, rotation_deg, align, realize, group, given=()):
    ng = bpy.data.node_groups.new(f'StudioScatter_{name}', 'GeometryNodeTree')
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n, link = ng.nodes, ng.links.new
    gi, go = n.new('NodeGroupInput'), n.new('NodeGroupOutput')
    if mode == 'surface':
        pts = n.new('GeometryNodeDistributePointsOnFaces')
        pts.inputs['Density'].default_value = float(density)
        pts.inputs['Seed'].default_value = int(seed)
        link(gi.outputs[0], pts.inputs['Mesh'])
        points, normal = pts.outputs['Points'], pts.outputs['Rotation']
    else:
        pts = n.new('GeometryNodeMeshToPoints')
        link(gi.outputs[0], pts.inputs['Mesh'])
        points, normal = pts.outputs[0], None
    info = n.new('GeometryNodeCollectionInfo')
    info.inputs['Collection'].default_value = group
    info.inputs['Separate Children'].default_value = True
    info.inputs['Reset Children'].default_value = True
    inst = n.new('GeometryNodeInstanceOnPoints')
    inst.inputs['Pick Instance'].default_value = True
    link(points, inst.inputs['Points']); link(info.outputs[0], inst.inputs['Instance'])
    def random(kind, low, high, offset):
        node = n.new('FunctionNodeRandomValue'); node.data_type = kind
        node.inputs['Min'].default_value, node.inputs['Max'].default_value = low, high
        node.inputs['Seed'].default_value = int(seed) + offset
        return node.outputs[0]  # 5.x: one output, typed by data_type

    def named(key, kind):   # a per-point value computed by the caller (env_fill): along-a-path headings, weighted picks
        node = n.new('GeometryNodeInputNamedAttribute'); node.data_type = kind
        node.inputs['Name'].default_value = f'studio_{key}'
        return node.outputs['Attribute']

    link(named('source_index', 'INT') if 'source_index' in given else random('INT', 0, max(0, len(group.children) - 1), 1),
         inst.inputs['Instance Index'])
    euler = n.new('ShaderNodeCombineXYZ')
    link(named('rot_z', 'FLOAT') if 'rot_z' in given else random('FLOAT', math.radians(rotation_deg[0]), math.radians(rotation_deg[1]), 2),
         euler.inputs['Z'])
    rotate = n.new('FunctionNodeEulerToRotation')
    link(euler.outputs[0], rotate.inputs[0])
    if align and normal is not None:
        both = n.new('FunctionNodeRotateRotation')
        link(normal, both.inputs[0]); link(rotate.outputs[0], both.inputs[1])
        link(both.outputs[0], inst.inputs['Rotation'])
    else:
        link(rotate.outputs[0], inst.inputs['Rotation'])
    if 'scale_xyz' in given:   # per-axis (lots of different footprints/heights from one template)
        link(named('scale_xyz', 'FLOAT_VECTOR'), inst.inputs['Scale'])
    else:
        link(named('scale', 'FLOAT') if 'scale' in given else random('FLOAT', float(scale[0]), float(scale[1]), 3), inst.inputs['Scale'])
    out = inst.outputs[0]
    if realize:
        real = n.new('GeometryNodeRealizeInstances'); link(out, real.inputs[0]); out = real.outputs[0]
    link(out, go.inputs[0])
    return ng


def scatter(name, sources, *, surface=None, points=None, density=1.0, seed=0, scale=(1.0, 1.0), rotation_deg=(0.0, 360.0),
            align_to_surface=False, realize=False, attributes=None, weights=None):
    """Place instances of `sources` on `surface` (a mesh object, `density` per m2) or at `points` ([[x, y, z]]).
    Points mode only: `attributes` {rot_z: [rad], scale: [s], source_index: [i]} replaces the random draw of that
    quantity point by point; `weights` (one per source) picks sources by weight with `seed` instead of uniformly.
    Returns the host object (role 'scatter'; 'studio_scatter' records the arguments for reports)."""
    if (surface is None) == (points is None):
        raise ValueError('SCATTER: give exactly one of surface or points')
    if not sources:
        raise ValueError('SCATTER: no sources')
    group = _collect(name, sources)
    if surface is not None:
        mesh = surface.data.copy()
        host = bpy.data.objects.new(f'scatter.{name}', mesh)
        host.matrix_world = surface.matrix_world.copy()
        mode = 'surface'
    else:
        attributes = dict(attributes or {})
        if weights is not None and 'source_index' not in attributes:
            if len(weights) != len(sources):
                raise ValueError('SCATTER: one weight per source')
            import random as _random
            rng = _random.Random(f'{seed}:pick:{name}')
            attributes['source_index'] = rng.choices(range(len(sources)), weights=weights, k=len(points))
        host = bpy.data.objects.new(f'scatter.{name}', _points_host(name, points, attributes))
        mode = 'points'
    bpy.context.scene.collection.objects.link(host)
    modifier = host.modifiers.new('scatter', 'NODES')
    given = tuple(sorted((attributes or {}).keys())) if mode == 'points' else ()
    modifier.node_group = _tree(name, mode, seed, density, scale, rotation_deg, align_to_surface, realize, group, given)
    host[ROLE] = 'scatter'
    host['studio_id'] = f'scatter.{name}'
    record = {'mode': mode, 'sources': len(sources), 'seed': seed, 'density': density, 'scale': list(scale),
              'rotation_deg': list(rotation_deg), 'realize': realize, 'points': len(points) if points is not None else None}
    if given:
        record['attributes'] = list(given)
    host['studio_scatter'] = json.dumps(record, sort_keys=True)
    return host


def instance_count(host):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return sum(1 for i in depsgraph.object_instances if i.is_instance and i.parent is not None and i.parent.original is host)


def digest(host):
    """Hash of the evaluated instance placement (for determinism checks)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    rows = sorted(tuple(round(x, 5) for row in i.matrix_world for x in row) for i in depsgraph.object_instances
                  if i.is_instance and i.parent is not None and i.parent.original is host)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]
