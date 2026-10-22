"""The changes a shot's lines explain, made in its scene (studio/change_moves.py says what each is; library/change_kinds.json
says how each kind is made): before the drives, the openings and the camera, so all of them see the scene changing.

  fill       the part fills or drains: a copy of the part's own shape (a hair larger, so it shows on its skin) clipped
             by a box whose top rises or falls to the level - '<part>/fill', water-coloured even in clay, in the part's
             group (ids_core.in_group) for the keeps and a coat of the part (coats.py) for the rays, openings and
             blockers: never in front of the tank it fills
  transform  the part, or the whole subject, turned, moved or scaled about the centre of its box, keyed from `from` to
             `to` over the change's frames; a part that is the child of its spec's joint of the same motion is driven
             by that joint instead (a drive action, kinematics.apply_drives)
  simulate   a stream from the part's face along `toward` (a simulate action, simulate.apply)

Nothing here is written into shot.json: the actions it adds are the build's (change_<id>), as show_scene's openings
are; a hand-written action of that id is refused (CHANGE_SCENE_CONFLICT). What it made is removed before it makes
again (OWNER), so a workbench session and a build give the same scene.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from scene_index import resolve_group

OWNER = 'studio_change'          # an object this module made (fill bodies, level boxes)
KEYED = 'studio_change_keyed'    # an object this module keyed (its rest kept in REST)
REST = 'studio_change_rest'
WATER_SRGB = (0.16, 0.45, 0.85)  # the made body's colour: water reads as water in the grey clay
SKIN = 1.003                     # a fill body this much larger than its part shows on the part's skin
PENDING = {}                     # object name -> its rest and the changes that move it (keyed together: _key_transforms)


def _kinds(job):
    return {k['id']: k for k in json.loads((Path(job['library_root']) / 'change_kinds.json').read_text())['kinds']}


def _tree(obj):
    yield obj
    for child in obj.children_recursive:
        yield child


def _box(objects):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = [o.evaluated_get(depsgraph).matrix_world @ Vector(c) for obj in objects for o in _tree(obj) if o.type == 'MESH'
              for c in o.evaluated_get(depsgraph).bound_box]
    if not points:
        return None
    return Vector([min(p[i] for p in points) for i in range(3)]), Vector([max(p[i] for p in points) for i in range(3)])


def clear():
    """Remove what an earlier run made and put back what it keyed."""
    for obj in [o for o in bpy.data.objects if o.get(OWNER)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for obj in [o for o in bpy.data.objects if o.get(KEYED)]:
        if obj.animation_data:
            obj.animation_data_clear()
        obj.matrix_basis = Matrix([obj[REST][i * 4:i * 4 + 4] for i in range(4)])
        del obj[KEYED]


def _water():
    material = bpy.data.materials.get('StudioChangeWater') or bpy.data.materials.new('StudioChangeWater')
    material.diffuse_color = (*WATER_SRGB, 1.0)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get('Principled BSDF')
    if bsdf is not None:
        bsdf.inputs['Base Color'].default_value = (*WATER_SRGB, 1.0)
        bsdf.inputs['Roughness'].default_value = 0.15
    return material


def _fill(change, roots, start, end):
    """The part's shape below a level that moves from change.from to change.to of its height."""
    from scene_roles import TAG
    low, high = _box(roots)
    height = high.z - low.z
    pad = max(high.x - low.x, high.y - low.y, height) * 0.05
    level = bpy.data.objects.new(f"change_{change['id']}_level", None)
    bpy.context.scene.collection.objects.link(level)
    level.empty_display_type = 'CUBE'
    level[OWNER], level[TAG], level.hide_render = True, 'helper', True
    # a unit cube's top at z=+1: placed so its bottom is below the part and its top at the level
    cube = bpy.data.meshes.new(f"change_{change['id']}_level_box")
    cube.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    box = bpy.data.objects.new(f"change_{change['id']}_box", cube)
    bpy.context.scene.collection.objects.link(box)
    box[OWNER], box[TAG], box.hide_render, box.display_type = True, 'helper', True, 'WIRE'
    half = Vector(((high.x - low.x) / 2 + pad, (high.y - low.y) / 2 + pad, 0.0))
    for frame, share in ((start, change['from']), (end, change['to'])):
        top = low.z + height * share
        bottom = low.z - pad
        box.location = (((low.x + high.x) / 2), ((low.y + high.y) / 2), (top + bottom) / 2)
        box.scale = (half.x, half.y, max((top - bottom) / 2, 1e-4))
        box.keyframe_insert('location', frame=frame + 1)
        box.keyframe_insert('scale', frame=frame + 1)
    made = []
    water = _water()
    for root in roots:
        for part in [o for o in _tree(root) if o.type == 'MESH']:
            body = part.copy()
            body.data = part.data.copy()
            for key in [k for k in body.keys() if k.startswith('studio_')]:
                del body[key]
            body.animation_data_clear()
            sid = part.get('studio_id') or part.name
            body.name = f"{sid}/fill"
            body['studio_id'], body[OWNER], body[TAG] = f'{sid}/fill', True, 'object'
            bpy.context.scene.collection.objects.link(body)
            # with the part's parent: a tank that turns with its boat carries its water; the level box stays level
            world = Matrix.Translation((low + high) / 2) @ Matrix.Scale(SKIN, 4) @ Matrix.Translation(-(low + high) / 2) @ part.matrix_world
            body.parent = part.parent
            body.matrix_parent_inverse = Matrix.Identity(4)
            body.matrix_world = world
            body.data.materials.clear()
            body.data.materials.append(water)
            import coats   # the water on the tank's skin is the tank wherever the engine looks (rays, openings, blockers)
            coats.mark(body, sid)
            clip = body.modifiers.new('StudioChangeLevel', 'BOOLEAN')
            clip.operation, clip.object = 'INTERSECT', box
            clip.solver = 'EXACT'
            made.append(body['studio_id'])
    return {'made': made, 'measure_ids': made}


