"""Turn a studio-v1 subject spec's ``builders`` list into tagged Blender objects.

Conventions
- Part object name and ``studio_id``: ``<subject_id>/<part_id>``; array items
  ``<subject_id>/<part_id>.<i>``; mirror children ``<subject_id>/<part_id>.<i>``.
- ``transform`` (location m, rotation_deg XYZ Euler, scale) is relative to the
  parent part's frame (or the subject root); parent inverse is identity.
- ``array`` params: {count, axis: 'x'|'y'|'z', center: [x, y, z],
  item: {builder, params, transform}} -> Empty part with ``count`` children,
  copy i = rotate(axis through center, i*360/count) @ item.transform.
  ``start_deg`` (0) offsets the first copy. ``pattern: 'grid'`` instead places
  copies on a lattice: {counts: [n, ...], pitch_m: [p, ...], axes: ['x', ...],
  item} (1-3 axes), copy index i is [i0, i1, ...] at sum(i_k * pitch_k * axis_k).
  ``pattern: 'path'`` places copies along a polyline: {points: [[x, y, z], ...], pitch_m, start_m (0),
  count (as many as fit), orient: 'tangent' (item +Y follows the path, +Z kept up) | 'fixed', item}.
  The item may be ``{builder: 'group', params: {items: [item, ...]}}``: one copy made of several parts
  (a railing bay = post + rail, a stair = tread + riser); groups nest, so arrays of assemblies need no
  element-specific builder.
- ``mirror`` params: {source: part_id, axis: 'x'} -> mesh(es) reflected across
  the plane axis=0 of the source's parent frame (baked into the vertices, so
  the object has a positive scale and outward normals). Empty sources (arrays)
  are mirrored per mesh child under a new Empty.
- ``asset`` params: {manifest, instance_id?} -> Empty part parenting the
  instance root returned by assets.import_prepared_asset.
- Anchors: every part stores ``studio_anchors`` (local coordinates) named
  ``<subject_id>/<part_id>/<name>`` for center, origin, the six bbox face
  centres (+x -x +y -y +z -z, of the part as placed: its own rotation applied, in
  its parent's axes) and any ``anchors: {name: [x, y, z]}`` (local) the spec
  builder declares. scene_tools.anchor_for resolves them for labels.
- ``relations`` (spec root) place parts from other parts instead of typed
  coordinates; they are solved after parenting and before mirrors, so a mirror
  copies the solved position. Types (frame = subject root, translation only):
    attach      anchor a := anchor b + offset_m
    align       anchor a matches anchor b on ``axis`` only
    through     anchor a matches anchor b on the two axes other than ``axis``
    on_surface  move a along ``axis`` ('-z' default) until anchor a touches b
    symmetric   anchor a := anchor b reflected across the plane ``axis`` = 0
"""
import hashlib
import json
import math
import re

import bpy
from mathutils import Euler, Matrix, Vector

from relations_core import relation_order, split_ref  # studio/blender_ops on sys.path

from .details import srgb_to_linear
from .loft import loft
from .primitives import SHARP_ANGLE_DEG, apply_smoothing, axis_index, box
from .profile import profile_extrude, toothed_ring
from .revolve import revolve
from .sweep import sweep
from .wall import wall
from .wing import wing

GEOMETRY = {'loft': loft, 'wing': wing, 'revolve': revolve, 'sweep': sweep, 'box': box,
            'profile': profile_extrude, 'wall': wall, 'toothed_ring': toothed_ring}
FACE_ANCHORS = {'+x': (0, 1), '-x': (0, 0), '+y': (1, 1), '-y': (1, 0), '+z': (2, 1), '-z': (2, 0)}


def _matrix(transform):
    transform = transform or {}
    loc = Vector(transform.get('location', (0, 0, 0)))
    rot = Euler([math.radians(a) for a in transform.get('rotation_deg', (0, 0, 0))], 'XYZ')
    scale = transform.get('scale', (1, 1, 1))
    if any(abs(s) < 1e-12 for s in scale):
        raise ValueError('transform scale must be non-zero')
    return Matrix.LocRotScale(loc, rot.to_quaternion(), Vector(scale))


