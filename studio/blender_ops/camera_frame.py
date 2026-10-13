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
import scene_roles
from scene_tools import object_by_id

SAMPLES = 4000          # points over the whole focus: thin layers keep a few hundred each
SEED = 7
HIT_TOL = 1e-3          # a ray reaching within this share of the distance hits the point itself
SKIP_HITS = 4           # rays continue past objects that never render (cutters, helpers)


def _meshes(obj):
    return [o for o in [obj, *obj.children_recursive] if o.type == 'MESH' and not o.hide_render]


def sample(refs, depsgraph, count=SAMPLES, seed=SEED, keep=None):
    """World points over the parts named by refs (and their mesh children; `keep(obj)` narrows those - the exposure
    survey keeps a part's own meshes, not a child part's): half spread over their surfaces by area,
    half along their edges by length - an outline lives on edges, and a long edge running off screen has its corners
    off screen too (a section's bottom edge seen up close), so area samples alone miss where the part ends."""
    tris, edges = [], []
    for ref in refs:
        obj = object_by_id(ref)
        if obj is None:
            raise ValueError(f'FRAME_UNSOLVED: focus part {ref!r} is not in the scene')
        for o in _meshes(obj):
            if keep is not None and not keep(o):
                continue
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


HIDDEN_SHARE = 0.5   # a part is hidden (not merely nicked) when this share of what is in frame of it is behind something
SEE_WINDOW = 'studio_see_through_window'   # [from, until) scene frames an object is a see-through skin (show_scene.py)


def passes_through(obj, frame):
    """A ray goes on past this object: it never renders (cutters, helpers), its scene role blocks no ray (scene_roles
    'raycast': a flame or fog stand-in, 2026-10-13 - the rocket's own plume read as hiding its engine), or it is a
    see-through skin at this frame."""
    if obj.hide_render or obj.display_type in ('WIRE', 'BOUNDS') or not scene_roles.counts(obj, 'raycast'):
        return True
    window = obj.get(SEE_WINDOW)
    return window is not None and window[0] <= frame < window[1]


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
                if obj is not None and passes_through(obj, scene.frame_current):
                    step = (location - start).length + 1e-4
                    start, left = start + direction * step, left - step
                    continue
                break
        return out
    return seen_from


def solve_focus(job, focus, refs, start, view, frame, lens_mm, details=(), roll_deg=0.0):
    """Solve camera.move.focus for one move: the scene at `frame` (1-based Blender frame), sampled once."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    width, height = job.get('output_size') or (1080, 1920)
    ui = job.get('ui_rect') or [0.0, 0.0, 1.0, 1.0]
    target = {'height_share': focus['height_share'], 'centre': focus.get('centre') or [(ui[0] + ui[2]) / 2, (ui[1] + ui[3]) / 2],
              **({'edges': focus['edges']} if focus.get('edges') else {})}
    points = sample(refs, depsgraph)
    result = core.solve(visible(points, depsgraph, scene), start, view, target, lens_mm, width / height, roll_deg=roll_deg)
    result['target'] = target
    result['focus'] = list(refs)
    rows = {}
    basis, tans = core.basis(*view, roll_deg=roll_deg), core.tangents(lens_mm, width / height)
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


def blocked_share(refs, own, position, aim, lens_mm, aspect, frame, count=800, roll_deg=0.0):
    """Share of the focus that shows in frame but sits behind something that is not the subject (a plume stand-in,
    a deck, a tower): sampled points inside the frame whose ray from the camera first meets another object. The
    subject hiding its own back is not blocking. None when nothing of the focus is in frame."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = sample(refs, depsgraph, count=count)
    own_names = {o.name for ref in own for o in ([object_by_id(ref)] + list(object_by_id(ref).children_recursive) if object_by_id(ref) else [])}
    frame_basis, tans = core.basis(*core.view_of(position, aim), roll_deg=roll_deg), core.tangents(lens_mm, aspect)
    origin, shown, blocked = Vector(position), 0, 0
    for p in points:
        screen, _ = core.project([p], position, frame_basis, tans)
        if not screen or not (0.0 <= screen[0][0] <= 1.0 and 0.0 <= screen[0][1] <= 1.0):
            continue
        shown += 1
        target = Vector(p)
        ray = target - origin
        distance = ray.length
        if distance <= 1e-6:
            continue
        direction, start, left = ray / distance, origin, distance
        for _ in range(SKIP_HITS):
            hit, location, _n, _i, obj, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
            if not hit or (location - target).length <= HIT_TOL * distance:
                break
            if obj is not None and passes_through(obj, scene.frame_current):
                step = (location - start).length + 1e-4
                start, left = start + direction * step, left - step
                continue
            if obj is None or obj.name not in own_names:
                blocked += 1
            break
    scene.frame_set(1)
    return round(blocked / shown, 4) if shown else None


