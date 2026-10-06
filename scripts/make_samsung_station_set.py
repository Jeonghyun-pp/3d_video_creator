"""Migration (one-off, kept as provenance): the samsung station (s01 path of samsung_lib: cutaway, bright, detail) as
declarative scene data. Pure edit functions are read from samsung_lib.py itself. Usage: python -I scripts/make_samsung_station_set.py <repo>"""
import ast, json, math, sys
from copy import deepcopy
from pathlib import Path

REPO = Path(sys.argv[1])
LIB = (REPO / 'examples/samsung_cutaway/samsung_lib.py').read_text()
tree = ast.parse(LIB)
ns = {'math': math}
for node in tree.body:
    if isinstance(node, ast.Assign) and any(n.id in ('LEVEL_H', 'LEVELS', 'BOX_W', 'BOX_L', 'ROAD_W', 'FLAT', 'CATALOG', 'EMISSIVE') for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)):
        exec(compile(ast.Module([node], []), 'lib', 'exec'), ns)
    if isinstance(node, ast.FunctionDef) and node.name in ('_builder', 'run_length', 'drop_parts', 'chain', 'girders', 'station_levels'):
        exec(compile(ast.Module([node], []), 'lib', 'exec'), ns)
LEVEL_H, LEVELS, BOX_W, BOX_L = ns['LEVEL_H'], ns['LEVELS'], ns['BOX_W'], ns['BOX_L']

def latest(name):
    folder = REPO / 'library/exemplars' / name
    version = sorted(p.name for p in folder.glob('v*'))[-1]
    return version, json.loads((folder / version / 'spec.json').read_text())

def edits_for(name, edit):
    """The edit as data: drop_parts for removed parts, then set ops on changed builder params (indices after drops)."""
    version, spec = latest(name)
    edited = deepcopy(spec); edit(edited) if edit else None
    ops = []
    dropped = [b['part_id'] for b in spec['builders'] if b['part_id'] not in {x['part_id'] for x in edited['builders']}]
    if dropped:
        ops.append({'op': 'drop_parts', 'parts': dropped})
    base = deepcopy(spec); ns['drop_parts'](*dropped)(base) if dropped else None
    for i, (a, b) in enumerate(zip(base['builders'], edited['builders'])):
        if a['params'] != b['params']:
            ops.append({'op': 'set', 'path': f'/builders/{i}/params', 'value': b['params']})
    return f'{name}@{version}', ops

y0, interior = 60.0, {'concrete': 'interior_concrete', 'slab_edge': 'interior_slab'}
kind = lambda k: interior.get(k, k)
depth = LEVELS * LEVEL_H; yc = y0 + BOX_L / 2; atrium_w = 9.0
prims, insts = [], []
def box(i, size, at, k, **extra):
    prims.append({'id': i, 'shape': 'box', 'size': list(size), 'at': list(at), 'material': k, **extra})
box('st.wall_l', (1.2, BOX_L, depth), (-BOX_W / 2, yc, -depth / 2), kind('concrete_dark'))
box('st.wall_r', (1.2, BOX_L, depth), (BOX_W / 2, yc, -depth / 2), kind('concrete_dark'))
box('st.base', (BOX_W, BOX_L, 1.5), (0, yc, -depth - 0.75), kind('concrete_dark'))
box('st.end_far', (BOX_W, 1.2, depth), (0, y0 + BOX_L, -depth / 2), kind('concrete_dark'))
w = (BOX_W - atrium_w) / 2; x = atrium_w / 2 + w / 2
box('st.slab', (w, BOX_L, 0.8), (x, yc, -LEVEL_H - 0.4), kind('slab_edge'), mirror_x=True,
    repeat={'counts': [1, 1, LEVELS - 1], 'pitch_m': [0, 0, -LEVEL_H]})
box('st.col', (1.0, 1.0, LEVEL_H - 0.8), (atrium_w / 2 + 1.0, y0 + 6, -0.8 - (LEVEL_H - 0.8) / 2), kind('concrete'), mirror_x=True,
    repeat={'counts': [1, int(BOX_L / 9), LEVELS], 'pitch_m': [0, 9, -LEVEL_H]}, level_by_z=[f'B{l + 1}' for l in range(LEVELS)], yields_to_fill='column')
girder_ex, girder_ops = edits_for('beam_grid_ceiling', ns['girders'](w, int(BOX_L / 9), 9.0))
row_ex, row_ops = edits_for('light_row', ns['run_length'](['fixtures'], BOX_L - 10, axis=1))
rail_ex, rail_ops = edits_for('glass_railing', ns['run_length'](['posts', 'panels', 'top_rail'], BOX_L - 2.5))
esc_ex, _ = edits_for('escalator', None)
track_ex, track_ops = edits_for('track', ns['run_length'](['rail_l', 'sleepers'], BOX_L - 0.5, axis=1))
for lvl in range(1, LEVELS):
    z = -lvl * LEVEL_H
    for side in (-1, 1):
        sx = side * x
        tag = f"{lvl}.{'l' if side < 0 else 'r'}"
        insts.append({'id': f'st.girders.{tag}', 'exemplar': girder_ex, 'at': [sx, y0 + 6, z + LEVEL_H - 1.2 if lvl > 1 else -1.2], 'edits': girder_ops})
        for k in (-1, 1):
            insts.append({'id': f"st.lights.{tag}.{'a' if k < 0 else 'b'}", 'exemplar': row_ex,
                          'at': [sx + k * w / 4, y0 + 5, z + LEVEL_H - 1.7 if lvl > 1 else -1.7], 'edits': row_ops})
    if lvl < LEVELS - 1:
        for side in (-1, 1):
            insts.append({'id': f"st.railing.{lvl}.{'l' if side < 0 else 'r'}", 'exemplar': rail_ex, 'at': [side * atrium_w / 2, y0 + 1, z],
                          'rot_z_deg': 90, 'edits': rail_ops})
