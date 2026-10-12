"""Screen grammar, the pure part (no numpy, no bpy): turning per-frame measurements into the vocabulary of
.agents/skills/reel-production/references/screen_grammar.md.

- canvas: which rows/columns of a reel are the picture (they change broadly over time) and which are static bands
  (title, subtitle bands of a window layout);
- camera: a shot's camera move from the per-frame global motion the Blender probe fits (grammar_probe.py);
- composition: a labelled box -> the same numbers shot.screen.targets uses (screen_core.METRICS, one definition);
- flow: relations between consecutive shots.

Thresholds are initial values, named and explained here, and calibrated against renders with a known camera
(docs/BUILD_REPORT.md, screen grammar P2); a value is changed here, never at a call site.
"""
from __future__ import annotations

import math
import statistics

from .blender_ops.screen_core import METRICS

# --- canvas -----------------------------------------------------------------------------------------------------
ACTIVE_PIXEL_STD = 6.0     # a pixel whose luma std over the sampled frames exceeds this (0-255) changes with the picture
ACTIVE_LINE_SHARE = 0.5    # a row/column is picture when at least this share of its pixels change: a subtitle or title
                           # band changes only where its letters are, a picture changes almost everywhere
MIN_BAND_SHARE = 0.04      # a static run thinner than this share of the frame is a border, not a band
BAND_EDGE_STEP = 12.0       # a band meets the picture at a straight edge: mean |luma step| across that row (0-255) ...
BAND_EDGE_FRAMES = 0.7      # ... at least this large in this share of the sampled frames. A flat sky or black void above
                            # a horizon is static too, but its edge moves with the camera (calibration renders, 2026-10-10:
                            # a black sky was read as a title band and the full frame as a window)


EDGE_INSET = 2              # compare the band row with a row this far into the picture: the row at the edge is a blend
                            # after downscaling (a real bottom band scored 0.42 at the edge row, 1.0 two rows in)


def _sharp_edge(frames, w, a, b):
    """True when row a (band side) and the picture EDGE_INSET rows past b differ sharply across the width in most frames."""
    if not frames:
        return True
    h = len(frames[0]) // w
    b = min(h - 1, max(0, b + (EDGE_INSET if b > a else -EDGE_INSET)))
    hits = sum(1 for f in frames if statistics.mean(abs(f[a * w + x] - f[b * w + x]) for x in range(w)) >= BAND_EDGE_STEP)
    return hits >= BAND_EDGE_FRAMES * len(frames)


def canvas(active, w, h, frames=None):
    """active: row-major flags (w*h), True where a pixel changes over the sampled frames; frames: the sampled frames
    (row-major luma bytes) for the edge test. Rows are judged across the full width; columns only inside the picture
    rows (a window's static bands would otherwise dilute every column). A static run is a band only where it meets the
    picture at a fixed straight edge. -> {layout, content_rect, bands}."""
    row_activity = [sum(active[y * w:(y + 1) * w]) / w for y in range(h)]
    rows = [a >= ACTIVE_LINE_SHARE for a in row_activity]
    if not any(rows):
        return {'layout': 'full_bleed', 'content_rect': [0.0, 0.0, 1.0, 1.0], 'bands': []}
    r0 = rows.index(True); r1 = h - rows[::-1].index(True)
    if r0 and not _sharp_edge(frames, w, r0 - 1, r0):
        r0 = 0
    if r1 < h and not _sharp_edge(frames, w, r1, r1 - 1):
        r1 = h
    cols = [sum(active[y * w + x] for y in range(r0, r1)) / (r1 - r0) >= ACTIVE_LINE_SHARE for x in range(w)]
    if not any(cols):
        return {'layout': 'full_bleed', 'content_rect': [0.0, 0.0, 1.0, 1.0], 'bands': []}
    c0 = cols.index(True); c1 = w - cols[::-1].index(True)
    rect = [round(c0 / w, 4), round(r0 / h, 4), round(c1 / w, 4), round(r1 / h, 4)]
    bands = []
    for a, b in ((0, r0), (r1, h)):
        if (b - a) / h >= MIN_BAND_SHARE:
            bands.append({'rect': [0.0, round(a / h, 4), 1.0, round(b / h, 4)],
                          'changing_share': round(statistics.mean(row_activity[a:b]), 4)})
    window = max(rect[0], rect[1], 1 - rect[2], 1 - rect[3]) >= MIN_BAND_SHARE
    return {'layout': 'window' if window else 'full_bleed', 'content_rect': rect if window else [0.0, 0.0, 1.0, 1.0], 'bands': bands}


