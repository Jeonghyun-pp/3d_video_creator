"""Storyboard ping-pong: each shot is agreed with the user as pictures - camera, objects, framing - before looks and paid
generation, and the approved pictures become a loose contract the final scene must keep.

  storyboard propose --shot S --frames 0,0.5,1 [--focus "0.5=st.box"]   build if needed, render a sheet (Workbench)
  storyboard revise  --shot S --user-words "…" --ops ops.json            their words as edits on any value -> rebuild -> new sheet
  storyboard approve --shot S --sheet rNN --user-words "…"               bind the measured frames as the contract
  storyboard variants --shot S --variants takes.json --frames 0,0.5,1     2-4 different takes built side by side, one sheet
  storyboard pick --shot S --sheet vNN --variant B --user-words "…"      the user's take becomes the shot (then approve as usual)

Variants are where the agent is free: a take may replace any of the shot's content (camera, scene, actions, titles,
graphics) - a different move, a different staging - and every take passes the same build gates. Judgement stays
strict: the user picks in their own words, and the picked take still goes through sheet -> approve -> contract.

Edits reach every value of the shot's content: set / add / remove by JSON pointer (studio/shot_edit.py - the same grammar
as the workbench and fill revise), refused only when nothing reads the value; the word-ops (closer, higher, from the
side, lens, horizon, look at, object move/scale/add/remove, title text) are shorthands over the same values, and a move
without the asked knob points to set on its params. The contract is checked with tolerances wide enough to allow
polish and narrow enough to catch a different shot (provisional, uncalibrated: it catches drift; the user judges looks).
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile

from . import shot_edit
from .blender_ops import camera_moves_core as moves_core   # pure math, no Blender
from .common import REPO, StudioError, blender_binary, now, read_json, run_command, stable_hash, write_json
from .project import load_shot, project_dir, shot_path

MOVE_KNOBS = {   # what "closer", "higher", "from the side" mean for each move type (params of camera_moves_core)
    'push_in': {'distance': 'to_m', 'height': 'height_m', 'angle': 'azimuth_deg'},
    'section_push': {'distance': 'back_m', 'height': 'above_m'},
    'orbit_reveal': {'distance': 'radius_m', 'height': 'height_m', 'angle': 'start_deg'},
    'crane': {'distance': 'dist_m', 'height': 'to_h', 'angle': 'azimuth_deg'},
    'dive_through': {'height': 'above_m'},
    'turntable': {'distance': 'distance_scale', 'height': 'elevation_deg', 'angle': 'start_deg'},
    'slide': {'distance': 'distance_scale', 'height': 'elevation_deg', 'angle': 'azimuth_deg'},
    'macro_push': {'distance': 'distance_scale', 'height': 'elevation_deg', 'angle': 'azimuth_deg'},
}
OPS = ('set', 'add', 'remove', 'camera.closer', 'camera.height', 'camera.angle', 'camera.lens', 'camera.horizon', 'camera.look_at',
       'object.move', 'object.scale', 'object.add', 'object.remove', 'title.set', 'note')
TOLERANCE = {'view_deg': 15.0, 'eye_share': 0.25, 'eye_min_m': 0.5, 'lens_ratio': 0.30, 'focus_centre': 0.15, 'focus_area': (0.5, 2.0)}
SHOT_CONTENT = shot_edit.SHOT_CONTENT


def _envelope_path(path, shot_id):
    return project_dir(path) / 'decisions' / 'storyboard' / f'{shot_id}.json'


def envelope(path, shot_id):
    file = _envelope_path(path, shot_id)
    return read_json(file) if file.is_file() else None


def content_sha256(shot):
    return stable_hash({k: shot.get(k) for k in SHOT_CONTENT})


# --- typed edits ----------------------------------------------------------------------------------------------------

def _scene_entry(scene, ident):
    for key in ('instances', 'primitives'):
        for row in scene.get(key, []):
            if row['id'] == ident:
                return key, row
    raise StudioError('INPUT_INVALID', f'storyboard edit: no scene object {ident} (shot.scene instances or primitives)')


KNOB_PATH = {'camera.closer': ('distance', 'factor'), 'camera.height': ('height', 'delta'), 'camera.angle': ('angle', 'delta')}


def apply_ops(shot, ops):
    """(changed shot fields, plain-language list of what changed) for typed storyboard edits. `set` reaches any value of
    the shot's content; the other ops are the words people use, expressed through the same values."""
    content = {k: deepcopy(shot.get(k)) for k in SHOT_CONTENT}
    content['scene'] = content['scene'] if content['scene'] is not None else {}
    content['titles'] = content['titles'] or []
    camera, scene, titles = content['camera'], content['scene'], content['titles']
    said = []
    for op in ops:
        kind = op.get('op')
        if kind not in OPS:
            raise StudioError('INPUT_INVALID', f'storyboard edit {kind!r} is not one of {OPS}')
        move = camera.get('move')
        if kind in shot_edit.OPS:
            said.append(shot_edit.edit_shot(content, op))
        elif kind in KNOB_PATH:
            if not move:
                raise StudioError('INPUT_INVALID', f'{kind} needs a camera.move (this shot has fixed keys); use set on /camera/keys')
            meaning, how = KNOB_PATH[kind]
            knob = MOVE_KNOBS.get(move['type'], {}).get(meaning)
            if knob is None:
                raise StudioError('INPUT_INVALID', f"{move['type']} has no {meaning} knob ({sorted(MOVE_KNOBS.get(move['type'], {}))}); "
                                  f"use set on /camera/move/params/<one of {sorted(moves_core.PARAMS[move['type']])}>")
            amount = op['factor'] if how == 'factor' else op.get('delta_m', op.get('delta_deg', 0))
            said.append(shot_edit.edit_shot(content, {'op': 'set', 'path': f'/camera/move/params/{knob}', how: amount}))
        elif kind == 'camera.lens':
            target = move if move else camera
            before = target.get('lens_mm')
            target['lens_mm'] = op['mm']
            said.append(f'lens {before} → {op["mm"]} mm')
        elif kind == 'camera.horizon':
            if not move:
                raise StudioError('INPUT_INVALID', 'camera.horizon holds the horizon of a camera.move')
            move.setdefault('framing', {})['horizon_v'] = op['v']
            said.append(f"horizon held at {op['v']} of the frame height")
        elif kind == 'camera.look_at':
            if not move:
                raise StudioError('INPUT_INVALID', 'camera.look_at aims a camera.move')
            move['look_target'] = op['id']
            said.append(f"camera aims at {op['id']}")
        elif kind == 'object.move':
            _, row = _scene_entry(scene, op['id'])
            row['at'] = [round(a + d, 4) for a, d in zip(row['at'], op['delta_m'])]
            said.append(f"{op['id']} moved by {op['delta_m']} m")
        elif kind == 'object.scale':
            key, row = _scene_entry(scene, op['id'])
            if key != 'primitives':
                raise StudioError('INPUT_INVALID', f"{op['id']} is a built subject: change its spec through edits or declare a deviation")
            row['size'] = [round(v * op['factor'], 4) for v in row['size']]
            said.append(f"{op['id']} scaled ×{op['factor']}")
        elif kind == 'object.add':
            key = 'instances' if {'exemplar', 'subject'} & set(op['entry']) else 'primitives'
            scene.setdefault(key, []).append(op['entry'])
            said.append(f"added {op['entry']['id']}")
        elif kind == 'object.remove':
            key, row = _scene_entry(scene, op['id'])
            scene[key] = [r for r in scene[key] if r['id'] != op['id']]
            said.append(f"removed {op['id']}")
        elif kind == 'title.set':
            title = next((t for t in titles if t['title_id'] == op['title_id']), None)
            if title is None:
                raise StudioError('INPUT_INVALID', f"no title {op['title_id']}")
            title['text'] = op['text']
            said.append(f"title {op['title_id']}: {op['text']}")
        else:
            said.append(op.get('text', 'noted'))
    change = {'camera': camera}
    for key in SHOT_CONTENT:
        original = shot.get(key) if key != 'titles' else (shot.get('titles') or [])
        if key != 'camera' and content[key] != (original if original is not None else ({} if key == 'scene' else original)):
            change[key] = content[key]
    if shot.get('scene') is not None:
        change['scene'] = content['scene']
    from .project import unread_values
    never_read = unread_values({**shot, **change})   # e.g. left over after set /camera/move/type, or a key an orbit ignores
    if never_read:
        raise StudioError('INPUT_INVALID', 'nothing reads these values: ' + '; '.join(never_read[:6]))
    return change, said                     # the rebuild validates the whole shot (schema, timing)


