"""A revision equals a fresh build (builds only, no render). Every generator after the author - fill, camera move and
rig, reveal, baked simulation, graphics, look - must give the same scene whether the build starts fresh or from a base
version's authored checkpoint with an empty patch. Before the checkpoint, a base build failed on the fill sources
("already built"), doubled graphics and debris, and skipped the look.

Scenes: the combo2 dive (move + reveal + debris + world/screen graphics + photoreal look + compositor) and the fill
station (declared levels, a brief with subject / identity / ambient).
Run: .venv/bin/python tests/studio/revision_equivalence_smoke.py
"""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot

combo2 = (ROOT / 'tests/studio/combo2_smoke.py').read_text()
combo = (ROOT / 'tests/studio/combo_smoke.py').read_text()
sim = (ROOT / 'tests/studio/sim_bake_smoke.py').read_text()
fill = (ROOT / 'tests/studio/fill_smoke.py').read_text()


def literal(source, name, pattern=r"^{} = (\{{.*?\}})\n(?=[A-Z])"):
    return eval(re.search(pattern.format(name), source, re.S | re.M).group(1))


DIVE_AUTHOR = re.search(r"^AUTHOR = '''(.*?)'''", combo2, re.S | re.M).group(1)
REVEAL, CAMERA, DEBRIS = literal(combo, 'REVEAL'), literal(combo, 'CAMERA'), literal(sim, 'DEBRIS')
GRAPHICS = eval(re.search(r"^GRAPHICS = (\[.*?\n\])", combo2, re.S | re.M).group(1), {'BOUND': literal(combo2, 'BOUND')})
FILL_AUTHOR = re.search(r"^AUTHOR = '''(.*?)'''", fill, re.S | re.M).group(1)
exec(re.search(r"^def item\(.*?\n\n", fill, re.S | re.M).group(0))
STATION = eval(re.search(r"^STATION = (\{.*?\]\}\]\})", fill, re.S | re.M).group(1))

PROBE = '''import bpy, json
scene = bpy.context.scene
objects = sorted(bpy.data.objects, key=lambda o: o.name)
ids = [o.get('studio_id') for o in objects if o.get('studio_id')]
scene.frame_set(scene.frame_end)
debris = sorted((o for o in objects if o.name.startswith('StudioSim_')), key=lambda o: o.name)
print('PROBE ' + json.dumps({'objects': [[o.name, o.type] for o in objects], 'duplicate_ids': sorted({i for i in ids if ids.count(i) > 1}),
    'debris_end': [round(v, 4) for o in debris for v in o.matrix_world.translation],
    'camera': [round(v, 4) for v in scene.camera.matrix_world.translation], 'shift_y': round(scene.camera.data.shift_y, 5)}))
'''


def probe(version):
    script = version.parent / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    return json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])


def reports(version):
    keep = {}
    for name in ('camera_rig_report.json', 'camera_move_report.json', 'graphics_report.json', 'fill_report.json', 'environment_report.json'):
        if (version / name).is_file():
            keep[name] = read_json(version / name)
    look = read_json(version / 'look_report.json')
    keep['look'] = {k: look.get(k) for k in ('preset', 'applied', 'skipped', 'scene_state_sha256')}
    return keep


def equivalent(p, shot_id, author):
    """Fresh v1 -> base v1 + empty patch -> fresh v3: the base build and the fresh rebuild match v1."""
    v1 = build_shot(p, shot_id, author)['scene_version']
    noop = p / 'noop_patch.py'; noop.write_text('# an empty revision patch\n')
    v2 = build_shot(p, shot_id, noop, base=v1)['scene_version']
    v3 = build_shot(p, shot_id, author)['scene_version']
    versions = [p / 'shots' / shot_id / 'versions' / v for v in (v1, v2, v3)]
    states = [probe(v) for v in versions]
    reps = [reports(v) for v in versions]
    for state in states:
        assert state['duplicate_ids'] == [], state['duplicate_ids']
    assert states[1] == states[0] == states[2], [s['objects'][:5] for s in states]
    for name in reps[0]:
        assert reps[1][name] == reps[0][name] == reps[2][name], name
    return versions, states[0]


checks = []
with tempfile.TemporaryDirectory(prefix='revision-equivalence-') as root:
    p = Path(init_project('rev_eq', {'request': 'revision equivalence', 'shots': [{'shot_id': 'dive', 'frame_count': 90},
                                                                                  {'shot_id': 'station', 'frame_count': 30}]}, root)['project_path'])
    style = read_json(p / 'style.json'); style.setdefault('look', {})['compositor'] = 'explainer_finish'; write_json(p / 'style.json', style)
    dive_author = p / 'dive_author.py'; dive_author.write_text(DIVE_AUTHOR)
    shot = read_json(shot_path(p, 'dive'))
    shot.update({'camera': CAMERA, 'actions': [REVEAL, DEBRIS], 'graphics': GRAPHICS})
    shot['render'].update({'look_preset': 'photoreal_interior'})
    write_json(shot_path(p, 'dive'), shot)
    versions, state = equivalent(p, 'dive', dive_author)
    assert (versions[0] / 'authored.blend').is_file() and read_json(versions[1] / 'dependencies.json')['base_version'] == versions[0].name
    checks += ['dive_revision_equals_fresh', 'no_duplicate_generated_objects']

    station_author = p / 'station_author.py'; station_author.write_text(FILL_AUTHOR)
    shot = read_json(shot_path(p, 'station')); shot['fill_brief'] = STATION; shot['render'].update({'look_preset': 'photoreal_interior'})
    write_json(shot_path(p, 'station'), shot)
    equivalent(p, 'station', station_author)
    checks.append('fill_revision_equals_fresh')

    legacy = versions[0]
    (legacy / 'authored.blend').unlink()                      # a version built before checkpoints is not revisable
    try:
        build_shot(p, 'dive', p / 'noop_patch.py', base=legacy.name)
        raise AssertionError('legacy base accepted')
    except StudioError as error:
        assert error.code == 'BASE_NOT_REVISABLE', error.code
    checks.append('legacy_base_refused')

print('STUDIO_REVISION_EQUIVALENCE_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'objects': len(state['objects']),
                                                       'debris': len(state['debris_end']) // 3}))
