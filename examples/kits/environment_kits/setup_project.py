"""Environment-kit exemplars (city kit, first set) as spec data only - the same route as the building elements.

Run from the repo root: .venv/bin/python examples/kits/environment_kits/setup_project.py
Creates projects/harness_validation/environment_kits: one shot holding the kit subjects. Fill functions
(studio/blender_ops/env_fill.py) place them; variation (lit windows, colours) lives in shaders
(studio/blender_ops/env_materials.py) and in data edits, never in per-kit code. Typical values (lighting pole
height, car size, lane dash) are agent recall and must be checked by a human before publication.
Every part is modelled with its base at z = 0 and its front / travel direction along +Y.
"""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

P = ROOT / 'projects/harness_validation/environment_kits'
SRC = Path(__file__).parents[1] / 'template'   # tracked minimal project/style/shot
DESIGN = {'id': 'design', 'kind': 'measurement', 'license': 'own schematic design'}


def typical(id_, text):
    return {'id': id_, 'kind': 'standard', 'license': 'typical value (agent recall; human check required)', 'note': text}


def spec(subject_id, identity, request, trace, sources, dimensions, features, builders, materials):
    return {'schema_version': 1, 'subject_id': subject_id, 'identity': identity, 'subject_mode': 'schematic', 'request': request,
            'request_trace': trace, 'sources': sources, 'dimensions': dimensions, 'features': features, 'builders': builders,
            'materials': materials}


def box(part, size, loc, **extra):
    return {'part_id': part, 'builder': 'box', 'dim_role': 'none', 'params': {'size': list(size)}, 'transform': {'location': list(loc)}, **extra}


def present(fid, text, parts):
    return {'id': fid, 'description': text, 'part_ids': parts, 'verify': 'presence'}


CONCRETE = {'color_srgb': [0.55, 0.55, 0.53], 'roughness': 0.9}
STEEL = {'color_srgb': [0.3, 0.32, 0.34], 'metallic': 1, 'roughness': 0.5}
OFFICE = {'kind': 'window_grid', 'params': {'floor_h_m': 3.6, 'bay_w_m': 3.0, 'window_frac': [0.7, 0.55], 'lit_ratio': 0.45, 'emission': 6.0}}
SHOPFRONT = {'kind': 'window_grid', 'params': {'floor_h_m': 4.5, 'bay_w_m': 6.0, 'window_frac': [0.85, 0.7], 'sill_frac': 0.1,
                                                'lit_ratio': 0.85, 'emission': 8.0, 'palette_k': [2700, 3000, 3500]}}

# 1. Tower block: podium + office shaft + crown. 24 x 24 m footprint, 61.2 m; fill functions scale it per lot ----------
tower = spec('tower_block', 'office tower block: 8 m podium with shopfronts, 20 x 20 m shaft to 60 m, roof crown', '도시 빌딩',
             [{'phrase': '도시 빌딩', 'items': ['shaft', 'podium', 'crown', 'dim.height', 'feat.shaft']}],
             [DESIGN],
             [{'id': 'dim.height', 'value_m': 61.2, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'z'},
              {'id': 'dim.footprint', 'value_m': 24.0, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x'}],
             [present('feat.shaft', 'office shaft with window grid', ['shaft']), present('feat.podium', 'podium with shopfronts', ['podium'])],
             [box('podium', (24, 24, 8), (0, 0, 4), features=['feat.podium']), box('shaft', (20, 20, 52), (0, 0, 34), features=['feat.shaft']), box('crown', (20.4, 20.4, 1.2), (0, 0, 60.6))],
             [{'part_ids': ['shaft'], 'shader': OFFICE}, {'part_ids': ['podium'], 'shader': SHOPFRONT}, {'part_ids': ['crown'], **CONCRETE}])

# 2. Streetlight: 9 m pole, 4.6 m arm towards +X (the road), warm LED head 3-4 m over the carriageway ------------------------------------------------
light = spec('streetlight', 'road lighting column, 9 m pole, 4.6 m arm over the carriageway, warm LED head', '가로등',
             [{'phrase': '가로등', 'items': ['pole', 'arm', 'head', 'dim.pole_height', 'feat.head']}],
             [DESIGN, typical('pole', 'urban road lighting columns are typically 8-10 m'),
              typical('reach', 'arm puts the head 3-4 m over the carriageway (reference stills: heads over the outer lane)')],
             [{'id': 'dim.pole_height', 'value_m': 9.0, 'tol_pct': 0.5, 'source_id': 'pole', 'measure': 'z', 'part_ids': ['pole']},
              {'id': 'dim.reach', 'value_m': 4.7, 'tol_pct': 0.5, 'source_id': 'reach', 'measure': 'x'}],
             [present('feat.head', 'lit lamp head', ['head'])],
             [box('pole', (0.2, 0.2, 9.0), (0, 0, 4.5)), box('arm', (4.6, 0.12, 0.12), (2.3, 0, 8.9)), box('head', (0.6, 0.3, 0.15), (4.2, 0, 8.8), features=['feat.head'])],
             [{'part_ids': ['pole', 'arm'], **STEEL},
              {'part_ids': ['head'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.82, 0.6], 'strength': 30.0}}, 'scene_role': 'light_fixture'}])