# --- the contract ---------------------------------------------------------------------------------------------------

def _angle(a, b):
    dot = sum(x * y for x, y in zip(a, b)) / (math.dist(a, [0, 0, 0]) * math.dist(b, [0, 0, 0]) or 1)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def compare(contract, measured):
    """[problems] between the approved frames and a measured state (same frames). Pure, no Blender."""
    problems = []
    rows = {r['frame']: r for r in measured['frames']}
    for want in contract['frames']:
        got = rows.get(want['frame'])
        if got is None:
            problems.append(f"frame {want['frame']}: not measured")
            continue
        view = _angle(want['forward'], got['forward'])
        if view > TOLERANCE['view_deg']:
            problems.append(f"frame {want['frame']}: camera looks {view:.0f}° away from the approved view")
        reach = max(TOLERANCE['eye_min_m'], TOLERANCE['eye_share'] * (want.get('focus_distance_m') or 10.0))   # 10 m without a focus
        moved = math.dist(want['eye'], got['eye'])
        if moved > reach:
            problems.append(f"frame {want['frame']}: camera {moved:.1f} m from the approved position (allowed {reach:.1f} m)")
        if abs(got['lens_mm'] - want['lens_mm']) > TOLERANCE['lens_ratio'] * want['lens_mm']:
            problems.append(f"frame {want['frame']}: lens {got['lens_mm']} mm against {want['lens_mm']} mm approved")
        for ident, box in want.get('focus', {}).items():
            now_box = got.get('focus', {}).get(ident)
            if not isinstance(box, list):
                continue
            if not isinstance(now_box, list):
                problems.append(f"frame {want['frame']}: {ident} is no longer in front of the camera")
                continue
            centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)  # noqa: E731
            area = lambda b: max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))  # noqa: E731
            if math.dist(centre(box), centre(now_box)) > TOLERANCE['focus_centre']:
                problems.append(f"frame {want['frame']}: {ident} moved in the frame")
            ratio = area(now_box) / area(box)
            if not TOLERANCE['focus_area'][0] <= ratio <= TOLERANCE['focus_area'][1]:
                problems.append(f"frame {want['frame']}: {ident} is {ratio:.1f}× the approved size in the frame")
    return problems