def _link(obj, collection):
    if collection is not None and obj.name not in collection.objects:
        collection.objects.link(obj)
        for c in list(obj.users_collection):
            if c != collection:
                c.objects.unlink(obj)


def _empty(name, collection):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_size = 0.2
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def _tag(obj, subject_id, part_id, studio_id, features, dim_role='none'):
    obj['studio_id'] = studio_id
    obj['studio_part_id'] = part_id
    obj['studio_subject_id'] = subject_id
    obj['studio_features'] = json.dumps(list(features or []))
    obj['studio_dim_role'] = dim_role or 'none'


def _set_parent(child, parent):
    child.parent = parent
    child.matrix_parent_inverse = Matrix.Identity(4)


def _geometry(builder, name, params, collection):
    if builder not in GEOMETRY:
        raise ValueError(f'{name}: builder {builder!r} cannot be used here')
    obj = GEOMETRY[builder](name, params)
    _link(obj, collection)
    return obj


def _mirror_mesh(src, matrix, name, collection, reflect):
    """Copy src mesh with vertices mapped by reflect @ matrix, faces reversed when det < 0."""
    m = reflect @ matrix
    verts = [tuple(m @ v.co) for v in src.data.vertices]
    faces = [tuple(p.vertices) for p in src.data.polygons]
    if m.determinant() < 0:
        faces = [tuple(reversed(f)) for f in faces]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for mat in src.data.materials:
        mesh.materials.append(mat)
    smooth = any(p.use_smooth for p in src.data.polygons)
    apply_smoothing(mesh, {'smooth': smooth, 'sharp_angle_deg': src.get('studio_sharp_angle_deg', SHARP_ANGLE_DEG)})
    obj = bpy.data.objects.new(name, mesh)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def _path_offsets(name, params):
    pts = [Vector(p) for p in params['points']]
    if len(pts) < 2 or float(params['pitch_m']) <= 0:
        raise ValueError(f'{name}: path array needs >= 2 points and pitch_m > 0')
    seg = [(a, b, (b - a).length) for a, b in zip(pts, pts[1:]) if (b - a).length > 1e-9]
    total = sum(length for _, _, length in seg)
    start, pitch = float(params.get('start_m', 0.0)), float(params['pitch_m'])
    count = int(params['count']) if 'count' in params else int(math.floor((total - start) / pitch + 1e-9)) + 1
    if count < 1 or start + (count - 1) * pitch > total + 1e-6:
        raise ValueError(f'{name}: path array of {count} x {pitch} m from {start} m exceeds the path ({total:.3f} m)')
    out = []
    for i in range(count):
        s, run = start + i * pitch, 0.0
        for a, b, length in seg:
            if s <= run + length + 1e-9:
                t = (s - run) / length
                point, tangent = a.lerp(b, t), (b - a).normalized()
                break
            run += length
        else:  # rounding put the copy a hair past the end (validated above): it sits at the end
            a, b, _ = seg[-1]
            point, tangent = b.copy(), (b - a).normalized()
        if params.get('orient', 'tangent') == 'tangent':
            rotation = tangent.to_track_quat('Y', 'Z').to_matrix().to_4x4()
        else:
            rotation = Matrix.Identity(4)
        out.append((i, Matrix.Translation(point) @ rotation))
    return out


def _item(item, name, collection, subject_id, part_id, features, dim_role):
    """One array copy: a geometry builder, or a 'group' Empty holding several (nested) items."""
    if item['builder'] != 'group':
        return _geometry(item['builder'], name, item.get('params', {}), collection)
    group = _empty(name, collection)
    for j, sub in enumerate(item['params']['items']):
        child = _item(sub, f'{name}.{j}', collection, subject_id, part_id, features, dim_role)
        child.matrix_basis = _matrix(sub.get('transform'))
        _set_parent(child, group)
        _tag(child, subject_id, part_id, f'{name}.{j}', features, dim_role)
    return group


