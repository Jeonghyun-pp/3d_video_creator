"""Environment fill functions (any production, any kit): put verified parts along paths, on lots, on roofs, in lanes.

  along(name, sources, path, pitch_m, ...)    streetlights, trees, bollards, sign panels, lane dashes
  blocks(name, sources, path, side, ...)      buildings on lots along a frontage (instances, per-axis scaled)
  on_top(name, sources, lots, ...)            rooftop equipment on the lots `blocks` returned
  traffic(name, sources, lanes, frames, ...)  vehicles per lane, keyed at constant speed, spacing kept

Sources are objects (often an exemplar's root, see `source`) modelled with travel/front along local +Y and their base
at z = 0. Everything is an instance (scatter / collection instance): one mesh per part however many copies, seen by
every pass through scene_geometry. Placement maths lives in env_fill_core (unit-tested); seeds are per generator.
"""
from __future__ import annotations

import json
import math

import bpy

import env_fill_core as core
from scatter import scatter

ROLE = 'studio_scene_role'


def source(exemplar, library_root, *, edit=None, name=None):
    """Build an exemplar (library/exemplars/<id>, latest) once, as a scatter source. Returns its root object."""
    from pathlib import Path
    from modeling import build_subject
    lib = Path(library_root) / 'exemplars' / exemplar
    spec = json.loads((sorted(lib.glob('v*'))[-1] / 'spec.json').read_text())
    spec['subject_id'] = name or f'{exemplar.replace("_", "-")}-src'
    if edit:
        edit(spec)
    return build_subject(spec)['root']


def _face(heading, offset_m, face):
    if face == 'road':   # local +X points at the path: right of travel for left-side copies, left for right-side ones
        return heading - math.pi / 2 if offset_m >= 0 else heading + math.pi / 2
    return heading - math.pi / 2   # 'along': local +Y follows the travel direction


def along(name, sources, path, pitch_m, *, offset_m=0.0, both_sides=False, start_m=0.0, jitter_m=0.0, weights=None,
          face='along', seed=0, scale=1.0, realize=False, role=None, avoid=(), avoid_margin_m=0.0, rows_out=None):
    """realize + role: for markings that must behave like surface (a reveal cuts realized `clutter`, never instances).
    avoid: arc-length ranges left empty (openings, crossings). rows_out: a list that receives (point, rot_z) of every
    copy (spot lights over lamp heads, keep-out for pedestrians)."""
    rows, sides = [], []
    for side, tag in ((offset_m, name), (-offset_m, name + ':other')) if both_sides else ((offset_m, name),):
        part = core.along(path, pitch_m, offset_m=side, start_m=start_m, jitter_m=jitter_m, seed=seed, name=tag,
                          avoid=avoid, avoid_margin_m=avoid_margin_m)
        rows += part
        sides += [side] * len(part)
    if not rows:
        return None
    rot = [_face(h, side, face) for (_, h), side in zip(rows, sides)]
    if rows_out is not None:
        rows_out += [(p, r) for (p, _), r in zip(rows, rot)]
    host = scatter(name, sources, points=[p for p, _ in rows], attributes={'rot_z': rot, 'scale': [scale] * len(rows)},
                   weights=weights, seed=seed, realize=realize)
    if role:
        host[ROLE] = role
    return host


def at_positions(name, sources, placements, *, weights=None, seed=0, scale=1.0, realize=False, role=None):
    """Copies at explicit (point, rot_z) placements (signals at an intersection, people on a corner)."""
    if not placements:
        return None
    host = scatter(name, sources, points=[p for p, _ in placements], attributes={'rot_z': [r for _, r in placements],
                   'scale': [scale] * len(placements)}, weights=weights, seed=seed, realize=realize)
    if role:
        host[ROLE] = role
    return host


def blocks(name, sources, path, *, side, base_size, lot_width_range_m, depth_range_m, height_range_m, gap_range_m=(4, 14),
           setback_m=10.0, weights=None, avoid=(), seed=0, sightline=None, min_height_m=6.0):
    """Lots along `path` on `side` ('left'|'right'), one building per lot: a source scaled to the lot (base_size =
    the source's (width_x, depth_y, height_z) in metres). Returns (host, lots) - lots carry the roof rectangles.
    sightline: env_fill_core.sightline_cap rule - buildings in an establishing camera's view stay under its sky line."""
    lots = core.lots(path, lot_width_range_m=lot_width_range_m, gap_range_m=gap_range_m, depth_range_m=depth_range_m,
                     setback_m=setback_m, side=side, seed=seed, name=name, avoid=avoid)
    if not lots:
        return None, []
    r = core.rng(seed, name, 'height')
    for lot in lots:
        lot['height'] = r.uniform(*height_range_m)
        cap = core.sightline_cap(lot, sightline) if sightline else None
        if cap is not None and lot['height'] > cap:   # the sky rule: lower the buildings the establishing frame sees
            lot['capped_from'] = lot['height']
            lot['height'] = max(min_height_m, cap)
    w0, d0, h0 = base_size
    attrs = {'rot_z': [lot['heading'] - math.pi / 2 for lot in lots],
             'scale_xyz': [(lot['width'] / w0, lot['depth'] / d0, lot['height'] / h0) for lot in lots]}
    host = scatter(name, sources, points=[lot['centre'] for lot in lots], attributes=attrs, weights=weights, seed=seed)
    return host, lots


