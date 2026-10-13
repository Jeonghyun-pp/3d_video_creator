"""Run inside Blender (headless): how much of each part of a subject can be seen from outside the intact subject.

blender --background --factory-startup --python exposure_survey.py -- job.json out.json
job: {spec: <subject spec>, directions?, samples?}. out: {threshold, directions, samples, parts: {part_id: {best_share,
best_dir, hidden_by}}} - best_share is the largest share of the part's surface points a ray from outside reaches first,
over evenly spread directions (exposure_core); hidden_by names the parts that stopped the rays most. No render, rays only.
The host side is studio/subjects.py `subject exposure`; the reading, studio/explains.py.

The subject is built from its spec as it stands (no joint posed, nothing cut or see-through): a part that only shows
once something moves (a leg unfolding) is caught at the shot, where the camera's own diagnosis looks at that moment.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

import camera_frame  # noqa: E402
import exposure_core  # noqa: E402
from modeling import build_subject  # noqa: E402
from scene_tools import object_by_id  # noqa: E402


def _part_of(obj):
    """The part id an object belongs to (array copies and helpers carry their part's tags, or sit under it)."""
    while obj is not None:
        if obj.get('studio_part_id'):
            return str(obj['studio_part_id'])
        obj = obj.parent
    return None


def _coats(scene, depsgraph, location, direction, coat, own):
    """The object hit lies on the part as a coat (or coats over coats): the part's own surface is right behind it."""
    start, left = location + direction * 1e-4, coat
    for _ in range(camera_frame.SKIP_HITS):
        hit, at, _n, _i, behind, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
        if not hit or behind is None:
            return False
        if behind.name in own:
            return True
        step = (at - start).length + 1e-4
        start, left = start + direction * step, left - step
    return False


def _radius(objects, depsgraph):
    points = []
    for o in objects:
        if o.type == 'MESH' and not o.hide_render:
            ev = o.evaluated_get(depsgraph)
            points += [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    centre = sum(points, Vector()) / max(1, len(points))
    return centre, max((p - centre).length for p in points) if points else 1.0


def survey(spec, directions=exposure_core.DIRECTIONS, samples=exposure_core.SAMPLES):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    built = build_subject(spec)
    return measure(spec['subject_id'], built['root'], sorted(built['parts']), directions, samples)


def measure(subject_id, root, part_ids, directions=exposure_core.DIRECTIONS, samples=exposure_core.SAMPLES):
    """The survey of a subject already in the scene (root and its tree), part by part."""
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    centre, radius = _radius([root, *root.children_recursive], depsgraph)
    reach = 2.0 * radius + 1.0
    # a coat lying on a surface (soot, seams, a decal a millimetre out) does not hide what it coats: a first hit this close
    # to the point reaches it - the camera's own tolerance (camera_frame.HIT_TOL of the distance) at a whole-subject view
    coat = camera_frame.HIT_TOL * 2.0 * radius
    dirs = [Vector(d) for d in exposure_core.directions(directions)]
    parts = {}
    for part_id in part_ids:
        ref = f"{subject_id}/{part_id}"
        obj = object_by_id(ref)
        try:
            points = [Vector(p) for p in camera_frame.sample([ref], depsgraph, count=samples)]
        except ValueError:   # nothing that renders: nothing to show
            parts[part_id] = {'best_share': None, 'best_dir': None, 'hidden_by': []}
            continue
        own = {o.name for o in [obj, *obj.children_recursive]}
        best, best_dir, stoppers = -1.0, None, {}
        for d in dirs:
            seen = 0
            for target in points:
                start = target + d * reach
                direction = (target - start).normalized()
                left = (target - start).length * 1.001
                for _ in range(camera_frame.SKIP_HITS):
                    hit, location, _n, _i, first, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
                    if not hit or first is None or (location - target).length <= coat:
                        seen += 1
                        break
                    if camera_frame.passes_through(first, scene.frame_current):
                        step = (location - start).length + 1e-4
                        start, left = start + direction * step, left - step
                        continue
                    if first.name in own or _coats(scene, depsgraph, location, direction, coat, own):
                        seen += 1
                    else:
                        other = _part_of(first) or first.name
                        stoppers[other] = stoppers.get(other, 0) + 1
                    break
            share = seen / len(points)
            if share > best:
                best, best_dir = share, [round(c, 4) for c in d]
        parts[part_id] = {'best_share': round(best, 4), 'best_dir': best_dir,
                          'hidden_by': [p for p, _n in sorted(stoppers.items(), key=lambda kv: -kv[1])[:3]]}
    return {'threshold': camera_frame.HIDDEN_SHARE, 'directions': directions, 'samples': samples, 'parts': parts}


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    job = json.loads(Path(args[0]).read_text())
    import contrib_loader
    contrib_loader.TABLE.update(job.get('contrib') or {})   # resolved and hash-checked on the host (subjects.exposure)
    result = survey(job['spec'], job.get('directions', exposure_core.DIRECTIONS), job.get('samples', exposure_core.SAMPLES))
    Path(args[1]).write_text(json.dumps(result, indent=2, sort_keys=True))
