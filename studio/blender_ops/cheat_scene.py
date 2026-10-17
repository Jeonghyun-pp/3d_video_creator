"""The declared cheats of a shot (studio/cheat_core.CHEATS, carried in camera.move.frames[].cheats) applied to the
scene and the frame targets: after the mechanisms are driven and before the openings and the camera are built
(generate.py), so the support check, the openings and the camera solve all see the scene as it will be shot.

Each row of the table names its mechanism (`applies`); a mechanism is written once here, so a new cheat that uses one
is a table row only:
  lens         the moment's frame target starts from another lens (the solve's own ladder goes on from it)
  view         the moment's frame target looks from an angle off the picture's (offsets, within the row's limits)
  see_through  the named parts fade to a see-through skin around the moment (show_scene's skin: SEE_WINDOW for rays,
               its _wrap after the look), never cut or moved: the subject's structure stays
  scale        the thing made smaller or larger about where it is attached: a volume (atmosphere) about the point of it
               nearest the framed subject (where a flame leaves its nozzle), anything else about the middle of its base
  move         the thing moved (at most the row's share of its own size) and/or turned about its own upright
  turn         the framed subject turned about its own long axis, through its middle
The scene mechanisms key delta transforms (delta_location / delta_rotation / delta_scale, which add to whatever the
author or a drive animates) at each moment that declares the cheat, smooth (Bezier) between them; a moment that does not
declare it holds the nearest value - one cheat at one moment holds over the shot. Only this shot's file is changed.
Every changed object is tagged studio_cheat; cheat_report.json says what was applied where.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Vector

import camera_frame
from cheat_table import CHEATS, MOVES, held_locked
import scene_roles
from id_view import _curves
from scene_index import resolve_ref, world_box

TAG = 'studio_cheat'


def _roots(ident):
    """The roots a cheat moves, scales or turns: the target's, and its coats' (coats.py: soot on a body goes with it)."""
    found = resolve_ref(ident)
    if not found or not found['objects']:
        return []
    roots = list(found['roots'])
    held = {o.name for r in roots for o in (r, *r.children_recursive)}
    laid = [o for o in camera_frame.own_objects(ident) if o.name not in held]
    return roots + [o for o in laid if o.parent not in laid]


def _objects(ident):
    """The objects a cheat makes see-through: the target's and its coats'."""
    return camera_frame.own_objects(ident) if resolve_ref(ident) else []


def _solid_tree(roots):
    """Every object the roots carry that is a solid thing (blocks rays: scene_roles raycast) - what can sink into a
    support; a volume made smaller or a helper cannot."""
    return {o.name for r in roots for o in (r, *r.children_recursive) if scene_roles.counts(o, 'raycast')}


def moved_by(cheats):
    """The solid objects the scene cheats among `cheats` would move, scale or turn (MOVES), their whole trees."""
    return set().union(*(_solid_tree(_roots(c['target'])) for c in cheats or []
                         if CHEATS.get(c.get('kind'), {}).get('applies') in MOVES and c.get('target')))


def moved_objects():
    """The solid objects a cheat applied in this session moved, scaled or turned (TAG on a root, its whole tree):
    judged for sinking even when the cheat holds them still over the shot (generate._sinks)."""
    roots = [o for o in bpy.data.objects if any(CHEATS.get(k, {}).get('applies') in MOVES for k in str(o.get(TAG, '')).split(','))]
    return _solid_tree(roots)


def _tag(obj, kind):
    obj[TAG] = ','.join(sorted(set(filter(None, str(obj.get(TAG, '')).split(','))) | {kind}))


def _box(roots):
    objects = list(dict.fromkeys(o for r in roots for o in (r, *r.children_recursive)))
    return world_box(objects, roots)


def _scene_frame(at, count):
    return round(at * (count - 1)) + 1


def _rot_mode(obj):
    return 'quaternion' if obj.rotation_mode == 'QUATERNION' else ('euler' if obj.rotation_mode != 'AXIS_ANGLE' else None)


def _delta(obj):
    """The object's delta transform as (location Vector, rotation 3x3 Matrix, scale Vector)."""
    rotation = obj.delta_rotation_quaternion.to_matrix() if obj.rotation_mode == 'QUATERNION' else obj.delta_rotation_euler.to_matrix()
    return Vector(obj.delta_location), rotation, Vector(obj.delta_scale)


