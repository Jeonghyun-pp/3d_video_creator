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
from .common import CODE, REPO, StudioError, blender_binary, file_hash, json_arg, lock, now, put_back, read_json, run_command, stable_hash, write_json
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
BOARD_CONTENT = ('scene', 'camera', 'actions', 'titles', 'graphics')   # what a board shows; its approval binds these


def _envelope_path(path, shot_id):
    return project_dir(path) / 'decisions' / 'storyboard' / f'{shot_id}.json'


def envelope(path, shot_id):
    file = _envelope_path(path, shot_id)
    return read_json(file) if file.is_file() else None


def content_sha256(shot):
    return stable_hash({k: shot.get(k) for k in BOARD_CONTENT})


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
    said = []
    for op in ops:
        kind = op.get('op')
        if kind not in OPS:
            raise StudioError('INPUT_INVALID', f'storyboard edit {kind!r} is not one of {OPS}')
        # read through content every time: a set on a whole key (/camera, /scene, /titles) rebinds it, and an op later in
        # the list must edit the new value (2026-10-07: a /camera replacement was reported but the old camera returned)
        camera, scene, titles = content['camera'], content['scene'], content['titles']
        move = camera.get('move')
        if kind in shot_edit.OPS:
            said.append(shot_edit.edit_shot(content, op))
        elif kind in KNOB_PATH:
            if not move:
                raise StudioError('INPUT_INVALID', f'{kind} needs a camera.move (this shot has fixed keys); use set on /camera/keys')
            meaning, how = KNOB_PATH[kind]
            if kind == 'camera.closer' and move.get('frames'):   # frame targets: every moment shows the subject larger
                for target in move['frames']:
                    target['height_share'] = round(target['height_share'] / op['factor'], 4)
                said.append(f"every frame target shown {1 / op['factor']:.2f}x as tall (the poses are solved from them)")
                continue
            if kind == 'camera.closer' and move.get('focus'):   # a solved focus: "closer" is the focus shown larger
                before = move['focus']['height_share']
                move['focus']['height_share'] = round(min(1.2, max(0.05, before / op['factor'])), 4)
                said.append(f"focus height {before} → {move['focus']['height_share']} of the frame (the camera is solved from it)")
                continue
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
    change = {'camera': content['camera']}   # the camera always goes back; other keys only when they changed
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
    if problems and contract.get('projection', 1) != measured.get('projection', 1):
        problems.append(f"(the approved board was measured with projection {contract.get('projection', 1)}, this version with "
                        f"{measured.get('projection', 1)}: geometry the near plane cuts no longer counts - if that is the change, it is "
                        "real and worth a new sheet)")
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
                 '--python', str(CODE / 'studio/blender_ops/storyboard_render.py'), '--', str(job)], Path(out_dir) / 'storyboard.log', timeout=900)
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


def _target_row(sheet_path, concept_path, hero_path, concept_label, hero_label):
    """Append a row to a sheet: the concept frame and the hero frame side by side, same height."""
    from PIL import Image, ImageDraw, ImageFont
    from .common import font_file
    font = ImageFont.truetype(str(font_file({}, REPO)), 20)
    sheet = Image.open(sheet_path).convert('RGB')
    h = 480
    tiles = []
    for p in (concept_path, hero_path):
        with Image.open(p) as src:
            im = src.convert('RGB')
        tiles.append(im.resize((max(1, round(im.width * h / im.height)), h)))
    row = Image.new('RGB', (max(sheet.width, sum(t.width for t in tiles) + 36), h + 44), (24, 24, 26))
    draw, x = ImageDraw.Draw(row), 12
    for tile, label in zip(tiles, (concept_label, hero_label)):
        row.paste(tile, (x, 34)); draw.text((x, 6), label, fill=(255, 210, 0), font=font); x += tile.width + 12
    out = Image.new('RGB', (max(sheet.width, row.width), sheet.height + row.height), (24, 24, 26))
    out.paste(sheet, (0, 0)); out.paste(row, (0, sheet.height))
    out.save(sheet_path)
    return sheet_path


def _picture_rows(path, shot, version, pictures, sheet_path, out_dir):
    """Append the frame pictures to a sheet: each picture with what was read on it (frame_targets.check_picture) above
    the built frame of the same moment with the probe's box of the subject, and what does not match, in words."""
    from PIL import Image, ImageDraw, ImageFont
    from .common import font_file
    from . import frame_targets
    font = ImageFont.truetype(str(font_file({}, REPO)), 18)
    version_dir = shot_path(path, shot['shot_id']).parent / 'versions' / version
    report = read_json(version_dir / 'frame_report.json') if (version_dir / 'frame_report.json').is_file() else {}
    rig = read_json(version_dir / 'camera_rig_report.json') if (version_dir / 'camera_rig_report.json').is_file() else {}
    move = read_json(version_dir / 'camera_move_report.json') if (version_dir / 'camera_move_report.json').is_file() else {}
    found = {row['concept_id']: row for row in frame_targets.moments(path, shot, report, rig, move)}
    store = frame_targets._store(path, shot['shot_id'])
    h, tiles, lines = 480, [], []
    for picture in pictures:
        frame = _frames(shot, [picture['frame']['at']])[0]
        with Image.open(project_dir(path) / picture['path']) as src:
            top = src.convert('RGB')
        top = top.resize((max(1, round(top.width * h / top.height)), h))
        with Image.open(Path(out_dir) / f'frame_{frame:06d}.png') as src:
            below = src.convert('RGB')
        below = below.resize((max(1, round(below.width * h / below.height)), h))
        width = max(top.width, below.width)
        tile = Image.new('RGB', (width, 2 * h + 90), (24, 24, 26))
        tile.paste(top, (0, 30)); tile.paste(below, (0, h + 60))
        draw = ImageDraw.Draw(tile)
        row = store['pictures'].get(picture['concept_id'])
        if row and row['sha256'] == picture['sha256']:
            r = {**row['read'], **(row.get('marked') or {})}
            if r.get('subject_box'):
                b = r['subject_box']
                draw.rectangle((b[0] * top.width, 30 + b[1] * h, b[2] * top.width, 30 + b[3] * h), outline=(255, 215, 0), width=3)
            if r.get('horizon_v') is not None:
                draw.line((0, 30 + r['horizon_v'] * h, top.width, 30 + r['horizon_v'] * h), fill=(0, 220, 255), width=2)
        moment = found.get(picture['concept_id'])
        if moment and moment['box']:
            b = moment['box']
            draw.rectangle((b[0] * below.width, h + 60 + b[1] * h, b[2] * below.width, h + 60 + b[3] * h), outline=(255, 215, 0), width=3)
        draw.text((4, 4), f"{picture['frame']['role']} {picture['concept_id']} (picture)", fill=(255, 210, 0), font=font)
        verdict = 'not measured (no probe of this moment)' if moment is None else ('matches' if not moment['problems'] else f"{len(moment['problems'])} differences")
        draw.text((4, h + 34), f"f{frame} built - {verdict}", fill=(255, 210, 0) if moment and not moment['problems'] else (255, 120, 90), font=font)
        lines += [f"- {picture['frame']['role']} (t={picture['frame']['at']}, {picture['concept_id']}): {verdict}"]
        lines += [f"  - {q}" for q in (moment or {}).get('problems', [])]
        lines += [f"  - kept different on purpose: {d['item']} - {d['why']}" for d in (moment or {}).get('departs', [])]
        tiles.append(tile)
    sheet = Image.open(sheet_path).convert('RGB')
    row_w = sum(t.width for t in tiles) + 12 * (len(tiles) + 1)
    out = Image.new('RGB', (max(sheet.width, row_w), sheet.height + 2 * h + 100), (24, 24, 26))
    out.paste(sheet, (0, 0))
    x = 12
    for tile in tiles:
        out.paste(tile, (x, sheet.height + 10)); x += tile.width + 12
    out.save(sheet_path)
    return sheet_path, lines


