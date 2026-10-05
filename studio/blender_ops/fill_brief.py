"""Fill briefs in Blender: place what shot.fill_brief asks for on the levels the author declared, then check the result
against the camera.

Authors declare levels (any multi-level structure: a station, a car park, a building's floors) with
`declare_levels([{level_id, z, rects: [[x0, y0, x1, y1]], obstacles: [[x, y, r]]}])` - the long axis of each rect runs
from its near end (toward the section face) to its far end. Nothing here knows a place: the brief says which elements
(verified exemplars, or project specs) go where and why (studio/fill.py); the layouts are env_fill_core.level_layout.
Every copy is an instance on a host tagged studio_fill_level / studio_fill_role / studio_fill_item.

check(): on the baked camera, a level the camera sees must carry a subject or identity item unless the brief declares it
void (FILL_LEVEL_EMPTY); identity/ambient copies may not sit in front of most subject copies (FILL_SUBJECT_HIDDEN);
every fill host must trace to a brief item (FILL_OFF_BRIEF). Results in fill_report.json.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

import env_fill
import env_fill_core as core

LEVELS_KEY = 'studio_levels'
REPORT_KEY = 'studio_environment'
SAMPLE_EVERY = 6            # frames between visibility samples
VISIBLE_FRAMES = 3          # a level counts as seen after this many sampled frames
POINTS_SEEN = 2             # ... with at least this many of its probe points unoccluded
HIDDEN_SHARE = 0.4          # a subject copy is hidden when an identity/ambient copy covers it this often


def declare_levels(levels):
    """Author API: the levels of the structure, as data the build fills from the brief."""
    for level in levels:
        if not {'level_id', 'z', 'rects'} <= set(level):
            raise ValueError(f'FILL: a level needs level_id, z and rects: {level}')
    bpy.context.scene[LEVELS_KEY] = json.dumps(levels)


def declared_levels():
    raw = bpy.context.scene.get(LEVELS_KEY)
    return json.loads(raw) if raw else []


def _elements(element, library_root):
    lib = Path(library_root) / 'exemplars'
    if '*' in element:
        return sorted(fnmatch.filter([p.name for p in lib.iterdir() if p.is_dir()], element))
    return [element]


def _source(element, library_root, job, cache):
    if element in cache:
        return cache[element]
    if element.startswith('subject:'):
        from modeling import build_subject
        sid = element.split(':', 1)[1]
        spec_path = (job.get('subject_spec_paths') or {}).get(sid) or str(Path(job['project_dir']) / 'subjects' / sid / 'spec.json')
        spec = json.loads(Path(spec_path).read_text())
        spec['subject_id'] = f'fill-{sid}'
        cache[element] = [build_subject(spec)['root']]
    else:
        cache[element] = [env_fill.source(e, library_root, name=f'fill-{e}') for e in _elements(element, library_root)]
    if not cache[element]:
        raise ValueError(f'FILL: element {element!r} is not in the library (fill propose lists what must be modelled)')
    return cache[element]


def apply(job, seed=0):
    """Place the brief's items on the declared levels. Returns the report (also appended to scene[REPORT_KEY])."""
    brief = job['shot'].get('fill_brief')
    levels = {lv['level_id']: lv for lv in declared_levels()}
    if not brief or not levels:
        return None
    cache, placements, hosts, counts = {}, [], [], {}
    for level in brief['levels']:
        if level['level_id'] not in levels:
            raise ValueError(f"FILL: brief level {level['level_id']} is not declared by the author (declared: {sorted(levels)})")
        geo = levels[level['level_id']]
        obstacles = [tuple(o) for o in geo.get('obstacles', [])]
        centre = (sum((r[0] + r[2]) / 2 for r in geo['rects']) / len(geo['rects']), sum((r[1] + r[3]) / 2 for r in geo['rects']) / len(geo['rects']))
        for item in level['items']:
            sources = _source(item['element'], job['library_root'], job, cache)
            rows = []
            for k, rect in enumerate(geo['rects']):
                for x, y, rot in core.level_layout(tuple(rect), item, obstacles=obstacles, seed=seed, name=f"{level['level_id']}:{k}", centre=centre):
                    rows.append(((x, y, geo['z'] + item.get('height_m', 0.0)), rot))
            counts.setdefault(level['level_id'], {}).setdefault(item['role'], 0)
            counts[level['level_id']][item['role']] += len(rows)
            if not rows:
                continue
            host = env_fill.at_positions(f"fill.{level['level_id']}.{item['item_id']}", sources, rows, seed=seed)
            host['studio_fill_level'], host['studio_fill_role'], host['studio_fill_item'] = level['level_id'], item['role'], item['item_id']
            hosts.append(host.name)
            dims = max((_extent(s) for s in sources), key=lambda d: d[2])
            placements += [{'level': level['level_id'], 'role': item['role'], 'item': item['item_id'], 'point': list(p), 'size': dims} for p, _ in rows]
    digest = hashlib.sha256(json.dumps([(p['item'], [round(c, 3) for c in p['point']]) for p in placements]).encode()).hexdigest()[:16]
    report = {'kind': 'fill', 'status': brief['status'], 'counts': counts, 'hosts': hosts, 'digest': digest}
    scene = bpy.context.scene
    scene[REPORT_KEY] = json.dumps(json.loads(scene.get(REPORT_KEY, '[]')) + [report], sort_keys=True)
    scene['studio_fill_placements'] = json.dumps(placements)
    return report


def _extent(root):
    corners = [o.matrix_world @ Vector(c) for o in [root, *root.children_recursive] if o.type == 'MESH' for c in o.bound_box]
    if not corners:
        return [0.5, 0.5, 1.7]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    return [round(hi[i] - lo[i], 3) for i in range(3)]


