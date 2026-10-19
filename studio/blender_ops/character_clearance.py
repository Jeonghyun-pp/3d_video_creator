"""Character clearance (runs inside Blender): does a person's body pass through a solid thing of the scene?

Why (2026-10-19, the bike reel): the mechanic's torso and arms went through the bicycle frame in six shots and every
check passed - the engine measured part against part of one mechanism (MECHANISM_INTERFERENCE) and subjects against
their support (SUBJECT_SINKS), never a person against what stands around them. The user saw it at once.

Measured on the posed, animated scene (after the author, fill, drives and contact poses): each character's figure
meshes, evaluated with their armature at sampled frames, against every solid mesh near it as the depsgraph renders it
(collection and scatter instances included): a thing that blocks the camera's rays (scene_roles 'raycast') and is not a
floor (the ground under the feet is SUBJECT_SINKS'). Depth is the deepest sampled point of one inside the other
(geom_checks: ray parity, closed meshes, behind the nearest face), in metres: a room or shell built inward holds the
body in front of its walls - where it stands, not what it passes through. A holding hand or foot (characters[].contacts)
may close round what it holds a little deeper (GRIP_RADIUS_M, CONTACT_TOL_M); the rest of the body may not.
"""
from __future__ import annotations

import json
import math

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

import geom_checks
from scene_roles import counts

CLEAR_TOL_M = 0.02     # a body may touch a thing this deep (the mesh's skin and a tube's wall; SUBJECT_SINKS's 0.02 m)
CONTACT_TOL_M = 0.05   # ... and, by a limb that holds something, this deep near what it holds (GRIP_RADIUS_M): fingers
                       # close round a 4 cm grip and touch what it is mounted on (2026-10-19: a thumb 0.041 m into a crate
                       # behind its handle), a sole presses a pedal
GRIP_RADIUS_M = 0.15   # the hand or foot and its fingers or toes, this close to their contact, grip
SAMPLES = 12           # frames read over the shot (and the first and last)
NEAR_M = 1.0           # solids farther than this from the body's box are not read


class Shape:
    """A mesh in world space, as geom_checks.Group keeps it: tree, probe points, closed or not, box."""

    def __init__(self, name, verts, polys):
        self.name, self.verts, self.polys = name, verts, polys
        self.probes = verts + [sum((verts[i] for i in p), Vector()) / len(p) for p in polys]
        self.closed = geom_checks.closed(polys)
        self.tree = BVHTree.FromPolygons(verts, polys, epsilon=0.0) if polys else None
        self.box = geom_checks.aabb(verts) if verts else None


def characters():
    """{character id: [figure mesh objects]} of the scene (characters.build tags them studio_part_id 'figure')."""
    out = {}
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.get('studio_part_id') == 'figure' and obj.get('studio_subject_id'):
            out.setdefault(obj['studio_subject_id'], []).append(obj)
    return out


def figure_shape(character_id, meshes, dg):
    verts, polys = [], []
    for obj in meshes:
        if obj.hide_render:   # not rendered at this frame (appear_frame keys it)
            continue
        v, p = geom_checks.eval_mesh(obj, dg)
        base = len(verts)
        verts += v
        polys += [tuple(i + base for i in poly) for poly in p]
    return Shape(character_id, verts, polys) if polys else None


def _instance_box(ob, matrix):
    corners = [matrix @ Vector(c) for c in ob.bound_box]
    return (Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners))),
            Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners))))


def _apart(a, b, pad=0.0):
    return any(a[1][i] + pad < b[0][i] or b[1][i] + pad < a[0][i] for i in range(3))


def solids(dg, near, cache=None, frame=None):
    """[Shape] of the solid meshes the depsgraph renders whose box comes within NEAR_M of `near` (a box): objects and
    instances alike, the figures left out. cache: {(frame, name): Shape} kept across a search - moving a body moves no
    solid."""
    out = []
    for n, inst in enumerate(dg.object_instances):
        ob = inst.object
        if ob.type != 'MESH':
            continue
        orig = ob.original
        if orig.hide_render or orig.get('studio_part_id') == 'figure' or not counts(orig, 'raycast') or counts(orig, 'floor'):
            continue
        matrix = inst.matrix_world.copy()
        if _apart(_instance_box(ob, matrix), near, NEAR_M):
            continue
        name = f"{orig.name}#{n}" if inst.is_instance else orig.name
        key = (frame, name)
        if cache is not None and key in cache:
            out.append(cache[key])
            continue
        mesh = ob.to_mesh()
        shape = Shape(orig.get('studio_id') or orig.name, [matrix @ v.co for v in mesh.vertices], [tuple(p.vertices) for p in mesh.polygons])
        ob.to_mesh_clear()
        if cache is not None:
            cache[key] = shape
        out.append(shape)
    return out


def _deepest(probes, tree, is_closed):
    """(depth m, point) of the deepest probe inside a closed mesh's material, else (None, None). Inside by ray parity and
    behind the nearest face (against its normal): a room or a shell built inward holds its inside in front of its faces,
    so what stands in it is not in its walls (2026-10-19 review: a worker in a flipped room read 1.5 m inside it)."""
    if not is_closed:
        return None, None
    best, at = 0.0, None
    for p in geom_checks.sample(probes, 1500):
        if geom_checks.inside(tree, p):
            location, normal, _, d = tree.find_nearest(p)
            if location is not None and normal is not None and (p - location).dot(normal) > 0:
                continue   # in front of its nearest face: an inward-built enclosure's inside
            if (d or 0.0) > best:
                best, at = d or 0.0, p
    return best, at


