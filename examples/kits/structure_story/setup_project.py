"""Exemplars a structure story fills its levels with (spec data only): a rebar column in cutaway, a platform screen door
module, a metro car. Made for the samsung_cutaway column story, usable by any section that explains columns or a
railway level.

Run from the repo root: .venv/bin/python examples/kits/structure_story/setup_project.py
then build: .venv/bin/python -m studio shot build --project projects/harness_validation/structure_story --shot kit
                --script examples/kits/building_elements/author_elements.py
and promote each subject (`subject promote`). Base at z = 0, front along +Y (fill layouts turn them).
"""
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
P = ROOT / 'projects/harness_validation/structure_story'
TEMPLATE = Path(__file__).parents[1] / 'template'
DESIGN = {'id': 'design', 'kind': 'measurement', 'license': 'own schematic design'}


def typical(id_, text):
    return {'id': id_, 'kind': 'standard', 'license': 'typical value (agent recall; human check required)', 'note': text}


def spec(subject_id, identity, request, trace, sources, dimensions, features, builders, materials):
    return {'schema_version': 1, 'subject_id': subject_id, 'identity': identity, 'subject_mode': 'schematic', 'request': request,
            'request_trace': trace, 'sources': sources, 'dimensions': dimensions, 'features': features, 'builders': builders,
            'materials': materials}


def box(part, size, loc, **extra):
    return {'part_id': part, 'builder': 'box', 'dim_role': 'none', 'params': {'size': list(size)}, 'transform': {'location': list(loc)}, **extra}


CONCRETE = {'color_srgb': [0.66, 0.66, 0.64], 'roughness': 0.9}
REBAR = {'color_srgb': [0.3, 0.17, 0.1], 'metallic': 0.6, 'roughness': 0.6}
STEEL = {'color_srgb': [0.55, 0.57, 0.6], 'metallic': 1, 'roughness': 0.4}
GLASS = {'color_srgb': [0.55, 0.65, 0.7], 'roughness': 0.05}

# 1. Rebar column, cutaway: D1.2 m round column 6.2 m, a quarter cut away to show 16 main bars (lap 0.4 m above) and ties
H, R, COVER, BARS, TIE_PITCH = 6.2, 0.6, 0.075, 16, 0.3
bar_r = R - COVER - 0.016
ties = int((H - 0.2) / TIE_PITCH) + 1
tie_ring = [[bar_r + 0.03 + 0.008 * math.cos(a), 0.008 * math.sin(a)] for a in [k * math.pi / 4 for k in range(8)]]
column = spec('rebar_column', 'reinforced concrete column D1.2 m, 6.2 m, quarter cut away: 16 main bars D32, ties every 0.3 m', '철근 기둥',
              [{'phrase': '철근', 'items': ['main_bars', 'ties', 'feat.bars']}, {'phrase': '기둥', 'items': ['concrete', 'dim.height']}],
              [DESIGN, typical('column', 'station columns of about 1-1.2 m diameter with 16-24 main bars D29-D35 and ties at 0.3 m or less')],
              [{'id': 'dim.height', 'value_m': H + 0.4, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'z'},
               {'id': 'dim.diameter', 'value_m': 2 * R, 'tol_pct': 0.5, 'source_id': 'column', 'measure': 'x', 'part_ids': ['concrete']}],
              [{'id': 'feat.bars', 'description': f'{BARS} main bars', 'part_ids': ['main_bars'], 'verify': 'count', 'count': BARS},
               {'id': 'feat.ties', 'description': 'ties along the height', 'part_ids': ['ties'], 'verify': 'count', 'count': ties},
               {'id': 'feat.cut', 'description': 'concrete cut away to show the cage', 'part_ids': ['concrete'], 'verify': 'presence'}],
              [{'part_id': 'concrete', 'builder': 'revolve', 'dim_role': 'none', 'features': ['feat.cut'],
                'params': {'profile': [[0, 0], [R, 0], [R, H], [0, H]], 'segments': 48, 'angle_deg': 270, 'smooth': True},
                'transform': {'rotation_deg': [0, 0, 135]}},   # the open quarter faces local +Y: place with facing 'face' so it opens toward the section
               {'part_id': 'main_bars', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.bars'],
                'params': {'pattern': 'rotational', 'count': BARS, 'axis': 'z',
                           'item': {'builder': 'box', 'params': {'size': [0.032, 0.032, H + 0.4]}, 'transform': {'location': [bar_r, 0, (H + 0.4) / 2]}}}},
               {'part_id': 'ties', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.ties'],
                'params': {'pattern': 'path', 'points': [[0, 0, 0.1], [0, 0, 0.1 + TIE_PITCH * (ties - 1)]], 'pitch_m': TIE_PITCH, 'count': ties, 'orient': 'fixed',
                           'item': {'builder': 'revolve', 'params': {'profile': tie_ring, 'closed_profile': True, 'segments': 32}}}}],
              [{'part_ids': ['concrete'], **CONCRETE}, {'part_ids': ['main_bars', 'ties'], **REBAR}])

