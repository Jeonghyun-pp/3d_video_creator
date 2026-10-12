"""Screen grammar T2, measured on a built scene instead of its pixels: the same per-frame-pair similarity fit as
grammar_probe.py, fed with where the scene's points actually go on screen.

Why (2026-10-12, rocket_landing): the user wanted the camera to move like a reference account they named, and asked
how that could be checked. The reference reels were measured by grammar_probe (phase correlation of 32 px patches,
flat patches dropped). A workbench clay frame has texture in only 13-34 % of its patches, nearly all on the rocket: a
camera following the rocket over a flat sky would read "still" while the world streams past. Here every patch centre
is a ray into the scene; its hit (or, past everything, a direction at infinity - the sky's clouds in a real reel) is
carried to the next frame with the object it lies on and projected by the next frame's camera. The shifts go through
grammar_probe.fit, so the numbers are in the reference's units: tx, ty, ds (log scale step), rot, residual (parallax).

Each pair also carries 'streak' (as grammar_probe) and 'feel', which only a scene can give (grammar_core.feel):
edges (share of patches whose object changes under them), near (foreground share), subject and world flow on screen,
the camera's and the subject's velocity in the world, the
camera's height over what is below it, its speed, lens and pitch (+ looks up).

Run: blender --background SCENE.blend --python grammar_synth.py -- OUT.json [WIDTH] [FIRST LAST] [sky|nosky] [SUBJECT] [PARTS]
(SUBJECT: object name prefix of what the shot is about, for near and subject flow; PARTS: comma-separated prefixes of
what it explains, for where the camera heads)
"""
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grammar_probe import MIN_PATCHES, PATCH, STREAK, fit, focus, grid, misfit   # noqa: E402  the reference's own patch layout and fit

FAR = 1.0e6   # a ray that hits nothing: a point this far along it (it moves only with the camera's turn)
NEAR_SHARE = 0.5   # a patch closer than this share of the subject's distance is in front of it (foreground)