def _clone(template, name, collection, subject_id, part_id, features, dim_role):
    """A copy of an array item sharing the template's mesh data (groups: the whole tree, child order kept)."""
    if template.type == 'MESH':
        obj = bpy.data.objects.new(name, template.data)
        (collection or bpy.context.scene.collection).objects.link(obj)
        return obj
    group = _empty(name, collection)
    for j, child in enumerate(sorted(template.children, key=lambda o: o.name)):
        copy = _clone(child, f'{name}.{j}', collection, subject_id, part_id, features, dim_role)
        copy.matrix_basis = child.matrix_basis.copy()
        _set_parent(copy, group)
        _tag(copy, subject_id, part_id, f'{name}.{j}', features, dim_role)
    return group


def _array_offsets(name, params):
    """Matrices placing each array copy (before the item's own transform), with their index labels."""
    if params.get('pattern') == 'path':
        return _path_offsets(name, params)
    if params.get('pattern', 'rotational') == 'grid':
        counts, pitch, axes = list(params['counts']), list(params['pitch_m']), list(params.get('axes', ['x', 'y', 'z'][:len(params['counts'])]))
        if not 1 <= len(counts) <= 3 or len(pitch) != len(counts) or len(axes) != len(counts) or min(int(c) for c in counts) < 1:
            raise ValueError(f'{name}: grid array needs equal-length counts (>=1), pitch_m and axes (1-3 entries)')
        origin = Vector(params.get('center', (0, 0, 0)))
        out = [((), origin.copy())]
        for count, step, axis in zip(counts, pitch, axes):
            direction = Vector((0.0, 0.0, 0.0)); direction[axis_index(axis)] = float(step)
            out = [(index + (i,), offset + direction * i) for index, offset in out for i in range(int(count))]
        return [(list(index), Matrix.Translation(offset)) for index, offset in out]
    count = int(params['count'])
    if count < 1:
        raise ValueError(f'{name}: array count must be >= 1')
    ax = Vector([0.0, 0.0, 0.0])
    ax[axis_index(params.get('axis', 'z'))] = 1.0
    center = Vector(params.get('center', (0, 0, 0)))
    start = math.radians(float(params.get('start_deg', 0.0)))
    return [(i, Matrix.Translation(center) @ Matrix.Rotation(start + 2 * math.pi * i / count, 4, ax)) for i in range(count)]


def _local_points(obj, matrix=None):
    """Vertices of obj and its descendants in obj's local frame (Empties contribute their mesh children)."""
    matrix = matrix if matrix is not None else Matrix.Identity(4)
    points = [matrix @ v.co for v in obj.data.vertices] if obj.type == 'MESH' else []
    for child in obj.children:
        points += _local_points(child, matrix @ child.matrix_basis)
    return points


def _write_anchors(obj, subject_id, part_id, declared=None):
    """Face anchors are the faces of the part as placed (its own rotation/scale applied, i.e. in its parent's
    axes), so 'plate/-z' is the plate's underside after it was rotated flat; stored in local coordinates."""
    basis = obj.matrix_basis.copy()
    to_local = basis.inverted()
    points = [basis @ p for p in _local_points(obj)]
    anchors = {'origin': [0.0, 0.0, 0.0]}
    if points:
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        center = [(a + b) / 2 for a, b in zip(lo, hi)]
        anchors['center'] = list(to_local @ Vector(center))
        for name, (axis, side) in FACE_ANCHORS.items():
            point = list(center)
            point[axis] = hi[axis] if side else lo[axis]
            anchors[name] = list(to_local @ Vector(point))
    for name, point in (declared or {}).items():
        anchors[name] = [float(c) for c in point]
    obj['studio_anchors'] = json.dumps({f'{subject_id}/{part_id}/{k}': [round(c, 9) for c in v] for k, v in sorted(anchors.items())})


