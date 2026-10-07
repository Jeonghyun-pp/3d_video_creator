"""Typed, allow-listed tools for the resident Blender workbench (workbench_server.py) and its replay.

Every tool is ``fn(state, **args) -> dict``. Read tools never change the scene. Write tools change it
deterministically and are recorded in ops.jsonl; ``replay`` re-applies the same calls inside a normal
``shot build`` (via the generated patch script), so a workbench session is never the source of truth:
a version exists only when the build reproduced the session's measurements (``expect``).

Ownership rule: data a subject spec owns (its parts' geometry, placement and spec materials) changes
only through ``set_spec_param``; ``set_transform`` / ``set_material_param`` refuse spec-owned data so the
spec, the version and the session can never disagree.

state = {'specs': {subject_id: spec}, 'session_dir': Path, 'project_dir': str, 'allow_exec': bool,
         'checkpoints': {name: path}}
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import time

import bpy
from mathutils import Euler, Matrix, Vector

import id_view   # studio/blender_ops on sys.path

READ = 'read'
WRITE = 'write'
CONTROL = 'control'
LIT_LIGHTS = ('scene', 'studio')
VIEW_DIRS = {  # camera looks along -Z of its frame; (rotation XYZ deg, axis index of depth)
    'front': ((90, 0, 0), 1),    # looking along +Y at the -Y face
    'back': ((90, 0, 180), 1),
    'side': ((90, 0, 90), 0),    # looking along -X at the +X face
    'top': ((0, 0, 0), 2),
}


def _objects():
    return sorted(bpy.context.scene.objects, key=lambda o: (o.get('studio_id') or o.name, o.name))


def _by_id(identifier):
    if identifier == '@camera':
        if bpy.context.scene.camera is None:
            raise ValueError('scene has no camera')
        return bpy.context.scene.camera
    obj = next((o for o in bpy.context.scene.objects if o.get('studio_id') == identifier), None)
    if obj is None:
        obj = bpy.context.scene.objects.get(identifier)
    if obj is None:
        raise ValueError(f'no object with studio_id or name {identifier!r}')
    return obj


def _spec_owned(obj):
    return obj.get('studio_subject_id') is not None


def _world_box(obj):
    if obj.type != 'MESH':
        return None
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    return [[round(v, 5) for v in lo], [round(v, 5) for v in hi]]


def _modifier_inputs(mod):
    if mod.type != 'NODES':
        return None
    found = {}
    properties = getattr(mod, 'properties', None)  # Blender 5.2: mod.properties.inputs.<Socket_n>.value
    inputs = getattr(properties, 'inputs', None) if properties is not None else None
    if inputs is not None:
        for key in dir(inputs):
            if key.startswith('Socket_'):
                value = getattr(getattr(inputs, key), 'value', None)
                found[key] = value if isinstance(value, (int, float, str, bool)) else str(value)
    else:
        for key in mod.keys():
            if key.startswith('Socket_') and not key.endswith(('_use_attribute', '_attribute_name')):
                value = mod[key]
                found[key] = value if isinstance(value, (int, float, str, bool)) else str(value)
    return found


# ---- read tools -------------------------------------------------------------------------------------

def scene_graph(state, filter=None, fields=None, limit=50, offset=0):
    """Objects with type, parent, tags, world bbox, dimensions and modifiers; paged."""
    bpy.context.view_layer.update()
    wanted = set(fields or ['type', 'parent', 'studio_id', 'part', 'subject', 'box', 'dimensions', 'modifiers', 'materials'])
    rows = []
    for obj in _objects():
        key = obj.get('studio_id') or obj.name
        if filter and filter.lower() not in key.lower() and filter.lower() not in obj.name.lower():
            continue
        row = {'name': obj.name}
        if 'type' in wanted: row['type'] = obj.type
        if 'parent' in wanted: row['parent'] = obj.parent.get('studio_id') or obj.parent.name if obj.parent else None
        if 'studio_id' in wanted: row['studio_id'] = obj.get('studio_id')
        if 'part' in wanted and obj.get('studio_part_id'): row['part'] = obj['studio_part_id']
        if 'subject' in wanted and obj.get('studio_subject_id'): row['subject'] = obj['studio_subject_id']
        if 'box' in wanted: row['box'] = _world_box(obj)
        if 'dimensions' in wanted: row['dimensions'] = [round(v, 5) for v in obj.dimensions]
        if 'modifiers' in wanted and obj.modifiers:
            row['modifiers'] = [{'name': m.name, 'type': m.type, **({'inputs': _modifier_inputs(m)} if m.type == 'NODES' else {})} for m in obj.modifiers]
        if 'materials' in wanted and obj.type == 'MESH':
            row['materials'] = [m.name for m in obj.data.materials if m]
        rows.append(row)
    return {'total': len(rows), 'offset': offset, 'objects': rows[offset:offset + limit]}


def measure(state, target=None, pair=None):
    """World bbox/size/location of one object, or the min surface distance and overlap of a pair."""
    bpy.context.view_layer.update()
    if target:
        obj = _by_id(target)
        return {'target': target, 'location': [round(v, 6) for v in obj.matrix_world.translation], 'box': _world_box(obj),
                'dimensions': [round(v, 6) for v in obj.dimensions]}
    if pair:
        from geom_checks import pair_distance
        a, b = _by_id(pair[0]), _by_id(pair[1])
        d = pair_distance(a, b, bpy.context.evaluated_depsgraph_get(), search=1e3)
        return {'pair': pair, 'distance_m': None if math.isinf(d) else round(d, 6)}
    raise ValueError('measure needs target or pair')


def subject_report(state, subject_id, views=None, geometry_only=True):
    """Raw fidelity geometry of a built subject (host turns it into the fidelity report)."""
    from fidelity import measure_subject
    spec = state['specs'][subject_id]
    geometry = measure_subject(spec, geometry_only=True, views=views) if geometry_only else \
        measure_subject(spec, state.get('shot'), state.get('output_size'))
    return {'subject_id': subject_id, 'geometry': geometry}


def api_lookup(state, path):
    """Live RNA introspection: properties (type, enum items), functions (parameters) of a bpy type."""
    name = path.split('.')[-1] if path.startswith('bpy.types.') else path.split('.')[0]
    member = None if path.startswith('bpy.types.') else (path.split('.', 1)[1] if '.' in path else None)
    cls = getattr(bpy.types, name, None)
    if cls is None:
        close = sorted(n for n in dir(bpy.types) if name.lower() in n.lower())[:20]
        return {'path': path, 'found': False, 'similar': close}
    rna = cls.bl_rna
    def prop_row(p):
        row = {'id': p.identifier, 'type': p.type, 'readonly': p.is_readonly, 'description': p.description}
        if p.type == 'ENUM':
            row['items'] = [i.identifier for i in p.enum_items]
        if p.type == 'POINTER':
            row['fixed_type'] = p.fixed_type.identifier
        if p.type == 'COLLECTION':
            row['fixed_type'] = p.fixed_type.identifier
        return row
    props = [prop_row(p) for p in rna.properties if p.identifier != 'rna_type']
    funcs = [{'id': f.identifier, 'description': f.description, 'parameters': [prop_row(p) for p in f.parameters]} for f in rna.functions]
    if member:
        props = [p for p in props if p['id'] == member]
        funcs = [f for f in funcs if f['id'] == member]
    return {'path': path, 'found': True, 'type': rna.identifier, 'base': rna.base.identifier if rna.base else None,
            'description': rna.description, 'properties': props, 'functions': funcs, 'blender_version': bpy.app.version_string}


# ---- write tools ------------------------------------------------------------------------------------

def _spec_file(state, subject_id):
    path = Path(state['session_dir']) / 'specs' / f'{subject_id}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state['specs'][subject_id], ensure_ascii=False, indent=2))
    return str(path)


def build_subject(state, subject_id, root_location=None, replace=True):
    from modeling import build_subject as build
    from modeling.assemble import _subject_root
    spec = state['specs'][subject_id]
    old = _subject_root(subject_id)
    location = tuple(root_location) if root_location is not None else (tuple(old.location) if old else (0.0, 0.0, 0.0))
    result = build(spec, root_location=location, replace=replace)
    _spec_file(state, subject_id)
    return {'subject_id': subject_id, 'parts': sorted(result['parts']), 'relations': result['relations'], 'root_location': list(location)}


def set_spec_param(state, subject_id, pointer, value):
    """Change one value of the session's spec copy, then rebuild only what it affects."""
    from modeling import rebuild_parts
    import sys
    spec = copy.deepcopy(state['specs'][subject_id])
    keys = pointer.split('/')[1:]
    node = spec
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    last = keys[-1]
    old = node[int(last)] if isinstance(node, list) else node.get(last)
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    state['specs'][subject_id] = spec
    if keys[0] == 'builders' and len(keys) > 2:
        part = spec['builders'][int(keys[1])]['part_id']
        result = rebuild_parts(spec, [part])
        scope = result['rebuilt']
    elif keys[0] in ('relations', 'materials'):
        result = rebuild_parts(spec, [])
        if keys[0] == 'materials':
            from modeling.assemble import _apply_materials, scene_parts
            _apply_materials(spec, scene_parts(subject_id))
        scope = keys[0]
    else:
        build_subject(state, subject_id)
        scope = 'subject'
    _spec_file(state, subject_id)
    return {'subject_id': subject_id, 'pointer': pointer, 'old': old, 'new': value, 'rebuilt': scope}


