"""Frame probe (Blender side): look at the frames through the real scene camera and judge what shows.

Runs at the end of a build, after scene.blend is saved - it changes the open scene freely (object colours, render
settings, visibility) and nothing it does reaches the saved file. A Workbench id pass, FLAT light and OBJECT colour,
no anti-aliasing, 256 px tall at the output aspect, through the scene camera itself so clip planes, lens shift and
sensor fit are exactly what the render uses. Each pixel decodes to one class: a key part, the subject, support
(everything else that renders) or background. The verdicts are frame_probe_core.judge; this file only measures.

job['probe'] (written by the host, studio/blender.py): {role, subjects: [ids], key_parts: [{id, from_frame?, to_frame?,
min_px?}], frames: [extra frames], exempt_frames: [frames], screen: shot.screen or None, ui_rect: [x0, y0, x1, y1]}.
Output: frame_report.json (with a `screen` block: shapes per class and frame, speeds, declared targets - screen_core.py)
and frame_probe/*.png (the id images, which an agent can open to see what the probe saw).
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
import time

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
import numpy as np

import frame_probe_core as core
from camera_frame import see_windows
import id_view
import screen_core
from ids_core import suggest
from scene_index import AnchorIndex, known_ids, resolve_group
from scene_roles import role

HIDDEN_ROLES = {'helper', 'atmosphere', 'graphic'}   # scatter sources stay: instances render with their source's colour
BACKGROUND_IDS = {'StudioBackdrop'}   # the generated backdrop card is the background the subject is set against
HEIGHT_PX = core.PROBE_HEIGHT_PX
LEVELS = (0.0, 0.2140, 1.0)           # linear values that land at sRGB 0, 0.5, 1 under the Standard view transform
SRGB_LEVELS = (0.0, 0.5, 1.0)


def _palette():
    """Class colours: support, subject, then key parts. 27 well-separated colours (per channel 0 / 0.5 / 1 in sRGB)."""
    combos = list(itertools.product(range(3), repeat=3))
    combos.remove((0, 0, 0)); combos.remove((2, 2, 2))
    order = [(1, 1, 1), (2, 0, 0)] + [c for c in combos if c not in ((1, 1, 1), (2, 0, 0))]
    return [(tuple(LEVELS[i] for i in c), tuple(SRGB_LEVELS[i] for i in c)) for c in order]


def _tree(obj):
    return [obj, *obj.children_recursive]


def _classes(probe):
    """({object name: class} for every object, key ids, concealed ids) - the last two in palette order. Every declared
    id is found by scene_index.resolve_group (one rule: as written, layout form, fill copies, <id>.n / <id>/part groups).
    Ids the shot declares on purpose - key and concealed parts, screen.subject, screen.keep - must exist (KEY_PART_UNKNOWN,
    with the closest built ids); subjects inferred from the camera may be points, not objects, and are skipped."""
    index = AnchorIndex()
    out = {}
    named = set(probe.get('subjects', []))   # the shot's subject: what its camera, its subjects list, screen.subject and
    for obj in bpy.data.objects:             # the fill brief's role-subject items name - not every spec-built object (fill copies are built from specs too)
        hidden = role(obj) in HIDDEN_ROLES or obj.type == 'VOLUME' or obj.type.startswith(('GREASEPENCIL', 'GPENCIL')) or obj.get('studio_id') in BACKGROUND_IDS \
            or getattr(obj, 'visible_camera', True) is False
        out[obj.name] = 'hidden' if hidden else ('subject' if obj.get('studio_subject_id') in named else 'support')
    unknown = []
    declared = set(probe.get('declared_subjects', [])) | set(probe.get('keep', []))
    for ident in sorted(declared - named):   # keep ids are checked here, at build time, not first at generation time
        if not resolve_group(ident, index):
            unknown.append(ident)
    roots = [o for o in bpy.data.objects if o.get('studio_fill_role') == 'subject']   # what fill_brief declares role: subject
    for ident in sorted(named):
        found = resolve_group(ident, index)
        if not found and ident in declared:
            unknown.append(ident)
        roots += found
    for obj in roots:
        for o in _tree(obj):
            if out.get(o.name) == 'support':
                out[o.name] = 'subject'
    for obj in bpy.data.objects:   # a collection instance shows its collection's objects in the instancer's class
        if obj.instance_type == 'COLLECTION' and obj.instance_collection and out.get(obj.name) not in ('support', 'hidden', None):
            for source in obj.instance_collection.all_objects:
                if out.get(source.name) == 'support':
                    out[source.name] = out[obj.name]
    keys, resolved, reach = [], [], {}
    for part in probe.get('key_parts', []):
        found = resolve_group(part['id'], index)
        if not found:
            unknown.append(part['id'])
            continue
        keys.append(part['id'])
        resolved += [(part, obj) for obj in found]
        reach[part['id']] = len({o.name for obj in found for o in _tree(obj)})
    depth = lambda o: 0 if o.parent is None else 1 + depth(o.parent)  # noqa: E731
    # parents first: a key part inside another keeps its own class; of two keys over the same objects, the narrower last
    # (a part named on its own beats the feature or group that holds it - 2026-10-17: a feature listed after its tube took
    # the tube's pixels and the tube read 0 px)
    for part, obj in sorted(resolved, key=lambda pair: (depth(pair[1]), -reach[pair[0]['id']])):
        for o in _tree(obj):
            if out.get(o.name) != 'hidden':
                out[o.name] = f"key:{part['id']}"
    # a coat (coats.py: soot, paint, a decal shell on a part) is the part it lies on: drawn in that part's class
    import coats
    from camera_frame import _owner
    by_owner = {}
    for name, cls in out.items():
        if cls.startswith('key:') and name in bpy.data.objects:
            by_owner.setdefault(_owner(bpy.data.objects[name], coat_as_part=False), cls)
    for o in bpy.data.objects:
        of = coats.coat_of(o)
        if of in by_owner and out.get(o.name) != 'hidden' and not out.get(o.name, '').startswith('key:'):
            out[o.name] = by_owner[of]   # a coat declared a key part itself keeps its own class
    concealed, clash = [], []
    for part in probe.get('concealed_parts', []):   # parts an intact view must not show (an exterior hides its valve train)
        found = resolve_group(part['id'], index)
        if not found:
            unknown.append(part['id'])
            continue
        concealed.append(part['id'])
        for o in (o for obj in found for o in _tree(obj)):
            if out.get(o.name, '').startswith('key:'):
                clash.append(f"{o.name} ({out[o.name][4:]} / {part['id']})")
            elif out.get(o.name) != 'hidden':
                out[o.name] = f"hide:{part['id']}"
    if unknown and not probe.get('allow_unknown'):   # keep masks measuring visibility treat an unbuilt part as unseen
        known = known_ids()
        hints = '; '.join(f"{u} (closest built: {', '.join(suggest(u, known)) or 'none'})" for u in unknown)
        raise ValueError(f'KEY_PART_UNKNOWN: no object for declared id(s): {hints}')
    if clash:   # the same object must show and must not show in one shot: the declaration contradicts itself
        raise ValueError(f'KEY_PART_UNKNOWN: objects both a key part and a concealed part: {clash[:6]}')
    return out, keys, concealed


def _setup(scene, classes, keys, concealed=()):
    palette = _palette()
    named = [f'key:{k}' for k in keys] + [f'hide:{c}' for c in concealed]
    if len(named) > len(palette) - 2:
        raise ValueError(f'FRAME_PROBE: {len(keys)} key and {len(concealed)} concealed parts; the id pass separates at most {len(palette) - 2}')
    colour = {'support': palette[0], 'subject': palette[1], **{name: palette[2 + i] for i, name in enumerate(named)}}
    for obj in bpy.data.objects:
        cls = classes.get(obj.name, 'support')
        if cls == 'hidden':
            id_view.hide_always(obj)
        else:
            obj.color = (*colour[cls][0], 1.0)
    render = scene.render
    aspect = render.resolution_x * render.pixel_aspect_x / max(1, render.resolution_y * render.pixel_aspect_y)
    render.resolution_y, render.resolution_x, render.resolution_percentage = HEIGHT_PX, max(16, round(HEIGHT_PX * aspect)), 100
    id_view.apply(scene)   # the probe runs last in the build, on a copy that is never saved: nothing to restore
    return colour


def measure_at(parts, eye, aim, lens_mm, roll_deg, frame, output_size, subjects=(), keys=(), cache=None):
    """{part: thickness in output px} as the build's check reads it (frame_probe_core.thickness_px on this id pass), from
    a camera at `eye` looking at `aim` - one id render for every part, at the build's grid (PROBE_HEIGHT_PX tall at the
    output's aspect), the scene camera's sensor and clip, see-through skins open at the frame left out as the build's
    through-render does. Leaves the scene as it found it: colours, render visibility and its keyed curves, resolution,
    the id-view settings, the camera, the frame. The camera judges its chosen poses by it (camera_moves._intent_pose):
    points rasterised on the grid read a big part thin and a thin rod thick, 2026-10-17 - a body 15 px against the
    build's 180, a piston 30 against 25.
    keys: the shot's key parts (job['probe']['key_parts'] ids): classed as the build classes them - a key part inside a
    must-see part keeps its own class there too - though only `parts` are read. cache: a dict kept for one moment (the
    scene not changing between its poses): the classes are found once in it."""
    import tempfile
    from frame_candidates import _pose
    scene = bpy.context.scene
    render = scene.render
    held = cache.get('measure_at') if cache is not None else None
    wanted = (tuple(parts), tuple(keys), tuple(subjects))
    if held is None or held['wanted'] != wanted:
        ids = list(dict.fromkeys([*keys, *parts]))
        classes, found, _concealed = _classes({'subjects': list(subjects), 'key_parts': [{'id': p} for p in ids], 'allow_unknown': True})
        palette = _palette()
        named = [f'key:{k}' for k in found]
        if len(named) > len(palette) - 2:
            raise ValueError(f'FRAME_PROBE: {len(found)} parts; the id pass separates at most {len(palette) - 2}')
        held = {'wanted': wanted, 'classes': classes, 'keys': found,
                'colour': {'support': palette[0], 'subject': palette[1], **{name: palette[2 + i] for i, name in enumerate(named)}}}
        if cache is not None:
            cache['measure_at'] = held
    classes, found, colour = held['classes'], held['keys'], held['colour']
    saved = {'frame': scene.frame_current, 'camera': scene.camera, 'path': render.filepath,
             'res': (render.resolution_x, render.resolution_y, render.resolution_percentage),
             'colors': {o.name: tuple(o.color) for o in bpy.data.objects}, 'hide': {}}
    muted, settings, cam = [], None, None
    try:
        for obj in bpy.data.objects:
            if classes.get(obj.name) == 'hidden':   # out on every frame, whatever its keyed visibility says (hide_always, restored)
                saved['hide'].setdefault(obj.name, obj.hide_render)
                muted += _mute_visibility(obj)
                obj.hide_render = True
            else:   # an object new since the moment's classes were found reads as support, as _setup reads it
                obj.color = (*colour[classes.get(obj.name, 'support')][0], 1.0)
        width, height = output_size
        aspect = width * render.pixel_aspect_x / max(1e-6, height * render.pixel_aspect_y)   # as _setup reads the output
        render.resolution_y, render.resolution_x, render.resolution_percentage = HEIGHT_PX, max(16, round(HEIGHT_PX * aspect)), 100
        settings = id_view.apply(scene)
        # the scene camera's own intrinsics, all of them (sensor, fit, shift, type, ortho scale, clip): only the pose is ours
        data = saved['camera'].data.copy() if saved['camera'] is not None and saved['camera'].type == 'CAMERA' \
            else bpy.data.cameras.new('frame_probe_measure')
        if data.animation_data:
            data.animation_data_clear()
        cam = bpy.data.objects.new('frame_probe_measure', data)
        scene.collection.objects.link(cam)
        scene.camera = cam
        scene.frame_set(frame)
        _pose(cam, eye, aim, lens_mm, roll_deg or 0.0)
        skins = [o for o in scene.objects if not o.hide_render and any(a <= frame < b for a, b in see_windows(o))]
        # a measured part that is itself see-through is read with the skins drawn, the rest through them (probe's rule)
        faded = faded_keys(skins, classes) & set(parts)
        shapes = {}
        with tempfile.TemporaryDirectory() as tmp:
            for o in skins:
                saved['hide'].setdefault(o.name, o.hide_render)
                muted += _mute_visibility(o)
            for p in sorted(faded):   # its own skin drawn, every other skin left out
                for o in skins:
                    o.hide_render = classes.get(o.name, '') != f'key:{p}'
                labels, names = _decode(_render(scene, Path(tmp) / 'drawn.png'), colour)
                shapes[p] = _shapes(labels, names, [0.0, 0.0, 1.0, 1.0])
            for o in skins:
                o.hide_render = True
            labels, names = _decode(_render(scene, Path(tmp) / 'measure.png'), colour)
            shapes['through'] = _shapes(labels, names, [0.0, 0.0, 1.0, 1.0])
        return {p: round(float(core.thickness_px(shapes[p if p in faded else 'through'].get(f'key:{p}'), output_size)), 1)
                if p in found else 0.0 for p in parts}
    finally:
        for o_name, hidden in saved['hide'].items():
            if o_name in bpy.data.objects:
                bpy.data.objects[o_name].hide_render = hidden
        for curve in muted:
            curve.mute = False
        for o in bpy.data.objects:
            if o.name in saved['colors']:
                o.color = saved['colors'][o.name]
        if settings is not None:
            id_view.restore(scene, settings)
        render.resolution_x, render.resolution_y, render.resolution_percentage = saved['res']
        render.filepath = saved['path']
        scene.camera = saved['camera']
        if cam is not None:
            data = cam.data
            bpy.data.objects.remove(cam, do_unlink=True)
            bpy.data.cameras.remove(data)
        scene.frame_set(saved['frame'])


def faded_keys(skins, classes):
    """The key parts that are themselves see-through: every mesh of the part is among the skins (a sleeve faded round
    its pipe). A key that only holds skins among other things (the whole subject as a key) is read through them."""
    skin_names = {o.name for o in skins}
    members = {}
    for o in bpy.data.objects:
        cls = classes.get(o.name, '')
        if cls.startswith('key:') and o.type == 'MESH' and not o.hide_render:
            members.setdefault(cls[4:], []).append(o.name)
    return {k for k, names in members.items() if names and all(n in skin_names for n in names)}


def _faded_labels(scene, path, skins, classes, colour):
    """{see-through key part (faded_keys): labels} - each from a render with only that part's skins drawn, every other
    skin left out (one in front of it, another faded part round it, would hide it); {} when none."""
    out = {}
    for key in sorted(faded_keys(skins, classes)):
        others = [o for o in skins if classes.get(o.name, '') != f'key:{key}']
        for o in others:
            o.hide_render = True
        try:
            out[key] = _decode(_render(scene, Path(path).with_name(f"{Path(path).stem}_{key.replace('/', '_')}.png")), colour)[0]
        finally:
            for o in others:
                o.hide_render = False
    return out


def _mute_visibility(obj):
    """Mute the keyed render visibility of an object (its drivers and action curves, NLA strips included) so a value set
    now holds on frame changes; the curves muted, to unmute."""
    out = []
    anim = obj.animation_data
    if not anim:
        return out
    actions = [anim.action] + [strip.action for track in anim.nla_tracks for strip in track.strips]
    curves = list(anim.drivers) + [c for action in actions if action is not None for c in id_view._curves(action)[0]]
    for curve in curves:
        if curve.data_path in id_view.VISIBILITY_PATHS and not curve.mute:
            curve.mute = True
            out.append(curve)
    return out


def _render(scene, path):
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = image.size
        pixels = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    return pixels.reshape(h, w, 4)[::-1]   # top row first


def _decode(pixels, colour):
    """Class label per pixel: -1 background, else an index into the class list."""
    names = list(colour)
    table = np.array([colour[n][1] for n in names], dtype=np.float32)
    rgb = pixels[..., :3].reshape(-1, 3)
    labels = np.argmin(((rgb[:, None, :] - table[None, :, :]) ** 2).sum(-1), axis=1)
    labels[pixels[..., 3].reshape(-1) < 0.5] = -1
    return labels.reshape(pixels.shape[:2]), names


def _ground_rays(scene, camera, h, w):
    """(h, w) bool: where the camera ray points below the horizon - where ground or structure belongs. Background there
    is a void; background above it is sky, the world (archcut3 s01: sky above a street is not a doll's house)."""
    m = camera.matrix_world
    origin = m.translation
    if camera.data.type == 'ORTHO':
        forward = (m.to_3x3() @ Vector((0.0, 0.0, -1.0)))
        return np.full((h, w), forward.z < 0)
    tr, br, bl, tl = [(m @ c) - origin for c in camera.data.view_frame(scene=scene)]
    u = np.linspace(0.0, 1.0, w)[None, :]
    v = np.linspace(0.0, 1.0, h)[:, None]   # top row first, like the label image
    z = (1 - v) * ((1 - u) * tl.z + u * tr.z) + v * ((1 - u) * bl.z + u * br.z)
    return z < 0


def _row(labels, names, ground=None):
    counts = {'subject': 0, 'support': 0, 'background': int((labels == -1).sum()), 'key': {}, 'concealed': {}}
    for i, name in enumerate(names):
        n = int((labels == i).sum())
        if name.startswith('key:'):
            counts['key'][name[4:]] = n
        elif name.startswith('hide:'):
            counts['concealed'][name[5:]] = n
        else:
            counts[name] += n
    ground = np.ones(labels.shape, dtype=bool) if ground is None else ground
    edges = {'left': (labels[:, 0], ground[:, 0]), 'right': (labels[:, -1], ground[:, -1]), 'top': (labels[0, :], ground[0, :]),
             'bottom': (labels[-1, :], ground[-1, :])}
    # void: background where the ray points below the horizon, over a fifth of the border (not a stray pixel, not sky)
    borders = {side: {'subject': bool(np.isin(line, [names.index('subject')]).any()),
                      'key': [names[i][4:] for i in set(line.tolist()) if i >= 0 and names[i].startswith('key:')],
                      'background': float(((line == -1) & below).mean()) > 0.2}
               for side, (line, below) in edges.items()}
    return core.metrics(counts, borders, labels.size)


def _shapes(labels, names, ui_rect):
    """Per class: pixel count, share, bbox, centroid (normalized, 0,0 top-left) and pixels outside the UI rect - from the
    label image already decoded, so the screen targets cost no render. 'subject' is the whole subject (its key and
    concealed parts too, as in the shares), 'all' everything that renders."""
    h, w = labels.shape
    groups = {'all': labels >= 0, 'subject': np.isin(labels, [i for i, n in enumerate(names) if n == 'subject' or n.startswith(('key:', 'hide:'))])}
    groups.update({n: labels == i for i, n in enumerate(names) if n.startswith('key:')})
    x0, y0, x1, y1 = ui_rect
    inside = np.zeros_like(labels, dtype=bool)
    inside[int(round(y0 * h)):int(round(y1 * h)), int(round(x0 * w)):int(round(x1 * w))] = True
    out = {}
    for name, mask in groups.items():
        n = int(mask.sum())
        if not n:
            out[name] = {'px': 0}
            continue
        ys, xs = np.nonzero(mask)
        out[name] = {'px': n, 'share': round(n / labels.size, 6),
                     **({'col_v': round(core.column_thickness(mask), 5)} if name.startswith('key:') else {}),
                     'bbox': [round(xs.min() / w, 4), round(ys.min() / h, 4), round((xs.max() + 1) / w, 4), round((ys.max() + 1) / h, 4)],
                     'centroid': [round((xs.mean() + 0.5) / w, 4), round((ys.mean() + 0.5) / h, 4)],
                     'outside_ui_px': int((mask & ~inside).sum())}
    return out


MOTION_STEPS = 60   # projected frames for screen speed: every frame of a 2 s shot, every 5th of a 10 s one


def _motion(scene, camera, classes, count, extra_classes):
    """{'stride', 'frames', 'centers': {class: [[x, y] | None]}}: each class's projected bounding-box centre (clipped to
    the frame) on every stride-th frame, from the scene alone (no render)."""
    groups = {'subject': lambda c: c == 'subject' or c.startswith(('key:', 'hide:'))}
    groups.update({cls: (lambda target: lambda c: c == target)(cls) for cls in extra_classes})
    members = {g: [o for o in scene.objects if o.type == 'MESH' and test(classes.get(o.name, 'support'))] for g, test in groups.items()}
    corners = {o.name: np.c_[np.array([tuple(v) for v in o.bound_box]), np.ones(8)] for objs in members.values() for o in objs}
    stride = max(1, -(-count // MOTION_STEPS))
    frames = list(range(0, count, stride))
    if frames[-1] != count - 1:
        frames.append(count - 1)
    render = scene.render
    centers = {g: [] for g in members}
    for frame in frames:
        scene.frame_set(frame + 1)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        project = np.array(camera.calc_matrix_camera(depsgraph, x=render.resolution_x, y=render.resolution_y,
                                                     scale_x=render.pixel_aspect_x, scale_y=render.pixel_aspect_y)) @ np.array(camera.matrix_world.inverted())
        for g, objs in members.items():
            if not objs:
                centers[g].append(None)
                continue
            world = np.concatenate([corners[o.name] @ np.array(o.matrix_world).T for o in objs])
            clip = world @ project.T
            front = clip[:, 3] > 1e-6
            if not front.any():
                centers[g].append(None)
                continue
            ndc = clip[front, :2] / clip[front, 3:4]
            x, y = np.clip((ndc[:, 0] + 1) / 2, 0, 1), np.clip(1 - (ndc[:, 1] + 1) / 2, 0, 1)
            if x.max() - x.min() <= 0 and y.max() - y.min() <= 0:   # entirely off one side of the frame
                centers[g].append(None)
                continue
            centers[g].append([round(float(x.min() + x.max()) / 2, 4), round(float(y.min() + y.max()) / 2, 4)])
    return {'stride': stride, 'frames': frames, 'centers': centers}


def _scale_refs(scene, camera, frames, out_h):
    """{id: px} how tall each thing of known size (obj['studio_scale_ref']: a library person, a stand-in marked
    scale_ref) shows at most over the probed frames, in output pixels - its projected box, the part inside the frame,
    nothing in front of it subtracted (a reference behind the subject still says how big things are)."""
    refs = [o for o in scene.objects if 'studio_scale_ref' in o.keys()]
    out = {}
    for ref in refs:
        meshes = [m for m in (ref, *ref.children_recursive) if m.type == 'MESH' and not m.hide_render]
        best = 0.0
        for frame in frames:
            scene.frame_set(frame + 1)
            pts = [world_to_camera_view(scene, camera, m.matrix_world @ Vector(c)) for m in meshes for c in m.bound_box]
            pts = [q for q in pts if q.z > 0]
            if not pts:
                continue
            xs = [min(1.0, max(0.0, q.x)) for q in pts]
            ys = [min(1.0, max(0.0, q.y)) for q in pts]
            if max(xs) - min(xs) <= 0 or max(ys) - min(ys) <= 0:
                continue
            best = max(best, (max(ys) - min(ys)) * out_h)
        out[str(ref.get('studio_id', ref.name))] = round(best, 1)
    scene.frame_set(1)
    return out


def _straddles_near(scene, camera, objects):
    """True when a subject / key bounding box reaches in front of the near plane while part of it is behind it."""
    near = camera.data.clip_start
    for obj in objects:
        depths = [world_to_camera_view(scene, camera, obj.matrix_world @ Vector(c)).z for c in obj.bound_box]
        if min(depths) < near < max(depths):
            return True
    return False


def _why_hidden(scene, camera, part, window, rows):
    """Why a part does not show, at the probed frame of its window where it shows most: {frame, on_screen (share of its
    points inside the frame), occluders {object: share}} - so the fix is named: open what is in front, or frame it."""
    import camera_frame
    lo, hi = window
    inside = [r for r in rows if lo <= r['frame'] <= hi] or rows
    best = max(inside, key=lambda r: r.get('key_px', {}).get(part, 0))['frame'] if inside else lo
    scene.frame_set(best + 1)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    try:
        points = camera_frame.sample([part], depsgraph, count=300)
    except ValueError:
        return {'frame': best, 'on_screen': 0.0, 'occluders': {}}
    origin = camera.matrix_world.translation
    mine = {o.name for o in camera_frame.own_objects(part)}
    on, hidden = 0, {}
    for p in points:
        v = world_to_camera_view(scene, camera, Vector(p))
        if not (0.0 <= v.x <= 1.0 and 0.0 <= v.y <= 1.0 and v.z > 0):
            continue
        on += 1
        reached, obj = camera_frame.walk(scene, depsgraph, origin, Vector(p), mine)
        if not reached and obj is not None:
            hidden[obj.name] = hidden.get(obj.name, 0) + 1
    scene.frame_set(1)
    return {'frame': best, 'on_screen': round(on / max(1, len(points)), 3),
            'occluders': {k: round(v / max(1, on), 3) for k, v in sorted(hidden.items(), key=lambda kv: -kv[1])[:5]}}


def probe(job, output):
    started = time.perf_counter()
    settings = job.get('probe') or {}
    scene = bpy.context.scene
    camera = scene.camera
    count = job['shot']['duration_frames']
    classes, keys, concealed = _classes(settings)
    key_parts = [k for k in settings.get('key_parts', []) if k['id'] in keys]
    concealed_parts = [c for c in settings.get('concealed_parts', []) if c['id'] in concealed]
    frames = core.pick_frames(count, core.required_frames(key_parts + concealed_parts, count, settings.get('frames', [])))
    colour = _setup(scene, classes, keys, concealed)
    screen = settings.get('screen') or {}
    ui_rect = settings.get('ui_rect') or [0.0, 0.0, 1.0, 1.0]
    watched = [o for o in scene.objects if o.type == 'MESH' and classes.get(o.name, 'support') not in ('support', 'hidden')]
    images = Path(output) / 'frame_probe'
    images.mkdir(exist_ok=True)
    rows = []
    for frame in frames:
        scene.frame_set(frame + 1)
        labels, names = _decode(_render(scene, images / f'frame_{frame:04d}_id.png'), colour)
        ground = _ground_rays(scene, camera, *labels.shape)
        row = {'frame': frame, **_row(labels, names, ground), 'near_cut_share': None, 'shapes': _shapes(labels, names, ui_rect)}
        skins = [o for o in scene.objects if not o.hide_render and any(a <= frame + 1 < b for a, b in see_windows(o))]
        if skins:   # see-through skins at this frame (show_scene): the key parts are judged as seen through them
            for o in skins:
                o.hide_render = True
            try:
                through, _ = _decode(_render(scene, images / f'frame_{frame:04d}_through.png'), colour)
            finally:
                for o in skins:
                    o.hide_render = False
            seen = _row(through, names, ground)
            row.update({k: seen[k] for k in ('key_px', 'key_share', 'key_edges')})
            row['shapes'].update({n: v for n, v in _shapes(through, names, ui_rect).items() if n.startswith('key:')})
            # a key part that is itself see-through (show_core.SHOWN_THROUGH: a sleeve shown round its pipe) is read where
            # it is drawn, every other skin left out (one in front of it would hide it)
            drawn = _faded_labels(scene, images / f'frame_{frame:04d}_faded.png', skins, classes, colour)
            for key, labels_f in drawn.items():
                kept = _row(labels_f, names, ground)
                row.update({k: {**row[k], **({key: kept[k][key]} if key in kept[k] else {})} for k in ('key_px', 'key_share', 'key_edges')})
                row['shapes'].update({n: v for n, v in _shapes(labels_f, names, ui_rect).items() if n == f'key:{key}'})
            if drawn:
                row['faded_keys'] = sorted(drawn)
            row['see_through'] = sorted(o.name for o in skins)
        if watched and _straddles_near(scene, camera, watched):
            # A clipped closed mesh still fills its outline with its own inside faces; with backface culling the cut
            # shows as a hole. Compare the culled render with and without the near plane.
            near, shading = camera.data.clip_start, scene.display.shading
            shading.show_backface_culling = True
            # the engine's environment shell (env_shell.py) is far context: out of this comparison, where the 1e-4 near
            # plane leaves too little depth precision to keep a deck and the sea apart (2026-10-18, a ship at sea level)
            shell = [o for o in scene.objects if o.get('studio_env_shell') and not o.hide_render]
            for o in shell:
                o.hide_render = True
            try:
                cut, _ = _decode(_render(scene, images / f'frame_{frame:04d}_clip.png'), colour)
                camera.data.clip_start = 1e-4
                whole, _ = _decode(_render(scene, images / f'frame_{frame:04d}_noclip.png'), colour)
            finally:
                camera.data.clip_start, shading.show_backface_culling = near, False
                for o in shell:
                    o.hide_render = False
            seen = lambda lab: int(sum((lab == names.index(n)).sum() for n in names if n != 'support'))  # noqa: E731
            row['near_cut_share'] = round(max(0, seen(whole) - seen(cut)) / labels.size, 6)
        rows.append(row)
    scale_refs = _scale_refs(scene, camera, frames, (settings.get('output_size') or (1080, 1920))[1])
    targeted = sorted({screen_core.class_of(t['of']) for t in screen.get('targets', []) if t['of'] not in ('subject', 'all')})
    motion = _motion(scene, camera, classes, count, targeted)
    scene.frame_set(1)
    # key parts are judged by their own rules; the shot's declared focus (screen.subject, measured in its own class for
    # the patterns) is still its subject
    # a subject a picture measures as its own thing (frame_targets.layout_elements) is still the subject (2026-10-13:
    # every r10 booster shot read 'no subject' - the booster had its own class as a drawn element)
    named = set(settings.get('subjects', []))
    focus = {f"key:{k['id']}" for k in key_parts if k.get('source') == 'focus' or k['id'] in named}
    has_subject = any(c == 'subject' or c in focus for c in classes.values())
    failures, notes = core.judge(rows, key_parts, settings.get('role'), has_subject, count, settings.get('exempt_frames', []), concealed_parts,
                                 tuple(settings.get('output_size') or (1080, 1920)), bool(screen.get('diorama')),
                                 [d for d in settings.get('details', []) if d['id'] in keys])
    for f in failures:
        if f['code'] in ('KEY_PART_INVISIBLE', 'KEY_PART_INVISIBLE_MOOD', 'DETAIL_NOT_SHOWN') and f.get('part'):
            f['why_hidden'] = _why_hidden(scene, camera, f['part'], f.get('frames') or [0, count - 1], rows)
    aspect = scene.render.resolution_x / max(1, scene.render.resolution_y)
    screen_failures, screen_summary = screen_core.judge(rows, motion, screen, key_parts, count, aspect, core.THRESHOLDS['key_min_px'])
    for f in screen_failures:   # a mood shot explains nothing exact: a covered key part is said, not refused (as KEY_PART_INVISIBLE)
        if f['code'] == 'KEY_PART_UNDER_UI' and settings.get('role') != 'explain':
            f['by_role'] = True
    failures += screen_failures
    if not has_subject:   # nothing is the subject: subject share, fidelity and subject targets measure nothing
        failures.append({'code': 'SUBJECT_UNDECLARED', 'hint': 'no object is the shot\'s subject: name it in shot.screen.subject '
                         '(ids, group ids or fill subjects), shot.subjects or the camera target'})
    import gate_policy
    by_role = [f for f in failures if core.is_warning_by_role(f)]
    errors, warnings = gate_policy.split([f for f in failures if f not in by_role], 'code')
    report = {'schema_version': 1, 'frames': rows, 'summary': {**notes, 'key_parts': keys, 'concealed_parts': concealed, 'role': settings.get('role'),
                                                               'scale_refs': scale_refs,
                                                               'size_px': [scene.render.resolution_x, scene.render.resolution_y]},
              'thresholds': core.THRESHOLDS, 'gate_failures': errors,
              'warnings': [f"{f['code']}: {json.dumps({k: v for k, v in f.items() if k != 'code'})[:240]}" for f in warnings + by_role],
              'screen': {**screen_summary, 'ui_rect': ui_rect, 'motion': motion},
              'images': sorted(str(p) for p in images.glob('*_id.png')), 'seconds': round(time.perf_counter() - started, 3)}
    (Path(output) / 'frame_report.json').write_text(json.dumps(report, indent=1))
    if errors:
        raise ValueError('FRAME_PROBE_FAILED: ' + '; '.join(f"{f['code']} {f.get('part', '')} frames {f.get('frames', '')}" for f in errors)[:800])
    return report