def pair_depth(figure, solid):
    """(depth m, point) of the deeper of the figure in the solid or the solid in the figure; (0, None) when apart; (None,
    None) when they overlap and neither mesh is closed (no inside to measure)."""
    if figure.tree is None or solid.tree is None or _apart(figure.box, solid.box):
        return 0.0, None
    if not figure.tree.overlap(solid.tree):
        return 0.0, None
    found = [r for r in (_deepest(figure.probes, solid.tree, solid.closed), _deepest(solid.probes, figure.tree, figure.closed))
             if r[0] is not None]
    return max(found, key=lambda r: r[0]) if found else (None, None)


def _bone_near(armature, point):
    """The deform bone whose segment passes nearest a world point (the body part that is inside)."""
    best = (math.inf, None)
    for pb in armature.pose.bones:
        if not pb.bone.use_deform:
            continue
        a, b = armature.matrix_world @ pb.head, armature.matrix_world @ pb.tail
        ab = b - a
        t = max(0.0, min(1.0, (point - a).dot(ab) / max(ab.length_squared, 1e-12)))
        d = (point - (a + ab * t)).length
        if d < best[0]:
            best = (d, pb.name)
    return best[1]


def _gripping(armature, bone, point, grips):
    """The pass is a grip: the deepest point is on a holding limb's end (the hand or foot, or a finger or toe under it),
    near its contact target (GRIP_RADIUS_M). grips: [(end bone name, target object)] (character_contacts)."""
    if armature is None or bone is None or point is None:
        return False
    for end, target in grips or ():
        held = armature.pose.bones.get(end)
        chain = {end, *(b.name for b in held.children_recursive)} if held else {end}
        if bone in chain and (point - target.matrix_world.translation).length <= GRIP_RADIUS_M:
            return True
    return False


def frames_of(count, samples=SAMPLES):
    return sorted({0, count - 1, *(round(i * (count - 1) / max(1, samples - 1)) for i in range(samples))})


def measure(character_id, meshes, frames, grips=None, cache=None, first=False):
    """[{frame, object, depth_m, bone, tolerance_m}] - the deepest pass of this character into each solid over the frames,
    one row per object (its worst frame). grips: its holding limbs and their targets (_gripping); cache: solids kept
    across calls (a search); first: stop at the first pass found (a search asks only whether the place is clear)."""
    scene = bpy.context.scene
    armature = next((o.parent for o in meshes if o.parent is not None and o.parent.type == 'ARMATURE'), None)
    worst = {}
    for frame in frames:
        scene.frame_set(frame + 1)
        dg = bpy.context.evaluated_depsgraph_get()
        figure = figure_shape(character_id, meshes, dg)
        if figure is None:
            continue
        for solid in solids(dg, figure.box, cache, frame):
            depth, point = pair_depth(figure, solid)
            if depth == 0.0:
                continue
            bone = _bone_near(armature, point) if (armature is not None and point is not None) else None
            tolerance = CONTACT_TOL_M if _gripping(armature, bone, point, grips) else CLEAR_TOL_M
            score = math.inf if depth is None else depth
            row = worst.get(solid.name)
            if score > tolerance and (row is None or score > (math.inf if row['depth_m'] is None else row['depth_m'])):
                worst[solid.name] = {'frame': frame, 'object': solid.name, 'depth_m': None if depth is None else round(depth, 4),
                                     'tolerance_m': tolerance, 'bone': bone}
                if first:
                    scene.frame_set(1)
                    return list(worst.values())
    scene.frame_set(1)
    return sorted(worst.values(), key=lambda r: -(math.inf if r['depth_m'] is None else r['depth_m']))


def check(job, held=None):
    """{characters: {id: [rows]}, tolerance_m, gate_failures, warnings} over the shot; held: {character id: {'grips':
    [(end bone, target object name)]}} from the contact poses (character_contacts.apply). None when the scene has no
    characters."""
    import gate_policy
    found = characters()
    if not found:
        return None
    frames = frames_of(job['shot']['duration_frames'])
    rows = {cid: measure(cid, meshes, frames, grips=[(end, bpy.data.objects[name]) for end, name in ((held or {}).get(cid) or {}).get('grips', [])
                                                    if name in bpy.data.objects])
            for cid, meshes in sorted(found.items())}
    failures = []
    for cid, through in rows.items():
        for r in through[:1]:
            how = 'overlaps it (depth not measurable: an open mesh)' if r['depth_m'] is None else f"is {r['depth_m']:.3f} m inside it"
            failures.append({'gate': 'CHARACTER_THROUGH_OBJECT', 'character': cid, **r,
                             'why': f"{cid}'s {r['bone'] or 'body'} {how} - {r['object']} at shot frame {r['frame']} (allowed "
                                    f"{r['tolerance_m']} m) - give the character contacts (the limbs on what they hold) and let it "
                                    'stand clear (scene.characters[].contacts, stance), or move it (at, facing_deg)'})
    errors, softened = gate_policy.split(failures, 'gate')
    return {'characters': rows, 'frames': frames, 'tolerance_m': CLEAR_TOL_M, 'contact_tolerance_m': CONTACT_TOL_M,
            'gate_failures': errors, 'warnings': [f"CHARACTER_THROUGH_OBJECT (warning by policy): {f['why']}" for f in softened]}


def write(output, report):
    (output / 'character_clearance.json').write_text(json.dumps(report, ensure_ascii=False, indent=1))
