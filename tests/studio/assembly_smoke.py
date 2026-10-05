"""Host smoke (needs Blender): relations place parts, assembly claims measure them, planted defects fail.

A bolted plate on a stand plus a core embedded in a shell, built only from relations (no typed
positions for the plate, bolts or stand). The correct build passes every claim; each planted defect
(bolt shifted into its hole wall, plate lifted off the stand, stand pushed into the plate, cover too
thin, shell moved too close) fails exactly the claim that describes it.
Run: .venv/bin/python tests/studio/assembly_smoke.py
"""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import write_json  # noqa: E402
from studio.fidelity import build_report  # noqa: E402
from studio.subject_fit import SubjectSession  # noqa: E402
from studio.subjects import lint_spec  # noqa: E402

HOLE, SHANK = 0.024, 0.020  # 2 mm clearance per side
bolt = {'builder': 'revolve', 'params': {'axis': 'y', 'segments': 32, 'profile': [[0, -0.03], [SHANK / 2, -0.03], [SHANK / 2, 0.03],
                                                                                   [0.016, 0.03], [0.016, 0.043], [0, 0.043]]}}
SPEC = {
    'schema_version': 1, 'subject_id': 'joint', 'identity': 'bolted plate on a stand with an embedded core', 'subject_mode': 'fictional',
    'request': 'bolted plate', 'request_trace': [{'phrase': 'bolted plate', 'items': ['plate', 'bolts']}],
    'sources': [{'id': 'design', 'kind': 'measurement', 'license': 'own'}],
    'dimensions': [{'id': 'dim.plate', 'value_m': 0.4, 'tol_pct': 1, 'source_id': 'design', 'measure': 'x', 'part_ids': ['plate']}],
    'features': [{'id': 'feat.bolts', 'description': 'four bolts through the plate', 'part_ids': ['bolts'], 'verify': 'assembly'}],
    'builders': [
        {'part_id': 'stand', 'builder': 'box', 'params': {'size': [0.6, 0.6, 0.2]}},
        # the wall builder makes a plate in X-Z with thickness along Y; rotating 90 deg about X lays it flat (thickness along Z)
        {'part_id': 'plate', 'builder': 'wall', 'params': {'length': 0.4, 'height': 0.4, 'thickness': 0.02, 'openings': [
            {'x': x - HOLE / 2, 'z': z - HOLE / 2, 'w': HOLE, 'h': HOLE} for x in (0.1, 0.3) for z in (0.1, 0.3)]},
         'transform': {'rotation_deg': [90, 0, 0]}},
        {'part_id': 'bolts', 'builder': 'array', 'features': ['feat.bolts'], 'params': {'pattern': 'grid', 'counts': [2, 2], 'pitch_m': [0.2, 0.2], 'axes': ['x', 'y'],
                                                             'item': {**bolt, 'transform': {'rotation_deg': [90, 0, 0]}}}},
        {'part_id': 'shell', 'builder': 'box', 'params': {'size': [0.2, 0.2, 0.2]}, 'transform': {'location': [1.0, 0, 0.1]}},
        {'part_id': 'core', 'builder': 'box', 'params': {'size': [0.12, 0.12, 0.12]}, 'transform': {'location': [3.0, 3.0, 3.0]}},
    ],
    'relations': [
        {'type': 'on_surface', 'a': 'plate/-z', 'b': 'stand', 'axis': '-z'},
        {'type': 'through', 'a': 'bolts/center', 'b': 'plate/center', 'axis': 'z'},
        {'type': 'align', 'a': 'bolts/-z', 'b': 'plate/-z', 'axis': 'z'},
        {'type': 'attach', 'a': 'core', 'b': 'shell'},
    ],
    'assembly_claims': [
        {'id': 'bolts-through-plate', 'type': 'through', 'a': 'bolts', 'b': 'plate', 'axis': 'z'},
        {'id': 'plate-on-stand', 'type': 'contact', 'a': 'plate', 'b': 'stand'},
        {'id': 'bolts-clear-plate', 'type': 'no_interference', 'a': 'bolts', 'b': 'plate'},
        {'id': 'nothing-floats', 'type': 'no_floating', 'a': 'plate'},
        {'id': 'core-cover', 'type': 'cover', 'a': 'core', 'b': 'shell', 'value_m': 0.04},
        {'id': 'shell-clear-stand', 'type': 'clearance', 'a': 'shell', 'b': 'stand', 'value_m': 0.5},
    ],
}


def variant(name):
    spec = deepcopy(SPEC)
    if name == 'bolt_off_axis':
        spec['relations'][1]['offset_m'] = [0.003, 0, 0]
    elif name == 'plate_lifted':
        spec['relations'][0]['offset_m'] = [0, 0, 0.002]
    elif name == 'stand_in_plate':
        spec['relations'][0]['offset_m'] = [0, 0, -0.005]
    elif name == 'thin_cover':
        spec['builders'][4]['params']['size'] = [0.13, 0.13, 0.13]
    elif name in ('exploded_declared', 'exploded_undeclared'):
        spec['relations'][0]['offset_m'] = [0, 0, 0.2]  # exploded view: plate lifted 200 mm on purpose
        if name == 'exploded_declared':
            spec['deviations'] = [{'id': 'explode.contact', 'check': 'assembly:plate-on-stand', 'waive': True, 'reason': 'exploded view separates the plate from the stand'},
                                  {'id': 'explode.float', 'check': 'assembly:nothing-floats', 'waive': True, 'reason': 'exploded view: the plate floats on purpose'}]
    elif name == 'shell_too_close':
        spec['builders'][3]['transform']['location'] = [0.6, 0, 0.1]
    return spec


EXPECT = {'correct': set(), 'bolt_off_axis': {'bolts-through-plate', 'bolts-clear-plate'}, 'plate_lifted': {'plate-on-stand', 'nothing-floats'},
          'stand_in_plate': {'plate-on-stand'}, 'thin_cover': {'core-cover'}, 'shell_too_close': {'shell-clear-stand'},
          'exploded_declared': set(), 'exploded_undeclared': {'plate-on-stand', 'nothing-floats'}}

(ROOT / 'projects/harness_validation').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT / 'projects/harness_validation') as tmp:
    project = Path(tmp) / 'joint'
    shutil.copytree(ROOT / 'tests/fixtures/winch_spec', project, ignore=shutil.ignore_patterns('shots', 'renders', 'workbench', 'runs'))
    lint = lint_spec(SPEC, project)
    assert not lint['errors'], lint['errors']
    write_json(project / 'subjects/joint/spec.json', SPEC)
    results = {}
    with SubjectSession(project, 'joint') as session:
        for name in EXPECT:
            spec = variant(name)
            geometry = session.geometry(spec, views=[])
            report = build_report(spec, geometry, project)
            failed = {c['id'] for c in report['checks'] if c['kind'] == 'assembly' and c['passed'] is False}
            results[name] = {'failed': sorted(failed), 'notes': [c.get('note') for c in report['checks'] if c['kind'] == 'assembly' and c.get('note')],
                             'deviations': [d['id'] for d in report['deviations_applied']]}
            if name == 'exploded_declared':
                assert not lint_spec(spec, project)['errors'] and report['summary']['stylized'] and report['unused_deviations'] == [], report
            assert failed == EXPECT[name], (name, failed, EXPECT[name], report['failures'])
        relation_moves = session.workbench.call(project, session.session_id, 'build_subject', {'subject_id': 'joint'})['result']['relations']
print('STUDIO_ASSEMBLY_SMOKE ' + json.dumps({'ok': True, 'variants': results, 'relations': relation_moves}, sort_keys=True))
