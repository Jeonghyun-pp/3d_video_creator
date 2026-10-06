"""Which camera keys are read in which context - pure Python (no bpy).

The shot schema closes camera.rig, camera.move and their timing, but whether a declared key is read depends on the
rig type, the timing profile or the kind of move: an orbit has no path to clear, a flythrough timed by a profile
ignores speed_mps, a linear timing has no burst. A key set where nothing reads it would be a silent no-op, so
shot validation refuses it (camera_unread). The rows are checked against the code: tests run camera_rig_core.bake
and timing_curve on recording dicts per type/profile, and scan camera_rig.py for the keys the Blender side reads.
"""
from __future__ import annotations

try:
    from .camera_rig_core import TIMING_DEFAULTS
except ImportError:   # imported from blender_ops on sys.path (Blender, tests)
    from camera_rig_core import TIMING_DEFAULTS

RIG_ALWAYS = {'type', 'look_target', 'guard_target', 'timing', 'aim_keys', 'lens_keys', 'roll', 'smoothing', 'screen_anchor', 'shake',
              'pitch_limit_deg', 'framing', 'motion_blur_shutter', 'guards'}
RIG_BY_TYPE = {
    'flythrough': {'path', 'speed_mps', 'start_offset_m', 'look_ahead_m', 'offset_keys'},
    'orbit': {'subject', 'orbit', 'sweep_deg'},
    'chase': {'subject', 'offset_keys'},
    'follow': {'subject', 'offset_keys'},
    'procedural': {'subject', 'script', 'offset_keys'},
}
TIMING_ALWAYS = {'profile', 'dwell', 'scope', 'distance_m'}
TIMING_BY_PROFILE = {
    'burst_settle': set(TIMING_DEFAULTS) | {'head_frac', 'head_share'},   # the curve's own defaults name its shape keys
    'points': {'points'},
    'linear': set(),
    'ease_in_out': set(),
}
# a move compiles into an orbit or a flythrough rig (camera_moves_core.plan 'kind'); these move keys only act on a flythrough
ORBIT_MOVES = ('orbit_reveal', 'turntable')
FLYTHROUGH_ONLY_MOVE_KEYS = ('whip_in_deg', 'clearance_m', 'look_target', 'arrive', 'dwell', 'framing')


def timing_unread(timing, where, distance=True):
    """Timing keys nothing reads for this profile (`distance`: the rig travels a path, so distance_m is read)."""
    if not timing:
        return []
    profile = timing.get('profile', 'burst_settle')
    known = TIMING_ALWAYS | TIMING_BY_PROFILE.get(profile, set())
    out = [f'{where}/{k}' for k in timing if k not in known]
    if not distance and 'distance_m' in timing:
        out.append(f'{where}/distance_m')
    if profile == 'burst_settle' and 'head_share' in timing and not timing.get('head_frac'):
        out.append(f'{where}/head_share (read only with head_frac > 0)')
    return out


def rig_unread(rig, where='camera/rig'):
    kind = rig.get('type')
    if kind not in RIG_BY_TYPE:
        return []                       # the schema refuses unknown types
    out = [f'{where}/{k}' for k in rig if k not in RIG_ALWAYS | RIG_BY_TYPE[kind]]
    timing = rig.get('timing')
    if kind == 'flythrough' and timing and 'speed_mps' in rig:
        out.append(f'{where}/speed_mps (timing sets the pace)')
    if kind == 'orbit':
        if 'sweep_deg' in rig and not timing:
            out.append(f'{where}/sweep_deg (read only with timing; deg_per_s sets the pace)')
        if timing and 'sweep_deg' in rig and 'deg_per_s' in (rig.get('orbit') or {}):
            out.append(f'{where}/orbit/deg_per_s (timing and sweep_deg set the pace)')
    return out + timing_unread(timing, f'{where}/timing', distance=kind == 'flythrough')


def move_unread(move, where='camera/move'):
    orbit = move.get('type') in ORBIT_MOVES
    out = [f'{where}/{k} (an orbit has no path)' for k in FLYTHROUGH_ONLY_MOVE_KEYS if orbit and k in move]
    if 'lens_end_mm' in move and not move.get('lens_mm'):
        out.append(f'{where}/lens_end_mm (read only with lens_mm)')
    return out + timing_unread(move.get('timing'), f'{where}/timing', distance=not orbit)


def camera_unread(camera):
    """Every camera value that nothing reads in its context."""
    out = []
    if camera.get('rig'):
        out += rig_unread(camera['rig'])
    if camera.get('move'):
        out += move_unread(camera['move'])
    if camera.get('target_anchor') and (camera.get('rig') or camera.get('move')):
        out.append('camera/target_anchor (keyed cameras only; a rig or move aims with look_target)')
    return out
