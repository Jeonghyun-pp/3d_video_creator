"""Render-quality settings applied by the frozen render worker (ported from photoreal research 01).

Principle: the job's render_settings (in the fingerprint) are the only inputs; enum values
are checked against the live Blender RNA so an upgrade that renames one fails loudly.
Look decisions owned by the scene author (view transform, look, exposure, light paths) are
reported, never changed here.
"""
import bpy

# View transforms that tone-map scene-linear HDR into display range (no hard clip).
TONEMAPPED_VIEWS = {'AgX', 'Filmic', 'Khronos PBR Neutral', 'ACES 1.3', 'ACES 2.0'}


def _set_enum(owner, prop, value):
    try:
        setattr(owner, prop, value)
    except TypeError as error:
        raise ValueError(f'{prop}={value!r} rejected by Blender {bpy.app.version_string}: {error}') from error


GPU_BACKENDS = ('METAL', 'OPTIX', 'CUDA')   # the Mac's, then a rented NVIDIA server's (OptiX first: RT cores)
LAST_BACKEND = {'name': None}


def select_device(scene, want):
    """The first GPU backend this machine has (Metal on the Mac, OptiX or CUDA on a rented NVIDIA server) when requested
    and present, else CPU; threads always AUTO. Until 2026-10-08 only Metal was tried, so an NVIDIA server rendered on
    its CPU without saying so; the backend used is reported (renderer_actual.json) and is part of the job's settings.

    Saved scenes can carry FIXED threads (hero: 2), which made CPU renders 2.5x slower.
    """
    scene.cycles.device = 'CPU'
    scene.render.threads_mode = 'AUTO'
    LAST_BACKEND['name'] = None
    if want != 'GPU':
        return 'CPU'
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        for backend in GPU_BACKENDS:
            try:
                prefs.compute_device_type = backend
            except TypeError:   # not compiled into this build (Metal on Linux, OptiX on macOS)
                continue
            prefs.get_devices()
            if any(d.type == backend for d in prefs.devices):
                for device in prefs.devices:
                    device.use = device.type == backend
                scene.cycles.device = 'GPU'
                LAST_BACKEND['name'] = backend
                break
    except Exception:
        scene.cycles.device = 'CPU'
    return scene.cycles.device


def apply_render_profile(scene, settings):
    report = {'profile': settings.get('profile'), 'warnings': []}
    if scene.render.engine == 'CYCLES':
        c = scene.cycles
        report['device'] = select_device(scene, settings.get('device', 'GPU'))
        report['compute'] = (LAST_BACKEND['name'] or 'CPU').lower()
        c.samples = settings['samples']
        c.use_adaptive_sampling = True
        c.adaptive_threshold = settings.get('adaptive_threshold', .02)
        c.use_denoising = True
        _set_enum(c, 'denoiser', 'OPENIMAGEDENOISE')
        _set_enum(c, 'denoising_input_passes', 'RGB_ALBEDO_NORMAL')
        _set_enum(c, 'denoising_prefilter', 'ACCURATE')
        _set_enum(c, 'denoising_quality', 'HIGH')  # no measurable time cost on M4 GPU OIDN
        c.denoising_use_gpu = report['device'] == 'GPU'
        c.use_animated_seed = False  # fixed seed measured less flicker on slow moves
        c.use_light_tree = True
        scene.render.use_persistent_data = bool(settings.get('animation'))
        if c.max_bounces < 8 or c.glossy_bounces < 4 or c.diffuse_bounces < 3:
            report['warnings'].append('scene has reduced bounces (author choice); measured darker metal on hero')
        # firefly controls are the scene's light-path choice: reported, and flagged when switched off
        report['light_paths'] = {'blur_glossy': c.blur_glossy, 'sample_clamp_indirect': c.sample_clamp_indirect,
                                 'sample_clamp_direct': c.sample_clamp_direct}
        if c.blur_glossy == 0 or c.sample_clamp_indirect == 0:
            report['warnings'].append('glossy filter or indirect clamp is off (author choice); expect fireflies on glossy bounces')
        report.update(samples=c.samples, threshold=c.adaptive_threshold, denoiser=c.denoiser,
                      threads_mode=scene.render.threads_mode)
    view = scene.view_settings
    if view.view_transform not in TONEMAPPED_VIEWS:
        report['warnings'].append(f'view_transform {view.view_transform!r} clips highlights (Otis Standard: 29 % pixels >= 254; AgX 0 %)')
    report.update(view_transform=view.view_transform, look=view.look, exposure=view.exposure)
    image = scene.render.image_settings
    _set_enum(image, 'file_format', 'PNG')
    _set_enum(image, 'color_mode', 'RGB')
    _set_enum(image, 'color_depth', settings.get('png_depth', '8'))
    report['png_depth'] = image.color_depth
    return report