# --- sheets ---------------------------------------------------------------------------------------------------------

def _frames(shot, spec):
    count = shot['duration_frames']
    return sorted({min(count - 1, max(0, round(float(t) * (count - 1)))) for t in spec})


def _measure(path, shot_id, version, frames, focus, out_dir, render=True, height=480):
    scene = shot_path(path, shot_id).parent / 'versions' / version / 'scene.blend'
    job = Path(out_dir) / 'storyboard_job.json'
    write_json(job, {'frames': frames, 'focus': {str(k): v for k, v in focus.items()}, 'out_dir': str(out_dir), 'height': height, 'render': render})
    run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(scene), '--python-exit-code', '1',
                 '--python', str(REPO / 'studio/blender_ops/storyboard_render.py'), '--', str(job)], Path(out_dir) / 'storyboard.log', timeout=900)
    return read_json(Path(out_dir) / 'state.json')


def _sheet_image(state, out_dir, captions, previous=None):
    from PIL import Image, ImageDraw
    from .common import font_file
    from PIL import ImageFont
    font = ImageFont.truetype(str(font_file({}, REPO)), 18)
    tiles = [Image.open(Path(out_dir) / f"frame_{r['frame']:06d}.png").convert('RGB') for r in state['frames']]
    top = Image.open(Path(out_dir) / 'top.png').convert('RGB')
    h = tiles[0].height
    draw_top = ImageDraw.Draw(top)
    centre, scale, (rw, rh) = state['top']['center'], state['top']['ortho_scale'], state['top']['resolution']
    to_px = lambda p: (rw / 2 + (p[0] - centre[0]) / scale * rw, rh / 2 - (p[1] - centre[1]) / scale * rw)  # noqa: E731
    path = [to_px(p) for p in state['path']]
    if len(path) > 1:
        draw_top.line(path, fill=(230, 60, 40), width=4)
    for n, row in enumerate(state['frames'], 1):
        x, y = to_px(row['eye'])
        draw_top.ellipse((x - 9, y - 9, x + 9, y + 9), fill=(255, 210, 0))
        draw_top.text((x + 11, y - 11), str(n), fill=(255, 255, 255), font=font)
    top = top.resize((max(1, round(top.width * h / top.height)), h))
    rows = [('after' if previous else None, tiles)]
    if previous:
        rows.insert(0, ('before', [Image.open(p).convert('RGB') for p in previous]))
    width = sum(t.width for t in tiles) + top.width + 12 * (len(tiles) + 1)
    sheet = Image.new('RGB', (width, (h + 40) * len(rows) + 10), (24, 24, 26))
    draw = ImageDraw.Draw(sheet)
    for r, (label, images) in enumerate(rows):
        x, y = 12, 10 + r * (h + 40)
        for n, (image, row) in enumerate(zip(images, state['frames']), 1):
            sheet.paste(image.resize((tiles[0].width, h)), (x, y))
            draw.text((x + 6, y + h + 6), f"{n}. f{row['frame']} {captions.get(row['frame'], '')}"[:60], fill=(235, 235, 235), font=font)
            x += tiles[0].width + 12
        if label:
            draw.text((12, y - 2), label, fill=(255, 210, 0), font=font)
        if r == len(rows) - 1:
            sheet.paste(top, (x, y))
    target = Path(out_dir) / 'sheet.png'
    sheet.save(target)
    return target