# 2. Platform screen door module: 10 m, 5 glass doors between 6 posts, header with LED strip (row along local X)
MOD, DOORS = 10.0, 5
psd = spec('platform_screen_doors', f'platform screen door module {MOD:.0f} m: {DOORS} glass leaves, posts, header with LED strip', '스크린도어',
           [{'phrase': '스크린도어', 'items': ['posts', 'panels', 'header', 'dim.length', 'feat.panels']}],
           [DESIGN, typical('psd', 'full-height platform screen doors about 2.1-2.5 m high, leaves about 1.8-2 m')],
           [{'id': 'dim.length', 'value_m': MOD + 0.12, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x'},
            {'id': 'dim.height', 'value_m': 2.7, 'tol_pct': 0.5, 'source_id': 'psd', 'measure': 'z'}],
           [{'id': 'feat.panels', 'description': f'{DOORS} glass leaves', 'part_ids': ['panels'], 'verify': 'count', 'count': DOORS},
            {'id': 'feat.led', 'description': 'lit header strip', 'part_ids': ['led'], 'verify': 'presence'}],
           [{'part_id': 'posts', 'builder': 'array', 'dim_role': 'none',
             'params': {'pattern': 'path', 'points': [[-MOD / 2, 0, 0], [MOD / 2, 0, 0]], 'pitch_m': MOD / DOORS, 'count': DOORS + 1, 'orient': 'fixed',
                        'item': {'builder': 'box', 'params': {'size': [0.12, 0.25, 2.3]}, 'transform': {'location': [0, 0, 1.15]}}}},
            {'part_id': 'panels', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.panels'],
             'params': {'pattern': 'path', 'points': [[-MOD / 2 + MOD / DOORS / 2, 0, 0], [MOD / 2 - MOD / DOORS / 2, 0, 0]], 'pitch_m': MOD / DOORS, 'count': DOORS, 'orient': 'fixed',
                        'item': {'builder': 'box', 'params': {'size': [MOD / DOORS - 0.16, 0.03, 2.1]}, 'transform': {'location': [0, 0, 1.1]}}}},
            box('header', (MOD + 0.12, 0.3, 0.4), (0, 0, 2.5)),
            box('led', (MOD * 0.6, 0.02, 0.12), (0, 0.16, 2.5), features=['feat.led'])],
           [{'part_ids': ['posts', 'header'], **STEEL}, {'part_ids': ['panels'], **GLASS},
            {'part_ids': ['led'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [0.4, 0.9, 1.0], 'strength': 4.0}}, 'scene_role': 'light_fixture'}])

