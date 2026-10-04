"""Look camera realism + compositor (runs inside Blender, build time, after camera_rig).

Ported from the photoreal research prototype (06_camera_comp/camera_comp.py, verified on 5.2.2).
Presets are starting points; each principle below is an invariant checked in code:
  * DOF: focus follows `focus_anchor` (resolved with scene_tools.anchor_for, keyed per frame as
    dof.focus_distance so semantic/centre anchors work too); f-stop is raised until the anchor
    owner's bbox stays within subject_max_coc_px on every sampled frame (max f/22).
  * Motion blur: shutter = scene_target_blur_px / p95 screen speed, then lowered until every
    label anchor smears <= anchor_max_blur_px. Position CENTER keeps the anchor at the blur centroid.
  * Shake: F-curve Noise modifiers (named StudioLook_Shake, seeded phase) on the camera's DELTA
    location/rotation channels, so the rig's baked keys are never touched and matrix_world (what
    world_to_camera_view / anchors_for_frame use) contains the shake. Amplitude is calibrated by
    sampling, then scaled down until the measured anchor jitter RMS <= shake_max_rms_px.
    Skipped when the rig already shook the camera (scene['studio_camera_rig_shake']).
  * Two-point: a per-frame delta rotation levels the camera (keeps yaw) and the lost pitch moves
    into shift_y; verified by re-projecting the original optical-axis point.
  * Anchored labels (labels present): the compositor is default-deny; only ops that keep every
    pixel where world_to_camera_view puts it (POSITION_PRESERVING) survive. Lens distortion moved
    anchors 2.4-3.9 px in the prototype, so it is never added on labelled shots.
  * Grain is intentionally not implemented (prototype finding); presets using it are rejected.
Everything this module writes is recorded in scene['StudioLook_camera_state'] /
scene['StudioLook_comp_state'] and undone by revert_camera_realism(). Pre-existing animation on a
channel this module would key is never overwritten (that step is skipped with a warning).
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import bpy
from bpy_extras import anim_utils
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from scene_tools import anchor_for

PRESETS = json.loads((Path(__file__).resolve().parent / 'look_data' / 'camera_presets.json').read_text())
PREFIX = 'StudioLook_'
SHAKE_MOD = PREFIX + 'Shake'
COMP_GROUP = PREFIX + 'Comp'
STATE = PREFIX + 'camera_state'
COMP_STATE = PREFIX + 'comp_state'
LABELS_FLAG = PREFIX + 'anchored_labels'
RIG_SHAKE = 'studio_camera_rig_shake'
DEFAULT_SHOT_TYPE = 'product'
TWO_POINT_MAX_ERR_NDC = 1e-4  # ~0.2 px at 1920; prototype measured ~1e-6
FSTOP_MAX = 22.0
# Compositor ops this module can build, and the subset that never moves pixels (allow-list for
# shots with 3D-anchored labels; anything not listed is dropped there, including future ops).
COMPOSITOR_OPS = {'glare', 'lens', 'vignette', 'soften', 'sharpen'}
POSITION_PRESERVING = {'glare', 'vignette', 'soften', 'sharpen'}


class _Occupied(Exception):
    """A channel this module would key already has someone else's animation."""


def _r(value, digits=4):
    return None if value is None else round(float(value), digits)


# ---------------------------------------------------------------- scene helpers
def _px(scene):
    return (scene.render.resolution_x * scene.render.resolution_percentage / 100,
            scene.render.resolution_y * scene.render.resolution_percentage / 100)


def _fitted_sensor(cam_data, scene):
    """Sensor size (mm) that maps to the fitted image dimension, and that dimension in px."""
    w, h = _px(scene)
    w *= scene.render.pixel_aspect_x
    h *= scene.render.pixel_aspect_y
    if cam_data.sensor_fit == 'AUTO':
        return cam_data.sensor_width, max(w, h)
    if cam_data.sensor_fit == 'HORIZONTAL':
        return cam_data.sensor_width, w
    return cam_data.sensor_height, h


def _frames(scene):
    return list(range(scene.frame_start, scene.frame_end + 1))


