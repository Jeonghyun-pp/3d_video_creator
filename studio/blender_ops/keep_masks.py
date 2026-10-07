"""Keep masks (runs inside Blender): per frame, where the parts a shot keeps from Blender (shot.screen.keep) show -
white where they are seen through the real camera, black elsewhere, occluders included - at the output size.

A generated take may restyle every pixel; the parts an explanation depends on are put back from the Blender look render
through these masks (studio/generative/keep.py). They are the frame probe's classes (frame_probe._classes: the same part
ids, scene roles and occlusion) painted white and black under id_view's neutral settings, one Workbench render a frame.
Nothing here depends on a model: the same masks feed an open-weight model that takes masks directly.

Job JSON (after `--`): {output_dir, frame_count, width, height, parts: [part ids], frames?: [frames], split?: bool}. Writes
<output_dir>/frame_NNNNNN.png (8-bit grey, all parts) - or with split, <output_dir>/<n>/frame_NNNNNN.png per part (n its
index in parts: one colour each in a single pass, the frame probe's palette) - and keep_meta.json. `frames` limits the
pass to those frames (per-part QA samples a dozen).
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
    parts = job['parts']
    split = bool(job.get('split'))
    classes, keys, _ = frame_probe._classes({'subjects': [], 'key_parts': [{'id': p} for p in parts]})
    palette = frame_probe._palette()[2:]
    if split and len(parts) > len(palette):
        raise ValueError(f'KEEP_MASKS: {len(parts)} parts in one pass; at most {len(palette)}')
    colour = {f'key:{p}': (*palette[i][0], 1.0) if split else WHITE for i, p in enumerate(parts)}
    for obj in bpy.data.objects:
        cls = classes.get(obj.name, 'support')
        if cls == 'hidden':
            obj.hide_render = True
        else:
            obj.color = colour.get(cls, BLACK)
    id_view.apply(scene)
    scene.render.film_transparent = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new('StudioKeepWorld')
    scene.world.color = (0.0, 0.0, 0.0)
    render = scene.render
    render.resolution_x, render.resolution_y, render.resolution_percentage = job['width'], job['height'], 100
    render.image_settings.color_mode = 'RGB' if split else 'BW'
    out = Path(job['output_dir'])
    frames = job.get('frames') or list(range(job['frame_count']))
    for frame in frames:
        scene.frame_set(frame + 1)
        target = out / f'frame_{frame:06d}.png'
        render.filepath = str(target)
        bpy.ops.render.render(write_still=True)
        if split:
            _split(target, out, [palette[i][1] for i in range(len(parts))], frame)
    (out / 'keep_meta.json').write_text(json.dumps({'parts': keys, 'frames': frames, 'size': [job['width'], job['height']], 'split': split,
                                                    'seconds': round(time.perf_counter() - started, 3)}))


def _split(path, out, srgb, frame):
    """One grey mask per part from a palette render (nearest colour; black and unlisted colours are no part)."""
    import numpy as np
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = image.size
        pixels = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    rgb = pixels.reshape(h, w, 4)[::-1, :, :3]
    table = np.array([(0.0, 0.0, 0.0)] + list(srgb), dtype=np.float32)
    label = np.argmin(((rgb[:, :, None, :] - table[None, None]) ** 2).sum(-1), axis=2)
    for i in range(len(srgb)):
        mask = bpy.data.images.new(f'keep_{i}', w, h)
        on = (label == i + 1)[::-1].astype(np.float32)
        mask.pixels.foreach_set(np.repeat(on.reshape(-1), 4))
        (out / str(i)).mkdir(exist_ok=True)
        mask.filepath_raw = str(out / str(i) / f'frame_{frame:06d}.png')
        mask.file_format = 'PNG'
        mask.save()
        bpy.data.images.remove(mask)
    path.unlink()


main(json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text()))
