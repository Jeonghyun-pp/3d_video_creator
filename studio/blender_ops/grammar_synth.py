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
the camera's and the subject's velocity in the world, how much of the picture lies inside the subject's shell (a
section's view) and whether the camera is inside it, the
camera's height over what is below it, its speed, lens and pitch (+ looks up).

Run: blender --background SCENE.blend --python grammar_synth.py -- OUT.json [WIDTH] [FIRST LAST | - -] [sky|nosky] [SUBJECTS] [PARTS]
(SUBJECTS: comma-separated ids of what the shot is about, for near, subject flow and the shell; PARTS: comma-separated ids of
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


SKIP_STEPS = 32   # unseen things a ray may pass through before what it shows


def _seen(obj):
    return not obj.hide_render and getattr(obj, 'visible_camera', True)


def _hit(scene, depsgraph, origin, direction):
    """What the patch shows: the first thing along the ray that renders. Viewport ray casts also meet things the camera
    never shows - a section's cutter box, helpers - and are carried through them (2026-10-12: the rocket's sections read
    as the cutter's flat face, inside 0 and its motion measured on a box nobody sees)."""
    start = origin
    for _ in range(SKIP_STEPS):
        hit, location, _normal, _index, obj, _matrix = scene.ray_cast(depsgraph, start, direction)
        if not hit or obj is None:
            break
        original = obj.original if hasattr(obj, 'original') else obj
        if _seen(original):
            return original.name, original.matrix_world.inverted() @ location
        start = location + direction * 1e-4
    return None, origin + direction * FAR


def _pixel(scene, cam, world, w, h):
    ndc = world_to_camera_view(scene, cam, world)
    return ndc.x * w, (1.0 - ndc.y) * h, ndc.z


def _height(scene, depsgraph, cam):
    """Distance from the camera down to whatever is below it (a deck, the sea), or its z over 0 when nothing is."""
    origin = cam.matrix_world.translation
    name, where = _hit(scene, depsgraph, origin, Vector((0.0, 0.0, -1.0)))
    return float(origin.z - (bpy.data.objects[name].matrix_world @ where).z) if name else float(origin.z)


def _cast(scene, cam, centres, w, h):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return [_hit(scene, depsgraph, origin, direction) for origin, direction in _rays(scene, cam, centres, w, h)], depsgraph


def resolve_root(ident):
    from scene_index import resolve_group
    return resolve_group(ident)


ENCLOSED_HITS = 3   # of the six axis rays from a point, at least this many meet the shell: the point is inside it. A point on
                    # the outer skin escapes to the open side (1 of 6); one behind a section cut escapes only through the cut
                    # (and an open tube's ends) - 3 to 5
AXES = [Vector(v) for v in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]


SHELL_SHARE = 0.8   # seen meshes with a box at least this share of the largest one's are the shell together (a body and
                    # the soot skin laid over it)


def _shell(names):
    """The subject's outer shell, intact: [(object, BVH tree of its own mesh before modifiers)] for its seen meshes as large
    as its largest (a rocket's body, a building's envelope). A section is usually a boolean modifier; the mesh before it is
    the whole shell, so a point the section exposes still lies inside it. Only what renders counts: a section's cutter is a
    large hidden box among the subject's children (2026-10-12, the rocket's 20 x 10 x 60 m cutter was taken for the body)."""
    from mathutils.bvhtree import BVHTree
    meshes = [o for o in (bpy.data.objects[n] for n in names)
              if o.type == 'MESH' and _seen(o) and len(o.data.polygons)]
    if not meshes:
        return []
    volume = lambda o: o.dimensions.x * o.dimensions.y * o.dimensions.z  # noqa: E731
    largest = max(volume(o) for o in meshes)
    return [(o, BVHTree.FromPolygons([v.co for v in o.data.vertices], [p.vertices for p in o.data.polygons]))
            for o in meshes if volume(o) >= SHELL_SHARE * largest]


def _enclosed(shell, point, eye=None):
    """Whether a world point lies inside the shell: ENCLOSED_HITS of the six axis rays, in a shell mesh's own axes, meet that
    intact mesh. eye: where the point is seen from - a surface point is tested a millimetre toward it, on the side the camera
    sees (on the surface itself, rays along the face graze the walls it meets: an outer wall read as inside, smoke 2026-10-12)."""
    if eye is not None:
        point = point + (eye - point).normalized() * 1e-3
    for obj, tree in shell:
        local = obj.matrix_world.inverted() @ point
        reach = max(obj.dimensions) * 4.0 + 1.0
        if sum(tree.ray_cast(local, axis, reach)[0] is not None for axis in AXES) >= ENCLOSED_HITS:
            return True
    return False


def _opened(subject_names, parts):
    """The shells a shot can open: the subject's (its body) and each part it explains (a leg's piston: its outer tube). A
    surface inside any of them is an inside shown - a tank inside the body, a tube inside the piston. 2026-10-12: with the
    body alone, the rocket's piston section (outside the body) read as no inside at all."""
    shells = list(_shell(subject_names))
    for part in parts:
        for obj, tree in _shell(_members([part])):
            if all(obj is not o for o, _ in shells):
                shells.append((obj, tree))
    return shells


def _members(idents):
    """Object names under declared ids (scene_index.resolve_group: the one rule the frame probe uses - a part class such
    as booster/landing_assembly names a group, not an object), with every child."""
    from scene_index import AnchorIndex, resolve_group
    index = AnchorIndex()
    return {o.name for ident in idents for root in resolve_group(ident, index) for o in (root, *root.children_recursive)}


def measure(width=192, first=None, last=None, sky=True, subject=None, parts=()):
    """sky: rays that hit nothing count as points at infinity (a real sky's clouds); False drops them, as grammar_probe drops a
    flat sky - the setting to compare with the pixel measurement of an untextured render. subject: the shot's subject id,
    or several (near, subject flow, the shell need it); parts: name prefixes of what the shot explains (part_box: where they are
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
    subjects = [subject] if isinstance(subject, str) else list(subject or [])
    subject_names, part_names = _members(subjects), _members(parts)
    shell = _opened(subject_names, parts)
    body = next((root for ident in subjects for root in resolve_root(ident)), None)   # the subject whose motion counters
    anchors, depsgraph = _cast(scene, cam, centres, w, h)
    for f in range(first, last):
        origin = cam.matrix_world.translation.copy()
        body_at = body.matrix_world.translation.copy() if body else None
        height = _height(scene, depsgraph, cam)
        dist = [None if name is None else (bpy.data.objects[name].matrix_world @ where - origin).length for name, where in anchors]
        on = [name in subject_names for name, _ in anchors]
        inner = [bool(shell) and o and _enclosed(shell, bpy.data.objects[name].matrix_world @ where, origin) for o, (name, where) in zip(on, anchors)]
        at_part = [(x / w, y / h) for (y, x), (name, _) in zip(centres, anchors) if name in part_names]
        anchors = [(name, where.copy()) for name, where in anchors]
        # where each patch's point stands now, held still: the next camera's view of it is the camera's own share of the
        # motion (camera_comfort) - a stone falling past the lens moves the picture, not the camera
        held = [(bpy.data.objects[name].matrix_world @ where) if name else where for name, where in anchors]
        scene.frame_set(f + 1)
        dx, dy, keep = [], [], []
        cx, cy, ckeep = [], [], []
        for (y, x), (name, where), still in zip(centres, anchors, held):
            world = bpy.data.objects[name].matrix_world @ where if name else where
            px, py, depth = _pixel(scene, cam, world, w, h)
            ok = depth > 0 and math.isfinite(px) and math.isfinite(py) and (sky or name is not None)
            keep.append(ok)
            dx.append(px - x if ok else 0.0)
            dy.append(py - y if ok else 0.0)
            sx, sy, sdepth = _pixel(scene, cam, still, w, h)
            sok = sdepth > 0 and math.isfinite(sx) and math.isfinite(sy) and (sky or name is not None)
            ckeep.append(sok)
            cx.append(sx - x if sok else 0.0)
            cy.append(sy - y if sok else 0.0)
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
                # what a section shows: share of the picture on surfaces inside the subject's shell, and the camera itself inside
                'inside_view': round(sum(inner) / len(centres), 4) if shell else None,
                'camera_inside': _enclosed(shell, origin) if shell else None,
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
        still = np.array(ckeep)
        if still.sum() >= MIN_PATCHES:   # the camera's own motion: the same fit over the points held still
            csol, _res, _inl = fit(points[still], np.array(cx)[still], np.array(cy)[still], np.ones(int(still.sum())))
            row['camera'] = {'tx': round(float(csol[0]) / w, 6), 'ty': round(float(csol[1]) / w, 6),
                             'ds': round(float(np.log1p(csol[2])), 6), 'rot': round(float(csol[3]), 6)}
        rows.append(row)
        anchors = following
    return {'schema_version': 3, 'width': w, 'height': h, 'fps': scene.render.fps / scene.render.fps_base, 'first': first, 'last': last,
            'sky': sky, 'subject': subject, 'parts': list(parts), 'pairs': rows,
            'method': 'synthetic: scene points carried frame to frame, grammar_probe.fit; camera: the same points held still'}


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    target = args[0]
    width = int(args[1]) if len(args) > 1 else 192
    first, last = (int(args[2]), int(args[3])) if len(args) > 3 and args[2] != '-' else (None, None)   # '-': the scene's own range
    sky = (args[4] != 'nosky') if len(args) > 4 else True
    subject = [i for i in args[5].split(',') if i] if len(args) > 5 and args[5] != '-' else None
    parts = tuple(p for p in args[6].split(',') if p) if len(args) > 6 and args[6] != '-' else ()
    import ids_core   # world element -> the scene ids that build it, as in the build
    ids_core.from_job(json.loads(Path(args[7]).read_text()) if len(args) > 7 and args[7] != '-' else {})
    Path(target).write_text(json.dumps(measure(width, first, last, sky, subject, parts)))
