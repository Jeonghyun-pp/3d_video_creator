"""Mechanisms in Blender: joint pivots on spec-built subjects, `drive` actions baked into keys, and a check that coupled
parts never pass through each other over the motion.

rig_subject (called by modeling.build_subject when a spec declares joints): one pivot empty per joint at its origin,
parented to the joint's parent part (or the subject root); the child part hangs from the pivot with its world place
kept. A planet riding a carrier therefore orbits with it and spins on its own pivot.

apply_drives (build_scene, after the standard actions): each drive action's inputs over its interval -> coupled joint
values (kinematics_core.solve) -> a key per frame on every pivot (LINEAR). Keys are written whole: workers render frames
in any order, and nothing is evaluated at render time.
"""
import json
import math

import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

import kinematics_core as core

SAMPLES = 12          # frames checked for interference per drive action (plus both ends)
INSET_SHARE = 0.0005  # surfaces moved inward by this share of the subject's size (0.1 mm on a 20 cm gearbox) before testing


def rig_subject(spec, root, parts):
    problems = core.check(spec.get('joints', []), spec.get('couplings', []))
    if problems:
        raise ValueError('MECHANISM: ' + '; '.join(problems))
    sid = spec['subject_id']
    for joint in spec['joints']:
        parent = root if joint['parent'] == 'root' else parts.get(joint['parent'])
        child = parts.get(joint['child'])
        if parent is None or child is None:
            raise ValueError(f"MECHANISM: joint {joint['id']} names parts that were not built ({joint['parent']}, {joint['child']})")
        pivot = bpy.data.objects.new(f"{sid}/{joint['id']}.pivot", None)
        for collection in child.users_collection:
            collection.objects.link(pivot)
        bpy.context.view_layer.update()
        world = root.matrix_world @ Matrix.Translation(Vector(joint.get('origin', (0, 0, 0))))
        pivot.parent = parent
        pivot.matrix_world = world
        pivot.rotation_mode = 'QUATERNION'
        place = child.matrix_world.copy()
        child.parent = pivot
        child.matrix_world = place
        pivot.update_tag()
        pivot['studio_id'] = f"{sid}/{joint['id']}"
        pivot['studio_subject_id'] = sid
        pivot['studio_joint'] = joint['id']
        pivot['studio_scene_role'] = 'helper'
        bpy.context.view_layer.update()
    for coupling in spec.get('couplings', []):
        if coupling['kind'] == 'harmonic':
            _wave_keys(parts.get(coupling['deform_part']), coupling)
    root['studio_mechanism'] = json.dumps({'joints': spec['joints'], 'couplings': spec.get('couplings', [])})


WAVE_KEYS = ('studio_wave_cos', 'studio_wave_sin')


def _wave_keys(part, coupling):
    """A flexspline pushed into an ellipse that turns: radial offset d cos 2(a - t) = d cos 2a cos 2t + d sin 2a sin 2t, so
    two shape keys (d cos 2a and d sin 2a along the radius, in the part's own frame about its axis z) weighted by cos 2t and
    sin 2t carry it exactly for any wave angle t; apply_drives keys the weights per frame."""
    if part is None:
        raise ValueError(f"MECHANISM: harmonic {coupling['id']} deforms a part that was not built ({coupling['deform_part']})")
    d = float(coupling['deflection_m'])
    for obj in [o for o in [part, *part.children_recursive] if o.type == 'MESH' and not o.get('studio_joint')]:
        if obj.data.shape_keys is None:
            obj.shape_key_add(name='Basis', from_mix=False)
        for name, fn in zip(WAVE_KEYS, (math.cos, math.sin)):
            key = obj.data.shape_keys.key_blocks.get(name) or obj.shape_key_add(name=name, from_mix=False)
            key.slider_min, key.slider_max = -1.0, 1.0
            for v, target in zip(obj.data.vertices, key.data):
                r = math.hypot(v.co.x, v.co.y)
                a = math.atan2(v.co.y, v.co.x)
                push = d * fn(2 * a) / r if r > 1e-12 else 0.0
                target.co = (v.co.x * (1 + push), v.co.y * (1 + push), v.co.z)


def _pivots(sid):
    return {o['studio_joint']: o for o in bpy.data.objects if o.get('studio_subject_id') == sid and o.get('studio_joint')}


def _mechanism(sid):
    root = next((o for o in bpy.data.objects if o.get('studio_subject_id') == sid and o.get('studio_mechanism')), None)
    if root is None:
        raise ValueError(f'MECHANISM: subject {sid} has no joints (declare joints in its spec)')
    return json.loads(root['studio_mechanism'])


def _set(pivot, joint, value):
    axis = Vector(joint.get('axis', (0, 0, 1))).normalized()
    if joint['type'] == 'prismatic':
        rest = Vector(pivot['studio_rest_location'])
        pivot.location = rest + axis * value
        pivot.keyframe_insert('location')
    else:
        pivot.rotation_quaternion = Quaternion(axis, math.radians(value))
        pivot.keyframe_insert('rotation_quaternion')