def set_spec(state, subject_id, spec):
    """Replace the session's spec copy whole (fitter batches); rebuild only builders that changed when the
    part list is unchanged, else the whole subject."""
    from modeling import rebuild_parts
    from modeling.assemble import _apply_materials, _subject_root, scene_parts
    if spec.get('subject_id') != subject_id:
        raise ValueError('spec subject_id mismatch')
    old = state['specs'].get(subject_id)
    state['specs'][subject_id] = copy.deepcopy(spec)
    same_parts = old is not None and [b['part_id'] for b in old['builders']] == [b['part_id'] for b in spec['builders']]
    if not same_parts or _subject_root(subject_id) is None:
        return build_subject(state, subject_id)
    changed = [b['part_id'] for a, b in zip(old['builders'], spec['builders']) if a != b]
    if changed or old.get('relations') != spec.get('relations'):
        rebuild_parts(spec, changed)
    if old.get('materials') != spec.get('materials'):
        _apply_materials(spec, scene_parts(subject_id))
    _spec_file(state, subject_id)
    return {'subject_id': subject_id, 'rebuilt': changed}


def set_transform(state, id, location=None, rotation_deg=None, scale=None):
    obj = _by_id(id)
    if _spec_owned(obj):
        raise ValueError(f'{id} belongs to subject {obj["studio_subject_id"]}; change /builders/<i>/transform or relations with set_spec_param')
    if location is not None:
        obj.location = location
    if rotation_deg is not None:
        obj.rotation_mode = 'XYZ'
        obj.rotation_euler = Euler([math.radians(a) for a in rotation_deg], 'XYZ')
    if scale is not None:
        obj.scale = scale
    bpy.context.view_layer.update()
    result = {'id': id, 'location': [round(v, 6) for v in obj.location], 'rotation_deg': [round(math.degrees(a), 4) for a in obj.rotation_euler],
              'scale': [round(v, 6) for v in obj.scale]}
    from scene_tools import curves
    keyed = sorted({c.data_path for c in curves(obj.animation_data.action if obj.animation_data else None)
                    if c.data_path in ('location', 'rotation_euler', 'rotation_quaternion', 'scale')})
    if keyed:  # the next frame change re-evaluates these curves and silently undoes this edit
        result['warnings'] = [f'animated_target_overridden: {id} has keyed {keyed}; change its keys (or the author animation) instead']
    return result