def _set_delta(obj, location, rotation, scale):
    obj.delta_location = location
    if obj.rotation_mode == 'QUATERNION':
        obj.delta_rotation_quaternion = rotation.to_quaternion()
    else:
        obj.delta_rotation_euler = rotation.to_euler(obj.rotation_mode)
    obj.delta_scale = scale


CHANNELS = ('delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')


OWN_KEYS = 'studio_cheat_keyed'   # set on an object whose delta channels this module keyed


def _delta_curves(obj):
    """The object's F-curves on its delta channels (legacy or layered actions: id_view._curves)."""
    action = obj.animation_data.action if obj.animation_data else None
    return [fc for fc in (_curves(action)[0] if action else []) if fc.data_path in CHANNELS]


def _keyed(obj):
    """The object's delta channels are animated by someone else (the author, a drive): a cheat would fight them."""
    return bool(_delta_curves(obj)) and not obj.get(OWN_KEYS)


def _ids(obj):
    """Every id a scene object answers to: its studio id, subject/part, subject, its Blender name."""
    out = [str(obj.get('studio_id') or ''), str(obj.get('studio_subject_id') or ''), obj.name]
    if obj.get('studio_part_id'):
        out.append(f"{obj.get('studio_subject_id', '')}/{obj['studio_part_id']}")
    return [i for i in dict.fromkeys(out) if i]


def tree_lock(kind, roots, lock):
    """Why changing the roots (and everything they carry) touches what the shot's lines speak of, else None."""
    if not lock:
        return None
    names = [i for r in roots for o in (r, *r.children_recursive) for i in _ids(o)]
    return held_locked(kind, names, lock)


def _check_roots(roots, ident):
    if not roots:
        raise ValueError(f'CHEAT_TARGET_MISSING: {ident} is not in the scene')
    for r in roots:
        if _rot_mode(r) is None:
            raise ValueError(f'CHEAT_TARGET_ROTATION: {ident} ({r.name}) turns by axis-angle; a cheat keys euler or quaternion deltas')


def _parent_axes(obj):
    """(world -> parent space 3x3, parent space -> world 3x3) of the space the object's location and deltas are in:
    its parent's (with the parent inverse) - None for a root without a parent (world axes)."""
    if obj.parent is None:
        return None
    space = (obj.matrix_world @ obj.matrix_basis.inverted()).to_3x3()
    return space.inverted(), space


def _rigid(roots, rotation, about, shift):
    """Turn the roots by `rotation` (world, a 3x3) about the point `about` and shift them by `shift`, as deltas on top of
    what they hold now (a root's delta rotation is applied before its own, in its parent's axes: a world turn and a world
    shift are carried into them for a root that hangs from something - a fog volume on its emitter, a sign on a wall)."""
    for r in roots:
        location, turned, scale = _delta(r)
        origin = r.matrix_world.translation.copy()
        placed = Vector(about) + rotation @ (origin - Vector(about)) + Vector(shift)
        axes = _parent_axes(r)
        if axes is None:
            _set_delta(r, location + (placed - origin), rotation @ turned, scale)
        else:   # the same world change as the parent sees it (its uniform scale cancels out of the turn)
            to_parent, to_world = axes
            local = (to_parent @ rotation @ to_world).normalized()
            _set_delta(r, location + to_parent @ (placed - origin), local @ turned, scale)


def _scaled(roots, factor, anchor):
    """Make the roots `factor` times as large about the world point `anchor` (it stays where it is), in the parent's
    space for a root that hangs from something."""
    for r in roots:
        location, turned, scale = _delta(r)
        origin = r.matrix_world.translation.copy()
        shift = (1.0 - factor) * (Vector(anchor) - origin)
        axes = _parent_axes(r)
        _set_delta(r, location + (shift if axes is None else axes[0] @ shift), turned, scale * factor)