def _depth(cam, p):
    return -(cam.matrix_world.inverted() @ p).z


def _channelbag(idb):
    ad = idb.animation_data
    if not ad or not ad.action:
        return None
    return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)


def _fcurves(idb, path):
    cb = _channelbag(idb)
    return [fc for fc in cb.fcurves if fc.data_path == path] if cb else []


def _delta_rot_path(cam):
    """Delta channel Blender composes with the camera's own rotation mode (None: unsupported)."""
    if cam.rotation_mode == 'QUATERNION':
        return 'delta_rotation_quaternion'
    if cam.rotation_mode == 'AXIS_ANGLE':
        return None
    return 'delta_rotation_euler'


# ---------------------------------------------------------------- recorded edits (for revert)
class _Edits:
    """Every attribute write and created F-curve goes through here so revert is exact."""

    def __init__(self, scene, cam):
        self.scene, self.cam = scene, cam
        self.state = {'camera': cam.name, 'values': [], 'curves': [],
                      'had_action': {'obj': bool(cam.animation_data and cam.animation_data.action),
                                     'data': bool(cam.data.animation_data and cam.data.animation_data.action)}}

    def owner(self, key):
        return {'obj': self.cam, 'data': self.cam.data, 'render': self.scene.render}[key]

    def set(self, key, path, value):
        owner, attr = _resolve(self.owner(key), path)
        if not any(v[0] == key and v[1] == path for v in self.state['values']):
            self.state['values'].append([key, path, _jsonable(getattr(owner, attr))])
        setattr(owner, attr, value)

    def key(self, key, path, frame, value):
        """Set and key `path` at `frame`; refuses channels animated by someone else."""
        idb = self.owner(key)
        ours = [key, path] in self.state['curves']
        if not ours:
            if _fcurves(idb, path):
                raise _Occupied(f'{key}.{path}')
            self.set(key, path, getattr(*_resolve(idb, path)))
            self.state['curves'].append([key, path])
        owner, attr = _resolve(idb, path)
        setattr(owner, attr, value)
        idb.keyframe_insert(path, frame=frame)

    def linear(self):
        for key, path in self.state['curves']:
            for fc in _fcurves(self.owner(key), path):
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'

    def save(self):
        self.scene[STATE] = json.dumps(self.state, sort_keys=True)


def _resolve(owner, path):
    *parents, attr = path.split('.')
    for p in parents:
        owner = getattr(owner, p)
    return owner, attr


def _jsonable(v):
    if isinstance(v, bpy.types.ID):
        return {'id': v.name}
    if hasattr(v, '__len__') and not isinstance(v, str):
        return [float(x) for x in v]
    return v


def _restore_value(v):
    if isinstance(v, dict) and 'id' in v:
        return bpy.data.objects.get(v['id'])
    return v


# ---------------------------------------------------------------- anchors
def _label_anchor_ids(labels):
    ids = {(l['anchor'] if isinstance(l, dict) else str(l)) for l in labels}
    return sorted(i for i in ids if i)


def _anchor_points(anchor_ids):
    points = []
    for identifier in anchor_ids:
        _, p = anchor_for(identifier)
        if p is not None:
            points.append(p)
    return points


