"""Screen grammar measurement: a video -> one row per shot in the vocabulary of
.agents/skills/reel-production/references/screen_grammar.md, the same way for a reference reel and for our render.

  T1  canvas (picture rect and static bands), cuts, shot lengths, visual-change events  (ffmpeg + PIL, every frame)
  T2  camera global motion per frame pair                                               (blender_ops/grammar_probe.py)
  T3  roles, boxes, reveal, graphics, subtitles, free note                              (Codex labeller, keyframe strips)

The labeller sees, per shot, one strip of four keyframes (A-D at 8 / 35 / 65 / 92 % of the shot) of the whole canvas,
and answers in a fixed JSON schema (labeller_schema.json beside the strips) plus one free sentence (the open layer:
what works and what no item describes). It runs `runs` times independently so agreement can be measured; nothing
is averaged here. References stay local: strips are written under the output folder and only attached to the local
Codex CLI (the same route Astra's images take), never to a generation model.
"""
from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from .common import REPO, StudioError, blender_binary, file_hash, now, read_json, run_command, write_json
from . import grammar_core

KEY_SHARES = (0.08, 0.35, 0.65, 0.92)
PANELS = 'ABCD'
STRIP_HEIGHT = 300          # px per keyframe: image input is most of the labeller's cost (2026-10-09: ~3k tokens a shot at 400)
SHOTS_PER_CALL = 6           # strips per labeller call: one image per shot keeps calls few and context small
LABEL_MAX = 12               # shots labelled per video (T1/T2 measure every shot): evenly spaced, first and last kept
MIN_SHOT_S = 0.2
CANVAS_SAMPLES = 40
CANVAS_WIDTH = 96
CHANGE_JUMP = 3.0            # a pair difference this many times the shot's median, while the camera is still: a
CHANGE_FLOOR = 2.0           # visible change (a subtitle swap, a graphic popping in); floor in mean |diff| (0-255)
LABELLER_MODEL = 'gpt-6-astra'   # Astra's model: the labeller that reads references also reads our renders
LABELLER_EFFORT = 'medium'

from .patterns import ROLES   # one role vocabulary (shot lists, shots, patterns)
MOVES = ['static', 'push', 'pull', 'pan', 'tilt', 'orbit', 'truck', 'pedestal', 'roll', 'handheld', 'zoom', 'compound']
REVEALS = ['section_cut', 'explode', 'transparency', 'isolate', 'zoom_scale', 'real_to_diagram', 'time_lapse', 'xray', 'none']
GRAPHICS = ['arrow', 'outline', 'highlight', 'dimension', 'path_trace', 'pin', 'label', 'header_word', 'counter']
ANCHORS = ['person', 'hand', 'vehicle', 'door', 'furniture', 'tool', 'none']
CHANGES = ['camera_only', 'object_motion', 'state_change', 'none']


def _probe(video):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate,nb_frames',
                          '-show_entries', 'format=duration', '-of', 'json', str(video)], capture_output=True, text=True, check=True)
    d = json.loads(out.stdout)
    s = d['streams'][0]
    num, den = (int(x) for x in s['r_frame_rate'].split('/'))
    return {'width': s['width'], 'height': s['height'], 'fps': num / den, 'duration': float(d['format']['duration'])}


def _gray_frames(video, width, times):
    frames = []
    for t in times:
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', str(video), '-frames:v', '1', '-vf', f'scale={width}:-2',
                              '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, check=True).stdout
        if raw:
            from io import BytesIO
            frames.append(Image.open(BytesIO(raw)).convert('L'))
    return frames


def canvas_of(video, media):
    times = [media['duration'] * (i + 0.5) / CANVAS_SAMPLES for i in range(CANVAS_SAMPLES)]
    frames = _gray_frames(video, CANVAS_WIDTH, times)
    w, h = frames[0].size
    data = [f.tobytes() for f in frames if f.size == (w, h)]
    active = [statistics.pstdev([d[i] for d in data]) >= grammar_core.ACTIVE_PIXEL_STD for i in range(w * h)]
    return grammar_core.canvas(active, w, h, data)


