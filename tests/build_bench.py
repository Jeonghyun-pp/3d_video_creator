"""Build shots in throwaway copies and report build time, object count and distinct mesh datablocks.

For performance work that must not change results: run before and after, compare seconds and counts;
inventory transforms are compared by tests/run_smokes.py / rig_regression.py, not here.
Run: ../.venv/bin/python tests/build_bench.py samsung_moves:s04 samsung_detail:s02 ...
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot  # noqa: E402
from studio.common import blender_binary, read_json, write_json  # noqa: E402

HV = ROOT / 'projects' / 'harness_validation'
AUTHORS = {'samsung_moves': HV / 'samsung_reel_inputs/author_samsung.py', 'samsung_detail': HV / 'samsung_reel_inputs/author_samsung.py',
           'samsung_photoreal': HV / 'samsung_reel_inputs/author_samsung.py', 'samsung_blockout': HV / 'samsung_reel_inputs/author_samsung.py'}
COUNT = '''import bpy, json
print('MESHES ' + json.dumps({'objects': len(bpy.data.objects), 'meshes': len(bpy.data.meshes),
      'mesh_objects': sum(o.type == 'MESH' for o in bpy.data.objects)}))
'''


def bench(project, shot_id):
    tmp = Path(tempfile.mkdtemp(prefix='bench-', dir=HV))
    try:
        (tmp / 'samsung_reel_inputs').symlink_to((HV / 'samsung_reel_inputs').resolve())
        dst = tmp / project
        shutil.copytree(HV / project, dst, ignore=shutil.ignore_patterns('versions', 'renders', 'final', 'edit', 'audio', 'control', 'runs', 'candidates', 'generated'))
        shot = read_json(dst / 'shots' / shot_id / 'shot.json'); shot['scene_version'] = None
        write_json(dst / 'shots' / shot_id / 'shot.json', shot)
        started = time.monotonic()
        built = build_shot(dst, shot_id, AUTHORS[project])
        seconds = round(time.monotonic() - started, 1)
        version = dst / 'shots' / shot_id / 'versions' / built['scene_version']
        script = tmp / 'count.py'; script.write_text(COUNT)
        out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
        counts = json.loads(next(line for line in out.splitlines() if line.startswith('MESHES '))[7:])
        return {'shot': f'{project}:{shot_id}', 'build_s': seconds, **counts, 'blend_mb': round((version / 'scene.blend').stat().st_size / 1e6, 1)}
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    for arg in sys.argv[1:]:
        print(json.dumps(bench(*arg.split(':'))), flush=True)
