"""camera fit: choose a move's timing so its predicted screen motion reads like a motion style.

The move decides where the camera goes; this decides how it covers that path. One Blender probe measures the
screen flow along the built shot's compiled rig as a function of progress (camera_fit_probe.py). Any timing
curve then predicts a per-frame motion series (flow integrated over each frame's progress step, scaled by the
proxy calibration) whose envelope is scored against the style's feature quartiles - no rendering. Bounded
Nelder-Mead over (burst_frac, burst_share, hold_frac) - the burst_settle family, the only one with shape parameters.
A shot that declares another profile (camera.move.timing.profile: linear, ease_in_out, points) chose its rhythm on
purpose (a constant push, a slow retreat): the fit keeps it and only scores it, unless --profile burst_settle asks
for that candidate. Shape features are trusted; the absolute level only to
the calibration's +/-50 %, so it is weighted down and reported as a hint (a level far off means the path,
not the timing, is too short or too far for the style). Output is a candidate; --apply revises the shot.
"""
from __future__ import annotations

import bisect
import tempfile
from pathlib import Path

from .common import REPO, StudioError, blender_binary, now, read_json, run_command, safe_path, write_json
from .motion_style import FLOOR, judge, load, shot_envelope
from .blender_ops import camera_rig_core as rig_core   # pure math, no Blender

SHAPE = ('burst_share', 'peak_t', 'decay_half_s', 'hold_frac', 'head_whip')
LEVEL_WEIGHT = 0.25          # level trusted to +/-50 % (motion_styles/_calibration.json)
BOUNDS = {'burst_frac': (0.1, 0.5), 'burst_share': (0.35, 0.85), 'hold_frac': (0.0, 0.45)}
HEAD_BOUNDS = (0.0, 0.6)     # slow head, searched only when the move declares arrive constraints
ARRIVE_WEIGHT = 400.0        # per second of early arrival, squared: meaning outranks the style's rhythm
MAX_EVALS = 160


def _calibration():
    path = REPO / 'library' / 'motion_styles' / '_calibration.json'
    return read_json(path)['mad_per_proxy'] if path.is_file() else 1.0


def predict(profile, timing, frame_count, k):
    """Predicted per-pair motion (MAD units) of a timing curve over a probe profile {u, G}."""
    u_grid, g = profile['u'], profile['G']

    def at(u):
        i = min(len(u_grid) - 2, max(0, bisect.bisect_right(u_grid, u) - 1))
        x = (u - u_grid[i]) / (u_grid[i + 1] - u_grid[i])
        return g[i] + (g[i + 1] - g[i]) * x
    curve = rig_core.timing_curve(timing)
    progress = [curve(f / max(1, frame_count - 1)) for f in range(frame_count)]
    return [k * (at(b) - at(a)) for a, b in zip(progress, progress[1:])]


def score(style, envelope):
    total = 0.0
    for feature in SHAPE + ('mean_mad',):
        q = style['features'][feature]
        spread = max(q['p75'] - q['p25'], FLOOR[feature])
        weight = LEVEL_WEIGHT if feature == 'mean_mad' else 1.0
        total += weight * ((envelope[feature] - q['median']) / spread) ** 2
    return total


def _bounds(arrive):
    return {**BOUNDS, **({'head_frac': HEAD_BOUNDS} if arrive else {})}


def _timing(x, base, bounds=BOUNDS):
    t = {**base, 'profile': 'burst_settle'}
    for (name, (lo, hi)), v in zip(bounds.items(), x):
        t[name] = round(lo + (hi - lo) * v, 4)
    return t


def _x(timing, bounds=BOUNDS):
    return [min(1.0, max(0.0, (timing.get(name, lo if name == 'head_frac' else (lo + hi) / 2) - lo) / (hi - lo))) for name, (lo, hi) in bounds.items()]


def arrive_penalty(timing, arrive, frame_count, fps):
    """Seconds-squared of early arrival at each constrained mark ([{u, not_before_s}]), weighted."""
    if not arrive:
        return 0.0
    curve = rig_core.timing_curve(timing)
    total = 0.0
    for a in arrive:
        f = next((f for f in range(frame_count) if curve(f / max(1, frame_count - 1)) >= a['u'] - 1e-9), frame_count - 1)
        early = max(0.0, a['not_before_s'] - f / fps)
        total += ARRIVE_WEIGHT * early ** 2
    return total


def fit(profile, style, frame_count, start_timing, fps=30, k=1.0, arrive=None):
    """arrive: [{u, not_before_s}] - the camera may not pass progress u before that time."""
    from .subject_fit import nelder_mead
    base = {key: v for key, v in start_timing.items() if key not in ('points',)}
    bounds = _bounds(arrive)

    def objective(x):
        timing = _timing(x, base, bounds)
        try:
            env = shot_envelope(predict(profile, timing, frame_count, k), fps)
        except (ValueError, StudioError):
            return 1e6  # infeasible curve (burst past the hold)
        return score(style, env) + arrive_penalty(timing, arrive, frame_count, fps)
    best_x, best_f, evals = nelder_mead(objective, _x(start_timing, bounds), MAX_EVALS)
    return _timing(best_x, base, bounds), best_f, evals