def _probe_points(geo):
    out = []
    for x0, y0, x1, y1 in geo['rects']:
        for fx in (0.25, 0.75):
            for fy in (0.05, 0.25, 0.5, 0.75, 0.95):
                out.append(Vector((x0 + (x1 - x0) * fx, y0 + (y1 - y0) * fy, geo['z'] + 1.5)))
    return out


def _seen(scene, depsgraph, camera, point):
    from bpy_extras.object_utils import world_to_camera_view
    from scene_roles import first_blocking_hit
    ndc = world_to_camera_view(scene, camera, point)
    if ndc.z <= 0 or not (0 <= ndc.x <= 1 and 0 <= ndc.y <= 1):
        return None
    eye = camera.matrix_world.translation
    d = point - eye
    hit = first_blocking_hit(scene, depsgraph, eye, d.normalized(), d.length - 0.3)
    return None if hit[0] else ndc


def check(job, output):
    """Gate on the baked camera. Writes fill_report.json; raises ValueError('FILL_...') on failure."""
    brief = job['shot'].get('fill_brief')
    levels = {lv['level_id']: lv for lv in declared_levels()}
    scene = bpy.context.scene
    if not brief or not levels or scene.camera is None:
        return None
    placements = json.loads(scene.get('studio_fill_placements', '[]'))
    by_id = {lv['level_id']: lv for lv in brief['levels']}
    seen_frames = {lid: 0 for lid in levels}
    covered, covers = {}, {}
    count = job['shot']['duration_frames']
    camera = scene.camera
    for f in range(0, count, SAMPLE_EVERY):
        scene.frame_set(f + 1)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for lid, geo in levels.items():
            if sum(1 for p in _probe_points(geo) if _seen(scene, depsgraph, camera, p)) >= POINTS_SEEN:
                seen_frames[lid] += 1
        eye = camera.matrix_world.translation
        from bpy_extras.object_utils import world_to_camera_view
        screen = []
        for p in placements:
            point = Vector(p['point']) + Vector((0, 0, p['size'][2] / 2))
            ndc = world_to_camera_view(scene, camera, point)
            if ndc.z > 0 and 0 <= ndc.x <= 1 and 0 <= ndc.y <= 1:
                screen.append((p, ndc))
        others = [(p, n) for p, n in screen if p['role'] != 'subject']
        frame = [Vector(c) for c in camera.data.view_frame(scene=scene)]          # camera-local frame at the sensor distance
        depth = abs(frame[0].z)
        half_w = (max(c.x for c in frame) - min(c.x for c in frame)) / 2 / depth   # tan of the half angles (shift ignored)
        half_h = (max(c.y for c in frame) - min(c.y for c in frame)) / 2 / depth
        for p, n in screen:
            if p['role'] != 'subject':
                continue
            key = (p['item'], tuple(p['point']))
            hits = covered.setdefault(key, [0, 0])
            hits[1] += 1
            # covered: a nearer identity/ambient copy whose projected box contains the subject's centre
            cover = next((o for o, o_n in others if o_n.z < n.z - 0.5 and abs(o_n.x - n.x) < max(o['size'][0], o['size'][1]) / 2 / (2 * o_n.z * half_w)
                          and abs(o_n.y - n.y) < o['size'][2] / 2 / (2 * o_n.z * half_h)), None)
            if cover:
                hits[0] += 1
                covers.setdefault(key, set()).add(cover['item'])
    scene.frame_set(1)
    failures = []
    seen = sorted(lid for lid, n in seen_frames.items() if n >= VISIBLE_FRAMES)
    for lid in seen:
        level = by_id.get(lid)
        if level and level.get('void'):
            continue
        carried = [p for p in placements if p['level'] == lid and p['role'] in ('subject', 'identity')]
        if not carried:
            failures.append({'gate': 'FILL_LEVEL_EMPTY', 'level': lid, 'frames_seen': seen_frames[lid] * SAMPLE_EVERY,
                             'detail': 'the camera sees this level and nothing the brief says it should explain or identify is on it'})
    hidden = [k for k, (c, n) in covered.items() if n and c / n > HIDDEN_SHARE]
    subjects = [k for k in covered]
    if subjects and len(hidden) > 0.3 * len(subjects):
        failures.append({'gate': 'FILL_SUBJECT_HIDDEN', 'hidden_copies': len(hidden), 'subject_copies': len(subjects),
                         'covered_by': sorted({i for k in hidden for i in covers.get(k, ())})})
    items = {(lv['level_id'], i['item_id']) for lv in brief['levels'] for i in lv['items']}
    stray = sorted(o.name for o in scene.objects if o.get('studio_fill_item') and (o.get('studio_fill_level'), o.get('studio_fill_item')) not in items)
    if stray:
        failures.append({'gate': 'FILL_OFF_BRIEF', 'hosts': stray[:10]})
    report = {'schema_version': 1, 'status': brief['status'], 'levels_seen': seen, 'frames_seen': seen_frames,
              'counts': {lid: {r: sum(1 for p in placements if p['level'] == lid and p['role'] == r) for r in ('subject', 'identity', 'ambient')}
                         for lid in levels},
              'gate_failures': failures, 'warnings': ([] if brief['status'] == 'approved' else
                                                      ['FILL_BRIEF_UNAPPROVED: built from a proposed brief; renders wait for the user\'s approval'])}
    (Path(output) / 'fill_report.json').write_text(json.dumps(report, indent=1))
    if failures:
        raise ValueError(f"{failures[0]['gate']}: " + json.dumps(failures[:5], ensure_ascii=False))
    return report