def to_content(box, rect):
    """A box in canvas shares -> content-rect shares (clipped)."""
    x0, y0, x1, y1 = rect
    w, h = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    out = [(box[0] - x0) / w, (box[1] - y0) / h, (box[2] - x0) / w, (box[3] - y0) / h]
    return [round(min(1.0, max(0.0, v)), 4) for v in out]


# --- composition ------------------------------------------------------------------------------------------------
def composition(box):
    """A box [x0, y0, x1, y1] in content-rect shares -> the screen_core metrics shot.screen.targets names. area_share is
    the box area (a box overstates a non-rectangular subject; our renders are measured the same way, from boxes)."""
    if not box:
        return None
    x0, y0, x1, y1 = box
    shape = {'bbox': [x0, y0, x1, y1], 'centroid': [(x0 + x1) / 2, (y0 + y1) / 2], 'share': max(0.0, (x1 - x0) * (y1 - y0))}
    return {name: round(fn(shape), 4) for name, (fn, _) in METRICS.items() if fn is not None and name != 'ui_overlap'}


# --- camera -----------------------------------------------------------------------------------------------------
# T2 says which way and how much the picture moves; it cannot tell a push from a zoom, a pan from a truck, or the camera
# from a thing moving on its own (a study clip: a spinning dust cloud on black measured as a 26 deg roll). T3 sees
# the picture and names the move; the two are combined by compatibility (camera_move) and a disagreement is kept.
STILL_SPEED = 0.02          # typical (median) screen translation below this share of the content width per second
STILL_SCALE = 0.02          # and typical log-scale change below this per second: the camera holds
STILL_ROLL_DEG = 1.5        # degrees per second
COMPOUND_SHARE = 0.6        # a second component at least this share of the first: compound
SCALE_WEIGHT = 1.0          # log-scale counts like translation of the same size when choosing the dominant component
AXIS_MOVES = {'still': ('static', 'handheld'), 'scale_in': ('push', 'zoom'), 'scale_out': ('pull', 'zoom'),
              'x': ('pan', 'truck', 'orbit'), 'y': ('tilt', 'pedestal'), 'roll': ('roll',), 'compound': ('compound', 'orbit')}


def _typical(values):
    """Median step: one fast frame (a flash, a mis-match) cannot make a shot 'move'."""
    return statistics.median(values) if values else 0.0