# 3. Car: 4.5 x 1.8 x 1.45 m, white headlights at +Y, red tail lights at -Y -------------------------------------------
car = spec('car', 'mid-size car 4.5 x 1.8 x 1.45 m with lit head and tail lights', '자동차',
           [{'phrase': '자동차', 'items': ['body', 'cabin', 'wheels', 'headlights', 'taillights', 'dim.length', 'feat.lights']}],
           [DESIGN, typical('car', 'mid-size passenger car about 4.5 m long, 1.8 m wide, 1.45 m high')],
           [{'id': 'dim.length', 'value_m': 4.5, 'tol_pct': 0.5, 'source_id': 'car', 'measure': 'y', 'part_ids': ['body']},
            {'id': 'dim.width', 'value_m': 1.8, 'tol_pct': 0.5, 'source_id': 'car', 'measure': 'x', 'part_ids': ['body']},
            {'id': 'dim.height', 'value_m': 1.45, 'tol_pct': 0.5, 'source_id': 'car', 'measure': 'z'}],
           [present('feat.lights', 'head and tail lights', ['headlights', 'taillights']),
            {'id': 'feat.wheels', 'description': 'four wheels', 'part_ids': ['wheels'], 'verify': 'count', 'count': 4}],
           [box('body', (1.8, 4.5, 0.7), (0, 0, 0.55)), box('cabin', (1.6, 2.4, 0.55), (0, -0.2, 1.175)),
            {'part_id': 'wheels', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.wheels'],
             'params': {'pattern': 'grid', 'counts': [2, 2], 'pitch_m': [1.6, 2.8], 'axes': ['x', 'y'], 'center': [-0.8, -1.4, 0],
                        'item': {'builder': 'box', 'params': {'size': [0.25, 0.66, 0.66]}, 'transform': {'location': [0, 0, 0.33]}}}},
            box('headlights', (1.4, 0.05, 0.12), (0, 2.26, 0.65), features=['feat.lights']), box('taillights', (1.4, 0.05, 0.12), (0, -2.26, 0.7), features=['feat.lights'])],
           [{'part_ids': ['body'], 'color_srgb': [0.62, 0.64, 0.68], 'roughness': 0.3}, {'part_ids': ['cabin'], 'color_srgb': [0.08, 0.1, 0.12], 'roughness': 0.1},
            {'part_ids': ['wheels'], 'color_srgb': [0.05, 0.05, 0.05], 'roughness': 0.8},
            {'part_ids': ['headlights'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.97, 0.9], 'strength': 14.0}}, 'scene_role': 'light_fixture'},
            {'part_ids': ['taillights'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.08, 0.05], 'strength': 8.0}}, 'scene_role': 'light_fixture'}])

# 4. Sign panel: 3 x 1.2 m lit panel, no text (text is never modelled; labels come from the edit) ----------------------
sign = spec('sign_panel', 'lit facade sign panel 3.0 x 1.2 m, blank', '간판',
            [{'phrase': '간판', 'items': ['panel', 'dim.width', 'feat.panel']}],
            [DESIGN],
            [{'id': 'dim.width', 'value_m': 3.0, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'y', 'part_ids': ['panel']}],
            [present('feat.panel', 'lit blank panel', ['panel'])],
            [box('panel', (0.15, 3.0, 1.2), (0, 0, 0.6), features=['feat.panel'])],
            [{'part_ids': ['panel'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [0.3, 0.75, 1.0], 'strength': 5.0}}, 'scene_role': 'light_fixture'}])

