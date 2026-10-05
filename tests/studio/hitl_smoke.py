"""Human-in-the-loop generation, end to end with a real build and no paid call (paid_call is mocked).

A hybrid hall with two exemplar elements (stair in view, escalator behind the camera) builds a subjects_index; the
prompt maps only the on-screen exemplar from it; the review sheet is made; approval names the sheet and the user's
words; a prompt edit after approval is refused (ROUTE_APPROVAL_STALE); a fresh sheet + approval generates; an explain
shot's failed structure take is never used by the edit (falls back to the Blender pass); as a mood shot the same take
is selected in the user's words and used.
Run: .venv/bin/python tests/studio/hitl_smoke.py
"""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, load_shot, shot_path
from studio.blender import build_shot
from studio.edit import latest_render
from studio.generative import clip as clipmod
from studio.generative.control import build_control
from studio.generative.review import build_review
from studio.routing import approve, assert_route

AUTHOR = '''import bpy, json
from pathlib import Path
from mathutils import Vector
from modeling import build_subject
scene = bpy.context.scene
lib = Path(STUDIO_JOB['library_root']) / 'exemplars'
def place(name, sid, loc):
    spec = json.loads(sorted((lib / name).glob('v*'))[-1].joinpath('spec.json').read_text()); spec['subject_id'] = sid
    return build_subject(spec, root_location=loc)
bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, 0)); bpy.context.object.name = 'floor'; bpy.context.object['studio_id'] = 'floor'
place('stair', 'stair-001', (0, 6, 0))
place('escalator', 'escalator-001', (0, -30, 0))           # behind the camera: never on screen
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
cam.location = (0, -6, 3); cam.rotation_euler = (Vector((0, 6, 1.5)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
bpy.ops.object.light_add(type='SUN', location=(0, 0, 10))
scene['studio_authored_animation'] = True
'''
GEN = {'provider': 'fal', 'model': 'wan-2.2-vace', 'operation': 'video_to_video', 'prompt_ref': 'prompts/hall.txt', 'duration_seconds': 1.0,
       'usd_per_second': None, 'max_attempts': 1, 'text_in_frame': False, 'ai_disclosure': True, 'inputs': [],
       'prompt_spec': {'look': 'polished granite and brushed steel under warm light', 'add': ['a worker in an orange vest on the landing']}}


def fake_paid(endpoint, arguments, dest, **kwargs):
    out = Path(dest); out.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'mandelbrot=s=360x640:r=30', '-t', '1', '-pix_fmt', 'yuv420p',
                    str(out / 'raw.mp4')], check=True)   # unrelated pattern: fails the structure gate
    return {'request_id': 'r1', 'estimated_usd': 0.1, 'files': [{'path': str(out / 'raw.mp4')}]}


def set_route(p, **changes):
    shot = load_shot(p, 'hall'); shot['route'].update(changes); write_json(shot_path(p, 'hall'), shot)


checks = []
with tempfile.TemporaryDirectory(prefix='hitl-smoke-') as root:
    brief = {'request': 'HITL smoke', 'output': {'width': 360, 'height': 640, 'fps': 30, 'target_seconds': 1},
             'route_policy': {'allow_generative': True, 'budget_usd': 2, 'turnaround_required': True},
             'shots': [{'shot_id': 'hall', 'frame_count': 30, 'route_features': ['exact_geometry', 'photoreal_beyond_assets'], 'generative': GEN}]}
    p = Path(init_project('hitl_test', brief, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)
    built = build_shot(p, 'hall', author)
    index = read_json(p / 'shots/hall/versions' / built['scene_version'] / 'subjects_index.json')['subjects']
    on = {s['subject_id']: bool(s['on_screen_frames']) for s in index}
    assert on == {'stair-001': True, 'escalator-001': False}, on
    checks.append('subjects_index_with_on_screen_frames')

    control = build_control(p, 'hall', kinds=('depth', 'clay'), height=640)
    clay = Path(control['files']['clay']['path']).relative_to(p); depth = Path(control['files']['depth']['path']).relative_to(p)
    shot = load_shot(p, 'hall')
    shot['route']['generative']['inputs'] = [{'kind': 'previs', 'path': str(clay)}, {'kind': 'control', 'path': str(depth)}]
    write_json(shot_path(p, 'hall'), shot)
    text = clipmod.assemble_prompt(p, 'hall')['text']
    assert 'stair' in text.lower() and 'escalator' not in text.lower() and 'Add: a worker in an orange vest' in text, text
    checks.append('prompt_maps_on_screen_exemplars_only')

    review = build_review(p)
    assert Path(review['sheet']).is_file()
    try:
        approve(p, 'hall', '이대로 생성해도 좋아요')
        raise AssertionError('approved without a sheet')
    except StudioError as error:
        assert error.code == 'ROUTE_REVIEW_MISSING', error.code
    approve(p, 'hall', '이대로 생성해도 좋아요', budget_usd=2, review_id=review['review_id'])
    (p / 'prompts/hall.txt').write_text((p / 'prompts/hall.txt').read_text().replace('warm light', 'cold light'))
    try:
        assert_route(load_shot(p, 'hall'), 'generate', p)
        raise AssertionError('edited prompt accepted')
    except StudioError as error:
        assert error.code == 'ROUTE_APPROVAL_STALE', error.code
    checks += ['sheet_required_for_approval', 'edit_after_approval_refused']

    again = build_review(p, after=review['review_id'], user_words='조명은 차갑게 바꿔줘')
    approve(p, 'hall', '차가운 조명 버전으로 생성해', review_id=again['review_id'])
    with patch.object(clipmod, 'paid_call', side_effect=fake_paid):
        take = clipmod.generate_clip(p, 'hall', allow_paid=True, max_usd=1)
    assert take['policy'] == {'role': 'explain', 'usable': False, 'reasons': take['policy']['reasons'], 'warnings': take['policy']['warnings']}, take['policy']
    assert take['structure_qa']['preservation'] is not None
    checks.append('take_recorded_with_policy_and_split_metrics')

    # explain: the failed take is never used; the edit falls back to the Blender pass (reject_route)
    renders = p / 'shots/hall/renders/fake'; renders.mkdir(parents=True)
    shutil.copy(p / clay, renders / 'clip.mp4')
    write_json(renders / 'render.json', {'status': 'complete', 'scene_version': built['scene_version'], 'frame_count': 30, 'clip_path': str(renders / 'clip.mp4'), 'profile': 'preview'})
    base = latest_render(p, load_shot(p, 'hall'), 30, 'rough')
    assert not base['generated'] and 'reject_route' in base['warnings'][0], base['warnings']
    checks.append('explain_failed_take_falls_back_to_blender')

    # mood: the same take is usable once the user chooses it
    set_route(p, role='mood')
    key = Path(take['clip_path']).parent.name
    try:
        clipmod.select_take(p, 'hall', key)
        raise AssertionError('selected without the user')
    except StudioError:
        pass
    chosen = clipmod.select_take(p, 'hall', key, '이 테이크로 갈게요', {'a worker in an orange vest on the landing': 'absent'})
    assert any('absent' in w for w in chosen['warnings']), chosen['warnings']
    used = latest_render(p, load_shot(p, 'hall'), 30, 'rough')
    assert used['generated'] and any('structure (mood' in w for w in used['warnings']), used
    checks += ['mood_take_selected_in_user_words', 'absent_addition_reported']

print('STUDIO_HITL_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
