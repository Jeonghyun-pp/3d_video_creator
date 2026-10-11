"""Blender layer of camera.move.focus: the focus's visible points from a camera position, and the solve.

The solver (camera_frame_core) needs what a camera standing somewhere actually sees of the focus. Points are sampled
on the focus parts' evaluated triangles (area-weighted, fixed seed) at the frame the move settles on, and a point
counts only when a ray from the camera reaches it before anything else - another object, or the part's own near side.
Nothing renders. The frame probe judges the built shot afterwards (FOCUS_FRAMING, DETAIL_NOT_SHOWN, KEY_PART_UNDER_UI).
"""
from __future__ import annotations

import random

import bpy
from mathutils import Vector

import camera_frame_core as core
from scene_tools import object_by_id

SAMPLES = 4000          # points over the whole focus: thin layers keep a few hundred each
SEED = 7
HIT_TOL = 1e-3          # a ray reaching within this share of the distance hits the point itself
SKIP_HITS = 4           # rays continue past objects that never render (cutters, helpers)


def _meshes(obj):
    return [o for o in [obj, *obj.children_recursive] if o.type == 'MESH' and not o.hide_render]


def sample(refs, depsgraph, count=SAMPLES, seed=SEED):
    """World points over the parts named by refs (and their mesh children): half spread over their surfaces by area,
    half along their edges by length - an outline lives on edges, and a long edge running off screen has its corners
    off screen too (a section's bottom edge seen up close), so area samples alone miss where the part ends."""
    tris, edges = [], []
    for ref in refs:
        obj = object_by_id(ref)
        if obj is None:
            raise ValueError(f'FRAME_UNSOLVED: focus part {ref!r} is not in the scene')
        for o in _meshes(obj):
            ev = o.evaluated_get(depsgraph)
            mesh = ev.to_mesh()
            try:
                mw = ev.matrix_world
                verts = [mw @ v.co for v in mesh.vertices]
                for e in mesh.edges:
                    a, b = verts[e.vertices[0]], verts[e.vertices[1]]
                    if (b - a).length > 0:
                        edges.append(((b - a).length, a, b))
                mesh.calc_loop_triangles()
                for t in mesh.loop_triangles:
                    a, b, c = (verts[i] for i in t.vertices)
                    area = (b - a).cross(c - a).length / 2
                    if area > 0:
                        tris.append((area, a, b, c))
            finally:
                ev.to_mesh_clear()
    if not tris:
        raise ValueError(f'FRAME_UNSOLVED: the focus {refs} has no surface to see')
    rng = random.Random(seed)
    out = []
    for _, a, b, c in rng.choices(tris, weights=[t[0] for t in tris], k=count // 2):
        u, v = rng.random(), rng.random()
        if u + v > 1:
            u, v = 1 - u, 1 - v
        out.append(tuple(a + (b - a) * u + (c - a) * v))
    if edges:
        for _, a, b in rng.choices(edges, weights=[e[0] for e in edges], k=count - count // 2):
            out.append(tuple(a + (b - a) * rng.random()))
    return out


def visible(points, depsgraph, scene=None):
    """visible(position) -> the points a camera at position reaches first."""
    scene = scene or bpy.context.scene

    def seen_from(position):
        origin = Vector(position)
        out = []
        for p in points:
            target = Vector(p)
            ray = target - origin
            distance = ray.length
            if distance <= 1e-6:
                continue
            direction = ray / distance
            start, left = origin, distance
            for _ in range(SKIP_HITS):
                hit, location, _n, _i, obj, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
                if not hit or (location - target).length <= HIT_TOL * distance:
                    out.append(p)
                    break
                if obj is not None and (obj.hide_render or obj.display_type in ('WIRE', 'BOUNDS')):
                    step = (location - start).length + 1e-4
                    start, left = start + direction * step, left - step
                    continue
                break
        return out
    return seen_from


def solve_focus(job, focus, refs, start, view, frame, lens_mm, details=()):
    """Solve camera.move.focus for one move: the scene at `frame` (1-based Blender frame), sampled once."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    width, height = job.get('output_size') or (1080, 1920)
    ui = job.get('ui_rect') or [0.0, 0.0, 1.0, 1.0]
    target = {'height_share': focus['height_share'], 'centre': focus.get('centre') or [(ui[0] + ui[2]) / 2, (ui[1] + ui[3]) / 2],
              **({'edges': focus['edges']} if focus.get('edges') else {})}
    points = sample(refs, depsgraph)
    result = core.solve(visible(points, depsgraph, scene), start, view, target, lens_mm, width / height)
    result['target'] = target
    result['focus'] = list(refs)
    rows = {}
    basis, tans = core.basis(*view), core.tangents(lens_mm, width / height)
    for ref in details:   # the thinnest parts' on-screen thickness at the solved pose (said, never forced)
        try:
            pts = visible(sample([ref], depsgraph, count=800), depsgraph, scene)(result['position'])
        except ValueError:
            rows[ref] = None
            continue
        screen, _ = core.project(pts, result['position'], basis, tans)
        rows[ref] = core.detail_px(screen, height)
    if rows:
        result['detail_px'] = rows
    scene.frame_set(1)
    return result
