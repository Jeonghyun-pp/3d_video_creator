"""s01 with no author code (builds only, no render): the samsung s01 scene built from its declarative scene
(examples/samsung_cutaway/project/sets/station.json + shot.scene) matches the same shot built by author_samsung.py -
compared by geometry, not by names (ids differ: the data names what the author script numbered).

Compared per object: type, vertex count, world bounding box (mm), material kinds; lights: type, energy, position.
Run: .venv/bin/python tests/studio/s01_parity_smoke.py
"""
from collections import Counter
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import blender_binary, read_json, write_json
from studio.project import from_example, shot_path

PROBE = '''import bpy, json, re
PREFIX = re.compile(r'^(StudioMat_)?(samsung_|layout_|flat_|emit_)')
rows = []
for o in bpy.data.objects:
    if o.type == 'MESH':
        corners = [o.matrix_world @ __import__('mathutils').Vector(c) for c in o.bound_box]
        box = [round(min(c[i] for c in corners), 3) for i in range(3)] + [round(max(c[i] for c in corners), 3) for i in range(3)]
        mats = sorted(PREFIX.sub('', m.name.split('/', 1)[-1]) for m in o.data.materials if m)   # exemplar materials carry their instance id
        rows.append(['MESH', len(o.data.vertices), box, mats])
    elif o.type == 'LIGHT':
        rows.append(['LIGHT', o.data.type, round(o.data.energy, 3), [round(v, 3) for v in o.matrix_world.translation]])
print('PROBE ' + json.dumps(rows))
'''


def probe(version):
    script = version.parent / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    return Counter(json.dumps(r) for r in json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:]))


with tempfile.TemporaryDirectory(prefix='s01-parity-') as root:
    project = Path(root) / 'samsung'
    from_example('samsung_cutaway', project)
    shot = read_json(shot_path(project, 's01'))
    scene = shot.pop('scene')
    write_json(shot_path(project, 's01'), shot)
    authored = build_shot(project, 's01', ROOT / 'examples/samsung_cutaway/author_samsung.py')
    shot = read_json(shot_path(project, 's01')); shot['scene'] = scene
    shot.pop('author', None)   # the first build recorded its script as shot.author (E5); the data build is the one without it
    write_json(shot_path(project, 's01'), shot)
    data = build_shot(project, 's01', None)
    versions = project / 'shots/s01/versions'
    a, b = probe(versions / authored['scene_version']), probe(versions / data['scene_version'])
    only_author, only_data = a - b, b - a
    deps = read_json(versions / data['scene_version'] / 'dependencies.json')
    report = {'objects_author': sum(a.values()), 'objects_data': sum(b.values()), 'only_author': sum(only_author.values()),
              'only_data': sum(only_data.values()), 'author_lines': deps['author_lines'],
              'examples_only_author': [json.loads(k) for k in list(only_author)[:5]], 'examples_only_data': [json.loads(k) for k in list(only_data)[:5]]}
    assert deps['author_lines'] == 0, deps
    rigs = [read_json(versions / v / 'camera_rig_report.json') for v in (authored['scene_version'], data['scene_version'])]
    report['camera_samples_equal'] = rigs[0]['samples'] == rigs[1]['samples']
    assert report['camera_samples_equal'], 'the camera flies differently through the data-built scene'
    envs = [read_json(versions / v / 'environment_report.json')['streets'][0]['digest'] for v in (authored['scene_version'], data['scene_version'])]
    assert envs[0] == envs[1], envs
    assert not only_author and not only_data, json.dumps(report)[:3000]
print('STUDIO_S01_PARITY_SMOKE ' + json.dumps({'ok': True, **{k: v for k, v in report.items() if not k.startswith('examples')}}))
