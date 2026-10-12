"""Host smoke (needs Blender): the synthetic screen-grammar measurement (blender_ops/grammar_synth.py) against known
camera moves, and against the pixel measurement (grammar_probe.py, the one the reference reels were measured with) on
a render of the same scene with enough texture for it.

A field of small random-coloured blocks; four cameras of 48 frames: held, pushing in, trucking right, rolling. Each
must come out as its axis (grammar_core.camera) with the right sign; for the trucking camera the workbench render is
measured by grammar_probe too and the two rates must agree within RATE_TOL (the synthetic run then drops the sky, as
the pixel probe drops a flat sky; with a sky of clouds both would count it).
Run: .venv/bin/python tests/studio/grammar_synth_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import grammar_core  # noqa: E402
from studio.common import blender_binary  # noqa: E402

RATE_TOL = 0.35   # synthetic vs pixel rate, relative: phase correlation on a 192 px render is not exact

DRIVER = r'''
import bpy, math, random, sys, json
sys.path.insert(0, {ops!r})
import grammar_synth
from scene_tools import curves
move, out, video = sys.argv[sys.argv.index('--') + 1:][:3]
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y, scene.render.fps = 360, 640, 30
scene.frame_start, scene.frame_end = 1, 48
random.seed(4)
for i in range(-12, 13):
    for j in range(0, 30):
        bpy.ops.mesh.primitive_cube_add(size=0.6, location=(i * 1.1, j * 1.1, random.uniform(-0.3, 0.6)))
        o = bpy.context.object
        o.color = (random.random(), random.random(), random.random(), 1)
cam_data = bpy.data.cameras.new('cam'); cam_data.lens = 35
cam = bpy.data.objects.new('cam', cam_data); scene.collection.objects.link(cam); scene.camera = cam
cam.rotation_mode = 'XYZ'
def key(f, loc, roll=0.0):
    cam.location = loc; cam.rotation_euler = (math.radians(70), math.radians(roll), 0)
    cam.keyframe_insert('location', frame=f); cam.keyframe_insert('rotation_euler', frame=f)
start = (0, -6, 5)
ends = {{'still': ((0, -6, 5), 0), 'push': ((0, -2, 3.6), 0), 'truck': ((4, -6, 5), 0), 'roll': ((0, -6, 5), 20)}}
key(1, start); key(48, *ends[move])
for fc in curves(cam.animation_data.action):
    for k in fc.keyframe_points:
        k.interpolation = 'LINEAR'
result = grammar_synth.measure(192, sky=(video == '-'))   # beside a flat-sky render, drop the sky as the pixel probe does
json.dump(result, open(out, 'w'))
if video != '-':
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.color_type = 'OBJECT'
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = video + '_f'
    bpy.ops.render.render(animation=True)
'''


def run(tmp, move, video='-'):
    driver = Path(tmp) / 'driver.py'
    driver.write_text(DRIVER.format(ops=str(ROOT / 'studio/blender_ops')))
    out = Path(tmp) / f'{move}.json'
    done = subprocess.run([blender_binary(), '--background', '--factory-startup', '--python-exit-code', '1', '--python', str(driver), '--',
                           move, str(out), video], capture_output=True, text=True, timeout=900)
    assert done.returncode == 0 and out.is_file(), (done.stdout + done.stderr)[-3000:]
    data = json.loads(out.read_text())
    return grammar_core.camera(data['pairs'], data['fps'])


checks = {}
with tempfile.TemporaryDirectory(prefix='grammar-synth-') as tmp:
    still = run(tmp, 'still')
    assert still['axis'] == 'still', still
    push = run(tmp, 'push')
    assert push['axis'] in ('scale_in', 'compound') and push['rate_per_s']['scale'] > 0, push
    roll = run(tmp, 'roll')
    assert abs(roll['rate_per_s']['roll_deg']) > 3, roll
    video = Path(tmp) / 'truck.mp4'
    truck = run(tmp, 'truck', str(video))
    assert truck['axis'] in ('x', 'compound') and truck['rate_per_s']['x'] < 0, truck   # the camera goes right, the world left
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', '30', '-i', str(video) + '_f%04d.png', '-c:v', 'libx264', '-crf', '12',
                    '-pix_fmt', 'yuv420p', str(video)], check=True)
    probe = Path(tmp) / 'probe.json'
    subprocess.run([blender_binary(), '--background', '--factory-startup', '--python-exit-code', '1', '--python',
                    str(ROOT / 'studio/blender_ops/grammar_probe.py'), '--', str(video), str(probe), '0', '0', '1', '1'],
                   check=True, capture_output=True, timeout=900)
    pixel = grammar_core.camera(json.loads(probe.read_text())['pairs'], 30)
    synth_x, pixel_x = truck['rate_per_s']['x'], pixel['rate_per_s']['x']
    assert abs(synth_x - pixel_x) <= RATE_TOL * abs(pixel_x), (synth_x, pixel_x)
    checks = {'still': still['rate_per_s'], 'push': push['rate_per_s'], 'roll': roll['rate_per_s'],
              'truck_synthetic_x': synth_x, 'truck_pixel_x': pixel_x, 'truck_parallax': [truck['parallax_per_s'], pixel['parallax_per_s']]}
print('GRAMMAR SYNTH SMOKE OK ' + json.dumps(checks))
