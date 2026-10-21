"""The sealed engine end to end (studio/seal.py, 2026-10-18): the MCP server Astra is given starts from the sealed copy,
its studio_run commands run the sealed code, and nothing planted in the checkout runs outside the sandbox - a json.py at
its root (first on a -m path before), a line appended to the checkout's studio/common.py copy is never what runs. Then a
contrib entry is checked by the broker and a brokered build uses it (the reason the seal was made). Needs a seal
(studio freeze record); real processes, real Blender, no network.
Run: .venv/bin/python tests/studio/sealed_engine_smoke.py
"""
from pathlib import Path
import json
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import seal
from studio.common import read_json, write_json
from studio.mechanisms import harmonic_spec
from studio.project import init_project, shot_path
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)
from studio.subjects import spec_path

engine = seal.current()
assert engine is not None, 'no sealed engine: studio freeze record --user-words "..." first'
checks = []


class Server:
    """The MCP server as codex starts it for Astra (scripts/reel_agent.py): the sealed launcher, cwd the checkout."""
    def __init__(self):
        self.proc = subprocess.Popen(seal.launch_argv('mcp', engine=engine), cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        self.n = 0
        self.call_raw('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'smoke', 'version': '1'}})

    def call_raw(self, method, params):
        self.n += 1
        self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params}) + '\n')
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def run(self, args, timeout=900):
        reply = self.call_raw('tools/call', {'name': 'studio_run', 'arguments': {'args': args, 'timeout_s': timeout}})
        text = reply['result']['content'][0]['text']
        return json.loads(text)

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=30)


trap_marker = Path(tempfile.gettempdir()) / f'sealed-smoke-{secrets.token_hex(4)}'
trap = ROOT / 'json.py'
assert not trap.exists(), 'a json.py already sits at the checkout root'
trap.write_text(f'open({str(trap_marker)!r}, "w").write("ran")\nraise ImportError("planted")\n')
server = Server()
try:
    status = server.run(['seal', 'status'], timeout=120)
    assert status.get('ok') and Path(status['code']).resolve() == engine.resolve(), status   # the sealed code ran it
    assert not trap_marker.exists(), 'a file planted in the checkout ran outside the sandbox'
    checks.append(f"studio_run runs the sealed engine ({engine.name}); the planted json.py never ran")

    (ROOT / 'projects' / 'harness_validation').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sealed-smoke-', dir=ROOT / 'projects' / 'harness_validation') as root:
        p = Path(init_project('sealed', {'request': 'sealed engine smoke', 'shots': [{'shot_id': 's', 'frame_count': 12}]}, root)['project_path'])
        settle(p)
        entry = p / 'contrib/profile/cycloid_disc'
        shutil.copytree(ROOT / 'tests/fixtures/contrib/cycloid_disc', entry)
        spec = harmonic_spec('hd', 0.0005, 100)
        wave = next(b for b in spec['builders'] if 'wave_cam' in json.dumps(b['params']))
        wave['params']['profile'] = {'contrib': 'contrib:cycloid_disc@draft', 'args': {'ring_r': 0.012, 'pin_r': 0.0008, 'ecc': 0.0003, 'pins': 10}}
        spec.pop('couplings', None); spec.pop('joints', None)
        spec_path(p, 'hd').parent.mkdir(parents=True, exist_ok=True); write_json(spec_path(p, 'hd'), spec)
        shot = read_json(shot_path(p, 's'))
        shot.update({'scene': {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                               'instances': [{'id': 'hd', 'subject': 'hd', 'at': [0, 0, 0]}]},
                     'camera': {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                                'move': {'type': 'turntable', 'params': {'target': 'hd', 'sweep_deg': 20}, 'lens_mm': 50}}})
        write_json(shot_path(p, 's'), shot)

        checked = server.run(['contrib', 'check', '--project', str(p), '--name', 'cycloid_disc'], timeout=300)
        assert checked.get('ok') and checked.get('checked_by') == 'broker' and checked.get('isolation') == 'os+hook', checked
        checks.append('the broker checks a contrib entry under its own OS sandbox')
        built = server.run(['shot', 'build', '--project', str(p), '--shot', 's'], timeout=900)
        assert built.get('ok') and built.get('scene_version'), {k: built.get(k) for k in ('ok', 'error', 'exit_code')}
        assert not trap_marker.exists()
        checks.append(f"a brokered build uses the entry ({built['scene_version']})")
finally:
    server.close()
    trap.unlink(missing_ok=True)
    trap_marker.unlink(missing_ok=True)

print('STUDIO_SEALED_ENGINE_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
