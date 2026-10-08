"""Screen-motion (speed-feel) metric: mean absolute difference of adjacent grey frames.

A proxy, not a perception score: it rises with camera motion, near-field parallax and
subject motion, and it is comparable between clips measured at the same width.
Calibration (448 px wide, 2026-10-04): Otis explainer shots 0.06-0.64 per shot
(static/slow keys; its 2.4 s fast push-in 6.45; whole cut 1.45 incl. cuts),
jet canyon rig-style chase 2.86, its Blender reference blockout 3.62.
"""
from __future__ import annotations

import json
from pathlib import Path
import statistics
import subprocess

from PIL import Image, ImageChops

from .common import StudioError

WIDTH = 448
STILL_MAD = 0.3
# Warning thresholds per shot energy; warnings never change technical_pass.
ENERGY_RULES = {'high': {'min_mean': 2.5, 'max_still_ratio': 0.10}, 'calm': {'max_p95': 6.0}, 'medium': {}}


def _frames(video, width=WIDTH, start=0, count=None):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                            '-of', 'json', str(video)], capture_output=True, text=True)
    if probe.returncode:
        raise StudioError('QA_FAILED', probe.stderr[-1000:])
    stream = json.loads(probe.stdout)['streams'][0]
    height = max(2, round(stream['height'] * width / stream['width'] / 2) * 2)
    trim = f'trim=start_frame={start}' + (f':end_frame={start + count}' if count else '') + ',setpts=PTS-STARTPTS,'
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(video), '-vf', f'{trim}scale={width}:{height}:flags=bicubic,format=gray',
                          '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], capture_output=True)
    if raw.returncode:
        raise StudioError('QA_FAILED', raw.stderr.decode(errors='replace')[-1000:])
    size = width * height
    return [Image.frombytes('L', (width, height), raw.stdout[i:i + size]) for i in range(0, len(raw.stdout) - size + 1, size)]


def pair_differences(video, width=WIDTH, start=0, count=None):
    frames = _frames(video, width, start, count)
    diffs = []
    for a, b in zip(frames, frames[1:]):
        histogram = ImageChops.difference(b, a).histogram()
        diffs.append(sum(level * n for level, n in enumerate(histogram)) / (a.width * a.height))
    return diffs


def summarize(diffs, fps=30):
    if not diffs:
        raise StudioError('QA_FAILED', 'Need at least two frames to measure motion')
    ordered = sorted(diffs)
    window = int(round(fps))
    return {'pairs': len(diffs), 'mad_mean': round(statistics.mean(diffs), 4), 'mad_p50': round(statistics.median(diffs), 4),
            'mad_p95': round(ordered[round(.95 * (len(ordered) - 1))], 4),
            'still_ratio': round(sum(d < STILL_MAD for d in diffs) / len(diffs), 4),
            'windows_1s': [round(statistics.mean(diffs[i:i + window]), 4) for i in range(0, len(diffs), window)]}


def measure_motion(video, width=WIDTH, start=0, count=None, fps=30):
    return {'width': width, 'start_frame': start, **summarize(pair_differences(video, width, start, count), fps)}


def compare_motion(candidate, reference, reference_start=0, count=None, fps=30):
    ours = measure_motion(candidate, count=count, fps=fps)
    ref = measure_motion(reference, start=reference_start, count=count or ours['pairs'] + 1, fps=fps)
    return {'candidate': ours, 'reference': ref, 'ratio': round(ours['mad_mean'] / ref['mad_mean'], 4) if ref['mad_mean'] else None,
            'window_ratios': [round(a / b, 4) if b else None for a, b in zip(ours['windows_1s'], ref['windows_1s'])]}


CUT_WINDOW = 6          # frames each side of a cut whose screen motion is compared
CUT_JUMP_RATIO = 4.0    # one side moving this many times more than the other reads as a jolt


def cut_motion(diffs, shots, window=CUT_WINDOW):
    """Screen motion just before and just after every plain cut (`shots` in timeline order, with `transition` kind
    when declared). Why (archcut3, 2026-10-08): a dive at full speed cut to a near-still hall; nothing measured the
    jolt. A cut is flagged when one side moves CUT_JUMP_RATIO times the other and the faster side is not still."""
    rows = []
    for previous, shot in zip(shots, shots[1:]):
        cut = shot['start_frame']
        before = diffs[max(previous['start_frame'], cut - 1 - window):cut - 1]   # pairs inside the outgoing shot
        after = diffs[cut:cut + window]                                         # pairs inside the incoming shot
        if not before or not after:
            continue
        a, b = sum(before) / len(before), sum(after) / len(after)
        ratio = max(a, b) / max(min(a, b), STILL_MAD)
        row = {'shot_id': shot['shot_id'], 'frame': cut, 'motion_before': round(a, 4), 'motion_after': round(b, 4),
               'ratio': round(ratio, 2), 'transition': shot.get('transition') or 'cut', 'warnings': []}
        if row['transition'] == 'cut' and ratio > CUT_JUMP_RATIO and max(a, b) > 2 * STILL_MAD:
            slow, fast = (shot['shot_id'], previous['shot_id']) if a > b else (previous['shot_id'], shot['shot_id'])
            row['warnings'].append(f"CUT_MOTION_JUMP: {previous['shot_id']} -> {shot['shot_id']} at frame {cut}: screen motion "
                                   f"{a:.2f} -> {b:.2f} ({ratio:.1f}x); ease {fast} toward the cut or carry motion into {slow} "
                                   f"(one continuous camera is one shot)")
        rows.append(row)
    return rows


def shot_motion(video, shots, fps=30):
    """Per-shot metrics for an edited candidate; the pair across each cut is excluded."""
    diffs = pair_differences(video)
    rows = []
    for shot in shots:
        start, count = shot['start_frame'], shot['frame_count']
        energy = shot.get('energy') or 'calm'
        metrics = summarize(diffs[start:start + count - 1], fps)
        rule = ENERGY_RULES[energy]
        warnings = []
        if 'min_mean' in rule and metrics['mad_mean'] < rule['min_mean']:
            warnings.append(f"motion_low: {shot['shot_id']} energy high but mean screen motion {metrics['mad_mean']} < {rule['min_mean']}")
        if 'max_still_ratio' in rule and metrics['still_ratio'] > rule['max_still_ratio']:
            warnings.append(f"motion_stalls: {shot['shot_id']} energy high but {metrics['still_ratio']:.0%} of frames are near-still")
        if 'max_p95' in rule and metrics['mad_p95'] > rule['max_p95']:
            warnings.append(f"motion_busy: {shot['shot_id']} energy calm but p95 screen motion {metrics['mad_p95']} > {rule['max_p95']}; check label readability")
        rows.append({'shot_id': shot['shot_id'], 'energy': energy, **metrics, 'warnings': warnings})
    return rows


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--reference-start', type=int, default=0)
    parser.add_argument('--frames', type=int)
    args = parser.parse_args(argv)
    result = compare_motion(args.video, args.reference, args.reference_start, args.frames) if args.reference \
        else measure_motion(args.video, count=args.frames)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
