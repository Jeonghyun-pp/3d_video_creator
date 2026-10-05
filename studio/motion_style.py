"""Motion styles: what a family of reference reels does with the camera, as numbers.

A style is learned from one or more reference videos: cuts are found, and each shot's screen-motion
envelope (frame-difference energy, the same measure as qa motion) is reduced to a few features - how much
of the shot's motion happens in its first 30 %, when it peaks, how fast it decays, how long it holds still
at the end, its level and its head 'whip'. The distribution of those features (median, quartiles) and of the
cut lengths is the style. References are only measured: no frames or audio are stored, so a style can be
kept while the reference stays private. `check` judges a new edit's shots against a style.
"""
from __future__ import annotations

from pathlib import Path
import re
import statistics
import subprocess

from .common import REPO, StudioError, check_id, file_hash, now, read_json, write_json
from .qa_motion import STILL_MAD, WIDTH, pair_differences

STYLES = REPO / 'library' / 'motion_styles'
FEATURES = ('burst_share', 'peak_t', 'decay_half_s', 'hold_frac', 'mean_mad', 'p95_mad', 'head_whip')
MERGE_S = 0.3        # cuts closer than this are one transition (flash frames)
MIN_SHOT_S = 1.0     # shorter fragments are transitions, not shots
MARGIN_IQR = 0.5     # check accepts [p25 - m*IQR, p75 + m*IQR]
FLOOR = {'burst_share': 0.05, 'peak_t': 0.08, 'decay_half_s': 0.25, 'hold_frac': 0.05, 'mean_mad': 0.6, 'p95_mad': 1.5, 'head_whip': 0.3}


def cut_points(video, threshold=0.25, fps=30):
    """Frame indices where a new shot starts (scene-change detection, flash clusters merged)."""
    result = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(video), '-vf', f"select='gt(scene,{threshold})',showinfo", '-an', '-f', 'null', '-'],
                            capture_output=True, text=True)
    times = [float(t) for t in re.findall(r'pts_time:([0-9.]+)', result.stderr)]
    cuts = []
    for t in times:
        if not cuts or t - cuts[-1][-1] > MERGE_S:
            cuts.append([t])
        else:
            cuts[-1].append(t)
    return [round(cluster[0] * fps) for cluster in cuts]


def _windows(values, size):
    out, i = [], 0.0
    while int(i) < len(values):
        chunk = values[int(i):max(int(i) + 1, int(i + size))]
        out.append(sum(chunk) / len(chunk))
        i += size
    return out


DUPLICATE_MAD = 0.05   # an exact repeat frame (24->30 fps pulldown) differs by encoder noise only


def drop_pulldown(diffs):
    """Remove repeat-frame pairs when a clip carries a 24->30 fps pulldown cadence, so a converted reference
    is not read as 'holding still' every fifth frame. A repeat is an isolated near-zero pair between two
    moving pairs; a real hold is a run of near-zero pairs and is kept."""
    moving = 10 * DUPLICATE_MAD
    repeat = [0 < i < len(diffs) - 1 and diffs[i] < DUPLICATE_MAD and diffs[i - 1] > moving and diffs[i + 1] > moving
              for i in range(len(diffs))]
    ratio = sum(repeat) / len(diffs) if diffs else 0
    if 0.12 <= ratio <= 0.30:
        return [d for d, r in zip(diffs, repeat) if not r], round(ratio, 3)
    return list(diffs), 0.0


DWELL_LEVEL = 0.3   # a window under this share of the shot's peak rate is 'stopped'


