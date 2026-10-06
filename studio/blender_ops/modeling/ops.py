"""Shape operations a spec builder entry declares (``ops: [{op, ...}]``), baked into the part's mesh in order.

They run on the part's own mesh before its transform, so lengths are in the part's local metres. Each op ends as
plain mesh data (no live modifier is left): anchors, mirrors, linked array copies and mesh_hash all see the result,
and the build is the same every time. ``boolean.with`` is an array-style item ({builder, params, transform, ops?},
or {builder: group, params: {items}}), placed in the part's local frame and removed after the cut.

    bevel         width_m, segments (3), angle_deg (30: only edges sharper than this), profile (0.5)
    boolean       with, mode (difference | union | intersect), solver (manifold | exact)
    subdivide     levels (2), crease_angle_deg (edges sharper than this stay sharp; omit = all smooth)
    solidify      thickness_m, offset (-1: inward .. 1: outward)
    remesh_voxel  voxel_m, adaptivity (0)
    displace      strength_m, scale_m, seed (0) - noise along the vertex normals (cast or worn surfaces)
    weld          dist_m
    shade         smooth (True), sharp_angle_deg (SHARP_ANGLE_DEG)

The keys each op reads are listed in blender_ops/builder_params.py OP_PARAMS (tests/test_registry_sync.py keeps
them equal), so spec lint refuses a key no op reads.
"""
import math
import random

import bmesh
import bpy
from mathutils import Vector, noise

from .primitives import SHARP_ANGLE_DEG, apply_smoothing

BOOLEAN_MODES = {'difference': 'DIFFERENCE', 'union': 'UNION', 'intersect': 'INTERSECT'}
BOOLEAN_SOLVERS = {'manifold': 'MANIFOLD', 'exact': 'EXACT'}


def _positive(value, key):
    value = float(value)
    if not value > 0:
        raise ValueError(f'{key} must be positive (got {value})')
    return value


def _bake(obj, modifier):
    """Replace obj's mesh with its evaluated mesh (the one modifier applied), keeping every attribute layer."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
    old = obj.data
    obj.modifiers.remove(modifier)
    obj.data = mesh
    name = old.name
    if old.users == 0:
        bpy.data.meshes.remove(old)
    mesh.name = name


def bevel(obj, op, build_item):
    m = obj.modifiers.new('studio_op', 'BEVEL')
    m.width = _positive(op['width_m'], 'width_m')
    m.segments = int(op.get('segments', 3))
    m.limit_method = 'ANGLE'
    m.angle_limit = math.radians(float(op.get('angle_deg', 30.0)))
    m.profile = float(op.get('profile', 0.5))
    _bake(obj, m)


def boolean(obj, op, build_item):
    mode, solver = op.get('mode', 'difference'), op.get('solver', 'manifold')
    if mode not in BOOLEAN_MODES or solver not in BOOLEAN_SOLVERS:
        raise ValueError(f'mode must be one of {sorted(BOOLEAN_MODES)} and solver one of {sorted(BOOLEAN_SOLVERS)}')
    tools = bpy.data.collections.new(f'{obj.name}.boolean')
    bpy.context.scene.collection.children.link(tools)
    try:
        build_item(op['with'], f'{obj.name}.with', tools)
        m = obj.modifiers.new('studio_op', 'BOOLEAN')
        m.operation, m.solver = BOOLEAN_MODES[mode], BOOLEAN_SOLVERS[solver]
        m.operand_type = 'COLLECTION'
        m.collection = tools
        _bake(obj, m)
    finally:
        for o in list(tools.all_objects):
            data = o.data
            bpy.data.objects.remove(o)
            if data is not None and data.users == 0:
                bpy.data.meshes.remove(data)
        bpy.data.collections.remove(tools)
    if not obj.data.polygons:
        raise ValueError(f'{mode} left no faces (inputs must be closed meshes that overlap)')


def subdivide(obj, op, build_item):
    if 'crease_angle_deg' in op:
        limit = math.radians(float(op['crease_angle_deg']))
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        sharp = [len(e.link_faces) == 2 and e.calc_face_angle(0.0) > limit for e in bm.edges]
        bm.free()
        layer = obj.data.attributes.get('crease_edge') or obj.data.attributes.new('crease_edge', 'FLOAT', 'EDGE')
        layer.data.foreach_set('value', [1.0 if s else 0.0 for s in sharp])
    m = obj.modifiers.new('studio_op', 'SUBSURF')
    m.levels = m.render_levels = int(op.get('levels', 2))
    _bake(obj, m)
    if 'crease_edge' in obj.data.attributes:
        obj.data.attributes.remove(obj.data.attributes['crease_edge'])


def solidify(obj, op, build_item):
    m = obj.modifiers.new('studio_op', 'SOLIDIFY')
    m.thickness = _positive(op['thickness_m'], 'thickness_m')
    m.offset = float(op.get('offset', -1.0))
    m.use_even_offset = True
    _bake(obj, m)


def remesh_voxel(obj, op, build_item):
    m = obj.modifiers.new('studio_op', 'REMESH')
    m.mode = 'VOXEL'
    m.voxel_size = _positive(op['voxel_m'], 'voxel_m')
    m.adaptivity = float(op.get('adaptivity', 0.0))
    _bake(obj, m)


def displace(obj, op, build_item):
    strength, scale = float(op['strength_m']), _positive(op['scale_m'], 'scale_m')
    rng = random.Random(int(op.get('seed', 0)))
    shift = Vector([rng.uniform(-1000.0, 1000.0) for _ in range(3)])
    mesh = obj.data
    normals = [n.vector.copy() for n in mesh.vertex_normals]
    for v, n in zip(mesh.vertices, normals):
        v.co += n * (strength * noise.noise(v.co / scale + shift, noise_basis='PERLIN_ORIGINAL'))
    mesh.update()


def weld(obj, op, build_item):
    m = obj.modifiers.new('studio_op', 'WELD')
    m.merge_threshold = _positive(op['dist_m'], 'dist_m')
    _bake(obj, m)


def shade(obj, op, build_item):
    apply_smoothing(obj.data, {'smooth': op.get('smooth', True), 'sharp_angle_deg': op.get('sharp_angle_deg', SHARP_ANGLE_DEG)})


OPS = {'bevel': bevel, 'boolean': boolean, 'subdivide': subdivide, 'solidify': solidify, 'remesh_voxel': remesh_voxel,
       'displace': displace, 'weld': weld, 'shade': shade}


def apply_ops(obj, ops, build_item):
    """Bake ``ops`` into obj (a mesh object at the identity transform). ``build_item(item, name, collection)`` builds a
    boolean operand the way an array item is built. Errors name the op index so the spec entry can be found."""
    if obj.type != 'MESH':
        raise ValueError(f'{obj.name}: ops apply to a geometry part, not a {obj.type.lower()}')
    for i, op in enumerate(ops or []):
        kind = op.get('op')
        if kind not in OPS:
            raise ValueError(f'{obj.name}: ops[{i}]: unknown op {kind!r} (known: {sorted(OPS)})')
        try:
            OPS[kind](obj, op, build_item)
        except (KeyError, ValueError, TypeError, RuntimeError) as exc:
            raise ValueError(f'{obj.name}: ops[{i}] {kind}: {exc}') from exc
    return obj