def camera(frames, fps):
    """frames: per-frame-pair dicts from grammar_probe {tx, ty, ds, rot, residual, ok} (content-width shares, log-scale,
    radians per frame). -> {axis, rates per second, net over the shot as typical rate x duration, parallax per second}."""
    good = [f for f in frames if f.get('ok')]
    fresh = [f for f in frames if not f.get('duplicate')]   # pairs that show a new picture
    if len(good) < 3:
        if frames and len(fresh) <= 0.1 * len(frames):   # the picture never changes: a locked-off camera on a still scene
            return {'axis': 'still', 'rate_per_s': {'x': 0.0, 'y': 0.0, 'scale': 0.0, 'roll_deg': 0.0},
                    'net': {'x': 0.0, 'y': 0.0, 'scale': 0.0, 'roll_deg': 0.0}, 'parallax_per_s': 0.0, 'measured_share': 0.0,
                    'new_pictures_per_s': round(len(fresh) / (len(frames) / fps), 2)}
        return {'axis': 'unknown', 'reason': 'too few measurable frames'}
    seconds = max(len(frames), 1) / fps
    per_s = len(fresh) / seconds                           # new pictures per second (24 for 24 fps material at 60)
    rate = {'x': _typical([f['tx'] for f in good]) * per_s, 'y': _typical([f['ty'] for f in good]) * per_s,
            'scale': _typical([f['ds'] for f in good]) * per_s, 'roll_deg': math.degrees(_typical([f['rot'] for f in good])) * per_s}
    comps = {'x': abs(rate['x']), 'y': abs(rate['y']), 'scale': abs(rate['scale']) * SCALE_WEIGHT}
    moving = {k: v for k, v in comps.items() if v >= (STILL_SCALE if k == 'scale' else STILL_SPEED)}
    if not moving:
        axis = 'roll' if abs(rate['roll_deg']) >= STILL_ROLL_DEG else 'still'
    else:
        ranked = sorted(moving, key=moving.get, reverse=True)
        axis = {'scale': 'scale_in' if rate['scale'] > 0 else 'scale_out', 'x': 'x', 'y': 'y'}[ranked[0]]
        if len(ranked) > 1 and moving[ranked[1]] >= COMPOUND_SHARE * moving[ranked[0]]:
            axis = 'compound'
    return {'axis': axis, 'rate_per_s': {k: round(v, 4) for k, v in rate.items()},
            'net': {k: round(v * seconds, 4) for k, v in rate.items()},
            'parallax_per_s': round(_typical([f['residual'] for f in good]) * per_s, 5),
            'measured_share': round(len(good) / max(len(fresh), 1), 3), 'new_pictures_per_s': round(per_s, 2)}


def camera_move(t2, guesses):
    """The vocabulary's camera.move. Naming a move is a visual judgement: the labeller's name when its runs agree
    (a study clip: one continuous orbit around a walker - side, back, front - that the global fit,
    dominated by the background, read as a push; the labeller called it compound). T2 keeps the numbers (rates,
    envelope) and its axis; 'agree' says whether the name fits the axis (an item's reliability counts it)."""
    axis = t2.get('axis', 'unknown')
    allowed = AXIS_MOVES.get(axis, ())
    votes = [g for g in guesses if g]
    if votes and len(set(votes)) == 1:
        return {'move': votes[0], 'axis': axis, 'agree': votes[0] in allowed}
    fitting = [g for g in votes if g in allowed]
    if fitting:
        return {'move': max(set(fitting), key=fitting.count), 'axis': axis, 'agree': False}
    return {'move': allowed[0] if allowed else axis, 'axis': axis, 'agree': False if votes else None}


# --- feel -------------------------------------------------------------------------------------------------------
# Two cameras at the same screen speed can feel unlike (2026-10-12, the user: "숫자는 같아도 다른 느낌을 줄수 있잖아").
# What changes the feel, and how it shows in the per-pair measurements:
# - where the camera heads: the fixed point of the pair's similarity motion (a push grows the picture about it);
# - things streaking past the lens: patches the global fit cannot explain by a lot (perceived speed follows the
#   edges passing per second more than the global flow - Larish & Flach 1990);
# - the camera against the subject: their velocities in the world (scene measurement only);
# - height and lens: the same speed low and wide reads faster (flow scales with speed / distance);
# - rhythm: rush and settle, slow build, or even (the envelope);
# - swirl: the picture turning about the view.
# grammar_probe (pixels) and grammar_synth (scene) both give 'streak'; the rest of 'feel' comes from the scene only.
HEADING_MIN_DS = 0.02       # log-scale per second: below this the picture does not grow about any point
HEADING_MIN_PAIRS = 3
BURST_PEAK_T = 0.3          # rush and settle: the envelope peaks in the first 30 % ...
BURST_HOLD = 0.3            # ... and creeps or holds over at least 30 % (the reference's push-and-settle shots: median
                            # peak_t 0.07, hold_frac 0.43, the motion style learnt from it)