def shot_envelope(diffs, fps=30):
    """Features of one shot's frame-difference series (pairs inside the shot only), on 0.25 s windows."""
    diffs, pulldown = drop_pulldown(diffs)
    if len(diffs) < 4:
        raise StudioError('QA_FAILED', 'shot too short to measure')
    n = len(diffs)
    total = sum(diffs) or 1e-9
    head = diffs[:max(1, round(0.3 * n))]
    win = _windows(diffs, fps / 4)
    peak = max(range(len(win)), key=win.__getitem__)
    half = next((i for i, w in enumerate(win[peak:]) if w <= win[peak] / 2), len(win) - peak)
    level = max(win) or 1e-9
    tail = 0
    for w in reversed(win):
        if w >= 0.2 * level:  # held = the last windows run at under a fifth of the shot's peak rate
            break
        tail += 1
    # dwell: the longest run inside the shot (not its head, not its held tail) under DWELL_LEVEL of the peak - the
    # camera stopping in front of something to let it read, then moving on
    run = best = best_end = 0
    for i, w in enumerate(win[:len(win) - tail]):
        run = run + 1 if (w < DWELL_LEVEL * level and i > 0) else 0
        if run > best:
            best, best_end = run, i
    best = best if best_end < len(win) - tail - 1 else 0     # a run that reaches the tail is the hold, not a dwell
    ordered = sorted(diffs)
    p95 = ordered[round(0.95 * (n - 1))] or 1e-9
    return {'duration_s': round((n + 1) / fps, 3), 'burst_share': round(sum(head) / total, 4), 'peak_t': round(peak / len(win), 4),
            'decay_half_s': round(half / 4, 3), 'hold_frac': round(tail / len(win), 4), 'mean_mad': round(statistics.mean(diffs), 4),
            'p95_mad': round(p95, 4), 'head_whip': round(win[0] / (statistics.mean(win) or 1e-9), 4), 'pulldown_ratio': pulldown,
            'windows_025': [round(w, 3) for w in win],
            'dwell_s': round(best / 4, 3), 'dwell_t': round((best_end - best / 2 + 0.5) / len(win), 4) if best else None}


def video_shots(video, fps=30, cuts=None):
    """[(start_frame, frame_count, envelope)] for every shot of a video."""
    diffs = pair_differences(video, WIDTH)
    frames = len(diffs) + 1
    starts = sorted({0, *[c for c in (cuts if cuts is not None else cut_points(video, fps=fps)) if 0 < c < frames]})
    bounds = list(zip(starts, starts[1:] + [frames]))
    shots = []
    for a, b in bounds:
        if (b - a) / fps < MIN_SHOT_S:
            continue
        series = diffs[a:b - 1]  # the pair across the cut is excluded
        if statistics.mean(series) < 0.02:  # black / frozen tail card
            continue
        shots.append((a, b - a, shot_envelope(series, fps)))
    return shots


def _quartiles(values):
    ordered = sorted(values)
    q = statistics.quantiles(ordered, n=4, method='inclusive') if len(ordered) > 1 else [ordered[0]] * 3
    return {'p25': round(q[0], 4), 'median': round(q[1], 4), 'p75': round(q[2], 4), 'n': len(ordered)}


def learn(name, videos, fps=30, ranges=None):
    """ranges: [[start_s, end_s] | None] per video - measure that window as one shot (e.g. one reel shot to copy the
    rhythm of) instead of detecting cuts."""
    name = check_id(name)
    rows, sources = [], []
    for i, video in enumerate(videos):
        path = Path(video)
        span = (ranges or [None] * len(videos))[i]
        if span:
            diffs = pair_differences(path, WIDTH)[round(span[0] * fps):round(span[1] * fps) - 1]
            shots = [(round(span[0] * fps), len(diffs) + 1, shot_envelope(diffs, fps))]
        else:
            shots = video_shots(path, fps)
        rows += [env for _, _, env in shots]
        sources.append({'sha256': file_hash(path), 'shots': len(shots), 'seconds': round(sum(n for _, n, _ in shots) / fps, 2),
                        **({'range_s': span} if span else {})})
    if not rows:
        raise StudioError('INPUT_INVALID', 'no measurable shots in the reference videos')
    style = {'schema_version': 1, 'name': name, 'created_at': now(), 'n_references': len(videos), 'sources': sources,
             'overfit_risk': len(videos) < 2, 'features': {f: _quartiles([r[f] for r in rows]) for f in FEATURES},
             'shot_seconds': _quartiles([r['duration_s'] for r in rows]),
             'defaults': _defaults(rows)}
    dwelled = [r for r in rows if r.get('dwell_s')]
    if dwelled:   # kept beside the judged features, so styles learned before dwell existed still judge the same
        style['dwell'] = {'dwell_s': _quartiles([r['dwell_s'] for r in dwelled]), 'dwell_t': _quartiles([r['dwell_t'] for r in dwelled]),
                          'shots_with_dwell': len(dwelled), 'shots': len(rows)}
        style['defaults']['dwell_s'] = style['dwell']['dwell_s']['median']
    write_json(STYLES / f'{name}.json', style)
    return {'style': name, 'shots': len(rows), 'path': str(STYLES / f'{name}.json'), 'features': style['features'],
            'defaults': style['defaults'], 'warnings': ['STYLE_SINGLE_REFERENCE: learned from one video; add more to avoid copying it'] if style['overfit_risk'] else []}