def set_modifier_input(state, id, modifier, socket, value):
    obj = _by_id(id)
    if _spec_owned(obj):
        raise ValueError(f'{id} is spec-owned; change the spec instead')
    mod = obj.modifiers[modifier]
    properties = getattr(mod, 'properties', None)
    inputs = getattr(properties, 'inputs', None) if properties is not None else None
    if inputs is not None and hasattr(inputs, socket):
        getattr(inputs, socket).value = value
    else:
        mod[socket] = value
    obj.update_tag()
    bpy.context.view_layer.update()
    return {'id': id, 'modifier': modifier, 'socket': socket, 'value': value}


def set_material_param(state, material, input, value):
    mat = bpy.data.materials[material]
    if any(mat.name.startswith(f'{sid}/material.') for sid in state['specs']):
        raise ValueError(f'{material} is a spec material; change /materials/<i> with set_spec_param')
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    socket = bsdf.inputs[input]
    socket.default_value = tuple(value) if isinstance(value, list) else value
    return {'material': material, 'input': input, 'value': value}


def set_camera_rig(state, rig):
    """Bake a camera rig variant exactly as shot build would (camera_rig.bake_camera_rig) and report its guards.

    The rig is shot-owned: commit writes it into shot.json and the build re-bakes it, so the session's
    keys are never replayed. Use this (not set_camera_keys) for shots whose camera comes from a rig."""
    from camera_rig import bake_camera_rig
    if not isinstance(rig, dict) or 'type' not in rig:
        raise ValueError('rig must be a camera.rig object with a type (see references/camera_rig.md)')
    shot = copy.deepcopy(state['shot'])
    shot['camera']['rig'] = rig
    shot['camera']['movement'] = 'rig'
    report = bake_camera_rig({'shot': shot, 'fps': state['fps'], 'project_dir': state['project_dir'], 'output_size': state.get('output_size')})
    state['shot'] = shot
    state['last_rig'] = {'summary': report['summary'], 'gate_failures': report['gate_failures'][:10], 'warnings': report['warnings'][:10]}
    return state['last_rig']