# ---------------------------------------------------------------- two-point perspective
def _two_point(scene, cam, ed, frames):
    """Per-frame delta rotation D with world rotation P*D*R_basis == level(yaw), pitch -> shift_y."""
    path = _delta_rot_path(cam)
    sensor_mm, _ = _fitted_sensor(cam.data, scene)
    base_shift = cam.data.shift_y
    rows = []
    for f in frames:
        scene.frame_set(f)
        mw = cam.matrix_world.copy()
        rw, rb = mw.to_quaternion(), cam.matrix_basis.to_quaternion()
        fwd = (rw @ Vector((0, 0, -1))).normalized()
        level = Vector((fwd.x, fwd.y, 0.0))
        if level.length < 1e-6:
            return {'two_point': False, 'failure': f'camera_two_point: optical axis vertical at frame {f}'}
        target = level.normalized().to_track_quat('-Z', 'Y')
        parent = rw @ rb.inverted()
        delta = parent.inverted() @ (target @ rw.inverted()) @ parent
        pitch = math.asin(max(-1.0, min(1.0, fwd.z)))
        shift = base_shift + math.tan(pitch) * cam.data.lens / sensor_mm
        probe = mw.translation + fwd * 10.0
        rows.append((f, delta, shift, probe, world_to_camera_view(scene, cam, probe)))
    prev = None
    for f, delta, shift, _, _ in rows:
        if path == 'delta_rotation_quaternion':
            if prev is not None and prev.dot(delta) < 0:
                delta = -delta
            value = delta
        else:
            value = delta.to_euler(cam.rotation_mode, prev) if prev is not None else delta.to_euler(cam.rotation_mode)
        prev = value.copy()
        ed.key('obj', path, f, value)
        ed.key('data', 'shift_y', f, shift)
    ed.linear()
    err = 0.0
    for f, _, _, probe, ndc0 in rows:
        scene.frame_set(f)
        ndc = world_to_camera_view(scene, cam, probe)
        err = max(err, abs(ndc.x - ndc0.x), abs(ndc.y - ndc0.y))
    rep = {'two_point': True, 'shift_y_start': _r(rows[0][2], 5), 'max_axis_reproj_err_ndc': _r(err, 7)}
    if err > TWO_POINT_MAX_ERR_NDC:
        rep['failure'] = f'camera_two_point: optical axis re-projection error {err:.2e} NDC > {TWO_POINT_MAX_ERR_NDC}'
    return rep


# ---------------------------------------------------------------- DOF
def _coc_px(scene, cam_data, subject_m, point_m, fstop):
    f = cam_data.lens / 1000.0
    s = max(subject_m, f * 1.01)
    d = max(point_m, 1e-4)
    coc_m = (f / fstop) * abs(d - s) / d * f / (s - f)
    sensor_mm, dim_px = _fitted_sensor(cam_data, scene)
    return coc_m * 1000.0 / sensor_mm * dim_px


