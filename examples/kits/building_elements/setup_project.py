"""Building-element exemplars as spec data only (no element-specific builder code).

Run from the repo root: .venv/bin/python examples/kits/building_elements/setup_project.py
Creates projects/harness_validation/building_elements: one shot holding eight subjects - stair, escalator,
glass railing, beam-grid ceiling, light row, track, slab with an opening, and (N+1, added last, no code)
a ventilation duct with hangers. Every element is made of box/profile/wall/sweep plus grid/path arrays
of single parts or 'group' assemblies. Code values (riser, railing height, escalator incline, gauge) are
agent recall of the cited standards and must be checked by a human before publication.
"""
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

P = ROOT / 'projects/harness_validation/building_elements'
SRC = Path(__file__).parents[1] / 'template'   # tracked minimal project/style/shot
DESIGN = {'id': 'design', 'kind': 'measurement', 'license': 'own schematic design'}


def code(id_, text):
    return {'id': id_, 'kind': 'standard', 'license': 'design rule value (agent recall; human check required)', 'note': text}


def spec(subject_id, identity, request, trace, sources, dimensions, features, builders, materials, **extra):
    return {'schema_version': 1, 'subject_id': subject_id, 'identity': identity, 'subject_mode': 'schematic', 'request': request,
            'request_trace': trace, 'sources': sources, 'dimensions': dimensions, 'features': features, 'builders': builders,
            'materials': materials, **extra}


CONCRETE = {'color_srgb': [0.62, 0.62, 0.6], 'roughness': 0.9}
STEEL = {'color_srgb': [0.35, 0.37, 0.4], 'metallic': 1, 'roughness': 0.45}
GLASS = {'color_srgb': [0.75, 0.85, 0.88], 'roughness': 0.05}

