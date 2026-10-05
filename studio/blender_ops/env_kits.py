"""Environment kits composed: street(...) lays a whole urban street from the verified city-kit exemplars with the fill
functions - road, lane dashes, sidewalks, streetlights, trees, building lots, rooftop plant, lit signs and traffic.

Any production calls it with a path and a density preset (env_kits_data/presets.json); nothing here knows a project.
Night/day changes only emission data (lit windows, lamp heads, car lights), never placement, so a day and a night shot
of the same street match. Returns a report (also written per version as environment_report.json by build_scene).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import bpy

import env_fill
import env_fill_core as core

ROLE = 'studio_scene_role'
PRESETS = Path(__file__).parent / 'env_kits_data' / 'presets.json'
TOWER_BASE = (24.0, 24.0, 61.2)   # tower_block exemplar footprint and height (fidelity-checked dims)
REPORT_KEY = 'studio_environment'


def presets():
    return {k: v for k, v in json.loads(PRESETS.read_text()).items() if not k.startswith('_')}


def day_facade():
    return {k: v for k, v in json.loads(PRESETS.read_text())['_day'].items() if not k.startswith('_')}


def _material(name, srgb, roughness=0.9):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    lin = tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)
    bsdf.inputs['Base Color'].default_value = (*lin, 1.0)
    bsdf.inputs['Roughness'].default_value = roughness
    mat.diffuse_color = (*lin, 1.0)
    return mat


def _strip(name, path, width, z, thickness, material, lateral=0.0, avoid=()):
    """Boxes along each path segment (road, sidewalk), skipping `avoid` arc-length ranges. Faces wind outward
    (positive signed volume): a reveal's MANIFOLD boolean corrupts an inside-out target (measured: a seam line)."""
    objects = []
    s0 = 0.0
    for a, b in zip(path, path[1:]):
        seg = math.dist(a[:2], b[:2])
        heading = math.atan2(b[1] - a[1], b[0] - a[0])
        pieces = [(s0, s0 + seg)]
        for x0, x1 in avoid:
            pieces = [p for q in pieces for p in ((q[0], min(q[1], x0)), (max(q[0], x1), q[1])) if p[1] - p[0] > 0.01]
        for i, (u0, u1) in enumerate(pieces):
            mid, _ = core.at(path, (u0 + u1) / 2)
            centre = core.offset_point(mid, heading, lateral)
            mesh = bpy.data.meshes.new(f'{name}.{len(objects)}')
            hw, hl = width / 2, (u1 - u0) / 2
            mesh.from_pydata([(x, y, zz) for zz in (-thickness, 0) for x, y in ((-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw))], [],
                             [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
            mesh.materials.append(material)
            obj = bpy.data.objects.new(f'{name}.{len(objects)}', mesh)
            obj.location = (centre[0], centre[1], z)
            obj.rotation_euler = (0, 0, heading)
            obj['studio_id'] = obj.name
            obj['studio_dim_role'] = 'none'
            bpy.context.scene.collection.objects.link(obj)
            objects.append(obj)
        s0 += seg
    return objects


def street(name, path, *, library_root, road_w_m=24.0, lanes_per_direction=3, sidewalk_w_m=6.0, night=True, density='urban_dense',
           avoid=(), keep_clear=(), frames=(1, 120), fps=30, seed=0, both_sides=True, overrides=None, lane_w_m=None, median_w_m=None,
           intersections=(), sightline=None, bare=()):
    """Lay an urban street along `path` ([(x, y, z)], travel +path direction on the right-hand lanes).
    avoid: [(s0, s1)] arc-length ranges with no road surface or buildings (an opening the camera dives through).
    keep_clear: ranges no vehicle crosses although the road is there (an opening a reveal cuts during the shot).
    lane_w_m / median_w_m: real lane geometry (default from the preset); width the lanes do not use is shoulder.
    intersections: [{'s': arc length, 'cross': {...}}] four-way crossings (env_fill_core.intersection_layout).
    sightline: {'camera': [x, y, z], 'forward': [dx, dy], 'horizon_v', 'keep_sky_v', 'lens_mm'} - buildings the
    establishing camera sees stay below keep_sky_v (env_fill_core.sightline_cap), so its frame has sky above the street.
    bare: ranges with road and buildings but no lamps, trees, people or shelters (ground a section cut removes).
    Returns {counts, hosts, seed, density} and tags the scene with it."""
    preset = {**presets()[density], **(overrides or {})}
    import hashlib
    seed_of = lambda part: int(hashlib.sha256(f'{seed}:{name}:{part}'.encode()).hexdigest()[:7], 16)   # per generator, stable
    report = {'name': name, 'density': density, 'night': night, 'seed': seed, 'hosts': {}, 'counts': {}}
    half = road_w_m / 2
    lanes_n = lanes_per_direction
    median = preset.get('median_w_m', 0.0) if median_w_m is None else median_w_m
    lane_w = lane_w_m or preset.get('lane_w_m') or (road_w_m - median) / (2 * lanes_n)
    if half - median / 2 - lanes_n * lane_w < -1e-6:
        if lane_w_m:
            raise ValueError(f'ENV_KIT: {lanes_n} lanes of {lane_w_m} m and a {median} m median do not fit a {road_w_m} m road')
        lane_w = (road_w_m - median) / (2 * lanes_n)   # a preset lane width never forces a road wider than asked
    shoulder = half - median / 2 - lanes_n * lane_w
    main = {'lane_w': lane_w, 'lanes': lanes_n, 'median_w': median, 'shoulder_w': shoulder, 'sidewalk_w': sidewalk_w_m}
    report['geometry'] = {k: round(v, 3) for k, v in {'lane_w_m': lane_w, 'median_w_m': median, 'shoulder_w_m': shoulder}.items()}
    layouts = [core.intersection_layout(path, x['s'], main, {**preset.get('cross_street', {}), **x.get('cross', {})})
               for x in intersections]
    crossing = [lay['crossing'] for lay in layouts]
    frontage = [lay['frontage'] for lay in layouts]
    stop_clear = [lay['stop_clear'] for lay in layouts]
    kerb_clear = [lay['clear'] for lay in layouts]
    # surfaces
    asphalt = _material('env_asphalt', tuple(preset.get('asphalt_srgb', (0.16, 0.16, 0.17))))
    pave = _material('env_sidewalk', (0.45, 0.44, 0.42))
    road = _strip(f'{name}.road', path, road_w_m, 0.0, 0.4, asphalt, avoid=avoid)
    walks = []
    for side in ((1, -1) if both_sides else (1,)):
        walks += _strip(f'{name}.walk{side}', path, sidewalk_w_m, 0.15, 0.55, pave, lateral=side * (half + sidewalk_w_m / 2),
                        avoid=list(avoid) + crossing)
    for i, (x, lay) in enumerate(zip(intersections, layouts)):
        cross = {**preset.get('cross_street', {}), **x.get('cross', {})}
        cross_w = 2 * lay['cross_half_w']
        for sign in (1, -1):   # the crossing street, from the main kerb outward on both sides
            n = (-math.sin(lay['heading']) * sign, math.cos(lay['heading']) * sign)
            c = lay['centre']
            z = core.at(path, x['s'])[0][2]
            leg = [(c[0] + n[0] * half, c[1] + n[1] * half, z), (c[0] + n[0] * (half + sidewalk_w_m + cross['length_m']),
                                                                  c[1] + n[1] * (half + sidewalk_w_m + cross['length_m']), z)]
            road += _strip(f'{name}.cross{i}{sign:+d}', leg, cross_w, 0.0, 0.4, asphalt)
            wleg = [(c[0] + n[0] * (half + sidewalk_w_m), c[1] + n[1] * (half + sidewalk_w_m), z), leg[1]]
            for lat in (1, -1):
                walks += _strip(f'{name}.cwalk{i}{sign:+d}{lat:+d}', wleg, cross['sidewalk_w'], 0.15, 0.55, pave,
                                lateral=lat * (lay['cross_half_w'] + cross['sidewalk_w'] / 2))
    report['counts']['surfaces'] = len(road) + len(walks)
    # sources (built once from the exemplars, edited by data; hidden, they render only as instances)
    def src(exemplar, tag, edit=None):
        return env_fill.source(exemplar, library_root, edit=edit, name=f'{name}-{tag}')

    def emission(spec, part, factor):
        for m in spec['materials']:
            if part in m['part_ids'] and m.get('shader'):
                m['shader'].setdefault('params', {})
                if m['shader']['kind'] == 'emissive':
                    m['shader']['params']['strength'] = m['shader']['params'].get('strength', 8.0) * factor
                else:
                    m['shader']['params']['emission'] = m['shader']['params'].get('emission', 6.0) * factor

    def all_lit(spec, factor):
        for m in spec['materials']:
            if m.get('shader', {}).get('kind') == 'emissive':
                m['shader']['params']['strength'] = m['shader']['params'].get('strength', 8.0) * factor

    lit = 1.0 if night else 0.0   # night factor for emitters that are off by day
    # paint: lane dashes, the double yellow centre line, intersection markings - realized surface a reveal can cut
    dash = src('lane_dash', 'dash')
    dash_hosts = []
    for k in range(1, lanes_n):
        for side in (1, -1):
            host = env_fill.along(f'{name}.dash{k}{side}', [dash], path, 12.0, offset_m=side * (median / 2 + k * lane_w), seed=seed_of('dash'),
                                  realize=True, role='clutter', avoid=list(avoid) + stop_clear)
            if host:
                dash_hosts.append(host)
    yellow = _material('env_yellow_paint', (0.85, 0.65, 0.12), 0.7)
    centre_lines = []
    for side in (1, -1):
        offset = (median / 2 + 0.075) if median > 0.3 else 0.15
        centre_lines += _strip(f'{name}.centre{side:+d}', path, 0.15, 0.012, 0.02, yellow, lateral=side * offset,
                               avoid=list(avoid) + stop_clear)
    white = _material('env_white_paint', (0.88, 0.88, 0.86), 0.7)
    if shoulder > 0.5:   # a solid edge line where the lanes end: the spare width reads as a shoulder / bus lane, not road
        for side in (1, -1):
            centre_lines += _strip(f'{name}.edge{side:+d}', path, 0.15, 0.012, 0.02, white, lateral=side * (median / 2 + lanes_n * lane_w + 0.075),
                                   avoid=list(avoid) + stop_clear)
    paint = _paint(f'{name}.paint', [r for lay in layouts for a in lay['approaches'] for r in a['paint']],
                   [arrow for lay in layouts for a in lay['approaches'] for arrow in a['arrows']], white,
                   z=core.at(path, 0)[0][2]) if layouts else None
    for obj in centre_lines + ([paint] if paint else []):
        obj[ROLE] = 'clutter'
    # streetlights (arm towards the road) with one wide spot per head at night, trees
    lamp = src('streetlight', 'lamp', lambda s: emission(s, 'head', lit))
    lamp_rows = []
    lamps = env_fill.along(f'{name}.lamps', [lamp], path, preset['lamp_pitch_m'], offset_m=half + 0.8, both_sides=both_sides,
                           face='road', seed=seed_of('lamps'), avoid=list(avoid) + crossing + list(bare), avoid_margin_m=2.0, rows_out=lamp_rows)
    spots = _lamp_spots(f'{name}.spots', lamp, lamp_rows, preset.get('lamp_spot')) if night and preset.get('lamp_spot') else []
    tree_ids = [e for e in sorted(p.name for p in (Path(library_root) / 'exemplars').glob('bare_tree_*'))]
    tree_rows = []
    if tree_ids:
        trees_src = [src(t, t.replace('_', '')) for t in tree_ids]
    else:   # library without the winter set: the old inline tree
        trees_src = [_inline_tree(name)]
    trees = env_fill.along(f'{name}.trees', trees_src, path, preset['tree_pitch_m'], offset_m=half + sidewalk_w_m * 0.65, both_sides=both_sides,
                           start_m=preset['tree_pitch_m'] / 2, jitter_m=min(1.5, preset['tree_pitch_m'] * 0.1), seed=seed_of('trees'),
                           avoid=list(avoid) + kerb_clear + list(bare), avoid_margin_m=1.0, rows_out=tree_rows)
    # signals at every intersection approach, pedestrian signals at the crosswalk ends
    signal_hosts, signal_rows = [], []
    if layouts:
        vehicle = src('traffic_signal', 'signal', lambda s: all_lit(s, 1.0 if night else 0.6))
        ped = src('ped_signal', 'pedsignal', lambda s: all_lit(s, 1.0 if night else 0.6))
        placements = [(tuple(a['signal']['point']) + (core.at(path, x['s'])[0][2] + 0.15,), a['signal']['heading'] - math.pi / 2)
                      for x, lay in zip(intersections, layouts) for a in lay['approaches']]
        peds = [(tuple(p['point']) + (core.at(path, x['s'])[0][2] + 0.15,), p['heading'] - math.pi / 2)
                for x, lay in zip(intersections, layouts) for a in lay['approaches'] for p in a['ped_signals']]
        signal_hosts = [env_fill.at_positions(f'{name}.signals', [vehicle], placements, seed=seed_of('signals')),
                        env_fill.at_positions(f'{name}.pedsignals', [ped], peds, seed=seed_of('pedsignals'))]
        signal_rows = placements + peds
    # buildings: one source per variant (data edits of the tower exemplar), weighted per lot
    variants, weights = [], []
    for i, variant in enumerate(preset['building_variants']):
        def edit(spec, v=variant):
            for m in spec['materials']:
                if 'shaft' in m['part_ids'] and m.get('shader'):
                    m['shader']['params'] = {**m['shader'].get('params', {}), 'white_mix': preset.get('window_white_mix', 0.0),
                                             **v.get('shaft', {}), **({} if night else day_facade())}
            emission(spec, 'shaft', lit)
            for m in spec['materials']:
                if 'podium' in m['part_ids'] and m.get('shader'):
                    m['shader']['params']['emission'] = preset.get('podium_emission', 8.0) * lit
                    m['shader']['params']['white_mix'] = preset.get('window_white_mix', 0.0)
        variants.append(src('tower_block', f'tower{i}', edit)); weights.append(variant['weight'])
    lots_all, building_hosts = [], []
    for side, sign in (('left', 1), ('right', -1)) if both_sides else (('left', 1),):
        host, lots = env_fill.blocks(f'{name}.blocks.{side}', variants, path, side=side, base_size=TOWER_BASE,
                                     lot_width_range_m=preset['lot_width_m'], depth_range_m=preset['lot_depth_m'], height_range_m=preset['height_m'],
                                     gap_range_m=preset['lot_gap_m'], setback_m=half + sidewalk_w_m + 1.0, weights=weights,
                                     avoid=list(avoid) + frontage, seed=seed_of(f'blocks{side}'), sightline=sightline)
        if host:
            building_hosts.append(host); lots_all += [{**lot, 'side': side} for lot in lots]
    if preset.get('skyline'):   # distant city across the end of the street: a horizon of lit towers instead of empty sky
        end, heading = core.at(path, core.length(path))
        f, r_ = (math.cos(heading), math.sin(heading)), (math.sin(heading), -math.cos(heading))
        sky = preset['skyline']
        for k, dist in enumerate(sky['distances_m']):
            c = (end[0] + f[0] * dist, end[1] + f[1] * dist)
            row = [(c[0] - r_[0] * sky['half_width_m'], c[1] - r_[1] * sky['half_width_m'], end[2]),
                   (c[0] + r_[0] * sky['half_width_m'], c[1] + r_[1] * sky['half_width_m'], end[2])]
            host, lots = env_fill.blocks(f'{name}.skyline{k}', variants, row, side='left', base_size=TOWER_BASE,
                                         lot_width_range_m=sky['lot_width_m'], depth_range_m=sky['lot_width_m'], height_range_m=sky['height_m'],
                                         gap_range_m=sky['gap_m'], setback_m=0.0, weights=weights, seed=seed_of(f'skyline{k}'))
            if host:
                building_hosts.append(host)
    roof = src('rooftop_unit', 'roof')
    roofs = env_fill.on_top(f'{name}.roofs', [roof], lots_all, spacing_m=preset['roof_spacing_m'], keep_ratio=preset['roof_keep'], seed=seed_of('roofs'))
    # signs on podium fronts, facing the road, colours from the preset
    sign_sources = []
    for i, colour in enumerate(preset['sign_colors']):
        def edit(spec, c=colour):
            for m in spec['materials']:
                if m.get('shader'):
                    m['shader']['params'] = {**m['shader'].get('params', {}), 'color_srgb': c}
            emission(spec, 'panel', 0.35 + 0.65 * lit)
        sign_sources.append(src('sign_panel', f'sign{i}', edit))
    sign_hosts = []
    for side, sign in (('left', 1), ('right', -1)) if both_sides else (('left', 1),):
        side_lots = [lot for lot in lots_all if lot.get('side') == side]
        host = env_fill.on_front(f'{name}.signs.{side}', sign_sources, side_lots, per_lot=preset.get('signs_per_lot', (0, 2)),
                                 z_range=preset['sign_z_m'], side_sign=sign, seed=seed_of(f'signs{side}'))
        if host:
            sign_hosts.append(host)
    # bus shelters on the kerb, open side to the road
    shelters = None
    if preset.get('shelter_pitch_m'):
        rows = []
        for side in ((1, -1) if both_sides else (1,)):
            for p, h in core.along(path, preset['shelter_pitch_m'], offset_m=side * (half + 2.2), start_m=preset['shelter_pitch_m'] * (0.35 if side > 0 else 0.8),
                                   avoid=list(avoid) + kerb_clear + list(bare), avoid_margin_m=8.0, seed=seed_of('shelters'), name=f'shelter{side}'):
                rows.append(((p[0], p[1], p[2] + 0.15), h + (-math.pi if side > 0 else 0.0)))   # local +Y toward the road
        shelters = env_fill.at_positions(f'{name}.shelters', [src('bus_shelter', 'shelter', lambda s: all_lit(s, 0.3 + 0.7 * lit))], rows,
                                         seed=seed_of('shelters'))
    # people: along the sidewalks and grouped on the corners, never inside a pole, trunk or signal
    people, placements_people = None, []
    pedestrian_ids = sorted(p.name for p in (Path(library_root) / 'exemplars').glob('pedestrian_*'))
    if pedestrian_ids and preset.get('ped_per_100m'):
        r = core.rng(seed_of('people'), 'people')
        points = []
        total = core.length(path)
        for side in ((1, -1) if both_sides else (1,)):
            for _ in range(round(total * preset['ped_per_100m'] / 100)):
                s_at = r.uniform(0, total)
                if core.inside(s_at, list(avoid) + crossing + list(bare), 1.0):
                    continue
                p, h = core.at(path, s_at)
                q = core.offset_point(p, h, side * r.uniform(half + 0.7, half + sidewalk_w_m - 0.7))
                points.append(((q[0], q[1], q[2] + 0.15), h + r.choice((0.0, math.pi)) + r.uniform(-0.3, 0.3)))
        for x, lay in zip(intersections, layouts):
            z = core.at(path, x['s'])[0][2] + 0.15
            for k, corner in enumerate(lay['corners']):
                count = r.randint(*preset.get('corner_peds', (3, 6)))
                for q in core.scatter_in_rect(corner['point'], corner['heading'], tuple(v * 0.8 for v in corner['size']), count,
                                              seed=seed_of('people'), name=f'corner{x["s"]}:{k}', z=z):
                    points.append((q, r.uniform(-math.pi, math.pi)))
        obstacles = [p for p, _ in lamp_rows] + [p for p, _ in tree_rows] + [p for p, _ in signal_rows]
        kept = core.keep_out([p for p, _ in points], obstacles, 0.6)
        kept_set = {tuple(p) for p in kept}
        placements_people = [(p, rot) for p, rot in points if tuple(p) in kept_set]
        people = env_fill.at_positions(f'{name}.people', [src(t, t.replace('_', '')) for t in pedestrian_ids], placements_people,
                                       seed=seed_of('people'))
    # traffic: right-hand driving, lanes_per_direction each way; cars, taxis and buses by the preset's mix
    mix = preset.get('vehicle_mix', {'car': 1.0})
    car_sources, vehicle_weights = [], []
    for i, colour in enumerate(preset['car_colors']):
        def edit(spec, c=colour):
            for m in spec['materials']:
                if m['part_ids'] == ['body']:
                    m['color_srgb'] = c
            emission(spec, 'headlights', 0.15 + 0.85 * lit); emission(spec, 'taillights', 0.3 + 0.7 * lit)
        car_sources.append(src('car', f'car{i}', edit)); vehicle_weights.append(mix.get('car', 1.0) / len(preset['car_colors']))
    for kind in ('taxi', 'bus'):
        if mix.get(kind) and (Path(library_root) / 'exemplars' / kind).is_dir():
            car_sources.append(src(kind, kind, lambda s: all_lit(s, 0.15 + 0.85 * lit))); vehicle_weights.append(mix[kind])
    lanes = []
    for k in range(lanes_n):
        offset = median / 2 + (k + 0.5) * lane_w
        lanes.append({'path': [core.offset_point(core.at(path, s)[0], core.at(path, s)[1], -offset) for s in _breaks(path)], 'direction': 1})
        lanes.append({'path': [core.offset_point(core.at(path, s)[0], core.at(path, s)[1], offset) for s in _breaks(path)], 'direction': -1})
    traffic_root, cars = env_fill.traffic(f'{name}.traffic', car_sources, lanes, frames, per_100m=preset['cars_per_100m_lane'],
                                          speed_mps_range=preset['speed_mps'], min_gap_m=preset['min_gap_m'], fps=fps, seed=seed_of('traffic'),
                                          weights=vehicle_weights, avoid=list(avoid) + list(keep_clear))
    from scatter import digest, instance_count
    hosts = {'dashes': dash_hosts, 'lamps': [lamps], 'trees': [trees], 'buildings': building_hosts, 'roofs': [roofs], 'signs': sign_hosts,
             'signals': signal_hosts, 'people': [people], 'shelters': [shelters]}
    for key, items in hosts.items():
        items = [h for h in items if h is not None]
        report['hosts'][key] = [h.name for h in items]
        report['counts'][key] = sum(_copies(h, instance_count) for h in items)
    report['counts']['vehicles'] = len(cars)
    report['counts']['lots'] = len(lots_all)
    report['counts']['lots_capped_for_sky'] = sum(1 for lot in lots_all if 'capped_from' in lot)
    report['counts']['intersections'] = len(layouts)
    report['counts']['crosswalk_stripes'] = sum(a['stripes'] for lay in layouts for a in lay['approaches'])
    report['counts']['lamp_spots'] = len(spots)
    report['placements'] = {'lamps': len(lamp_rows), 'trees': len(tree_rows), 'signal_poles': len(signal_rows) - (len(peds) if layouts else 0),
                            'ped_signals': len(peds) if layouts else 0, 'people': len(placements_people)}   # copies, not part instances
    report['counts']['centre_line_pieces'] = len(centre_lines)
    report['lamp_spots'] = [s.name for s in spots]
    import hashlib
    parts = [digest(h) for items in hosts.values() for h in items if h is not None] + [json.dumps(cars, sort_keys=True, default=str)]
    parts += [json.dumps([[round(c, 4) for c in s.location] for s in spots])]
    report['digest'] = hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]
    scene = bpy.context.scene
    reports = json.loads(scene.get(REPORT_KEY, '[]'))
    scene[REPORT_KEY] = json.dumps(reports + [report], sort_keys=True)
    return report


def _inline_tree(name):
    """Round-crown tree from primitives, for a library without the bare_tree exemplars."""
    import bmesh
    trunk = bpy.data.meshes.new(f'{name}.trunk')
    bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.18, radius2=0.14, depth=3.0)
    bmesh.ops.translate(bm, vec=(0, 0, 1.5), verts=bm.verts); bm.to_mesh(trunk); bm.free()
    crown = bpy.data.meshes.new(f'{name}.crown')
    bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=2, radius=2.0)
    bmesh.ops.translate(bm, vec=(0, 0, 4.4), verts=bm.verts); bm.to_mesh(crown); bm.free()
    trunk.materials.append(_material('env_trunk', (0.3, 0.22, 0.15))); crown.materials.append(_material('env_leaf', (0.18, 0.3, 0.14), 0.8))
    tree = bpy.data.objects.new(f'{name}.tree', None); bpy.context.scene.collection.objects.link(tree)
    for mesh in (trunk, crown):
        part = bpy.data.objects.new(mesh.name, mesh); bpy.context.scene.collection.objects.link(part); part.parent = tree
    return tree