def board(project):
    """Every shot's latest storyboard sheet on one page (the second human checkpoint: approve or edit shot by shot)."""
    from .project import load_project
    path = project_dir(project)
    items, lines = [], ['# Storyboard board', '']
    reopened = []
    for entry in load_project(path)['shots']:
        env = envelope(path, entry['shot_id']) or {}
        if env.get('reopened'):   # the reel's watcher sent it back and no pick has answered it yet
            reopened.append(f"REEL_SHOT_REOPENED: {entry['shot_id']} - {env['reopened']['why']} (try: {env['reopened']['try']})")
        sheet = env.get('sheet') or {}
        image = (path / sheet['path']).parent / 'sheet.png' if sheet.get('path') else None
        lines.append(f"- {entry['shot_id']}: {env.get('status', 'no sheet')} {sheet.get('rev', '')} "
                     + (f"(approve: storyboard approve --shot {entry['shot_id']} --sheet {sheet['rev']} --user-words \"…\")" if sheet.get('rev') else ''))
        if image and image.is_file():
            items.append((image, f"{entry['shot_id']} {sheet['rev']} - {env.get('status')}"))
    if not items:
        raise StudioError('INPUT_INVALID', 'no storyboard sheets yet: storyboard propose each shot first')
    from . import patterns   # the reel's size flow from each shot's current build (advice: SCALE_FLOW_FLAT)
    from .frame_targets import _box_at
    shares, ends = [], []
    for entry in load_project(path)['shots']:
        shot = load_shot(path, entry['shot_id'])
        version = shot_path(path, entry['shot_id']).parent / 'versions' / str(shot.get('scene_version'))
        report = read_json(version / 'frame_report.json') if (version / 'frame_report.json').is_file() else {}
        shares.append(patterns.measure_shot(report, shot, None)[0].get('height_share') if report else None)
        rig = read_json(version / 'camera_rig_report.json') if (version / 'camera_rig_report.json').is_file() else {}
        pitch = {row['frame']: row.get('pitch_deg') for row in rig.get('samples', [])}
        params = ((shot.get('camera') or {}).get('move') or {}).get('params') or {}
        ids = (shot.get('screen') or {}).get('subject') or list(params.get('parts') or [params.get('target')])
        probed = sorted(report.get('frames', []), key=lambda f: f['frame'])
        at = lambda row: (_box_at(row, ids), -pitch[row['frame']] if pitch.get(row['frame']) is not None else None)  # noqa: E731
        from .blender_ops.show_core import legacy_handover
        handover = shot.get('handover') or legacy_handover(shot.get('direction'))
        ends.append({'shot_id': entry['shot_id'], 'handover': (handover or {}).get('kind'),
                     **({'first': at(probed[0]), 'last': at(probed[-1])} if probed else {})})
    try:
        usual = patterns.section(patterns.load(patterns.library_name(path)), 'all')[1]['items'].get('scale_step', {}).get('values')
    except StudioError:
        usual = None
    flow = patterns.scale_flow(shares, usual)
    repeats = patterns.framing_repeats(ends)
    lines += ['', f"Size flow (focus height_share in cut order): {shares} -> {flow['steps']}; study reels: {flow['usual']}"] + flow['warnings'] + repeats
    root = path / 'decisions' / 'sheets' / 'board'
    rev = f"b{len(list(root.glob('b*'))) + 1:02d}"
    out = root / rev; out.mkdir(parents=True, exist_ok=True)
    image = _stack(items, out / 'sheet.png')
    (out / 'sheet.md').write_text('\n'.join(lines + ['', '![board](sheet.png)']) + '\n', encoding='utf-8')
    from .storyboard_brief import brief   # the same reel in words: what each shot shows, how it moves and joins
    made = brief(path)
    return {'rev': rev, 'sheet': str(out / 'sheet.md'), 'image': str(image), 'brief': made['html'],
            'artifacts': [str(out / 'sheet.md'), str(image), *made['artifacts']], 'warnings': reopened + flow['warnings'] + repeats + made['warnings']}


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
    from .concept import picked, frames as frame_pictures
    pictures = frame_pictures(path, shot_id)
    concept = None if pictures else picked(path, shot_id)   # frame pictures are the targets when the shot has them
    hero = env['body'].get('hero_frame')
    moment_lines = []
    if pictures:
        image, moment_lines = _picture_rows(path, shot, version, pictures, image, out)
    if concept:   # the target row: the picked concept beside the frame that explains, at the same height
        image = _target_row(image, project_dir(path) / concept['path'], out / f'frame_{(hero if hero is not None else frames[-1]):06d}.png',
                            f"target {concept['concept_id']} (picked by {'the user' if concept['decision']['by'] == 'user' else 'the agent'})",
                            f"hero f{hero if hero is not None else frames[-1]}")
    lines = [f'# Storyboard {shot_id} — sheet {rev}', '', '![sheet](sheet.png)', '', f"Version {version}; {len(frames)} frames; Workbench blocking, not the look."
             + (f" Target: concept {concept['concept_id']} ({concept['path']}) beside hero frame {hero}." if concept else ''), '']
    lines += [f"{n}. frame {r['frame']}: camera at {r['eye']}, lens {r['lens_mm']} mm {captions.get(r['frame'], '')}" for n, r in enumerate(state['frames'], 1)]
    if moment_lines:
        lines += ['', '**Frame pictures against the build** (top: the picture and what was read on it; below: the built moment):'] + moment_lines
    from . import patterns
    pattern = patterns.compare_shot(path, shot, shot_path(path, shot_id).parent / 'versions' / version)
    lines += patterns.sheet_lines(pattern) + [f'- {w}' for w in patterns.project_records(path, shot)]
    if env.get('history'):
        lines += ['', '**What you asked → what changed:**'] + [f"- \"{h['user_words']}\" → {'; '.join(h['changes'])}" for h in env['history']]
    lines += ['', f'Approve: `storyboard approve --shot {shot_id} --sheet {rev} --user-words "<the user\'s words>"`']
    (out / 'sheet.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    env.update({'sheet': {'rev': rev, 'path': str((out / 'sheet.md').relative_to(project_dir(path))), 'version': version,
                          'content_sha256': content_sha256(shot),
                          **({'concept': {'concept_id': concept['concept_id'], 'sha256': concept['sha256']}} if concept else {}),
                          **({'frame_pictures': [{'concept_id': p['concept_id'], 'sha256': p['sha256'], 'at': p['frame']['at']} for p in pictures]}
                             if pictures else {})}})
    write_json(_envelope_path(path, shot_id), env)
    return {'shot_id': shot_id, 'rev': rev, 'sheet': str(out / 'sheet.md'), 'image': str(image), 'version': version,
            'frames': [r['frame'] for r in state['frames']], **({'pattern': pattern} if pattern else {}),
            'artifacts': [str(out / 'sheet.md'), str(image)]}


def propose(project, shot_id, times, focus=None, captions=None, hero=None):
    """Frames at shot fractions `times` (0..1), focus ids per fraction, captions per fraction; `hero` the fraction of the
    frame that explains (the one set beside the shot's concept frame)."""
    from .concept import require as require_concept
    path = project_dir(project)
    require_concept(path, shot_id)   # a shot is staged toward the picture it was picked to be
    shot = load_shot(path, shot_id)
    hero_frame = _frames(shot, [hero])[0] if hero is not None else None
    frames = _sheet_frames(path, shot, times, hero_frame)
    by_frame = lambda mapping: {_frames(shot, [t])[0]: v for t, v in (mapping or {}).items()}  # noqa: E731
    env = {'schema_version': 1, 'shot_id': shot_id, 'status': 'proposed', 'approval': None, 'history': (envelope(path, shot_id) or {}).get('history', []),
           'body': {'frames': frames, 'focus': by_frame(focus), 'captions': {str(k): v for k, v in by_frame(captions).items()},
                    **({'hero_frame': hero_frame} if hero_frame is not None else {})}}
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
    ops = json_arg(ops)
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
        if take.get('recipe') is not None:   # the feel the take is built for (library/camera_recipes.json); measured by storyboard animatic
            from .camera_taste import recipes
            known = [r['id'] for r in recipes()]
            if take['recipe'] not in known:
                raise StudioError('INPUT_INVALID', f"variant {take['id']}: recipe {take['recipe']!r} is not one of {known}")


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


