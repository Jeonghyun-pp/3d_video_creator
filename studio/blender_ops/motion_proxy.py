"""Screen-motion estimate without rendering: how far scene points move on screen per frame.

Every render-visible mesh contributes its bounding-box corners and centre, moved by the object's world
matrix each frame (so moving cars and trains count). Points are projected through the real camera
(lens, sensor fit, animated keys) at the measuring width (448 px, the qa_motion width); the per-frame value
is the mean displacement of points visible in both frames. A calibration (motion_styles/_calibration.json)
maps it to rendered frame-difference energy, so camera fitting can aim at a style in seconds instead of
rendering. Also runnable as a script: blender -b scene.blend --python motion_proxy.py -- out.json
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from scene_roles import first_blocking_hit  # noqa: E402

WIDTH = 448
MAX_POINTS = 12000


def _point_sets(scene):
    objs = [o for o in scene.objects if o.type == 'MESH' and not o.hide_render and o.visible_get()]
    sets = []
    for o in objs:
        corners = np.array([list(c) for c in o.bound_box], dtype=float)
        local = np.vstack([corners, corners.mean(axis=0, keepdims=True)])
        sets.append((o, np.hstack([local, np.ones((len(local), 1))])))
    total = sum(len(p) for _, p in sets)
    if total > MAX_POINTS:  # deterministic thinning by object order
        stride = int(np.ceil(total / MAX_POINTS))
        sets = sets[::stride]
    return sets


def _project(scene, camera, world, width, height):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    proj = np.array(camera.calc_matrix_camera(depsgraph, x=width, y=height, scale_x=1, scale_y=1))
    view = np.array(camera.matrix_world.inverted())
    clip = (proj @ view @ world.T).T
    w = clip[:, 3]
    ndc = clip[:, :3] / np.where(np.abs(w) < 1e-9, 1e-9, w)[:, None]
    visible = (w > 0) & (np.abs(ndc[:, 0]) <= 1) & (np.abs(ndc[:, 1]) <= 1)
    px = np.stack([(ndc[:, 0] + 1) * width / 2, (1 - ndc[:, 1]) * height / 2], axis=1)
    return px, visible


GRID = (24, 42)        # screen cells (x, y) for a 9:16 frame; scaled by aspect
FLOW_CAP_PX = 24.0     # frame difference saturates once an edge moves past its own width
NORMAL_EDGE = 0.9      # cos(~25 deg): a crease reads as an edge


def _cast_grid(scene, camera, cols, rows):
    """Ray-cast a screen grid: per cell (object, local hit point, normal, depth) or None."""
    from mathutils import Vector
    depsgraph = bpy.context.evaluated_depsgraph_get()
    frame = camera.data.view_frame(scene=scene)  # camera-space corners of the image plane
    tr, br, bl, tl = [camera.matrix_world @ v for v in frame]
    origin = camera.matrix_world.translation
    cells = []
    for j in range(rows):
        for i in range(cols):
            u, v = (i + 0.5) / cols, (j + 0.5) / rows
            point = tl.lerp(tr, u).lerp(bl.lerp(br, u), v)
            hit, location, normal, _index, original, _matrix = first_blocking_hit(scene, depsgraph, origin, (point - origin).normalized(), 1.0e6)
            if hit and original is not None:
                cells.append((original, original.matrix_world.inverted() @ location, normal.copy(), (location - origin).length))
            else:
                cells.append(None)
    return cells


def _edge_weights(cells, cols, rows):
    weights = []
    for j in range(rows):
        for i in range(cols):
            c = cells[j * cols + i]
            edge = 0.0
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ii, jj = i + di, j + dj
                if not (0 <= ii < cols and 0 <= jj < rows):
                    continue
                n = cells[jj * cols + ii]
                if (c is None) != (n is None):
                    edge = 1.0
                elif c is not None and (c[0] != n[0] or c[2].dot(n[2]) < NORMAL_EDGE or abs(c[3] - n[3]) > 0.1 * max(c[3], n[3])):
                    edge = 1.0
            weights.append(edge)
    return weights


def flow_proxy(scene=None, camera=None, start=None, count=None, width=WIDTH):
    """Per-frame screen flow at visible edges (px at `width`): ray-cast surfaces, carry each hit with its
    object to the next frame, re-project, weight by edge cells (flat areas do not change pixels)."""
    from bpy_extras.object_utils import world_to_camera_view
    scene = scene or bpy.context.scene
    camera = camera or scene.camera
    start = start or scene.frame_start
    count = count or (scene.frame_end - start + 1)
    height = max(2, round(width * scene.render.resolution_y / scene.render.resolution_x))
    cols = GRID[0]
    rows = max(2, round(cols * height / width))
    series = []
    scene.frame_set(start)
    cells = _cast_grid(scene, camera, cols, rows)
    for f in range(start + 1, start + count):
        weights = _edge_weights(cells, cols, rows)
        before = []
        for c in cells:
            if c is None:
                before.append(None)
                continue
            p = world_to_camera_view(scene, camera, c[0].matrix_world @ c[1])
            before.append((p.x * width, (1 - p.y) * height))
        scene.frame_set(f)
        total, mass = 0.0, 0.0
        for c, w, b in zip(cells, weights, before):
            if c is None or not w:
                continue
            p = world_to_camera_view(scene, camera, c[0].matrix_world @ c[1])
            if p.z <= 0:
                continue
            d = ((p.x * width - b[0]) ** 2 + ((1 - p.y) * height - b[1]) ** 2) ** 0.5
            total += min(d, FLOW_CAP_PX); mass += 1
        series.append(round(total / (cols * rows), 4))  # edge flow per cell: denser edges = more changed pixels
        cells = _cast_grid(scene, camera, cols, rows)
    return {'width': width, 'grid': [cols, rows], 'per_frame': series}


def motion_proxy(scene=None, camera=None, start=None, count=None, width=WIDTH):
    """Per-frame mean on-screen displacement (px at `width`) of scene points, frames start..start+count-1 (1-based)."""
    scene = scene or bpy.context.scene
    camera = camera or scene.camera
    start = start or scene.frame_start
    count = count or (scene.frame_end - start + 1)
    height = max(2, round(width * scene.render.resolution_y / scene.render.resolution_x))
    sets = _point_sets(scene)
    previous = None
    series = []
    for f in range(start, start + count):
        scene.frame_set(f)
        world = np.vstack([(np.array(o.matrix_world) @ p.T).T for o, p in sets]) if sets else np.zeros((0, 4))
        px, visible = _project(scene, camera, world, width, height)
        if previous is not None:
            both = visible & previous[1]
            series.append(float(np.linalg.norm(px[both] - previous[0][both], axis=1).mean()) if both.any() else 0.0)
        previous = (px, visible)
    return {'width': width, 'points': int(sum(len(p) for _, p in sets)), 'per_frame': [round(v, 4) for v in series]}


if __name__ == '__main__' and '--' in sys.argv:
    out = sys.argv[sys.argv.index('--') + 1]
    result = flow_proxy() if '--flow' in sys.argv else motion_proxy()
    with open(out, 'w') as stream:
        json.dump(result, stream)
