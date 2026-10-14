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


def sample(refs, depsgraph, count=SAMPLES, seed=SEED, keep=None):
    """World points over the parts named by refs (and their mesh children; `keep(obj)` narrows those - the exposure
    survey keeps a part's own meshes, not a child part's): half spread over their surfaces by area,
    half along their edges by length - an outline lives on edges, and a long edge running off screen has its corners
    off screen too (a section's bottom edge seen up close), so area samples alone miss where the part ends."""
    tris, edges = [], []
    for ref in refs:
        named = named_objects(ref)
        if not named:
            raise ValueError(f'FRAME_UNSOLVED: focus part {ref!r} is not in the scene')
        for o in named:
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
SEE_WINDOW = 'studio_see_through_window'   # [from, until) scene frames an object is a see-through skin (show_scene.py)


def passes_through(obj, frame):
    """A ray goes on past this object: it never renders (cutters, helpers), its scene role blocks no ray (scene_roles
    'raycast': a flame or fog stand-in, 2026-10-13 - the rocket's own plume read as hiding its engine), or it is a
    see-through skin at this frame."""
    if obj.hide_render or obj.display_type in ('WIRE', 'BOUNDS') or not scene_roles.counts(obj, 'raycast'):
        return True
    window = obj.get(SEE_WINDOW)
    return window is not None and window[0] <= frame < window[1]


def _stops(scene, depsgraph, origin, target, mine):
    """The objects the ray from origin to target stops at, in order - everything passes_through lets by stepped over -
    until it reaches the target (nothing more in between, a hit within HIT_TOL of it, or an object named in `mine`: the
    target's own). An object Blender names none of is None; RAY_MAX_HOPS running out (a broken scene) ends with None."""
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
        if obj is None or not passes_through(obj, scene.frame_current):
            yield obj
        step = (location - start).length + 1e-4
        start, left = start + direction * step, left - step
    yield None


_REACHED = object()


def walk(scene, depsgraph, origin, target, mine=frozenset()):
    """Follow the ray from origin to target past everything passes_through lets by (2026-10-14: rays gave up after four
    hits, so a part inside nested see-through skins read as hidden). (True, None) when it reaches the target - nothing
    in between, a hit within HIT_TOL of it, or an object named in `mine` (the target's own); else (False, the object it
    stopped at - None when Blender names none or RAY_MAX_HOPS ran out)."""
    first = next(_stops(scene, depsgraph, origin, target, mine), _REACHED)
    return (True, None) if first is _REACHED else (False, first)


def blockers(scene, depsgraph, origin, target, mine=frozenset()):
    """Every object between origin and target that stops a ray, layer after layer, in order (walk's rule; None left out):
    what an opening must clear so the target shows (show_scene.in_front, 2026-10-14: only the first was opened, so a
    tube inside a tank inside the skin stayed behind the tank)."""
    out = []
    for obj in _stops(scene, depsgraph, origin, target, mine):
        if obj is not None and obj not in out:
            out.append(obj)
    return out


def visible(points, depsgraph, scene=None):
    """visible(position) -> the points a camera at position reaches first."""
    scene = scene or bpy.context.scene

    def seen_from(position):
        origin = Vector(position)
        out = []
        for p in points:
            target = Vector(p)
            if (target - origin).length > 1e-6 and walk(scene, depsgraph, origin, target)[0]:
                out.append(p)
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
    own_names = {o.name for ref in own for o in named_objects(ref)}
    frame_basis, tans = core.basis(*core.view_of(position, aim), roll_deg=roll_deg), core.tangents(lens_mm, aspect)
    origin, shown, blocked = Vector(position), 0, 0
    for p in points:
        screen, _ = core.project([p], position, frame_basis, tans)
        if not screen or not (0.0 <= screen[0][0] <= 1.0 and 0.0 <= screen[0][1] <= 1.0):
            continue
        shown += 1
        target = Vector(p)
        if (target - origin).length <= 1e-6:
            continue
        reached, obj = walk(scene, depsgraph, origin, target)
        if not reached and (obj is None or obj.name not in own_names):
            blocked += 1
    scene.frame_set(1)
    return round(blocked / shown, 4) if shown else None


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
        reached, obj = walk(scene, depsgraph, origin, target, mine)
        if reached:
            seen.append(screen[0])
            continue
        name = obj.name if obj is not None else '?'
        hidden[name] = hidden.get(name, 0) + 1
        front = {_owner(o) for o in blockers(scene, depsgraph, origin, target, mine)} - others
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


def sinks(scene, subject_names, frames):
    """How far the subject objects the shot MOVES reach under the top of the support below them: (deepest row or None,
    [moved object names]). A row is {object, support, depth_m, at, frame} (frame: shot frame, 0-based).
    Principle: a subject the shot moves (its transform, drives, pose or deformation change its geometry over the
    frames) must not move into what it stands on - the booster's feet 0.30 m in the deck after its lean (2026-10-14).
    A subject placed once and never moved is the author's design and is not judged here: a cutaway building whose
    basement levels go below a support ground is built that way (review, 2026-10-14). The limit: a statically
    misplaced subject (standing 0.3 m in a deck from frame 1 to the end) is not this gate's job.
    subject_names: subject objects, collection instancers included (their instances count as the instancer). Points:
    SINK_POINTS surface points per object plus its vertices inside a support's box (scene_geometry.records: modifiers,
    instances, deformation). The support under a point is searched from the subject's highest point down: a support
    above the whole subject (a street over a station hall) is its ceiling; the subject's middle (the camera's rule) is
    not enough here - an arm's gripper hanging past the table edge puts it under the table top (support_smoke). A
    subject part that is itself a support (a pad the scene is about) is not measured."""
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
        return None, []
    first = {}
    for frame, pieces, _built in track:
        for name, rows in pieces.items():
            first.setdefault(name, (frame, np.vstack([_surface(w, t, SINK_POINTS) for w, t in rows])))
    moved = set()
    for frame, pieces, _built in track:
        for name, rows in pieces.items():
            now, then = np.vstack([_surface(w, t, SINK_POINTS) for w, t in rows]), first[name][1]
            if now.shape != then.shape or (len(now) and np.abs(now - then).max() > MOVED_M):
                moved.add(name)
    deepest = None
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
                if surface is None or surface <= z or (deepest and surface - z <= deepest['depth_m'] + 1e-5):   # the first frame it is deepest
                    continue
                support = next(n for first_tri, n in reversed(owners) if index >= first_tri)
                deepest = {'object': name, 'support': support, 'depth_m': round(float(surface - z), 4),
                           'at': [round(float(x), 3), round(float(y), 3), round(float(z), 3)], 'frame': frame}
    scene.frame_set(1)
    return deepest, sorted(moved)
