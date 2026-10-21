"""A contrib entry inside an agent's sandbox (2026-10-18): no OS sandbox can be made inside another (macOS), so a confined
`contrib check` stops with CONTRIB_NEEDS_BROKER and runs no entry code; the broker runs the check where the checker
makes its own sandbox and keeps the verdicts; then the confined check and a brokered build (confined: the broker's own
sandbox) use the entry; a changed entry asks again. The sealed MCP path end to end: tests/studio/sealed_engine_smoke.py. Real sandbox-exec, real Blender; the verdicts this smoke made are
removed from the store at the end.
Run: .venv/bin/python tests/studio/contrib_broker_smoke.py
"""
from pathlib import Path
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import broker, contrib_probe
from studio.common import read_json, write_json
from studio.mechanisms import harmonic_spec
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)
from studio.subjects import spec_path

DISC = ROOT / 'tests/fixtures/contrib/cycloid_disc'
CAMERA = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
          'move': {'type': 'turntable', 'params': {'target': 'hd', 'sweep_deg': 20}, 'lens_mm': 50}}


def confined(args):
    """`python -m studio <args>` as the agent runs it: inside a sandbox (sandbox-exec), told so (CODEX_SANDBOX)."""
    env = {**os.environ, 'CODEX_SANDBOX': 'seatbelt'}
    env.pop('STUDIO_BROKERED', None)
    done = subprocess.run(['sandbox-exec', '-p', '(version 1)(allow default)', sys.executable, '-m', 'studio', *args],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    return json.loads([l for l in done.stdout.splitlines() if l.startswith('{')][-1]), done.returncode


checks = []
ours = set()
(ROOT / 'projects' / 'harness_validation').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='contrib-broker-smoke-', dir=ROOT / 'projects' / 'harness_validation') as root:
    try:
        p = Path(init_project('cb', {'request': 'contrib broker smoke', 'shots': [{'shot_id': 's', 'frame_count': 12}]}, root)['project_path'])
        settle(p)
        entry = p / 'contrib/profile/cycloid_disc'
        shutil.copytree(DISC, entry)
        (entry / 'impl.py').write_text((entry / 'impl.py').read_text() + f'\n# smoke {secrets.token_hex(8)}\n')   # code no verdict knows
        spec = harmonic_spec('hd', 0.0005, 100)
        wave = next(b for b in spec['builders'] if 'wave_cam' in json.dumps(b['params']))
        wave['params']['profile'] = {'contrib': 'contrib:cycloid_disc@draft', 'args': {'ring_r': 0.012, 'pin_r': 0.0008, 'ecc': 0.0003, 'pins': 10}}
        spec.pop('couplings', None); spec.pop('joints', None)
        spec_path(p, 'hd').parent.mkdir(parents=True, exist_ok=True); write_json(spec_path(p, 'hd'), spec)
        shot = read_json(shot_path(p, 's'))
        shot.update({'scene': {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                               'instances': [{'id': 'hd', 'subject': 'hd', 'at': [0, 0, 0]}]}, 'camera': CAMERA})
        write_json(shot_path(p, 's'), shot)
        args = ['contrib', 'check', '--project', str(p), '--name', 'cycloid_disc']
        policy = __import__('studio.contrib', fromlist=['policy']).policy()
        sha = __import__('studio.blender_ops.contrib_loader', fromlist=['code_sha']).code_sha(entry)
        manifest = read_json(entry / 'manifest.json')

        out, code = confined(args)
        assert code != 0 and out['error']['code'] == 'CONTRIB_NEEDS_BROKER' and 'studio_run' in out['error']['recovery'], (code, out)
        checks.append('confined_without_verdict_needs_broker')

        ours = {contrib_probe.verdict_key('contrib_check.py', {'sha256': sha, **policy}),
                contrib_probe.verdict_key('contrib_probe_child.py', {'sha256': sha, 'entry': manifest['entry'], **policy})}
        done = broker.run(args, timeout=300)   # the broker checks in its own process (the sealed engine's, under codex)
        assert done.get('ok') and done['exit_code'] == 0 and done.get('isolation') == 'os+hook' and not (done.get('probe') or {}).get('error'), done
        checks.append('broker_checks_under_the_os_sandbox_and_keeps_verdicts')

        out, code = confined(args)
        assert code == 0 and out['ok'], (code, out)
        checks.append('confined_check_reads_the_verdict')

        built = broker.run(['shot', 'build', '--project', str(p), '--shot', 's'], timeout=900)
        assert built.get('ok') and built.get('scene_version'), {k: built.get(k) for k in ('ok', 'error', 'exit_code')}
        checks.append(f"brokered_build_uses_the_entry ({built['scene_version']})")

        (entry / 'impl.py').write_text((entry / 'impl.py').read_text() + '# changed\n')
        out, code = confined(args)
        assert code != 0 and out['error']['code'] == 'CONTRIB_NEEDS_BROKER', (code, out)
        checks.append('changed_entry_needs_a_new_verdict')
    finally:
        # the verdicts this smoke made - its entry's code (a fresh token), checked and probed - removed under the store's lock
        from studio import contrib
        from studio.blender_ops.contrib_loader import code_sha
        from studio.common import lock
        path = contrib_probe.verdicts_path()
        if path.is_file() and ours:
            with lock(path.with_suffix('.lock')):
                data = read_json(path)
                for key in ours:
                    data['verdicts'].pop(key, None)
                write_json(path, data)

print('STUDIO_CONTRIB_BROKER_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