def _anchor(roots, row, framed_box):
    """Where a thing scaled by `row` stays put: a volume at its point nearest the framed subject's middle (its source),
    anything else at the middle of its base (it stands where it stood)."""
    lo, hi = _box(roots)
    if row['targets'] == 'atmosphere' and framed_box is not None:
        middle = [(framed_box[0][i] + framed_box[1][i]) / 2 for i in range(3)]
        return Vector([min(max(middle[i], lo[i]), hi[i]) for i in range(3)])
    return Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]))


def _long_axis(target):
    import camera_moves
    name = camera_moves._long_axis(target)
    return camera_moves._axis(target, name) if name else None


def change(job, fr, cheat, framed_box=None, enforce=True):
    """Apply one scene cheat statically to the scene as it stands at the current frame: -> {objects, ...details}.
    Values are checked against the row's limits on the scene's own measures (a move against the thing's size now), and
    what the moved thing carries against job['locked'] (cheat_core.locked) - unless `enforce` is off: frames check
    tries a barred cheat only to say whether the facts are what stands in the way."""
    row = CHEATS[cheat['kind']]
    how, value = row['applies'], cheat['value']
    if how == 'see_through':
        refs = cheat['target'] if isinstance(cheat['target'], list) else [cheat['target']]
        names = sorted({o.name for ref in refs for o in _objects(ref) if o.type == 'MESH'})
        if not names:
            raise ValueError(f"CHEAT_TARGET_MISSING: {refs} are not in the scene")
        return {'objects': names, 'alpha': value}
    roots = _roots(cheat['target'])
    _check_roots(roots, cheat['target'])
    held = tree_lock(cheat['kind'], roots, job.get('locked')) if enforce else None
    if held:   # what the moved thing carries, by every id its objects answer to (a name, a part, a subject)
        raise ValueError(f'CHEAT_TOUCHES_LOCKED: {held}')
    box = _box(roots)
    if how == 'scale':
        lo, hi = row['limits']['factor']
        if not lo <= value <= hi:
            raise ValueError(f"CHEAT_OUT_OF_RANGE: {cheat['kind']} x{value} outside [{lo}, {hi}]")
        anchor = _anchor(roots, row, framed_box)
        _scaled(roots, value, anchor)
        return {'objects': [r.name for r in roots], 'anchor': [round(c, 4) for c in anchor]}
    if how == 'move':
        size = max(box[1][i] - box[0][i] for i in range(3))
        move = Vector(value.get('move_m') or (0.0, 0.0, 0.0))
        if move.length > row['limits']['size_share'] * size * (1 + 1e-6) + 1e-6:
            raise ValueError(f"CHEAT_OUT_OF_RANGE: {cheat['kind']} moves {cheat['target']} {move.length:.2f} m, its size is {size:.2f} m "
                             f"(at most {row['limits']['size_share']} x)")
        turn = float(value.get('turn_deg') or 0.0)
        if abs(turn) > row['limits']['turn_deg']:
            raise ValueError(f"CHEAT_OUT_OF_RANGE: {cheat['kind']} turns {turn} deg")
        middle = Vector([(box[0][i] + box[1][i]) / 2 for i in range(3)])
        _rigid(roots, Matrix.Rotation(math.radians(turn), 3, 'Z'), middle, move)
        return {'objects': [r.name for r in roots], 'size_m': round(size, 4)}
    if how == 'turn':
        if abs(value) > row['limits']['deg']:
            raise ValueError(f"CHEAT_OUT_OF_RANGE: {cheat['kind']} {value} deg")
        axis = _long_axis(cheat['target'])
        if axis is None:
            raise ValueError(f"CHEAT_NO_AXIS: {cheat['target']} has no long axis of its own to turn about")
        middle = Vector([(box[0][i] + box[1][i]) / 2 for i in range(3)])
        _rigid(roots, Matrix.Rotation(math.radians(value), 3, Vector(axis)), middle, Vector())
        return {'objects': [r.name for r in roots], 'axis': [round(c, 4) for c in axis]}
    raise ValueError(f"CHEAT_UNKNOWN: no mechanism {how!r} for {cheat['kind']}")