def set_camera_keys(state, keys, camera=None):
    """Replace the camera's transform/lens keys: [{frame, location, rotation_deg, lens?}] (frames 1-based)."""
    scene = bpy.context.scene
    cam = _by_id(camera) if camera else scene.camera
    if cam is None or cam.type != 'CAMERA':
        raise ValueError('no camera')
    cam.animation_data_clear(); cam.data.animation_data_clear()
    for key in keys:
        cam.location = key['location']
        cam.rotation_mode = 'XYZ'
        cam.rotation_euler = Euler([math.radians(a) for a in key['rotation_deg']], 'XYZ')
        cam.keyframe_insert('location', frame=key['frame']); cam.keyframe_insert('rotation_euler', frame=key['frame'])
        if 'lens' in key:
            cam.data.lens = key['lens']; cam.data.keyframe_insert('lens', frame=key['frame'])
    scene.camera = cam
    scene['studio_authored_animation'] = True
    return {'camera': cam.get('studio_id') or cam.name, 'keys': len(keys)}


def apply_shot(state, shot, job, replay_ops, pristine, layout=None):
    """Shot content changed (studio/workbench.py set_shot_value, validated on the host by the shot edit grammar): the
    session becomes what a build of that shot makes - the authored checkpoint (or, when the scene data changed, the
    scene built fresh from it), the session's other write ops replayed, then the build's own generator chain
    (generate.py: fill, camera, actions, drives, move, reveals, simulations, rig, graphics, look). One state rule:
    session = f(authored checkpoint, write ops, shot), so later writes regenerate the same way."""
    import shutil
    import generate
    if layout is not None:   # a fresh build starts from the factory scene with its objects removed, then the layout
        bpy.ops.wm.read_factory_settings(use_empty=False)
        bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
        import layout as declarative
        declarative.build({**job, 'shot': shot}, layout)
    else:
        bpy.ops.wm.open_mainfile(filepath=pristine, load_ui=False)
    out = Path(state['session_dir']) / 'generated'
    shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    replay({**job, 'shot': shot, 'output_dir': str(out)}, replay_ops, state['specs'])
    result = generate.generate({**job, 'shot': copy.deepcopy(shot), 'output_dir': str(out)}, out)
    state['shot'], state['generated'] = copy.deepcopy(shot), True
    rig = result.get('rig_report') or {}
    move = result.get('move_report') or {}
    return {'generated': True, 'camera_cues': move.get('camera_cues'), 'rig_gate_failures': (rig.get('gate_failures') or [])[:10],
            'rig_warnings': (rig.get('warnings') or [])[:10], 'reports': sorted(p.name for p in out.glob('*.json'))}


def current_shot(state):
    return {'shot': state.get('shot'), 'generated': bool(state.get('generated'))}


def generated_snapshot(frames, subject_ids=(), object_ids=()):
    """What a generated session shows, for the build to reproduce after its own generators: the camera (world matrix and
    lens) at sample frames, plus the subjects and touched objects at frame 1."""
    scene = bpy.context.scene
    out = snapshot(subject_ids, object_ids)
    out['camera'] = {}
    for frame in frames:
        scene.frame_set(frame + 1)
        cam = scene.camera
        out['camera'][str(frame)] = [round(v, 5) for row in cam.matrix_world for v in row] + [round(cam.data.lens, 4)]
    scene.frame_set(1)
    return out


# ---- preview ----------------------------------------------------------------------------------------

def part_color(key):
    """Stable 8-bit sRGB colour per part key (never pure black: that is the background)."""
    h = hashlib.sha256(key.encode()).digest()
    return tuple(40 + (b % 200) for b in h[:3])


