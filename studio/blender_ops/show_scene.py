"""The scene follows the shot list's show (show_core.py): each moment that opens its subject gets the opening built
here, before the camera is solved, so the camera keeps its picture's view and the parts show because the subject is
open - never because the camera went round to another side (2026-10-13; the rocket reel turned 10-20 degrees in 38 of
296 moments to see an engine inside).

  section / cutaway / interior   a box cutter (reveal.py) whose cut plane faces the picture's camera and passes through
                                 the middle of the deepest opened part (show_core.cutter); it opens while the camera moves
                                 from the moment before, closes before a later moment that shows the outside again (scale
                                 down, then the boolean switched off)
  explode                        the things in front of the parts move away from them, toward the camera's side
  xray / transparency            the things in front fade to a see-through skin (see_through, after the look: the clay
                                 look replaces every material)

What is "in front": everything a ray from the picture's camera side meets on its way to each part, layer after layer -
a skin, the tank under it, the sleeve round a pipe (camera_frame.blockers; 2026-10-14: only the first layer opened, so
the rest still hid the part). What any moment of the shot shows (its show parts and must-see) is never cut, faded or
moved, by its own opening or another one held open. Once the cuts are built, check_cuts measures
each opened part from the picture's view (SHOW_CUT_MISSES, before the camera is solved). Report: show_report.json.
"""
from __future__ import annotations

import bmesh
import bpy
from mathutils import Matrix, Vector

import camera_frame
import camera_frame_core as frame_core
import show_core
from scene_roles import TAG as ROLE_TAG
from scene_index import resolve_ref
from scene_tools import object_by_id

PREFIX = 'show_'
CAP = 'StudioShowCap'
SEE_PROP = 'studio_see_through'          # keyed 0..1 on each see-through object (its materials read it)


def _tree(obj):
    return [obj, *obj.children_recursive]


def _box(objects, depsgraph):
    """World axis-aligned box of what the objects render."""
    points = []
    for obj in objects:
        for o in _tree(obj):
            if o.type != 'MESH':
                continue
            ev = o.evaluated_get(depsgraph)
            points += [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    if not points:
        return None
    return (tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3)))


def _target_of(obj):
    """An action target for one object (scene_tools.targets): its studio id as instance/part, else its name."""
    ident = str(obj.get('studio_id') or '')
    if '/' in ident:
        instance, part = ident.split('/', 1)
        return {'instance_id': instance, 'part_id': part}
    return {'instance_id': '_', 'part_id': obj.name}


def in_front(parts, toward, frame, reach, keep=()):
    """{object: share of the part samples it hides} - every object a ray from `reach` metres out on the camera's side
    meets on the way to each sampled point of the parts, layer after layer (camera_frame.blockers). The parts and their
    children are skipped, and so is everything named in `keep` (what a moment shows while this opening is open: never opened,
    a ray meeting it stops there); helpers, cutters and see-through skins let rays by (camera_frame.passes_through)."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    own = {o.name for ref in [*parts, *keep] for o in camera_frame.own_objects(ref)}   # with their coats
    points = camera_frame.sample(parts, depsgraph, count=400)
    hits = {}
    for p in points:
        target = Vector(p)
        for obj in camera_frame.blockers(scene, depsgraph, target + Vector(toward) * reach, target, own):
            hits[obj] = hits.get(obj, 0) + 1
    return {obj: n / max(1, len(points)) for obj, n in hits.items()}


def hidden_share(part, parts, toward, frame, reach, count=400):
    """Share of one part's samples still behind something from the view `toward` (parallel rays from `reach` metres
    out, as in_front looks); the other opened parts count as its own (an opening keeps them whole)."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    own = {o.name for ref in parts for o in camera_frame.own_objects(ref)}   # with their coats
    points = camera_frame.sample([part], depsgraph, count=count)
    hidden = sum(1 for p in points if not camera_frame.walk(scene, depsgraph, Vector(p) + Vector(toward) * reach, Vector(p), own)[0])
    return hidden / max(1, len(points))