def _ensure_current_version(path, shot_id):
    """The version whose content is the shot's: build it from the scene data when the shot changed."""
    from .blender import build_shot
    shot = load_shot(path, shot_id)
    version = shot.get('scene_version')
    if version:
        snapshot = read_json(shot_path(path, shot_id).parent / 'versions' / version / 'shot.snapshot.json')
        if content_sha256(snapshot) == content_sha256(shot):
            return version
    if not shot.get('scene'):
        raise StudioError('INPUT_INVALID', f'{shot_id}: build the shot first (it has no declarative scene to build from)')
    return build_shot(path, shot_id, None)['scene_version']


def _write_sheet(path, shot_id, env, captions, previous=None):
    version = _ensure_current_version(path, shot_id)
    shot = load_shot(path, shot_id)
    frames = env['body']['frames']
    root = project_dir(path) / 'decisions' / 'sheets' / f'storyboard_{shot_id}'
    rev = f"r{len(list(root.glob('r*'))) + 1:02d}"
    out = root / rev; out.mkdir(parents=True, exist_ok=True)
    state = _measure(path, shot_id, version, frames, env['body'].get('focus', {}), out)
    image = _sheet_image(state, out, captions, previous)
    lines = [f'# Storyboard {shot_id} — sheet {rev}', '', '![sheet](sheet.png)', '', f"Version {version}; {len(frames)} frames; Workbench blocking, not the look.", '']
    lines += [f"{n}. frame {r['frame']}: camera at {r['eye']}, lens {r['lens_mm']} mm {captions.get(r['frame'], '')}" for n, r in enumerate(state['frames'], 1)]
    if env.get('history'):
        lines += ['', '**What you asked → what changed:**'] + [f"- \"{h['user_words']}\" → {'; '.join(h['changes'])}" for h in env['history']]
    lines += ['', f'Approve: `storyboard approve --shot {shot_id} --sheet {rev} --user-words "<the user\'s words>"`']
    (out / 'sheet.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    env.update({'sheet': {'rev': rev, 'path': str((out / 'sheet.md').relative_to(project_dir(path))), 'version': version,
                          'content_sha256': content_sha256(shot)}})
    write_json(_envelope_path(path, shot_id), env)
    return {'shot_id': shot_id, 'rev': rev, 'sheet': str(out / 'sheet.md'), 'image': str(image), 'version': version,
            'frames': [r['frame'] for r in state['frames']], 'artifacts': [str(out / 'sheet.md'), str(image)]}