def _joint(job, root, mode):
    """(subject, joint) when the part is the child of its spec's joint that moves as `mode` does, else None."""
    sid, part = root.get('studio_subject_id'), root.get('studio_part_id')
    path = (job.get('subject_spec_paths') or {}).get(sid) if sid else None
    if not path or not part:
        return None
    for joint in json.loads(Path(path).read_text()).get('joints') or []:
        if joint.get('child') == part and (joint.get('type'), mode) in (('revolute', 'rotate'), ('prismatic', 'translate')):
            return sid, joint
    return None


def _transform(job, change, kind, roots, start, end):
    mode = kind['mode']
    if kind.get('joint_first') and len(roots) == 1:
        found = _joint(job, roots[0], mode)
        if found:
            sid, joint = found
            sign = 1.0
            if mode == 'translate':   # the change moves along `toward`; the joint along its own axis
                toward, axis = Vector(change['toward']).normalized(), Vector(joint.get('axis') or (0, 0, 1)).normalized()
                if abs(abs(toward.dot(axis)) - 1) > 1e-3:
                    found = None
                sign = 1.0 if toward.dot(axis) > 0 else -1.0
            if found:
                return {'action': {'action_id': f"change_{change['id']}", 'type': 'drive', 'start_frame': start, 'end_frame': end,
                                   'easing': 'ease_in_out', 'targets': [{'instance_id': str(roots[0].get('studio_id') or sid).partition('/')[0],
                                                                         'part_id': roots[0]['studio_part_id']}],
                                   'params': {'drives': [{'joint': joint['id'], 'subject': sid, 'profile': 'linear', 'keys': [
                                       {'t': 0, 'value': sign * change['from']}, {'t': 1, 'value': sign * change['to']}]}]}},
                        'made': [], 'measure_ids': [r.get('studio_id') or r.name for r in roots], 'by': 'joint ' + joint['id']}
    low, high = _box(roots)
    centre = (low + high) / 2
    axis = _axis_vector(roots[0], change.get('axis')) if change.get('axis') else None

    def motion(value):
        if mode == 'rotate':
            return Matrix.Translation(centre) @ Matrix.Rotation(math.radians(value), 4, axis) @ Matrix.Translation(-centre)
        if mode == 'translate':
            return Matrix.Translation(Vector(change['toward']).normalized() * value)
        kept = axis if kind.get('keeps_axis') else None   # the axis whose length a squeeze keeps
        scale = Matrix.Identity(3) * value
        if kept is not None:   # value everywhere but along the kept axis: value*I + (1 - value) k k^T
            scale = scale + (1.0 - value) * Matrix([[kept[i] * kept[j] for j in range(3)] for i in range(3)])
        return Matrix.Translation(centre) @ scale.to_4x4() @ Matrix.Translation(-centre)
    for root in roots:
        if root.animation_data and root.animation_data.action and not root.get(KEYED) and root.name not in PENDING:
            raise ValueError(f"CHANGE_CONFLICT: change {change['id']} moves {root.get('studio_id') or root.name}, which is keyed "
                             'already (an author script or a hand action) - say the motion as changes, not both')
        row = PENDING.setdefault(root.name, {'root': root, 'rest': root.matrix_world.copy(), 'basis': [v for r in root.matrix_basis for v in r],
                                             'motions': []})
        row['motions'].append((motion, start, end, change['from'], change['to']))
    return {'made': [], 'measure_ids': [r.get('studio_id') or r.name for r in roots], 'by': mode}


