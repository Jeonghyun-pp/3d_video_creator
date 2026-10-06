"""A shot built from data alone (no author script, builds only): a two-level box with columns repeated and mirrored,
exemplar columns from the library, declared levels, a section stage with a front cutter, a bound reveal target and a
section_push camera move. Columns yield to the fill brief on the level where it puts its subject columns.
Run: .venv/bin/python tests/studio/layout_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.layout import lint
from studio.project import init_project, shot_path

Y0, LEVEL_H = 40.0, 7.0
SCENE = {
    'world': {'kind': 'blockout', 'color': [0.2, 0.24, 0.4], 'strength': 0.5, 'samples': 16, 'sun': {'energy': 0.6, 'color': [1, 0.6, 0.35]}},
    'materials': {'concrete': {'color': [0.62, 0.62, 0.64]}, 'slab': {'color': [0.7, 0.7, 0.7]}, 'ground': {'color': [0.3, 0.3, 0.3]}},
    'volumes': [{'id': 'st.box', 'box': [[-10.6, Y0, -15.5], [10.6, Y0 + 40, 0.0]]}],
    'primitives': [
        {'id': 'ground', 'shape': 'box', 'size': [200, 140, 1], 'at': [0, -30, -0.5], 'material': 'ground'},
        {'id': 'st.wall', 'shape': 'box', 'size': [1.2, 40, 14], 'at': [10, Y0 + 20, -7], 'material': 'concrete', 'mirror_x': True},
        {'id': 'st.slab', 'shape': 'box', 'size': [8, 40, 0.8], 'at': [5, Y0 + 20, -LEVEL_H - 0.4], 'material': 'slab', 'mirror_x': True},
        {'id': 'st.col', 'shape': 'box', 'size': [1, 1, 6.2], 'at': [2, Y0 + 6, -3.9], 'material': 'concrete', 'mirror_x': True,
         'repeat': {'counts': [1, 4, 2], 'pitch_m': [0, 9, -LEVEL_H]}, 'level_by_z': ['L1', 'L2'], 'yields_to_fill': 'column'}],
    'instances': [{'id': 'rc', 'exemplar': 'rebar_column@v001', 'at': [5, Y0 + 10, -2 * LEVEL_H], 'repeat': {'counts': [1, 2, 1], 'pitch_m': [0, 18, 0]}}],
    'levels': [{'level_id': 'L1', 'z': 0.0, 'rects': [[-9, Y0, 9, Y0 + 40]]}, {'level_id': 'L2', 'z': -LEVEL_H, 'rects': [[-9, Y0, 9, Y0 + 40]]}],
    'section': {'id': 'st.section', 'box': 'st.box', 'ceilings': [-LEVEL_H - 0.8, -0.4 - 0.8],
                'front_cutter': {'id': 'ground.cutter', 'reach_m': 120, 'half_w_m': 80, 'z_lo': -40, 'z_hi': 0.5},
                'copy_materials': [{'id': 'section_cap', 'from': 'st.section.poche'}]},
    'bind': [{'select': 'ground', 'instance_id': 'road', 'part_id': 'slab'}]}
CAMERA = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'energy': 'high',
          'move': {'type': 'section_push', 'params': {'section': 'st.box', 'fill': 0.6, 'centre_v': 0.68, 'back_m': 50, 'above_m': 20,
                                                     'into_m': 4, 'inside_z': -5.0}, 'lens_mm': 24, 'framing': {'horizon_v': 0.4}}}
REVEAL = {'action_id': 'ground_cut', 'type': 'reveal', 'targets': [{'instance_id': 'road', 'part_id': 'slab'}], 'start_frame': 10, 'end_frame': 40,
          'easing': 'ease_in_out', 'params': {'cutter_object_id': 'ground.cutter', 'cap_material_id': 'section_cap', 'also_cut_overlapping': True,
                                               'cutter_keys': [{'t': 0, 'scale': [1, 0.02, 1]}, {'t': 1, 'scale': [1, 1, 1]}]}}
BRIEF = {'status': 'proposed', 'topic': 'columns', 'request_trace': [], 'levels': [
    {'level_id': 'L2', 'items': [{'item_id': 'cols', 'role': 'subject', 'element': 'rebar_column', 'layout': 'along_edge', 'pitch_m': 9,
                                  'why': 'the columns are the story', 'source': 'agent'}]},
    {'level_id': 'L1', 'void': True, 'note': 'top level, not part of the story', 'items': []}]}
PROBE = '''import bpy, json
names = sorted(o.name for o in bpy.data.objects)
print('PROBE ' + json.dumps({'names': names, 'road_target': [o.name for o in bpy.data.objects if o.get('studio_instance_id') == 'road'],
      'levels': json.loads(bpy.context.scene.get('studio_levels', '[]')) if isinstance(bpy.context.scene.get('studio_levels'), str) else None}))
'''

checks = []
with tempfile.TemporaryDirectory(prefix='layout-smoke-') as root:
    p = Path(init_project('layout_test', {'request': 'layout smoke', 'shots': [{'shot_id': 's', 'frame_count': 60}]}, root)['project_path'])
    project = read_json(p / 'project.json')   # a mechanics fixture, not a composition: its subject is small on purpose
    project['policy'] = {'gates': {'FRAME_SUBJECT_SMALL': 'warn'}}; write_json(p / 'project.json', project)
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'camera': CAMERA, 'actions': [REVEAL], 'fill_brief': BRIEF})
    write_json(shot_path(p, 's'), shot)
    checked = lint(p, shot)
    assert checked['errors'] == [] and checked['counts']['primitives'] == 1 + 2 + 2 + 8, checked   # ground, walls, slabs, cols: 4 per side on L1 only (L2 yields)
    assert checked['yielded'] == ['st.col@L2'], checked['yielded']
    checks.append('lint_and_yield_to_fill')

    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    deps = read_json(version / 'dependencies.json')
    assert deps['author_lines'] == 0 and deps['layout_sha256'] and set(deps['exemplar_specs']) == {'rc.0', 'rc.1'}, deps
    assert read_json(version / 'camera_rig_report.json')['gate_failures'] == []
    assert read_json(version / 'camera_move_report.json')['ok']
    checks += ['built_without_author_code', 'section_push_on_layout_volume']
    script = version.parent / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    state = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    assert {'st.col.0.0', 'st.col.0.0.m', 'st.wall.m', 'ground.cutter'} <= set(state['names']) and 'st.col.0.1' not in state['names'], state['names'][:40]
    assert state['road_target'] == ['ground'], state['road_target']
    checks.append('repeat_mirror_bind_in_scene')

    bad = json.loads(json.dumps(SCENE)); bad['instances'][0]['exemplar'] = 'no_such_thing@v001'
    shot['scene'] = bad; write_json(shot_path(p, 's'), shot)
    try:
        build_shot(p, 's', None); raise AssertionError('unknown exemplar accepted')
    except StudioError as error:
        assert error.code == 'LAYOUT_INVALID', error.code
    checks.append('unknown_exemplar_refused_before_blender')

print('STUDIO_LAYOUT_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'objects': len(state['names'])}))
