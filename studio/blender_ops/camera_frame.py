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
from mathutils.bvhtree import BVHTree
import numpy as np

import camera_frame_core as core
import scene_geometry
import scene_roles
from scene_index import resolve_ref

SAMPLES = 4000          # points over the whole focus: thin layers keep a few hundred each
SEED = 7
HIT_TOL = 1e-3          # a ray reaching within this share of the distance hits the point itself
RAY_MAX_HOPS = 64       # layers a ray steps through to reach its target (_stops: two hits each); this only bounds a broken scene


def named_objects(ref):
    """The objects a declared id names (scene_index.resolve_ref: an object's tree, a group's copies); [] when none."""
    return (resolve_ref(ref) or {}).get('objects') or []


SAMPLE_CACHE_MAX = 256   # sample sets kept per Blender session (sample): a moment's focus and parts, over every trial


_SAMPLED = {}


def sample(refs, depsgraph, count=SAMPLES, seed=SEED, keep=None):
    """World points over the parts named by refs (and their mesh children; `keep(obj)` narrows those - the exposure
    survey keeps a part's own meshes, not a child part's): half spread over their surfaces by area,
    half along their edges by length - an outline lives on edges, and a long edge running off screen has its corners
    off screen too (a section's bottom edge seen up close), so area samples alone miss where the part ends.
    The same parts, unchanged, give the same points: a set is kept while every mesh it was drawn from stands as it did
    (_shape: placement, render flag and every evaluated vertex - a turn, a move, a cut, a deformation or another frame
    is a new set). Why (2026-10-15): one failing moment drew the same 4000 points 846 times - 40 % of its 337 s."""
    named = []
    for ref in refs:
        found = named_objects(ref)
        if not found:
            raise ValueError(f'FRAME_UNSOLVED: focus part {ref!r} is not in the scene')
        named.append(found)
    meshes = [o for found in named for o in found if o.type == 'MESH']
    key = (tuple(refs), count, seed) if keep is None else None
    shape = _shape(meshes, depsgraph) if key is not None else None
    held = _SAMPLED.get(key) if key is not None else None
    if held is not None and held[0] == shape:
        return list(held[1])
    out = _draw(named, depsgraph, count, seed, keep, refs)
    if key is not None:
        if len(_SAMPLED) >= SAMPLE_CACHE_MAX:
            _SAMPLED.pop(next(iter(_SAMPLED)))
        _SAMPLED[key] = (shape, tuple(out))
    return out


def _shape(meshes, depsgraph):
    """What a sample set was drawn from, exactly: each mesh's name, render flag, world matrix and evaluated vertices."""
    import hashlib
    rows = []
    for o in meshes:
        ev = o.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
            mesh.vertices.foreach_get('co', co)
            rows.append((o.name, bool(o.hide_render), tuple(v for row in ev.matrix_world for v in row), len(mesh.edges),
                         len(mesh.polygons), hashlib.blake2b(co.tobytes(), digest_size=16).digest()))
        finally:
            ev.to_mesh_clear()
    return tuple(rows)


def _draw(named, depsgraph, count, seed, keep, refs):
    tris, edges = [], []
    for found in named:
        for o in found:
            if o.type != 'MESH' or o.hide_render or (keep is not None and not keep(o)):
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
SEE_WINDOW = 'studio_see_through_window'   # [from, until, from, until, ...): the scene frames an object is a see-through
                                           # skin (show_scene.py, cheat_scene.py), each window its own pair


def see_windows(obj):
    """[(from, until)] the scene frames the object is a see-through skin (SEE_WINDOW), earliest first."""
    held = obj.get(SEE_WINDOW)
    values = list(held) if held is not None else []
    return [(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)]


