"""Screen grammar T2, measured on a built scene instead of its pixels: the same per-frame-pair similarity fit as
grammar_probe.py, fed with where the scene's points actually go on screen.

Why (2026-10-12, rocket_landing): the user wanted the camera to move like a reference account they named, and asked
how that could be checked. The reference reels were measured by grammar_probe (phase correlation of 32 px patches,
flat patches dropped). A workbench clay frame has texture in only 13-34 % of its patches, nearly all on the rocket: a
camera following the rocket over a flat sky would read "still" while the world streams past. Here every patch centre
is a ray into the scene; its hit (or, past everything, a direction at infinity - the sky's clouds in a real reel) is
carried to the next frame with the object it lies on and projected by the next frame's camera. The shifts go through
grammar_probe.fit, so the numbers are in the reference's units: tx, ty, ds (log scale step), rot, residual (parallax).

Run: blender --background SCENE.blend --python grammar_synth.py -- OUT.json [WIDTH] [FIRST LAST]
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
from grammar_probe import MIN_PATCHES, fit, grid   # noqa: E402  the reference's own patch layout and fit

FAR = 1.0e6   # a ray that hits nothing: a point this far along it (it moves only with the camera's turn)


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


def measure(width=192, first=None, last=None, sky=True):
    """sky: rays that hit nothing count as points at infinity (a real sky's clouds); False drops them, as grammar_probe drops a
    flat sky - the setting to compare with the pixel measurement of an untextured render."""
    scene = bpy.context.scene
    cam = scene.camera
    if cam is None:
        raise ValueError('GRAMMAR_SYNTH: the scene has no camera')
    w, h = _image_size(scene, width)
    centres = grid(h, w)
    points = np.array([(x - w / 2, y - h / 2) for y, x in centres], np.float64)
    first = scene.frame_start if first is None else first
    last = scene.frame_end if last is None else last
    rows = []
    for f in range(first, last):
        scene.frame_set(f)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        anchors = []
        for origin, direction in _rays(scene, cam, centres, w, h):
            name, where = _hit(scene, depsgraph, origin, direction)
            anchors.append((name, where.copy()))
        scene.frame_set(f + 1)
        dx, dy, keep = [], [], []
        for (y, x), (name, where) in zip(centres, anchors):
            world = bpy.data.objects[name].matrix_world @ where if name else where
            px, py, depth = _pixel(scene, cam, world, w, h)
            ok = depth > 0 and math.isfinite(px) and math.isfinite(py) and (sky or name is not None)
            keep.append(ok)
            dx.append(px - x if ok else 0.0)
            dy.append(py - y if ok else 0.0)
        good = np.array(keep)
        row = {'ok': False, 'frame': f}
        if good.sum() >= MIN_PATCHES:
            sol, residual, inliers = fit(points[good], np.array(dx)[good], np.array(dy)[good], np.ones(int(good.sum())))
            row = {'ok': True, 'frame': f, 'tx': round(float(sol[0]) / w, 6), 'ty': round(float(sol[1]) / w, 6),
                   'ds': round(float(np.log1p(sol[2])), 6), 'rot': round(float(sol[3]), 6),
                   'residual': round(residual / w, 6), 'inliers': round(inliers, 3), 'patches': int(good.sum())}
        rows.append(row)
    return {'schema_version': 1, 'width': w, 'height': h, 'fps': scene.render.fps / scene.render.fps_base, 'first': first, 'last': last,
            'sky': sky, 'pairs': rows, 'method': 'synthetic: scene points carried frame to frame, grammar_probe.fit'}


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    target = args[0]
    width = int(args[1]) if len(args) > 1 else 192
    first, last = (int(args[2]), int(args[3])) if len(args) > 3 else (None, None)
    sky = (args[4] != 'nosky') if len(args) > 4 else True
    Path(target).write_text(json.dumps(measure(width, first, last, sky)))
