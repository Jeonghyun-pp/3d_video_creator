import json
import os
from pathlib import Path
import sys
import time
import bpy

sys.path.insert(0, str(Path(__file__).parent))
from scene_tools import anchors_for_frame
from render_profile import apply_render_profile

job = json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
scene = bpy.context.scene
settings = job['render_settings']
scene.render.resolution_x = settings['width']; scene.render.resolution_y = settings['height']; scene.render.resolution_percentage = 100
scene.render.fps = settings['fps']; scene.render.fps_base = 1
scene.render.film_transparent = False
if settings.get('engine'):
    engine = settings['engine']
    if engine == 'BLENDER_EEVEE_NEXT':
        engine = 'BLENDER_EEVEE' if bpy.app.version >= (5, 0, 0) else 'BLENDER_EEVEE_NEXT'
    scene.render.engine = engine
profile_report = apply_render_profile(scene, settings)
if hasattr(scene, 'eevee') and hasattr(scene.eevee, 'taa_render_samples'):
    scene.eevee.taa_render_samples = settings['samples']
frames_dir = Path(job['output_dir']) / 'frames'; frames_dir.mkdir(parents=True, exist_ok=True)
anchors, times = [], []
for index, frame in enumerate(job['frames']):
    if Path(job['cancel_path']).exists():
        raise RuntimeError('Cancelled by user')
    scene.frame_set(frame+1)
    anchors.extend(anchors_for_frame(job['shot']['labels'], frame))
    if frame in job['missing_frames']:
        started = time.monotonic()
        temporary = frames_dir / f'frame_{frame:06d}.pending.png'
        scene.render.filepath = str(temporary)
        bpy.ops.render.render(write_still=True)
        os.replace(temporary, frames_dir / f'frame_{frame:06d}.png')
        times.append({'frame': frame, 'seconds': time.monotonic()-started})
    progress = {'completed_frames': index+1, 'total_frames': len(job['frames']), 'last_frame': frame, 'updated_at_unix': time.time()}
    target = Path(job['progress_path']); temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(progress)); os.replace(temp, target)
(Path(job['output_dir']) / 'anchors.json').write_text(json.dumps({'schema_version':1,'frames':anchors},ensure_ascii=False))
(Path(job['output_dir']) / 'frame_times.json').write_text(json.dumps(times,indent=2))

(Path(job['output_dir']) / 'renderer_actual.json').write_text(json.dumps({'engine':scene.render.engine,'device':scene.cycles.device if scene.render.engine=='CYCLES' else None,'blender_version':bpy.app.version_string, **profile_report}))