def retarget(fr, cheat, row):
    """A camera cheat changes the moment's frame target (the picture stays what it was, recorded in fr['_cheated'])."""
    held = fr.setdefault('_cheated', {})
    if row['applies'] == 'lens':
        held.setdefault('lens_from', fr.get('lens_mm'))
        fr['lens_mm'] = float(cheat['value'])
    elif row['applies'] == 'view':
        view = dict(fr['view'])
        held.setdefault('view_from', dict(view))
        view['elevation_deg'] = max(-85.0, min(85.0, view['elevation_deg'] + float(cheat['value'].get('elevation_deg', 0.0))))
        view['azimuth_deg'] = view.get('azimuth_deg', 0.0) + float(cheat['value'].get('azimuth_deg', 0.0))
        fr['view'] = view


def _framed_box(job, frame):
    params = (job['shot']['camera'].get('move') or {}).get('params') or {}
    bpy.context.scene.frame_set(frame)
    found = resolve_ref(params.get('target')) if params.get('target') else None
    return found['box'] if found else None


def apply(job):
    """Apply every declared cheat of the shot (keyed per moment, see the module doc). -> report {cheats, see_through}."""
    shot = job['shot']
    move = shot['camera'].get('move') or {}
    frames = sorted(move.get('frames') or [], key=lambda f: f['at'])
    count, scene = shot['duration_frames'], bpy.context.scene
    table = CHEATS
    rows, keyed, see = [], {}, []   # keyed: object name -> {scene frame: (location, rotation, scale)}
    base = {}                       # object name -> its delta before any cheat
    for n, fr in enumerate(frames):
        frame = _scene_frame(fr['at'], count)
        for cheat in fr.get('cheats') or []:
            row = table.get(cheat['kind'])
            if row is None:
                raise ValueError(f"CHEAT_UNKNOWN: {cheat['kind']} is not a row of the cheat table")
            if row['applies'] in ('lens', 'view'):
                retarget(fr, cheat, row)
                rows.append({'at': fr['at'], 'kind': cheat['kind'], 'value': cheat['value'], 'frame_target': fr.get('_cheated')})
                continue
            if row['applies'] == 'see_through':
                done = change(job, fr, cheat)
                before = _scene_frame(frames[n - 1]['at'], count) if n > 0 else None
                after = _scene_frame(frames[n + 1]['at'], count) if n + 1 < len(frames) else None
                start = before + 1 if before is not None else max(1, frame - round(0.8 * job['fps']))
                on = max(start, frame - 1)
                close = [frame + 1, max(frame + 2, after - 1)] if after is not None else None
                window = [(start + on) // 2, ((close[0] + close[1]) // 2) if close else 10 ** 6]
                for name in done['objects']:
                    camera_frame.add_see_window(bpy.data.objects[name], window)
                    _tag(bpy.data.objects[name], cheat['kind'])
                # zero-based frames, as show_scene's rows (see_through keys at frame + 1)
                see.append({'action_id': f"cheat_{n}_{cheat['kind']}", 'how': cheat['kind'], 'objects': done['objects'],
                            'open': [start - 1, on - 1], 'close': [close[0] - 1, close[1] - 1] if close else None,
                            'see_through': {'alpha': float(cheat['value']), 'rim': False}})
                rows.append({'at': fr['at'], 'kind': cheat['kind'], 'target': cheat['target'], 'value': cheat['value'],
                             'objects': done['objects'], 'window': window})
                continue
    # scene cheats on things: each moment computed from the uncheated scene at its frame, then keyed
    for n, fr in enumerate(frames):
        frame = _scene_frame(fr['at'], count)
        moved = [c for c in fr.get('cheats') or [] if table[c['kind']]['applies'] in MOVES]
        if not moved:
            continue
        framed = _framed_box(job, frame)
        names = set()
        for cheat in moved:
            for r in _roots(cheat['target']):
                if r.name not in base:
                    if _keyed(r):
                        raise ValueError(f'CHEAT_CONFLICT: {r.name} has its delta transform keyed already')
                    base[r.name] = _delta(r)
        for name, held in base.items():   # every moment starts from the scene without cheats
            _set_delta(bpy.data.objects[name], *held)
        scene.frame_set(frame)
        for cheat in moved:
            bpy.context.view_layer.update()
            done = change(job, fr, cheat, framed)
            names |= set(done['objects'])
            for name in done['objects']:
                _tag(bpy.data.objects[name], cheat['kind'])
            rows.append({'at': fr['at'], 'kind': cheat['kind'], 'target': cheat['target'], 'value': cheat['value'], 'frame': frame - 1,
                         **{k: v for k, v in done.items()}})
        for name in names:
            keyed.setdefault(name, {})[frame] = _delta(bpy.data.objects[name])
    for name, held in base.items():
        obj = bpy.data.objects[name]
        _set_delta(obj, *held)
        for frame, (location, rotation, scale) in sorted(keyed.get(name, {}).items()):
            _set_delta(obj, location, rotation, scale)
            for path in ('delta_location', 'delta_scale', 'delta_rotation_quaternion' if obj.rotation_mode == 'QUATERNION' else 'delta_rotation_euler'):
                obj.keyframe_insert(path, frame=frame)
        obj[OWN_KEYS] = True
        for fc in _delta_curves(obj):
            for point in fc.keyframe_points:
                point.interpolation = 'BEZIER'
    if see:
        shot.setdefault('_see_through', []).extend(see)   # faded after the look, with the openings' skins (show_scene.see_through)
    scene.frame_set(1)
    return {'cheats': rows, 'see_through': [{k: r[k] for k in ('action_id', 'objects', 'open', 'close')} for r in see]}


def trial(job, fr, cheat, enforce=True):
    """Apply one cheat for a single solve in the same session (frames check): statically, the whole shot; -> undo().
    On a thing a recorded cheat keyed, the trial goes on top of what those keys hold at the moment (they are muted for
    the trial); a thing whose deltas someone else keyed is not tried (CHEAT_CONFLICT)."""
    row = CHEATS[cheat['kind']]
    if row['applies'] in ('lens', 'view'):
        saved = {k: fr.get(k) for k in ('lens_mm', 'view', '_cheated')}
        retarget(fr, cheat, row)

        def undo():
            for k, v in saved.items():
                if v is None:
                    fr.pop(k, None)
                else:
                    fr[k] = v
        return undo
    if row['applies'] == 'see_through':
        done = change(job, fr, cheat, enforce=enforce)
        saved = {name: (list(w) if (w := bpy.data.objects[name].get(camera_frame.SEE_WINDOW)) is not None else None)
                 for name in done['objects']}
        for name in done['objects']:
            bpy.data.objects[name][camera_frame.SEE_WINDOW] = [0, 10 ** 6]

        def undo():
            for name, window in saved.items():
                obj = bpy.data.objects[name]
                if window is None:
                    del obj[camera_frame.SEE_WINDOW]
                else:
                    obj[camera_frame.SEE_WINDOW] = window
        return undo
    roots = _roots(cheat['target'])
    for r in roots:
        if _keyed(r):
            raise ValueError(f'CHEAT_CONFLICT: {r.name} has its delta transform keyed already (not by a cheat)')
    frame = _scene_frame(fr['at'], job['shot']['duration_frames'])
    framed = _framed_box(job, frame)   # sets the frame: deltas a recorded cheat keyed read as they stand there
    saved = {r.name: _delta(r) for r in roots}
    tags = {r.name: r.get(TAG) for r in roots}   # tagged for the trial as a build tags it: judged for sinking (moved_objects)
    muted = [(fc, fc.mute) for r in roots for fc in _delta_curves(r)]   # a recorded cheat's keys would overwrite the trial
    for fc, _was in muted:
        fc.mute = True
    bpy.context.view_layer.update()

    def undo():
        for name, held in saved.items():
            _set_delta(bpy.data.objects[name], *held)
        for name, tag in tags.items():
            obj = bpy.data.objects[name]
            if tag is None:
                obj.pop(TAG, None)
            else:
                obj[TAG] = tag
        for fc, was in muted:
            fc.mute = was
        bpy.context.view_layer.update()
    try:
        change(job, fr, cheat, framed, enforce=enforce)
    except ValueError:
        undo()
        raise
    for r in roots:
        _tag(r, cheat['kind'])
    return undo