# 5. Rooftop unit: plant housing with a fan deck ----------------------------------------------------------------------
roof = spec('rooftop_unit', 'rooftop plant unit 2.4 x 1.6 x 1.4 m with fan deck', '옥상 설비',
            [{'phrase': '옥상 설비', 'items': ['housing', 'fan', 'dim.height', 'feat.housing']}],
            [DESIGN],
            [{'id': 'dim.height', 'value_m': 1.6, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'z'}],
            [present('feat.housing', 'plant housing and fan', ['housing', 'fan'])],
            [box('housing', (2.4, 1.6, 1.4), (0, 0, 0.7), features=['feat.housing']), box('fan', (1.0, 1.0, 0.2), (0.4, 0, 1.5), features=['feat.housing'])],
            [{'part_ids': ['housing'], 'color_srgb': [0.7, 0.71, 0.7], 'roughness': 0.6}, {'part_ids': ['fan'], **STEEL}])

# 6. Lane dash: 5 m white dash, 0.15 m wide --------------------------------------------------------------------------
dash = spec('lane_dash', 'white lane dash 0.15 x 5 m', '차선',
            [{'phrase': '차선', 'items': ['dash', 'dim.length', 'feat.dash']}],
            [DESIGN, typical('dash', 'broken lane line segments are typically several metres long')],
            [{'id': 'dim.length', 'value_m': 5.0, 'tol_pct': 0.5, 'source_id': 'dash', 'measure': 'y', 'part_ids': ['dash']}],
            [present('feat.dash', 'painted dash', ['dash'])],
            [box('dash', (0.15, 5.0, 0.02), (0, 0, 0.01), features=['feat.dash'])],
            [{'part_ids': ['dash'], 'color_srgb': [0.92, 0.92, 0.9], 'roughness': 0.7, 'scene_role': 'clutter'}])

# --- street detail set (s01 feedback 3, measured against the reference: references/environment_kits.md) ------------
import math
import random

KS = {'id': 'ks_signal', 'kind': 'standard', 'license': 'Korean traffic signal installation manual values (human check required)',
      'note': 'vehicle heads 4-aspect horizontal 1420 x 355 mm, head bottom 4.5-5.0 m over the road'}
LAMP_OFF = 0.35   # unlit aspects keep a little glow so a night head reads as a 4-lamp unit, not one dot


def lamp(part, x, z, colour, strength):
    return ({'part_id': part, 'builder': 'box', 'dim_role': 'none', 'params': {'size': [0.27, 0.06, 0.27]},
             'transform': {'location': [x, 0.18, z]}, 'features': ['feat.lamps']},
            {'part_ids': [part], 'shader': {'kind': 'emissive', 'params': {'color_srgb': colour, 'strength': strength}}, 'scene_role': 'light_fixture'})


# 7. Cantilever vehicle signal: pole behind the kerb, arm along +X over the lanes, two 4-aspect heads facing +Y
heads, mats = [], []
for i, hx in enumerate((3.4, 6.6)):
    zc = 4.7 + 0.355 / 2
    heads.append(box(f'head{i}', (1.42, 0.3, 0.355), (hx, 0, zc), features=['feat.heads']))
    for j, colour in enumerate(((1.0, 0.08, 0.05), (1.0, 0.7, 0.1), (0.2, 1.0, 0.45), (0.2, 1.0, 0.45))):
        b, m = lamp(f'lamp{i}{j}', hx - 0.53 + j * 0.355, zc, list(colour), 6.0 if j == 0 else LAMP_OFF)
        heads.append(b); mats.append(m)
signal = spec('traffic_signal', 'cantilever traffic signal: 6.6 m pole, 7.4 m arm over the lanes, two 4-aspect horizontal heads (red lit)', '신호등',
              [{'phrase': '신호등', 'items': ['pole', 'arm', 'head0', 'head1', 'dim.head_width', 'feat.heads']}],
              [DESIGN, KS],
              [{'id': 'dim.head_width', 'value_m': 1.42, 'tol_pct': 1.0, 'source_id': 'ks_signal', 'measure': 'x', 'part_ids': ['head0']},
               {'id': 'dim.height', 'value_m': 6.6, 'tol_pct': 1.0, 'source_id': 'design', 'measure': 'z'}],
              [present('feat.heads', '4-aspect vehicle heads', ['head0', 'head1']), present('feat.lamps', 'signal lamps', ['lamp00'])],
              [box('pole', (0.28, 0.28, 6.6), (0, 0, 3.3)), box('arm', (7.4, 0.16, 0.2), (3.7, 0, 6.2)),
               box('hanger0', (0.06, 0.06, 1.15), (3.4, 0, 5.6)), box('hanger1', (0.06, 0.06, 1.15), (6.6, 0, 5.6))] + heads,
              [{'part_ids': ['pole', 'arm', 'hanger0', 'hanger1'], **STEEL},
               {'part_ids': ['head0', 'head1'], 'color_srgb': [0.05, 0.05, 0.05], 'roughness': 0.6}] + mats)