BUILD_PEAK_T = 0.6          # a slow build: the envelope peaks after 60 % of the shot
MOVING_MPS = 0.05           # camera or subject slower than this on average: no direction to compare
PIXEL_STREAK_FLOOR = 0.05   # the pixel probe calls this share "streaking" with nothing near the lens (mismatches at block
                            # edges; tests/studio/grammar_synth_smoke.py 2026-10-12: a field trucked past 5.2 %, the scene 0 %;
                            # with posts near the lens 11.8 % against the scene's 9.5 %): taken off a pixel measurement's streak


def heading(pair, aspect):
    """Where on the picture a pair's motion is centred (content shares, x right, y down), or None: the probe's focus
    (where the patches' motion lines radiate from - right whatever the depths), else, for measurements made before it,
    the similarity fit's fixed point p0 = -t / (k + i w) of d = t + (k + i w) p (biased when near and far patches mix:
    2026-10-12 smoke, a push at an off-centre block read 0.24 frame heights high). aspect: content height / width."""
    if pair.get('ok') and pair.get('focus'):
        return tuple(pair['focus'])
    k, w = math.expm1(pair.get('ds', 0.0)), pair.get('rot', 0.0)
    if not pair.get('ok') or math.hypot(k, w) < 1e-9:
        return None
    p0 = -complex(pair['tx'], pair['ty']) / complex(k, w)
    return 0.5 + p0.real, 0.5 + p0.imag / aspect


