"""Keep masks (runs inside Blender): per frame, where the parts a shot keeps from Blender (shot.screen.keep) show -
white where they are seen through the real camera, black elsewhere, occluders included - at the output size.

A generated take may restyle every pixel; the parts an explanation depends on are put back from the Blender look render
through these masks (studio/generative/keep.py). They are the frame probe's classes (frame_probe._classes: the same part
ids, scene roles and occlusion) painted white and black under id_view's neutral settings, one Workbench render a frame.
Nothing here depends on a model: the same masks feed an open-weight model that takes masks directly.

Job JSON (after `--`): {output_dir, frame_count, width, height, parts: [part ids]}. Writes <output_dir>/frame_NNNNNN.png
(8-bit grey) and keep_meta.json.
"""
import json
from pathlib import Path
import sys
import time

import bpy

sys.path.insert(0, str(Path(__file__).parent))
import frame_probe
import id_view

WHITE, BLACK = (1.0, 1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)


def main(job):
    started = time.perf_counter()
    scene = bpy.context.scene
    classes, keys, _ = frame_probe._classes({'subjects': [], 'key_parts': [{'id': p} for p in job['parts']]})
    for obj in bpy.data.objects:
        cls = classes.get(obj.name, 'support')
        if cls == 'hidden':
            obj.hide_render = True
        else:
            obj.color = WHITE if cls.startswith('key:') else BLACK
    id_view.apply(scene)
    scene.render.film_transparent = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new('StudioKeepWorld')
    scene.world.color = (0.0, 0.0, 0.0)
    render = scene.render
    render.resolution_x, render.resolution_y, render.resolution_percentage = job['width'], job['height'], 100
    render.image_settings.color_mode = 'BW'
    out = Path(job['output_dir'])
    for frame in range(job['frame_count']):
        scene.frame_set(frame + 1)
        render.filepath = str(out / f'frame_{frame:06d}.png')
        bpy.ops.render.render(write_still=True)
    (out / 'keep_meta.json').write_text(json.dumps({'parts': keys, 'frames': job['frame_count'], 'size': [job['width'], job['height']],
                                                    'seconds': round(time.perf_counter() - started, 3)}))


main(json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text()))
