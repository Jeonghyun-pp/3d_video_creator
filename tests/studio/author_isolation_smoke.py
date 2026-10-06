"""Author isolation: an author script has free bpy, but runs in its own Blender process under a sandbox and hands on only
a .blend - so it cannot weaken a gate, change the job the build trusts, read keys, write outside its build folder or
make 3D text, and whatever it changes in the render settings is recorded. Linking a .blend asset is allowed; the linked
data is made local and packed, with the library's path and hash recorded.
Run: .venv/bin/python tests/studio/author_isolation_smoke.py
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.project import init_project, shot_path

SCENE = '''import bpy
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5)); cube = bpy.context.object; cube.name = 'pump'; cube['studio_id'] = 'pump'
bpy.ops.mesh.primitive_plane_add(size=20); bpy.context.object['studio_id'] = 'floor'
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); bpy.context.scene.collection.objects.link(cam)
cam.location = (0, -5, 1.5); cam.rotation_euler = (1.3, 0, 0); bpy.context.scene.camera = cam
'''
CAMERA = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None, 'keys': []}


def build(p, extra, expect=None, env=None):
    author = p / 'author.py'
    author.write_text(SCENE + extra)
    old = dict(os.environ)
    os.environ.update(env or {})
    try:
        return build_shot(p, 's', author)
    except StudioError as error:
        if expect is None:
            raise
        assert error.code == expect, (expect, error.code, error.message[:500])
        return error
    finally:
        os.environ.clear(); os.environ.update(old)


checks = []
with tempfile.TemporaryDirectory(prefix='author-isolation-') as root:
    p = Path(init_project('iso', {'request': 'isolation smoke', 'shots': [{'shot_id': 's', 'frame_count': 12}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's')); shot['camera'] = CAMERA; write_json(shot_path(p, 's'), shot)

    clean = build(p, '')
    checks.append('a_plain_author_builds')

    # A gate monkeypatched, the job edited in memory, a handler registered: stage 2 never sees any of it.
    build(p, '''
import frame_probe, sys
frame_probe.probe = lambda job, output: None
STUDIO_JOB['probe'] = {'key_parts': []}
bpy.app.handlers  # noqa
''', expect='AUTHOR_SCRIPT_REFUSED')   # handlers are refused by the lint before Blender starts
    build(p, '''
import frame_probe
frame_probe.probe = lambda job, output: None
STUDIO_JOB['shot']['key_parts'] = []
cam.rotation_euler = (2.9, 0, 0)   # the camera now looks at the sky - the real probe (stage 2) must still catch it
''', expect='FRAME_EMPTY')
    checks.append('monkeypatching_a_gate_or_the_job_changes_nothing')

    build(p, "open(STUDIO_JOB['output_dir'] + '/../escape.txt', 'w').write('x')\n", expect='AUTHOR_SANDBOX_VIOLATION')
    build(p, "import json\njson.dump({}, open(STUDIO_JOB['output_dir'] + '/author_job.json', 'w'))\n", expect='AUTHOR_SANDBOX_VIOLATION')
    built = build(p, "open(STUDIO_JOB['output_dir'] + '/mechanism_note.json', 'w').write('{}')\n")   # its own data file: allowed
    checks.append('writes_only_to_its_own_build_folder')

    build(p, 'import subprocess\n', expect='AUTHOR_SCRIPT_REFUSED')
    build(p, 'x = eval("1")\n', expect='AUTHOR_SCRIPT_REFUSED')
    checks.append('processes_and_dynamic_code_refused_by_lint')

    env_build = build(p, "bpy.context.scene['argv_seen'] = 1\n", env={'FAL_KEY': 'secret-should-not-pass'})
    log = (p / 'shots/s/versions' / env_build['scene_version'] / 'author.log').read_text()
    assert 'secret-should-not-pass' not in log
    checks.append('keys_never_written_into_the_build')   # the scrub itself: tests/test_freeze.py BlenderEnvTest

    build(p, "bpy.ops.object.text_add(location=(0, 0, 2)); bpy.context.object.data.body = 'PUMP'\n", expect='TEXT_3D_FORBIDDEN')
    checks.append('3d_text_refused')

    changed = build(p, "bpy.context.scene.view_settings.exposure = 1.5\nbpy.context.scene.cycles.samples = 8\n")
    audit = read_json(p / 'shots/s/versions' / changed['scene_version'] / 'author_audit.json')
    rows = {r['path']: r for r in audit['settings_changed']}
    assert rows['view_settings.exposure']['after'] == 1.5 and rows['cycles.samples']['renderer_overrides'], rows
    checks.append('setting_changes_recorded_not_refused')

    # A linked .blend asset: allowed, made local and packed, path + hash recorded.
    asset = Path(root) / 'asset.blend'
    subprocess.run([blender_binary(), '-b', '--factory-startup', '--python-expr',
                    f"import bpy\nbpy.ops.mesh.primitive_uv_sphere_add(location=(1.5, 0, 0.5))\nbpy.context.object.name = 'ball'\n"
                    f"bpy.ops.wm.save_as_mainfile(filepath={str(asset)!r})"], check=True, capture_output=True)
    linked = build(p, f'''
with bpy.data.libraries.load({str(asset)!r}, link=True) as (src, dst):
    dst.objects = ['ball']
for obj in dst.objects:
    bpy.context.scene.collection.objects.link(obj)
''')
    version = p / 'shots/s/versions' / linked['scene_version']
    deps = read_json(version / 'dependencies.json')
    assert deps['linked_libraries'][0]['sha256'], deps.get('linked_libraries')
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python-expr',
                          "import bpy\nprint('LIBS', len(bpy.data.libraries), 'ball' in bpy.data.objects)"], capture_output=True, text=True).stdout
    assert 'LIBS 0 True' in out, out[-500:]
    checks.append('linked_asset_made_local_and_recorded')

    # The same asset declared as data (shot.scene.links): appended at a place, its hash in the layout.
    shot = read_json(shot_path(p, 's'))
    shot['scene'] = {'links': [{'id': 'ball2', 'file': '../asset.blend', 'data_type': 'objects', 'name': 'ball', 'at': [0, 1, 0]}]}
    write_json(shot_path(p, 's'), shot)
    declared = build(p, '')
    layout = read_json(p / 'shots/s/versions' / declared['scene_version'] / 'layout.json')
    assert layout['scene']['links'][0]['sha256'], layout['scene']['links']
    checks.append('declared_link_appended_with_its_hash')

print('STUDIO_AUTHOR_ISOLATION_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