# 8. Pedestrian signal: 2.4 m post, head with red (lit) over green figure, facing +Y
ped_signal = spec('ped_signal', 'pedestrian signal post 2.4 m with red/green head', '보행 신호등',
                  [{'phrase': '보행 신호등', 'items': ['post', 'head', 'dim.height', 'feat.head']}],
                  [DESIGN, KS],
                  [{'id': 'dim.height', 'value_m': 3.1, 'tol_pct': 1.0, 'source_id': 'design', 'measure': 'z'}],
                  [present('feat.head', 'pedestrian head', ['head']), present('feat.lamps', 'signal lamps', ['red'])],
                  [box('post', (0.12, 0.12, 2.4), (0, 0, 1.2)), box('head', (0.36, 0.25, 0.72), (0, 0, 2.74), features=['feat.head']),
                   box('red', (0.28, 0.05, 0.28), (0, 0.14, 2.92), features=['feat.lamps']), box('green', (0.28, 0.05, 0.28), (0, 0.14, 2.56))],
                  [{'part_ids': ['post', 'head'], 'color_srgb': [0.1, 0.1, 0.1], 'roughness': 0.6},
                   {'part_ids': ['red'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.1, 0.05], 'strength': 5.0}}, 'scene_role': 'light_fixture'},
                   {'part_ids': ['green'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [0.2, 1.0, 0.45], 'strength': LAMP_OFF}}, 'scene_role': 'light_fixture'}])


# 9. Bare winter street tree (the reference's December street): recursive tapered branches, 6 seeded variants
def _branch_rot(d):
    """rotation_deg (XYZ) turning local +Z onto direction d (no roll)."""
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, d[2]))))
    return [tilt, 0.0, math.degrees(math.atan2(d[0], -d[1]))]


def _turn(d, angle, around):
    """d rotated by angle (rad) about unit axis `around` (Rodrigues)."""
    c, s = math.cos(angle), math.sin(angle)
    k = around
    dot = sum(a * b for a, b in zip(k, d))
    cross = (k[1] * d[2] - k[2] * d[1], k[2] * d[0] - k[0] * d[2], k[0] * d[1] - k[1] * d[0])
    return tuple(d[i] * c + cross[i] * s + k[i] * dot * (1 - c) for i in range(3))