def check_cuts(job, rows):
    """SHOW_CUT_MISSES (broken, gates.SOFTENABLE): each part a cut opens must show from its picture's view once the cut
    is built - at most camera_frame.HIDDEN_SHARE of it still behind something, on the frame it is fully open. Run after
    the cuts are built (reveal.apply_camera_bound) and before the camera is solved, so a cut that misses stops here with
    the part and its share, not as a camera failure later (2026-10-14: a cut plane missed a lower tank, 94 % of it
    stayed behind skin). -> {checked: [{action_id, part, hidden_share}], gate_failures, warnings}."""
    import gate_policy
    checked, failures = [], []
    for row in rows:
        if not row.get('cutter'):
            continue
        for part in row['parts']:
            try:
                share = round(hidden_share(part, row['parts'], row['toward'], row['open'][1] + 1, row['reach_m']), 3)
            except ValueError:   # nothing of the part renders once the cut is built: the cut took it, or it never rendered
                failures.append({'gate': 'SHOW_CUT_MISSES', 'action_id': row['action_id'], 'part': part, 'hidden_share': None,
                                 'why': f"{part} has no surface left to see after {row['action_id']} ({row['how']}) at t={row['at']} - "
                                        'the cut removed it or it renders nothing (see show_report.json in_front)'})
                continue
            checked.append({'action_id': row['action_id'], 'part': part, 'hidden_share': share})
            if share > camera_frame.HIDDEN_SHARE:
                failures.append({'gate': 'SHOW_CUT_MISSES', 'action_id': row['action_id'], 'part': part, 'hidden_share': share,
                                 'why': f"{part} stays {round(share * 100)} % hidden after {row['action_id']} ({row['how']}) from the "
                                        f"picture's view at t={row['at']} - what still hides it: the cut's plane or window misses it "
                                        "(see show_report.json in_front and cutter)"})
    bpy.context.scene.frame_set(1)
    errors, softened = gate_policy.split(failures, 'gate')
    return {'checked': checked, 'gate_failures': errors, 'warnings': [f"SHOW_CUT_MISSES (warning by policy): {f['why']}" for f in softened]}


def _local_axis(obj, world):
    """A world direction as scene_tools.apply_actions reads an explode axis for this object: in its asset root's frame
    when it has one, else in its parent's (obj.location's space)."""
    direction = Vector(world)
    root = object_by_id(obj.get('studio_asset_root_id', '')) if obj.get('studio_asset_root_id') else None
    if root is not None:
        direction = root.matrix_world.to_quaternion().inverted() @ direction
    elif obj.parent is not None:
        direction = obj.parent.matrix_world.to_3x3().inverted() @ direction
    direction.normalize()
    return [round(v, 6) for v in direction]


def _cap():
    material = bpy.data.materials.get(CAP)
    if material is None:
        material = bpy.data.materials.new(CAP)
        material.diffuse_color = (0.12, 0.12, 0.13, 1.0)
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get('Principled BSDF')
        if bsdf is not None:
            bsdf.inputs['Base Color'].default_value = (0.12, 0.12, 0.13, 1.0)
            bsdf.inputs['Roughness'].default_value = 0.8
    return material


def _cutter(name, placed, right, up, toward):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['studio_id'] = name
    obj[ROLE_TAG] = 'helper'
    obj.hide_render, obj.display_type = True, 'WIRE'   # a later opening's rays pass it (camera_frame.passes_through)
    basis = Matrix((right, up, toward)).transposed()
    obj.matrix_world = Matrix.Translation(Vector(placed['centre'])) @ basis.to_4x4()
    obj.scale = placed['half']
    return obj