def _srgb_to_linear(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _frame_box(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type == 'MESH' for c in o.bound_box]
    if not pts:
        return Vector((0, 0, 0)), Vector((1, 1, 1))
    lo = Vector([min(p[i] for p in pts) for i in range(3)]); hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi


def preview(state, views=('front', 'side', 'top', 'shot'), passes=('shaded', 'id'), size=512, frame=1, subject_id=None, frames=None,
            lit_samples=16, lit_light='scene'):
    """preview at one frame, or at several (``frames``) with results keyed '<view>@<frame>' so camera and
    motion alternatives can be compared at the same moments."""
    lit = {'samples': int(lit_samples), 'light': lit_light}
    if lit_light not in LIT_LIGHTS:
        raise ValueError(f'lit_light must be one of {LIT_LIGHTS}')
    if not frames:
        return _preview_frame(state, views, passes, size, frame, subject_id, lit)
    merged = {'images': {}, 'pixels': {}, 'anchors_px': {}, 'palette': {}, 'size': size, 'frames': list(frames)}
    for f in frames:
        one = _preview_frame(state, views, passes, size, int(f), subject_id, lit)
        merged['palette'].update(one['palette'])
        for key in ('images', 'pixels', 'anchors_px'):
            merged[key].update({f'{view}@{f}': value for view, value in one[key].items()})
    return merged


def _view_name(view, index):
    return view if isinstance(view, str) else str(view.get('name') or f'camera{index}')


def _orbit_camera(scene, view):
    """A temporary perspective camera from an orbit camera dict (view_match_core), e.g. a solved photo view."""
    from view_match_core import full, pose
    from mathutils import Matrix, Quaternion
    cam = full({k: v for k, v in view.items() if k not in ('name', 'frame_subject')})
    eye, q, _, _ = pose(cam)
    frame = Matrix.Identity(4)
    if view.get('frame_subject'):   # the camera is in that subject's root frame (as a reference view's camera is)
        frame = _built_root(view['frame_subject']).matrix_world.copy()
    data = bpy.data.cameras.new('studio_wb_view')
    data.lens, data.sensor_fit = cam['lens_mm'], cam['sensor_fit']
    data.sensor_width = data.sensor_height = cam['sensor_mm']
    data.shift_x, data.shift_y = cam['shift_x'], cam['shift_y']
    data.clip_start, data.clip_end = max(1e-3, cam['distance_m'] * 1e-3), cam['distance_m'] * 100
    obj = bpy.data.objects.new('studio_wb_view', data)
    obj.matrix_world = frame @ Matrix.LocRotScale(eye, Quaternion(q), None)
    scene.collection.objects.link(obj)
    return obj, (cam['width'], cam['height'])


def _studio_light(scene, cam):
    """lit_light 'studio': a neutral grey world and a key light over the camera's shoulder, removed afterwards."""
    world = bpy.data.worlds.new('studio_wb_world')
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    bg.inputs['Color'].default_value = (0.6, 0.6, 0.6, 1.0)
    bg.inputs['Strength'].default_value = 0.6
    data = bpy.data.lights.new('studio_wb_key', 'SUN')
    data.energy, data.angle = 3.0, math.radians(8)
    key = bpy.data.objects.new('studio_wb_key', data)
    key.rotation_euler = (cam.matrix_world.to_euler('XYZ')[0] - math.radians(20), 0.0, cam.matrix_world.to_euler('XYZ')[2] + math.radians(35))
    scene.collection.objects.link(key)
    saved, scene.world = scene.world, world
    return saved, world, key


def _preview_frame(state, views, passes, size, frame, subject_id, lit=None):
    """Workbench stills per view: 'shaded' (studio solid), 'id' (flat per-part colour, no AA, no dither) and 'lit'
    (Cycles, ``lit['samples']`` samples on the GPU when present, the scene's lights and materials - or a neutral
    studio light with lit['light'] 'studio' - for judging appearance against a reference photo).

    A view is 'front' / 'side' / 'top' / ... (orthographic, framing the subject or every mesh), 'shot' (the scene
    camera) or an orbit camera dict (view_match_core keys + optional name; the image takes its width:height).
    Returns image
    paths, the colour->part table, per-part visible pixel counts of each id image, and 2D projections of
    each part's centre anchor (for overlays)."""
    import numpy as np
    from bpy_extras.object_utils import world_to_camera_view
    scene = bpy.context.scene
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    out_dir = Path(state['session_dir']) / 'preview' / f'{int(time.time() * 1000)}'
    out_dir.mkdir(parents=True, exist_ok=True)
    meshes = [o for o in scene.objects if o.type == 'MESH' and not o.hide_render]
    focus = [o for o in meshes if o.get('studio_subject_id') == subject_id] if subject_id else meshes
    r, disp, sh = scene.render, scene.display, scene.display.shading
    # id and shaded passes take id_view's neutral settings and give them back after each render; the lit pass keeps the
    # scene's own view transform, exposure, white balance and compositor (what the real render uses) and changes only these
    saved = {'engine': r.engine, 'res': (r.resolution_x, r.resolution_y, r.resolution_percentage), 'film': r.film_transparent,
             'camera': scene.camera, 'path': r.filepath, 'colors': {o.name: tuple(o.color) for o in meshes},
             'fmt': r.image_settings.file_format, 'mode': r.image_settings.color_mode}
    keys = {}
    lit = lit or {'samples': 16, 'light': 'scene'}
    saved['cycles'] = (scene.cycles.samples, scene.cycles.device) if 'lit' in passes else None
    for o in meshes:
        key = f"{o.get('studio_subject_id')}/{o.get('studio_part_id')}" if o.get('studio_part_id') else (o.get('studio_id') or o.name)
        keys[o.name] = key
    palette = {}
    for key in sorted(set(keys.values())):
        rgb = part_color(key)
        while '#%02x%02x%02x' % rgb in palette:
            rgb = tuple((c + 7) % 200 + 40 for c in rgb)
        palette['#%02x%02x%02x' % rgb] = key
    color_of = {v: k for k, v in palette.items()}
    temp_cam = None
    temp_views = []
    images, pixels, anchors2d = {}, {}, {}
    try:
        r.resolution_x = r.resolution_y = int(size); r.resolution_percentage = 100
        cam_data = bpy.data.cameras.new('studio_wb_cam'); cam_data.type = 'ORTHO'
        temp_cam = bpy.data.objects.new('studio_wb_cam', cam_data); scene.collection.objects.link(temp_cam)
        lo, hi = _frame_box(focus)
        center, extent = (lo + hi) / 2, hi - lo
        for index, spec_view in enumerate(views):
            view = _view_name(spec_view, index)
            if isinstance(spec_view, dict):
                cam, (w, h) = _orbit_camera(scene, spec_view)
                temp_views.append(cam)
                scale = int(size) / max(w, h)
                r.resolution_x, r.resolution_y = max(2, int(w * scale)), max(2, int(h * scale))
            elif view == 'shot':
                if saved['camera'] is None:
                    continue
                cam = saved['camera']
                r.resolution_x, r.resolution_y = state.get('output_size', (int(size), int(size)))
                scale = int(size) / max(r.resolution_x, r.resolution_y)
                r.resolution_x, r.resolution_y = max(2, int(r.resolution_x * scale)), max(2, int(r.resolution_y * scale))
            else:
                rot, depth_axis = VIEW_DIRS[view]
                temp_cam.rotation_euler = Euler([math.radians(a) for a in rot], 'XYZ')
                direction = temp_cam.rotation_euler.to_matrix() @ Vector((0, 0, 1))
                temp_cam.location = center + direction * (extent.length + 1.0)
                plane = [i for i in range(3) if i != depth_axis]
                cam_data.ortho_scale = max(extent[plane[0]], extent[plane[1]]) * 1.1 or 1.0
                cam_data.clip_end = (extent.length + 1.0) * 3
                cam = temp_cam
                r.resolution_x = r.resolution_y = int(size)
            scene.camera = cam
            bpy.context.view_layer.update()
            images[view] = {}
            for kind in passes:
                if kind == 'lit':
                    from render_profile import select_device
                    r.engine = 'CYCLES'
                    scene.cycles.samples = lit['samples']
                    select_device(scene, 'GPU')
                    r.film_transparent = False
                    r.image_settings.file_format, r.image_settings.color_mode = 'PNG', 'RGB'
                    lights = _studio_light(scene, cam) if lit['light'] == 'studio' else None
                    path = out_dir / f'{view}_{kind}.png'
                    r.filepath = str(path)
                    try:
                        bpy.ops.render.render(write_still=True)
                    finally:
                        r.engine, r.film_transparent = saved['engine'], saved['film']
                        if lights:
                            scene.world = lights[0]
                            bpy.data.worlds.remove(lights[1])
                            key_data = lights[2].data
                            bpy.data.objects.remove(lights[2], do_unlink=True)
                            bpy.data.lights.remove(key_data)
                    images[view][kind] = str(path)
                    continue
                if kind not in ('id', 'shaded'):
                    raise ValueError(f'unknown pass {kind!r} (passes: id, shaded, lit)')
                neutral = id_view.apply(scene)   # every setting it changes is given back below, whatever the scene's look
                try:
                    if kind == 'id':
                        for o in meshes:
                            rgb = [int(color_of[keys[o.name]][i:i + 2], 16) for i in (1, 3, 5)]
                            o.color = (*[_srgb_to_linear(c) for c in rgb], 1.0)
                    else:   # studio solid on the same neutral view; these overrides are id_view entries, so restored too
                        disp.render_aa = '8'
                        sh.light = 'STUDIO'; sh.color_type = 'MATERIAL'; sh.show_cavity = True; sh.show_object_outline = True
                        sh.show_specular_highlight = True
                        for o in meshes:
                            o.color = saved['colors'][o.name]
                    path = out_dir / f'{view}_{kind}.png'
                    r.filepath = str(path)
                    bpy.ops.render.render(write_still=True)
                finally:
                    id_view.restore(scene, neutral)
                images[view][kind] = str(path)
                if kind == 'id':
                    img = bpy.data.images.load(str(path))
                    try:
                        buf = np.empty(len(img.pixels), dtype=np.float32); img.pixels.foreach_get(buf)
                    finally:
                        bpy.data.images.remove(img)
                    px = (buf.reshape(-1, 4) * 255 + 0.5).astype(np.int32)
                    px = px[px[:, 3] > 250]
                    codes = px[:, 0] * 65536 + px[:, 1] * 256 + px[:, 2]
                    uniq, counts = np.unique(codes, return_counts=True)
                    by_part, unknown = {}, 0
                    for code, count in zip(uniq.tolist(), counts.tolist()):
                        key = palette.get('#%06x' % code)
                        if key is None:
                            unknown += int(count)
                        else:
                            by_part[key] = by_part.get(key, 0) + int(count)
                    pixels[view] = {'parts': dict(sorted(by_part.items())), 'unmatched_px': unknown}
            anchors2d[view] = {}
            for o in focus:
                if o.get('studio_part_id') and o.get('studio_id') == f"{o.get('studio_subject_id')}/{o.get('studio_part_id')}":
                    raw = json.loads(o.get('studio_anchors', '{}'))
                    key = f"{o['studio_id']}/center"
                    if key in raw:
                        ndc = world_to_camera_view(scene, cam, o.matrix_world @ Vector(raw[key]))
                        anchors2d[view][o['studio_part_id']] = [round(ndc.x * r.resolution_x, 1), round((1 - ndc.y) * r.resolution_y, 1)]
    finally:
        r.engine = saved['engine']; r.resolution_x, r.resolution_y, r.resolution_percentage = saved['res']
        if saved['cycles']:
            scene.cycles.samples, scene.cycles.device = saved['cycles']
        for cam_obj in temp_views:
            data = cam_obj.data
            bpy.data.objects.remove(cam_obj, do_unlink=True); bpy.data.cameras.remove(data)
        r.film_transparent = saved['film']
        r.image_settings.file_format, r.image_settings.color_mode = saved['fmt'], saved['mode']
        scene.camera = saved['camera']; r.filepath = saved['path']
        for o in meshes:
            o.color = saved['colors'][o.name]
        if temp_cam is not None:
            data = temp_cam.data
            bpy.data.objects.remove(temp_cam, do_unlink=True); bpy.data.cameras.remove(data)
    return {'images': images, 'palette': palette, 'pixels': pixels, 'anchors_px': anchors2d, 'size': size}


def _built_root(subject_id):
    from modeling.assemble import _subject_root
    root = _subject_root(subject_id)
    if root is None:
        raise ValueError(f'subject {subject_id!r} is not built in this session')
    return root


def anchors(state, subject_id, corners=True):
    """Positions of a subject's named anchors and (``corners``) each part's bounding-box corners in the subject's root
    frame (where it stands in a scene does not matter): the 3D side of the point pairs that solve a reference photo's
    camera (studio/photo_match.solve_pose), so that camera is in the root frame too."""
    to_root = _built_root(subject_id).matrix_world.inverted()
    out = {}
    for o in _objects():
        if o.get('studio_subject_id') != subject_id or o.get('studio_id') != f"{subject_id}/{o.get('studio_part_id')}":
            continue
        for name, local in json.loads(o.get('studio_anchors', '{}')).items():
            out[name] = [round(v, 6) for v in to_root @ o.matrix_world @ Vector(local)]
        if corners and o.type == 'MESH':
            for i, c in enumerate(o.bound_box):
                out[f"{o['studio_id']}/corner{i}"] = [round(v, 6) for v in to_root @ o.matrix_world @ Vector(c)]
    if not out:
        raise ValueError(f'subject {subject_id!r} has no built parts in this session')
    return {'subject_id': subject_id, 'points': dict(sorted(out.items()))}


# ---- snapshots --------------------------------------------------------------------------------------

def checkpoint(state, name):
    path = Path(state['session_dir']) / 'checkpoints' / f'{name}.blend'
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    state.setdefault('checkpoints', {})[name] = {'path': str(path), 'specs': copy.deepcopy(state['specs']), 'generated': bool(state.get('generated')),
                                                 'shot': copy.deepcopy(state.get('shot')), 'last_rig': copy.deepcopy(state.get('last_rig'))}
    return {'checkpoint': name}


def restore(state, name):
    saved = state.get('checkpoints', {}).get(name)
    if saved is None:
        raise ValueError(f'no checkpoint {name!r}')
    bpy.ops.wm.open_mainfile(filepath=saved['path'], load_ui=False)
    state['specs'] = copy.deepcopy(saved['specs'])
    state['shot'], state['last_rig'] = copy.deepcopy(saved.get('shot')), copy.deepcopy(saved.get('last_rig'))
    state['generated'] = saved.get('generated', False)
    for subject_id in state['specs']:
        _spec_file(state, subject_id)
    return {'restored': name}


def variant_save(state, name, note=''):
    """Exploration: keep the current state as a named alternative (checkpoint + note). Costs no version or budget."""
    check_name = f'variant.{name}'
    checkpoint(state, check_name)
    state.setdefault('variants', {})[name] = {'note': note, 'checkpoint': check_name, 'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    return {'variant': name, 'checkpoint': check_name, 'note': note, 'rig': state.get('last_rig')}


def variant_restore(state, name):
    if name not in state.get('variants', {}):
        raise ValueError(f'no variant {name!r} (saved: {sorted(state.get("variants", {}))})')
    restore(state, f'variant.{name}')
    return {'restored_variant': name}


def run_exec(state, code):
    if not state.get('allow_exec'):
        raise PermissionError('exec is disabled for this session (start it with --allow-exec; the session then cannot be committed)')
    state['non_replayable'] = True   # before running: code that raises halfway may still have changed the scene
    namespace = {'bpy': bpy, 'result': None}
    exec(compile(code, '<workbench exec>', 'exec'), namespace)  # noqa: S102 - explicit opt-in, session marked non-replayable
    result = namespace.get('result')
    return {'result': result if isinstance(result, (int, float, str, bool, list, dict, type(None))) else str(result)}


TOOLS = {
    'scene_graph': (scene_graph, READ), 'measure': (measure, READ), 'subject_report': (subject_report, READ),
    'api_lookup': (api_lookup, READ), 'preview': (preview, READ), 'anchors': (anchors, READ),
    'build_subject': (build_subject, WRITE), 'set_spec_param': (set_spec_param, WRITE), 'set_spec': (set_spec, WRITE),
    'set_transform': (set_transform, WRITE), 'set_modifier_input': (set_modifier_input, WRITE),
    'set_material_param': (set_material_param, WRITE), 'set_camera_keys': (set_camera_keys, WRITE), 'set_camera_rig': (set_camera_rig, WRITE),
    'apply_shot': (apply_shot, WRITE), 'current_shot': (current_shot, READ),
    'generated_snapshot': (lambda state, frames=(0,), subject_ids=(), object_ids=(): generated_snapshot(frames, subject_ids, object_ids), READ),
    'checkpoint': (checkpoint, CONTROL), 'restore': (restore, CONTROL), 'exec': (run_exec, CONTROL),
    'variant_save': (variant_save, CONTROL), 'variant_restore': (variant_restore, CONTROL),
    'replay_snapshot': (lambda state, subject_ids=(), object_ids=(): snapshot(subject_ids, object_ids), READ),
}
SPEC_TOOLS = {'build_subject', 'set_spec_param', 'set_spec'}  # replayed by rebuilding from the committed spec
SHOT_TOOLS = {'set_camera_rig', 'apply_shot'}  # committed into shot.json; the build re-bakes them


def call(state, tool, args):
    if tool not in TOOLS:
        raise ValueError(f'unknown tool {tool!r}; allowed: {sorted(TOOLS)}')
    fn, kind = TOOLS[tool]
    try:
        import preserve
        preserve._shader_cache.clear()  # material pointers are reused after deletes; never trust a stale entry
    except ImportError:
        pass
    return fn(state, **(args or {})), kind


# ---- replay (inside shot build) ---------------------------------------------------------------------

def replay(job, ops, specs):
    """Rebuild changed subjects from their committed specs, then re-apply the non-spec write ops."""
    state = {'specs': specs, 'session_dir': job['output_dir'], 'allow_exec': False,
             'output_size': job.get('output_size'), 'shot': job.get('shot')}
    for subject_id in sorted({op['args']['subject_id'] for op in ops if op['tool'] in SPEC_TOOLS}):
        build_subject(state, subject_id)
    for op in ops:
        if op['tool'] not in SPEC_TOOLS | SHOT_TOOLS:
            TOOLS[op['tool']][0](state, **op['args'])
    return state


def snapshot(subject_ids, object_ids):
    """Numbers a replay must reproduce: subject extents and the world matrices of touched objects."""
    from fidelity import measure_subject
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    out = {'subjects': {}, 'objects': {}}
    for subject_id in sorted(subject_ids):
        geometry = measure_subject({'subject_id': subject_id, 'features': [], 'silhouettes': []}, geometry_only=True)
        out['subjects'][subject_id] = {'whole': geometry['whole'], 'parts': geometry['parts']}
    for identifier in sorted(object_ids):
        obj = _by_id(identifier)
        out['objects'][identifier] = [round(v, 6) for row in obj.matrix_world for v in row]
    return out


def compare(expected, actual, tol=1e-4):
    issues = []
    def walk(a, b, path):
        if isinstance(a, dict):
            for k in a:
                if k not in b:
                    issues.append(f'{path}/{k}: missing after replay')
                else:
                    walk(a[k], b[k], f'{path}/{k}')
        elif isinstance(a, list):
            if len(a) != len(b):
                issues.append(f'{path}: length {len(a)} -> {len(b)}')
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f'{path}/{i}')
        elif isinstance(a, (int, float)) and not isinstance(a, bool):
            if not isinstance(b, (int, float)) or abs(a - b) > tol:
                issues.append(f'{path}: {a} -> {b}')
        elif a != b:
            issues.append(f'{path}: {a!r} -> {b!r}')
    walk(expected, actual, '')
    return issues