def _probe(path, shot_id, version, rig, frame_count, fps):
    directory = safe_path(path / 'shots' / shot_id / 'versions', version)
    with tempfile.TemporaryDirectory(prefix='camera-fit-') as tmp:
        job, out = Path(tmp) / 'probe_job.json', Path(tmp) / 'probe.json'
        write_json(job, {'rig': rig, 'frame_count': frame_count, 'fps': fps})
        run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', directory / 'scene.blend',
                     '--python-exit-code', '1', '--python', REPO / 'studio/blender_ops/camera_fit_probe.py', '--', job, out],
                    Path(tmp) / 'probe.log', timeout=900)
        return read_json(out)


SEARCHED = 'burst_settle'   # the family fit() searches


def camera_fit(project, shot_id, style_name=None, apply=False, family=None):
    from .project import load_project, load_shot, project_dir
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    move = shot['camera'].get('move')
    if not move:
        raise StudioError('INPUT_INVALID', 'camera fit needs a shot whose camera is a move (camera.move)')
    if not shot.get('scene_version'):
        raise StudioError('INPUT_INVALID', 'Build the shot first: the fit probes the built scene')
    name = style_name or move.get('style') or shot['camera'].get('motion_style')
    if not name:
        raise StudioError('INPUT_INVALID', 'No motion style: pass --style or set camera.move.style')
    style = load(name)
    version = shot['scene_version']
    report = read_json(safe_path(path / 'shots' / shot_id / 'versions', f'{version}/camera_move_report.json'))
    fps = load_project(path)['output']['fps']
    count = shot['duration_frames']
    profile = _probe(path, shot_id, version, report['rig'], count, fps)
    k = _calibration()
    current = report['rig'].get('timing') or {'profile': 'linear'}
    before = shot_envelope(predict(profile, current, count, k), fps)
    arrive = [{'u': report['mark_progress'][a['cue']], 'not_before_s': a['not_before_s']} for a in move.get('arrive', [])
              if a['cue'] in report.get('mark_progress', {})]
    if family not in (None, SEARCHED, 'any'):
        raise StudioError('INPUT_INVALID', f'--profile {family}: only {SEARCHED} has shape parameters to search (or any)')
    declared = (move.get('timing') or {}).get('profile')
    family = family or declared or SEARCHED
    if family in (SEARCHED, 'any'):
        fitted, objective, evals = fit(profile, style, count, current, fps, k, arrive)
    else:   # the declared rhythm stays: score it against the style, search nothing
        fitted, evals = dict(current), 0
        objective = score(style, before) + arrive_penalty(fitted, arrive, count, fps)
    after = shot_envelope(predict(profile, fitted, count, k), fps)
    level = after['mean_mad'] / style['features']['mean_mad']['median']
    hints = []
    if family not in (SEARCHED, 'any'):
        hints.append(f'DECLARED_PROFILE_KEPT: the shot declares a {family} rhythm; it is scored, not searched '
                     f'(--profile {SEARCHED} for the style\'s burst-and-settle candidate)')
    if level < 0.6:
        hints.append(f'LEVEL_LOW: predicted motion {level:.2f}x the style median - lengthen the move or bring the path nearer to geometry')
    elif level > 1.6:
        hints.append(f'LEVEL_HIGH: predicted motion {level:.2f}x the style median - shorten the move or pull the path away from geometry')
    keep = {key: v for key, v in fitted.items() if key not in ('distance_m', 'dwell')}  # path length and the resolved dwell come from the move (move.dwell stays as written)
    result = {'shot_id': shot_id, 'scene_version': version, 'style': name, 'created_at': now(), 'evals': evals, 'family': family,
              'objective': round(objective, 4), 'timing': keep,
              'arrive': [{**a, 'early_penalty': round(arrive_penalty(fitted, [a], count, fps), 3)} for a in arrive], 'level_ratio': round(level, 3), 'hints': hints,
              'before': {f: before[f] for f in SHAPE + ('mean_mad',)}, 'after': {f: after[f] for f in SHAPE + ('mean_mad',)},
              'judge_before': {f: v['ok'] for f, v in judge(style, before).items()},
              'judge_after': {f: v['ok'] for f, v in judge(style, after).items()},
              'limits': ['moving scene objects and the whip head are not in the probe profile', 'level is +/-50 % (proxy calibration)']}
    candidates = path / 'shots' / shot_id / 'candidates'
    number = len(list(candidates.glob('camera_fit_*.json'))) + 1 if candidates.is_dir() else 1
    out = candidates / f'camera_fit_{number:03d}.json'
    write_json(out, result)
    result['candidate'] = str(out)
    if apply:
        from .blender import revise_shot
        camera = {**shot['camera'], 'move': {**move, 'timing': keep}}
        change = Path(tempfile.mkdtemp(prefix='camera-fit-')) / 'change.json'
        write_json(change, {'base_revision': shot['revision'], 'scope': 'camera', 'change': {'camera': camera}})
        result['applied'] = revise_shot(path, shot_id, change)
    return result


def register_commands(subparsers):
    parser = subparsers.add_parser('camera', help='Camera moves: fit timing to a motion style')
    commands = parser.add_subparsers(dest='camera_command', required=True)
    p = commands.add_parser('fit')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--style'); p.add_argument('--apply', action='store_true')
    p.add_argument('--profile', choices=(SEARCHED, 'any'), help='search this family even when the shot declares another timing profile')
    p.set_defaults(handler=lambda a: camera_fit(a.project, a.shot, a.style, a.apply, a.profile))
