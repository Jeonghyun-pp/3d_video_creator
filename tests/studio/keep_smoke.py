"""Keep masks and kept takes (studio/generative/keep.py): the parts a shot keeps (shot.screen.keep) are masked where the
camera sees them - occluders included, matching the frame probe - and a generated take gets the Blender look render
back inside the masks (clip_kept.mp4), which the edit then uses. No paid call: the take and the render are stand-ins.
Run: .venv/bin/python tests/studio/keep_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from studio.blender import build_shot
from studio.common import file_hash, now, read_json, write_json
from studio.generative.keep import build_keep_masks, keep_take
from studio.project import init_project, shot_path

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'m': {'color': [0.7, 0.4, 0.2]}},
         'primitives': [{'id': 'pump', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0.5], 'material': 'm'},
                        {'id': 'valve', 'shape': 'box', 'size': [0.3, 0.3, 0.3], 'at': [0, 3, 0.15], 'material': 'm'},
                        {'id': 'wall', 'shape': 'box', 'size': [3, 0.2, 3], 'at': [0, 1.6, 1.5], 'material': 'm'}]}
CAMERA = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
          'keys': [{'frame': 0, 'location': [0, -5, 1.5], 'target': [0, 0, 0.5]}, {'frame': 29, 'location': [0.6, -5, 1.5], 'target': [0, 0, 0.5]}]}


def colour_clip(path, colour, frames=30, size=(1080, 1920)):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s={size[0]}x{size[1]}:r=30', '-frames:v', str(frames),
                    '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', str(path)], check=True)


def frame_of(clip, n, out):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(clip), '-vf', f'select=eq(n\\,{n})', '-frames:v', '1', str(out)], check=True)
    return Image.open(out).convert('RGB')


checks = []
with tempfile.TemporaryDirectory(prefix='keep-smoke-') as root:
    p = Path(init_project('keep_test', {'request': 'keep smoke', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'camera': CAMERA, 'key_parts': [{'id': 'pump'}], 'screen': {'keep': ['pump']}})
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    probe = read_json(p / 'shots/s/versions' / built['scene_version'] / 'frame_report.json')

    masks = build_keep_masks(p, 's')
    assert masks['frames'] == 30 and masks['size'] == [1080, 1920] and masks['status'] == 'built', masks
    for row in probe['frames']:   # the mask covers what the probe saw as the pump, at full size
        mask = Image.open(masks['pattern'] % row['frame']).convert('L')
        white = [i for i, v in enumerate(mask.tobytes()) if v > 127]
        share = len(white) / (1080 * 1920)
        want = row['shapes']['key:pump']
        cx = sum(i % 1080 for i in white) / len(white) / 1080
        cy = sum(i // 1080 for i in white) / len(white) / 1920
        assert abs(share - want['share']) <= 0.05 * want['share'] + 0.002, (row['frame'], share, want['share'])
        assert abs(cx - want['centroid'][0]) < 0.01 and abs(cy - want['centroid'][1]) < 0.01, (row['frame'], cx, cy, want['centroid'])
    assert build_keep_masks(p, 's')['status'] == 'reused'
    checks.append(f"masks_match_the_probe_classes ({masks['seconds']} s for 30 frames at 1080x1920)")

    hidden = build_keep_masks(p, 's', ['valve'])   # behind the wall: occluders count, nothing to keep shows
    assert max(Image.open(hidden['pattern'] % 0).convert('L').tobytes()) == 0
    checks.append('occluded_part_masks_nothing')

    # Stand-ins: a red generated take and a blue look render of the same version.
    shot_dir = shot_path(p, 's').parent
    take = shot_dir / 'generated' / 'take1' / 'clip.mp4'; colour_clip(take, 'red')
    write_json(take.parent / 'clip.json', {'schema_version': 1, 'status': 'complete', 'shot_id': 's', 'frame_count': 30,
                                           'clip_path': str(take), 'clip_sha256': file_hash(take)})
    render = shot_dir / 'renders' / 'look1' / 'clip.mp4'; colour_clip(render, 'blue')
    write_json(render.parent / 'render.json', {'status': 'complete', 'scene_version': built['scene_version'], 'profile': 'review',
                                               'full_sequence': True, 'clip_path': str(render), 'clip_sha256': file_hash(render),
                                               'fingerprint': 'look1fingerprint', 'created_at': now()})
    kept = keep_take(p, 's')
    assert [k['take'] for k in kept['kept']] == ['take1'], kept
    out = Path(kept['kept'][0]['path'])
    row = probe['frames'][0]
    image = frame_of(out, row['frame'], Path(root) / 'f.png')
    cx, cy = row['shapes']['key:pump']['centroid']
    inside, outside = image.getpixel((int(cx * 1080), int(cy * 1920))), image.getpixel((40, 40))
    assert inside[2] > 180 and inside[0] < 60 and outside[0] > 180 and outside[2] < 60, (inside, outside)
    assert read_json(take.parent / 'clip.json')['kept']['parts'] == ['pump']
    checks.append('take_gets_the_render_back_inside_the_mask')

    from studio.edit import latest_generated
    shot = read_json(shot_path(p, 's'))
    shot['route'] = {'mode': 'hybrid', 'role': 'mood', 'status': 'proposed', 'decided_by': 'agent', 'approval_evidence': None,
                     'approved_at': None, 'generative': {'model': 'wan-2.2-vace', 'operation': 'video_to_video', 'inputs': []}}
    chosen = latest_generated(p, shot, 30)
    assert chosen and Path(chosen['clip']) == out, chosen
    checks.append('edit_uses_the_kept_clip')

print('STUDIO_KEEP_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