def scene_scores(video, rect):
    """[(time_s, scene_score)] for every frame scoring above grammar_core.CUT_SCORE, judged on the picture rect only."""
    import re
    full = rect == [0.0, 0.0, 1.0, 1.0]
    crop = '' if full else f"crop=iw*{rect[2] - rect[0]:.4f}:ih*{rect[3] - rect[1]:.4f}:iw*{rect[0]:.4f}:ih*{rect[1]:.4f},"
    err = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(video), '-vf', f"{crop}select='gt(scene,{grammar_core.CUT_SCORE})',metadata=print",
                          '-an', '-f', 'null', '-'], capture_output=True, text=True).stderr
    times = [float(x) for x in re.findall(r'pts_time:([0-9.]+)', err)]
    scores = [float(x) for x in re.findall(r'lavfi.scene_score=([0-9.]+)', err)]
    return list(zip(times, scores))


def boundaries_of(video, media, canvas, pairs, out):
    """Shot starts from cuts on the picture rect and transition humps (grammar_core.boundaries: why, and the measured limits)."""
    clusters = grammar_core.cut_clusters(scene_scores(video, canvas['content_rect']))
    found = grammar_core.boundaries(clusters, grammar_core.transition_humps(pairs, media['fps']))
    write_json(out / 'boundaries.json', {'boundaries': found})
    return found


def shots_of(media, bounds):
    fps = media['fps']
    total = int(round(media['duration'] * fps))
    starts = [(0, 'start')] + [(int(round(b['t'] * fps)), b['kind']) for b in bounds if 0 < b['t'] * fps < total]
    starts = sorted(dict(starts).items())
    ends = [a for a, _ in starts[1:]] + [total]
    return [{'index': i, 'start_frame': a, 'end_frame': b, 'start_s': round(a / fps, 3), 'len_s': round((b - a) / fps, 3), 'transition_in': kind}
            for i, ((a, kind), b) in enumerate(zip(starts, ends)) if (b - a) / fps >= MIN_SHOT_S]


def motion_of(video, rect, out):
    target = out / 'motion.json'
    if not target.is_file():
        run_command([blender_binary(), '--background', '--factory-startup', '--python-exit-code', '1', '--python',
                     REPO / 'studio/blender_ops/grammar_probe.py', '--', video, target, *rect], timeout=3600)
    return read_json(target)['pairs']


def visual_changes(video, shots, pairs):
    """Per shot: visible changes per second while the camera holds (text swaps, graphics appearing), from T1 pair
    differences of the whole canvas."""
    from .qa_motion import WIDTH, pair_differences
    diffs = pair_differences(video, WIDTH)
    out = []
    for s in shots:
        a, b = s['start_frame'], min(s['end_frame'] - 1, len(diffs))
        series = diffs[a:b]
        if len(series) < 3:
            out.append(0.0); continue
        base = max(statistics.median(series), 0.05)
        still = [i for i in range(a, b) if i < len(pairs) and pairs[i].get('ok') and
                 abs(pairs[i]['tx']) + abs(pairs[i]['ty']) + abs(pairs[i]['ds']) < 0.002]
        events = sum(1 for i in still if diffs[i] >= max(CHANGE_JUMP * base, CHANGE_FLOOR))
        out.append(round(events / max(s['len_s'], 1e-6), 3))
    return out


def _grab(video, t, height, fps):
    """One frame at t; the container's duration can run past the last video frame (audio longer than picture,
    2026-10-10: 3 of 84 reels), so step back a frame at a time, up to a second."""
    from io import BytesIO
    for back in range(int(fps) + 1):
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, t - back / fps):.3f}', '-i', str(video), '-frames:v', '1',
                              '-vf', f'scale=-2:{height}', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, check=True).stdout
        if raw:
            return Image.open(BytesIO(raw)).convert('RGB')
    raise StudioError('INPUT_INVALID', f'{video}: no frame near {t:.2f} s')


