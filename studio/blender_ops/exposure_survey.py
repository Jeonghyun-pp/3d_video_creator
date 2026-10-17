"""Run inside Blender (headless): how much of each part of a subject can be seen from outside the intact subject.

blender --background --factory-startup --python exposure_survey.py -- job.json out.json
job: {spec: <subject spec>, directions?, samples?}. out: {threshold, directions, samples, parts: {part_id: {best_share,
best_dir, hidden_by, per_direction, exposable_share, enclosed_by, box, reach_m}}} - best_share is the largest share of the part's
surface points a ray from outside reaches first, over evenly spread directions (exposure_core); per_direction is that
share from each direction (directions() order); exposable_share the share of points some direction reaches directly -
the ray meets the point itself, not the part's own near side: what is left is inside something for good (a bulb's foot
in its socket, a bell's top in its bay), and a view that shows all the rest shows all it can; hidden_by
names the parts that stopped the rays first most often; enclosed_by the share of all the part's rays (directions x
points) on which another part lies both in front of the point and behind it - wrapped round it, as a sleeve round a pipe
(a neighbour beside it is crossed from its side only, never on both); box is the part's world box (the subject is
built at the origin: its own frame); reach_m the most its thinnest side can show at (_reach). No render, rays only.
The host side is studio/subjects.py `subject exposure`
(schema_version exposure_core.SCHEMA_VERSION); the reading, studio/explains.py.

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
import coats  # noqa: E402
import exposure_core  # noqa: E402
from modeling import build_subject  # noqa: E402


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
    for _ in range(camera_frame.RAY_MAX_HOPS):
        hit, at, _n, _i, behind, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
        if not hit or behind is None:
            return False
        if behind.name in own:
            return True
        step = (at - start).length + 1e-4
        start, left = start + direction * step, left - step
    return False


def _behind(scene, depsgraph, point, direction, reach, own):
    """The parts the ray going on from a point, away from where it came, meets within `reach` (its own part stepped over)."""
    out, start, left = set(), point + direction * 1e-4, reach
    for _ in range(camera_frame.RAY_MAX_HOPS):
        hit, location, _n, _i, obj, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
        if not hit:
            break
        if obj is not None and obj.name not in own and not camera_frame.passes_through(obj, scene.frame_current):
            out.add(_part_of(obj) or obj.name)
        step = (location - start).length + 1e-4
        start, left = start + direction * step, left - step
    return out


def _radius(objects, depsgraph):
    points = []
    for o in objects:
        if o.type == 'MESH' and not o.hide_render:
            ev = o.evaluated_get(depsgraph)
            points += [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    centre = sum(points, Vector()) / max(1, len(points))
    return centre, max((p - centre).length for p in points) if points else 1.0


def _box(objects, depsgraph):
    """[[lo], [hi]] world box of what the objects render (their evaluated bound boxes: never smaller than the part)."""
    points = [o.evaluated_get(depsgraph).matrix_world @ Vector(c) for o in objects if o.type == 'MESH' and not o.hide_render
              for c in o.evaluated_get(depsgraph).bound_box]
    return [[round(min(p[i] for p in points), 4) for i in range(3)], [round(max(p[i] for p in points), 4) for i in range(3)]] if points else None


def _reach(objects, depsgraph):
    """The most the part's thinnest side can show at on any screen, in metres: hypot of its two shortest extents along
    its principal axes (exposure_core.reach_m's rule on its oriented box - a slanted strut is as thin as it is, not as
    its world box); None when nothing renders."""
    import numpy as np
    points = []
    for o in objects:
        if o.type != 'MESH' or o.hide_render:
            continue
        ev = o.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            points += [tuple(ev.matrix_world @ v.co) for v in mesh.vertices]
        finally:
            ev.to_mesh_clear()
    if len(points) < 2:
        return None
    cloud = np.array(points)
    cloud -= cloud.mean(axis=0)
    _u, _s, axes = np.linalg.svd(cloud, full_matrices=False)
    extents = sorted(float(np.ptp(cloud @ axis)) for axis in axes)
    while len(extents) < 3:   # flat or straight: no extent along the missing axes
        extents.insert(0, 0.0)
    return round(exposure_core.reach_m(([0.0, 0.0, 0.0], extents)), 4)


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
        try:
            mine = lambda o, part_id=part_id: _part_of(o) == part_id   # a child part (a foot under its leg) is another part
            points = [Vector(p) for p in camera_frame.sample([ref], depsgraph, count=samples, keep=mine)]
        except ValueError:   # nothing that renders: nothing to show
            parts[part_id] = {'best_share': None, 'best_dir': None, 'hidden_by': [], 'per_direction': None, 'exposable_share': None,
                              'enclosed_by': {}, 'box': None, 'reach_m': None}
            continue
        own = {o.name for o in camera_frame.named_objects(ref) if _part_of(o) == part_id}   # a grouped part: every copy
        # a layer declared a coat of this part, or of the part this one coats (soot shells stacked on one body), is said
        surface = {ref} | {coats.coat_of(bpy.data.objects[n]) for n in own} - {None}
        shares, stoppers, met, ever, coated = [], {}, {}, [False] * len(points), {}
        for d in dirs:
            seen = 0
            for k, target in enumerate(points):
                start = target + d * reach
                direction = (target - start).normalized()
                left = (target - start).length * 1.001
                first, layers = None, set()   # the first other part that stops the ray; every other part it crosses on the way
                for _ in range(camera_frame.RAY_MAX_HOPS):
                    hit, location, _n, _i, obj, _m = scene.ray_cast(depsgraph, start, direction, distance=left)
                    if not hit or obj is None or (location - target).length <= coat:
                        ever[k] = ever[k] or first is None
                        break
                    if not camera_frame.passes_through(obj, scene.frame_current):
                        layer = obj.name not in own and first is None and _coats(scene, depsgraph, location, direction, coat, own)
                        if layer and coats.coat_of(obj) not in surface:   # lies on the part like a coat, not declared one (coats.py)
                            other = _part_of(obj) or obj.name
                            coated[other] = coated.get(other, 0) + 1
                        if obj.name in own or layer:
                            # behind the part's own near side (or a coat on it): it shows, the point itself does not
                            ever[k] = ever[k] or (first is None and (location - target).length <= 2.0 * coat)
                            break
                        other = _part_of(obj) or obj.name
                        first = first or other
                        layers.add(other)
                    step = (location - start).length + 1e-4
                    start, left = start + direction * step, left - step
                else:   # RAY_MAX_HOPS ran out (a broken scene): not seen, as before
                    first = first or '?'
                if first is None:
                    seen += 1
                elif first != '?':
                    stoppers[first] = stoppers.get(first, 0) + 1
                if layers:   # a part in front that is behind the point too, on the same line, wraps it along this ray
                    for other in layers & _behind(scene, depsgraph, target, direction, reach, own):
                        met[other] = met.get(other, 0) + 1
            shares.append(round(seen / len(points), 4))
        best = max(range(len(dirs)), key=lambda i: (shares[i], -i))   # the first of equal bests, as before
        rays = len(dirs) * len(points)
        parts[part_id] = {'best_share': shares[best], 'best_dir': [round(c, 4) for c in dirs[best]],
                          'hidden_by': [p for p, _n in sorted(stoppers.items(), key=lambda kv: -kv[1])[:3]],
                          'per_direction': shares, 'exposable_share': round(sum(ever) / len(points), 4),
                          'enclosed_by': {p: round(n / rays, 4) for p, n in sorted(met.items(), key=lambda kv: -kv[1])},
                          # parts lying on it like a coat without saying so (share of rays): COAT_UNTAGGED on the host
                          'coated_by': {p: round(n / rays, 4) for p, n in sorted(coated.items(), key=lambda kv: -kv[1])},
                          'box': _box([o for o in camera_frame.named_objects(ref) if _part_of(o) == part_id], depsgraph),
                          'reach_m': _reach([o for o in camera_frame.named_objects(ref) if _part_of(o) == part_id], depsgraph)}
    return {'threshold': camera_frame.HIDDEN_SHARE, 'directions': directions, 'samples': samples, 'parts': parts}


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    job = json.loads(Path(args[0]).read_text())
    import contrib_loader
    contrib_loader.TABLE.update(job.get('contrib') or {})   # resolved and hash-checked on the host (subjects.exposure)
    result = survey(job['spec'], job.get('directions', exposure_core.DIRECTIONS), job.get('samples', exposure_core.SAMPLES))
    Path(args[1]).write_text(json.dumps(result, indent=2, sort_keys=True))