def bare_tree(variant):
    r = random.Random(f'bare_tree:{variant}')
    parts, tips = [], []

    def limb(pid, base, d, length, r0, r1):
        parts.append({'part_id': pid, 'builder': 'loft', 'dim_role': 'none', 'features': ['feat.branches'] if pid != 'trunk' else ['feat.trunk'],
                      'params': {'axis': 'z', 'segments': 6, 'cap_start': False, 'cap_end': True, 'smooth': True,
                                 'stations': [{'s': 0.0, 'section': {'type': 'ellipse', 'a': r0, 'b': r0}},
                                              {'s': length, 'section': {'type': 'ellipse', 'a': r1, 'b': r1}}]},
                      'transform': {'location': [round(c, 4) for c in base], 'rotation_deg': [round(a, 3) for a in _branch_rot(d)]}})
        tip = tuple(base[i] + d[i] * length for i in range(3))
        tips.append(tip)
        return tip

    trunk_h = r.uniform(2.8, 3.4)
    top = limb('trunk', (0, 0, 0), (0, 0, 1), trunk_h, 0.15, 0.1)
    count = 0
    for i in range(r.randint(3, 4)):
        az = 2 * math.pi * (i + r.uniform(-0.2, 0.2)) / 4
        tilt = math.radians(r.uniform(28, 45))
        d1 = (math.sin(tilt) * math.cos(az), math.sin(tilt) * math.sin(az), math.cos(tilt))
        base = (top[0], top[1], top[2] - r.uniform(0.0, 0.6))
        e1 = limb(f'b{i}', base, d1, r.uniform(2.2, 3.0), 0.08, 0.035)
        for j in range(r.randint(2, 3)):
            side = (-math.sin(az + j), math.cos(az + j), 0.0)
            d2 = _turn(d1, math.radians(r.uniform(18, 35)) * (1 if j % 2 else -1), side)
            e2 = limb(f'b{i}{j}', e1, d2, r.uniform(1.2, 1.9), 0.035, 0.015)
            for k in range(2):
                d3 = _turn(d2, math.radians(r.uniform(15, 30)) * (1 if k else -1), (-d2[1], d2[0], 0.0) if abs(d2[2]) < 0.99 else (1, 0, 0))
                limb(f'b{i}{j}{k}', e2, d3, r.uniform(0.6, 1.1), 0.015, 0.006)
                count += 1
    height = max(t[2] for t in tips)
    names = [p['part_id'] for p in parts]
    return spec(f'bare_tree_{variant}', f'bare deciduous street tree in winter, {len(parts)} tapered limbs, variant {variant}', '겨울 가로수',
                [{'phrase': '겨울 가로수', 'items': names[:3] + ['dim.height', 'feat.branches']}],
                [DESIGN, typical('tree', 'urban street trees (ginkgo, plane) 6-9 m, planted every 8 m; bare in December')],
                [{'id': 'dim.height', 'value_m': round(height, 3), 'tol_pct': 2.0, 'source_id': 'design', 'measure': 'z'}],
                [present('feat.trunk', 'trunk', ['trunk']), present('feat.branches', 'branch crown', [n for n in names if n != 'trunk'][:3])],
                parts, [{'part_ids': names, 'color_srgb': [0.17, 0.14, 0.12], 'roughness': 0.9}])


TREES = [bare_tree(v) for v in range(1, 7)]


# 10. Pedestrian silhouettes: box figure, 1.72 m, four poses (stand, two walk phases, stand with bag); front +Y
def figure(pose):
    legs = {'stand': (0, 0), 'walk_a': (22, -18), 'walk_b': (-18, 22), 'bag': (0, 4)}[pose]
    arms = {'stand': (4, -4), 'walk_a': (-20, 18), 'walk_b': (18, -20), 'bag': (2, -8)}[pose]
    out, hip_z, shoulder_z = [], 0.92, 1.42

    def limb(pid, x, pivot_z, length, thick, swing):
        a = math.radians(swing)
        cy, cz = math.sin(a) * length / 2, pivot_z - math.cos(a) * length / 2
        out.append({'part_id': pid, 'builder': 'box', 'dim_role': 'none', 'params': {'size': [thick, thick, length]},
                    'transform': {'location': [x, round(cy, 4), round(cz, 4)], 'rotation_deg': [-swing, 0, 0]}, 'features': ['feat.body']})
    limb('leg_l', -0.1, hip_z, 0.9, 0.14, legs[0]); limb('leg_r', 0.1, hip_z, 0.9, 0.14, legs[1])
    limb('arm_l', -0.25, shoulder_z, 0.62, 0.09, arms[0]); limb('arm_r', 0.25, shoulder_z, 0.62, 0.09, arms[1])
    out += [box('torso', (0.4, 0.24, 0.6), (0, 0, 1.17), features=['feat.body']), box('head', (0.19, 0.22, 0.24), (0, 0, 1.6), features=['feat.body'])]
    if pose == 'bag':
        out.append(box('bag', (0.12, 0.32, 0.3), (0.34, 0, 0.75)))
    lowest = min(b['transform']['location'][2] - (abs(math.cos(math.radians(b['transform'].get('rotation_deg', [0])[0]))) * b['params']['size'][2]
                 + abs(math.sin(math.radians(b['transform'].get('rotation_deg', [0])[0]))) * b['params']['size'][1]) / 2 for b in out)
    for b in out:   # a stride lifts the hips' lowest point: feet back on the ground, the figure a little shorter
        b['transform']['location'][2] = round(b['transform']['location'][2] - lowest, 4)
    height = round(1.72 - lowest, 4)
    names = [b['part_id'] for b in out]
    coat = {'stand': [0.1, 0.1, 0.12], 'walk_a': [0.22, 0.2, 0.18], 'walk_b': [0.08, 0.09, 0.13], 'bag': [0.3, 0.27, 0.24]}[pose]
    return spec(f'pedestrian_{pose}', f'pedestrian silhouette 1.72 m, pose {pose} (box figure for crowds, never a hero)', '보행자',
                [{'phrase': '보행자', 'items': ['torso', 'head', 'leg_l', 'dim.height', 'feat.body']}],
                [DESIGN, typical('person', 'adult height about 1.7 m')],
                [{'id': 'dim.height', 'value_m': height, 'tol_pct': 1.5, 'source_id': 'person', 'measure': 'z'}],
                [present('feat.body', 'figure', ['torso', 'head', 'leg_l'])], out,
                [{'part_ids': [n for n in names if n not in ('head', 'leg_l', 'leg_r')], 'color_srgb': coat, 'roughness': 0.8},
                 {'part_ids': ['leg_l', 'leg_r'], 'color_srgb': [0.06, 0.06, 0.07], 'roughness': 0.8},
                 {'part_ids': ['head'], 'color_srgb': [0.45, 0.35, 0.3], 'roughness': 0.7}])