def build_part(spec_builder, subject_id, parts=None, collection=None):
    """Build one builder entry; returns the part object (mesh or Empty), untransformed by parents.

    ``parts`` maps already-built part_id -> object (needed by ``mirror``).
    The part's own transform is applied; parenting is done by build_subject.
    """
    parts = parts if parts is not None else {}
    part_id, builder = spec_builder['part_id'], spec_builder['builder']
    params = spec_builder.get('params', {})
    name = f'{subject_id}/{part_id}'
    features = spec_builder.get('features', [])
    dim_role = spec_builder.get('dim_role', 'none')
    children = []
    if builder in GEOMETRY:
        obj = _geometry(builder, name, params, collection)
        obj['studio_sharp_angle_deg'] = float(params.get('sharp_angle_deg', SHARP_ANGLE_DEG))
    elif builder == 'array':
        item = params['item']
        obj = _empty(name, collection)
        template = None
        for index, placement in _array_offsets(name, params):
            label = index if isinstance(index, int) else '_'.join(str(i) for i in index)
            child_name = f'{name}.{label}'
            # one mesh per distinct item, shared by every copy (linked duplicates): edits go through mesh_data.unique_data
            child = (_item(item, child_name, collection, subject_id, part_id, features, dim_role) if template is None
                     else _clone(template, child_name, collection, subject_id, part_id, features, dim_role))
            template = template or child
            child.matrix_basis = placement @ _matrix(item.get('transform'))
            _set_parent(child, obj)
            _tag(child, subject_id, part_id, child_name, features, dim_role)
            child['studio_array_index'] = index
            children.append(child)
    elif builder == 'mirror':
        src = parts.get(params['source'])
        if src is None:
            raise ValueError(f'{name}: mirror source {params["source"]!r} not built yet')
        reflect = Matrix.Identity(4)
        idx = axis_index(params.get('axis', 'x'))
        reflect[idx][idx] = -1.0
        if src.type == 'MESH':
            obj = _mirror_mesh(src, src.matrix_basis, name, collection, reflect)
        else:
            obj = _empty(name, collection)
            stack = [(c, src.matrix_basis @ c.matrix_basis) for c in sorted(src.children, key=lambda o: o.name)]
            i = 0
            while stack:
                child, mat = stack.pop(0)
                if child.type == 'MESH':
                    child_name = f'{name}.{i}'
                    m = _mirror_mesh(child, mat, child_name, collection, reflect)
                    _set_parent(m, obj)
                    _tag(m, subject_id, part_id, child_name, features, dim_role)
                    children.append(m)
                    i += 1
                stack.extend((c, mat @ c.matrix_basis) for c in sorted(child.children, key=lambda o: o.name))
        obj['studio_mirror_of'] = params['source']
    elif builder == 'asset':
        from assets import import_prepared_asset  # studio/blender_ops on sys.path
        obj = _empty(name, collection)
        instance_id = params.get('instance_id') or re.sub(r'[^a-z0-9_-]', '-', f'{subject_id}-{part_id}')
        result = import_prepared_asset(params['manifest'], instance_id)
        root = bpy.data.objects[result['instance_root_id']]
        _set_parent(root, obj)
        obj['studio_asset_import'] = json.dumps(result, sort_keys=True)
    else:
        raise ValueError(f'{name}: unknown builder {builder!r}')
    _tag(obj, subject_id, part_id, name, features, dim_role)
    obj.matrix_basis = _matrix(spec_builder.get('transform'))
    obj['studio_children'] = json.dumps([c['studio_id'] for c in children])
    _write_anchors(obj, subject_id, part_id, spec_builder.get('anchors'))
    return obj


def _ordered(builders):
    """Spec order, except a mirror waits for its source."""
    pending, done, out = list(builders), set(), []
    while pending:
        progressed = False
        for b in list(pending):
            src = b.get('params', {}).get('source') if b['builder'] == 'mirror' else None
            if src is None or src in done:
                out.append(b)
                done.add(b['part_id'])
                pending.remove(b)
                progressed = True
        if not progressed:
            raise ValueError(f'unresolved mirror sources: {[b["part_id"] for b in pending]}')
    return out


