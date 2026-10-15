"""A shot's intent poses to pick from (host: studio/frames_check.candidates): on the authored checkpoint, the chain up to
the camera as frames check runs it, every pictured moment solved by its picture's intent (camera_moves._solve_intent)
keeping up to `keep` judged poses (the engine's choice first, then its alternatives, each a choice and not a copy:
INTENT_KEEP_APART_DEG), each rendered from its own pose at the moment's frame - Workbench, `height` px, one session.
Nothing saved. Writes candidates.json and <concept>_<n>.png into the job's output_dir.
Usage: blender -b <authored.blend> --python frame_candidates.py -- <job.json>
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import time

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).parent))
import camera_moves  # noqa: E402

def _camera(scene):
    """A camera for the candidates, the scene camera's sensor (the solve's: its long side fitted, 36 mm)."""
    data = bpy.data.cameras.new('frame_candidates')
    if scene.camera is not None:
        for attr in ('sensor_width', 'sensor_height', 'sensor_fit', 'clip_start', 'clip_end'):
            setattr(data, attr, getattr(scene.camera.data, attr))
    else:
        data.sensor_width, data.sensor_fit = 36.0, 'AUTO'
    cam = bpy.data.objects.new('frame_candidates', data)
    scene.collection.objects.link(cam)
    return cam


def _pose(cam, eye, aim, lens, roll_deg):
    eye, aim = Vector(eye), Vector(aim)
    quat = (aim - eye).to_track_quat('-Z', 'Y')
    # roll turns the camera's right toward its up (camera_frame_core.basis): about its own view axis
    cam.matrix_world = Matrix.Translation(eye) @ quat.to_matrix().to_4x4() @ Matrix.Rotation(math.radians(roll_deg), 4, 'Z')
    cam.data.lens = lens


def run(job):
    out = Path(job['output_dir'])
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    import generate
    import cheat_scene
    import reveal
    import show_scene
    generate.prepare(job, out)
    move = job['shot']['camera']['move']
    move.setdefault('params', {})['solve'] = 'intent'
    if any(f.get('cheats') for f in move.get('frames') or []):
        cheat_scene.apply(job)   # what is recorded: the scene the build will shoot
    rows = show_scene.apply(job)
    if rows:
        reveal.apply_camera_bound(job['shot'], {})
    ctx = camera_moves.frame_context(job, move)
    ctx['keep'] = int(job['keep'])   # poses per moment (studio/frames_check.CANDIDATES_KEEP)
    scene = bpy.context.scene
    aspect = scene.render.resolution_x / scene.render.resolution_y
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.render.resolution_y = int(job.get('height', 480))
    scene.render.resolution_x = max(2, round(scene.render.resolution_y * aspect / 2) * 2)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.use_compositing = False
    cam = _camera(scene)
    result = {'shot_id': job['shot_id'], 'moments': []}
    for fr in sorted(move['frames'], key=lambda f: f['at']):
        concept = (fr.get('picture') or {}).get('concept_id')
        moment = {'at': fr['at'], 'concept_id': concept, 'candidates': []}
        picked = fr.pop('chosen', None)   # the sheet shows every candidate; the one picked before is marked, not forced
        try:
            key, row, warnings = camera_moves.solve_frame(job, move, fr, ctx)
        except ValueError as error:
            moment['error'] = {'code': camera_moves._code(str(error), 'FRAME_UNSOLVED'), 'message': str(error)[:600]}
            result['moments'].append(moment)
            continue
        kept = row.get('alternatives') or []
        options = [{'key': key, 'row': row, 'warnings': warnings, 'score': kept[0]['score'] if kept else None}] + kept[1:]
        for n, option in enumerate(options[:ctx['keep']]):
            k, r = option['key'], option['row']
            scene.camera = cam
            scene.frame_set(k['frame'] + 1)
            _pose(cam, k['eye'], k['aim'], k['lens'], k.get('roll') or 0.0)
            image = out / f'{concept or fr["at"]}_{n}.png'
            scene.render.filepath = str(image)
            bpy.ops.render.render(write_still=True)
            moment['candidates'].append({
                'n': n, 'view': r['view'], 'offset': r.get('offset_from_picture'), 'lens_mm': r['lens_mm'],
                'score': option.get('score'),
                'must_see_px': r.get('must_see_px'), 'departed': r.get('departed') or [], 'placement': r.get('placement'),
                'eye': [round(v, 4) for v in k['eye']], 'aim': [round(v, 4) for v in k['aim']], 'roll_deg': round(k.get('roll') or 0.0, 3),
                'image': image.name,
                **({'picked': True} if picked and list(picked['view']) == list(r['view']) and abs(float(picked['lens_mm']) - r['lens_mm']) < 1e-6 else {})})
        result['moments'].append(moment)
    result['seconds'] = round(time.monotonic() - started, 1)
    (out / 'candidates.json').write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return result


if __name__ == '__main__' and '--' in sys.argv:
    run(json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text()))