PEOPLE = [figure(p) for p in ('stand', 'walk_a', 'walk_b', 'bag')]

# 11. City bus 12 x 2.5 x 3.2 m: lit window band both sides, destination sign, head/tail lights (front +Y)
bus = spec('bus', 'city bus 12 x 2.5 x 3.2 m with lit window band', '버스',
           [{'phrase': '버스', 'items': ['body', 'windows', 'headlights', 'dim.length', 'feat.lights']}],
           [DESIGN, typical('bus', 'standard city bus about 11-12 m long, 2.5 m wide, 3.2 m high')],
           [{'id': 'dim.length', 'value_m': 12.0, 'tol_pct': 0.5, 'source_id': 'bus', 'measure': 'y', 'part_ids': ['body']},
            {'id': 'dim.height', 'value_m': 3.2, 'tol_pct': 0.5, 'source_id': 'bus', 'measure': 'z'}],
           [present('feat.lights', 'head and tail lights', ['headlights', 'taillights']), present('feat.windows', 'lit window band', ['windows'])],
           [box('body', (2.5, 12.0, 2.85), (0, 0, 1.775)), box('windows', (2.54, 10.4, 1.0), (0, -0.4, 2.35), features=['feat.windows']),
            box('sign', (1.8, 0.05, 0.3), (0, 6.01, 2.95)),
            box('headlights', (2.0, 0.05, 0.15), (0, 6.01, 0.8), features=['feat.lights']), box('taillights', (2.0, 0.05, 0.15), (0, -6.01, 0.9), features=['feat.lights']),
            {'part_id': 'wheels', 'builder': 'array', 'dim_role': 'none',
             'params': {'pattern': 'grid', 'counts': [2, 2], 'pitch_m': [2.2, 6.0], 'axes': ['x', 'y'], 'center': [-1.1, -3.0, 0],
                        'item': {'builder': 'box', 'params': {'size': [0.3, 1.0, 1.0]}, 'transform': {'location': [0, 0, 0.5]}}}}],
           [{'part_ids': ['body'], 'color_srgb': [0.2, 0.45, 0.32], 'roughness': 0.35},
            {'part_ids': ['windows'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.92, 0.78], 'strength': 2.5}}, 'scene_role': 'light_fixture'},
            {'part_ids': ['sign'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.6, 0.15], 'strength': 6.0}}, 'scene_role': 'light_fixture'},
            {'part_ids': ['wheels'], 'color_srgb': [0.05, 0.05, 0.05], 'roughness': 0.8},
            {'part_ids': ['headlights'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.97, 0.9], 'strength': 14.0}}, 'scene_role': 'light_fixture'},
            {'part_ids': ['taillights'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.08, 0.05], 'strength': 8.0}}, 'scene_role': 'light_fixture'}])

# 12. Taxi: the car exemplar with a lit roof sign (data copy of the car spec, front +Y)
import copy
taxi = copy.deepcopy(car)
taxi.update({'subject_id': 'taxi', 'identity': 'taxi: mid-size car with a lit roof sign', 'request': '택시',
             'request_trace': [{'phrase': '택시', 'items': ['body', 'roof_sign', 'dim.length', 'feat.lights']}]})
