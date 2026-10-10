"""The world flow end to end (studio/world.py, generative/inputs.py, keep.mixed_previs): a world with a place settled
from beat pictures, a shot list naming its elements, the shot built with a Blender subject and a clay stand-in, the
stand-in linked, and the clay-fill input: the look render where Blender owns the look, clay where the model fills,
with the shot's concept as the reference picture and the world's light in the prompt. Stand-in clips, no paid call.
Run: .venv/bin/python tests/studio/world_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from studio import concept, decisions, world
from studio.blender import build_shot
from studio.common import file_hash, now, read_json, write_json
from studio.generative.inputs import fill_inputs
from studio.generative.keep import build_keep_masks
from studio.project import init_project, load_shot, shot_path

WORDS = '좋아요 이대로 진행해요'
SCRIPT = {'lines': [{'line_id': 'l1', 'text': '위층에서 뛰면 소리가 내려옵니다.', 'illustrative': 'the event'}]}
SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'m': {'color': [0.6, 0.6, 0.6]}},
         'primitives': [{'id': 'slab', 'shape': 'box', 'size': [3, 2, 0.3], 'at': [0, 0, 0.15], 'material': 'm'},
                        {'id': 'room', 'shape': 'box', 'size': [1.2, 1, 1.2], 'at': [0, 0.5, 0.9], 'material': 'm'}]}
CAMERA = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
          'keys': [{'frame': 0, 'location': [0, -6, 1.2], 'target': [0, 0, 0.5]}, {'frame': 29, 'location': [0.3, -5.8, 1.2], 'target': [0, 0, 0.5]}]}


def clip(path, colour, size=(1080, 1920), frames=30):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={colour}:s={size[0]}x{size[1]}:r=30', '-frames:v', str(frames),
                    '-pix_fmt', 'yuv420p', str(path)], check=True)


def picture(root, name, colour):
    file = Path(root) / f'{name}.png'
    Image.new('RGB', (90, 160), colour).save(file)
    return file


def centre(pattern, frame):
    mask = Image.open(pattern % frame).convert('L')
    white = [i for i, v in enumerate(mask.tobytes()) if v > 127]
    return (sum(i % mask.width for i in white) // len(white), sum(i // mask.width for i in white) // len(white)), mask.size


checks = []
with tempfile.TemporaryDirectory(prefix='world-smoke-') as root:
    p = Path(init_project('world_smoke', {'request': 'floor noise', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, root)['project_path'])
    for layer, body in (('brief', {'topic': 'floor noise', 'audience': 'residents', 'length_s': 1, 'key_message': 'it travels', 'subject_mode': 'schematic'}),
                        ('facts', {'sources': [], 'claims': []}), ('script', SCRIPT)):
        decisions.approve(p, layer, WORDS, decisions.propose(p, layer, body)['rev'])
    beat = concept.add_beat(p, 'b1', picture(root, 'beat', (220, 170, 120)), 'a warm living room above a cut floor')['picture']
    body = {'kind': 'continuous', 'place': 'a Seoul apartment', 'time_light': {'time_of_day': 'late afternoon', 'mood': 'warm and quiet',
                                                                             'temperature_k': 4500},
            'beats': [{'beat_id': 'b1', 'line_ids': ['l1'], 'pictures': [beat]}], 'reference': beat,
            'elements': [{'id': 'floor', 'function': 'subject', 'description': 'the floor slab', 'requirements': ['exact'],
                          'owner': {'shape': 'blender', 'look': 'blender'}, 'why': 'its thickness is the explanation'},
                         {'id': 'room', 'function': 'set', 'description': 'the living room above', 'owner': {'shape': 'blender', 'look': 'generative'},
                          'why': 'a real room is what the model draws well'},
                         {'id': 'estate', 'function': 'context', 'description': 'the estate outside', 'requirements': ['far_background'],
                          'owner': {'shape': 'generative', 'look': 'generative'}, 'why': 'tells where we are; nothing to measure'}]}
    sheet = decisions.propose(p, 'world', body)
    assert not sheet['errors'], sheet['errors']
    decisions.approve(p, 'world', WORDS, sheet['rev'])
    world.link(p, 'floor', ['slab'])
    shots = {'shots': [{'shot_id': 's', 'purpose': 'the floor under the room', 'line_ids': ['l1'], 'duration_s': 1,
                        'elements': ['floor', 'room', 'estate'], 'beat_ids': ['b1']}]}
    decisions.approve(p, 'shotlist', WORDS, decisions.propose(p, 'shotlist', shots)['rev'])
    shot = load_shot(p, 's')
    assert (shot['route']['mode'], shot['route']['role'], shot['screen']['generate_only']) == ('hybrid', 'explain', ['@background']), shot['screen']
    checks.append('world_owners_make_a_hybrid_shot')

    shot['scene'], shot['camera'] = SCENE, CAMERA
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    linked = world.link(p, 'room', ['room'])                       # the stand-in, built now
    assert linked['shots'] == ['s'] and load_shot(p, 's')['screen']['generate_only'] == ['room', '@background']
    checks.append('linking_a_stand_in_updates_the_shot_regions')

    # stand-ins: a blue look render and a grey control clay of this version
    shot_dir = shot_path(p, 's').parent
    render = shot_dir / 'renders' / 'look1' / 'clip.mp4'; clip(render, 'blue')
    write_json(render.parent / 'render.json', {'status': 'complete', 'scene_version': built['scene_version'], 'profile': 'review', 'full_sequence': True,
                                               'clip_path': str(render), 'clip_sha256': file_hash(render), 'fingerprint': 'look1fingerprint0000000',
                                               'created_at': now()})
    control = shot_dir / 'control' / 'c1'
    for kind, colour in (('clay', 'gray'), ('depth', 'white')):
        clip(control / f'{kind}.mp4', colour)
    write_json(control / 'control.json', {'scene_version': built['scene_version'], 'fingerprint': 'c' * 24, 'created_at': now(),
                                          'files': {k: {'path': str(control / f'{k}.mp4')} for k in ('clay', 'depth')}})
    first = [(i['kind'], i['path']) for i in fill_inputs(p, 's')['inputs']]   # a continuous world's reference is enough to start
    assert ('reference_image', beat['path']) in first, first
    added = concept.add(p, 's', picture(root, 'concept', (230, 180, 130)), 'the warm room over the cut slab')
    concept.pick(p, 's', added['concept_id'], user_words='이 그림으로 갈게요')
    filled = fill_inputs(p, 's')
    kinds = [(i['kind'], i['path']) for i in filled['inputs']]
    assert kinds[0][0] == 'previs' and 'mixed_previs' in kinds[0][1], kinds
    assert ('reference_image', added['path']) in kinds and ('reference_image', beat['path']) in kinds, kinds
    checks.append('clay_fill_sends_the_mixed_input_with_concept_and_world_reference')

    mixed = p / kinds[0][1]
    masks = build_keep_masks(p, 's', ['room'])
    (cx, cy), size = centre(masks['pattern'], 15)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(mixed), '-vf', 'select=eq(n\\,15)', '-frames:v', '1', str(Path(root) / 'm.png')], check=True)
    frame = Image.open(Path(root) / 'm.png').convert('RGB')
    room_px = frame.getpixel((cx * frame.width // size[0], cy * frame.height // size[1]))
    slab_mask = build_keep_masks(p, 's', ['slab'])
    (sx, sy), _ = centre(slab_mask['pattern'], 15)
    slab_px = frame.getpixel((sx * frame.width // size[0], sy * frame.height // size[1]))
    assert abs(room_px[0] - room_px[2]) < 30 and room_px[0] > 90, ('room is clay grey', room_px)
    assert slab_px[2] > 150 and slab_px[0] < 80, ('slab keeps the look render', slab_px)
    checks.append('mixed_input_is_clay_where_the_model_fills_and_render_elsewhere')

    from studio.generative.clip import assemble_prompt
    text = assemble_prompt(p, 's')['text']
    assert 'late afternoon, warm and quiet' in text and 'Grey clay areas' in text, text
    checks.append('prompt_reads_the_world_light_and_names_the_clay')

print('STUDIO_WORLD_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
