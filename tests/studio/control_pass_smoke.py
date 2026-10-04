"""Blender smoke for the hybrid control pass: depth/clay/canny, fixed near/far, fingerprint reuse."""
from pathlib import Path
import json
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from studio.common import file_hash, read_json
from studio.project import init_project
from studio.blender import build_shot
from studio.generative.control import build_control
from studio.qa_generative import structure

AUTHOR = '''import bpy
from mathutils import Vector
bpy.ops.mesh.primitive_cube_add(size=1, location=(0.6, 0, 0)); n=bpy.context.object; n.name='near_cube'; n['studio_id']='near_cube'
bpy.ops.mesh.primitive_cube_add(size=1.5, location=(-0.9, 8, 0)); f=bpy.context.object; f.name='far_cube'; f['studio_id']='far_cube'
bpy.ops.object.camera_add(location=(0, -6, 0.6)); c=bpy.context.object; bpy.context.scene.camera=c
for frame, x in ((1, -0.5), (30, 0.5)):
    c.location.x = x
    c.rotation_euler = (Vector((0, 4, 0)) - c.location).to_track_quat('-Z', 'Y').to_euler()
    c.keyframe_insert('location', frame=frame); c.keyframe_insert('rotation_euler', frame=frame)
bpy.context.scene['studio_authored_animation']=True
'''

with tempfile.TemporaryDirectory(prefix='control-smoke-') as root:
    p = Path(init_project('control_test', {'request': 'Control pass smoke', 'shots': [{'shot_id': 'shot_01', 'frame_count': 30, 'labels': [
        {'label_id': 'near', 'anchor': 'near_cube', 'start_frame': 0, 'end_frame': 30, 'text': 'near', 'slot': 'upper_left', 'occlusion_policy': 'hide'}]}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)
    build_shot(p, 'shot_01', author)
    started = time.monotonic()
    first = build_control(p, 'shot_01', height=320)
    seconds = round(time.monotonic() - started, 1)
    directory = Path(first['control_dir'])
    depth = sorted((directory / 'depth').glob('frame_*.png'))
    assert first['status'] == 'complete' and len(depth) == 30, (first['status'], len(depth))
    assert len(list((directory / 'clay').glob('frame_*.png'))) == 30
    meta = read_json(directory / 'control.json')
    assert 0 < meta['near'] < meta['far'], meta
    for kind in ('depth', 'clay', 'canny'):
        entry = meta['files'][kind]
        assert entry['frames'] == 30 and file_hash(entry['path']) == entry['sha256'], (kind, entry)
    # near cube is on the right half (x=+0.9), far cube on the left (x=-0.9, 8 m back).
    image = Image.open(depth[15]).convert('I')
    w, h = image.size
    scale = 65535 if image.getextrema()[1] > 255 else 255
    mean = lambda box: sum(getattr(image.crop(box), 'get_flattened_data', image.crop(box).getdata)()) / ((box[2] - box[0]) * (box[3] - box[1])) / scale
    near_value = mean((int(w * .6), int(h * .45), int(w * .8), int(h * .6)))
    far_value = mean((int(w * .3), int(h * .45), int(w * .4), int(h * .55)))
    corner = mean((0, 0, 8, 8))
    assert near_value > far_value > corner, (near_value, far_value, corner)
    anchors = read_json(first['anchors_path'])['frames']
    assert len(anchors) == 30 and all(a['visible'] for a in anchors) and anchors[15]['u'] > .5, anchors[15]
    clay = meta['files']['clay']['path']
    self_check = structure(clay, clay, anchors)
    assert self_check['passed'] and self_check['anchor_error']['measured'] > 0, self_check['reasons']
    second = build_control(p, 'shot_01', height=320)
    assert second['status'] == 'reused' and second['fingerprint'] == first['fingerprint']
    print(json.dumps({'ok': True, 'build_seconds': seconds, 'size': [meta['width'], meta['height']], 'near': round(meta['near'], 3), 'far': round(meta['far'], 3),
                      'depth_near_cube': round(near_value, 3), 'depth_far_cube': round(far_value, 3), 'depth_background': round(corner, 3),
                      'files': {k: v['frames'] for k, v in meta['files'].items()}, 'second_call': second['status'], 'clay_self_structure': {'iou': self_check['iou']['median'], 'anchors_measured': self_check['anchor_error']['measured']}}, indent=2))
