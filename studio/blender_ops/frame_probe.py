"""Frame probe (Blender side): look at the frames through the real scene camera and judge what shows.

Runs at the end of a build, after scene.blend is saved - it changes the open scene freely (object colours, render
settings, visibility) and nothing it does reaches the saved file. A Workbench id pass, FLAT light and OBJECT colour,
no anti-aliasing, 256 px tall at the output aspect, through the scene camera itself so clip planes, lens shift and
sensor fit are exactly what the render uses. Each pixel decodes to one class: a key part, the subject, support
(everything else that renders) or background. The verdicts are frame_probe_core.judge; this file only measures.

job['probe'] (written by the host, studio/blender.py): {role, subjects: [ids], key_parts: [{id, from_frame?, to_frame?,
min_px?}], frames: [extra frames], exempt_frames: [frames]}. Output: frame_report.json and frame_probe/*.png (the id
images, which an agent can open to see what the probe saw).
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
from scene_index import AnchorIndex
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
    """({object name: class} for every object, key ids, concealed ids) - the last two in palette order."""
    index = AnchorIndex()
    out = {}
    named = set(probe.get('subjects', []))   # the shot's subject: what its camera and its subjects list name - not every
    for obj in bpy.data.objects:             # spec-built object (fill copies are built from specs too)
        hidden = role(obj) in HIDDEN_ROLES or obj.type == 'VOLUME' or obj.type.startswith(('GREASEPENCIL', 'GPENCIL')) or obj.get('studio_id') in BACKGROUND_IDS \
            or getattr(obj, 'visible_camera', True) is False
        out[obj.name] = 'hidden' if hidden else ('subject' if obj.get('studio_subject_id') in named else 'support')
    roots = [index.resolve(ident)[0] for ident in named]
    roots += [o for o in bpy.data.objects if any(str(o.get('studio_layout_id', '')) == n or str(o.get('studio_layout_id', '')).startswith(n + '.')
                                                 for n in named)]   # a repeated instance's copies: rc -> rc.0, rc.1
    for obj in roots:
        for o in _tree(obj) if obj is not None else []:
            if out.get(o.name) == 'support':
                out[o.name] = 'subject'
    for obj in bpy.data.objects:   # a collection instance shows its collection's objects in the instancer's class
        if obj.instance_type == 'COLLECTION' and obj.instance_collection and out.get(obj.name) not in ('support', 'hidden', None):
            for source in obj.instance_collection.all_objects:
                if out.get(source.name) == 'support':
                    out[source.name] = out[obj.name]
    keys, unknown, resolved = [], [], []
    for part in probe.get('key_parts', []):
        obj = index.resolve(part['id'])[0]
        if obj is None:
            unknown.append(part['id'])
            continue
        keys.append(part['id'])
        resolved.append((part, obj))
    depth = lambda o: 0 if o.parent is None else 1 + depth(o.parent)  # noqa: E731
    for part, obj in sorted(resolved, key=lambda pair: depth(pair[1])):   # parents first: a key part inside another keeps its own class
        for o in _tree(obj):
            if out.get(o.name) != 'hidden':
                out[o.name] = f"key:{part['id']}"
    concealed, clash = [], []
    for part in probe.get('concealed_parts', []):   # parts an intact view must not show (an exterior hides its valve train)
        obj = index.resolve(part['id'])[0]
        if obj is None:
            unknown.append(part['id'])
            continue
        concealed.append(part['id'])
        for o in _tree(obj):
            if out.get(o.name, '').startswith('key:'):
                clash.append(f"{o.name} ({out[o.name][4:]} / {part['id']})")
            elif out.get(o.name) != 'hidden':
                out[o.name] = f"hide:{part['id']}"
    if unknown:
        raise ValueError(f'KEY_PART_UNKNOWN: no object for key or concealed part(s) {unknown}')
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
    watched = [o for o in scene.objects if o.type == 'MESH' and classes.get(o.name, 'support') not in ('support', 'hidden')]
    images = Path(output) / 'frame_probe'
    images.mkdir(exist_ok=True)
    rows = []
    for frame in frames:
        scene.frame_set(frame + 1)
        labels, names = _decode(_render(scene, images / f'frame_{frame:04d}_id.png'), colour)
        row = {'frame': frame, **_row(labels, names), 'near_cut_share': None}
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
    scene.frame_set(1)
    has_subject = any(c == 'subject' for c in classes.values())   # key parts are judged by their own rules
    failures, notes = core.judge(rows, key_parts, settings.get('role'), has_subject, count, settings.get('exempt_frames', []), concealed_parts)
    import gate_policy
    by_role = [f for f in failures if core.is_warning_by_role(f)]
    errors, warnings = gate_policy.split([f for f in failures if f not in by_role], 'code')
    report = {'schema_version': 1, 'frames': rows, 'summary': {**notes, 'key_parts': keys, 'concealed_parts': concealed, 'role': settings.get('role'),
                                                               'size_px': [scene.render.resolution_x, scene.render.resolution_y]},
              'thresholds': core.THRESHOLDS, 'gate_failures': errors,
              'warnings': [f"{f['code']}: {json.dumps({k: v for k, v in f.items() if k != 'code'})[:240]}" for f in warnings + by_role],
              'images': sorted(str(p) for p in images.glob('*_id.png')), 'seconds': round(time.perf_counter() - started, 3)}
    (Path(output) / 'frame_report.json').write_text(json.dumps(report, indent=1))
    if errors:
        raise ValueError('FRAME_PROBE_FAILED: ' + '; '.join(f"{f['code']} {f.get('part', '')} frames {f.get('frames', '')}" for f in errors)[:800])
    return report
