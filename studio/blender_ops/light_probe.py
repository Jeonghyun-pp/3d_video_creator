"""Light contributions (runs in a workbench session): how much each light group adds to each picture region, so a
region's brightness target can be solved for (studio/light_targets.py) instead of guessed light by light.

Light adds linearly in a path tracer: the picture's linear luminance is the sum of what each light gives alone. One
low-resolution Cycles render per group with every other group off (the world counts as a group; the emission of
meshes - windows, light panels - is what is left with everything off), under the Standard view at the scene's
exposure, read back as linear float (EXR) and averaged per region (regions_core cells and our id classes). Everything
the probe changes is given back.

Groups: each look light by its rig name (key, fill, moon_rim ...), the look's practicals together, the look's sun, the
world, every other light by its own name (author lights), and `emission`.
"""
from __future__ import annotations

from pathlib import Path
import time

import bpy

import regions_core

LOOK_PREFIX = 'StudioLook_'


def _group(obj):
    name = obj.name
    if name.startswith(LOOK_PREFIX):
        name = name[len(LOOK_PREFIX):]
        return 'practicals' if name.startswith('practical_') else name
    return f'author:{name}'


def _pixels(path):
    import numpy as np
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = image.size
        buf = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(image)
    return buf.reshape(h, w, 4)[::-1]   # Blender stores rows bottom-up: top row first, like the PNGs the host reads


def _masks(h, w, id_png, palette):
    import numpy as np
    out = {}
    for name, (x0, y0, x1, y1) in regions_core.cells().items():
        mask = np.zeros((h, w), dtype=bool)
        mask[round(y0 * h):round(y1 * h), round(x0 * w):round(x1 * w)] = True
        out[name] = mask
    if id_png:
        ids = _pixels(id_png)
        if ids.shape[:2] == (h, w):
            codes = (np.clip(ids[:, :, :3], 0, 1) * 255 + 0.5).astype(np.int32)   # a byte PNG reads back as its stored values (as preview reads it)
            alpha = ids[:, :, 3] > 0.98
            for colour, key in palette.items():
                rgb = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
                hit = alpha & (np.abs(codes - np.array(rgb)).max(axis=2) <= 2)
                if hit.any():
                    out[f'class:{key}'] = out.get(f'class:{key}', np.zeros((h, w), dtype=bool)) | hit
    return out


def contributions(state, frame=1, size=160, samples=16):
    """{groups: {group: {region: linear Y}}, lights: {group: [object names]}, size, seconds}."""
    import numpy as np
    from workbench_tools import preview
    started = time.perf_counter()
    scene = bpy.context.scene
    if scene.camera is None:
        raise ValueError('light contributions need the shot camera: apply the shot first (set_shot_value)')
    ids = preview(state, views=('shot',), passes=('id',), size=size, frame=frame)
    id_png, palette = ids['images'].get('shot', {}).get('id'), ids['palette']
    scene.frame_set(frame)
    lights = [o for o in scene.objects if o.type == 'LIGHT' and not o.hide_render]
    groups = {}
    for obj in lights:
        groups.setdefault(_group(obj), []).append(obj)
    world = scene.world
    # every Background node of the world: a mix of two (sky + ground) is still world light
    backgrounds = [n for n in world.node_tree.nodes if n.type == 'BACKGROUND'] if world is not None and world.use_nodes else []
    r, view = scene.render, scene.view_settings
    saved = []   # (owner, attribute, value) of every setting the probe changes, each restored whatever happens

    def change(owner, *pairs):
        """Set (attribute, value) pairs of one owner, all saved first: Blender rewrites a dependent setting the moment
        the first changes (OPEN_EXR makes the depth '32', Standard makes the look 'None'), so saving each just before
        it is set would keep the rewritten value, not the scene's."""
        saved.extend((owner, attribute, getattr(owner, attribute)) for attribute, _ in pairs)
        for attribute, value in pairs:
            setattr(owner, attribute, value)
    out_dir = Path(state['session_dir']) / 'light_probe' / f'{int(time.time() * 1000)}'
    out_dir.mkdir(parents=True, exist_ok=True)
    ow, oh = state.get('output_size') or (r.resolution_x, r.resolution_y)   # the shot view's frame, as preview sizes it (the id pass must match)
    width, height = max(2, int(ow * int(size) / max(ow, oh))), max(2, int(oh * int(size) / max(ow, oh)))
    result = {}
    try:
        from render_profile import select_device
        change(r, ('engine', 'CYCLES'))
        for owner, attribute in ((scene.cycles, 'device'), (r, 'threads_mode')):   # what select_device sets on the scene
            saved.append((owner, attribute, getattr(owner, attribute)))
        select_device(scene, 'GPU')
        change(scene.cycles, ('samples', int(samples)))
        change(r, ('resolution_x', width), ('resolution_y', height), ('resolution_percentage', 100), ('film_transparent', False))
        change(r.image_settings, ('file_format', 'OPEN_EXR'), ('color_mode', 'RGBA'), ('color_depth', '32'))
        change(view, ('view_transform', 'Standard'), ('look', 'None'))
        change(r, ('use_compositing', False))   # the compositor is a picture choice, not light (the switch Blender 5 reads)
        if hasattr(scene, 'use_nodes'):
            change(scene, ('use_nodes', False))
        strengths = [n.inputs['Strength'].default_value for n in backgrounds]
        for obj in lights:
            saved.append((obj, 'hide_render', obj.hide_render))
        for node in backgrounds:
            saved.append((node.inputs['Strength'], 'default_value', node.inputs['Strength'].default_value))
        change(r, ('filepath', r.filepath))
        masks = None
        for group in [*groups, 'world', 'emission']:
            for name, objs in groups.items():
                for obj in objs:
                    obj.hide_render = name != group
            for node, strength in zip(backgrounds, strengths):
                node.inputs['Strength'].default_value = strength if group == 'world' else 0.0
            path = out_dir / f'{group.replace(":", "_")}.exr'
            r.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            px = _pixels(path)
            if masks is None:
                masks = _masks(px.shape[0], px.shape[1], id_png, palette)
            lum = 0.2126 * px[:, :, 0] + 0.7152 * px[:, :, 1] + 0.0722 * px[:, :, 2]
            result[group] = {name: round(float(lum[m].mean()), 6) for name, m in masks.items() if m.any()}
    finally:
        # in the order they were changed: an enum whose items depend on another (color_depth on file_format, look on
        # view_transform) is set back after the one it depends on, or Blender refuses it and the rest stays changed
        for owner, attribute, value in saved:
            setattr(owner, attribute, value)
    return {'groups': result, 'lights': {g: [o.name for o in objs] for g, objs in groups.items()},
            'exposure': view.exposure, 'size': [width, height], 'seconds': round(time.perf_counter() - started, 2)}