def add_see_window(obj, window):
    """Join [from, until) to the object's windows: overlapping or touching ones become one, apart ones stay apart (an
    opening early in the shot and a cheat late in it leave the skin solid between them)."""
    merged = []
    for start, end in sorted(see_windows(obj) + [(int(window[0]), int(window[1]))]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    obj[SEE_WINDOW] = [v for pair in merged for v in pair]


def passes_through(obj, frame):
    """A ray goes on past this object: it never renders (cutters, helpers), its scene role blocks no ray (scene_roles
    'raycast': a flame or fog stand-in, 2026-10-13 - the rocket's own plume read as hiding its engine), or it is a
    see-through skin at this frame."""
    if obj.hide_render or obj.display_type in ('WIRE', 'BOUNDS') or not scene_roles.counts(obj, 'raycast'):
        return True
    return any(start <= frame < end for start, end in see_windows(obj))


def through_at(frame):
    """passes_through at one frame, remembered per object: for one batch of rays over a scene that does not change
    while they are cast (a solve's visibility, one blocked share). Why (2026-10-15): 8.9 million role and window
    look-ups for one failing moment."""
    held = {}

    def through(obj):
        name = obj.name
        if name not in held:
            held[name] = passes_through(obj, frame)
        return held[name]
    return through


def _stops(scene, depsgraph, origin, target, mine, through=None):
    """The objects the ray from origin to target stops at, in order - everything passes_through lets by stepped over -
    until it reaches the target (nothing more in between, a hit within HIT_TOL of it, or an object named in `mine`: the
    target's own). An object Blender names none of is None; RAY_MAX_HOPS running out (a broken scene) ends with None.
    through: passes_through for this batch (through_at), else asked per hit."""
    ray = target - origin
    distance = ray.length
    if distance <= 1e-6:
        return
    direction, start, left = ray / distance, origin, distance
    # RAY_MAX_HOPS bounds layers, not hits: a closed layer is met twice (its near face, then its far face), so a stack
    # of RAY_MAX_HOPS skins is followed through before the budget ends (review 2026-10-14)
    for _ in range(2 * RAY_MAX_HOPS):
        hit, location, _n, _i, obj, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
        if not hit or (location - target).length <= HIT_TOL * distance or (obj is not None and obj.name in mine):
            return
        if obj is None or not (through(obj) if through is not None else passes_through(obj, scene.frame_current)):
            yield obj
        step = (location - start).length + 1e-4
        start, left = start + direction * step, left - step
    yield None


_REACHED = object()


def walk(scene, depsgraph, origin, target, mine=frozenset(), through=None):
    """Follow the ray from origin to target past everything passes_through lets by (2026-10-14: rays gave up after four
    hits, so a part inside nested see-through skins read as hidden). (True, None) when it reaches the target - nothing
    in between, a hit within HIT_TOL of it, or an object named in `mine` (the target's own); else (False, the object it
    stopped at - None when Blender names none or RAY_MAX_HOPS ran out)."""
    first = next(_stops(scene, depsgraph, origin, target, mine, through), _REACHED)
    return (True, None) if first is _REACHED else (False, first)


def blockers(scene, depsgraph, origin, target, mine=frozenset(), through=None):
    """Every object between origin and target that stops a ray, layer after layer, in order (walk's rule; None left out):
    what an opening must clear so the target shows (show_scene.in_front, 2026-10-14: only the first was opened, so a
    tube inside a tank inside the skin stayed behind the tank)."""
    out = []
    for obj in _stops(scene, depsgraph, origin, target, mine, through):
        if obj is not None and obj not in out:
            out.append(obj)
    return out


def visible(points, depsgraph, scene=None):
    """visible(position) -> the points a camera at position reaches first."""
    scene = scene or bpy.context.scene

    def seen_from(position):
        origin = Vector(position)
        through = through_at(scene.frame_current)
        out = []
        for p in points:
            target = Vector(p)
            if (target - origin).length > 1e-6 and walk(scene, depsgraph, origin, target, through=through)[0]:
                out.append(p)
        return out
    return seen_from


def solve_focus(job, focus, refs, start, view, frame, lens_mm, details=(), roll_deg=0.0, floor=None, trace=None, tol=None,
                samples=SAMPLES):
    """Solve a focus framed whole ({height_share, centre}) for one move: the scene at `frame` (1-based Blender frame),
    sampled once. floor(position) -> (support top, margin) keeps the solve's poses above the support; tol and samples:
    a framing kept by its intent (camera_frame_core.solve tol; camera_moves._intent_pose)."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    width, height = job.get('output_size') or (1080, 1920)
    ui = job.get('ui_rect') or [0.0, 0.0, 1.0, 1.0]
    target = {'height_share': focus['height_share'], 'centre': focus.get('centre') or [(ui[0] + ui[2]) / 2, (ui[1] + ui[3]) / 2]}
    points = sample(refs, depsgraph, count=samples)
    result = core.solve(visible(points, depsgraph, scene), start, view, target, lens_mm, width / height, roll_deg=roll_deg,
                        floor=floor, trace=trace, tol=tol)
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
    own_names = {o.name for ref in own for o in named_objects(ref)}
    frame_basis, tans = core.basis(*core.view_of(position, aim), roll_deg=roll_deg), core.tangents(lens_mm, aspect)
    origin, shown, blocked = Vector(position), 0, 0
    through = through_at(scene.frame_current)
    for p in points:
        screen, _ = core.project([p], position, frame_basis, tans)
        if not screen or not (0.0 <= screen[0][0] <= 1.0 and 0.0 <= screen[0][1] <= 1.0):
            continue
        shown += 1
        target = Vector(p)
        if (target - origin).length <= 1e-6:
            continue
        reached, obj = walk(scene, depsgraph, origin, target, through=through)
        if not reached and (obj is None or obj.name not in own_names):
            blocked += 1
    scene.frame_set(1)
    return round(blocked / shown, 4) if shown else None


def is_atmosphere(obj):
    """A fog, smoke or beam volume the camera sees (scene_roles 'atmosphere': it blocks no ray, yet the frame shows it);
    one hidden from camera rays (a backdrop's reflection dome) is in no frame."""
    source = getattr(obj, 'original', None) or obj
    return not source.hide_render and getattr(source, 'visible_camera', True) and scene_roles.role(source) == 'atmosphere'


def atmosphere_objects(scene=None):
    return [o for o in (scene or bpy.context.scene).objects if o.type == 'MESH' and is_atmosphere(o)]


AXIS_RAYS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _holds(tree, point):
    """The closed surface in `tree` encloses `point`: an odd number of crossings along every axis direction (whatever
    way its faces point - a dome seen from inside, a box from outside); an open card or tube is crossed an even number
    of times along some axis, so it encloses nothing."""
    for axis in AXIS_RAYS:
        direction, start, crossed = Vector(axis), Vector(point), 0
        for _ in range(2 * RAY_MAX_HOPS):
            location, _n, _i, _d = tree.ray_cast(start, direction)
            if location is None:
                break
            crossed += 1
            start = location + direction * 1e-4
        if crossed % 2 == 0:
            return False
    return True


def _volume(obj, depsgraph):
    """What a camera could stand in of one atmosphere object, or None: a cloud of instanced pieces (dust, smoke, sparks:
    thousands of specks) is the region they fill, its box; one mesh is its surface, unless it is smaller than
    ATMOSPHERE_NEAR_M across - a speck, nothing to stand in. {'box': (lo, hi), 'tree': BVH or None}."""
    pieces = scene_geometry.records(depsgraph, keep=lambda host, name=obj.name: host.name == name)
    points = [w for _host, w, _tri, _inst in pieces if len(w)]
    if not points:
        return None
    every = np.vstack(points)
    box = (tuple(every.min(axis=0)), tuple(every.max(axis=0)))
    if any(inst for _h, _w, _t, inst in pieces) or len(pieces) > 1:
        return {'box': box, 'tree': None}
    if max(h - l for l, h in zip(*box)) < core.ATMOSPHERE_NEAR_M:
        return None
    _host, world, tri, _inst = pieces[0]
    if not len(tri):
        return None
    return {'box': box, 'tree': BVHTree.FromPolygons(world.tolist(), tri.tolist())}


def holds(volume, point):
    if volume['tree'] is None:
        return core.box_distance(volume['box'], point) == 0.0
    return core.box_distance(volume['box'], point) == 0.0 and _holds(volume['tree'], point)


def distance(volume, point):
    """How far a point is from the volume: 0 inside it."""
    if holds(volume, point):
        return 0.0
    if volume['tree'] is None:
        return core.box_distance(volume['box'], point)
    location, _n, _i, d = volume['tree'].find_nearest(Vector(point))
    return d if location is not None else float('inf')


def _entry(volume, origin, direction, far):
    """Where along the ray the volume begins within `far` (0 when the ray starts in it), else None."""
    if volume['tree'] is None:
        return core.box_entry(volume['box'], tuple(origin), tuple(direction), far)
    if holds(volume, origin):
        return 0.0
    location, _n, _i, d = volume['tree'].ray_cast(origin, direction, far)
    return d if location is not None else None


def volumes(depsgraph, focus, scene=None):
    """{object name: volume (_volume)} of the atmosphere objects standing apart from the focus: a volume that holds the
    focus point (a hall's fog box, a dust cloud the subject stands in) is the air the subject stands in, not a thing in
    front of it, and is left out. Why (2026-10-14): a camera inside an exhaust volume beside its subject showed nothing
    but the volume; a camera inside the fog that fills a whole hall shows the hall."""
    out = {}
    for obj in atmosphere_objects(scene):
        volume = _volume(obj, depsgraph)
        if volume is not None and (focus is None or not holds(volume, focus)):
            out[obj.name] = volume
    return out


def _solid(scene, depsgraph, origin, direction, far):
    """How far the ray goes before something that blocks a ray (passes_through lets volumes, helpers and see-through
    skins by); `far` when nothing does."""
    start, left = origin, far
    for _ in range(2 * RAY_MAX_HOPS):
        hit, location, _n, _i, obj, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
        if not hit:
            return far
        if obj is None or not passes_through(obj, scene.frame_current):
            return (location - origin).length
        step = (location - start).length + 1e-4
        start, left = start + direction * step, left - step
        if left <= 0:
            return far
    return far


COVER_GRID = (27, 48)   # rays across the frame (x, y) for the share an atmosphere volume covers


def atmosphere_cover(refs, frame, count=400):
    """What the atmosphere volumes standing apart from the focus (volumes) do to the scene camera's frame at Blender
    frame `frame`: {'frame_share': the share of rays through the frame that enter one nearer than the focus's centre and
    before anything solid (a backdrop card behind the set is not over the frame), 'focus_share': the share of the
    focus's visible points (in frame, reached past solids) seen only through one}, or None when no such volume stands
    in the scene or the focus has nothing to sample. Volumes keep blocking no ray (2026-10-13: a subject's own exhaust
    read as hiding the part it comes from); this measures them, it judges nothing. Why (2026-10-14): a camera 3 m from
    its subject stood in an exhaust volume - 0.997 of the frame - and every check passed."""
    scene = bpy.context.scene
    if not atmosphere_objects(scene) or scene.camera is None:
        return None
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    try:
        points = sample(refs, depsgraph, count=count)
    except ValueError:   # the focus shows no surface at this frame (an opening, a part hidden by the look): nothing to measure
        scene.frame_set(1)
        return None
    centre = Vector([sum(p[i] for p in points) / len(points) for i in range(3)])
    fog = list(volumes(depsgraph, centre, scene).values())
    if not fog:
        scene.frame_set(1)
        return None
    camera = scene.camera
    matrix = camera.matrix_world
    eye = matrix.translation.copy()
    corners = [matrix @ c for c in camera.data.view_frame(scene=scene)]   # top-right, bottom-right, bottom-left, top-left
    nx, ny = COVER_GRID
    within = (centre - eye).length
    covered = 0
    for i in range(nx):
        for j in range(ny):
            u, v = (i + 0.5) / nx, (j + 0.5) / ny
            top, bottom = corners[3].lerp(corners[0], u), corners[2].lerp(corners[1], u)
            direction = (top.lerp(bottom, v) - eye).normalized()
            reach = min(within, _solid(scene, depsgraph, eye, direction, camera.data.clip_end))
            covered += any(_entry(f, eye, direction, reach) is not None for f in fog)
    from bpy_extras.object_utils import world_to_camera_view
    seen = fogged = 0
    for p in points:
        target = Vector(p)
        ndc = world_to_camera_view(scene, camera, target)
        ray = target - eye
        if not (0.0 <= ndc.x <= 1.0 and 0.0 <= ndc.y <= 1.0 and ndc.z > 0) or ray.length <= 1e-6:
            continue
        if walk(scene, depsgraph, eye, target)[0]:
            seen += 1
            fogged += any(_entry(f, eye, ray.normalized(), ray.length) is not None for f in fog)
    scene.frame_set(1)
    return {'frame_share': round(covered / (nx * ny), 4), 'focus_share': round(fogged / seen, 4) if seen else None}


def _owner(obj):
    """What an object is part of: subject/part of its own or its nearest tagged parent's studio_part_id (a shell built
    as several panels is one shell), else its studio id or name."""
    o = obj
    while o is not None:
        if o.get('studio_part_id'):
            return f"{o.get('studio_subject_id', '')}/{o['studio_part_id']}"
        o = o.parent
    return str(obj.get('studio_id') or obj.name)


def _owners_behind(scene, depsgraph, point, direction, far):
    """The owners (_owner) of what the ray going on from a point, away from the camera, meets within `far`."""
    return {_owner(obj) for obj in _stops(scene, depsgraph, point + direction * 1e-4, point + direction * far, frozenset()) if obj is not None}


AROUND = 14          # directions (exposure_core.directions) a hidden point looks out along to judge what is around it
AROUND_SHARE = 0.5   # an owner met on at least this share of them, and behind the point as well as in front, wraps it


def _wraps(scene, depsgraph, point, owner, far, mine):
    """The owner lies round the point: met going out from it along at least AROUND_SHARE of AROUND evenly spread
    directions (the part's own objects stepped over). Independent of how anything is aligned to the world axes (a pod
    on the outside of a round body at 45 degrees sits in the body's box, and is not inside it - review 2026-10-14)."""
    import exposure_core
    hits = 0
    for d in exposure_core.directions(AROUND):
        d = Vector(d)
        if any(obj is not None and obj.name not in mine and _owner(obj) == owner
               for obj in _stops(scene, depsgraph, point + d * 1e-4, point + d * far, frozenset())):
            hits += 1
    return hits >= AROUND_SHARE * AROUND


def diagnose_part(ref, own, position, view, lens_mm, output_size, frame, count=400, roll_deg=0.0, shown=(), exposable=None):
    """Why a part does or does not show from a camera: {on_screen (share of its points inside the frame), occluders
    {object: share of its on-screen points it hides (the first thing in front)}, own_shell (what ENCLOSES it hides it
    by itself - no other view helps: an opening's job), shell_share, hidden_share, hidden_raw, px (thickness as shown_px
    judges it), px_if_open (its thickness were nothing in front: what opening it would give)}.
    Enclosed (A3-3, 2026-10-14): a hidden point is enclosed when something in front of it (any layer, by its owner -
    _owner: a shell of several panels is one) is also behind it on the same line and lies round it (_wraps: met from the
    point along most directions). A sibling in front - a leg, the next engine, a pillar beside it - only blocks: the
    camera may look from another side. occluder_parts: {owner ref (subject/part, or a studio id): share of the
    on-screen points it stands in front of, at any layer} - what blocks, by the parts a shot list or a cheat can name.
    shown: the other parts this moment shows - one hiding another is not a shell an opening could cut. own: the
    subject's ids (kept for callers; the shell is judged by enclosure, not by ownership).
    exposable: the part's exposable_share from its subject's exposure survey (studio/explains.py) - when given, hidden
    is judged against what any side reaches of it at all (exposure_core.hidden_against: 2026-10-14, an outer engine whose
    top sits inside the thrust section for good read 'hidden' from every side, 0.47 seen of the 0.70 any side sees);
    without one, against all of it (hidden_raw). The shell is judged the same way: hidden as if it were all in front."""
    import exposure_core
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    width, height = output_size
    mine_objects = named_objects(ref)
    mine = {o.name for o in mine_objects}
    others = {_owner(o) for ident in shown for o in named_objects(ident)} - {_owner(o) for o in mine_objects}
    try:
        points = sample([ref], depsgraph, count=count)
    except ValueError:   # nothing to sample: off frame, never hidden - with every key the camera search reads
        scene.frame_set(1)
        return {'on_screen': 0.0, 'occluders': {}, 'occluder_parts': {}, 'own_shell': False, 'hidden_share': 0.0, 'hidden_raw': 0.0, 'shell_share': 0.0,
                'px': 0.0, 'px_if_open': 0.0, 'screen_centre': None}
    basis, tans = core.basis(*view, roll_deg=roll_deg), core.tangents(lens_mm, width / height)
    origin, inside, seen, hidden, every, enclosed = Vector(position), [], [], {}, [], 0
    in_front_of = {}   # owner -> on-screen points it stands in front of (any layer): what blocks, by part
    through = through_at(scene.frame_current)
    for p in points:
        screen, _ = core.project([p], position, basis, tans)
        if screen:
            every.append(screen[0])
        if not screen or not (0.0 <= screen[0][0] <= 1.0 and 0.0 <= screen[0][1] <= 1.0):
            continue
        inside.append(screen[0])
        target = Vector(p)
        if (target - origin).length <= 1e-6:
            continue
        reached, obj = walk(scene, depsgraph, origin, target, mine, through)
        if reached:
            seen.append(screen[0])
            continue
        name = obj.name if obj is not None else '?'
        hidden[name] = hidden.get(name, 0) + 1
        front = {_owner(o) for o in blockers(scene, depsgraph, origin, target, mine, through)} - others
        for owner in front:
            in_front_of[owner] = in_front_of.get(owner, 0) + 1
        far = 2.0 * (target - origin).length
        behind = front & _owners_behind(scene, depsgraph, target, (target - origin).normalized(), far) if front else set()
        if any(_wraps(scene, depsgraph, target, owner, far, mine) for owner in sorted(behind)):
            enclosed += 1
    scene.frame_set(1)

    def thickness(screen):
        if len(screen) < 3:
            return 0.0
        xs, ys = [s[0] for s in screen], [s[1] for s in screen]
        return round(min(core.detail_px(screen, height), (max(xs) - min(xs)) * width, (max(ys) - min(ys)) * height), 1)
    total = max(1, len(inside))
    raw, shell = sum(hidden.values()) / total, enclosed / total

    def judged(share):   # a hidden share, against what any side reaches of the part when the survey says
        return exposure_core.hidden_against(1.0 - share, exposable) if exposable is not None and inside else share
    return {'on_screen': round(len(inside) / max(1, len(points)), 3),
            'occluders': {k: round(v / total, 3) for k, v in sorted(hidden.items(), key=lambda kv: -kv[1])},
            'occluder_parts': {k: round(v / total, 3) for k, v in sorted(in_front_of.items(), key=lambda kv: -kv[1])},
            'own_shell': judged(shell) >= HIDDEN_SHARE, 'shell_share': round(shell, 3),
            'hidden_share': round(judged(raw), 3), 'hidden_raw': round(raw, 3),
            **({'exposable': exposable} if exposable is not None else {}),
            'px': thickness(seen), 'px_if_open': thickness(inside),
            'screen_centre': [round(sum(s[i] for s in every) / len(every), 4) for i in (0, 1)] if every else None}


SHOWN_FILL = 3          # sample points per hit fine cell (raster_cells) shown_px wants: fewer, and a cell the part covers may
SHOWN_MAX = 12000       # stay empty (its area read small); it samples again, more, up to this


def shown_px(ref, position, view, lens_mm, output_size, frame, count=800, roll_deg=0.0):
    """How thick a part shows from a camera, in output pixels, by the build's own rule (frame_probe_core.thickness_px):
    its visible points rasterised on the id pass's grid (raster_cells, cells_shape), the least of their column extent,
    area over long side and short side; 0 when hidden. A part that covers more cells than its points fill is sampled
    again with more points (SHOWN_FILL, SHOWN_MAX), so its area is not read small."""
    import frame_probe_core as probe_core
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    width, height = output_size
    grid = probe_core.grid_size(output_size)
    basis, tans = core.basis(*view, roll_deg=roll_deg), core.tangents(lens_mm, width / height)
    n = count
    while True:
        try:
            pts = visible(sample([ref], depsgraph, count=n), depsgraph, scene)(position)
        except ValueError:
            scene.frame_set(1)
            return 0.0
        screen, _ = core.project(pts, position, basis, tans)
        screen = [s for s in screen if 0.0 <= s[0] <= 1.0 and 0.0 <= s[1] <= 1.0]
        cells = probe_core.raster_cells(screen, grid)
        fine, inside = probe_core.raster_fill(screen, grid)
        if inside < 3 or inside >= SHOWN_FILL * fine or n >= SHOWN_MAX:
            break
        n = min(SHOWN_MAX, max(2 * n, round(n * SHOWN_FILL * fine / max(1, inside))))
    scene.frame_set(1)
    if len(screen) < 3:
        return 0.0
    return round(probe_core.thickness_px(probe_core.cells_shape(cells, grid), output_size), 1)


PROBE_FOCUS = 400   # probe: focus points per pose (blocked_share takes 800) ...
PROBE_PART = 200    # ... and points per must-see part (shown_px takes 800): a filter, the chosen pose is measured fully


PROBE_FRONTS = 40   # blocked points per pose whose every layer is counted by owner (what stands in front: a share)
PROBE_SCORE = 0.5   # frames check scores cheats on probes of this share of the points (the full solve confirms)
PROBE_CUT_SHARE = 0.02   # a frame edge cuts the subject when this share of its points lie past it (a grazing sliver does not)


def frame_cuts(screen):
    """The frame edges past which PROBE_CUT_SHARE of these projected points (camera_frame_core.project) lie."""
    of = max(1, len(screen))
    past = {'left': sum(s[0] < 0.0 for s in screen), 'right': sum(s[0] > 1.0 for s in screen),
            'top': sum(s[1] < 0.0 for s in screen), 'bottom': sum(s[1] > 1.0 for s in screen)}
    return sorted(side for side, n in past.items() if n / of >= PROBE_CUT_SHARE)


def cut_sides(points, position, view, lens_mm, aspect, roll_deg=0.0):
    """frame_cuts of `points` seen from a camera at `position` looking along `view` - projection only, no ray."""
    screen, _behind = core.project(points, position, core.basis(*view, roll_deg=roll_deg), core.tangents(lens_mm, aspect))
    return frame_cuts(screen)


def probe_bins(points):
    """detail_px's columns for `points` probed points at shown_px's points per column (24 over 800): a column's extent
    read off fewer points comes out short (2026-10-15: 200 points over 24 columns read a 49 px lamp as 23 px)."""
    return max(4, round(24 * points / 800))


def probe_set(refs, own, parts, frame, share=1.0):
    """What probe measures, drawn once for a moment whose scene does not change while its poses are probed: the frame
    and its evaluated scene, one passes_through memo (through_at), the focus points, each must-see part's points and the
    object names that are the subject's own and each part's own (share: of PROBE_FOCUS and PROBE_PART). The caller puts
    frame 1 back when the moment is done."""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    out = {'frame': frame, 'depsgraph': depsgraph, 'through': through_at(frame),
           'focus': sample(refs, depsgraph, count=max(40, round(PROBE_FOCUS * share))), 'parts': {},
           'own': {o.name for ref in own for o in named_objects(ref)},
           'itself': {}}
    for ref in parts:
        try:
            out['parts'][ref] = sample([ref], depsgraph, count=max(40, round(PROBE_PART * share)))
        except ValueError:
            out['parts'][ref] = None
        out['itself'][ref] = {o.name for o in named_objects(ref)}   # a part is hidden by anything but itself - its body too
    return out


def _at(prepared):
    """(scene, depsgraph) of a probe_set's frame: set again only when something else moved the scene off it."""
    scene = bpy.context.scene
    if scene.frame_current != prepared['frame']:
        scene.frame_set(prepared['frame'])
    return scene, prepared['depsgraph']


def seen_height(prepared, position, view, lens_mm, aspect, roll_deg=0.0):
    """{height_share, centre, whole_in_frame} the focus stands at from a camera at `position`, as a picture's frame row
    reads it: the frame-height extent of all its probe_set points in front of the camera - hidden ones too and not cut
    at the frame edge (a picture's height_share is its whole box, a close-up's subject running past the frame);
    whole_in_frame: every point in front of the camera and inside the frame. None when none is in front."""
    frame_basis, tans = core.basis(*view, roll_deg=roll_deg), core.tangents(lens_mm, aspect)
    screen, behind = core.project(prepared['focus'], position, frame_basis, tans)
    if not screen:
        return None
    ys, xs = [s[1] for s in screen], [s[0] for s in screen]
    return {'height_share': round(max(ys) - min(ys), 4), 'centre': [round((max(xs) + min(xs)) / 2, 4), round((max(ys) + min(ys)) / 2, 4)],
            'whole_in_frame': not behind and min(xs) >= 0.0 and max(xs) <= 1.0 and min(ys) >= 0.0 and max(ys) <= 1.0}


def probe(refs, own, parts, position, view, lens_mm, output_size, frame, roll_deg=0.0, prepared=None, blocked_max=None,
          count_fronts=True):
    """What a camera standing at `position` would see, with few rays (camera_moves._solve_intent filters placed views by
    it before any solve): {blocked_share (as blocked_share: focus points in frame whose ray first meets something not
    `own`; None when none is in frame), must_see_px {part: px as shown_px}, blockers {owner: share of the in-frame focus
    points it stands in front of}, part_blockers {part: {owner: share}}}. Every layer in front of a blocked point is
    counted by owner (_owner) on up to PROBE_FRONTS of them - what a see-through cheat or a moved neighbour would have to
    clear. in_frame {part: share of its points inside the frame}: a part off the frame is not hidden. prepared: probe_set
    of this moment (else drawn here). blocked_max: once more than this share of the in-frame focus is blocked the pose
    is refused whatever the rest shows - the probe stops there (blocked_share then a lower bound, parts not measured).
    count_fronts False: nothing is listed in front (a cheat's score reads none of it). seen_box (the box of the focus
    points inside the frame), cut_sides (frame edges past which PROBE_CUT_SHARE of its points lie), focus_in_frame
    (share of its points inside the frame): where the subject stands, for the picture's layout."""
    prepared = prepared or probe_set(refs, own, parts, frame)
    scene, depsgraph = _at(prepared)
    width, height = output_size
    frame_basis, tans = core.basis(*view, roll_deg=roll_deg), core.tangents(lens_mm, width / height)
    through = prepared['through']
    origin = Vector(position)
    own_names = prepared['own']

    def inside(p):
        screen, _ = core.project([p], position, frame_basis, tans)
        return screen[0] if screen and 0.0 <= screen[0][0] <= 1.0 and 0.0 <= screen[0][1] <= 1.0 else None

    def fronts(target, own):
        return {_owner(o) for o in blockers(scene, depsgraph, origin, target, frozenset(), through) if o.name not in own}

    def shares(counts, counted, hidden, of):   # owner -> share of `of` points it stands in front of (from `counted` of `hidden`)
        scale = hidden / max(1, counted) / max(1, of)
        return {o: round(n * scale, 3) for o, n in sorted(counts.items(), key=lambda kv: -kv[1])}
    blocked = counted = 0
    counts = {}
    framed_focus = [p for p in prepared['focus'] if inside(p) is not None and (Vector(p) - origin).length > 1e-6]
    shown = len(framed_focus)
    # where the subject stands in the frame (the intent camera keeps its picture's layout: camera_moves_core.picture_terms):
    # the box of its points inside the frame, the edges it runs past, the share of it in frame
    every, _behind = core.project(prepared['focus'], position, frame_basis, tans)
    framed_screen = [inside(p) for p in framed_focus]
    placed = {'seen_box': [round(min(s[0] for s in framed_screen), 4), round(min(s[1] for s in framed_screen), 4),
                           round(max(s[0] for s in framed_screen), 4), round(max(s[1] for s in framed_screen), 4)] if framed_screen else None,
              'cut_sides': frame_cuts(every),
              'focus_in_frame': round(shown / max(1, len(prepared['focus'])), 4)}
    for p in framed_focus:
        target = Vector(p)
        reached, obj = walk(scene, depsgraph, origin, target, through=through)
        if not reached and (obj is None or obj.name not in own_names):
            blocked += 1
            if count_fronts and counted < PROBE_FRONTS:
                counted += 1
                for owner in fronts(target, own_names):
                    counts[owner] = counts.get(owner, 0) + 1
            if blocked_max is not None and blocked > blocked_max * shown:   # refused whatever the rest shows
                return {'blocked_share': round(blocked / shown, 4), 'must_see_px': {}, 'blockers': shares(counts, counted, blocked, shown),
                        'part_blockers': {}, 'in_frame': {}, **placed}
    px, part_counts, in_frame = {}, {}, {}
    for ref in parts:
        points = prepared['parts'].get(ref)
        if not points:
            px[ref], in_frame[ref] = 0.0, 0.0
            continue
        seen, framed, hidden, tallied, front = [], 0, 0, 0, {}
        for p in points:
            screen = inside(p)
            target = Vector(p)
            if screen is None or (target - origin).length <= 1e-6:
                continue
            framed += 1
            if walk(scene, depsgraph, origin, target, through=through)[0]:
                seen.append(screen)
                continue
            hidden += 1
            if count_fronts and tallied < PROBE_FRONTS:
                tallied += 1
                for owner in fronts(target, prepared['itself'][ref]):
                    front[owner] = front.get(owner, 0) + 1
        if len(seen) < 3:
            px[ref] = 0.0
        else:
            xs, ys = [s[0] for s in seen], [s[1] for s in seen]
            px[ref] = round(min(core.detail_px(seen, height, bins=probe_bins(len(points))), (max(xs) - min(xs)) * width,
                                (max(ys) - min(ys)) * height), 1)
        part_counts[ref] = shares(front, tallied, hidden, framed)
        in_frame[ref] = round(framed / max(1, len(points)), 3)
    return {'blocked_share': round(blocked / shown, 4) if shown else None, 'must_see_px': px,
            'blockers': shares(counts, counted, blocked, shown), 'part_blockers': part_counts, 'in_frame': in_frame, **placed}


FLOOR_HOPS = 16   # surfaces a column search steps through (slab bottoms, stacked decks); this only bounds a broken scene
DOWN, UP = Vector((0.0, 0.0, -1.0)), Vector((0.0, 0.0, 1.0))


def _supports(depsgraph, skip=frozenset()):
    """(BVH, [(first triangle, object name)], [(lo, hi) world box of each piece]) over the visible meshes whose scene
    role counts 'floor' (a deck, a pad, a table, the ground), instances included, objects named in `skip` left out;
    None when there are none."""
    rows = [r for r in scene_geometry.records(depsgraph, keep=lambda h: h.name not in skip and not h.hide_render
                                              and scene_roles.counts(h, 'floor')) if len(r[2])]
    if not rows:
        return None
    offsets = np.cumsum([0] + [len(r[1]) for r in rows[:-1]])
    vertices, triangles = np.vstack([r[1] for r in rows]), np.vstack([r[2] + o for r, o in zip(rows, offsets)])
    owners = list(zip(np.cumsum([0] + [len(r[2]) for r in rows[:-1]]).tolist(), [r[0].name for r in rows]))
    boxes = [(r[1].min(axis=0), r[1].max(axis=0)) for r in rows]
    return BVHTree.FromPolygons(vertices.tolist(), triangles.tolist()), owners, boxes


def _top_under(tree, x, y, from_z):
    """z of the first upward-facing face met going straight down at (x, y) from from_z, or None. Faces seen from inside
    (a slab's underside) are stepped through."""
    start = Vector((x, y, from_z))
    for _ in range(FLOOR_HOPS):
        location, normal, index, _distance = tree.ray_cast(start, DOWN)
        if location is None:
            return None, None
        if normal.z > 0.0:
            return location.z, index
        start = location + DOWN * 1e-4
    return None, None


def support_floor(depsgraph, scene, skip=frozenset()):
    """floor(position, from_z=None) -> the z of the first upward-facing support top met going straight down at the
    position's x, y from `from_z` (default: above every support), or None where no support lies under it. None when
    the scene has no support (scene_roles 'floor'), so a scene without one keeps its old behaviour. The camera solve
    starts the search at the subject's middle or the camera, whichever is higher: a support above the subject (a
    street over a station hall) is its ceiling, not its floor. What a camera inside a slab meets is the keep-out's job
    (camera_moves._keep_out)."""
    built = _supports(depsgraph, skip)
    if built is None:
        return None
    tree, _owners, boxes = built
    high = max(float(hi[2]) for _lo, hi in boxes) + 1.0

    def floor(position, from_z=None):
        return _top_under(tree, position[0], position[1], high if from_z is None else from_z)[0]
    return floor


SINK_POINTS = 400   # surface points per subject object and frame (area-weighted, fixed seed): a wide face crossing a
                    # support between its vertices is caught; its vertices near a support are tested too (a dipping corner)
MOVED_M = 1e-4      # a subject object whose points leave their first-frame place by more than this is moved by the shot


def _surface(world, tri, count, seed=SEED):
    """`count` area-weighted points over the triangles (numpy, fixed seed: a rigidly moving piece gives the same points)."""
    a, b, c = world[tri[:, 0]], world[tri[:, 1]], world[tri[:, 2]]
    area = np.linalg.norm(np.cross(b - a, c - a), axis=1)
    if not len(area) or area.sum() <= 0:
        return np.empty((0, 3))
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(area), size=count, p=area / area.sum())
    u, v = rng.random(count), rng.random(count)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    return a[pick] + (b - a)[pick] * u[:, None] + (c - a)[pick] * v[:, None]


def sinks(scene, subject_names, frames, moved=(), allowance=None):
    """How far the subject objects the shot MOVES reach under the top of the support below them: (deepest row or None,
    [moved object names]). A row is {object, support, depth_m, at, frame} (frame: shot frame, 0-based), with
    allowance_m when the object had one: the deepest row is the one furthest past its allowance.
    Principle: a subject the shot moves (its transform, drives, pose or deformation change its geometry over the
    frames) must not move into what it stands on - feet 0.30 m in the support after a lean read as a crash (2026-10-14).
    `moved`: objects a cheat moved, scaled or turned (cheat_scene.moved_objects) - judged even when the cheat holds them
    still over the whole shot; `allowance` {object: m}: how deep each already reached without the cheat (sink_depths),
    so a cheat is refused only for sinking a thing deeper than it stood.
    A subject placed once and never moved is the author's design and is not judged here: a cutaway building whose
    basement levels go below a support ground is built that way (review, 2026-10-14). The limit: a statically
    misplaced subject (standing 0.3 m in a deck from frame 1 to the end) is not this gate's job.
    subject_names: subject objects, collection instancers included (their instances count as the instancer). Points:
    SINK_POINTS surface points per object plus its vertices inside a support's box (scene_geometry.records: modifiers,
    instances, deformation). The support under a point is searched from the subject's highest point down: a support
    above the whole subject (a street over a station hall) is its ceiling; the subject's middle (the camera's rule) is
    not enough here - an arm's gripper hanging past the table edge puts it under the table top (support_smoke). A
    subject part that is itself a support (a pad the scene is about) is not measured."""
    worst, moved, _motion = _sink_rows(scene, subject_names, frames, moved)
    allowance = allowance or {}
    rows = [{**row, **({'allowance_m': round(allowance[name], 4)} if allowance.get(name) else {})} for name, row in sorted(worst.items())]
    deepest = max(rows, key=lambda r: r['depth_m'] - r.get('allowance_m', 0.0), default=None)
    return deepest, moved


def sink_depths(scene, names, frames):
    """{object: deepest m it reaches into a support over the frames} for every object in `names` the shot holds still
    (0 when nowhere under one): what a cheat about to move them is measured against (sinks allowance). An object the
    shot itself moves gets 0: its own sinking is SUBJECT_SINKS with no allowance, and a cheat added on top must not
    excuse it (a driven subject 0.30 m in its deck stays refused when it is also turned - review, 2026-10-15)."""
    worst, _moved, motion = _sink_rows(scene, names, frames, names)
    return {name: worst[name]['depth_m'] if name in worst and name not in motion else 0.0 for name in names}


def _sink_rows(scene, subject_names, frames, forced=()):
    """({object: its deepest row}, [moved names], {names the frames themselves move}) - sinks' measure: objects in
    `forced` count as moved."""
    track = []
    for frame in frames:
        scene.frame_set(frame + 1)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        pieces = {}
        for host, world, tri, _inst in scene_geometry.records(depsgraph, keep=lambda h: h.name in subject_names and not h.hide_render
                                                              and not scene_roles.counts(h, 'floor')):
            if len(tri):
                pieces.setdefault(host.name, []).append((world, tri))
        built = _supports(depsgraph, frozenset(subject_names))
        track.append((frame, pieces, built))
    if not track:
        return {}, [], set()
    first = {}
    for frame, pieces, _built in track:
        for name, rows in pieces.items():
            first.setdefault(name, (frame, np.vstack([_surface(w, t, SINK_POINTS) for w, t in rows])))
    forced = set(forced)
    motion = set()
    for frame, pieces, _built in track:
        for name, rows in pieces.items():
            now, then = np.vstack([_surface(w, t, SINK_POINTS) for w, t in rows]), first[name][1]
            if now.shape != then.shape or (len(now) and np.abs(now - then).max() > MOVED_M):
                motion.add(name)
    moved = motion | {n for _frame, pieces, _built in track for n in pieces if n in forced}
    worst = {}
    for frame, pieces, built in track:
        if built is None or not any(n in moved for n in pieces):
            continue
        tree, owners, boxes = built
        every = np.vstack([w for rows in pieces.values() for w, _t in rows])
        top, span = float(every[:, 2].max()), float(every[:, 2].max() - every[:, 2].min())
        for name in sorted(n for n in pieces if n in moved):
            points = np.vstack([_surface(w, t, SINK_POINTS) for w, t in pieces[name]] + [w for w, _t in pieces[name]])
            near = np.zeros(len(points), dtype=bool)
            for lo, hi in boxes:
                near |= ((points[:, 0] >= lo[0]) & (points[:, 0] <= hi[0]) & (points[:, 1] >= lo[1]) & (points[:, 1] <= hi[1])
                         & (points[:, 2] >= lo[2] - span) & (points[:, 2] < hi[2]))
            for x, y, z in points[near]:
                surface, index = _top_under(tree, float(x), float(y), top)
                best = worst.get(name)
                if surface is None or surface <= z or (best and surface - z <= best['depth_m'] + 1e-5):   # the first frame it is deepest
                    continue
                support = next(n for first_tri, n in reversed(owners) if index >= first_tri)
                worst[name] = {'object': name, 'support': support, 'depth_m': round(float(surface - z), 4),
                               'at': [round(float(x), 3), round(float(y), 3), round(float(z), 3)], 'frame': frame}
    scene.frame_set(1)
    return worst, sorted(moved), motion
