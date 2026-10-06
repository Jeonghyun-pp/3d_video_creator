"""What the author step changed, recorded - not refused (free bpy is allowed; nothing it does goes unseen).

state() runs in stage 1 before the author (build_author.py) and audit() in stage 2 on the scene the author handed on
(build_scene.py). Recorded: render / colour / engine settings that changed (and whether the renderer or the look will
override them anyway), linked libraries (path and hash; made local and packed before the checkpoint), add-ons, the
camera on shots whose camera is generated, and studio ids that disappeared. Refused (hard): 3D text that renders
(SKILL rule #2: words are screen graphics, never geometry) and a linked library whose file is missing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy

# Settings an author may change, read as RNA paths from the scene. The renderer sets these itself at render time, so a
# change to them has no effect there (render_frames.py / render_profile.py): recorded as renderer_overrides.
SETTINGS = ('render.engine', 'render.resolution_x', 'render.resolution_y', 'render.fps', 'render.film_transparent',
            'render.use_motion_blur', 'render.motion_blur_shutter', 'render.filter_size', 'render.use_compositing',
            'view_settings.view_transform', 'view_settings.look', 'view_settings.exposure', 'view_settings.gamma',
            'view_settings.use_curve_mapping', 'cycles.samples', 'cycles.max_bounces', 'cycles.diffuse_bounces',
            'cycles.glossy_bounces', 'cycles.transmission_bounces', 'cycles.volume_bounces', 'cycles.transparent_max_bounces',
            'cycles.sample_clamp_direct', 'cycles.sample_clamp_indirect', 'cycles.blur_glossy', 'cycles.caustics_reflective',
            'cycles.caustics_refractive', 'cycles.use_denoising', 'eevee.taa_render_samples')
RENDERER_OWNS = ('render.engine', 'render.resolution_x', 'render.resolution_y', 'render.fps', 'render.film_transparent',
                 'cycles.samples', 'cycles.use_denoising', 'eevee.taa_render_samples')


def _get(scene, path):
    obj = scene
    for part in path.split('.'):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj if isinstance(obj, (int, float, str, bool)) else str(obj)


def _camera_digest(scene):
    cam = scene.camera
    if cam is None:
        return None
    keys = []
    if cam.animation_data and cam.animation_data.action:
        for curve in getattr(cam.animation_data.action, 'fcurves', []):
            keys.append((curve.data_path, curve.array_index, [tuple(round(v, 6) for v in k.co) for k in curve.keyframe_points]))
    data = {'name': cam.name, 'matrix': [round(v, 6) for row in cam.matrix_world for v in row], 'lens': round(cam.data.lens, 4), 'keys': keys}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _compositor_digest(scene):
    tree = getattr(scene, 'compositing_node_group', None) or getattr(scene, 'node_tree', None)
    if tree is None:
        return None
    nodes = sorted((n.bl_idname, n.name) for n in tree.nodes)
    links = sorted((l.from_node.name, l.to_node.name) for l in tree.links)
    return hashlib.sha256(json.dumps([nodes, links]).encode()).hexdigest()


def state():
    scene = bpy.context.scene
    import addon_utils
    return {'settings': {p: _get(scene, p) for p in SETTINGS}, 'compositor': _compositor_digest(scene),
            'studio_ids': sorted({o['studio_id'] for o in bpy.data.objects if isinstance(o.get('studio_id'), str)}),
            'camera': _camera_digest(scene), 'libraries': sorted(l.filepath for l in bpy.data.libraries),
            'addons': sorted(m.__name__ for m in addon_utils.modules() if addon_utils.check(m.__name__)[1])}


def _sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def audit(job, output):
    """Write author_audit.json; return (errors, warnings). Run before linked data is made local."""
    scene = bpy.context.scene
    before_path = Path(output) / 'pre_author_state.json'
    before = json.loads(before_path.read_text()) if before_path.is_file() else None
    now = state()
    errors, warnings = [], []
    changed = []
    if before:
        for path in SETTINGS:
            if before['settings'].get(path) != now['settings'].get(path):
                changed.append({'path': path, 'before': before['settings'].get(path), 'after': now['settings'].get(path),
                                'renderer_overrides': path in RENDERER_OWNS})
        if before['compositor'] != now['compositor']:
            changed.append({'path': 'compositor', 'before': before['compositor'], 'after': now['compositor'], 'renderer_overrides': False})
    libraries = []
    for library in bpy.data.libraries:
        path = bpy.path.abspath(library.filepath)
        digest = _sha(path)
        libraries.append({'path': library.filepath, 'sha256': digest})
        if digest is None and not library.packed_file:
            errors.append({'code': 'LINKED_ASSET_UNRESOLVED', 'detail': f'{library.filepath} is linked but not found'})
    fonts = [o.name for o in scene.objects if o.type == 'FONT' and not o.hide_render]
    if fonts:
        errors.append({'code': 'TEXT_3D_FORBIDDEN', 'detail': f'3D text renders: {fonts[:6]} - words are screen graphics (shot.titles / labels)'})
    camera = job['shot'].get('camera') or {}
    if before and (camera.get('rig') or camera.get('move')) and before['camera'] is not None and before['camera'] != now['camera']:
        warnings.append('AUTHOR_CAMERA_OVERRIDDEN: the author changed the camera on a shot whose camera is generated (rig / move); '
                        'the generated camera replaces it')
    removed = sorted(set(before['studio_ids']) - set(now['studio_ids'])) if before else []
    if removed:
        warnings.append(f'AUTHOR_REMOVED_IDS: {removed[:10]}')
    report = {'schema_version': 1, 'settings_changed': changed, 'libraries': libraries, 'fonts': fonts,
              'addons': {'before': (before or {}).get('addons'), 'after': now['addons']}, 'removed_ids': removed,
              'errors': errors, 'warnings': warnings}
    (Path(output) / 'author_audit.json').write_text(json.dumps(report, indent=1, default=str))
    return errors, warnings