def strips(video, shots, media, folder):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for s in shots:
        target = folder / f"shot{s['index']:03d}.jpg"
        paths.append(target)
        if target.is_file():
            continue
        panels = [_grab(video, s['start_s'] + share * s['len_s'], STRIP_HEIGHT, media['fps']) for share in KEY_SHARES]
        pad, top = 8, 28
        width = sum(p.width for p in panels) + pad * (len(panels) + 1)
        strip = Image.new('RGB', (width, STRIP_HEIGHT + top + pad), '#202020')
        draw = ImageDraw.Draw(strip)
        draw.text((pad, 6), f"shot {s['index']}  ({s['len_s']} s)", fill='white')
        x = pad
        for letter, p in zip(PANELS, panels):
            strip.paste(p, (x, top))
            draw.rectangle((x, top, x + 22, top + 20), fill='black')
            draw.text((x + 6, top + 4), letter, fill='yellow')
            x += p.width + pad
        strip.save(target, quality=88)
    return paths


def label_sample(count, limit=LABEL_MAX):
    """Indices of the shots the labeller reads: all when few, else the first two (the hook happens once per video, so
    it is always read) and the last, the rest evenly spaced between."""
    if limit is None or count <= limit:
        return list(range(count))
    middle = limit - 3
    picked = {0, 1, count - 1} | {round(2 + (i + 1) * (count - 3) / (middle + 1)) for i in range(middle)}
    return sorted(picked)