def rhythm(envelope, axis):
    if axis == 'still':
        return 'hold'
    if not envelope or envelope.get('peak_t') is None:
        return None
    if envelope['peak_t'] < BURST_PEAK_T and (envelope.get('hold_frac') or 0.0) >= BURST_HOLD:
        return 'burst_settle'
    return 'build' if envelope['peak_t'] > BUILD_PEAK_T else 'even'


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def feel(frames, fps, camera, envelope=None, aspect=16 / 9, box=None, pixels=False):
    """A shot's feel from its per-pair measurements (see above). box: [x0, y0, x1, y1] content shares of what the shot
    explains (a scene pair's own feel.part_box comes first); heading_on is the share of pushing pairs whose centre falls in
    it. pixels: the pairs come from grammar_probe (its streak floor is taken off). Values a source cannot give are None."""
    good = [f for f in frames if f.get('ok')]
    pushing = [f for f in good if abs(f.get('ds', 0.0)) * fps >= HEADING_MIN_DS]
    centred = [(c, (f.get('feel') or {}).get('part_box') or box) for f, c in ((f, heading(f, aspect)) for f in pushing) if c is not None]
    out = {'heading': None, 'heading_on': None}
    if len(centred) >= HEADING_MIN_PAIRS:
        out['heading'] = [round(statistics.median(c[0] for c, _ in centred), 3), round(statistics.median(c[1] for c, _ in centred), 3)]
        boxed = [(c, b) for c, b in centred if b]
        if boxed:   # the explained part's box in that pair (scene measurement) or the one given
            out['heading_on'] = round(sum(b[0] <= c[0] <= b[2] and b[1] <= c[1] <= b[3] for c, b in boxed) / len(boxed), 3)
    # how far from the picture's centre it heads (shares): the pixel-only stand-in for heading_on - a push about a point
    # near the centre goes toward what the shot frames, one about a far point slides past it
    out['heading_off'] = round(math.hypot(out['heading'][0] - 0.5, out['heading'][1] - 0.5), 3) if out['heading'] else None
    scene = [f.get('feel') or {} for f in good]
    out['streak'] = _mean([f.get('streak') for f in good])   # share of the picture streaking past, per pair
    if pixels and out['streak'] is not None:
        out['streak'] = max(0.0, out['streak'] - PIXEL_STREAK_FLOOR)
    out['lost'] = _mean([f.get('lost') for f in good])       # pixels only: textured patches no match was found for
    out['edges_per_s'] = None if _mean([s.get('edges') for s in scene]) is None else _mean([s.get('edges') for s in scene]) * fps
    out['near'] = _mean([s.get('near') for s in scene])
    flows = [(s['subject_flow'], s['world_flow']) for s in scene if s.get('subject_flow') and s.get('world_flow')]
    if flows:   # the subject's own screen motion against the world's: near 0 the camera rides along with it
        a = [sum(f[0][i] for f in flows) for i in (0, 1)]
        b = [sum(f[1][i] for f in flows) for i in (0, 1)]
        out['pinned'] = round(math.hypot(*a) / math.hypot(*b), 3) if math.hypot(*b) > 1e-9 else None
    else:
        out['pinned'] = None
    # the camera against the subject, in the world: -1 they meet head on (the camera rises as the rocket comes down),
    # +1 they go the same way. On screen both read as "the world moves down", so this needs the scene's velocities.
    moving = [(s['camera_vel'], s['subject_vel']) for s in scene if s.get('camera_vel') and s.get('subject_vel')]
    cam_v = [sum(m[0][i] for m in moving) / len(moving) for i in range(3)] if moving else None
    sub_v = [sum(m[1][i] for m in moving) / len(moving) for i in range(3)] if moving else None
    if moving and math.dist(cam_v, (0, 0, 0)) > MOVING_MPS and math.dist(sub_v, (0, 0, 0)) > MOVING_MPS:
        out['counter'] = round(sum(x * y for x, y in zip(cam_v, sub_v)) / (math.dist(cam_v, (0, 0, 0)) * math.dist(sub_v, (0, 0, 0))), 3)
    else:
        out['counter'] = None
    rates = [s['speed_mps'] / s['height_m'] for s in scene if s.get('speed_mps') is not None and (s.get('height_m') or 0) > 0.05]
    out['eye_rate'] = round(statistics.median(rates), 4) if rates else None
    heights = [s['height_m'] for s in scene if s.get('height_m') is not None]
    out['height_m'] = round(statistics.median(heights), 2) if heights else None
    pitches = [s['pitch_deg'] for s in scene if s.get('pitch_deg') is not None]
    out['pitch_deg'] = round(statistics.median(pitches), 1) if pitches else None
    lenses = [s['lens_mm'] for s in scene if s.get('lens_mm')]
    out['lens_mm'] = round(statistics.median(lenses), 1) if lenses else None
    out['rhythm'] = rhythm(envelope, (camera or {}).get('axis'))
    out['swirl'] = abs(((camera or {}).get('rate_per_s') or {}).get('roll_deg', 0.0))
    for key in ('streak', 'lost', 'edges_per_s', 'near'):
        out[key] = None if out[key] is None else round(out[key], 4)
    return out


def motion_series(frames):
    """Per-frame camera speed (content-width shares per frame, scale counted like translation) for envelope features."""
    return [math.hypot(f['tx'], f['ty']) + abs(f['ds']) * SCALE_WEIGHT if f.get('ok') else 0.0 for f in frames]


# --- boundaries -------------------------------------------------------------------------------------------------
# A new scene starts at a hard cut or inside a transition that takes ~0.5-1.5 s (AI reels morph one scene into the
# next). Measured against a hand-labelled ground truth (3 clips from 3 sample groups, first 60 s, 81 boundaries, 2026-10-09):
#   scene-change score >= 0.25 alone            80 % found, 6 false
#   score >= 0.25 + transition humps             89 % found, 11 false   <- used
#   + the labeller judging candidates (2 frames)  81 % / (6 consecutive frames) 74 %: it called cuts to a closer
#     framing of the same subject "camera moved", and rejected morphs - removed
# Transitions show as a hump of picture change that the camera fit cannot explain (high mad AND high residual over
# several new pictures); a fast rigid move has high mad and low residual. Known limits: ~1 boundary in 9 missed (a wipe
# into a split screen, a white dissolve, a morph-push), ~1 in 8 extra (graphics popping in, a textured fast push).
# Zoom-through chains (2026-10-10: a 31 s clip of ~15 scenes measured as 2 shots) look like one long rigid
# zoom to every picture signal; the labeller counts distinct_scenes per shot and such shots are left out of the
# timing, camera and composition patterns instead of being split by guess.
CUT_SCORE = 0.25
CLUSTER_S = 0.3             # scores this close are one transition (flash frames, a dissolve's frames)
HUMP_MAD = 2.5              # mad at least this many times the local 30th percentile (and +4 levels)
HUMP_RESIDUAL = 4.0         # residual at least this many times its local 30th percentile ...
HUMP_RESIDUAL_FLOOR = 0.008 # ... and above this share of the content width
HUMP_MIN_PICTURES = 3
HUMP_WINDOW_S = 2.0
MERGE_S = 0.4               # a hump this close to a cut is that cut