def _image_size(scene, width):
    r = scene.render
    return width, max(2, int(round(width * r.resolution_y / r.resolution_x)) // 2 * 2)


def _rays(scene, cam, centres, w, h):
    """World-space ray (origin, direction) through each patch centre of the camera's frame."""
    frame = [cam.matrix_world @ v for v in cam.data.view_frame(scene=scene)]   # top-right, bottom-right, bottom-left, top-left
    tr, br, bl, tl = frame
    origin = cam.matrix_world.translation
    out = []
    for y, x in centres:
        u, v = x / w, y / h
        top = tl.lerp(tr, u)
        bottom = bl.lerp(br, u)
        point = top.lerp(bottom, v)
        out.append((origin.copy(), (point - origin).normalized()))
    return out


def _hit(scene, depsgraph, origin, direction):
    hit, location, _normal, _index, obj, _matrix = scene.ray_cast(depsgraph, origin, direction)
    if not hit or obj is None:
        return None, origin + direction * FAR
    original = obj.original if hasattr(obj, 'original') else obj
    return original.name, original.matrix_world.inverted() @ location


def _pixel(scene, cam, world, w, h):
    ndc = world_to_camera_view(scene, cam, world)
    return ndc.x * w, (1.0 - ndc.y) * h, ndc.z


def _height(scene, depsgraph, cam):
    """Distance from the camera down to whatever is below it (a deck, the sea), or its z over 0 when nothing is."""
    origin = cam.matrix_world.translation
    hit, location, *_ = scene.ray_cast(depsgraph, origin, Vector((0.0, 0.0, -1.0)))
    return float(origin.z - location.z) if hit else float(origin.z)


def _cast(scene, cam, centres, w, h):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return [_hit(scene, depsgraph, origin, direction) for origin, direction in _rays(scene, cam, centres, w, h)], depsgraph


def resolve_root(ident):
    from scene_index import resolve_group
    return resolve_group(ident)


def _members(idents):
    """Object names under declared ids (scene_index.resolve_group: the one rule the frame probe uses - a part class such
    as booster/landing_assembly names a group, not an object), with every child."""
    from scene_index import AnchorIndex, resolve_group
    index = AnchorIndex()
    return {o.name for ident in idents for root in resolve_group(ident, index) for o in (root, *root.children_recursive)}


def measure(width=192, first=None, last=None, sky=True, subject=None, parts=()):
    """sky: rays that hit nothing count as points at infinity (a real sky's clouds); False drops them, as grammar_probe drops a
    flat sky - the setting to compare with the pixel measurement of an untextured render. subject: name prefix of the
    shot's subject (near / subject flow need it); parts: name prefixes of what the shot explains (part_box: where they are
    on the picture, for grammar_core.feel's heading_on)."""
    scene = bpy.context.scene
    cam = scene.camera
    if cam is None:
        raise ValueError('GRAMMAR_SYNTH: the scene has no camera')
    w, h = _image_size(scene, width)
    centres = grid(h, w)
    points = np.array([(x - w / 2, y - h / 2) for y, x in centres], np.float64)
    half = PATCH * 3 / 8   # half of grid()'s step
    first = scene.frame_start if first is None else first
    last = scene.frame_end if last is None else last
    rows = []
    fps = scene.render.fps / scene.render.fps_base
    scene.frame_set(first)
    subject_names, part_names = _members([subject] if subject else []), _members(parts)
    anchors, depsgraph = _cast(scene, cam, centres, w, h)
    for f in range(first, last):
        origin = cam.matrix_world.translation.copy()
        body = next(iter(resolve_root(subject)), None) if subject else None
        body_at = body.matrix_world.translation.copy() if body else None
        height = _height(scene, depsgraph, cam)
        dist = [None if name is None else (bpy.data.objects[name].matrix_world @ where - origin).length for name, where in anchors]
        on = [name in subject_names for name, _ in anchors]
        at_part = [(x / w, y / h) for (y, x), (name, _) in zip(centres, anchors) if name in part_names]
        anchors = [(name, where.copy()) for name, where in anchors]
        scene.frame_set(f + 1)
        dx, dy, keep = [], [], []
        for (y, x), (name, where) in zip(centres, anchors):
            world = bpy.data.objects[name].matrix_world @ where if name else where
            px, py, depth = _pixel(scene, cam, world, w, h)
            ok = depth > 0 and math.isfinite(px) and math.isfinite(py) and (sky or name is not None)
            keep.append(ok)
            dx.append(px - x if ok else 0.0)
            dy.append(py - y if ok else 0.0)
        following, depsgraph = _cast(scene, cam, centres, w, h)   # the next pair's anchors, and what is under each patch now
        good = np.array(keep)
        dxa, dya = np.array(dx), np.array(dy)
        subject_d = [d for d, o in zip(dist, on) if o and d is not None]
        reach = float(np.median(subject_d)) if subject_d else None
        sub = good & np.array(on)
        rest = good & ~np.array(on) & np.array([n is not None for n, _ in anchors])
        feel = {'edges': round(sum(a[0] != b[0] for a, b in zip(anchors, following)) / len(centres), 4),
                'near': round(sum(1 for d, o in zip(dist, on) if d is not None and not o and d < NEAR_SHARE * reach) / len(centres), 4)
                if reach else None,
                'subject_flow': [round(float(dxa[sub].mean()) / w, 6), round(float(dya[sub].mean()) / w, 6)] if sub.any() else None,
                'world_flow': [round(float(dxa[rest].mean()) / w, 6), round(float(dya[rest].mean()) / w, 6)] if rest.any() else None,
                'height_m': round(height, 3), 'speed_mps': round((cam.matrix_world.translation - origin).length * fps, 4),
                'camera_vel': [round(v * fps, 4) for v in cam.matrix_world.translation - origin],
                'subject_vel': [round(v * fps, 4) for v in body.matrix_world.translation - body_at] if body else None,
                'lens_mm': round(cam.data.lens, 2),
                'pitch_deg': round(math.degrees(math.asin(max(-1.0, min(1.0, (cam.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))).z)))), 2),
                # patch centres on the parts, widened by half the grid step: a part one patch row high still has a height
                'part_box': [round(min(u for u, _ in at_part) - half / w, 3), round(min(v for _, v in at_part) - half / h, 3),
                             round(max(u for u, _ in at_part) + half / w, 3), round(max(v for _, v in at_part) + half / h, 3)] if at_part else None}
        row = {'ok': False, 'frame': f, 'feel': feel}
        if good.sum() >= MIN_PATCHES:
            sol, residual, inliers = fit(points[good], dxa[good], dya[good], np.ones(int(good.sum())))
            off = misfit(points[good], dxa[good], dya[good], sol)
            row = {'ok': True, 'frame': f, 'tx': round(float(sol[0]) / w, 6), 'ty': round(float(sol[1]) / w, 6),
                   'ds': round(float(np.log1p(sol[2])), 6), 'rot': round(float(sol[3]), 6),
                   'residual': round(residual / w, 6), 'inliers': round(inliers, 3), 'patches': int(good.sum()),
                   'streak': round(float((off / w > STREAK).sum()) / len(centres), 4), 'feel': feel}
            centre = focus(points[good], dxa[good], dya[good])
            if centre is not None:
                row['focus'] = [round(0.5 + centre[0] / w, 4), round(0.5 + centre[1] / h, 4)]
        rows.append(row)
        anchors = following
    return {'schema_version': 2, 'width': w, 'height': h, 'fps': scene.render.fps / scene.render.fps_base, 'first': first, 'last': last,
            'sky': sky, 'subject': subject, 'parts': list(parts), 'pairs': rows, 'method': 'synthetic: scene points carried frame to frame, grammar_probe.fit'}


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    target = args[0]
    width = int(args[1]) if len(args) > 1 else 192
    first, last = (int(args[2]), int(args[3])) if len(args) > 3 else (None, None)
    sky = (args[4] != 'nosky') if len(args) > 4 else True
    subject = args[5] if len(args) > 5 and args[5] != '-' else None
    parts = tuple(p for p in args[6].split(',') if p) if len(args) > 6 and args[6] != '-' else ()
    Path(target).write_text(json.dumps(measure(width, first, last, sky, subject, parts)))
