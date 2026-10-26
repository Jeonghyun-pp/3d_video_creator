"""Blender side of a dive through scales (handover scale_through; the math is scale_through_core).

At build time a shot is at a dive's end when it hands over scale_through itself (its 'out' end: the camera keeps its
cruise to the last frame) or when the shot before it in the project does (its 'in' end: the camera starts where that
shot left the thing it pushed into - same place on screen, same size - at the rate it left it, read from that shot's
built scale_join.json). The move must close in on its target along its line of sight (an approach: fly_to); any other
move at a dive's end is refused (SCALE_THROUGH_UNMADE) rather than joined by chance. Blender is imported where the
scene is read: a shot at no dive's end (every compile_move) reads only files.

After the build, scale_join.json holds the carried things on the finished camera: the head (the thing handed in, over
the first JOIN_S) and the tail (the shot's own target, over the last JOIN_S), [frame, cx, cy, height share] each - what
the next shot's build starts from and the brief's SCALE_JOIN_OFF reads. The two are cut together on the matched frame.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import camera_frame_core as frame_core
import camera_moves_core as core
import scale_through_core as st

KIND = 'scale_through'
FILE = 'scale_join.json'
POINTS = 400   # surface points the carried thing is measured on (camera_frame.sample), the same in both scenes


def _read(path):
    return json.loads(Path(path).read_text())


def _kind(shot):
    return ((shot or {}).get('handover') or {}).get('kind')


def incoming(job):
    """The dive handed into this shot by the one before it in the project, or None: {'shot_id', 'id' (the carried
    thing), 'rate', 'share', 'centre'} at that shot's last frame. Raises SCALE_THROUGH_UNBUILT when the shot before
    dives but has no built measure to start from."""
    root = Path(job.get('project_dir') or '')
    if not (root / 'project.json').is_file():
        return None
    order = [s['shot_id'] for s in _read(root / 'project.json').get('shots') or []]
    if job['shot_id'] not in order or order.index(job['shot_id']) == 0:
        return None
    before = order[order.index(job['shot_id']) - 1]
    shot = _read(root / 'shots' / before / 'shot.json')
    if _kind(shot) != KIND:
        return None
    measured = root / 'shots' / before / 'versions' / str(shot.get('scene_version')) / FILE
    if not shot.get('scene_version') or not measured.is_file():
        raise ValueError(f'SCALE_THROUGH_UNBUILT: {before} dives into {job["shot_id"]} (handover scale_through) and has no built '
                         f'{FILE} to start from - build {before} first')
    data = _read(measured)
    tail = data.get('tail') or {}
    end = st.state(tail.get('samples'), data.get('fps') or job['fps'], 'end')
    if not end or end['rate'] is None:
        raise ValueError(f'SCALE_THROUGH_UNBUILT: {before} measured no carried thing at its end ({measured})')
    return {'shot_id': before, 'id': tail['id'], 'rate': end['rate'], 'share': end['share'], 'centre': end['centre']}


def ends(job):
    """{'out': bool, 'in': incoming(job) or None}, or None when the shot is at no dive's end."""
    found = {'out': _kind(job['shot']) == KIND, 'in': incoming(job)}
    return found if found['out'] or found['in'] else None


def _aspect(job):
    width, height = job.get('output_size') or (1080, 1920)
    return width / height


def _points(ref):
    import bpy
    import camera_frame
    return camera_frame.sample([ref], bpy.context.evaluated_depsgraph_get(), count=POINTS)


def _share(points, eye, aim, lens_mm, aspect):
    """Height share of points seen from eye looking at aim (unclipped, as world_to_camera_view)."""
    basis = frame_core.basis(*frame_core.view_of(eye, aim))
    seen, _behind = frame_core.project(points, eye, basis, frame_core.tangents(lens_mm, aspect))
    return (max(y for _x, y, _z in seen) - min(y for _x, y, _z in seen)) if seen else 0.0