def _dof(scene, cam, ed, anchor_id, fstop, max_coc_px, frames):
    owner, _ = anchor_for(anchor_id)
    if owner is None:
        return {'use_dof': False, 'reason': f'focus_anchor not found: {anchor_id}'}
    unit = scene.unit_settings.scale_length or 1.0
    meshes = [o for o in (owner, *owner.children_recursive) if o.type == 'MESH']
    ed.set('data', 'dof.focus_object', None)
    focus = {}
    for f in frames:
        scene.frame_set(f)
        _, p = anchor_for(anchor_id)
        focus[f] = max(_depth(cam, p), 1e-3)
        ed.key('data', 'dof.focus_distance', f, focus[f])
    ed.linear()
    sampled = frames[::max(1, len(frames) // 12)]
    n, worst = float(fstop), 0.0
    for _ in range(40):
        worst = 0.0
        for f in sampled:
            scene.frame_set(f)
            s = focus[f] * unit
            for o in meshes:
                for c in o.bound_box:
                    d = _depth(cam, o.matrix_world @ Vector(c)) * unit
                    if d > 0:
                        worst = max(worst, _coc_px(scene, cam.data, s, d, n))
        if worst <= max_coc_px or n >= FSTOP_MAX:
            break
        n = min(FSTOP_MAX, n * 1.19)  # third-stop steps
    ed.set('data', 'dof.aperture_fstop', n)
    ed.set('data', 'dof.use_dof', True)
    s0 = focus[frames[0]] * unit
    rep = {'use_dof': True, 'focus_anchor': anchor_id, 'fstop_requested': fstop, 'fstop': _r(n, 3),
           'subject_worst_coc_px': _r(worst, 2), 'max_coc_px': max_coc_px, 'focus_distance_m_start': _r(s0, 3),
           'background_coc_px_at_2x': _r(_coc_px(scene, cam.data, s0, 2 * s0, n), 2)}
    if worst > max_coc_px:
        rep['failure'] = f'camera_dof: subject CoC {worst:.2f} px > {max_coc_px} px even at f/{FSTOP_MAX:g}'
    return rep


# ---------------------------------------------------------------- shake
def _seed(seed, k):
    h = 2166136261
    for ch in f'{seed}:{k}':
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return (h % 10000) / 10.0


def _jitter_px(scene, cam, mods, probes_fn, frames):
    """RMS / max image displacement of the probes with shake vs. without, over the shot."""
    w, h = _px(scene)
    acc, n, peak = 0.0, 0, 0.0
    for f in frames:
        for m in mods:
            m.mute = True
        scene.frame_set(f)
        pts = probes_fn()
        a = [world_to_camera_view(scene, cam, p) for p in pts]
        for m in mods:
            m.mute = False
        scene.frame_set(f)
        for p, q in zip(a, [world_to_camera_view(scene, cam, p) for p in pts]):
            e2 = ((p.x - q.x) * w) ** 2 + ((p.y - q.y) * h) ** 2
            acc += e2; n += 1; peak = max(peak, math.sqrt(e2))
    return math.sqrt(acc / max(n, 1)), peak


def _shake(scene, cam, ed, name, cfg, seed, max_rms_px, probes_fn, frames):
    rot_path = _delta_rot_path(cam)
    sensor_mm, dim_px = _fitted_sensor(cam.data, scene)
    # Angular -> pixel: rms_px ~= rot_rad * focal / sensor * px. Telephoto magnifies, so cap first.
    rot_cap = math.degrees(max_rms_px / (cam.data.lens / sensor_mm * dim_px))
    rot_used = min(cfg['rot_deg_rms'], rot_cap)
    unit = scene.unit_settings.scale_length or 1.0
    targets = {'delta_location': lambda i: cfg['pos_mm_rms'] / 1000.0 / unit,
               'delta_rotation_euler': lambda i: math.radians(rot_used),
               # unit quaternion component ~ sin(angle/2); w stays keyed, Blender normalises.
               'delta_rotation_quaternion': lambda i: 0.0 if i == 0 else math.radians(rot_used) / 2}
    for path in ('delta_location', rot_path):
        if not _fcurves(cam, path):
            ed.key('obj', path, scene.frame_start, tuple(getattr(cam, path)))
        elif ['obj', path] not in ed.state['curves']:
            raise _Occupied(f'obj.{path}')
    scale = max(1.0, cfg['period_s'] * scene.render.fps)
    mods = []
    for path in ('delta_location', rot_path):
        for fc in sorted(_fcurves(cam, path), key=lambda c: c.array_index):
            target = targets[path](fc.array_index)
            if target <= 0:
                continue
            m = fc.modifiers.new('NOISE')
            m.name = SHAKE_MOD
            m.blend_type = 'ADD'
            m.scale, m.phase, m.depth, m.strength = scale, _seed(seed, f'{path}{fc.array_index}'), 1, 1.0
            # calibrate on a long window so short shots do not over-scale
            on = [fc.evaluate(f) for f in range(0, 2000, 3)]
            m.mute = True
            off = [fc.evaluate(f) for f in range(0, 2000, 3)]
            m.mute = False
            unit_rms = math.sqrt(statistics.fmean([(a - b) ** 2 for a, b in zip(on, off)])) or 1.0
            m.strength = target / unit_rms
            mods.append(m)
    window = frames if len(frames) > 1 else list(range(scene.frame_start, scene.frame_start + 240))
    rms, peak = _jitter_px(scene, cam, mods, probes_fn, window)
    k = 1.0
    if rms > max_rms_px:
        k = max_rms_px / rms
        for m in mods:
            m.strength *= k
        rms, peak = _jitter_px(scene, cam, mods, probes_fn, window)
    rep = {'shake': True, 'preset': name, 'seed': seed, 'pos_mm_rms': cfg['pos_mm_rms'],
           'rot_deg_rms_requested': cfg['rot_deg_rms'], 'rot_deg_rms_used_pre_cap': _r(rot_used, 4),
           'measured_scale': _r(k, 4), 'jitter_px_rms': _r(rms, 3), 'jitter_px_max': _r(peak, 3),
           'max_rms_px': max_rms_px, 'period_frames': scale, 'modifiers': len(mods)}
    if rms > max_rms_px * 1.01:
        rep['failure'] = f'camera_shake: anchor jitter {rms:.2f} px RMS > {max_rms_px} px'
    return rep


# ---------------------------------------------------------------- motion blur
def _motion_blur(scene, cam, ed, anchor_ids, inv, frames):
    if len(frames) < 2:
        ed.set('render', 'use_motion_blur', False)
        return {'motion_blur': False, 'reason': 'still'}, 0.0
    w, h = _px(scene)
    meshes = [o for o in scene.objects if o.type == 'MESH' and not o.hide_render]
    prev, speeds = {}, {}
    for f in frames:
        scene.frame_set(f)
        pts = [('m:' + o.name, o.matrix_world @ (sum((Vector(c) for c in o.bound_box), Vector()) / 8)) for o in meshes]
        pts += [('a:' + i, p) for i in anchor_ids for p in [anchor_for(i)[1]] if p is not None]
        cur = {}
        for key, p in pts:
            ndc = world_to_camera_view(scene, cam, p)
            if ndc.z > 0:
                cur[key] = Vector((ndc.x * w, ndc.y * h))
        for key in sorted(cur.keys() & prev.keys()):
            speeds.setdefault(key, []).append((cur[key] - prev[key]).length)
        prev = cur
    mesh_v = sorted(v for k, vs in speeds.items() if k.startswith('m:') for v in vs) or [0.0]
    p95 = mesh_v[int(0.95 * (len(mesh_v) - 1))]
    a_max = max([v for k, vs in speeds.items() if k.startswith('a:') for v in vs] or [0.0])
    shutter_max = inv['shutter_max']
    if scene.render.use_motion_blur:  # an explicit upstream shutter (e.g. rig motion_blur_shutter) is an upper bound
        shutter_max = min(shutter_max, scene.render.motion_blur_shutter)
    shutter = shutter_max if p95 < 1e-6 else min(shutter_max, inv['scene_target_blur_px'] / p95)
    if a_max > 1e-6:
        shutter = min(shutter, inv['anchor_max_blur_px'] / a_max)
    shutter = max(inv['shutter_min'], shutter)
    on = shutter > 0.02
    ed.set('render', 'use_motion_blur', on)
    if on:
        ed.set('render', 'motion_blur_shutter', shutter)
        ed.set('render', 'motion_blur_position', 'CENTER')
    rep = {'motion_blur': on, 'shutter': _r(shutter, 3), 'shutter_max': _r(shutter_max, 3), 'p95_px_per_frame': _r(p95, 2),
           'anchor_max_px_per_frame': _r(a_max, 2), 'anchor_blur_px': _r(a_max * shutter, 2),
           'scene_p95_blur_px': _r(p95 * shutter, 2)}
    return rep, (shutter if on else 0.0)


# ---------------------------------------------------------------- public: camera
def apply_camera_realism(scene, realism, *, labels=(), preset=None):
    """realism = shot.camera.realism. labels = shot labels (dicts with 'anchor') or anchor ids.
    preset: optional dict replacing the whole camera_presets.json content (tuning/tests).
    Order: lens -> two-point -> DOF -> shake -> motion blur (blur must see the final motion).
    Re-applying first reverts the previous camera realism, so equal inputs give equal scenes."""
    presets = preset or PRESETS
    inv = presets['invariants']
    realism = dict(realism or {})
    _revert_camera(scene)
    anchor_ids = _label_anchor_ids(labels)
    anchored = bool(labels)
    report = {'schema_version': 1, 'applied': [], 'shot_type': None, 'lens_mm': None, 'two_point': None,
              'dof': {'use_dof': False}, 'shutter': 0.0, 'motion_blur': {'motion_blur': False},
              'shake': {'shake': False}, 'anchored_labels': anchored, 'label_anchors': anchor_ids,
              'warnings': [], 'gate_failures': []}
    if anchored:
        scene[LABELS_FLAG] = True
    cam = scene.camera
    if cam is None or cam.type != 'CAMERA':
        report['gate_failures'].append('camera_realism: scene has no camera')
        return report
    shot_type = realism.get('shot_type') or DEFAULT_SHOT_TYPE
    if shot_type not in presets['shot_types']:
        report['gate_failures'].append(f'camera_realism: unknown shot_type {shot_type}')
        return report
    st = presets['shot_types'][shot_type]
    report['shot_type'] = shot_type
    seed = realism.get('seed', 0)
    frames = _frames(scene)
    frame0 = scene.frame_current
    missing = [i for i in anchor_ids if anchor_for(i)[0] is None]
    if missing:
        report['warnings'].append(f'camera_realism: label anchors not found: {missing}')
    resolved = [i for i in anchor_ids if i not in missing]
    ed = _Edits(scene, cam)

    def step(name, fn):
        try:
            rep = fn()
        except _Occupied as exc:
            report['warnings'].append(f'camera_{name}: skipped, {exc} already animated by another pass')
            return None
        if isinstance(rep, dict) and rep.get('failure'):
            report['gate_failures'].append(rep.pop('failure'))
        return rep

    try:
        if realism.get('lens_mm'):
            if _fcurves(cam.data, 'lens'):
                report['warnings'].append('camera_lens: lens_mm ignored, lens is animated (rig/author keys win)')
            else:
                ed.set('data', 'lens', float(realism['lens_mm'])); report['applied'].append('lens')
        report['lens_mm'] = _r(cam.data.lens, 3)

        if realism.get('two_point', st['two_point']):
            if cam.data.type != 'PERSP' or _delta_rot_path(cam) is None:
                report['warnings'].append(f'camera_two_point: skipped (camera {cam.data.type}, rotation {cam.rotation_mode})')
            else:
                rep = step('two_point', lambda: _two_point(scene, cam, ed, frames))
                if rep:
                    report['two_point'] = rep
                    if rep['two_point']:
                        report['applied'].append('two_point')

        focus = realism.get('focus_anchor')
        if st['dof'] and st['fstop'] and focus:
            rep = step('dof', lambda: _dof(scene, cam, ed, focus, st['fstop'], inv['subject_max_coc_px'], frames))
            if rep:
                report['dof'] = rep
                if rep['use_dof']:
                    report['applied'].append('dof')
                else:
                    report['warnings'].append(f"camera_dof: {rep['reason']}")
        else:
            ed.set('data', 'dof.use_dof', False)
            report['dof'] = {'use_dof': False, 'reason': 'shot_type has no DOF' if not (st['dof'] and st['fstop']) else 'no focus_anchor'}

        shake = realism['shake'] if 'shake' in realism else st['shake']
        shake = shake or 'none'
        if scene.get(RIG_SHAKE):
            report['shake'] = {'shake': False, 'reason': 'camera rig already applied shake'}
        elif shake not in presets['shake']:
            report['gate_failures'].append(f'camera_shake: unknown shake preset {shake}')
        elif presets['shake'][shake]['pos_mm_rms'] <= 0 and presets['shake'][shake]['rot_deg_rms'] <= 0:
            report['shake'] = {'shake': False, 'preset': shake}
        elif _delta_rot_path(cam) is None:
            report['warnings'].append('camera_shake: skipped, AXIS_ANGLE rotation mode is not supported')
        else:
            if resolved:
                probes = lambda: _anchor_points(resolved)
            else:  # no labels: judge the jitter at the focus / optical-axis point
                def probes():
                    mw = cam.matrix_world
                    d = cam.data.dof.focus_distance if cam.data.dof.use_dof else 5.0
                    return [mw.translation + (mw.to_quaternion() @ Vector((0, 0, -1))) * max(d, 0.5)]
            rep = step('shake', lambda: _shake(scene, cam, ed, shake, presets['shake'][shake], seed,
                                                inv['shake_max_rms_px'], probes, frames))
            if rep:
                report['shake'] = rep; report['applied'].append('shake')

        if realism.get('motion_blur', True):
            report['motion_blur'], report['shutter'] = _motion_blur(scene, cam, ed, resolved, inv, frames)
            report['shutter'] = _r(report['shutter'], 3)
            if report['motion_blur']['motion_blur']:
                report['applied'].append('motion_blur')
        else:
            ed.set('render', 'use_motion_blur', False)

        if anchored:
            group = scene.compositing_node_group
            if scene.render.use_compositing and group and not group.name.startswith(PREFIX) and \
                    any(n.bl_idname == 'CompositorNodeLensdist' for n in group.nodes):
                report['gate_failures'].append(f'camera_lens_distortion: compositor {group.name} distorts the image but the shot has 3D-anchored labels')
    finally:
        ed.save()
        scene.frame_set(frame0)
    return report


def _revert_camera(scene):
    raw = scene.get(STATE)
    removed = 0
    if raw:
        state = json.loads(raw)
        cam = bpy.data.objects.get(state['camera'])
        if cam is not None:
            owners = {'obj': cam, 'data': cam.data, 'render': scene.render}
            for key, path in state['curves']:
                cb = _channelbag(owners[key])
                for fc in [fc for fc in (cb.fcurves if cb else []) if fc.data_path == path]:
                    cb.fcurves.remove(fc); removed += 1
            for key, path, value in reversed(state['values']):
                owner, attr = _resolve(owners[key], path)
                setattr(owner, attr, _restore_value(value))
            for key in ('obj', 'data'):
                idb = owners[key]
                cb = _channelbag(idb)
                if not state['had_action'][key] and idb.animation_data and (cb is None or not len(cb.fcurves)):
                    action = idb.animation_data.action
                    idb.animation_data_clear()
                    if action is not None and action.users == 0:
                        bpy.data.actions.remove(action)
        del scene[STATE]
    # Sweep: no StudioLook_ modifier may outlive its state record.
    for obj in [o for o in bpy.data.objects if o.type == 'CAMERA']:
        cb = _channelbag(obj)
        for fc in (cb.fcurves if cb else []):
            for m in [m for m in fc.modifiers if m.name.startswith(PREFIX)]:
                fc.modifiers.remove(m); removed += 1
    if LABELS_FLAG in scene:
        del scene[LABELS_FLAG]
    return removed


def revert_camera_realism(scene):
    """Undo apply_camera_realism and compositor_setup: removes every StudioLook_ curve, modifier,
    node group and scene property, and restores the recorded original values."""
    comp = _revert_comp(scene)
    return {'camera_items_removed': _revert_camera(scene), 'compositor_removed': comp}


# ---------------------------------------------------------------- public: compositor
def _revert_comp(scene):
    raw = scene.get(COMP_STATE)
    if raw:
        state = json.loads(raw)
        scene.render.use_compositing = state['use_compositing']
        scene.compositing_node_group = bpy.data.node_groups.get(state['group']) if state['group'] else None
        del scene[COMP_STATE]
    groups = [g for g in bpy.data.node_groups if g.name.startswith(PREFIX) and g.bl_idname == 'CompositorNodeTree']
    for g in groups:
        bpy.data.node_groups.remove(g)
    return bool(groups)


def compositor_setup(scene, name, *, anchored_labels=None, preset=None):
    """name: a compositor preset ('off' | 'subtle_nograin' | ...). anchored_labels defaults to what
    apply_camera_realism recorded; when true only POSITION_PRESERVING ops are built.
    Chain: RenderLayers -> Glare(bloom) -> LensDistortion -> vignette -> soften/sharpen -> Output.
    The compositor runs after Cycles denoise. 'off' removes ours and restores the previous compositor."""
    table = (preset or PRESETS)['compositor']
    name = name or 'off'
    if name.startswith('_') or name not in table:
        raise ValueError(f'LOOK_QA_FAILED: unknown compositor preset {name!r}')
    cfg = {k: v for k, v in (table[name] or {}).items() if not k.startswith('_')}
    unsupported = sorted(set(cfg) - COMPOSITOR_OPS)
    if unsupported:
        raise ValueError(f'LOOK_QA_FAILED: compositor preset {name!r} uses unsupported ops {unsupported} (grain is not implemented)')
    if anchored_labels is None:
        anchored_labels = bool(scene.get(LABELS_FLAG))
    dropped = sorted(k for k in cfg if k not in POSITION_PRESERVING) if anchored_labels else []
    cfg = {k: v for k, v in cfg.items() if k not in dropped}
    _revert_comp(scene)
    if not cfg:
        return {'compositor': False, 'preset': name, 'ops': [], 'dropped_for_anchored_labels': dropped}
    scene[COMP_STATE] = json.dumps({'use_compositing': scene.render.use_compositing,
                                    'group': scene.compositing_node_group.name if scene.compositing_node_group else None})
    nt = bpy.data.node_groups.new(COMP_GROUP, 'CompositorNodeTree')
    nt.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    N, L = nt.nodes, nt.links
    rl = N.new('CompositorNodeRLayers'); rl.scene = scene
    out = N.new('NodeGroupOutput')
    img = rl.outputs['Image']
    g = cfg.get('glare')
    if g:
        n = N.new('CompositorNodeGlare')
        n.inputs['Type'].default_value = g['type']
        n.inputs['Quality'].default_value = g.get('quality', 'High')
        n.inputs['Threshold'].default_value = g['threshold']
        n.inputs['Strength'].default_value = g['strength']
        n.inputs['Size'].default_value = g['size']
        L.new(img, n.inputs['Image']); img = n.outputs['Image']
    ld = cfg.get('lens')
    if ld:
        n = N.new('CompositorNodeLensdist')
        n.inputs['Distortion'].default_value = ld['distortion']
        n.inputs['Dispersion'].default_value = ld['dispersion']
        n.inputs['Fit'].default_value = ld['distortion'] > 0  # 5.2.2: positive = barrel -> black corners without Fit
        L.new(img, n.inputs['Image']); img = n.outputs['Image']
    v = cfg.get('vignette')
    if v:
        # natural vignetting: 1 - k * r^p, r = distance from centre in units of the half-diagonal
        co = N.new('CompositorNodeImageCoordinates'); L.new(img, co.inputs['Image'])
        sub = N.new('ShaderNodeVectorMath'); sub.operation = 'SUBTRACT'
        sub.inputs[1].default_value = (0.5, 0.5, 0.0)
        L.new(co.outputs['Normalized'], sub.inputs[0])
        ln = N.new('ShaderNodeVectorMath'); ln.operation = 'LENGTH'; L.new(sub.outputs['Vector'], ln.inputs[0])
        r = N.new('ShaderNodeMath'); r.operation = 'DIVIDE'; r.inputs[1].default_value = math.sqrt(0.5)
        L.new(ln.outputs['Value'], r.inputs[0])
        pw = N.new('ShaderNodeMath'); pw.operation = 'POWER'; pw.inputs[1].default_value = v['power']
        L.new(r.outputs['Value'], pw.inputs[0])
        k = N.new('ShaderNodeMath'); k.operation = 'MULTIPLY_ADD'
        k.inputs[1].default_value = -v['strength']; k.inputs[2].default_value = 1.0
        L.new(pw.outputs['Value'], k.inputs[0])
        mix = N.new('ShaderNodeMix'); mix.data_type = 'RGBA'; mix.blend_type = 'MULTIPLY'
        mix.inputs['Factor'].default_value = 1.0
        cc = N.new('CompositorNodeCombineColor')
        for ch in ('Red', 'Green', 'Blue'):
            L.new(k.outputs['Value'], cc.inputs[ch])
        L.new(img, mix.inputs[6]); L.new(cc.outputs['Image'], mix.inputs[7]); img = mix.outputs[2]
    s = cfg.get('soften') or cfg.get('sharpen')
    if s:
        # Box Sharpen 0.08 gave visible bolt-edge halos in the hero test -> presets use a light Soften
        n = N.new('CompositorNodeFilter'); n.inputs['Type'].default_value = 'Soften' if cfg.get('soften') else 'Box Sharpen'
        n.inputs['Factor'].default_value = s['factor']
        L.new(img, n.inputs['Image']); img = n.outputs['Image']
    L.new(img, out.inputs[0])
    scene.compositing_node_group = nt
    scene.render.use_compositing = True
    return {'compositor': True, 'preset': name, 'ops': sorted(cfg), 'dropped_for_anchored_labels': dropped}
