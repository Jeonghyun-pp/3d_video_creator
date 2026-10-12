"""Host smoke (needs Blender): the synthetic screen-grammar measurement (blender_ops/grammar_synth.py) against known
camera moves, and against the pixel measurement (grammar_probe.py, the one the reference reels were measured with) on
a render of the same scene with enough texture for it.

A field of small random-coloured blocks; four cameras of 48 frames: held, pushing in, trucking right, rolling. Each
must come out as its axis (grammar_core.camera) with the right sign. Feel (grammar_core.feel, 2026-10-12): the same
truck past posts near the lens keeps its rate but streaks and passes edges; twice as high it has half the flow per
height; a push straight at an off-centre part heads at the part; a camera rising at a falling block meets it head on; for the trucking camera the workbench render is
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
SAME_RATE = 0.25  # posts in front must not change the measured camera rate by more than this (the fit trims them)
STREAK_GAIN = 0.03  # ... while the share of the picture streaking past grows by at least this
EDGES_GAIN = 1.0    # ... and object edges passing per second by at least this (picture shares per second)

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
def block(name, loc, size):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object; o.name = name; o.scale = size; o.color = (1, 1, 1, 1)
    return o
if move == 'posts':   # thin posts between the camera and the field, two metres in front
    for i, x in enumerate(range(-30, 90, 15)):
        block(f'post.{{i:03d}}', (x / 10, -4.0, 3.0), (0.12, 0.12, 6.0))
if move == 'aim':     # what the shot explains, off the centre of the picture
    block('part', (2.0, 6.0, 0.8), (1.2, 1.2, 1.2))
subject = None
if move in ('enter', 'outside'):   # an open box (its front face cut away) with a block inside: a section to push into
    import bmesh
    root = bpy.data.objects.new('shellsub', None); scene.collection.objects.link(root)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.0, 6.0, 2.5)); shell = bpy.context.object
    shell.name = 'shellsub/shell'; shell.scale = (4.0, 4.0, 4.0); shell.color = (0.9, 0.9, 0.9, 1)
    bpy.ops.object.transform_apply(scale=True)
    bm = bmesh.new(); bm.from_mesh(shell.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.y < -0.9], context='FACES'); bm.to_mesh(shell.data); bm.free()
    core = block('shellsub/core', (0.0, 6.5, 2.0), (1.2, 1.2, 1.6))
    shell.parent = root; core.parent = root; subject = 'shellsub'
if move in ('piston', 'piston_body'):   # what is explained sits outside the body: an open tube beside the box, a rod inside the tube
    import bmesh
    root = bpy.data.objects.new('shellsub', None); scene.collection.objects.link(root)
    body = block('shellsub/body', (6.0, 6.0, 2.5), (4.0, 4.0, 4.0)); body.parent = root
    bpy.ops.mesh.primitive_cylinder_add(radius=0.8, depth=4.0, location=(0.0, 6.0, 2.0), rotation=(math.radians(90), 0, 0))
    tube = bpy.context.object; tube.name = 'shellsub/piston/outer'
    bm = bmesh.new(); bm.from_mesh(tube.data)   # the tube's own axes (along local z): cut its front cap and lower half away
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.z > 0.9 or (f.calc_center_median().y > 0 and abs(f.normal.z) < 0.5)], context='FACES')
    bm.to_mesh(tube.data); bm.free()
    rod = block('shellsub/piston/rod', (0.0, 6.2, 2.0), (1.0, 3.0, 1.0))
    piston = bpy.data.objects.new('shellsub/piston', None); scene.collection.objects.link(piston); piston.parent = root
    tube.parent = piston; rod.parent = piston; subject = 'shellsub'
if move == 'against':   # a block coming down while the camera rises
    body = block('subj', (0.0, 4.0, 4.0), (1.0, 1.0, 1.0)); subject = 'subj'
    body.keyframe_insert('location', frame=1); body.location = (0.0, 4.0, 1.0); body.keyframe_insert('location', frame=48)
start = (0, -6, 5)
aim_end = tuple(a + 0.35 * (b - a) for a, b in zip(start, (2.0, 6.0, 0.8)))   # straight toward the part, view held
ends = {{'still': ((0, -6, 5), 0), 'push': ((0, -2, 3.6), 0), 'truck': ((4, -6, 5), 0), 'roll': ((0, -6, 5), 20),
        'posts': ((4, -6, 5), 0), 'high': ((4, -6, 10), 0), 'aim': (aim_end, 0), 'against': ((0, -6, 6.5), 0)}}
first = {{'high': (0, -6, 10), 'against': (0, -6, 3.5), 'enter': (0, -3, 2.5), 'outside': (-6, 14, 2.5), 'piston': (0, -4, 2.0), 'piston_body': (0, -4, 2.0)}}.get(move, start)
ends.update({{'enter': ((0, 6.0, 2.5), 0), 'outside': ((6, 14, 2.5), 0), 'piston': ((0, 3.4, 2.0), 0), 'piston_body': ((0, 3.4, 2.0), 0)}})
if move in ('enter', 'outside', 'piston', 'piston_body'):   # level cameras: into the opening, or along the closed back
    def key(f, loc, roll=0.0):
        cam.location = loc; cam.rotation_euler = (math.radians(90), 0, math.radians(180 if move == 'outside' else 0))
        cam.keyframe_insert('location', frame=f); cam.keyframe_insert('rotation_euler', frame=f)
key(1, first); key(48, *ends[move])
for o in bpy.data.objects:
    if o.animation_data and o.animation_data.action:
        for fc in curves(o.animation_data.action):
            for k in fc.keyframe_points:
                k.interpolation = 'LINEAR'
parts = {{'aim': ('part',), 'piston': ('shellsub/piston',)}}.get(move, ())
result = grammar_synth.measure(192, sky=(video == '-'), subject=subject, parts=parts)   # beside a flat-sky render, drop the sky as the pixel probe does
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
    camera = grammar_core.camera(data['pairs'], data['fps'])
    return {**camera, 'feel': grammar_core.feel(data['pairs'], data['fps'], camera, aspect=data['height'] / data['width'])}


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
    pixel_pairs = json.loads(probe.read_text())['pairs']
    pixel = grammar_core.camera(pixel_pairs, 30)
    pixel_streak = grammar_core.feel(pixel_pairs, 30, pixel)['streak']

    def pixel_feel(move):
        clip = Path(tmp) / f'{move}.mp4'
        run(tmp, move, str(clip))
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', '30', '-i', str(clip) + '_f%04d.png', '-c:v', 'libx264', '-crf', '12',
                        '-pix_fmt', 'yuv420p', str(clip)], check=True)
        out = Path(tmp) / f'{move}_probe.json'
        subprocess.run([blender_binary(), '--background', '--factory-startup', '--python-exit-code', '1', '--python',
                        str(ROOT / 'studio/blender_ops/grammar_probe.py'), '--', str(clip), str(out), '0', '0', '1', '1'],
                       check=True, capture_output=True, timeout=900)
        pairs = json.loads(out.read_text())['pairs']
        return grammar_core.feel(pairs, 30, grammar_core.camera(pairs, 30))
    pixel_posts = pixel_feel('posts')
    assert pixel_posts['streak'] >= pixel_streak + STREAK_GAIN, (pixel_posts, pixel_streak)   # the pixels see the posts too
    synth_x, pixel_x = truck['rate_per_s']['x'], pixel['rate_per_s']['x']
    assert abs(synth_x - pixel_x) <= RATE_TOL * abs(pixel_x), (synth_x, pixel_x)
    # the same move past thin posts near the lens: the camera numbers stay, the feel changes ("숫자는 같아도 다른 느낌")
    nosky_streak = truck['feel']['streak']   # the run beside the render: sky dropped, as the pixel probe drops it
    plain = run(tmp, 'truck')   # with its sky, as every feel case below
    posts = run(tmp, 'posts')
    assert abs(posts['rate_per_s']['x'] - plain['rate_per_s']['x']) <= SAME_RATE * abs(plain['rate_per_s']['x']), (posts['rate_per_s'], plain['rate_per_s'])
    truck = plain
    assert posts['feel']['streak'] >= truck['feel']['streak'] + STREAK_GAIN, (posts['feel'], truck['feel'])
    assert posts['feel']['edges_per_s'] > truck['feel']['edges_per_s'] + EDGES_GAIN, (posts['feel'], truck['feel'])
    high = run(tmp, 'high')   # the same truck twice as high: half the flow per height
    ratio = high['feel']['eye_rate'] / truck['feel']['eye_rate']
    assert abs(ratio - 0.5) < 0.1, (high['feel'], truck['feel'])
    aim = run(tmp, 'aim')     # straight toward an off-centre part: it heads at the part
    assert aim['feel']['heading_on'] is not None and aim['feel']['heading_on'] >= 0.5 and aim['feel']['heading_off'] > 0.1, aim['feel']
    enter = run(tmp, 'enter')       # through the cut face into the box: the inside fills the picture and the camera ends inside
    outside = run(tmp, 'outside')   # along the closed back: never inside
    assert enter['feel']['inside_gain'] > 0.2 and enter['feel']['camera_inside'] > 0.1, enter['feel']
    assert (outside['feel']['inside'] or 0) < 0.05 and not outside['feel']['camera_inside'], outside['feel']
    piston = run(tmp, 'piston')     # the open tube is outside the body: its own shell, the rod inside it shown
    body_only = run(tmp, 'piston_body')   # the same shot, nothing named as explained: only the body could be opened
    assert piston['feel']['inside'] > 0.1 and (body_only['feel']['inside'] or 0) < 0.02, (piston['feel'], body_only['feel'])
    against = run(tmp, 'against')
    assert against['feel']['counter'] is not None and against['feel']['counter'] < -0.9, against['feel']
    checks = {'still': still['rate_per_s'], 'push': push['rate_per_s'], 'roll': roll['rate_per_s'],
              'truck_synthetic_x': synth_x, 'truck_pixel_x': pixel_x, 'truck_parallax': [truck['parallax_per_s'], pixel['parallax_per_s']],
              'truck_streak_synthetic_pixel': [nosky_streak, pixel_streak],
              'posts_vs_truck': {k: [posts['feel'][k], truck['feel'][k]] for k in ('streak', 'edges_per_s', 'near')},
              'posts_x': posts['rate_per_s']['x'], 'eye_rate_high_over_low': round(ratio, 3), 'aim': [aim['feel']['heading'], aim['feel']['heading_on']],
              'against_counter': against['feel']['counter'],
              'enter': {k: enter['feel'][k] for k in ('inside', 'inside_gain', 'camera_inside')},
              'outside': {k: outside['feel'][k] for k in ('inside', 'inside_gain', 'camera_inside')},
              'piston': {k: piston['feel'][k] for k in ('inside', 'inside_gain', 'camera_inside')}, 'posts_pixel': {k: pixel_posts[k] for k in ('streak', 'heading', 'lost')}}
print('GRAMMAR SYNTH SMOKE OK ' + json.dumps(checks))