def on_top(name, sources, lots, *, spacing_m=9.0, margin_m=3.0, keep_ratio=0.5, seed=0, scale=(0.9, 1.2)):
    """Rooftop equipment: a jittered grid on each lot's roof (lot frame), `keep_ratio` of the cells used."""
    r = core.rng(seed, name, 'keep')
    points, rot, sizes = [], [], []
    for index, lot in enumerate(lots):
        local = core.grid_points((-lot['width'] / 2, -lot['depth'] / 2, lot['width'] / 2, lot['depth'] / 2, lot['height']),
                                 spacing_m, margin_m=margin_m, jitter_m=spacing_m * 0.2, seed=seed, name=f'{name}:{index}')
        c, s = math.cos(lot['heading'] - math.pi / 2), math.sin(lot['heading'] - math.pi / 2)
        for x, y, z in local:
            if r.random() < keep_ratio:
                points.append((lot['centre'][0] + x * c - y * s, lot['centre'][1] + x * s + y * c, z))
                rot.append(lot['heading'] - math.pi / 2 + r.choice((0, math.pi / 2)))
                sizes.append(r.uniform(*scale))
    if not points:
        return None
    return scatter(name, sources, points=points, attributes={'rot_z': rot, 'scale': sizes}, seed=seed)


def on_front(name, sources, lots, *, per_lot=(0, 2), z_range=(4.5, 7.5), standoff_m=0.15, side_sign=1, weights=None, seed=0):
    """Things fixed to each lot's street face (signs, canopies): 0-2 per lot at a height in z_range, facing the street.
    side_sign: +1 for lots on the left of the path, -1 on the right (the street face is towards the path)."""
    r = core.rng(seed, name, 'front')
    points, rot = [], []
    for lot in lots:
        for _ in range(r.randint(*per_lot)):
            along_lot = r.uniform(-0.35, 0.35) * lot['width']
            out = -(lot['depth'] / 2 + standoff_m) * side_sign   # from the lot centre back towards the street
            h = lot['heading']
            x = lot['centre'][0] + math.cos(h) * along_lot - math.sin(h) * out
            y = lot['centre'][1] + math.sin(h) * along_lot + math.cos(h) * out
            points.append((x, y, r.uniform(*z_range)))
            rot.append(h - math.pi / 2 if side_sign > 0 else h + math.pi / 2)   # local +X faces the street
    if not points:
        return None
    return scatter(name, sources, points=points, attributes={'rot_z': rot}, weights=weights, seed=seed)


def traffic(name, sources, lanes, frames, *, per_100m=4.0, speed_mps_range=(9.0, 14.0), min_gap_m=10.0, fps=30, weights=None, seed=0, avoid=()):
    """One collection-instance empty per vehicle, keyed linearly from frames[0] to frames[1] (no wheel spin)."""
    from scatter import _collect
    group = _collect(name, sources)   # sources leave the scene; each child collection is one vehicle kind
    kinds = list(group.children)
    duration = (frames[1] - frames[0]) / fps
    cars = core.traffic(lanes, per_100m=per_100m, speed_mps_range=speed_mps_range, duration_s=duration, min_gap_m=min_gap_m,
                        seed=seed, name=name, avoid=avoid)
    pick = core.rng(seed, name, 'kind')
    root = bpy.data.objects.new(f'traffic.{name}', None)
    bpy.context.scene.collection.objects.link(root)
    root['studio_id'] = f'traffic.{name}'
    for i, car in enumerate(cars):
        empty = bpy.data.objects.new(f'traffic.{name}.{i:03d}', None)
        empty.instance_type = 'COLLECTION'
        empty.instance_collection = pick.choices(kinds, weights=weights)[0] if weights else kinds[pick.randrange(len(kinds))]
        bpy.context.scene.collection.objects.link(empty)
        empty.parent = root
        empty['studio_id'] = empty.name
        empty.rotation_euler = (0, 0, car['heading'] - math.pi / 2)
        for frame, point in ((frames[0], car['start']), (frames[1], car['end'])):
            empty.location = point
            empty.keyframe_insert('location', frame=frame)
        if empty.animation_data and empty.animation_data.action:
            from scene_tools import curves
            for curve in curves(empty.animation_data.action):
                for key in curve.keyframe_points:
                    key.interpolation = 'LINEAR'
    root['studio_traffic'] = json.dumps({'vehicles': len(cars), 'lanes': len(lanes), 'seed': seed}, sort_keys=True)
    return root, cars