def _head_offset(lamp):
    """Lamp head centre in the streetlight source's frame (the exemplar's `head` part)."""
    for obj in lamp.children_recursive:
        if obj.get('studio_part_id') == 'head':
            return tuple(lamp.matrix_world.inverted() @ obj.matrix_world.translation)
    return (1.8, 0.0, 8.8)


def _lamp_spots(name, lamp, rows, spec):
    """One wide spot under every lamp head: the light pools on the road that make a night street read. Real lights
    (instances cannot carry lights); `studio_keep_light` so the photoreal look keeps them."""
    from env_materials import kelvin_rgb
    head = _head_offset(lamp)
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    root['studio_id'] = name
    out = []
    data = bpy.data.lights.new(name, 'SPOT')
    data.energy = spec['power_w']
    data.spot_size = math.radians(spec['cone_deg'])
    data.spot_blend = spec['blend']
    data.color = kelvin_rgb(spec['kelvin'])
    data.shadow_soft_size = 0.3
    for i, (point, rot) in enumerate(rows):
        c, s = math.cos(rot), math.sin(rot)
        obj = bpy.data.objects.new(f'{name}.{i:03d}', data)
        obj.location = (point[0] + head[0] * c - head[1] * s, point[1] + head[0] * s + head[1] * c, point[2] + head[2] - 0.15)
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = root
        obj['studio_keep_light'] = True
        obj['studio_id'] = obj.name
        out.append(obj)
    return out