def prepare(job, move, geo, plan, found):
    """(move, plan) with a received dive's start: the camera starts on its own line of sight where the carried thing
    stands as tall as the shot before left it (fly_to's above_m set to that reach). Unchanged for an 'out' end only."""
    if move['type'] not in core.LANDING_MOVES or not (plan.get('notes') or {}).get('approach'):
        raise ValueError(f"SCALE_THROUGH_UNMADE: {job['shot_id']} is at a dive's end (handover scale_through) with a {move['type']} move - "
                         'a dive closes in on its target along its line of sight: use a move that does (recipe fly_in)')
    if not found.get('in'):
        return move, plan
    handed = found['in']
    import bpy
    bpy.context.scene.frame_set(1)
    try:
        points = _points(handed['id'])
    except ValueError:
        raise ValueError(f"SCALE_JOIN_OFF: {job['shot_id']} is the inside of {handed['id']} ({handed['shot_id']} dives into it) and its "
                         f"scene has no {handed['id']} - build the thing the dive pushes into at this scale, named alike")
    p = move['params']
    aim = core.centre(geo['boxes'][p['target']]) if p['target'] in geo['boxes'] else geo['points'][p['target']]
    ray = core._dir(p.get('azimuth_deg', core.PARAMS['fly_to']['azimuth_deg']), p.get('elevation_deg', core.PARAMS['fly_to']['elevation_deg']))
    lens, aspect = move.get('lens_mm', 24.0), _aspect(job)
    land = plan['notes']['land_m']
    handed_share = handed['share'] * math.exp(handed['rate'] / job['fps'])   # one frame on from where it was left
    reach = st.reach_for_share(lambda d: _share(points, core._add(aim, core._mul(ray, d)), aim, lens, aspect), handed_share,
                               land, land * 4)
    first = [r for r in p['places'] if r != p['target']]
    top = (geo['boxes'][first[0]] if first and first[0] in geo['boxes'] else geo['boxes'].get(p['target'], ((0, 0, aim[2]),) * 2))[1][2]
    moved = {**move, 'params': {**p, 'above_m': round(reach * ray[2] + aim[2] - top, 6)}}
    return moved, core.plan(moved, geo)


def timing(job, found, plan, timing_spec, rig_core):
    """(timing, notes): the move's timing at a dive's end (scale_through_core.through_curve, in log distance)."""
    count, fps = job['shot']['duration_frames'], job['fps']
    seconds = (count - 1) / fps
    approach = plan['notes']['approach']
    span = math.log(approach['from_m'] / approach['to_m'])
    settle, ramp = st.curve_shape(rig_core.timing_curve(timing_spec))
    start = st.start_speed(found['in']['rate'], seconds, span) if found.get('in') else 0.0
    g, cruise = st.through_curve(start, settle, ramp, found['out'], seconds)
    notes = {'out': found['out'], 'in': found.get('in'), 'start_speed': round(start, 4), 'cruise': round(cruise, 4),
             'span_ln': round(span, 4), 'director_settle_at': settle, 'ramp': ramp,
             **({'hands_over_rate': round(st.end_rate(cruise, span, seconds), 4),
                 'settle_not_read': 'the camera keeps its cruise to the last frame: a dive does not arrive'} if found['out'] else {})}
    return core.approach_timing(g, approach['from_m'], approach['to_m'], samples=max(core.APPROACH_SAMPLES, count)), notes


def _samples(ref, frames, aspect):
    import bpy
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector
    scene = bpy.context.scene
    out = []
    for f in frames:
        scene.frame_set(f + 1)
        try:
            pts = _points(ref)
        except ValueError:
            return []
        seen = [world_to_camera_view(scene, scene.camera, Vector(p)) for p in pts]
        xy = [(v.x, 1.0 - v.y) for v in seen if v.z > 0]
        if not xy:
            continue
        x0, x1 = min(x for x, _ in xy), max(x for x, _ in xy)
        y0, y1 = min(y for _, y in xy), max(y for _, y in xy)
        out.append([f, round((x0 + x1) / 2, 5), round((y0 + y1) / 2, 5), round(y1 - y0, 6)])
    return out


def write(job, output, notes):
    """scale_join.json on the finished camera (see module)."""
    count, fps = job['shot']['duration_frames'], job['fps']
    n = max(2, round(st.JOIN_S * fps) + 1)
    aspect = _aspect(job)
    import bpy
    data = {'schema_version': 1, 'fps': fps, 'aspect': aspect, 'frames': count}
    if notes.get('in'):
        data['head'] = {'id': notes['in']['id'], 'from_shot': notes['in']['shot_id'], 'samples': _samples(notes['in']['id'], range(min(n, count)), aspect)}
    if notes.get('out'):
        target = (job['shot']['camera'].get('move') or {}).get('params', {}).get('target')
        data['tail'] = {'id': target, 'samples': _samples(target, range(max(0, count - n), count), aspect)}
    bpy.context.scene.frame_set(1)
    (Path(output) / FILE).write_text(json.dumps(data, indent=1))
    return data
