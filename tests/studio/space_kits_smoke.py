"""Space kits (2026-10-08): a columned hall, layered ground, trees and water build from shot.scene.kits by name - the
same dispatch as the street - with their columns on the bay grid, strata stacked without gaps, and no mesh left
without a material. Run: .venv/bin/python tests/studio/space_kits_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.layout import lint
from studio.project import init_project, shot_path

SCENE = {'world': {'kind': 'blockout', 'color': [0.3, 0.32, 0.36], 'strength': 0.8, 'samples': 16},
         'kits': [{'id': 'hall', 'kit': 'hall', 'args': {'box': [[-12, 0, 0], [12, 32, 7]], 'bay_m': [8, 8], 'rail': {'y': 4, 'x_range': [-6, 6]}}},
                  {'id': 'ground', 'kit': 'strata', 'args': {'box': [[-12, -10, -14], [12, -0.5, 0]]}},
                  {'id': 'park', 'kit': 'vegetation', 'args': {'area': [[-30, -40], [30, -15], 0], 'per_100m2': 0.4, 'keep_clear': [[-5, -40, 5, -15]]}},
                  {'id': 'pond', 'kit': 'water', 'args': {'area': [[-4, -38], [4, -20]], 'z': 0.05}}]}

checks = []
with tempfile.TemporaryDirectory(prefix='space-kits-smoke-') as root:
    p = Path(init_project('kits', {'request': 'space kits smoke', 'shots': [{'shot_id': 's', 'frame_count': 24}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'screen': {'subject': ['hall']},
                 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
                            'keys': [{'frame': 0, 'location': [0, -30, 9], 'target': [0, 10, 2]}, {'frame': 23, 'location': [0, -28, 9], 'target': [0, 10, 2]}]}})
    write_json(shot_path(p, 's'), shot)
    bad = json.loads(json.dumps(shot)); bad['scene']['kits'][0]['args'].pop('box')
    assert any('needs args.box' in e for e in lint(p, bad)['errors']), lint(p, bad)
    bad['scene']['kits'][0]['args'] = {'box': [[0, 0, 0], [1, 1, 1]], 'colour': 1}
    assert any('kits/hall/args/colour' in e for e in lint(p, bad)['errors']), lint(p, bad)
    checks.append('required_and_unread_kit_args_linted')
    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    kits = {k['id']: k for k in read_json(version / 'layout_report.json')['kits']}
    assert kits['hall']['columns'] == 12 and kits['hall']['bays'] == [8.0, 8.0] and kits['hall']['light_rows'] == 2, kits['hall']
    layers = kits['ground']['layers']
    assert layers[0]['top_z'] == 0 and layers[-1]['bottom_z'] == -14 and all(a['bottom_z'] == b['top_z'] for a, b in zip(layers, layers[1:])), layers
    assert kits['park']['trees'] == 6 and kits['pond']['area_m2'] == 144.0   # 1500 m2 x 0.4 per 100 m2, (kits['park'], kits['pond'])
    assert not any(w.startswith('MATERIAL_MISSING') for w in built['warnings']), built['warnings']
    inventory = read_json(version / 'inventory.json')
    bare = [o['studio_id'] for o in inventory['objects'] if o['type'] == 'MESH' and str(o.get('studio_id', '')).startswith(('hall.', 'ground.', 'pond')) and not o['materials']]
    assert not bare, bare[:5]
    checks.append(f"hall_strata_trees_water_built ({kits['hall']['columns']} columns, {len(layers)} strata, {kits['park']['trees']} trees)")
print('STUDIO_SPACE_KITS_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