def _paint(name, rects, arrows, material, z=0.0, top=0.012, thickness=0.02):
    """One mesh of road markings: rectangles (cx, cy, heading, length along heading, width) and 5 m arrows
    (tip, heading). Closed boxes wound outward, so a reveal cuts them like the road."""
    import bmesh
    bm = bmesh.new()

    def prism(outline):
        bottom = [bm.verts.new((x, y, z + top - thickness)) for x, y in outline]
        upper = [bm.verts.new((x, y, z + top)) for x, y in outline]
        bm.faces.new(list(reversed(bottom)))
        bm.faces.new(upper)
        n = len(outline)
        for k in range(n):
            bm.faces.new((bottom[k], bottom[(k + 1) % n], upper[(k + 1) % n], upper[k]))

    def frame(cx, cy, heading, pts):
        c, s = math.cos(heading), math.sin(heading)
        return [(cx + u * c - v * s, cy + u * s + v * c) for u, v in pts]

    for cx, cy, heading, length, width in rects:
        prism(frame(cx, cy, heading, [(-length / 2, -width / 2), (length / 2, -width / 2), (length / 2, width / 2), (-length / 2, width / 2)]))
    arrow = [(0.0, 0.0), (-1.4, 0.45), (-1.4, 0.15), (-5.0, 0.15), (-5.0, -0.15), (-1.4, -0.15), (-1.4, -0.45)]   # 5 m, tip at origin
    for (tx, ty), heading in arrows:
        # concave outline: split into the head triangle and the shaft rectangle
        prism(frame(tx, ty, heading, [arrow[0], arrow[1], arrow[6]]))
        prism(frame(tx, ty, heading, [(-1.4, -0.15), (-1.4, 0.15), (-5.0, 0.15), (-5.0, -0.15)]))
    mesh = bpy.data.meshes.new(name)
    bm.normal_update()
    bm.to_mesh(mesh); bm.free()
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['studio_id'] = name
    obj['studio_dim_role'] = 'none'
    return obj


def _copies(host, instance_count):
    """Placed copies of a scatter host (a realized host has no instances left; its record keeps the point count)."""
    record = json.loads(host.get('studio_scatter', '{}'))
    return record['points'] if record.get('realize') and record.get('points') else instance_count(host)


def _breaks(path):
    """Arc lengths of the path's corners (a lane polyline keeps the street's corners)."""
    out, s = [0.0], 0.0
    for a, b in zip(path, path[1:]):
        s += math.dist(a[:2], b[:2])
        out.append(s)
    return out