def _defaults(rows):
    """Camera defaults a move uses when the shot does not override them (median shot of the style)."""
    share = statistics.median(r['burst_share'] for r in rows)
    hold = statistics.median(r['hold_frac'] for r in rows)
    return {'timing': {'profile': 'burst_settle', 'burst_frac': 0.3, 'burst_share': round(min(0.85, max(0.35, share)), 3),
                       'hold_frac': round(min(0.4, max(0.0, hold)), 3), 'drift': 0.01},
            'motion_blur': {'shutter': 0.5, 'target_blur_px': 14.0}}


def load(name):
    path = STYLES / f'{check_id(name)}.json'
    if not path.is_file():
        raise StudioError('INPUT_INVALID', f'No motion style {name} (learn it with motion style learn)')
    return read_json(path)


def judge(style, envelope):
    """{feature: {value, low, high, ok}} for one shot against a style."""
    out = {}
    for feature in FEATURES:
        q = style['features'][feature]
        spread = max(q['p75'] - q['p25'], FLOOR[feature])
        low, high = q['p25'] - MARGIN_IQR * spread, q['p75'] + MARGIN_IQR * spread
        value = envelope[feature]
        out[feature] = {'value': value, 'low': round(low, 4), 'high': round(high, 4), 'ok': low <= value <= high}
    return out


def check(name, video, shots=None, fps=30):
    """Judge every shot of `video` against the style. shots: [{shot_id, start_frame, frame_count}] (an edit
    snapshot's shots) or None to detect cuts."""
    style = load(name)
    if shots:
        diffs = pair_differences(video, WIDTH)
        measured = [(s['shot_id'], shot_envelope(diffs[s['start_frame']:s['start_frame'] + s['frame_count'] - 1], fps)) for s in shots]
    else:
        measured = [(f'shot{i + 1:02d}', env) for i, (_, _, env) in enumerate(video_shots(video, fps))]
    rows = []
    for shot_id, env in measured:
        verdict = judge(style, env)
        misses = [f for f, v in verdict.items() if not v['ok']]
        rows.append({'shot_id': shot_id, 'ok': not misses, 'misses': misses, 'envelope': {k: env[k] for k in FEATURES}, 'verdict': verdict})
    passed = sum(r['ok'] for r in rows)
    return {'style': name, 'shots': len(rows), 'in_style': passed, 'in_style_ratio': round(passed / len(rows), 3) if rows else None, 'rows': rows}


def _snapshot_shots(path):
    return [{'shot_id': s['shot_id'], 'start_frame': s['start_frame'], 'frame_count': s['frame_count']} for s in read_json(path)['shots']]


def register_commands(subparsers):
    parser = subparsers.add_parser('motion', help='Motion styles learned from reference reels (numbers only)')
    commands = parser.add_subparsers(dest='motion_command', required=True)
    style = commands.add_parser('style').add_subparsers(dest='style_command', required=True)
    p = style.add_parser('learn'); p.add_argument('--name', required=True); p.add_argument('--video', action='append', required=True)
    p.add_argument('--range', action='append', help='start,end seconds for the matching --video (one shot, no cut detection)')
    p.set_defaults(handler=lambda a: learn(a.name, a.video, ranges=[[float(x) for x in r.split(',')] for r in a.range] if a.range else None))
    p = style.add_parser('show'); p.add_argument('--name', required=True)
    p.set_defaults(handler=lambda a: load(a.name))
    p = style.add_parser('check'); p.add_argument('--name', required=True); p.add_argument('--video', required=True)
    p.add_argument('--snapshot', help='edit.snapshot.json of the candidate: judge its shots instead of detected cuts')
    p.set_defaults(handler=lambda a: check(a.name, a.video, _snapshot_shots(a.snapshot) if a.snapshot else None))