def diagnose_part(ref, own, position, view, lens_mm, output_size, frame, count=400, roll_deg=0.0, shown=()):
    """Why a part does or does not show from a camera: {on_screen (share of its points inside the frame), occluders
    {object: share of its on-screen points it hides}, own_shell (one of them is the subject's own - its housing),
    px (thickness as shown_px judges it), px_if_open (its thickness were nothing in front: what opening it would give)}.
    own: the subject's ids (their objects other than the part are its own shell); shown: the other parts this moment
    shows - one hiding another is not a shell an opening could cut."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    width, height = output_size
    part = object_by_id(ref)
    mine = {o.name for o in ([part] + list(part.children_recursive) if part else [])}
    tree = lambda ident: ([object_by_id(ident)] + list(object_by_id(ident).children_recursive)) if object_by_id(ident) else []  # noqa: E731
    shell = {o.name for ident in own for o in tree(ident)} - mine - {o.name for ident in shown for o in tree(ident)}
    try:
        points = sample([ref], depsgraph, count=count)
    except ValueError:
        scene.frame_set(1)
        return {'on_screen': 0.0, 'occluders': {}, 'own_shell': False, 'px': 0.0, 'px_if_open': 0.0}
    basis, tans = core.basis(*view, roll_deg=roll_deg), core.tangents(lens_mm, width / height)
    origin, inside, seen, hidden, every = Vector(position), [], [], {}, []
    for p in points:
        screen, _ = core.project([p], position, basis, tans)
        if screen:
            every.append(screen[0])
        if not screen or not (0.0 <= screen[0][0] <= 1.0 and 0.0 <= screen[0][1] <= 1.0):
            continue
        inside.append(screen[0])
        target = Vector(p)
        ray = target - origin
        distance = ray.length
        if distance <= 1e-6:
            continue
        direction, start, left = ray / distance, origin, distance
        for _ in range(SKIP_HITS):
            hit, location, _n, _i, obj, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
            if not hit or (location - target).length <= HIT_TOL * distance or (obj is not None and obj.name in mine):
                seen.append(screen[0])
                break
            if obj is not None and passes_through(obj, scene.frame_current):
                step = (location - start).length + 1e-4
                start, left = start + direction * step, left - step
                continue
            name = obj.name if obj is not None else '?'
            hidden[name] = hidden.get(name, 0) + 1
            break
    scene.frame_set(1)

    def thickness(screen):
        if len(screen) < 3:
            return 0.0
        xs, ys = [s[0] for s in screen], [s[1] for s in screen]
        return round(min(core.detail_px(screen, height), (max(xs) - min(xs)) * width, (max(ys) - min(ys)) * height), 1)
    total = max(1, len(inside))
    return {'on_screen': round(len(inside) / max(1, len(points)), 3),
            'occluders': {k: round(v / total, 3) for k, v in sorted(hidden.items(), key=lambda kv: -kv[1])},
            'own_shell': sum(v for k, v in hidden.items() if k in shell) / total >= HIDDEN_SHARE,
            'hidden_share': round(sum(hidden.values()) / total, 3), 'px': thickness(seen), 'px_if_open': thickness(inside),
            'screen_centre': [round(sum(s[i] for s in every) / len(every), 4) for i in (0, 1)] if every else None}


def shown_px(ref, position, view, lens_mm, output_size, frame, count=800, roll_deg=0.0):
    """How thick a part shows from a camera, in output pixels, as the frame probe judges screen.details: the thinner of
    its column extent (a layer edge-on) and the short side of what shows of it (a compact part); 0 when hidden."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    width, height = output_size
    try:
        pts = visible(sample([ref], depsgraph, count=count), depsgraph, scene)(position)
    except ValueError:
        return 0.0
    screen, _ = core.project(pts, position, core.basis(*view, roll_deg=roll_deg), core.tangents(lens_mm, width / height))
    screen = [s for s in screen if 0.0 <= s[0] <= 1.0 and 0.0 <= s[1] <= 1.0]
    scene.frame_set(1)
    if len(screen) < 3:
        return 0.0
    xs, ys = [s[0] for s in screen], [s[1] for s in screen]
    short = min((max(xs) - min(xs)) * width, (max(ys) - min(ys)) * height)
    return round(min(core.detail_px(screen, height), short), 1)