def _part_meshes(obj):
    out = [obj] if obj.type == 'MESH' else []
    for child in sorted(obj.children, key=lambda o: o.name):
        if child.get('studio_part_id') == obj.get('studio_part_id'):
            out.extend(_part_meshes(child))
    return out


def _material(spec_mat, name):
    """Get-or-create, then set: re-applying a spec never leaves .001 copies behind."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    if 'color_srgb' in spec_mat:
        lin = [srgb_to_linear(c) for c in spec_mat['color_srgb']]
        bsdf.inputs['Base Color'].default_value = (*lin, 1.0)
        mat.diffuse_color = (*lin, 1.0)
    if 'metallic' in spec_mat:
        bsdf.inputs['Metallic'].default_value = float(spec_mat['metallic'])
    if 'roughness' in spec_mat:
        bsdf.inputs['Roughness'].default_value = float(spec_mat['roughness'])
    if spec_mat.get('emission_strength'):  # light fixtures: emits its own colour (or emission_color_srgb)
        colour = spec_mat.get('emission_color_srgb')
        bsdf.inputs['Emission Color'].default_value = (*[srgb_to_linear(c) for c in colour], 1.0) if colour else \
            bsdf.inputs['Base Color'].default_value
        bsdf.inputs['Emission Strength'].default_value = float(spec_mat['emission_strength'])
    return mat


def _parent_of(spec, builder):
    parent = builder.get('parent')
    if parent is None and builder['builder'] == 'mirror':  # same frame as the source by default
        parent = next((s.get('parent') for s in spec['builders'] if s['part_id'] == builder['params']['source']), None)
    return parent


def _apply_materials(spec, parts, only=None):
    catalog = []
    subject_id = spec['subject_id']
    for i, m in enumerate(spec.get('materials', [])):
        unknown = [p for p in m['part_ids'] if p not in parts]
        if unknown:
            raise ValueError(f'material {i} names unknown parts {unknown}')
        if m.get('catalog_key'):
            catalog.append({'part_ids': list(m['part_ids']), 'catalog_key': m['catalog_key']})
            continue
        if m.get('shader'):   # environment shaders (window grids, emissive heads): variation lives in the shader
            from env_materials import make
            mat = make(m['shader']['kind'], f'{subject_id}/material.{i}', m['shader'].get('params'))
        elif any(k in m for k in ('color_srgb', 'metallic', 'roughness', 'emission_strength')):
            mat = _material(m, f'{subject_id}/material.{i}')
        else:
            mat = None
        for pid in m['part_ids']:
            if only is not None and pid not in only:
                continue
            for mesh_obj in _part_meshes(parts[pid]):
                if mat is not None:
                    mesh_obj.data.materials.clear()
                    mesh_obj.data.materials.append(mat)
                if m.get('scene_role'):
                    mesh_obj['studio_scene_role'] = m['scene_role']
    return catalog


# ---- relations -----------------------------------------------------------------------------------

def _anchor_world(obj, subject_id, part_id, anchor):
    anchors = json.loads(obj.get('studio_anchors', '{}'))
    key = f'{subject_id}/{part_id}/{anchor}'
    if key not in anchors:
        raise ValueError(f'unknown anchor {part_id}/{anchor} (have {sorted(k.rsplit("/", 1)[1] for k in anchors)})')
    return obj.matrix_world @ Vector(anchors[key])


def _surface_hit(objs, origin, direction, far=1e4):
    """Signed distance along ``direction`` from origin to the first surface of objs met when coming from
    the far side opposite to ``direction`` (the face a dropped part lands on, even if it starts inside)."""
    from mathutils.bvhtree import BVHTree
    depsgraph = bpy.context.evaluated_depsgraph_get()
    start = origin - direction * far
    best = None
    for obj in objs:
        evaluated = obj.evaluated_get(depsgraph)
        tree = BVHTree.FromObject(evaluated, depsgraph)
        inverse = obj.matrix_world.inverted()
        local_dir = (inverse.to_3x3() @ direction).normalized()
        hit = tree.ray_cast(inverse @ start, local_dir)[0]
        if hit is not None:  # re-cast from just before the hit: a ray from far away carries float32 error (~far * 6e-8)
            near = tree.ray_cast(hit - local_dir * 1e-2, local_dir)[0]
            hit = near if near is not None else hit
            along = (obj.matrix_world @ hit - origin).dot(direction)
            if best is None or along < best:
                best = along
    return best


def _solve_relations(spec, parts, root):
    """Translate parts so the declared relations hold (root frame). Returns per-relation residuals (m)."""
    subject_id = spec['subject_id']
    to_root = root.matrix_world.inverted()
    residuals = []
    for r in relation_order(spec):
        (pa, aa), (pb, ab) = split_ref(r['a']), split_ref(r['b'])
        if pa not in parts or pb not in parts:
            raise ValueError(f'relation {r["type"]}: unknown part {pa if pa not in parts else pb}')
        bpy.context.view_layer.update()
        a_obj, b_obj = parts[pa], parts[pb]
        a_pt = to_root @ _anchor_world(a_obj, subject_id, pa, aa)
        b_pt = to_root @ _anchor_world(b_obj, subject_id, pb, ab)
        offset = Vector(r.get('offset_m', (0, 0, 0)))
        kind = r['type']
        if kind == 'attach':
            delta = b_pt + offset - a_pt
        elif kind == 'align':
            delta = Vector((0, 0, 0)); i = axis_index(r['axis']); delta[i] = b_pt[i] + offset[i] - a_pt[i]
        elif kind == 'through':
            i = axis_index(r['axis']); delta = b_pt + offset - a_pt; delta[i] = 0.0
        elif kind == 'symmetric':
            i = axis_index(r.get('axis', 'x')); mirrored = b_pt.copy(); mirrored[i] = -mirrored[i]
            delta = mirrored + offset - a_pt
        elif kind == 'on_surface':
            axis = r.get('axis', '-z')
            direction = Vector((0.0, 0.0, 0.0)); direction[axis_index(axis.lstrip('+-'))] = -1.0 if axis.startswith('-') else 1.0
            world_dir = (root.matrix_world.to_3x3() @ direction).normalized()
            along = _surface_hit(_part_meshes(b_obj), root.matrix_world @ a_pt, world_dir)
            if along is None:
                raise ValueError(f'relation on_surface: {pa} anchor does not project onto {pb} along {axis}')
            delta = (to_root.to_3x3() @ (world_dir * along)) + offset
        else:
            raise ValueError(f'unknown relation type {kind!r}')
        world_delta = root.matrix_world.to_3x3() @ delta
        new_world = Matrix.Translation(world_delta) @ a_obj.matrix_world
        parent = a_obj.parent
        a_obj.matrix_basis = (parent.matrix_world.inverted() @ new_world) if parent else new_world
        residuals.append({'type': kind, 'a': r['a'], 'b': r['b'], 'moved_m': round(delta.length, 6)})
    bpy.context.view_layer.update()
    return residuals


# ---- subject lifecycle ---------------------------------------------------------------------------

def _subject_root(subject_id):
    return next((o for o in bpy.data.objects if o.get('studio_id') == subject_id and o.get('studio_subject_id') == subject_id
                 and not o.get('studio_part_id')), None)


def _remove_tree(obj, keep=lambda o: False):
    """Delete obj and its descendants except subtrees for which keep(o) is true; returns kept roots."""
    kept, doomed, stack = [], [], [obj]
    while stack:
        cur = stack.pop()
        doomed.append(cur)
        for child in cur.children:
            (kept if keep(child) else stack).append(child)
    meshes = sorted({o.data.name for o in doomed if o.type == 'MESH'})  # by name: copies share one mesh
    for child in kept:
        world = child.matrix_world.copy()
        child.parent = None
        child.matrix_world = world
    for o in doomed:
        bpy.data.objects.remove(o, do_unlink=True)
    for name in meshes:
        mesh = bpy.data.meshes.get(name)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return kept


def remove_subject(subject_id):
    """Delete a built subject: its root, parts, imported asset trees, meshes and spec materials."""
    removed = 0
    root = _subject_root(subject_id)
    if root is not None:
        before = len(bpy.data.objects)
        _remove_tree(root)
        removed += before - len(bpy.data.objects)
    while True:  # orphaned parts of a partially built subject
        obj = next((o for o in bpy.data.objects if o.get('studio_subject_id') == subject_id), None)
        if obj is None:
            break
        before = len(bpy.data.objects)
        _remove_tree(obj)
        removed += before - len(bpy.data.objects)
    for mat in [m for m in bpy.data.materials if m.name.startswith(f'{subject_id}/material.') and m.users == 0]:
        bpy.data.materials.remove(mat)
    return removed


def scene_parts(subject_id):
    """{part_id: part object} for a subject already in the scene."""
    return {o['studio_part_id']: o for o in bpy.data.objects
            if o.get('studio_subject_id') == subject_id and o.get('studio_id') == f"{subject_id}/{o.get('studio_part_id')}"}


def subject_summary(spec):
    """What a spec-built subject is, in the words a prompt needs: identity, features and the builder kinds that
    shape each part (generative.clip turns builders into clay-shape words). Written on the subject root and
    collected per version in subjects_index.json, so a scene without shot.subjects still describes itself."""
    def slim(b):
        params = b.get('params') or {}
        kept = {k: params[k] for k in ('count', 'counts', 'source') if k in params}
        if isinstance(params.get('item'), dict):
            kept['item'] = {'builder': params['item'].get('builder')}
        return {'part_id': b['part_id'], 'builder': b['builder'], 'params': kept}
    return {'identity': spec.get('identity', spec.get('subject_id')), 'features': [{'description': f['description'], 'part_ids': f['part_ids']}
            for f in spec.get('features', [])], 'builders': [slim(b) for b in spec.get('builders', [])]}


def build_subject(spec, root_location=(0, 0, 0), collection=None, replace=False):
    """Build every builder of ``spec``.

    Order: geometry (non-mirror) -> parenting -> relations -> mirrors -> materials.
    ``replace`` deletes an existing build of the same subject first (no .001 duplicates).
    Returns {'root', 'parts': {part_id: obj}, 'catalog_materials': [...], 'relations': [...]}.
    Materials with catalog_key are NOT applied here (use look_materials); materials with
    color_srgb / metallic / roughness become simple Principled materials.
    """
    subject_id = spec['subject_id']
    if replace:
        remove_subject(subject_id)
    elif _subject_root(subject_id) is not None:
        raise ValueError(f'subject {subject_id!r} is already built; pass replace=True to rebuild it')
    root = _empty(subject_id, collection)
    root['studio_id'] = subject_id
    root['studio_subject_id'] = subject_id
    root['studio_subject_summary'] = json.dumps(subject_summary(spec), ensure_ascii=False)   # what it is, for prompts
    root.location = root_location
    parts = {}
    ordered = _ordered(spec['builders'])
    for b in ordered:
        if b['part_id'] in parts:
            raise ValueError(f'duplicate part_id {b["part_id"]!r}')
        if b['builder'] != 'mirror':
            parts[b['part_id']] = build_part(b, subject_id, parts, collection)
    _parent_parts(spec, parts, root, [b for b in ordered if b['builder'] != 'mirror'])
    relations = _solve_relations(spec, parts, root) if spec.get('relations') else []
    mirrors = [b for b in ordered if b['builder'] == 'mirror']
    for b in mirrors:
        parts[b['part_id']] = build_part(b, subject_id, parts, collection)
        _parent_parts(spec, parts, root, [b])
    catalog = _apply_materials(spec, parts)
    bpy.context.view_layer.update()
    if spec.get('joints'):   # a mechanism: pivots that drive actions turn (kinematics.py)
        from kinematics import rig_subject
        rig_subject(spec, root, parts)
    return {'root': root, 'parts': parts, 'catalog_materials': catalog, 'relations': relations}


def _parent_parts(spec, parts, root, builders):
    for b in builders:
        parent = _parent_of(spec, b)
        if parent is not None and parent not in parts:
            raise ValueError(f'{b["part_id"]}: unknown parent {parent!r}')
        _set_parent(parts[b['part_id']], parts[parent] if parent else root)
    bpy.context.view_layer.update()


def rebuild_parts(spec, part_ids):
    """Rebuild only ``part_ids`` (plus mirrors that copy them) of a subject already in the scene.

    Children belonging to other parts are re-parented to the new objects with their local placement kept;
    relations are re-solved (they are idempotent) and materials re-applied to the rebuilt parts.
    """
    subject_id = spec['subject_id']
    root = _subject_root(subject_id)
    if root is None:
        raise ValueError(f'subject {subject_id!r} is not built')
    by_id = {b['part_id']: b for b in spec['builders']}
    unknown = [p for p in part_ids if p not in by_id]
    if unknown:
        raise ValueError(f'unknown parts {unknown}')
    targets = set(part_ids)
    changed = True
    while changed:  # mirrors of rebuilt parts (and mirrors of those) follow
        changed = False
        for b in spec['builders']:
            if b['builder'] == 'mirror' and b['params']['source'] in targets and b['part_id'] not in targets:
                targets.add(b['part_id']); changed = True
    parts = scene_parts(subject_id)
    orphans = {}
    for pid in sorted(targets):
        obj = parts.pop(pid, None)
        if obj is None:
            continue
        locals_ = {c.name: c.matrix_basis.copy() for c in obj.children}
        for child in _remove_tree(obj, keep=lambda o, pid=pid: o.get('studio_subject_id') == subject_id
                                  and o.get('studio_part_id') not in (None, pid)):
            orphans[child.name] = (child, locals_.get(child.name))
    ordered = _ordered(spec['builders'])
    fresh = [b for b in ordered if b['part_id'] in targets and b['builder'] != 'mirror']
    for b in fresh:
        parts[b['part_id']] = build_part(b, subject_id, parts)
    _parent_parts(spec, parts, root, fresh)
    for child, local in orphans.values():
        parent = _parent_of(spec, by_id[child['studio_part_id']]) if child['studio_part_id'] in by_id else None
        _set_parent(child, parts[parent] if parent else root)
        if local is not None:
            child.matrix_basis = local
    bpy.context.view_layer.update()
    relations = _solve_relations(spec, parts, root) if spec.get('relations') else []
    for b in ordered:
        if b['part_id'] in targets and b['builder'] == 'mirror':
            parts[b['part_id']] = build_part(b, subject_id, parts)
            _parent_parts(spec, parts, root, [b])
    _apply_materials(spec, parts, only=targets)
    bpy.context.view_layer.update()
    return {'root': root, 'parts': parts, 'rebuilt': sorted(targets), 'relations': relations}


def mesh_hash(objs, digits=5):
    """sha256 of world-space vertices (rounded) + face indices, ordered by studio_id (or name)."""
    bpy.context.view_layer.update()
    digest = hashlib.sha256()
    meshes = [o for o in objs if o.type == 'MESH']
    for obj in sorted(meshes, key=lambda o: (o.get('studio_id') or o.name, o.name)):
        digest.update(str(obj.get('studio_id') or obj.name).encode())
        mw = obj.matrix_world
        fmt = f'%.{digits}f'
        for v in obj.data.vertices:
            w = mw @ v.co
            digest.update(','.join(fmt % (c + 0.0 if abs(c) >= 0.5 * 10 ** -digits else 0.0) for c in w).encode())
        for p in obj.data.polygons:
            digest.update(('f' + ','.join(str(i) for i in p.vertices)).encode())
    return digest.hexdigest()
