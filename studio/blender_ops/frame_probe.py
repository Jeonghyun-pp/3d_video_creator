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
import id_view
import screen_core
from ids_core import suggest
from scene_index import AnchorIndex, known_ids, resolve_group
from scene_roles import role

HIDDEN_ROLES = {'helper', 'atmosphere', 'graphic'}   # scatter sources stay: instances render with their source's colour
BACKGROUND_IDS = {'StudioBackdrop'}   # the generated backdrop card is the background the subject is set against
HEIGHT_PX = 256
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
    keys, resolved = [], []
    for part in probe.get('key_parts', []):
        found = resolve_group(part['id'], index)
        if not found:
            unknown.append(part['id'])
            continue
        keys.append(part['id'])
        resolved += [(part, obj) for obj in found]
    depth = lambda o: 0 if o.parent is None else 1 + depth(o.parent)  # noqa: E731
    for part, obj in sorted(resolved, key=lambda pair: depth(pair[1])):   # parents first: a key part inside another keeps its own class
        for o in _tree(obj):
            if out.get(o.name) != 'hidden':
                out[o.name] = f"key:{part['id']}"
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
            obj.hide_render = True
        else:
            obj.color = (*colour[cls][0], 1.0)
    render = scene.render
    aspect = render.resolution_x * render.pixel_aspect_x / max(1, render.resolution_y * render.pixel_aspect_y)
    render.resolution_y, render.resolution_x, render.resolution_percentage = HEIGHT_PX, max(16, round(HEIGHT_PX * aspect)), 100
    id_view.apply(scene)   # the probe runs last in the build, on a copy that is never saved: nothing to restore
    return colour


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


def _row(labels, names):
    counts = {'subject': 0, 'support': 0, 'background': int((labels == -1).sum()), 'key': {}, 'concealed': {}}
    for i, name in enumerate(names):
        n = int((labels == i).sum())
        if name.startswith('key:'):
            counts['key'][name[4:]] = n
        elif name.startswith('hide:'):
            counts['concealed'][name[5:]] = n
        else:
            counts[name] += n
    edges = {'left': labels[:, 0], 'right': labels[:, -1], 'top': labels[0, :], 'bottom': labels[-1, :]}
    borders = {side: {'subject': bool(np.isin(line, [names.index('subject')]).any()),
                      'key': [names[i][4:] for i in set(line.tolist()) if i >= 0 and names[i].startswith('key:')]}
               for side, line in edges.items()}
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


def _straddles_near(scene, camera, objects):
    """True when a subject / key bounding box reaches in front of the near plane while part of it is behind it."""
    near = camera.data.clip_start
    for obj in objects:
        depths = [world_to_camera_view(scene, camera, obj.matrix_world @ Vector(c)).z for c in obj.bound_box]
        if min(depths) < near < max(depths):
            return True
    return False


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
        row = {'frame': frame, **_row(labels, names), 'near_cut_share': None, 'shapes': _shapes(labels, names, ui_rect)}
        if watched and _straddles_near(scene, camera, watched):
            # A clipped closed mesh still fills its outline with its own inside faces; with backface culling the cut
            # shows as a hole. Compare the culled render with and without the near plane.
            near, shading = camera.data.clip_start, scene.display.shading
            shading.show_backface_culling = True
            try:
                cut, _ = _decode(_render(scene, images / f'frame_{frame:04d}_clip.png'), colour)
                camera.data.clip_start = 1e-4
                whole, _ = _decode(_render(scene, images / f'frame_{frame:04d}_noclip.png'), colour)
            finally:
                camera.data.clip_start, shading.show_backface_culling = near, False
            seen = lambda lab: int(sum((lab == names.index(n)).sum() for n in names if n != 'support'))  # noqa: E731
            row['near_cut_share'] = round(max(0, seen(whole) - seen(cut)) / labels.size, 6)
        rows.append(row)
    targeted = sorted({screen_core.class_of(t['of']) for t in screen.get('targets', []) if t['of'] not in ('subject', 'all')})
    motion = _motion(scene, camera, classes, count, targeted)
    scene.frame_set(1)
    has_subject = any(c == 'subject' for c in classes.values())   # key parts are judged by their own rules
    failures, notes = core.judge(rows, key_parts, settings.get('role'), has_subject, count, settings.get('exempt_frames', []), concealed_parts)
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
                                                               'size_px': [scene.render.resolution_x, scene.render.resolution_y]},
              'thresholds': core.THRESHOLDS, 'gate_failures': errors,
              'warnings': [f"{f['code']}: {json.dumps({k: v for k, v in f.items() if k != 'code'})[:240]}" for f in warnings + by_role],
              'screen': {**screen_summary, 'ui_rect': ui_rect, 'motion': motion},
              'images': sorted(str(p) for p in images.glob('*_id.png')), 'seconds': round(time.perf_counter() - started, 3)}
    (Path(output) / 'frame_report.json').write_text(json.dumps(report, indent=1))
    if errors:
        raise ValueError('FRAME_PROBE_FAILED: ' + '; '.join(f"{f['code']} {f.get('part', '')} frames {f.get('frames', '')}" for f in errors)[:800])
    return report