for lvl in range(1, LEVELS - 1):
    z_top = -lvl * LEVEL_H
    for side in (-1, 1):
        insts.append({'id': f"st.escalator.{lvl}.{'l' if side < 0 else 'r'}", 'exemplar': esc_ex,
                      'at': [side * 2.4, y0 + 30 + lvl * 4 - LEVEL_H / math.tan(math.radians(30)) / 2, z_top - LEVEL_H]})
zb = -depth
box('st.platform', (10.0, BOX_L - 10, 1.1), (0, yc, zb + 0.55), kind('floor_tile'))
box('st.edge', (0.35, BOX_L - 10, 0.02), (4.6, yc, zb + 1.11), kind('safety_line'), mirror_x=True)
for side in (-1, 1):
    insts.append({'id': f"st.track.{'l' if side < 0 else 'r'}", 'exemplar': track_ex, 'at': [side * 8.5, y0, zb], 'edits': track_ops})

materials = {}
for k, color in ns['FLAT'].items():
    materials[k] = {'color': list(color), 'roughness': 0.25 if k in ('glass', 'car_white', 'car_black', 'car_grey', 'car_yellow', 'train', 'screen') else 0.85,
                    **({'metallic': 0.6} if k in ('steel', 'rail', 'train') else {}), **({'transmission': 0.9} if k == 'glass' else {})}
for k, (key, overrides) in ns['CATALOG'].items():
    materials.setdefault(k, {'color': [0.6, 0.6, 0.6], 'roughness': 0.85})
    materials[k].update({'catalog': key, **({'catalog_overrides': overrides} if overrides else {})})
for k, (color, strength) in ns['EMISSIVE'].items():
    materials[k] = {'color': list(color), 'emission': {'color': list(color), 'strength': strength}, **({'alpha': 0.25} if k == 'holo' else {})}
volume = {'id': 'st.box', 'box': [[-BOX_W / 2 - 0.6, y0, -depth - 1.5], [BOX_W / 2 + 0.6, y0 + BOX_L, 0.0]]}
station = {'materials': materials, 'volumes': [volume], 'primitives': prims, 'instances': insts, 'levels': ns['station_levels'](y0)}
(REPO / 'examples/samsung_cutaway/project/sets').mkdir(exist_ok=True)
(REPO / 'examples/samsung_cutaway/project/sets/station.json').write_text(json.dumps(station, ensure_ascii=False, indent=1) + '\n')

y_lo, y_hi = -320.0, 520.0
city = {'id': 'city', 'kit': 'street', 'sightline': {'from_move': True, 'keep_sky_v': 0.33, 'forward': [0.0, 1.0]},
        'args': {'path': [[0.0, y_lo, 0.0], [0.0, y_hi, 0.0]], 'road_w_m': ns['ROAD_W'], 'lanes_per_direction': 4, 'sidewalk_w_m': 8.0, 'night': True,
                 'avoid': [], 'keep_clear': [[y0 - 260 - y_lo, y0 - y_lo]], 'seed': 7,
                 'overrides': {'height_m': [min(25, 130 * 0.5), 130], 'cars_per_100m_lane': round(60 / ((y_hi - y_lo) / 100 * 8), 3)},
                 'intersections': [{'s': cy - y_lo} for cy in (y0 + 22, 330.0)], 'bare': [[y0 - 70 - y_lo, y0 - y_lo]]}}
s01 = {'use': 'sets/station.json',
       'world': {'kind': 'blockout', 'color': [0.20, 0.24, 0.40], 'strength': 0.5, 'samples': 16, 'fast_gi_m': 6.0,
                 'sun': {'energy': 0.6, 'color': [1.0, 0.6, 0.35], 'rot_deg': [40, 15, 30]}},
       'kits': [city],
       'section': {'id': 'st.section', 'box': 'st.box', 'ceilings': [-lvl * LEVEL_H - 0.8 for lvl in range(LEVELS)][1:] + [-0.4 - 0.8],
                   'front_cutter': {'id': 'ground.cutter', 'reach_m': 260.0, 'half_w_m': 200.0, 'z_lo': -60.0, 'z_hi': 0.5},
                   'copy_materials': [{'id': 'section_cap', 'from': 'st.section.poche'}]},
       'bind': [{'select': 'city.road.0', 'instance_id': 'road', 'part_id': 'slab'}]}
shot_file = REPO / 'examples/samsung_cutaway/project/shots/s01/shot.json'
shot = json.loads(shot_file.read_text()); shot['scene'] = s01
shot_file.write_text(json.dumps(shot, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'primitives': len(prims), 'instances': len(insts), 'materials': len(materials)}))
