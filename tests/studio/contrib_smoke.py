"""Contrib end to end: a project draws a cycloidal disc with its own profile entry (contrib:cycloid_disc@draft), the
build passes and promotes it into the library (v001, with provenance); another project then builds with the pinned
version. A draft whose code changes after it was resolved is never run.
Run: .venv/bin/python tests/studio/contrib_smoke.py
"""
from pathlib import Path
import json
import shutil
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import contrib
from studio.blender import build_shot
from studio.common import read_json, write_json
from studio.mechanisms import harmonic_spec
from studio.project import init_project, shot_path
from studio.subjects import spec_path

DISC = ROOT / 'tests/fixtures/contrib/cycloid_disc'
CAMERA = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
          'move': {'type': 'turntable', 'params': {'target': 'hd', 'sweep_deg': 20}, 'lens_mm': 50}}


def project(root, name, ref):
    p = Path(init_project(name, {'request': 'contrib smoke', 'shots': [{'shot_id': 's', 'frame_count': 24}]}, root)['project_path'])
    spec = harmonic_spec('hd', 0.0005, 100)
    wave = next(b for b in spec['builders'] if 'wave_cam' in json.dumps(b['params']))
    wave['params']['profile'] = {'contrib': ref, 'args': {'ring_r': 0.012, 'pin_r': 0.0008, 'ecc': 0.0003, 'pins': 10}}
    spec.pop('couplings', None); spec.pop('joints', None)
    spec_path(p, 'hd').parent.mkdir(parents=True, exist_ok=True); write_json(spec_path(p, 'hd'), spec)
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                           'instances': [{'id': 'hd', 'subject': 'hd', 'at': [0, 0, 0]}]}, 'camera': CAMERA})
    write_json(shot_path(p, 's'), shot)
    return p


checks = []
with tempfile.TemporaryDirectory(prefix='contrib-smoke-') as root:
    with mock.patch.object(contrib, 'LIBRARY', Path(root) / 'library' / 'contrib'):
        a = project(root, 'maker', 'contrib:cycloid_disc@draft')
        shutil.copytree(DISC, a / 'contrib/profile/cycloid_disc')
        built = build_shot(a, 's', None)
        promoted = [w for w in built['warnings'] if w.startswith('CONTRIB_PROMOTED')]
        assert promoted and 'contrib:cycloid_disc@v001' in promoted[0], built['warnings']
        provenance = read_json(Path(root) / 'library/contrib/profile/cycloid_disc/v001/provenance.json')
        assert provenance['provenance']['version'] == built['scene_version'], provenance
        deps = read_json(a / 'shots/s/versions' / built['scene_version'] / 'author_job.json')['contrib']
        assert deps['contrib:cycloid_disc@draft']['sha256'] == provenance['sha256']
        version = a / 'shots/s/versions' / built['scene_version']
        for report in ('sandbox_report.json', 'sandbox_engine_report.json'):   # a clean build leaves no sandbox records (no __pycache__ writes)
            assert not (version / report).is_file(), (report, (version / report).read_text()[:400])
        checks.append('draft_used_by_a_passing_build_is_promoted_with_provenance')

        b = project(root, 'user', 'contrib:cycloid_disc@v001')
        again = build_shot(b, 's', None)
        assert not any(w.startswith('CONTRIB_PROMOTED') for w in again['warnings'])
        checks.append('another_project_builds_with_the_pinned_version')

        from studio import workbench   # a session builds the subject with the draft too (the host resolves it for the session)
        session = workbench.start(a, subjects=['hd'])['session_id']
        try:
            built = workbench.call(a, session, 'build_subject', {'subject_id': 'hd'})['result']
            assert built, built
        finally:
            workbench.stop(a, session)
        checks.append('workbench_session_builds_with_the_draft')

print('STUDIO_CONTRIB_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