def apply(job):
    """Build the openings of the shot's frame targets (camera.move.frames[].show). Adds reveal / explode actions to
    job['shot']['actions'] (reveal.apply builds the cuts with the camera's cue call; explode is applied here) and
    returns the report rows; see-through moments are kept for see_through() after the look."""
    shot = job['shot']
    move = (shot.get('camera') or {}).get('move') or {}
    frames = sorted(move.get('frames') or [], key=lambda f: f['at'])
    rows = [f.get('show') for f in frames]
    if not any(r and show_core.opens(r['how']) for r in rows):
        return []
    count, fps = shot['duration_frames'], job['fps']
    taken = {a['action_id'] for a in shot['actions']}
    # what a moment shows while an opening is open is never cut, faded or moved by it (show_core.held_parts): an opening
    # held open would otherwise take a part a later moment shows; what only a moment before it shows is its skin
    shown = [sorted(set((r or {}).get('parts') or []) | set(f.get('must_see') or [])) for r, f in zip(rows, frames)]
    report, explode, exploded = [], False, set()
    for n, opening in enumerate(show_core.openings(rows)):
        keep = show_core.held_parts(opening, shown)
        method = show_core.SHOW_METHODS[opening['how']]
        first, last = frames[opening['first']], frames[opening['last']]
        on = round(first['at'] * (count - 1))
        before = frames[opening['first'] - 1] if opening['first'] > 0 else None
        # one frame after the moment before: that moment's frame has no cut on yet (a shrunk cutter leaves a sliver)
        start = round(before['at'] * (count - 1)) + 1 if before else max(0, on - round(method['open_s'] * fps))
        start = min(start, max(0, on - 1))
        view = first['view']
        forward, right, up = frame_core.basis(view['elevation_deg'], view.get('azimuth_deg', 0.0))
        toward = tuple(-c for c in forward)
        scene = bpy.context.scene
        scene.frame_set(on + 1)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        found = {p: resolve_ref(p) for p in opening['parts']}
        parts = [p for p in opening['parts'] if found[p] and found[p]['objects']]
        missing = sorted(set(opening['parts']) - set(parts))
        if missing or not parts:
            raise ValueError(f"SHOW_PART_MISSING: the moment at {first['at']} opens {missing or opening['parts']}, which the scene does not have")
        named = resolve_ref(move['params']['target']) if (move.get('params') or {}).get('target') else None
        subject = named['roots'] if named and named['objects'] else found[parts[0]]['roots']
        part_boxes = [b for p in parts if (b := _box(found[p]['roots'], depsgraph))]
        parts_box = _box([r for p in parts for r in found[p]['roots']], depsgraph)
        subject_box = _box(subject, depsgraph) or parts_box
        reach = max((Vector(subject_box[1]) - Vector(subject_box[0])).length * 2, 1.0)
        front = in_front(parts, toward, on + 1, reach, keep)
        # the cutter reaches past everything in front, the subject's group or not (a shell may be its own object)
        subject_box = _box([*subject, *front], depsgraph) or subject_box
        if not front:   # nothing hides the parts from this side: there is nothing to open (said, the moment shows them)
            report.append({'how': opening['how'], 'parts': parts, 'at': first['at'], 'opened': [], 'note': 'nothing in front of the parts'})
            continue
        action_id = f'{PREFIX}{n}_{opening["how"]}'
        if action_id in taken:
            raise ValueError(f'SHOW_SCENE_CONFLICT: action {action_id} is declared by hand too')
        closes = frames[opening['closes']] if opening['closes'] is not None else None
        close = None
        if closes is not None:   # a later moment shows the outside again: held open, then shut as fast as it opened, before it
            held, shut = round(last['at'] * (count - 1)), round(closes['at'] * (count - 1))
            close = [max(held, shut - round(method['open_s'] * fps)), max(held + 1, shut)]
        row = {'action_id': action_id, 'how': opening['how'], 'parts': parts, 'why': opening['why'], 'at': first['at'], 'open': [start, on],
               'close': close, 'in_front': {o.name: round(s, 3) for o, s in sorted(front.items(), key=lambda kv: -kv[1])}}
        if method['opens'] == 'cut':
            clearance = float(move.get('clearance_m') or (move.get('guards') or {}).get('min_clearance_m') or 0.0)
            placed = show_core.cutter(method['extent'], part_boxes, subject_box, toward, right, up, clearance)
            name = f'{action_id}_cutter'
            _cutter(name, placed, right, up, toward)
            full, shut = list(placed['half']), [h * 1e-3 for h in placed['half']]
            keys = [{'t': 0.0, 'scale': full}] if on == 0 else [{'t': 0.0, 'scale': shut}, {'t': 1.0, 'scale': full}]
            if len(keys) == 1:
                keys.append({'t': 1.0, 'scale': full})
            shot['actions'].append({'action_id': action_id, 'type': 'reveal', 'start_frame': start, 'end_frame': max(on, start + 1),
                                    'easing': 'ease_in_out', 'targets': [_target_of(o) for o in front],
                                    'params': {'cutter_object_id': name, 'cap_material_id': _cap().name, 'cutter_keys': keys,
                                               **({'close': {'start_frame': close[0], 'end_frame': close[1]}} if close else {})}})
            row['cutter'] = {'centre': [round(c, 4) for c in placed['centre']], 'half': [round(h, 4) for h in placed['half']]}
            row['toward'], row['reach_m'] = [round(c, 6) for c in toward], round(reach, 4)   # what check_cuts looks along
        elif method['opens'] == 'explode':
            if on == 0:
                raise ValueError(f"SHOW_EXPLODE_AT_START: an explode at the shot's first moment has no time to happen - draw the first "
                                 'picture before it comes apart, or show that moment as a section / x-ray')
            height = max(parts_box[1][i] - parts_box[0][i] for i in range(3))
            moved = [o for o in front if o.name not in exploded]   # one that an open explode already moved stays moved
            for k, obj in enumerate(moved):
                axis = _local_axis(obj, toward)
                sub_id = f'{action_id}_{k}'
                shot['actions'].append({'action_id': sub_id, 'type': 'explode', 'start_frame': start, 'end_frame': max(on, start + 2),
                                        'easing': 'ease_in_out', 'targets': [_target_of(obj)],
                                        'params': {'distance_m': round(1.2 * height, 4), 'direction_source': 'axis', 'axis': axis}})
                if close:   # a later outside moment: it comes back together
                    shot['actions'].append({'action_id': f'{sub_id}_back', 'type': 'assemble', 'start_frame': close[0],
                                            'end_frame': max(close[1], close[0] + 2), 'easing': 'ease_in_out',
                                            'targets': [_target_of(obj)], 'params': {'source_action_id': sub_id}})
                else:
                    exploded.add(obj.name)
            explode = True
        else:   # see-through: built after the look (see_through)
            for obj in front:   # rays pass through it while it is see-through (camera_frame.passes_through)
                # rays pass it from the middle of its fade (half see-through) until it has shut again
                window = [(start + on) // 2 + 1, ((close[0] + close[1]) // 2 + 1) if close else 10 ** 6]
                camera_frame.add_see_window(obj, window)   # beside a declared cheat's window on it (cheat_scene.py)
            row['see_through'] = {'alpha': method['alpha'], 'rim': method['rim']}
            shot.setdefault('_see_through', []).append({**row, 'objects': [o.name for o in front]})
        report.append(row)
    if explode:
        from scene_tools import apply_actions
        apply_actions(shot)
    bpy.context.scene.frame_set(1)
    return report


def _wrap(material, alpha, rim):
    """A copy of material whose surface mixes toward a see-through shader by the object's SEE_PROP (0 opaque .. 1 at
    `alpha`); rim adds a Fresnel glow at grazing angles (the x-ray outline)."""
    copy = material.copy() if material is not None else bpy.data.materials.new('StudioSeeThrough')
    copy.use_nodes = True
    nodes, links = copy.node_tree.nodes, copy.node_tree.links
    output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None) or nodes.new('ShaderNodeOutputMaterial')
    surface = output.inputs['Surface'].links[0].from_socket if output.inputs['Surface'].links else None
    if surface is None:
        surface = nodes.new('ShaderNodeBsdfPrincipled').outputs[0]
    clear = nodes.new('ShaderNodeBsdfTransparent')
    if rim:
        weight = nodes.new('ShaderNodeLayerWeight'); weight.inputs['Blend'].default_value = 0.35
        glow = nodes.new('ShaderNodeEmission'); glow.inputs['Color'].default_value = (0.55, 0.8, 1.0, 1.0); glow.inputs['Strength'].default_value = 2.0
        edge = nodes.new('ShaderNodeMixShader')
        links.new(weight.outputs['Facing'], edge.inputs['Fac']); links.new(clear.outputs[0], edge.inputs[1]); links.new(glow.outputs[0], edge.inputs[2])
        clear_out = edge.outputs[0]
    else:
        clear_out = clear.outputs[0]
    amount = nodes.new('ShaderNodeAttribute'); amount.attribute_type = 'OBJECT'; amount.attribute_name = SEE_PROP
    scale = nodes.new('ShaderNodeMath'); scale.operation = 'MULTIPLY'; scale.inputs[1].default_value = alpha
    links.new(amount.outputs['Fac'], scale.inputs[0])
    mix = nodes.new('ShaderNodeMixShader')
    links.new(scale.outputs[0], mix.inputs['Fac']); links.new(surface, mix.inputs[1]); links.new(clear_out, mix.inputs[2])
    links.new(mix.outputs[0], output.inputs['Surface'])
    if hasattr(copy, 'surface_render_method'):
        copy.surface_render_method = 'DITHERED'
    copy['studio_see_through_of'] = material.name if material is not None else ''
    return copy