def propose(project, shot_id, times, focus=None, captions=None):
    """Frames at shot fractions `times` (0..1), focus ids per fraction, captions per fraction."""
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    frames = _frames(shot, times)
    by_frame = lambda mapping: {_frames(shot, [t])[0]: v for t, v in (mapping or {}).items()}  # noqa: E731
    env = {'schema_version': 1, 'shot_id': shot_id, 'status': 'proposed', 'approval': None, 'history': (envelope(path, shot_id) or {}).get('history', []),
           'body': {'frames': frames, 'focus': by_frame(focus), 'captions': {str(k): v for k, v in by_frame(captions).items()}}}
    return _write_sheet(path, shot_id, env, by_frame(captions))


def revise(project, shot_id, user_words, ops, agent_note=None):
    """The user's words as typed edits on the shot, a rebuild from data, and a before/after sheet."""
    from .blender import revise_shot
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'storyboard revise')
    path = project_dir(project)
    env = envelope(path, shot_id)
    if env is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: storyboard propose first')
    if env.get('status') == 'variants':
        raise StudioError('DECISION_STALE', f"{shot_id}: takes {env['variants']['rev']} wait for a pick; storyboard pick first, then revise the picked take")
    ops = read_json(ops) if isinstance(ops, str) else ops
    shot = load_shot(path, shot_id)
    change, said = apply_ops(shot, ops)
    with tempfile.TemporaryDirectory() as tmp:
        change_file = Path(tmp) / 'change.json'
        write_json(change_file, {'base_revision': shot['revision'], 'scope': 'scene', 'targets': [], 'change': change, 'preserve': []})
        revise_shot(path, shot_id, change_file)
    previous_dir = project_dir(path) / env['sheet']['path']
    previous = [previous_dir.parent / f'frame_{f:06d}.png' for f in env['body']['frames']]
    env['history'] = env.get('history', []) + [{'at': now(), 'user_words': words, 'ops': ops, 'changes': said, 'agent_note': agent_note}]
    env.update({'status': 'proposed', 'approval': None})
    captions = {int(k): v for k, v in env['body'].get('captions', {}).items()}
    return {**_write_sheet(path, shot_id, env, captions, previous if all(p.is_file() for p in previous) else None), 'changes': said}


MAX_VARIANTS = 4   # past four takes a comparison sheet stops being readable on a phone