def _key_wave(part, turn, frame):
    for obj in _meshes(part):
        keys = obj.data.shape_keys
        if keys is None:
            continue
        for name, value in zip(WAVE_KEYS, (math.cos(2 * turn), math.sin(2 * turn))):
            keys.key_blocks[name].value = value
            keys.key_blocks[name].keyframe_insert('value', frame=frame)


def _world_mesh(objects, inset):
    """One BVH of the objects as they stand - evaluated, so shape keys (a flexspline's wave) and modifiers count - each
    surface moved `inset` metres inward along its normals: meshing flanks touch at zero backlash, and the inset separates
    touching from passing through (hollow parts - a ring - stay hollow, which shrinking toward a centre would not keep)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    verts, polys = [], []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
        offset = len(verts)
        verts += [obj.matrix_world @ v.co - (normal_matrix @ v.normal).normalized() * inset for v in mesh.vertices]
        polys += [[offset + i for i in p.vertices] for p in mesh.polygons]
        evaluated.to_mesh_clear()
    return BVHTree.FromPolygons(verts, polys) if polys else None


def _meshes(part):
    """The part's own meshes: itself and its children, not what hangs from a joint below it (a carrier's planets)."""
    out, stack = [], [part]
    while stack:
        o = stack.pop()
        out += [o] if o.type == 'MESH' else []
        stack += [c for c in o.children if not c.get('studio_joint')]
    return out


def _body(obj, root):
    """The rigid body a part moves with: its nearest joint pivot, or the subject root."""
    o = obj.parent
    while o is not None and o != root and not o.get('studio_joint'):
        o = o.parent
    return o.name if o is not None else ''


def _pairs(parts, root):
    """Every two parts that can move relative to each other (different rigid bodies). Not the couplings' pairs: a part
    no coupling names (a rim, a housing) can still be in the way."""
    ids = sorted(parts)
    body = {i: _body(parts[i], root) for i in ids}
    return [(a, b) for n, a in enumerate(ids) for b in ids[n + 1:] if body[a] != body[b]]


def apply_drives(shot, fps):
    """Bake every drive action; returns report rows (interference included)."""
    scene = bpy.context.scene
    rows = []
    for action in shot['actions']:
        if action['type'] != 'drive':
            continue
        params = action['params']
        start, end = action['start_frame'], action['end_frame']
        seconds = max(1, end - start) / fps
        for target in action['targets']:
            sid = target['instance_id']
            mechanism = _mechanism(sid)
            joints = {j['id']: j for j in mechanism['joints']}
            pivots = _pivots(sid)
            for pivot in pivots.values():
                pivot.animation_data_clear()
                pivot['studio_rest_location'] = list(pivot.location)
            drives = [d for d in params['drives'] if d.get('subject', sid) == sid]
            for d in drives:
                if d['joint'] not in joints:
                    raise ValueError(f"MECHANISM: drive names no joint {d['joint']} of {sid}")
            peak = {}
            waves = [c for c in mechanism['couplings'] if c['kind'] == 'harmonic']
            from modeling.assemble import scene_parts
            parts = scene_parts(sid)
            for frame in range(start, end + 1):
                u = (frame - start) / max(1, end - start)
                values = core.solve(mechanism['couplings'], {d['joint']: core.drive_value(d, u, seconds) for d in drives})
                scene.frame_set(frame + 1)
                for jid, value in values.items():
                    _set(pivots[jid], joints[jid], value)
                    peak[jid] = round(value, 3)
                for c in waves:   # the ellipse turns with the wave generator, seen from the flexspline it pushes
                    if c['driver'] in values and c['driven'] in values:
                        turn = math.radians(values[c['driver']] - values[c['driven']])
                        _key_wave(parts.get(c['deform_part']), turn, frame + 1)
            from scene_tools import curves
            keyed = list(pivots.values()) + [o.data.shape_keys for c in waves for o in _meshes(parts[c['deform_part']]) if o.data.shape_keys]
            for owner in keyed:
                for curve in curves(owner.animation_data.action if owner.animation_data else None):
                    for key in curve.keyframe_points:
                        key.interpolation = 'LINEAR'
            root = next(o for o in bpy.data.objects if o.get('studio_subject_id') == sid and o.get('studio_mechanism'))
            pairs = _pairs(parts, root)
            corners = [o.matrix_world @ Vector(c) for part in parts.values() for o in _meshes(part) for c in o.bound_box]
            inset = INSET_SHARE * (Vector([max(c[i] for c in corners) for i in range(3)]) - Vector([min(c[i] for c in corners) for i in range(3)])).length
            interference = []
            frames = sorted({start + round(i * (end - start) / SAMPLES) for i in range(SAMPLES + 1)})
            for a, b in pairs:
                for frame in frames:
                    scene.frame_set(frame + 1)
                    trees = [_world_mesh(_meshes(parts[a]), inset), _world_mesh(_meshes(parts[b]), inset)]
                    if all(trees) and trees[0].overlap(trees[1]):
                        interference.append({'pair': [a, b], 'frame': frame})
                        break
            rows.append({'action_id': action['action_id'], 'subject': sid, 'frames': [start, end], 'joints_keyed': sorted(peak),
                         'final_values': peak, 'pairs_checked': len(pairs), 'inset_m': round(inset, 6), 'interference': interference})
    scene.frame_set(1)
    return rows