# 1. Stair: 16 risers of 170 mm, 280 mm treads, 1.5 m wide (riser <= 180 mm rule) -------------------------
RISE, TREAD, N = 0.17, 0.28, 16
stair = spec('stair', 'straight concrete stair flight, 16 risers 170 mm, treads 280 mm, 1.5 m wide', '콘크리트 계단',
             [{'phrase': '콘크리트 계단', 'items': ['steps', 'dim.flight_rise', 'feat.steps']}],
             [DESIGN, code('kr_stair', 'Korean building code: riser <= 180 mm, tread >= 260 mm')],
             [{'id': 'dim.flight_rise', 'value_m': RISE * N, 'tol_pct': 0.5, 'source_id': 'kr_stair', 'measure': 'z', 'part_ids': ['steps']},
              {'id': 'dim.width', 'value_m': 1.5, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x', 'part_ids': ['steps']}],
             [{'id': 'feat.steps', 'description': '16 steps', 'part_ids': ['steps'], 'verify': 'count', 'count': N}],
             [{'part_id': 'steps', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.steps'],
               'params': {'pattern': 'path', 'points': [[0, 0, 0], [0, TREAD * (N - 1), RISE * (N - 1)]], 'pitch_m': math.hypot(TREAD, RISE), 'count': N,
                          'orient': 'fixed', 'item': {'builder': 'box', 'params': {'size': [1.5, TREAD + 0.01, RISE]}, 'transform': {'location': [0, 0, RISE / 2]}}}}],
             [{'part_ids': ['steps'], **CONCRETE}])

# 2. Escalator: S1000 (1.0 m step), 30 deg, one 7 m storey; tread + riser groups along the incline ------------
INCL = math.radians(30)
RUN = 7.0 / math.tan(INCL)
PITCH = 0.4 / math.cos(INCL)
STEPS = int(math.hypot(RUN, 7.0) / PITCH) + 1
balustrade = [[-1.5, 0.0], [0.0, 0.0], [RUN, 7.0], [RUN + 1.5, 7.0], [RUN + 1.5, 8.0], [RUN, 8.0], [0.0, 1.0], [-1.5, 1.0]]
escalator = spec('escalator', 'S1000 escalator, 30 deg, 7 m rise: steps, glass balustrades, handrails, truss', '에스컬레이터',
                 [{'phrase': '에스컬레이터', 'items': ['steps', 'balustrade_l', 'handrail_l', 'truss', 'feat.steps', 'dim.step_width']}],
                 [DESIGN, code('en115', 'EN 115-1: inclination <= 30 deg, nominal step width 0.58-1.10 m')],
                 [{'id': 'dim.step_width', 'value_m': 1.0, 'tol_pct': 0.5, 'source_id': 'en115', 'measure': 'x', 'part_ids': ['steps']},
                  {'id': 'dim.truss_width', 'value_m': 1.4, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x', 'part_ids': ['truss']}],
                 [{'id': 'feat.steps', 'description': f'{STEPS} steps on the incline', 'part_ids': ['steps'], 'verify': 'count', 'count': STEPS},
                  {'id': 'feat.handrails', 'description': 'handrails on both balustrades', 'part_ids': ['handrail_l', 'handrail_r'], 'verify': 'presence'}],
                 [{'part_id': 'steps', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.steps'],
                   'params': {'pattern': 'path', 'points': [[0, 0, 0], [0, RUN, 7.0]], 'pitch_m': PITCH, 'orient': 'fixed',
                              'item': {'builder': 'group', 'params': {'items': [
                                  {'builder': 'box', 'params': {'size': [1.0, 0.4, 0.03]}},
                                  {'builder': 'box', 'params': {'size': [1.0, 0.02, 0.21]}, 'transform': {'location': [0, -0.2, -0.105]}}]}}}},
                  {'part_id': 'balustrade_l', 'builder': 'profile', 'dim_role': 'none', 'params': {'profile': balustrade, 'length': 0.02, 'axis': 'x'},
                   'transform': {'location': [-0.55, 0, 0]}},
                  {'part_id': 'balustrade_r', 'builder': 'mirror', 'params': {'source': 'balustrade_l', 'axis': 'x'}},
                  {'part_id': 'handrail_l', 'builder': 'sweep', 'dim_role': 'none', 'features': ['feat.handrails'],
                   'params': {'profile': {'type': 'circle', 'r': 0.04}, 'path': [[-0.55, -1.5, 1.04], [-0.55, 0, 1.04], [-0.55, RUN, 8.04], [-0.55, RUN + 1.5, 8.04]]}},
                  {'part_id': 'handrail_r', 'builder': 'mirror', 'features': ['feat.handrails'], 'params': {'source': 'handrail_l', 'axis': 'x'}},
                  {'part_id': 'truss', 'builder': 'profile', 'dim_role': 'none',
                   'params': {'profile': [[-1.5, -1.0], [0, -1.0], [RUN, 6.0], [RUN + 1.5, 6.0], [RUN + 1.5, 6.75], [RUN, 6.75], [0, -0.25], [-1.5, -0.25]],
                              'length': 1.4, 'axis': 'x', 'centered': True}}],
                 [{'part_ids': ['steps', 'truss'], **STEEL}, {'part_ids': ['balustrade_l', 'balustrade_r'], **GLASS},
                  {'part_ids': ['handrail_l', 'handrail_r'], 'color_srgb': [0.05, 0.05, 0.05], 'roughness': 0.4}])

# 3. Glass railing: posts every 1.5 m, glass infill, round top rail, 1.2 m high ---------------------------
railing = spec('glass_railing', 'glass balustrade 6 m long, 1.2 m high: posts at 1.5 m, glass panels, top rail', '유리 난간',
               [{'phrase': '유리 난간', 'items': ['posts', 'panels', 'top_rail', 'dim.height', 'feat.posts']}],
               [DESIGN, code('kr_guard', 'Korean building code: guard height >= 1.2 m at floor edges')],
               [{'id': 'dim.height', 'value_m': 1.2, 'tol_pct': 0.5, 'source_id': 'kr_guard', 'measure': 'z', 'part_ids': ['posts']},
                {'id': 'dim.length', 'value_m': 6.05, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x', 'part_ids': ['posts']}],
               [{'id': 'feat.posts', 'description': 'five posts', 'part_ids': ['posts'], 'verify': 'count', 'count': 5},
                {'id': 'feat.panels', 'description': 'four glass panels', 'part_ids': ['panels'], 'verify': 'count', 'count': 4}],
               [{'part_id': 'posts', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.posts'],
                 'params': {'pattern': 'path', 'points': [[0, 0, 0], [6, 0, 0]], 'pitch_m': 1.5,
                            'item': {'builder': 'box', 'params': {'size': [0.05, 0.05, 1.2]}, 'transform': {'location': [0, 0, 0.6]}}}},
                {'part_id': 'panels', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.panels'],
                 'params': {'pattern': 'path', 'points': [[0, 0, 0], [6, 0, 0]], 'pitch_m': 1.5, 'start_m': 0.75, 'count': 4,
                            'item': {'builder': 'box', 'params': {'size': [0.012, 1.4, 1.0]}, 'transform': {'location': [0, 0, 0.55]}}}},
                {'part_id': 'top_rail', 'builder': 'sweep', 'dim_role': 'none',
                 'params': {'profile': {'type': 'circle', 'r': 0.025}, 'path': [[0, 0, 1.2], [6, 0, 1.2]]}}],
               [{'part_ids': ['posts', 'top_rail'], **STEEL}, {'part_ids': ['panels'], **GLASS}])

# 4. Beam-grid ceiling: 18 x 18 m, H-400x200 at 9 m both ways, 300 mm slab on top ------------------------------
ceiling = spec('beam_grid_ceiling', 'beam grid ceiling: KS H-400x200 girders at 9 m both ways under a 300 mm slab', '보 격자 천장',
               [{'phrase': '보 격자 천장', 'items': ['beams_x', 'beams_y', 'slab', 'dim.beam_depth', 'feat.grid']}],
               [DESIGN, {'id': 'ks_d3502', 'kind': 'standard', 'license': 'dimension facts from studio/asset_factory/tables/ks_d3502.json'}],
               [{'id': 'dim.beam_depth', 'value_m': 0.4, 'tol_pct': 0.5, 'source_id': 'ks_d3502', 'measure': 'z', 'part_ids': ['beams_x']},
                {'id': 'dim.bay', 'value_m': 18.0, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x', 'part_ids': ['beams_x']}],
               [{'id': 'feat.grid', 'description': 'three girders each way', 'part_ids': ['beams_x', 'beams_y'], 'verify': 'count', 'count': 6}],
               [{'part_id': 'beams_x', 'builder': 'array', 'dim_role': 'wide_flange_beam', 'features': ['feat.grid'],
                 'params': {'pattern': 'grid', 'counts': [3], 'pitch_m': [9.0], 'axes': ['y'], 'center': [0, -9, 0],
                            'item': {'builder': 'profile', 'params': {'profile': {'table': 'KS D 3502', 'designation': 'H-400x200x8x13'}, 'length': 18.0, 'axis': 'x', 'centered': True}}}},
                {'part_id': 'beams_y', 'builder': 'array', 'dim_role': 'wide_flange_beam', 'features': ['feat.grid'],
                 'params': {'pattern': 'grid', 'counts': [3], 'pitch_m': [9.0], 'axes': ['x'], 'center': [-9, 0, 0],
                            'item': {'builder': 'profile', 'params': {'profile': {'table': 'KS D 3502', 'designation': 'H-400x200x8x13'}, 'length': 17.8, 'axis': 'y', 'centered': True}}}},
                {'part_id': 'slab', 'builder': 'box', 'dim_role': 'none', 'params': {'size': [18.4, 18.4, 0.3]}, 'transform': {'location': [0, 0, 0.35]}}],
               [{'part_ids': ['beams_x', 'beams_y'], **STEEL}, {'part_ids': ['slab'], **CONCRETE}])

# 5. Light row: 1.2 m linear fixtures every 2.4 m, self-lit ----------------------------------------------
lights = spec('light_row', 'row of six 1.2 m linear ceiling lights at 2.4 m', '선형 조명 열',
              [{'phrase': '선형 조명 열', 'items': ['fixtures', 'feat.fixtures', 'dim.fixture_width']}],
              [DESIGN, code('fixture', 'typical 1200 x 150 mm linear LED fixture')],
              [{'id': 'dim.fixture_width', 'value_m': 0.15, 'tol_pct': 0.5, 'source_id': 'fixture', 'measure': 'x', 'part_ids': ['fixtures']},
               {'id': 'dim.row_length', 'value_m': 13.2, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'y', 'part_ids': ['fixtures']}],
              [{'id': 'feat.fixtures', 'description': 'six fixtures', 'part_ids': ['fixtures'], 'verify': 'count', 'count': 6}],
              [{'part_id': 'fixtures', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.fixtures'],
                'params': {'pattern': 'path', 'points': [[0, 0, 0], [0, 12, 0]], 'pitch_m': 2.4,
                           'item': {'builder': 'box', 'params': {'size': [0.15, 1.2, 0.06]}}}}],
              [{'part_ids': ['fixtures'], 'color_srgb': [1.0, 0.96, 0.88], 'roughness': 0.5, 'emission_strength': 8.0}])

# 6. Track: two 50N rails at 1435 mm gauge on PC sleepers every 0.6 m -----------------------------------
HALF = (1.435 + 0.065) / 2  # rail centre = half gauge + half head width
track = spec('track', 'standard-gauge ballastless track: KS 50N rails at 1435 mm on PC sleepers every 600 mm', '선로',
             [{'phrase': '선로', 'items': ['rail_l', 'rail_r', 'sleepers', 'feat.sleepers', 'dim.rail_height']}],
             [DESIGN, {'id': 'ks_r9106', 'kind': 'standard', 'license': 'dimension facts from studio/asset_factory/tables/ks_r9106.json'}],
             [{'id': 'dim.rail_height', 'value_m': 0.153, 'tol_pct': 0.5, 'source_id': 'ks_r9106', 'measure': 'z', 'part_ids': ['rail_l']},
              {'id': 'dim.sleeper_length', 'value_m': 2.4, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x', 'part_ids': ['sleepers']}],
             [{'id': 'feat.sleepers', 'description': '50 sleepers', 'part_ids': ['sleepers'], 'verify': 'count', 'count': 50}],
             [{'part_id': 'sleepers', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.sleepers'],
               'params': {'pattern': 'path', 'points': [[0, 0, 0], [0, 30, 0]], 'pitch_m': 0.6, 'count': 50,
                          'item': {'builder': 'box', 'params': {'size': [2.4, 0.24, 0.17]}, 'transform': {'location': [0, 0, 0.085]}}}},
              {'part_id': 'rail_l', 'builder': 'profile', 'dim_role': 'none',
               'params': {'profile': {'table': 'KS R 9106', 'designation': '50N'}, 'length': 30.0, 'axis': 'y'}, 'transform': {'location': [-HALF, 0, 0.17]}},
              {'part_id': 'rail_r', 'builder': 'mirror', 'params': {'source': 'rail_l', 'axis': 'x'}}],
             [{'part_ids': ['rail_l', 'rail_r'], **STEEL}, {'part_ids': ['sleepers'], **CONCRETE}])

# 7. Slab with an opening: 12 x 8 m, 300 mm thick, 4 x 3 m void (wall builder laid flat) --------------------
slab = spec('slab_opening', 'concrete floor slab 12 x 8 m, 300 mm thick, with a 4 x 3 m opening', '개구부가 있는 슬래브',
            [{'phrase': '개구부가 있는 슬래브', 'items': ['slab', 'dim.thickness', 'feat.opening']}],
            [DESIGN, code('kr_slab', 'typical 300 mm transfer slab of a station box')],
            [{'id': 'dim.thickness', 'value_m': 0.3, 'tol_pct': 0.5, 'source_id': 'kr_slab', 'measure': 'z', 'part_ids': ['slab']},
             {'id': 'dim.length', 'value_m': 12.0, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x', 'part_ids': ['slab']}],
            [{'id': 'feat.opening', 'description': '4 x 3 m opening', 'part_ids': ['slab'], 'verify': 'presence'}],
            [{'part_id': 'slab', 'builder': 'wall', 'dim_role': 'concrete_wall_or_slab', 'features': ['feat.opening'],
              'params': {'length': 12.0, 'height': 8.0, 'thickness': 0.3, 'openings': [{'x': 4.0, 'z': 2.5, 'w': 4.0, 'h': 3.0}]},
              'transform': {'rotation_deg': [90, 0, 0]}}],
            [{'part_ids': ['slab'], **CONCRETE}])

# 8. N+1 (added after the seven above, no code change): ventilation duct with hangers -------------------------
DUCT = [[0, 0, 0], [8, 0, 0], [8, 6, 0]]
duct = spec('vent_duct', 'rectangular ventilation duct 600 x 400 mm with an L turn, threaded-rod hangers every 2 m', '환기 덕트',
            [{'phrase': '환기 덕트', 'items': ['duct', 'hangers', 'feat.hangers', 'dim.duct_height']}],
            [DESIGN, code('smacna', 'SMACNA-style rectangular duct 600 x 400 mm, hangers at <= 2.4 m')],
            [{'id': 'dim.duct_height', 'value_m': 0.4, 'tol_pct': 1.0, 'source_id': 'smacna', 'measure': 'z', 'part_ids': ['duct']},
             {'id': 'dim.run_x', 'value_m': 8.3, 'tol_pct': 1.0, 'source_id': 'design', 'measure': 'x', 'part_ids': ['duct']}],
            [{'id': 'feat.hangers', 'description': 'seven hanger pairs', 'part_ids': ['hangers'], 'verify': 'count', 'count': 7}],
            [{'part_id': 'duct', 'builder': 'sweep', 'dim_role': 'none',
              'params': {'profile': {'points': [[-0.3, -0.2], [0.3, -0.2], [0.3, 0.2], [-0.3, 0.2]]}, 'path': DUCT, 'smooth': False}},
             {'part_id': 'hangers', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.hangers'],
              'params': {'pattern': 'path', 'points': DUCT, 'pitch_m': 2.0, 'start_m': 1.0,
                         'item': {'builder': 'group', 'params': {'items': [
                             {'builder': 'box', 'params': {'size': [0.016, 0.016, 0.8]}, 'transform': {'location': [-0.34, 0, 0.6]}},
                             {'builder': 'box', 'params': {'size': [0.016, 0.016, 0.8]}, 'transform': {'location': [0.34, 0, 0.6]}},
                             {'builder': 'box', 'params': {'size': [0.76, 0.04, 0.04]}, 'transform': {'location': [0, 0, -0.22]}}]}}}}],
            [{'part_ids': ['duct', 'hangers'], 'color_srgb': [0.7, 0.72, 0.74], 'metallic': 1, 'roughness': 0.5}])

SUBJECTS = [stair, escalator, railing, ceiling, lights, track, slab, duct]
PLACES = {'stair': (0, 0, 0), 'escalator': (6, 0, 0), 'glass_railing': (-8, 0, 0), 'beam_grid_ceiling': (0, 40, 6), 'light_row': (-8, 8, 4),
          'track': (14, 0, 0), 'slab_opening': (-20, 20, 0), 'vent_duct': (-20, 0, 3)}

if __name__ == '__main__':
    if P.exists():
        shutil.rmtree(P)
    (P / 'shots/elements').mkdir(parents=True)
    for name in ('project.json', 'style.json'):
        shutil.copy(SRC / name, P / name)
    project = json.loads((P / 'project.json').read_text())
    project['project_id'] = 'building_elements'
    project['shots'] = [{'shot_id': 'elements', 'start_frame': 0, 'frame_count': 30}]
    (P / 'project.json').write_text(json.dumps(project, indent=2))
    (P / 'sources.json').write_text(json.dumps({'schema_version': 1, 'sources': [], 'claims': []}))
    for s in SUBJECTS:
        (P / 'subjects' / s['subject_id']).mkdir(parents=True)
        (P / 'subjects' / s['subject_id'] / 'spec.json').write_text(json.dumps(s, indent=2, ensure_ascii=False))
    shot = json.loads((SRC / 'shot.json').read_text())
    shot.update({'shot_id': 'elements', 'revision': 1, 'scene_version': None, 'goal': 'Building-element exemplars from spec data only; internal.',
                 'subjects': [{'subject_id': s['subject_id']} for s in SUBJECTS]})
    shot['camera']['keys'] = [{'frame': 0, 'location': [30, -40, 30], 'target': [0, 10, 2], 'lens_mm': 30}]
    (P / 'shots/elements/shot.json').write_text(json.dumps(shot, indent=2))
    (P / 'places.json').write_text(json.dumps(PLACES))
    print(json.dumps({'project': str(P), 'subjects': [s['subject_id'] for s in SUBJECTS], 'escalator_steps': STEPS}))
