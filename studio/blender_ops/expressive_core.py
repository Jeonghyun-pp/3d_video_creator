"""Expressive settings a shot declares - grade, compositor, engine settings, add-ons - and what reads them. Pure Python.

One table for shot validation (studio/project.py: a key nothing reads is refused), the edit grammar (studio/shot_edit.py:
every value reachable by path) and the apply step (blender_ops/expressive.py, run after the look in generate.py, so it
is outside the frozen look code and its fingerprint). A Blender smoke applies every row and reads it back.

shot.render:
  grade            {view_transform, look, exposure, exposure_offset_ev, gamma, white_balance_k, curve: [[x, y], ...]}
  compositor       {ops: [{op, ...params}]}  - nodes spliced in before the look's output, in order
  engine_settings  {cycles: {...}, eevee: {...}, render: {...}}  - RNA names below
  addons           [module names] - add-ons bundled with Blender only (build-time tools; render workers do not load them)

What the renderer sets itself at render time (render_frames.py / render_profile.py) is refused with a pointer to the
field that does act (RENDERER_OWNS): writing it would be a silent no-op.
"""
from __future__ import annotations

GRADE = {'view_transform': str, 'look': str, 'exposure': float, 'exposure_offset_ev': float, 'gamma': float,
         'white_balance_k': float, 'curve': list}
# op -> (node type, {param: socket name}); a param sets the node input of that name (Blender 5.2 compositor sockets)
COMPOSITOR_OPS = {
    'glare': ('CompositorNodeGlare', {'type': 'Type', 'quality': 'Quality', 'threshold': 'Threshold', 'smoothness': 'Smoothness',
                                      'strength': 'Strength', 'saturation': 'Saturation', 'size': 'Size', 'streaks': 'Streaks',
                                      'streaks_angle': 'Streaks Angle', 'fade': 'Fade'}),
    'lens_distortion': ('CompositorNodeLensdist', {'type': 'Type', 'distortion': 'Distortion', 'dispersion': 'Dispersion', 'fit': 'Fit'}),
    'color_balance': ('CompositorNodeColorBalance', {'factor': 'Factor', 'lift': ('Lift', 'RGBA'), 'gamma': ('Gamma', 'RGBA'),
                                                     'gain': ('Gain', 'RGBA')}),
    'hue_saturation': ('CompositorNodeHueSat', {'hue': 'Hue', 'saturation': 'Saturation', 'value': 'Value', 'factor': 'Factor'}),
    'bright_contrast': ('CompositorNodeBrightContrast', {'brightness': 'Brightness', 'contrast': 'Contrast'}),
    'sharpen': ('CompositorNodeFilter', {'factor': 'Factor'}),
    'soften': ('CompositorNodeFilter', {'factor': 'Factor'}),
}
FILTER_TYPE = {'sharpen': 'Sharpen', 'soften': 'Soften'}
POSITION_CHANGING = {'lens_distortion'}   # moves pixels: anchored labels would leave their anchors
ENGINE_SETTINGS = {
    'cycles': {'max_bounces', 'diffuse_bounces', 'glossy_bounces', 'transmission_bounces', 'volume_bounces', 'transparent_max_bounces',
               'sample_clamp_direct', 'sample_clamp_indirect', 'blur_glossy', 'caustics_reflective', 'caustics_refractive',
               'filter_width', 'pixel_filter_type', 'light_sampling_threshold', 'min_light_bounces', 'min_transparent_bounces'},
    'eevee': {'use_raytracing', 'ray_tracing_method', 'use_shadows', 'shadow_ray_count', 'shadow_step_count', 'shadow_resolution_scale',
              'use_fast_gi', 'fast_gi_method', 'fast_gi_distance', 'fast_gi_quality', 'gi_diffuse_bounces', 'clamp_surface_direct',
              'clamp_surface_indirect', 'volumetric_start', 'volumetric_end', 'volumetric_samples', 'volumetric_tile_size',
              'use_volumetric_shadows', 'bokeh_max_size', 'bokeh_threshold', 'motion_blur_steps', 'light_threshold'},
    'render': {'use_motion_blur', 'motion_blur_shutter', 'motion_blur_position', 'filter_size', 'dither_intensity'},
}
RENDERER_OWNS = {
    'render': {'engine': 'render.engine', 'resolution_x': 'project output', 'resolution_y': 'project output', 'resolution_percentage': 'project output',
               'fps': 'project output', 'fps_base': 'project output', 'film_transparent': None, 'threads_mode': None, 'use_persistent_data': None,
               'filepath': None},
    'cycles': {'samples': 'render.samples / the render profile', 'device': 'STUDIO_RENDER_DEVICE', 'use_adaptive_sampling': 'the render profile',
               'adaptive_threshold': 'the render profile', 'use_animated_seed': None, 'use_denoising': 'the render profile',
               'denoising_use_gpu': None, 'use_light_tree': None, 'denoiser': None, 'denoising_input_passes': None,
               'denoising_prefilter': None, 'denoising_quality': None},
    'eevee': {'taa_render_samples': 'render.samples / the render profile'},
}


def unread(render):
    """Paths under shot.render.{grade, compositor, engine_settings} that nothing reads (or the renderer overrides)."""
    out = []
    for key in (render.get('grade') or {}):
        if key not in GRADE:
            out.append(f'render/grade/{key} (known: {sorted(GRADE)})')
    for i, op in enumerate((render.get('compositor') or {}).get('ops') or []):
        row = COMPOSITOR_OPS.get(op.get('op'))
        if row is None:
            out.append(f"render/compositor/ops/{i}/op {op.get('op')!r} (known: {sorted(COMPOSITOR_OPS)})")
            continue
        out += [f"render/compositor/ops/{i}/{k} ({op['op']} reads {sorted(row[1])})" for k in op if k != 'op' and k not in row[1]]
    for owner, values in (render.get('engine_settings') or {}).items():
        known = ENGINE_SETTINGS.get(owner)
        if known is None:
            out.append(f'render/engine_settings/{owner} (known: {sorted(ENGINE_SETTINGS)})')
            continue
        for key in values:
            if key in RENDERER_OWNS.get(owner, {}):
                where = RENDERER_OWNS[owner][key]
                out.append(f'render/engine_settings/{owner}/{key} (the renderer sets it at render time' + (f'; use {where})' if where else ')'))
            elif key not in known:
                out.append(f'render/engine_settings/{owner}/{key} (known: {sorted(known)})')
    return out


def declared(render):
    return any(render.get(k) for k in ('grade', 'compositor', 'engine_settings', 'addons'))