# 3. Metro car: 20 m body on 0.9 m running gear, lit window band, rounded cab (front +Y)
L, W, HC = 20.0, 3.0, 3.6
car = spec('train_car', 'metro car 20 x 3.0 m with lit window band and rounded cab', '열차',
           [{'phrase': '열차', 'items': ['body', 'windows', 'cab', 'dim.length', 'feat.windows']}],
           [DESIGN, typical('car', 'metro / GTX cars about 20-22 m long, 3.0-3.2 m wide')],
           [{'id': 'dim.length', 'value_m': L, 'tol_pct': 0.5, 'source_id': 'car', 'measure': 'y', 'part_ids': ['body']},
            {'id': 'dim.width', 'value_m': W + 0.04, 'tol_pct': 0.5, 'source_id': 'car', 'measure': 'x'}],
           [{'id': 'feat.windows', 'description': 'lit window band', 'part_ids': ['windows'], 'verify': 'presence'},
            {'id': 'feat.bogies', 'description': 'two bogies', 'part_ids': ['bogies'], 'verify': 'count', 'count': 2}],
           [box('body', (W, L, HC), (0, 0, 0.9 + HC / 2)),
            box('windows', (W + 0.04, L - 2.0, 0.9), (0, 0, 0.9 + HC * 0.62), features=['feat.windows']),
            {'part_id': 'cab', 'builder': 'loft', 'dim_role': 'none',
             'params': {'axis': 'y', 'segments': 24, 'stations': [{'s': 0.0, 'section': {'type': 'superellipse', 'a': W / 2, 'b': HC / 2, 'n': 4}},
                                                                   {'s': 1.0, 'section': {'type': 'superellipse', 'a': W / 2 - 0.25, 'b': HC / 2 - 0.3, 'n': 4}}]},
             'transform': {'location': [0, L / 2, 0.9 + HC / 2]}},
            {'part_id': 'bogies', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.bogies'],
             'params': {'pattern': 'path', 'points': [[0, -L * 0.35, 0], [0, L * 0.35, 0]], 'pitch_m': L * 0.7, 'count': 2, 'orient': 'fixed',
                        'item': {'builder': 'box', 'params': {'size': [2.4, 2.8, 0.8]}, 'transform': {'location': [0, 0, 0.45]}}}}],
           [{'part_ids': ['body', 'cab'], 'color_srgb': [0.78, 0.8, 0.82], 'roughness': 0.3},
            {'part_ids': ['windows'], 'shader': {'kind': 'emissive', 'params': {'color_srgb': [1.0, 0.95, 0.85], 'strength': 2.5}}, 'scene_role': 'light_fixture'},
            {'part_ids': ['bogies'], 'color_srgb': [0.08, 0.08, 0.08], 'roughness': 0.7}])

SUBJECTS = [column, psd, car]
PLACES = {'rebar_column': (0, 0, 0), 'platform_screen_doors': (10, 0, 0), 'train_car': (25, 0, 0)}

if __name__ == '__main__':
    if P.exists():
        shutil.rmtree(P)
    (P / 'shots/kit').mkdir(parents=True)
    for name in ('project.json', 'style.json'):
        shutil.copy(TEMPLATE / name, P / name)
    project = json.loads((P / 'project.json').read_text()); project['project_id'] = 'structure_story'
    (P / 'project.json').write_text(json.dumps(project, indent=2))
    (P / 'sources.json').write_text(json.dumps({'schema_version': 1, 'sources': [], 'claims': []}))
    for s in SUBJECTS:
        (P / 'subjects' / s['subject_id']).mkdir(parents=True)
        (P / 'subjects' / s['subject_id'] / 'spec.json').write_text(json.dumps(s, indent=2, ensure_ascii=False))
    shot = json.loads((TEMPLATE / 'shot.json').read_text())
    shot.update({'shot_id': 'kit', 'goal': 'Structure-story exemplars from spec data only; internal.', 'subjects': [{'subject_id': s['subject_id']} for s in SUBJECTS]})
    shot['camera']['keys'] = [{'frame': 0, 'location': [12, -40, 12], 'target': [12, 0, 2], 'lens_mm': 30}]
    (P / 'shots/kit/shot.json').write_text(json.dumps(shot, indent=2))
    (P / 'places.json').write_text(json.dumps(PLACES))
    print(json.dumps({'project': str(P), 'subjects': [s['subject_id'] for s in SUBJECTS], 'ties': ties}))