def cut_clusters(scored):
    """scored: [(time_s, scene_score)] above CUT_SCORE -> [{t, score}] one per cluster at its peak."""
    out = []
    for t, score in sorted(scored):
        if out and t - out[-1]['last'] <= CLUSTER_S:
            out[-1]['last'] = t
            if score > out[-1]['score']:
                out[-1].update(t=t, score=score)
        else:
            out.append({'t': t, 'score': score, 'last': t})
    return [{'t': round(c['t'], 3), 'score': round(c['score'], 4)} for c in out]


def transition_humps(pairs, fps):
    """[{start, t (peak), end}] in seconds: bursts of change the camera does not explain."""
    fresh = [(i, p) for i, p in enumerate(pairs) if not p.get('duplicate')]
    if not fresh:
        return []
    mad = [p.get('mad', 0.0) for _, p in fresh]
    res = [p.get('residual', 0.0) if p.get('ok') else 0.05 for _, p in fresh]
    per_s = len(fresh) / (len(pairs) / fps)
    w = max(5, int(HUMP_WINDOW_S * per_s))
    flags = []
    for k in range(len(fresh)):
        lo, hi = max(0, k - w), min(len(fresh), k + w + 1)
        bm = sorted(mad[lo:hi])[int(0.3 * (hi - lo))]
        br = sorted(res[lo:hi])[int(0.3 * (hi - lo))]
        flags.append(mad[k] >= max(HUMP_MAD * bm, bm + 4) and res[k] >= max(HUMP_RESIDUAL * br, HUMP_RESIDUAL_FLOOR))
    out, k = [], 0
    while k < len(flags):
        if flags[k]:
            j = k
            while j < len(flags) and (flags[j] or (j + 1 < len(flags) and flags[j + 1])):
                j += 1
            if j - k >= HUMP_MIN_PICTURES:
                peak = max(range(k, j), key=lambda q: mad[q] * res[q])
                out.append({'start': round((fresh[k][0] + 1) / fps, 3), 't': round((fresh[peak][0] + 1) / fps, 3),
                            'end': round((fresh[min(j, len(fresh) - 1)][0] + 1) / fps, 3)})
            k = j
        k += 1
    return out


def boundaries(clusters, humps):
    """Shot starts: every cut cluster, plus every transition hump not already at a cut. [{t, kind, score?}]."""
    out = [{'t': c['t'], 'kind': 'cut', 'score': c['score']} for c in clusters]
    for h in humps:
        if not any(abs(h['t'] - b['t']) <= MERGE_S for b in out):
            out.append({'t': h['t'], 'kind': 'transition', 'span_s': round(h['end'] - h['start'], 3)})
    return sorted(out, key=lambda b: b['t'])


# --- flow -------------------------------------------------------------------------------------------------------
SCALE_STEP_SAME = 0.15      # subject height_share within +/-15 % of the previous shot's: same scale


def scale_step(prev_share, share):
    if not prev_share or not share:
        return None
    ratio = share / prev_share
    return {'step': 'same' if abs(math.log(ratio)) < math.log(1 + SCALE_STEP_SAME) else ('closer' if ratio > 1 else 'wider'),
            'ratio': round(ratio, 3)}
