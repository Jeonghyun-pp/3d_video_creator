"""N+1 building test: KS H-beam column base (plate, anchor bolts, pedestal) + a wall from a DXF, specs only.

Run from the repo root: .venv/bin/python examples/kits/steel_joint/setup_project.py
Creates projects/harness_validation/steel_joint (project, shot, two subject specs, a DXF made with ezdxf).
No builder or relation code is written here: geometry is spec data only.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from studio.asset_factory.factory import cad_python  # noqa: E402
from studio.subject_dxf import from_dxf  # noqa: E402

P = ROOT / 'projects/harness_validation/steel_joint'
INPUTS = Path(__file__).parent
if P.exists():
    shutil.rmtree(P)
(P / 'shots/base').mkdir(parents=True)
TEMPLATE = Path(__file__).parents[1] / 'template'   # tracked minimal project/style/shot (examples/kits/template)
for name in ('project.json', 'style.json'):
    shutil.copy(TEMPLATE / name, P / name)
project = json.loads((P / 'project.json').read_text()); project['project_id'] = 'steel_joint'
project['shots'] = [{'shot_id': 'base', 'start_frame': 0, 'frame_count': 30}]
(P / 'project.json').write_text(json.dumps(project, indent=2))

HOLE, M24 = 0.033, 0.012  # oversize anchor-bolt hole, M24 shank radius
anchor = {'builder': 'revolve', 'params': {'axis': 'z', 'segments': 32, 'profile': [
    [0, -0.45], [M24, -0.45], [M24, 0.025], [0.02, 0.025], [0.02, 0.05], [M24, 0.05], [M24, 0.12], [0, 0.12]]}}
column_base = {
    'schema_version': 1, 'subject_id': 'column_base', 'identity': 'steel column base: KS H-300x300x10x15 on a base plate with four M24 anchor bolts in a concrete pedestal',
    'subject_mode': 'schematic', 'request': 'H형강 기둥 베이스플레이트 앵커볼트',
    'request_trace': [{'phrase': 'H형강 기둥', 'items': ['column', 'dim.column_depth']}, {'phrase': '베이스플레이트', 'items': ['base_plate', 'dim.plate']},
                      {'phrase': '앵커볼트', 'items': ['anchors', 'feat.anchors']}],
    'sources': [{'id': 'ks_d3502', 'kind': 'standard', 'license': 'dimension facts from the standard table (studio/asset_factory/tables/ks_d3502.json)'},
                {'id': 'design', 'kind': 'measurement', 'license': 'own schematic design'}],
    'dimensions': [{'id': 'dim.column_depth', 'value_m': 0.3, 'tol_pct': 0.5, 'source_id': 'ks_d3502', 'measure': 'y', 'part_ids': ['column']},
                   {'id': 'dim.plate', 'value_m': 0.5, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x', 'part_ids': ['base_plate']}],
    'features': [{'id': 'feat.anchors', 'description': 'four anchor bolts through the base plate into the pedestal', 'part_ids': ['anchors'], 'verify': 'count', 'count': 4},
                 {'id': 'feat.load_path', 'description': 'column bears on the plate, plate on the pedestal', 'part_ids': ['column', 'base_plate', 'pedestal'], 'verify': 'assembly'}],
    'builders': [
        {'part_id': 'pedestal', 'builder': 'box', 'dim_role': 'none', 'features': ['feat.load_path'], 'params': {'size': [0.8, 0.8, 0.6]}},
        {'part_id': 'base_plate', 'builder': 'wall', 'dim_role': 'steel_angle_or_bracket_plate', 'features': ['feat.load_path'],
         'params': {'length': 0.5, 'height': 0.5, 'thickness': 0.025, 'openings': [
             {'x': c - HOLE / 2, 'z': r - HOLE / 2, 'w': HOLE, 'h': HOLE} for c in (0.05, 0.45) for r in (0.05, 0.45)]},
         'transform': {'rotation_deg': [90, 0, 0]}},
        {'part_id': 'column', 'builder': 'profile', 'dim_role': 'wide_flange_beam', 'features': ['feat.load_path'],
         'params': {'profile': {'table': 'KS D 3502', 'designation': 'H-300x300x10x15'}, 'length': 3.0, 'axis': 'z'}},
        {'part_id': 'anchors', 'builder': 'array', 'dim_role': 'none', 'features': ['feat.anchors'],
         'params': {'pattern': 'grid', 'counts': [2, 2], 'pitch_m': [0.4, 0.4], 'axes': ['x', 'y'], 'item': anchor}},
    ],
    'relations': [
        {'type': 'through', 'a': 'base_plate/center', 'b': 'pedestal/center', 'axis': 'z', 'note': 'plate centred on the pedestal'},
        {'type': 'on_surface', 'a': 'base_plate/-z', 'b': 'pedestal', 'axis': '-z', 'note': 'plate bears on the pedestal'},
        {'type': 'through', 'a': 'column/center', 'b': 'base_plate/center', 'axis': 'z'},
        {'type': 'on_surface', 'a': 'column/-z', 'b': 'base_plate', 'axis': '-z', 'note': 'column welded on the plate'},
        {'type': 'through', 'a': 'anchors/center', 'b': 'base_plate/center', 'axis': 'z'},
        {'type': 'align', 'a': 'anchors/-z', 'b': 'base_plate/-z', 'axis': 'z', 'offset_m': [0, 0, -0.45], 'note': '450 mm embedment'},
    ],
    'assembly_claims': [
        {'id': 'column-on-plate', 'type': 'contact', 'a': 'column', 'b': 'base_plate'},
        {'id': 'plate-on-pedestal', 'type': 'contact', 'a': 'base_plate', 'b': 'pedestal'},
        {'id': 'anchors-through-plate', 'type': 'through', 'a': 'anchors', 'b': 'base_plate', 'axis': 'z'},
        {'id': 'anchors-clear-plate', 'type': 'no_interference', 'a': 'anchors', 'b': 'base_plate', 'tol_m': 0.0005},
        {'id': 'nut-wrench-clearance', 'type': 'clearance', 'a': 'anchors', 'b': 'column', 'value_m': 0.03},
        {'id': 'nothing-floats', 'type': 'no_floating'},
    ],
    'materials': [{'part_ids': ['pedestal'], 'color_srgb': [0.62, 0.62, 0.6], 'roughness': 0.9},
                  {'part_ids': ['base_plate', 'column', 'anchors'], 'color_srgb': [0.35, 0.37, 0.4], 'metallic': 1, 'roughness': 0.45}],
}
wall = {
    'schema_version': 1, 'subject_id': 'wall_a', 'identity': 'concrete wall A with a door and a window (from the CAD elevation)', 'subject_mode': 'schematic',
    'request': '문과 창이 있는 벽', 'request_trace': [{'phrase': '문과 창이 있는 벽', 'items': ['wall', 'feat.openings']}],
    'sources': [{'id': 'design', 'kind': 'measurement', 'license': 'own schematic design'}],
    'dimensions': [{'id': 'dim.length', 'value_m': 6.0, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'x', 'part_ids': ['wall']},
                   {'id': 'dim.height', 'value_m': 3.0, 'tol_pct': 0.5, 'source_id': 'design', 'measure': 'z', 'part_ids': ['wall']}],
    'features': [{'id': 'feat.openings', 'description': 'door 1.0x2.1 and window 2.0x1.2', 'part_ids': ['wall'], 'verify': 'silhouette'}],
    'builders': [{'part_id': 'wall', 'builder': 'wall', 'dim_role': 'concrete_wall_or_slab', 'features': ['feat.openings'],
                  'params': {'length': 1.0, 'height': 1.0, 'thickness': 0.2, 'openings': []}}],
    'axes': {'length': 'y', 'width': 'x', 'height': 'z'},
}
for spec in (column_base, wall):
    (P / 'subjects' / spec['subject_id']).mkdir(parents=True)
    (P / 'subjects' / spec['subject_id'] / 'spec.json').write_text(json.dumps(spec, indent=2, ensure_ascii=False))

dxf = INPUTS / 'wall_a_elevation.dxf'
subprocess.run([str(cad_python()), '-I', '-c', '''
import sys, ezdxf
doc = ezdxf.new(); doc.header['$INSUNITS'] = 4
m = doc.modelspace()
m.add_lwpolyline([(0, 0), (6000, 0), (6000, 3000), (0, 3000)], close=True, dxfattribs={'layer': 'A-WALL'})
m.add_lwpolyline([(1000, 0), (2000, 0), (2000, 2100), (1000, 2100)], close=True, dxfattribs={'layer': 'A-WALL'})
m.add_lwpolyline([(3000, 1000), (5000, 1000), (5000, 2200), (3000, 2200)], close=True, dxfattribs={'layer': 'A-WALL'})
m.add_text('WALL A', dxfattribs={'layer': 'A-ANNO'}).set_placement((100, 3200))
doc.saveas(sys.argv[1])
''', str(dxf)], check=True)
result = from_dxf(P, 'wall_a', dxf, 'A-WALL', view='front', license='own schematic CAD drawing', apply=True)
spec = json.loads((P / 'subjects/wall_a/spec.json').read_text())
spec['builders'][0]['params'].update(result['wall'])  # numbers come from the CAD outlines, not typed
(P / 'subjects/wall_a/spec.json').write_text(json.dumps(spec, indent=2, ensure_ascii=False))

shot = json.loads((TEMPLATE / 'shot.json').read_text())
shot.update({'shot_id': 'base', 'revision': 1, 'scene_version': None, 'goal': 'N+1 building test: column base + CAD wall from specs only; internal.',
             'subjects': [{'subject_id': 'column_base'}, {'subject_id': 'wall_a'}]})
shot['camera']['keys'] = [{'frame': 0, 'location': [6.5, -7.5, 4.0], 'target': [1.8, 0.5, 1.2], 'lens_mm': 35}]
(P / 'shots/base/shot.json').write_text(json.dumps(shot, indent=2))
print(json.dumps({'project': str(P), 'wall_params': result['wall'], 'silhouette': result['silhouette']}))
