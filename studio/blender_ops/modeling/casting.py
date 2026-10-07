"""Casting: one cast or molded body from simple members, the way a pattern maker builds it - blend, fillet, round, then
machine the exact faces.

params = {
  members: [item, ...]     # array-style items {builder, params, transform, ops?} (or a group): the solid's pieces
  subtract: [item, ...]    # cored volumes (water jackets, pockets), removed before filleting so their edges round too
  voxel_m: m               # SDF resolution; features smaller than ~2 voxels are lost (start at 1/100 of the size)
  fillet_m: 0              # inner corners where members meet get this radius (closing: grow then shrink)
  round_m: 0               # outer edges get this radius (opening: shrink then grow)
  offset_m: 0              # final grow (+) or shrink (-) of the whole body
  adaptivity: 0.0..1.0     # fewer triangles on flat areas (0 = uniform; the shape is unchanged)
  cuts: [item, ...]        # machined after casting with an exact (manifold) boolean: bores, faces, flanges stay exact
  smooth: true, sharp_angle_deg: SHARP_ANGLE_DEG (31)
}
Fillets and rounds are lengths, so the part scales with its numbers. The members, subtract volumes and cuts are built
in the part's own frame and removed afterwards; the result is plain mesh data (closed, deterministic).
"""
import math

import bpy

from .ops import _bake, boolean
from .primitives import apply_smoothing


def _tree(name, members, subtract, voxel, fillet, round_, offset, adaptivity):
    tree = bpy.data.node_groups.new(f'{name}.casting', 'GeometryNodeTree')
    tree.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = tree.nodes, tree.links
    band = int(math.ceil(max(fillet, round_, abs(offset)) / voxel)) + 3   # the SDF must reach as far as the largest offset

    def sdf(obj):
        info = nodes.new('GeometryNodeObjectInfo')
        info.transform_space = 'RELATIVE'
        info.inputs['Object'].default_value = obj
        grid = nodes.new('GeometryNodeMeshToSDFGrid')
        grid.inputs['Voxel Size'].default_value = voxel
        grid.inputs['Band Width'].default_value = band
        links.new(info.outputs['Geometry'], grid.inputs['Mesh'])
        return grid.outputs['SDF Grid']

    def boolean(operation, base, grids):
        # Blender 5.2: union / intersect take every grid on one multi-input (identifier 'Grid 2'); difference is
        # 'Grid 1' minus the multi-input
        node = nodes.new('GeometryNodeSDFGridBoolean')
        node.operation = operation
        many = next(s for s in node.inputs if s.identifier == 'Grid 2')
        if base is not None:
            links.new(base, next(s for s in node.inputs if s.identifier == 'Grid 1'))
        for grid in grids:
            links.new(grid, many)
        return node.outputs['Grid']

    def grow(grid, distance):
        node = nodes.new('GeometryNodeSDFGridOffset')
        node.inputs['Distance'].default_value = distance
        links.new(grid, node.inputs['Grid'])
        return node.outputs['Grid']

    out = boolean('UNION', None, [sdf(obj) for obj in members]) if len(members) > 1 else sdf(members[0])
    if subtract:
        out = boolean('DIFFERENCE', out, [sdf(obj) for obj in subtract])
    if fillet > 0:   # closing: inner corners fill in with radius ~ fillet
        out = grow(grow(out, fillet), -fillet)
    if round_ > 0:   # opening: outer edges pull back with radius ~ round
        out = grow(grow(out, -round_), round_)
    if offset:
        out = grow(out, offset)
    mesh = nodes.new('GeometryNodeGridToMesh')
    mesh.inputs['Threshold'].default_value = 0.0   # the surface itself (the default 0.1 m shell is empty for small parts)
    mesh.inputs['Adaptivity'].default_value = adaptivity
    links.new(out, mesh.inputs['Grid'])
    links.new(mesh.outputs['Mesh'], nodes.new('NodeGroupOutput').inputs[0])
    return tree


def casting(name, params, build_item):
    """The cast body as one mesh object at the identity transform (assemble places it)."""
    members, subtract = list(params['members']), list(params.get('subtract') or [])
    if not members:
        raise ValueError(f'{name}: a casting needs at least one member')
    voxel = float(params['voxel_m'])
    fillet, round_, offset = float(params.get('fillet_m', 0.0)), float(params.get('round_m', 0.0)), float(params.get('offset_m', 0.0))
    adaptivity = float(params.get('adaptivity', 0.0))
    if voxel <= 0 or fillet < 0 or round_ < 0 or not 0.0 <= adaptivity <= 1.0:
        raise ValueError(f'{name}: voxel_m > 0, fillet_m >= 0, round_m >= 0 and 0 <= adaptivity <= 1')
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    tools = bpy.data.collections.new(f'{name}.members')
    bpy.context.scene.collection.children.link(tools)
    tree = None
    try:
        built = [build_item(item, f'{name}.member{i}', tools) for i, item in enumerate(members)]
        cored = [build_item(item, f'{name}.subtract{i}', tools) for i, item in enumerate(subtract)]
        solids = [o for o in tools.all_objects if o.type == 'MESH']
        if len(solids) != len(built) + len(cored):
            raise ValueError(f'{name}: casting members and subtract volumes are single geometry items (no groups)')
        for o in solids:
            o.hide_render = True
        tree = _tree(name, built, cored, voxel, fillet, round_, offset, adaptivity)
        modifier = obj.modifiers.new('studio_casting', 'NODES')
        modifier.node_group = tree
        _bake(obj, modifier)
    finally:
        for o in list(tools.all_objects):
            data = o.data
            bpy.data.objects.remove(o)
            if data is not None and data.users == 0:
                bpy.data.meshes.remove(data)
        bpy.data.collections.remove(tools)
        if tree is not None:
            bpy.data.node_groups.remove(tree)
    if not obj.data.polygons:
        raise ValueError(f'{name}: the casting came out empty (members must be closed meshes; voxel_m {voxel} may be too coarse)')
    for i, cut in enumerate(params.get('cuts') or []):
        try:
            boolean(obj, {'op': 'boolean', 'with': cut, 'mode': 'difference', 'solver': 'manifold'}, build_item)
        except (KeyError, ValueError, TypeError, RuntimeError) as exc:
            raise ValueError(f'{name}: cuts[{i}]: {exc}') from exc
    apply_smoothing(obj.data, params)   # smooth, with machined edges sharp by angle
    return obj