def see_through(job):
    """Fade what is in front of the parts of each see-through moment, after the look (its clay pass replaces every
    material): each object's materials wrapped (_wrap), SEE_PROP keyed 0 -> 1 over the opening, back to 0 when the
    moment closes. The rows come from the openings (apply) and the declared cheats (cheat_scene.apply), in any order:
    an object's rows are taken by when they open, and one that opens while the object is still see-through joins that
    window up to the later close - rows apart stay apart. Returns report rows."""
    rows = sorted(job['shot'].pop('_see_through', []), key=lambda r: r['open'][0])
    out, spans = [], {}   # object -> [{start, on, close, end}] (end: the frame it is solid again, inf while open)
    for row in rows:
        start, on = row['open']
        for name in row['objects']:
            obj = bpy.data.objects.get(name)
            if obj is None or obj.type != 'MESH':
                continue
            slots = obj.material_slots
            if not slots:
                obj.data.materials.append(None)
            for slot in obj.material_slots if not obj.get('studio_see_through_wrapped') else ():
                original = slot.material   # the mesh's own (data) material, before the slot is made the object's
                slot.link = 'OBJECT'
                slot.material = _wrap(original, row['see_through']['alpha'], row['see_through']['rim'])
            obj['studio_see_through_wrapped'] = True
            end = (row['close'][1] + 1) if row.get('close') else float('inf')
            held = spans.setdefault(name, [])
            if held and start + 1 < held[-1]['end']:   # still see-through when this opens: one window, to the later close
                if end > held[-1]['end']:
                    held[-1].update(close=row.get('close'), end=end)
            else:
                held.append({'start': start, 'on': on, 'close': row.get('close'), 'end': end})
        out.append({k: row[k] for k in ('action_id', 'how', 'objects', 'open', 'close')})
    for name, windows in spans.items():
        obj = bpy.data.objects[name]
        for w in windows:
            keys = [(w['start'] + 1, 0.0), (w['on'] + 1, 1.0)] if w['on'] > 0 else [(1, 1.0)]
            if w['close']:
                keys += [(w['close'][0] + 1, 1.0), (w['close'][1] + 1, 0.0)]
            for frame, value in keys:
                obj[SEE_PROP] = value
                obj.keyframe_insert(f'["{SEE_PROP}"]', frame=frame)
    return out