def labeller_schema():
    box = {'anyOf': [{'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4}, {'type': 'null'}]}
    panel = {'type': 'object', 'additionalProperties': False, 'required': ['panel', 'subject_box', 'anchor_box', 'void_edges', 'void_share'],
             'properties': {'panel': {'enum': list(PANELS)}, 'subject_box': box, 'anchor_box': box,
                            'void_edges': {'type': 'array', 'items': {'enum': ['top', 'bottom', 'left', 'right']}},
                            'void_share': {'type': 'number'}}}
    graphic = {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'first_panel', 'anchored'],
               'properties': {'kind': {'enum': GRAPHICS}, 'first_panel': {'enum': list(PANELS)}, 'anchored': {'type': 'boolean'}}}
    subtitle = {'type': 'object', 'additionalProperties': False, 'required': ['present', 'center_y', 'lines', 'chars'],
                'properties': {'present': {'type': 'boolean'}, 'center_y': {'type': ['number', 'null']},
                               'lines': {'type': 'integer'}, 'chars': {'type': 'integer'}}}
    shot = {'type': 'object', 'additionalProperties': False,
            'required': ['shot', 'distinct_scenes', 'role', 'view_height', 'camera_guess', 'change_kind', 'reveal_method', 'reveal_panel',
                         'scale_anchor', 'depth_layers', 'subject_count', 'graphics', 'subtitle', 'panels', 'note'],
            'properties': {'shot': {'type': 'integer'}, 'distinct_scenes': {'type': 'integer'}, 'role': {'enum': list(ROLES)}, 'view_height': {'enum': ['down', 'level', 'up']},
                           'camera_guess': {'enum': MOVES}, 'change_kind': {'enum': CHANGES}, 'reveal_method': {'enum': REVEALS},
                           'reveal_panel': {'enum': [*PANELS, 'none']}, 'scale_anchor': {'enum': ANCHORS},
                           'depth_layers': {'type': 'integer'}, 'subject_count': {'type': 'integer'},
                           'graphics': {'type': 'array', 'items': graphic}, 'subtitle': subtitle,
                           'panels': {'type': 'array', 'items': panel}, 'note': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['shots'],
            'properties': {'shots': {'type': 'array', 'items': shot}}}


PROMPT = """You label shots of a video for a screen-grammar dataset. Answer only from the attached images; run no commands.
Each image is one shot: four keyframes A-D left to right (8, 35, 65, 92 % through the shot), the shot number at the top.
Describe what the shot DOES on screen, never what it is about. Definitions:
- distinct_scenes: how many different scenes A-D show (1 when one continuous shot; 2+ when the picture becomes another
  place or subject - a cut, dissolve, morph or zoom-through the shot boundaries missed). Describe the shot as it is anyway.
- role: hook (opening, a reason to keep watching) | establish (where/what, often a real or familiar view) | explain (how
  something works) | reveal (opens what was hidden) | compare | consequence (what happens because) | close.
- view_height: down (camera looks down more than ~20 deg) | level | up.
- camera_guess: the camera's move across A-D: static | push (moves toward) | pull | pan | tilt | orbit (around the subject) |
  truck (sideways) | pedestal (up/down) | roll | handheld | zoom | compound.
- change_kind: what changes across A-D besides the camera: object_motion | state_change (fills, opens, breaks, grows) |
  camera_only | none.
- reveal_method: section_cut | explode | transparency | isolate (surroundings dimmed/greyed, subject kept) | zoom_scale
  (into a smaller scale) | real_to_diagram (a real view turns into a diagram or cutaway) | time_lapse | xray | none;
  reveal_panel: the first panel where it shows, or none.
- scale_anchor: a thing of known size in frame: person | hand | vehicle | door | furniture | tool | none.
- depth_layers: how many of foreground / middle / background hold something (0-3). subject_count: separate things
  competing for attention.
- graphics: drawn overlays (not the picture): arrow | outline | highlight | dimension (measure line) | path_trace (a drawn
  route) | pin (map marker) | label (text tied to a point) | header_word (a big word naming the step) | counter (a number);
  first_panel where each appears; anchored = it follows something in the picture.
- subtitle: the spoken-text caption: present, center_y (0 top - 1 bottom of the whole frame), lines, characters visible.
- panels: for each of A-D, subject_box = [x0, y0, x1, y1] of the thing the shot is about, in shares of THAT panel
  (0,0 top-left, 1,1 bottom-right; null if none); anchor_box likewise for the scale anchor; void_edges = edges touched by
  empty sky / flat backdrop / black; void_share = share of the panel that is such empty area.
- note: one or two sentences - why the shot works or not, and anything you see that none of the fields above captures.
Return every shot in the images, in order."""


def label(paths, out, run):
    """One labeller run over every strip; cached per batch (out/labels/run<run>_<batch>.json)."""
    codex = shutil.which('codex') or str(Path.home() / '.local/bin/codex')
    folder = out / 'labels'
    folder.mkdir(parents=True, exist_ok=True)
    schema = folder / 'labeller_schema.json'
    write_json(schema, labeller_schema())
    shots, usage = [], []
    for b in range(0, len(paths), SHOTS_PER_CALL):
        batch = paths[b:b + SHOTS_PER_CALL]
        target = folder / f'run{run}_{b // SHOTS_PER_CALL:03d}.json'
        if not target.is_file():
            with tempfile.TemporaryDirectory() as tmp:
                last = Path(tmp) / 'last.json'
                cmd = [codex, 'exec', '-m', LABELLER_MODEL, '-c', f'model_reasoning_effort="{LABELLER_EFFORT}"', '-s', 'read-only',
                       '--skip-git-repo-check', '-C', tmp, '--json', '--output-schema', str(schema), '-o', str(last)]
                for p in batch:
                    cmd += ['-i', str(p)]
                # the prompt goes on stdin: -i takes every following argument as an image
                result = subprocess.run(cmd, capture_output=True, text=True, input=PROMPT, timeout=900)
                if result.returncode != 0 or not last.is_file():
                    raise StudioError('LABELLER_FAILED', f'codex exec failed on {batch[0].name}: {result.stderr[-400:]}', retryable=True)
                answer = json.loads(last.read_text())
                tokens = [json.loads(line) for line in result.stdout.splitlines() if '"token_count"' in line or 'usage' in line]
                write_json(target, {'strips': [p.name for p in batch], 'answer': answer, 'usage_events': tokens[-1:] if tokens else []})
        data = read_json(target)
        shots += data['answer']['shots']
        usage += data.get('usage_events', [])
    return shots, usage


def _panel_box(box, rect):
    """A box in one panel's shares (the whole canvas) -> content-rect shares."""
    return grammar_core.to_content(box, rect) if box else None


def assemble(video, media, canvas, shots, pairs, changes, labels_by_run):
    from .motion_style import shot_envelope
    rows = []
    for s, change in zip(shots, changes):
        seg = pairs[s['start_frame']:max(s['start_frame'], s['end_frame'] - 1)]
        cam = grammar_core.camera(seg, media['fps'])
        series = grammar_core.motion_series(seg)
        try:
            envelope = shot_envelope([v * 1000 for v in series], media['fps']) if len(series) >= 4 else None
        except StudioError:
            envelope = None
        row = {'shot': s['index'], 'start_s': s['start_s'], 'len_s': s['len_s'], 'transition_in': s['transition_in'],
               'len_share': round(s['len_s'] / media['duration'], 4), 'position': round(s['start_s'] / media['duration'], 4),
               'camera': cam, 'camera_envelope': {k: envelope[k] for k in ('burst_share', 'peak_t', 'hold_frac', 'dwell_t')} if envelope else None,
               'visual_change_rate': change, 'labels': []}
        for run, labels in labels_by_run.items():
            found = next((l for l in labels if l['shot'] == s['index']), None)
            if found is None:
                continue
            panels = {p['panel']: p for p in found['panels']}
            comp = {k: grammar_core.composition(_panel_box(panels.get(k, {}).get('subject_box'), canvas['content_rect'])) for k in PANELS}
            anchor = {k: grammar_core.composition(_panel_box(panels.get(k, {}).get('anchor_box'), canvas['content_rect'])) for k in PANELS}
            row['labels'].append({'run': run, **{k: v for k, v in found.items() if k != 'panels'}, 'composition': comp,
                                  'anchor': {k: (v or {}).get('height_share') for k, v in anchor.items()},
                                  'void': {k: {'edges': panels.get(k, {}).get('void_edges'), 'share': panels.get(k, {}).get('void_share')}
                                           for k in PANELS}})
        row['camera_move'] = grammar_core.camera_move(cam, [l.get('camera_guess') for l in row['labels']])
        rows.append(row)
    return rows


def measure(video, out, runs=2, labelled=True, label_max=LABEL_MAX, cuts=None):
    """cuts: shot starts in seconds when they are known (our own edit) - then nothing is detected; else boundaries_of."""
    video, out = Path(video).resolve(), Path(out).resolve()
    if not video.is_file():
        raise StudioError('INPUT_INVALID', f'no video {video}')
    out.mkdir(parents=True, exist_ok=True)
    media = _probe(video)
    canvas = canvas_of(video, media)
    pairs = motion_of(video, canvas['content_rect'], out)
    bounds = [{'t': float(t), 'kind': 'cut'} for t in cuts] if cuts is not None else boundaries_of(video, media, canvas, pairs, out)
    shots = shots_of(media, bounds)
    changes = visual_changes(video, shots, pairs)
    chosen = label_sample(len(shots), label_max)
    paths = strips(video, [shots[i] for i in chosen], media, out / 'strips')
    labels_by_run, usage = {}, []
    if labelled:
        for run in range(1, runs + 1):
            labels_by_run[run], used = label(paths, out, run)
            usage += used
    rows = assemble(video, media, canvas, shots, pairs, changes, labels_by_run)
    result = {'schema_version': 1, 'video_sha256': file_hash(video), 'media': media, 'canvas': canvas, 'shots': rows,
              'labeller': {'model': LABELLER_MODEL, 'effort': LABELLER_EFFORT, 'runs': runs if labelled else 0, 'usage_events': usage},
              'measured_at': now()}
    write_json(out / 'grammar.json', result)
    return {'video': str(video), 'shots': len(rows), 'canvas': canvas, 'artifacts': [str(out / 'grammar.json')]}


def register_commands(subparsers):
    parser = subparsers.add_parser('grammar', help='Screen grammar: measure how a video uses the frame (references/screen_grammar.md)')
    commands = parser.add_subparsers(dest='grammar_command', required=True)
    p = commands.add_parser('measure', help='One video -> grammar.json (T1 cuts/canvas, T2 camera, T3 Codex labels)')
    p.add_argument('--video', required=True); p.add_argument('--out', required=True)
    p.add_argument('--runs', type=int, default=2); p.add_argument('--no-label', action='store_true')
    p.add_argument('--label-max', type=int, default=LABEL_MAX, help='shots the labeller reads per video (0: all)')
    p.add_argument('--cuts', help='known shot starts in seconds, comma separated (our own edit): skip boundary detection')
    p.set_defaults(handler=lambda a: measure(a.video, a.out, a.runs, not a.no_label, a.label_max or None,
                                             [float(t) for t in a.cuts.split(',') if t.strip()] if a.cuts else None))