taxi['builders'].append(box('roof_sign', (0.6, 0.25, 0.22), (0, -0.2, 1.56), features=['feat.lights']))
taxi['features'][0]['part_ids'] = ['headlights', 'taillights', 'roof_sign']
next(d for d in taxi['dimensions'] if d['id'] == 'dim.height')['value_m'] = 1.67   # roof sign top
taxi['materials'][0]['color_srgb'] = [0.85, 0.5, 0.18]
taxi['materials'].append({'part_ids': ['roof_sign'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.85, 0.5], 'strength': 6.0}}, 'scene_role': 'light_fixture'})

# 13. Bus shelter: roof, back glass, lit advertising panel (front, open side +Y towards the road)
shelter = spec('bus_shelter', 'bus shelter 4 x 1.6 x 2.6 m with lit advertising panel', '버스 정류장',
               [{'phrase': '버스 정류장', 'items': ['roof', 'back', 'ad', 'dim.height', 'feat.ad']}],
               [DESIGN],
               [{'id': 'dim.height', 'value_m': 2.6, 'tol_pct': 1.0, 'source_id': 'design', 'measure': 'z'}],
               [present('feat.ad', 'lit advertising panel', ['ad'])],
               [box('roof', (4.0, 1.6, 0.12), (0, 0, 2.54)), box('back', (4.0, 0.05, 2.1), (0, -0.75, 1.25)),
                box('post_l', (0.1, 0.1, 2.5), (-1.95, -0.75, 1.25)), box('post_r', (0.1, 0.1, 2.5), (1.95, -0.75, 1.25)),
                box('ad', (0.08, 1.2, 1.8), (1.9, 0.0, 1.2), features=['feat.ad']), box('bench', (2.4, 0.4, 0.45), (-0.6, -0.5, 0.225))],
               [{'part_ids': ['roof', 'post_l', 'post_r', 'bench'], **STEEL}, {'part_ids': ['back'], 'color_srgb': [0.6, 0.7, 0.75], 'roughness': 0.1},
                {'part_ids': ['ad'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [0.95, 0.95, 1.0], 'strength': 4.0}}, 'scene_role': 'light_fixture'}])

SUBJECTS = [tower, light, car, sign, roof, dash, signal, ped_signal, *TREES, *PEOPLE, bus, taxi, shelter]
PLACES = {'tower_block': (0, 0, 0), 'streetlight': (30, 0, 0), 'car': (40, 0, 0), 'sign_panel': (50, 0, 0), 'rooftop_unit': (60, 0, 0), 'lane_dash': (70, 0, 0),
          'traffic_signal': (80, 0, 0), 'ped_signal': (92, 0, 0), 'bus': (100, 0, 0), 'taxi': (108, 0, 0), 'bus_shelter': (116, 0, 0),
          **{t['subject_id']: (130 + 10 * i, 0, 0) for i, t in enumerate(TREES)}, **{f['subject_id']: (195 + 3 * i, 0, 0) for i, f in enumerate(PEOPLE)}}

if __name__ == '__main__':
    if P.exists():
        shutil.rmtree(P)
    (P / 'shots/kits').mkdir(parents=True)
    for name in ('project.json', 'style.json'):
        shutil.copy(SRC / name, P / name)
    project = json.loads((P / 'project.json').read_text())
    project['project_id'] = 'environment_kits'
    project['shots'] = [{'shot_id': 'kits', 'start_frame': 0, 'frame_count': 30}]
    (P / 'project.json').write_text(json.dumps(project, indent=2))
    (P / 'sources.json').write_text(json.dumps({'schema_version': 1, 'sources': [], 'claims': []}))
    for s in SUBJECTS:
        (P / 'subjects' / s['subject_id']).mkdir(parents=True)
        (P / 'subjects' / s['subject_id'] / 'spec.json').write_text(json.dumps(s, indent=2, ensure_ascii=False))
    shot = json.loads((SRC / 'shot.json').read_text())
    shot.update({'shot_id': 'kits', 'revision': 1, 'scene_version': None, 'goal': 'Environment-kit exemplars from spec data only; internal.',
                 'subjects': [{'subject_id': s['subject_id']} for s in SUBJECTS]})
    shot['camera']['keys'] = [{'frame': 0, 'location': [35, -90, 50], 'target': [35, 0, 15], 'lens_mm': 30}]
    for key in ('route',):
        shot.pop(key, None)
    (P / 'shots/kits/shot.json').write_text(json.dumps(shot, indent=2))
    (P / 'places.json').write_text(json.dumps(PLACES))
    print(json.dumps({'project': str(P), 'subjects': [s['subject_id'] for s in SUBJECTS]}))
