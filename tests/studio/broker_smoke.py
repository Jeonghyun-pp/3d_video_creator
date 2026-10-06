"""Blender access for a sandboxed agent: inside the codex sandbox a studio command that needs Blender is refused with the
way out (BLENDER_NEEDS_BROKER) instead of crashing; the same command through the MCP tool studio_run builds the shot
(GPU frame probe included) under the studio's own sandbox. Needs the codex CLI for the first half.
Run: .venv/bin/python tests/studio/broker_smoke.py
"""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import read_json, write_json
from studio.project import init_project, shot_path
from studio.workbench_mcp import run_tool

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'m': {'color': [0.7, 0.4, 0.2]}},
         'primitives': [{'id': 'pump', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0.5], 'material': 'm'}]}
CAMERA = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
          'keys': [{'frame': 0, 'location': [0, -5, 1.5], 'target': [0, 0, 0.5]}, {'frame': 11, 'location': [0, -5, 1.5], 'target': [0, 0, 0.5]}]}

checks = []
with tempfile.TemporaryDirectory(prefix='broker-smoke-', dir=ROOT / 'projects') as root:
    p = Path(init_project('broker', {'request': 'broker smoke', 'shots': [{'shot_id': 's', 'frame_count': 12}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's')); shot.update({'scene': SCENE, 'camera': CAMERA}); write_json(shot_path(p, 's'), shot)
    args = ['shot', 'build', '--project', str(p), '--shot', 's']
    if shutil.which('codex'):
        inside = subprocess.run(['codex', 'sandbox', '-c', 'sandbox_mode="workspace-write"', '--', sys.executable, '-m', 'studio', *args], cwd=ROOT, capture_output=True, text=True)
        line = next((l for l in inside.stdout.splitlines() if l.startswith('{')), None)
        assert line and json.loads(line)['error']['code'] == 'BLENDER_NEEDS_BROKER', (inside.returncode, inside.stdout[-600:], inside.stderr[-600:])
        checks.append('inside_the_codex_sandbox_refused_with_the_way_out')
    result = run_tool('studio_run', {'args': args})
    assert result['ok'] and result['frame']['seconds'] < 5, result
    checks.append(f"studio_run_builds_with_the_gpu_probe ({result['frame']['seconds']} s)")
    try:
        run_tool('studio_run', {'args': ['freeze', 'record', '--user-words', 'agent wrote this']})
        raise AssertionError('freeze record ran through the broker')
    except Exception as error:   # noqa: BLE001
        assert 'does not run' in str(error), error
    checks.append('line_keeping_commands_not_brokered')

print('STUDIO_BROKER_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