def _axis_vector(root, name):
    """The world direction of an axis a change names: x, y or z of the world, or the object's own height (its axis
    nearest the world's up), length (the longer of the other two, by its extent) and width. Why (2026-10-22 r3): a
    planner cannot know which way a placed subject lies; it can say 'pitch about its width'."""
    if name in ('x', 'y', 'z'):
        return Vector({'x': (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1)}[name])
    turn = root.matrix_world.to_3x3().normalized()
    cols = [turn.col[i].normalized() for i in range(3)]
    inverse = root.matrix_world.inverted()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = [inverse @ (o.evaluated_get(depsgraph).matrix_world @ Vector(c)) for o in _tree(root) if o.type == 'MESH'
              for c in o.evaluated_get(depsgraph).bound_box]
    extent = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)] if points else [1.0, 1.0, 1.0]
    up = max(range(3), key=lambda i: abs(cols[i].z))
    rest = [i for i in range(3) if i != up]
    length = max(rest, key=lambda i: extent[i])
    width = next(i for i in rest if i != length)
    return cols[{'height': up, 'length': length, 'width': width}[name]]


def _key_transforms():
    """Key every object the changes move: at each change's start and end, the changes composed in their order, each at
    its value then (from before its start, to after its end, between them along the way). Several changes of one object
    are one motion (2026-10-22 r3: a boat sinking while its bow dips was refused)."""
    for row in PENDING.values():
        root, rest = row['root'], row['rest']
        root[REST] = row['basis']
        root[KEYED] = True
        frames = sorted({f for _fn, s, e, _a, _b in row['motions'] for f in (s, e)})

        def at(frame):
            total = Matrix.Identity(4)
            for motion, s, e, a, b in row['motions']:
                share = 0.0 if frame <= s else 1.0 if frame >= e else (frame - s) / max(1, e - s)
                total = motion(a + (b - a) * share) @ total
            return total @ rest
        for frame in frames:
            root.matrix_world = at(frame)
            for path in ('location', 'rotation_euler', 'scale'):
                root.keyframe_insert(path, frame=frame + 1)
        root.matrix_world = at(0)


def _stream(change, kind, roots, start, end):
    low, high = _box(roots)
    toward = Vector(change['toward']).normalized()
    centre, half = (low + high) / 2, (high - low) / 2
    face = centre + Vector([toward[i] * half[i] for i in range(3)])
    thin = Vector([0.05 if abs(toward[i]) > 0.5 else half[i] * 0.4 for i in range(3)])
    sid = roots[0].get('studio_id') or roots[0].name
    instance, _, part = sid.partition('/')
    return {'action': {'action_id': f"change_{change['id']}", 'type': 'simulate', 'start_frame': start, 'end_frame': end,
                       'easing': 'linear', 'targets': [{'instance_id': instance, 'part_id': part or instance}],
                       'params': {'kind': kind['simulate_kind'], 'region': [list(face - thin), list(face + thin)], 'count': 400,
                                  'direction': list(toward), 'speed_mps': 1.5, 'color_srgb': list(kind.get('color_srgb') or WATER_SRGB)}},
            'made': [], 'measure_ids': [f"StudioSim_change_{change['id']}"], 'by': kind['simulate_kind']}


def apply(job):
    """Make each change of job['changes'] (studio/change_moves.timed: frames resolved); the actions it needs are added to
    job['shot']['actions']. -> report rows [{id, kind, makes, by, start_frame, end_frame, measure_ids, made}]."""
    clear()
    PENDING.clear()
    rows, book = [], _kinds(job)
    taken = {a['action_id'] for a in job['shot'].get('actions') or []}
    for change in job.get('changes') or []:
        kind = book[change['kind']]
        roots = list(dict.fromkeys(r for part in change['parts'] for r in resolve_group(part)))
        if not roots:
            raise ValueError(f"CHANGE_PART_MISSING: change {change['id']} changes {change['parts']}, which the scene does not have")
        start, end = change['start_frame'], change['end_frame']
        bpy.context.scene.frame_set(start + 1)
        made = {'fill': lambda: _fill(change, roots, start, end), 'transform': lambda: _transform(job, change, kind, roots, start, end),
                'simulate': lambda: _stream(change, kind, roots, start, end)}[kind['makes']]()
        action = made.pop('action', None)
        if action:
            if action['action_id'] in taken:
                raise ValueError(f"CHANGE_SCENE_CONFLICT: the shot has its own action {action['action_id']}; the build names its "
                                 'changes change_<id> - rename the hand action')
            job['shot']['actions'].append(action)
        rows.append({'id': change['id'], 'kind': change['kind'], 'makes': kind['makes'], 'by': made.pop('by', kind['makes']),
                     'start_frame': start, 'end_frame': end, **made})
    _key_transforms()
    bpy.context.scene.frame_set(1)
    return rows