def _sheet_frames(path, shot, times, hero_frame=None):
    """The frames a sheet shows: the asked times, every moment a frame picture shows (beside it) and the hero frame.
    One rule for a proposal and for takes: a take measured on fewer frames than the body names breaks the sheet a pick
    writes (2026-10-12 audit)."""
    from .concept import frames as frame_pictures
    pictured = {f for f in _frames(shot, [p['frame']['at'] for p in frame_pictures(path, shot['shot_id'])])} if frame_pictures(path, shot['shot_id']) else set()
    return sorted(set(_frames(shot, times)) | pictured | ({hero_frame} if hero_frame is not None else set()))


def variants(project, shot_id, takes, times, focus=None, captions=None):
    """Build each take as its own version (shot.json is left as it was), measure the same frames of each, and write one
    comparison sheet. Nothing is approved here."""
    from .blender import build_shot
    from .concept import require as require_concept
    path = project_dir(project)
    require_concept(path, shot_id)
    takes = json_arg(takes)
    _check_takes(takes)
    original = load_shot(path, shot_id)
    frames = _sheet_frames(path, original, times, ((envelope(path, shot_id) or {}).get('body') or {}).get('hero_frame'))
    by_frame = lambda mapping: {_frames(original, [t])[0]: v for t, v in (mapping or {}).items()}  # noqa: E731
    root = project_dir(path) / 'decisions' / 'sheets' / f'storyboard_{shot_id}'
    rev = f"v{len(list(root.glob('v[0-9]*'))) + 1:02d}"
    out = root / rev; out.mkdir(parents=True, exist_ok=True)
    rows, failed = [], []
    for take in takes:   # the takes are candidates, not the shot: built beside it, shot.json never written
        try:
            built = build_shot(path, shot_id, None, shot_override={**deepcopy(original), **deepcopy(take['change'])}, count_appearance=False,
                               candidate=True)
        except StudioError as exc:   # a take the scene cannot give is said on the sheet, never dropped silently
            failed.append({'id': take['id'], 'label': take['label'], 'code': exc.code, 'why': exc.message[:400], 'own': bool(take.get('own'))})
            continue
        folder = out / take['id']; folder.mkdir()
        state = _measure(path, shot_id, built['scene_version'], frames, by_frame(focus), folder)
        _sheet_image(state, folder, by_frame(captions))
        rows.append({'id': take['id'], 'label': take['label'], 'why': take.get('why'), 'recipe': take.get('recipe'), 'change': take['change'],
                     'version': built['scene_version'], 'eyes': [r['eye'] for r in state['frames']],
                     **{k: take[k] for k in ('own', 'chosen') if k in take}})
    if len(rows) < 2:
        raise StudioError('TAKES_UNBUILT', f'{shot_id}: {len(rows)} of {len(takes)} takes built - nothing to choose between: '
                          + '; '.join(f"{f['id']} {f['code']}: {f['why'][:160]}" for f in failed),
                          recovery='change the failing takes (another recipe, or its values inside their ranges) and make them again')
    image = _stack([(out / r['id'] / 'sheet.png', f"{r['id']}. {r['label']}") for r in rows], out / 'sheet.png')
    lines = [f'# Storyboard {shot_id} — takes {rev}', '', '![takes](sheet.png)', '', 'Workbench blocking, not the look. Same frames in every take.', '']
    lines += [f"- **{r['id']}. {r['label']}** ({r['version']})" + (f" — {r['why']}" if r.get('why') else '') for r in rows]
    lines += [f"- ~~{f['id']}. {f['label']}~~ not built: {f['code']} {f['why'][:200]}" for f in failed]
    lines += ['', f'Pick: `storyboard pick --shot {shot_id} --sheet {rev} --variant <id> --user-words "<the user\'s words>"`']
    (out / 'sheet.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    env = envelope(path, shot_id) or {'schema_version': 1, 'shot_id': shot_id, 'history': []}
    env.update({'status': 'variants', 'approval': None, 'sheet': None,
                'variants': {'rev': rev, 'path': str((out / 'sheet.md').relative_to(project_dir(path))), 'base_content_sha256': content_sha256(original),
                             'takes': rows, 'failed': failed},
                'body': {'frames': frames, 'focus': by_frame(focus), 'captions': {str(k): v for k, v in by_frame(captions).items()},
                         **({'hero_frame': env['body']['hero_frame']} if (env.get('body') or {}).get('hero_frame') is not None else {})}})
    write_json(_envelope_path(path, shot_id), env)
    warnings = ['TAKES_ONE_IDEA: the takes differ only in numbers - one idea at several knob values is a revise; '
                'make each take a different move, staging or focus'] if _one_idea(takes) else []
    warnings += [f"TAKE_UNBUILT: take {f['id']} ({f['label']}) - {f['code']}: {f['why'][:200]}" for f in failed]
    from .taste_picks import summary
    taste = summary(['take'])['text']   # the recipes the user picked before: say it beside the takes, never choose by it
    return {'shot_id': shot_id, 'rev': rev, 'sheet': str(out / 'sheet.md'), 'image': str(image), 'warnings': warnings,
            **({'taste': taste} if taste else {}),
            'takes': [{k: r[k] for k in ('id', 'label', 'version')} for r in rows], 'failed': failed,
            'artifacts': [str(out / 'sheet.md'), str(image)]}


ANIMATIC_WAIT_S = 900   # a workbench clip of one shot takes seconds; this only bounds a stuck worker


ANIMATIC_REMOTE_WAIT_S = 3600   # a rented server boots and renders a queue of clips first: bounds a stuck queue
ANIMATIC_STALL_S = 600   # nothing moved - not this job, not the queue ahead - for this long: a job no one will start


def animatic_clip(path, shot_id, version):
    return shot_path(path, shot_id).parent / 'versions' / str(version) / 'animatic.mp4'


def _animatic(path, shot_id, version, remote=False, job=None):
    """One version's clay clip (layout profile: the workbench engine) and its camera dynamics, kept beside the version:
    versions/<v>/animatic.mp4 and camera_dynamics.json. The clip is for the eye; the numbers come from the scene.
    remote: rendered on the rented GPU server (render submit --remote); job: one submitted already (reel clay --remote
    queues every shot first, so one server renders them all)."""
    import shutil
    import time
    from .camera_taste import measure_version
    from .jobs import job_status, submit_render
    clip = animatic_clip(path, shot_id, version)
    if not clip.is_file():
        job = job or submit_render(path, shot_id, version, profile='layout', remote=remote)
        from .jobs import ACTIVE_STATES, cancel_job, queued_remote_ahead
        from .waiting import wait_until

        def look():
            status = job_status(path, job['job_id'])
            if status['status'] in ('failed', 'cancelled', 'interrupted'):
                raise StudioError('ANIMATIC_FAILED', f"{shot_id} {version}: layout render {status['status']} "
                                  f"({(status.get('error') or {}).get('message', '')})")
            # progress is this job's frames or the queue ahead of it moving (12 clips share one server)
            return (status if status['status'] == 'complete' else None), {
                'status': status['status'], 'frames': (status.get('progress') or {}).get('completed_frames'),
                'ahead': queued_remote_ahead(path) if remote and status['status'] in ACTIVE_STATES else None}

        def give_up(reason):   # a job this wait asked for is not left running (and billing) behind it
            if not job.get('cache_hit'):
                cancel_job(path, job['job_id'])
        status = wait_until(look, limit_s=ANIMATIC_REMOTE_WAIT_S if remote else ANIMATIC_WAIT_S, stall_s=ANIMATIC_STALL_S,
                            on_give_up=give_up, what=f'{shot_id} {version} layout render {job["job_id"]}', code='ANIMATIC_FAILED',
                            every_s=1.0, sleep=time.sleep)
        shutil.copyfile(Path(status['output_dir']) / 'clip.mp4', clip)
    dynamics = measure_version(path, shot_id, version)
    from .quality_gate import read_roots   # a take carries what the hard floors read, so a pick reads them as a render does
    return {**{root: dynamics.get(root) for root in read_roots()},
            'version': version, 'animatic': str(clip.relative_to(project_dir(path))), 'dynamics': dynamics.get('judged'),
            'camera': dynamics.get('camera'), 'comfort': dynamics.get('comfort'), 'masked': dynamics.get('masked') or [],
            'small': dynamics.get('small') or [], 'nothing': dynamics.get('nothing') or []}


def animatic(project, shot_id, sheet_rev=None):
    """The clay clip and camera dynamics of every take on a takes sheet (storyboard variants), or of the shot's current
    version: what the user watches to pick a camera, and how it sits against their reference profile. Why (2026-10-12):
    the user asked that a camera chosen before any picture is painted be shown to be good and dynamic - a still sheet
    cannot show motion, and a camera was never measured against the reels they named."""
    path = project_dir(project)
    env = envelope(path, shot_id) or {}
    takes = (env.get('variants') or {}).get('takes') or []
    if sheet_rev and (env.get('variants') or {}).get('rev') != sheet_rev:
        raise StudioError('INPUT_INVALID', f"{shot_id}: the takes sheet is {(env.get('variants') or {}).get('rev')}, not {sheet_rev}")
    if not takes:
        current = load_shot(path, shot_id).get('scene_version')
        if not current:
            raise StudioError('INPUT_INVALID', f'{shot_id}: nothing built')
        return {'shot_id': shot_id, 'current': _animatic(path, shot_id, current)}
    rows = []
    for take in takes:
        made = _animatic(path, shot_id, take['version'])
        take.update({k: v for k, v in made.items() if k not in ('version', 'camera')})
        rows.append({'id': take['id'], **made})
    write_json(_envelope_path(path, shot_id), env)
    return {'shot_id': shot_id, 'takes': rows, 'warnings': feel_warnings(takes), 'artifacts': [str(path / r['animatic']) for r in rows]}


def feel_warnings(takes):
    """The takes as feels (camera_taste.recipe, measured): takes that all feel the same are one idea whatever their
    numbers (2026-10-12, the user: "숫자는 같아도 다른 느낌을 줄수 있잖아"), and a take should feel as it was built to."""
    measured = {t['id']: (t.get('dynamics') or {}).get('recipe') for t in takes if t.get('dynamics')}
    out = []
    if len(measured) >= 2 and len(set(measured.values())) == 1 and None not in measured.values():
        out.append(f"TAKES_ONE_FEEL: every take measures as {next(iter(measured.values()))} - build the takes as different recipes "
                   '(library/camera_recipes.json: each lists the moves that make it)')
    for t in takes:
        if t.get('recipe') and t['id'] in measured and measured[t['id']] != t['recipe']:
            also = (t.get('dynamics') or {}).get('also') or []
            out.append(f"TAKE_RECIPE_DIFFERS: take {t['id']} was built as {t['recipe']} and measures as {measured[t['id']] or 'no recipe'}"
                       + (f" (also {', '.join(also)})" if also else '') + " - see the take's feel line for the value that misses")
    return out


DELEGATION_SCOPE = 'takes'   # a delegation scope that names the takes (an empty scope covers everything off the ask-only list)


REASON_MIN = 8   # a reason that passes a floor is a sentence, not a word


def _agent_floors(shot_id, pending, take, because, shot=None):
    """What an agent's pick must not pass (the user's words pass them all): the director's own take unbuilt
    (TAKE_OWN_UNBUILT - direct the move again, or say why another take serves), no take that reads (TAKE_NONE_READS -
    direct the shot again; no reason passes it), a take the watcher did not read (TAKE_UNREAD), a take past a hard floor
    (quality_gate.held over the goal rows - SHOT_FLOOR, no reason passes it), and every reason floor of the goal rows
    (quality_gate.reason_lines: a rush, a held picture of nothing, a speck of a change, a masked move, a key change seen
    moving, a still tail ...) - each passes on its reason (`because`, or the shot list field the row names as `said`),
    read with the picture before a coloured render. `because`: {code: reason}. Why (2026-10-23, r3): four own takes
    failed and the defaults' takes were picked in their place; three cuts were picked though no take read; two picked
    takes rushed in half a second."""
    said = lambda code: len(str(because.get(code) or '').strip()) >= REASON_MIN   # noqa: E731
    own = [f for f in pending.get('failed') or [] if f.get('own')]
    if own and not said('TAKE_OWN_UNBUILT'):
        f = own[0]
        raise StudioError('TAKE_OWN_UNBUILT', f"{shot_id}: the director's own take {f['id']} ({f['label']}) was not built - {f['code']}: "
                          f"{f['why'][:240]}", recovery='find why (camera_move_report.json failure, the frame report), direct the move '
                          'again and make the takes again, or pick with --because TAKE_OWN_UNBUILT="<why another take serves>"')
    verdicts = (pending.get('judge') or {}).get('takes') or []
    if verdicts and not any(v.get('reads') for v in verdicts):
        raise StudioError('TAKE_NONE_READS', f"{shot_id}: the watcher read none of the takes - " + '; '.join(
                          f"{v['id']}: {v.get('note', '')[:120]}" for v in verdicts),
                          recovery='direct the shot again (size, view, what it frames, moments, placement) and make the takes again')
    mine = next((v for v in verdicts if v.get('id') == take['id']), None)
    if mine is not None and not mine.get('reads'):   # 2026-10-23 r3 s01: the one take that read was held back by another
        raise StudioError('TAKE_UNREAD', f"{shot_id}: the watcher did not read take {take['id']} - {mine.get('note', '')[:160]}",   # floor and
                          recovery='pick a take the watcher read (with its reason where a floor asks one), or direct again')   # an unread one was taken
    from .quality_gate import _walk, held, reason_lines
    hard = held(take)   # the goal rows' hard floors, read as the render gate reads them: no reason passes one
    if hard:
        raise StudioError('SHOT_FLOOR', f"{shot_id}: take {take['id']} - {hard[0]['code']} - {hard[0]['text'][:300]}",
                          recovery='pick another take or direct the shot again - this floor holds whatever the reason (only the user\'s words keep it)')
    for line in reason_lines(take):
        planned = line['said'] and len(str(_walk(shot or {}, line['said']) or '').strip()) >= REASON_MIN
        if not said(line['code']) and not planned:
            where = f" or write {line['said']} in the shot list" if line['said'] else ''
            raise StudioError(line['code'], f"{shot_id}: take {take['id']} - {line['text'][:300]}",
                              recovery=f"pick a take without it, direct the shot again (library/shot_goals.json {line['goal']}: what to try), "
                                       f'or pick with --because {line["code"]}="<what the cut gains as it is>"{where}')


def pick(project, shot_id, sheet_rev, variant_id, user_words=None, agent_note=None, repeat_why=None, because=None):
    """The picked take becomes the shot's content (its version is reused, nothing rebuilds) and gets a normal sheet.
    The user's words pick any take. In a delegated run the agent picks with its note, and only on a sheet a watcher
    judged (storyboard judge): a take nobody watched as a clip is never picked by the agent (default closed). The
    user's picks, with the takes passed over, are their taste (taste_picks); the agent's are not."""
    path = project_dir(project)
    env = envelope(path, shot_id)
    pending = (env or {}).get('variants')
    if not pending or env.get('status') != 'variants' or pending['rev'] != sheet_rev:
        raise StudioError('DECISION_STALE', f"{shot_id}: the latest takes sheet is {(pending or {}).get('rev')} (status {(env or {}).get('status')}), not {sheet_rev}")
    if user_words:
        from .generative.review import check_user_words
        words, note = check_user_words(user_words, 'storyboard pick'), None
    else:
        from .project import load_project
        delegation = load_project(path).get('delegation') or {}
        scope = delegation.get('scope') or []
        if not (agent_note and len(agent_note.strip()) >= 8 and delegation and (not scope or DELEGATION_SCOPE in scope)):
            raise StudioError('INPUT_INVALID', "storyboard pick needs the user's words (--user-words); the agent picks with --agent-note "
                              f"only in a run the user delegated (scope empty or '{DELEGATION_SCOPE}')")
        if not pending.get('judge'):
            raise StudioError('TAKES_UNJUDGED', f'{shot_id}: no watcher judged takes {sheet_rev} - storyboard judge --shot {shot_id} '
                              f'--sheet {sheet_rev} first; the agent picks only what was watched as a clip')
        words, note = None, agent_note.strip() + (f" (judge picked {pending['judge']['pick']})" if pending['judge']['pick'] != variant_id else '')
    take = next((t for t in pending['takes'] if t['id'] == variant_id), None)
    if take is None:
        raise StudioError('INPUT_INVALID', f"{shot_id}: no take {variant_id} on {sheet_rev} (takes: {[t['id'] for t in pending['takes']]})")
    felt = (take.get('dynamics') or {}).get('recipe')   # the picked feel, for the user's taste in recipes
    feel = lambda t: (t.get('dynamics') or {}).get('recipe') or t.get('recipe')   # noqa: E731 - measured, else as built
    if not user_words and not (repeat_why and len(repeat_why.strip()) >= 8):
        # the agent's pick in the reel: the feel that makes the reel one feel throughout (RECIPE_MONOTONE), or the feel
        # the reel's watcher sent back, needs its reason (2026-10-22: five shots picked alone took the same pull-back).
        # A take that moves otherwise than the reel's commonest feel is what the reel needs: never refused for the rest
        # of the reel (2026-10-23, r3: a look_up take was refused because 56 % of the reel felt watch)
        from .camera_taste import PROFILE, load_profile, recipe_lines, recipe_mix
        names = [m['felt'] or m['recipe'] for m in _reel_motions(path, shot_id) if m['felt'] or m['recipe']] + [feel(take) or 'pictured']
        monotone = recipe_lines(load_profile() if PROFILE.is_file() else {}, names)
        mix = recipe_mix(names)
        if monotone and max(mix, key=mix.get) == (feel(take) or 'pictured'):
            raise StudioError('TAKE_REPEATS_REEL', f'{shot_id}: take {variant_id} ({feel(take)}) - {monotone[0]}',
                              recovery='pick a take that moves otherwise, or keep it with --repeat-why "<why this scene needs it>"')
        rejected = (env.get('reopened') or {}).get('rejected')
        if rejected and feel(take) == rejected:
            raise StudioError('TAKE_SAME_AS_REJECTED', f"{shot_id}: the reel's watcher sent back the {rejected} take; {variant_id} moves "
                              'the same', recovery='pick another take, or keep it with --repeat-why "<why>"')
    if not user_words:
        _agent_floors(shot_id, pending, take, because or {}, shot=load_shot(path, shot_id))
    # the shot and its record change together: a sheet that fails (Blender refused, a killed command) puts both back -
    # 2026-10-18: a pick whose sheet could not start Blender left the take as the shot and the pick unrecorded
    with lock(path / '.project.lock', blocking=False, reentrant=True), put_back(path, [shot_path(path, shot_id), _envelope_path(path, shot_id)]) as kept:
        shot = load_shot(path, shot_id)
        if content_sha256(shot) != pending['base_content_sha256']:
            raise StudioError('DECISION_STALE', f'{shot_id}: the shot changed after takes {sheet_rev}; build new takes')
        snapshot = read_json(shot_path(path, shot_id).parent / 'versions' / take['version'] / 'shot.snapshot.json')
        from .project import validate_shot
        validate_shot({**snapshot, 'revision': shot['revision'] + 1})   # the take was valid when built; the rules may have moved since
        write_json(shot_path(path, shot_id), {**snapshot, 'revision': shot['revision'] + 1})
        env['history'] = env.get('history', []) + [{'at': now(), 'user_words': words,
                                                   'ops': [{'op': 'pick', 'variant': variant_id, 'sheet': sheet_rev, 'recipe': felt,
                                                            **({'repeat_why': repeat_why.strip()} if repeat_why else {}),
                                                            **({'because': {k: v.strip() for k, v in because.items()}} if because else {})}],
                                                   'changes': [f"take {variant_id} ({take['label']}) of {sheet_rev}"], 'agent_note': note}]
        env.update({'status': 'proposed', 'approval': None})
        env.pop('reopened', None)   # the reel's watcher sent it back; this pick answers it
        for key in ('takes_rounds', 'takes_directed', 'takes_extra'):   # directed again from here on counts afresh (DIRECT_RETRIES)
            env.pop(key, None)
        captions = {int(k): v for k, v in env['body'].get('captions', {}).items()}
        sheet = _write_sheet(path, shot_id, env, captions)
        kept.keep()
    if feel(take) and words:   # the user's taste only, once the pick stands
        from .taste_picks import record
        record('take', {'recipe': feel(take)}, [{'recipe': feel(t)} for t in pending['takes'] if t['id'] != variant_id and feel(t)],
               words, path, ref=f'{shot_id} {sheet_rev} {variant_id}')
    return {**sheet, 'picked': variant_id}


# --- takes made from the shot's motion, judged as clips (2026-10-22) ---------------------------------------------

TAKES_MADE = 3   # the shot's own recipe and two others: enough to see a choice, few enough to build and watch per shot


def _neighbours(path, shot_id):
    """The shots just before and after this one in the reel, each with 'where' (before / after)."""
    from .project import load_project
    ids = [s['shot_id'] for s in load_project(path).get('shots') or []]
    if shot_id not in ids:
        return []
    i = ids.index(shot_id)
    beside = ([(ids[i - 1], 'before')] if i > 0 else []) + ([(ids[i + 1], 'after')] if i + 1 < len(ids) else [])
    return [{**load_shot(path, other), 'where': where} for other, where in beside]


def _neighbour_recipes(path, shot_id):
    """The recipes the shots either side move by (their built move's, else their shot list's)."""
    out = set()
    for s in _neighbours(path, shot_id):
        made = (((s.get('camera') or {}).get('move') or {}).get('recipe') or {}).get('id')
        out |= {made or ((s.get('direction') or {}).get('move') or {}).get('recipe')} - {None}
    return out


def _reel_motions(path, shot_id):
    """[{shot_id, recipe, felt}] of every other shot of the reel: the recipe its built move was made by (else its shot
    list's) and the feel its current version measures (camera_dynamics.json) - what a shot's takes are chosen beside.
    Why (2026-10-22): judged beside its two neighbours only, five shots each picked the same pull-back."""
    from .project import load_project
    out = []
    for entry in load_project(path).get('shots') or []:
        if entry['shot_id'] == shot_id or not shot_path(path, entry['shot_id']).is_file():
            continue
        s = load_shot(path, entry['shot_id'])
        made = (((s.get('camera') or {}).get('move') or {}).get('recipe') or {}).get('id')
        recipe = made or ((s.get('direction') or {}).get('move') or {}).get('recipe')
        measured = shot_path(path, entry['shot_id']).parent / 'versions' / str(s.get('scene_version')) / 'camera_dynamics.json'
        felt = ((read_json(measured).get('judged') or {}).get('recipe') if measured.is_file() else None)
        out.append({'shot_id': entry['shot_id'], 'recipe': recipe, 'felt': felt})
    return out


def _uses(motions):
    """How many other shots move by each recipe (their feel when measured, else their recipe)."""
    counts = {}
    for m in motions:
        name = m['felt'] or m['recipe']
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


REEL_REPEAT = 2   # a shot whose own recipe this many other shots already move by gets one more take of a recipe the reel lacks


def _clip_seconds(clip):
    import subprocess
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(clip)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def _ui_centre(path):
    """Where a framed subject's middle stands by default: the middle of the frame no platform UI covers."""
    from .blender import ui_rect
    from .project import load_project
    out = load_project(path)['output']
    style = read_json(project_dir(path) / 'style.json') if (project_dir(path) / 'style.json').is_file() else {}
    x0, y0, x1, y1 = ui_rect(style, (out['width'], out['height']))
    return [round((x0 + x1) / 2, 4), round((y0 + y1) / 2, 4)]


def _carried(path, shot_id):
    """The azimuth a cut takes from the one before when that one hands over on (continue, match_action): where its
    planned move ends looking from (recipe_moves.end_azimuth), else None."""
    from .blender_ops.show_core import CONTINUOUS
    from .project import load_project
    from .recipe_moves import end_azimuth
    ids = [e['shot_id'] for e in load_project(path)['shots']]
    at = ids.index(shot_id) if shot_id in ids else 0
    if at == 0:
        return None
    before = load_shot(path, ids[at - 1])
    if (before.get('handover') or {}).get('kind') not in CONTINUOUS:
        return None
    return end_azimuth((before.get('direction') or {}).get('move'))


def take_rows(path, shot, recipes=None):
    """[{id, label, why, recipe, own, chosen, change}], [{recipe, why}]: the takes a shot's motion makes - its own move
    (the director's settings or moments; own), the compared takes the director wrote (direction.move.others), then the
    recipes asked for (else those the reel and the neighbours use least first, then the library's order) framed as the
    director framed the shot (recipe_moves.inherit: a compared take varies the motion, never invents the framing), and,
    when frame pictures set its camera now, that camera as it is ('as pictured'). A recipe the shot cannot take, or
    whose range the director's framing lies outside, is said with why, never dropped silently."""
    from .recipe_moves import FRAMING_SAID, book, camera_with, framing_of, inherit, pattern_size, recipe_move
    shot_id = shot['shot_id']
    size = pattern_size(path, shot)
    intent = (shot.get('direction') or {}).get('move')
    if not intent:
        raise StudioError('INPUT_INVALID', f'{shot_id}: MOVE_UNDECIDED - the shot list says no direction.move; takes are made from it')
    library = book()
    unknown = [r for r in recipes or () if r not in library]
    if unknown:
        raise StudioError('INPUT_INVALID', f'{shot_id}: recipes {unknown} are not in library/camera_recipes.json ({sorted(library)})')
    beside = _neighbour_recipes(path, shot_id)
    uses = _uses(_reel_motions(path, shot_id))   # the reel's motions so far: the least used come first (never ranked by taste)
    centre = _ui_centre(path)
    from .views import side_azimuth, subject_frame
    from .recipe_moves import subject_of
    side = side_azimuth(subject_frame(path, shot, subject_of(shot) or ''))   # the subject's broad side: a default azimuth
    carry = _carried(path, shot_id)   # a continued cut starts looking the way the cut before ends (recipe_moves.CARRIED)
    framing = framing_of(intent, library)
    base = {k: v for k, v in intent.items() if k not in ('others', 'moments', 'settings', 'path', 'follow')}
    asked = [(intent, True)] + [({**base, **other}, False) for other in intent.get('others') or []]   # the director's own
    order = list(recipes) if recipes else sorted((r for r in library if r != intent['recipe']),
                                                 key=lambda r: (uses.get(r, 0), r in beside, list(library).index(r)))
    wanted = len(asked) + (len(recipes) if recipes else max(0, TAKES_MADE - len(asked)) + (1 if uses.get(intent['recipe'], 0) >= REEL_REPEAT else 0))
    camera = shot.get('camera') or {}
    rows, refused = [], []
    move = camera.get('move') or {}
    if move.get('type') == 'frame_to_frame' and move.get('frames'):
        rows.append({'id': 'P', 'label': 'as pictured (frame pictures joined)', 'why': 'the camera its pictures set now',
                     'recipe': None, 'own': False, 'change': {'camera': deepcopy(camera)}})
    written = {(a['recipe'], bool(a.get('moments'))) for a, _own in asked}
    for rid in order:
        if (rid, False) in written:
            continue
        kept, outside = inherit(framing, rid, library)
        if outside:
            refused.append({'recipe': rid, 'why': 'the director framed the shot outside its range: ' + ', '.join(
                f'{n} {v} (it takes {lo}..{hi})' for n, (v, lo, hi) in outside.items()) + ' - write it under direction.move.others to compare it'})
            continue
        unsaid = [n for n in FRAMING_SAID if n in ((library[rid].get('make') or {}).get('choose') or {}) and n not in kept]
        if unsaid:   # nothing the director said frames it there: never a recipe default (2026-10-23)
            refused.append({'recipe': rid, 'why': f"the director's framing does not carry to it ({', '.join(unsaid)} unsaid for what it frames) "
                            '- write it under direction.move.others with its framing to compare it'})
            continue
        asked.append(({**base, 'recipe': rid, 'settings': kept, 'why': f"another feel to compare: {library[rid].get('means', '')}"}, False))
    for take_intent, own in asked:
        if len([r for r in rows if r['recipe']]) >= wanted:
            break
        rid = take_intent['recipe']
        try:
            made = recipe_move(shot, take_intent, library, size, centre=centre, side=side, carry=carry)
        except StudioError as exc:
            if own:   # the director's own move is never dropped: its failure stands on the sheet (TAKE_OWN_UNBUILT)
                raise
            refused.append({'recipe': rid, 'why': exc.message})
            continue
        rows.append({'id': chr(ord('A') + len([r for r in rows if r['recipe']])),
                     'label': f"{rid} ({library[rid].get('name', rid)})" + (f" - {len(take_intent['moments'])} moments" if take_intent.get('moments') else ''),
                     'why': take_intent.get('why'), 'recipe': rid, 'own': own, 'chosen': made['move']['recipe']['chosen'],
                     'change': {'camera': camera_with(deepcopy(camera), made['move'])}})
    return rows, refused


DIRECT_RETRIES = 2   # takes sheets a shot may be directed again after its first before the agent stops and reports (a
                     # floor that keeps refusing is an engine gap or the director's limit - said, never looped on)


def because_codes():
    """The agent floors a reason may pass: the own take unbuilt and every reason floor of the goal rows (TAKE_NONE_READS
    and the hard floors: none)."""
    from .quality_gate import reason_rows
    return ('TAKE_OWN_UNBUILT', *dict.fromkeys(r['code'] for _row, r in reason_rows()))


def _because(rows):
    """--because CODE=WHY rows -> {code: why}; a code no reason passes is refused."""
    out = {}
    for row in rows or []:
        code, _, why = row.partition('=')
        if code not in because_codes():
            raise StudioError('INPUT_INVALID', f'--because {code!r}: a reason passes only {because_codes()}')
        out[code] = why
    return out


def _framing_defaults(take):
    """The framing values (recipe_moves.FRAMING_VALUES) a take leaves to its recipe's defaults."""
    from .recipe_moves import FRAMING_VALUES
    return [k for k in FRAMING_VALUES if (take.get('chosen') or {}).get(k) == 'default']


def _marked(path, shot_id, version, clip):
    """The clip a judge watches: with the version's explainer graphics and labels laid over it, as the clay and the final
    show them (reel.marks_of) - the plain clip when it has none."""
    snapshot = shot_path(path, shot_id).parent / 'versions' / version / 'shot.snapshot.json'
    built = read_json(snapshot) if snapshot.is_file() else {}
    if not (built.get('graphics') or built.get('labels')):
        return clip
    from .reel import _probe, caption, marks_of
    target = clip.with_name('animatic_marked.mp4')
    if not target.is_file() or target.stat().st_mtime < clip.stat().st_mtime:
        layer, named = marks_of(path, shot_id, version, _probe(clip)[1])
        caption(clip, target, '', '', graphics=layer, labels=named)
    return target


def _take_notes(take):
    """What the engine measured of a take that a watcher should know: the director's own, framing left to defaults,
    every reason floor's line (quality_gate.reason_lines)."""
    from .quality_gate import reason_lines
    notes = (['the director\'s own'] if take.get('own') else []) + \
        ([f"{', '.join(_framing_defaults(take))} left to the recipe's defaults"] if _framing_defaults(take) else []) + \
        [line['text'].split(' - ')[0] for line in reason_lines(take)]
    return f" [{'; '.join(notes)}]" if notes else ''


def _replanned(path, shot_id, reopen):
    """Whether the shot list plans the cut anew since it was sent back to its plan (plan_read.PLAN_FIELDS)."""
    from .plan_read import _entry, plan_sha
    entry = _entry(path, shot_id)
    return entry is not None and plan_sha(entry) != reopen.get('plan_sha')


def takes(project, shot_id, recipes=None, user_words=None):
    """Takes of a shot made from its motion (take_rows), built side by side (variants) and as clay clips with their
    measured feel and comfort (animatic): what judge then watches. Nothing is picked here. A shot directed again more
    than DIRECT_RETRIES times since its last pick is held (TAKES_RETRIES_SPENT) until the user's words open it."""
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    env = envelope(path, shot_id) or {'schema_version': 1, 'shot_id': shot_id, 'history': [], 'status': 'none'}
    rounds = env.get('takes_rounds', 0)
    reopen = env.get('reopened') or {}
    if reopen.get('level') == 'plan' and env.get('takes_reset_for') != reopen.get('at') and _replanned(path, shot_id, reopen):
        # sent back to what it shows and planned anew: the tries were of the old plan (once per reopen, so a reopen that
        # changed nothing never resets them). Why (2026-10-26, semiconductor_fab s26: the viewer's reopen asked a new plan,
        # the redrawn chip could get no takes - its camera tries of the old plan were spent)
        rounds, env['takes_reset_for'], env['takes_directed'] = 0, reopen['at'], []
        env.pop('takes_extra', None)
    if user_words:
        from .generative.review import check_user_words
        env['takes_opened'] = {'user_words': check_user_words(user_words, 'storyboard takes'), 'at': now(), 'after': rounds}
        rounds = 0
    # what the shot is directed as: one more round past the limit only for a direction not tried yet (2026-10-24: a
    # shot whose tries ran out had no way on but the user; the same direction again is still held)
    directed = stable_hash({k: shot.get(k) for k in ('direction', 'frame', 'place', 'elements')})[:16]
    tried = env.get('takes_directed') or []
    if rounds > DIRECT_RETRIES and (directed in tried or env.get('takes_extra')):
        raise StudioError('TAKES_RETRIES_SPENT', f'{shot_id}: takes were made {rounds} times since its last pick - stop and report '
                          'what keeps failing (an engine gap or the limit of the direction)',
                          recovery='direct it in a way not tried yet (one more round), route it to a model (paid: the user\'s cap), '
                                   'or reel answer --awaits with what the user must settle; storyboard takes --user-words opens more')
    env['takes_rounds'] = rounds   # the user's words or a new plan, kept though the takes are refused below
    write_json(_envelope_path(path, shot_id), env)
    # refused before anything is built (take_rows raising, or fewer than two takes): no try spent. Why (2026-10-26,
    # semiconductor_fab s28: four such refusals spent every round and no take of it was ever built)
    rows, refused = take_rows(path, shot, recipes)
    if len(rows) < 2:
        raise StudioError('INPUT_INVALID', f'{shot_id}: {len(rows)} take(s) could be made - nothing to choose between: '
                          + '; '.join(f"{r['recipe']}: {r['why']}" for r in refused))
    if rounds > DIRECT_RETRIES:
        env['takes_extra'] = {'directed': directed, 'at': now()}
    env['takes_rounds'] = rounds + 1   # counted when made, built or not; a pick starts the count again
    env['takes_directed'] = tried + [directed]
    write_json(_envelope_path(path, shot_id), env)
    key_at = float(shot['direction']['move']['key_at'])
    made = variants(project, shot_id, rows, sorted({0.0, round(key_at, 3), 1.0}))
    clips = animatic(project, shot_id, made['rev'])
    warnings = made['warnings'] + clips['warnings']
    warnings += [f"TAKE_FRAMING_DEFAULT: take {r['id']} ({r['label']}) leaves {', '.join(_framing_defaults(r))} to the recipe's "
                 'defaults - write the take under direction.move.others with the framing it should have' for r in rows if _framing_defaults(r)]
    built = {t['id']: t for t in (envelope(path, shot_id) or {}).get('variants', {}).get('takes') or []}
    from .quality_gate import reason_lines
    warnings += [f"take {t['id']}: {line['text']}" for t in built.values() for line in reason_lines(t)]
    return {**made, 'animatic': clips['takes'], 'warnings': warnings, 'refused': refused,
            'next': f'storyboard judge --shot {shot_id} --sheet {made["rev"]}'}


JUDGE_ITEMS = ('reads', 'key_seen', 'change_seen', 'names_change', 'shows_the_line', 'rushed', 'dull', 'repeats_reel', 'harsh',
               'lost')   # what a watcher answers of each take (rushed, with each clip's length: 2026-10-26, plan R4)


def _judge_schema(ids):
    take = {'type': 'object', 'additionalProperties': False, 'required': ['id', *JUDGE_ITEMS, 'note'],
            'properties': {'id': {'enum': ids}, **{k: {'type': 'boolean'} for k in JUDGE_ITEMS}, 'note': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['takes', 'rank', 'pick', 'why'],
            'properties': {'takes': {'type': 'array', 'items': take}, 'rank': {'type': 'array', 'items': {'enum': ids}},
                           'pick': {'enum': ids}, 'why': {'type': 'string'}}}


JUDGE_PROMPT = """You choose the camera of one shot of a short vertical explainer reel. Answer only from the attached images.
Each image is one clip as {frames} left to right ({shares} % through it), then M, its motion map - white where the picture
changed over the clip, black where it held - its number at the top (shot N). The first
images are the takes of THIS shot; the last are the shots just before and after it, as they stand (context, not choices).
This is grey clay blocking: judge composition and motion, never surfaces or colour.
For each take answer:
- reads: a viewer can tell what the subject is and what is happening in it
- key_seen: the key moment ({key}) is on screen and large enough to read
- change_seen: what must change on screen in this shot is seen changing ({changes})
- names_change: a viewer can tell which thing is changing - large, named or pointed at (true when nothing must change)
- shows_the_line: the frames show what the narration says over the shot
- rushed: the clip moves on before what it shows can be taken in - the key moment or the change is still starting, or
  has just landed, at its last frame, or there is no time to read what it opens or moves (judge by its length and its
  frames' times); a short clip whose one picture reads at once is not rushed
- dull: nothing new is seen over the clip (the frames and the motion map show nothing happening or revealed), or it
  repeats the neighbouring shots' framing; a clip is never dull for its length while something keeps changing or being
  revealed in it
- repeats_reel: it moves the way most other shots of the reel already move ({reel})
- harsh: motion an eye cannot follow - the picture lurches (a burst from still, a lunge from the whole thing to one
  detail between two neighbouring frames, a stop dead, a shake or a swing) or moves with no clear destination; a
  destination in view does not excuse a lurch, and a fast, even flight toward a place in view that eases in and stops
  is not harsh for its speed
- lost: a viewer cannot tell where the camera is or what to look at
- note: one sentence, what makes it work or fail
Then rank the takes best first, pick one and say why in one or two sentences - for this scene, in this reel: prefer a
take that does not repeat the reel's common motion unless the scene needs it.{reopened}
The shot: {purpose}. Narration over it: {narration}. Its motion as planned: {move}. Takes: {takes}."""


def judge(project, shot_id, sheet_rev=None, reader=None, force=False):
    """A watcher's verdict on a takes sheet: each take's clip (four keyframes) beside the shots either side, judged by a
    model (codex_json; outside_model where none starts) on whether it reads, shows its key moment and its line, and is
    neither dull nor harsh - ranked, one picked with why. Recorded on the sheet; storyboard pick --agent-note takes only
    a judged sheet's pick (default closed). The engine never ranks takes itself."""
    from .screen_grammar import JUDGE_SHARES, codex_json, strips
    path = project_dir(project)
    env = envelope(path, shot_id) or {}
    pending = env.get('variants') or {}
    if env.get('status') != 'variants' or (sheet_rev and pending.get('rev') != sheet_rev):
        raise StudioError('DECISION_STALE', f"{shot_id}: no takes sheet {sheet_rev or ''} waiting (latest {pending.get('rev')}, status {env.get('status')})")
    takes = pending['takes']
    missing = [t['id'] for t in takes if not t.get('animatic')]
    if missing:
        raise StudioError('INPUT_INVALID', f'{shot_id}: takes {missing} have no clip - storyboard animatic --shot {shot_id} --sheet {pending["rev"]}')
    shot = load_shot(path, shot_id)
    folder = project_dir(path) / Path(pending['path']).parent / 'judge'
    fps = (shot.get('fps') or 30)
    from .reel_judge import timed   # each clip's length and its frames' times (the watcher judges rushed by them)
    clips = []
    for t in takes:
        clip = _marked(path, shot_id, t['version'], path / t['animatic'])
        clips.append((t['id'], f"take {t['id']}: {t['label']} ({timed(_clip_seconds(clip))}){_take_notes(t)}", clip))
    for other in _neighbours(path, shot_id):
        clip = animatic_clip(path, other['shot_id'], other.get('scene_version')) if other.get('scene_version') else None
        if clip and clip.is_file():
            clips.append((None, f"{other['shot_id']} ({other['where']}; {timed(_clip_seconds(clip))})", clip))
    images = [strips(clip, [{'index': n, 'start_s': 0.0, 'len_s': _clip_seconds(clip)}], {'fps': fps}, folder / f'clip{n}',
                     shares=JUDGE_SHARES, motion=True)[0]
              for n, (_tid, _label, clip) in enumerate(clips)]
    intent = (shot.get('direction') or {}).get('move') or {}
    from .change_moves import changes_of, words as change_words
    reel = ', '.join(f"{m['shot_id']} {m['felt'] or m['recipe'] or '?'}" for m in _reel_motions(path, shot_id)) or 'no other shots yet'
    opened = env.get('reopened')
    from .reel_judge import frames_words
    from .show_how import purpose_words   # what the cut is for (stored as goal) and what its viewer learns
    prompt = JUDGE_PROMPT.format(**frames_words(), key=f"at {intent.get('key_at')} of the shot: {intent.get('reveals')}", purpose=purpose_words(shot),
                                 changes='; '.join(change_words(c) for c in changes_of(shot)) or 'nothing - the shot holds',
                                 reel=reel,
                                 reopened=(f"\nThe reel's watcher asked to make this shot again: {opened['why']} Try: {opened['try']}. The take "
                                           f"it rejected moved as {opened.get('rejected') or 'unknown'}." if opened else ''),
                                 narration=(shot.get('narration') or {}).get('text') or '(none)',
                                 move=f"{intent.get('recipe')} - {intent.get('why')}",
                                 takes='; '.join(f"image {n} (shot {n}) = {label}" for n, (_tid, label, _clip) in enumerate(clips)))
    ids = [t['id'] for t in takes]
    schema = _judge_schema(ids)
    target = folder / f"judge_{stable_hash({'images': [file_hash(i) for i in images], 'prompt': prompt, 'schema': schema})[:16]}.json"
    if force or not target.is_file():
        if reader is None:
            codex_json(images, prompt, schema, target, force=force)
        else:
            reader(images, prompt, schema, target)
    got = read_json(target)
    answer = got['answer']
    if sorted(t['id'] for t in answer['takes']) != sorted(ids) or answer['pick'] not in ids:
        raise StudioError('JUDGE_INCOMPLETE', f'{shot_id}: the verdict does not answer every take ({answer.get("pick")})')
    pending['judge'] = {**answer, 'reader': got.get('reader') or 'codex', 'file': str(target.relative_to(project_dir(path))), 'at': now()}
    write_json(_envelope_path(path, shot_id), env)
    return {'shot_id': shot_id, 'rev': pending['rev'], 'judge': pending['judge'], 'artifacts': [str(i) for i in images] + [str(target)]}


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
                                                   'contract': {'frames': state['frames'], 'projection': state.get('projection', 1),
                                                                **({'hero_frame': env['body']['hero_frame']} if 'hero_frame' in env['body'] else {}),
                                                                **({'concept': env['sheet']['concept']} if env['sheet'].get('concept') else {}),
                                                                **({'frame_pictures': env['sheet']['frame_pictures']} if env['sheet'].get('frame_pictures') else {})}}})
    write_json(_envelope_path(path, shot_id), env)
    reopened = [f"REEL_SHOT_REOPENED: {shot_id} - {env['reopened']['why']}"] if env.get('reopened') else []   # said, the user decides
    return {'shot_id': shot_id, 'status': 'approved', 'version': env['sheet']['version'], 'frames': len(state['frames']), 'warnings': reopened}


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
    if contract.get('concept'):
        from .concept import picked, require as concept_kept
        concept_kept(path, shot['shot_id'])   # the image itself is unchanged
        now_picked = picked(path, shot['shot_id']) or {}
        if now_picked.get('sha256') != contract['concept']['sha256']:
            raise StudioError('DECISION_STALE', f"{shot['shot_id']}: the concept frame changed after the storyboard was approved",
                              recovery='show a new storyboard sheet against the new concept and approve it in the user\'s words')
    if contract.get('frame_pictures'):   # the pictures the user approved the shot against are still its pictures
        from .concept import frames as frame_pictures
        now_pictures = [{'concept_id': p['concept_id'], 'sha256': p['sha256'], 'at': p['frame']['at']} for p in frame_pictures(path, shot['shot_id'])]
        if now_pictures != contract['frame_pictures']:
            raise StudioError('DECISION_STALE', f"{shot['shot_id']}: the frame pictures changed after the storyboard was approved",
                              recovery='show a new storyboard sheet against the new pictures and approve it in the user\'s words')
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
    p.add_argument('--focus', help='JSON (text or a .json file) {fraction: [ids]} the contract keeps in frame'); p.add_argument('--captions', help='JSON {fraction: text}')
    p.add_argument('--hero', type=float, help='fraction of the frame that explains: set beside the concept frame, kept in the contract')
    p.set_defaults(handler=lambda a: propose(a.project, a.shot, [float(x) for x in a.frames.split(',')],
                                             {float(k): v for k, v in json_arg(a.focus).items()} if a.focus else None,
                                             {float(k): v for k, v in json_arg(a.captions).items()} if a.captions else None, a.hero))
    p = commands.add_parser('revise'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--user-words', required=True); p.add_argument('--ops', required=True, help=f'JSON (text or a .json file) list of edits {OPS}; set/add/remove reach any value: {{op, path: "/camera/move/params/span", value|factor|delta}}')
    p.add_argument('--agent-note')
    p.set_defaults(handler=lambda a: revise(a.project, a.shot, a.user_words, a.ops, a.agent_note))
    p = commands.add_parser('approve'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--sheet', required=True); p.add_argument('--user-words', required=True)
    p.set_defaults(handler=lambda a: approve(a.project, a.shot, a.user_words, a.sheet))
    p = commands.add_parser('variants', help='2-4 different takes of a shot built side by side on one sheet')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--variants', required=True, help='JSON (text or a .json file) [{id, label, why, change: {camera|scene|actions|titles|graphics}}]')
    p.add_argument('--frames', default='0,0.5,1'); p.add_argument('--focus'); p.add_argument('--captions')
    p.set_defaults(handler=lambda a: variants(a.project, a.shot, a.variants, [float(x) for x in a.frames.split(',')],
                                              {float(k): v for k, v in json_arg(a.focus).items()} if a.focus else None,
                                              {float(k): v for k, v in json_arg(a.captions).items()} if a.captions else None))
    p = commands.add_parser('pick', help="the picked take becomes the shot (the user's words; the agent's note on a judged sheet in a "
                            'delegated run); a normal sheet follows')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--sheet', required=True)
    p.add_argument('--variant', required=True); p.add_argument('--user-words'); p.add_argument('--agent-note')
    p.add_argument('--repeat-why', help="keep a take that repeats the reel's common motion or the one its watcher sent back: why")
    p.add_argument('--because', action='append', default=[], metavar='CODE=WHY',
                   help='pass an agent floor with its reason: TAKE_OWN_UNBUILT="..." or CAMERA_TOO_FAST="..."')
    p.set_defaults(handler=lambda a: pick(a.project, a.shot, a.sheet, a.variant, a.user_words, a.agent_note, a.repeat_why,
                                          _because(a.because)))
    p = commands.add_parser('takes', help="takes made from the shot's motion (direction.move): its recipe and others, built, as clay "
                            'clips with their measured feel')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--recipes', nargs='+', help='the recipes to make, in order (default: the shot\'s own, then ones its neighbours do not use)')
    p.add_argument('--user-words', help="the user's words: another round past DIRECT_RETRIES")
    p.set_defaults(handler=lambda a: takes(a.project, a.shot, a.recipes, a.user_words))
    p = commands.add_parser('judge', help='a watcher\'s verdict on a takes sheet: each clip beside its neighbours, ranked, one picked with why')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--sheet')
    p.add_argument('--force', action='store_true')
    p.set_defaults(handler=lambda a: judge(a.project, a.shot, a.sheet, force=a.force))
    p = commands.add_parser('show'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: envelope(a.project, a.shot) or {})
    p = commands.add_parser('animatic', help="each take's clay clip and camera dynamics against the user's reference profile")
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--sheet', help='the takes sheet (vNN)')
    p.set_defaults(handler=lambda a: animatic(a.project, a.shot, a.sheet))
    p = commands.add_parser('board', help='every shot\'s latest sheet on one page, to approve shot by shot')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: board(a.project))
    p = commands.add_parser('brief', help='the composition brief: each shot\'s first and last picture, camera, join and plan '
                                          'differences on one page (studio/storyboard_brief.py)')
    p.add_argument('--project', required=True); p.add_argument('--shot', action='append', help='only these shots (repeatable)')
    p.set_defaults(handler=lambda a: __import__('studio.storyboard_brief', fromlist=['brief']).brief(a.project, a.shot))
