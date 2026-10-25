"""Openings a camera flies through: a cut in each slab the journey declares passable, opened as the camera reaches it,
and the camera's path checked against everything else.

A move whose plan names passages (camera_moves_core: fly_to's notes.passages {mark: slab}, each mark a waypoint where
its line crosses that slab) gets, per passage, a box cutter at the crossing - a share of the slab's shorter side wide,
through its whole thickness - and a `reveal` action (reveal.py) bound to the camera's own cues: from the move's first
frame, growing from a sliver, fully open LEAD_S before the camera passes the mark. The line of sight to the destination
runs through each cutter's middle, so the destination shows through the opening as it grows.

Only declared slabs are cut. `through` steps the camera along its timed path against the scene as it stands at each
frame (the cuts included): a step through anything else is CAMERA_PASS_THROUGH - never routed round by switching the
guard off (camera_rig.GUARD_DEFAULTS no_pass_through stays on).

Why (2026-10-25): a fly-in through a roof failed the pass-through guard; the user's rule is a proper fix, so the opening
is the engine's, bound to what the director declared.
"""
from __future__ import annotations

import bmesh
import bpy
from mathutils import Vector

from scene_index import resolve_ref

PREFIX = 'StudioPassage_'
ACTION_PREFIX = 'passage_'
OPEN_SHARE = 0.2      # the opening is this share of the slab's shorter side wide ...
OPEN_MIN_M = 1.5      # ... and never narrower (a camera and its near field pass a metre and a half)
DEPTH_FACTOR = 3.0    # the cutter runs this many slab thicknesses through it (and half a metre more): clean through
LEAD_S = 0.25         # fully open this long before the camera reaches the slab
SLIVER = 0.02         # the opening starts this share of its width (it grows; nothing pops)
CAP = 'StudioPassageCap'


def _cap():
    material = bpy.data.materials.get(CAP)
    if material is None:
        material = bpy.data.materials.new(CAP)
        material.diffuse_color = (0.18, 0.18, 0.19, 1.0)
    return material


def _cutter(name, point, size):
    """A closed, outward-wound box mesh of `size` centred at `point` (reused across calls: cues settle in rounds)."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
    obj.animation_data_clear()
    for vertex in obj.data.vertices:   # the cube's corners at the box's own size (scale 1 is the full opening; idempotent)
        vertex.co = Vector(tuple(0.5 * size[i] * (1 if vertex.co[i] > 0 else -1) for i in range(3)))
    obj.data.update()
    obj.location, obj.scale = Vector(point), (1.0, 1.0, 1.0)
    obj['studio_id'] = name
    return obj


def open_passages(job, plan, geo, cues, fps):
    """The cutters and reveal actions of every passage the plan names (replacing an earlier call's); rows for the report."""
    shot = job['shot']
    shot['actions'] = [a for a in shot.get('actions') or [] if not str(a.get('action_id', '')).startswith(ACTION_PREFIX)]
    rows = []
    for mark, slab in sorted((plan.get('notes') or {}).get('passages', {}).items()):
        cue = f'cam-{mark}'
        if cue not in cues:
            continue
        (x0, y0, z0), (x1, y1, z1) = geo['boxes'][slab]
        width = max(OPEN_MIN_M, OPEN_SHARE * min(x1 - x0, y1 - y0))
        depth = (z1 - z0) * DEPTH_FACTOR + 0.5
        cutter = _cutter(f"{PREFIX}{shot['shot_id']}_{mark}", plan['waypoints'][plan['marks'][mark]], (width, width, depth))
        found = resolve_ref(slab)
        meshes = [o for o in (found or {}).get('objects') or [] if o.type == 'MESH']
        if not meshes:
            raise ValueError(f'CAMERA_MOVE: passable {slab} names no mesh to open in the scene')
        open_at = max(1, cues[cue] - max(1, round(LEAD_S * fps)))
        shot['actions'].append({
            'action_id': f'{ACTION_PREFIX}{mark}', 'type': 'reveal', 'start_frame': 0, 'end_frame': 1, 'easing': 'ease_in_out',
            'targets': [{'instance_id': '', 'part_id': o.name} for o in meshes],
            'time_binding': {'start_cue_id': 'cam-wp0', 'end_cue_id': cue, 'start_offset_frames': 0, 'end_offset_frames': open_at - cues[cue]},
            'params': {'cutter_object_id': cutter.name, 'cap_material_id': _cap().name,
                       'cutter_keys': [{'t': 0, 'scale': [SLIVER, SLIVER, 1.0]}, {'t': 1, 'scale': [1.0, 1.0, 1.0]}]}})
        rows.append({'mark': mark, 'slab': slab, 'cutter': cutter.name, 'width_m': round(width, 3), 'open_by_frame': open_at,
                     'camera_at_frame': cues[cue]})
    return rows


def through(dense, progress, flown, count):
    """CAMERA_PASS_THROUGH (ValueError) at the first frame whose step along the timed path goes through a solid thing of
    the scene as it stands then (camera_rig._crossing: openings cut, hidden helpers stepped over); else None."""
    import camera_rig
    lengths = [0.0]
    for a, b in zip(dense, dense[1:]):
        lengths.append(lengths[-1] + (Vector(b) - Vector(a)).length)

    def eye(f):
        s = progress(f / max(1, count - 1)) * flown
        i = next((k for k in range(1, len(lengths)) if lengths[k] >= s), len(lengths) - 1)
        span = max(1e-9, lengths[i] - lengths[i - 1])
        return Vector(dense[i - 1]).lerp(Vector(dense[i]), min(1.0, max(0.0, (s - lengths[i - 1]) / span)))
    scene = bpy.context.scene
    try:
        last = eye(0)
        for f in range(1, count):
            scene.frame_set(f + 1)
            now = eye(f)
            hit = camera_rig._crossing(scene, bpy.context.evaluated_depsgraph_get(), last, now)
            if hit:
                raise ValueError(f'CAMERA_PASS_THROUGH: the camera passes through {hit} at frame {f} - only the slabs the journey '
                                 'declares passable open as the camera reaches them: declare it passable where the journey '
                                 'goes through it, or fly another way (azimuth, elevation)')
            last = now
    finally:
        scene.frame_set(1)