def _check_takes(takes):
    if not 2 <= len(takes) <= MAX_VARIANTS:
        raise StudioError('INPUT_INVALID', f'variants: give 2-{MAX_VARIANTS} takes (got {len(takes)}); one take is a plain storyboard propose')
    ids = [t.get('id') for t in takes]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i.isalnum() and len(i) <= 8 for i in ids):
        raise StudioError('INPUT_INVALID', f'variants: ids must be unique short letters/digits (got {ids})')
    for take in takes:
        extra = set(take.get('change', {})) - set(SHOT_CONTENT)
        if not take.get('label') or not take.get('change') or extra:
            raise StudioError('INPUT_INVALID', f"variant {take['id']}: needs label and change over {SHOT_CONTENT}" + (f' (not {sorted(extra)})' if extra else ''))


def _shape(value):
    """A value with its numbers blanked: two takes of the same shape differ only in knob values."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    return None if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _one_idea(takes):
    shapes = [stable_hash(_shape(t['change'])) for t in takes]
    return len(set(shapes)) == 1


def _stack(images_and_labels, target):
    from PIL import Image, ImageDraw, ImageFont
    from .common import font_file
    font = ImageFont.truetype(str(font_file({}, REPO)), 22)
    images = [(Image.open(p).convert('RGB'), label) for p, label in images_and_labels]
    width = max(i.width for i, _ in images)
    sheet = Image.new('RGB', (width, sum(i.height + 40 for i, _ in images)), (24, 24, 26))
    draw, y = ImageDraw.Draw(sheet), 0
    for image, label in images:
        draw.text((12, y + 8), label[:90], fill=(255, 210, 0), font=font)
        sheet.paste(image, (0, y + 40)); y += image.height + 40
    sheet.save(target)
    return target


def variants(project, shot_id, takes, times, focus=None, captions=None):
    """Build each take as its own version (shot.json is left as it was), measure the same frames of each, and write one
    comparison sheet. Nothing is approved here."""
    from .blender import build_shot
    path = project_dir(project)
    takes = read_json(takes) if isinstance(takes, str) else takes
    _check_takes(takes)
    original = load_shot(path, shot_id)
    frames = _frames(original, times)
    by_frame = lambda mapping: {_frames(original, [t])[0]: v for t, v in (mapping or {}).items()}  # noqa: E731
    root = project_dir(path) / 'decisions' / 'sheets' / f'storyboard_{shot_id}'
    rev = f"v{len(list(root.glob('v[0-9]*'))) + 1:02d}"
    out = root / rev; out.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        for take in takes:
            built = build_shot(path, shot_id, None, shot_override={**deepcopy(original), **deepcopy(take['change'])})
            folder = out / take['id']; folder.mkdir()
            state = _measure(path, shot_id, built['scene_version'], frames, by_frame(focus), folder)
            _sheet_image(state, folder, by_frame(captions))
            rows.append({'id': take['id'], 'label': take['label'], 'why': take.get('why'), 'change': take['change'],
                         'version': built['scene_version'], 'eyes': [r['eye'] for r in state['frames']]})
    finally:   # the takes are candidates, not the shot: put the shot back (its revision only moves forward)
        write_json(shot_path(path, shot_id), {**original, 'revision': load_shot(path, shot_id)['revision']})
    image = _stack([(out / r['id'] / 'sheet.png', f"{r['id']}. {r['label']}") for r in rows], out / 'sheet.png')
    lines = [f'# Storyboard {shot_id} — takes {rev}', '', '![takes](sheet.png)', '', 'Workbench blocking, not the look. Same frames in every take.', '']
    lines += [f"- **{r['id']}. {r['label']}** ({r['version']})" + (f" — {r['why']}" if r.get('why') else '') for r in rows]
    lines += ['', f'Pick: `storyboard pick --shot {shot_id} --sheet {rev} --variant <id> --user-words "<the user\'s words>"`']
    (out / 'sheet.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    env = envelope(path, shot_id) or {'schema_version': 1, 'shot_id': shot_id, 'history': []}
    env.update({'status': 'variants', 'approval': None, 'sheet': None,
                'variants': {'rev': rev, 'path': str((out / 'sheet.md').relative_to(project_dir(path))), 'base_content_sha256': content_sha256(original),
                             'takes': rows},
                'body': {'frames': frames, 'focus': by_frame(focus), 'captions': {str(k): v for k, v in by_frame(captions).items()}}})
    write_json(_envelope_path(path, shot_id), env)
    warnings = ['TAKES_ONE_IDEA: the takes differ only in numbers - one idea at several knob values is a revise; '
                'make each take a different move, staging or focus'] if _one_idea(takes) else []
    return {'shot_id': shot_id, 'rev': rev, 'sheet': str(out / 'sheet.md'), 'image': str(image), 'warnings': warnings,
            'takes': [{k: r[k] for k in ('id', 'label', 'version')} for r in rows], 'artifacts': [str(out / 'sheet.md'), str(image)]}


def pick(project, shot_id, sheet_rev, variant_id, user_words):
    """The user's take becomes the shot's content (its version is reused, nothing rebuilds) and gets a normal sheet."""
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'storyboard pick')
    path = project_dir(project)
    env = envelope(path, shot_id)
    pending = (env or {}).get('variants')
    if not pending or env.get('status') != 'variants' or pending['rev'] != sheet_rev:
        raise StudioError('DECISION_STALE', f"{shot_id}: the latest takes sheet is {(pending or {}).get('rev')} (status {(env or {}).get('status')}), not {sheet_rev}")
    take = next((t for t in pending['takes'] if t['id'] == variant_id), None)
    if take is None:
        raise StudioError('INPUT_INVALID', f"{shot_id}: no take {variant_id} on {sheet_rev} (takes: {[t['id'] for t in pending['takes']]})")
    shot = load_shot(path, shot_id)
    if content_sha256(shot) != pending['base_content_sha256']:
        raise StudioError('DECISION_STALE', f'{shot_id}: the shot changed after takes {sheet_rev}; build new takes')
    snapshot = read_json(shot_path(path, shot_id).parent / 'versions' / take['version'] / 'shot.snapshot.json')
    from .project import validate_shot
    validate_shot({**snapshot, 'revision': shot['revision'] + 1})   # the take was valid when built; the rules may have moved since
    write_json(shot_path(path, shot_id), {**snapshot, 'revision': shot['revision'] + 1})
    env['history'] = env.get('history', []) + [{'at': now(), 'user_words': words, 'ops': [{'op': 'pick', 'variant': variant_id, 'sheet': sheet_rev}],
                                               'changes': [f"take {variant_id} ({take['label']}) of {sheet_rev}"], 'agent_note': None}]
    env.update({'status': 'proposed', 'approval': None})
    captions = {int(k): v for k, v in env['body'].get('captions', {}).items()}
    return {**_write_sheet(path, shot_id, env, captions), 'picked': variant_id}


def approve(project, shot_id, user_words, sheet_rev):
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'storyboard approve')
    path = project_dir(project)
    env = envelope(path, shot_id)
    if env is None or (env.get('sheet') or {}).get('rev') != sheet_rev:
        raise StudioError('DECISION_STALE', f"{shot_id}: the latest storyboard sheet is {((env or {}).get('sheet') or {}).get('rev')}, not {sheet_rev}"
                          + (' (takes are waiting for a pick)' if (env or {}).get('status') == 'variants' else ''))
    shot = load_shot(path, shot_id)
    if content_sha256(shot) != env['sheet']['content_sha256']:
        raise StudioError('DECISION_STALE', f'{shot_id}: the shot changed after sheet {sheet_rev}; propose a new sheet')
    state = read_json(project_dir(path) / Path(env['sheet']['path']).parent / 'state.json')
    env.update({'status': 'approved', 'approval': {'user_words': words, 'sheet_rev': sheet_rev, 'version': env['sheet']['version'],
                                                   'content_sha256': env['sheet']['content_sha256'], 'at': now(),
                                                   'contract': {'frames': state['frames']}}})
    write_json(_envelope_path(path, shot_id), env)
    return {'shot_id': shot_id, 'status': 'approved', 'version': env['sheet']['version'], 'frames': len(state['frames'])}


def require(path, shot, version):
    """Looks, finals and paid generation keep the approved storyboard (projects on the decision ladder)."""
    from .decisions import adopted
    path = project_dir(path)
    if not adopted(path):
        return
    env = envelope(path, shot['shot_id'])
    if env is None or env['status'] != 'approved':
        raise StudioError('DECISION_UNAPPROVED', f"{shot['shot_id']}: the storyboard is not approved",
                          recovery='storyboard propose → show the sheet → storyboard approve --user-words …')
    contract = env['approval']['contract']
    if version == env['approval']['version']:
        return
    with tempfile.TemporaryDirectory() as tmp:
        measured = _measure(path, shot['shot_id'], version, [f['frame'] for f in contract['frames']],
                            {f['frame']: [k for k, v in f.get('focus', {}).items() if isinstance(v, list)] for f in contract['frames']}, tmp, render=False)
    problems = compare(contract, measured)
    if problems:
        raise StudioError('STORYBOARD_DRIFT', f"{shot['shot_id']} {version}: " + '; '.join(problems[:5]),
                          recovery='Show a new storyboard sheet of this version and get the user\'s approval, or undo the change')


def register_commands(subparsers):
    parser = subparsers.add_parser('storyboard', help='Agree each shot as pictures with the user (Workbench sheets, typed edits)')
    commands = parser.add_subparsers(dest='storyboard_command', required=True)
    p = commands.add_parser('propose'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--frames', default='0,0.5,1', help='fractions of the shot, e.g. 0,0.4,1')
    p.add_argument('--focus', help='JSON {fraction: [ids]} the contract keeps in frame'); p.add_argument('--captions', help='JSON {fraction: text}')
    p.set_defaults(handler=lambda a: propose(a.project, a.shot, [float(x) for x in a.frames.split(',')],
                                             {float(k): v for k, v in json.loads(a.focus).items()} if a.focus else None,
                                             {float(k): v for k, v in json.loads(a.captions).items()} if a.captions else None))
    p = commands.add_parser('revise'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--user-words', required=True); p.add_argument('--ops', required=True, help=f'JSON list of edits {OPS}; set/add/remove reach any value: {{op, path: "/camera/move/params/span", value|factor|delta}}')
    p.add_argument('--agent-note')
    p.set_defaults(handler=lambda a: revise(a.project, a.shot, a.user_words, a.ops, a.agent_note))
    p = commands.add_parser('approve'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--sheet', required=True); p.add_argument('--user-words', required=True)
    p.set_defaults(handler=lambda a: approve(a.project, a.shot, a.user_words, a.sheet))
    p = commands.add_parser('variants', help='2-4 different takes of a shot built side by side on one sheet')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--variants', required=True, help='JSON [{id, label, why, change: {camera|scene|actions|titles|graphics}}]')
    p.add_argument('--frames', default='0,0.5,1'); p.add_argument('--focus'); p.add_argument('--captions')
    p.set_defaults(handler=lambda a: variants(a.project, a.shot, a.variants, [float(x) for x in a.frames.split(',')],
                                              {float(k): v for k, v in json.loads(a.focus).items()} if a.focus else None,
                                              {float(k): v for k, v in json.loads(a.captions).items()} if a.captions else None))
    p = commands.add_parser('pick', help="the user's take (their words) becomes the shot; a normal sheet follows")
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--sheet', required=True)
    p.add_argument('--variant', required=True); p.add_argument('--user-words', required=True)
    p.set_defaults(handler=lambda a: pick(a.project, a.shot, a.sheet, a.variant, a.user_words))
    p = commands.add_parser('show'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: envelope(a.project, a.shot) or {})
