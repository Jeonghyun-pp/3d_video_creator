"""Builds and cuts the clay (Workbench, no look render): a dive through three nested scales - a box on a yard, the plate
on the box, the grid on the plate - each a scene of its own, each cut pushing into the next and handing over
scale_through (studio/blender_ops/scale_through_core.py).

Checks: the shot list's journey reads its scales (a plain cut from the box to the grid, 80x apart, is JOURNEY_SCALE_JUMP; the three
dives are not); each received cut starts where the cut before left its carried thing (scale_join.json: place within 5 %
of the frame height, size, zoom rate - no SCALE_JOIN_OFF in the brief's join); the clay cuts each dive on its
matched frame and keeps every cut's length; measured on the clay itself (picture motion, grammar_probe), the camera's
pace across each join stays under the followable floor (no CAMERA_JERK at the joins).
With SCALE_THROUGH_SHEET=<png> it saves a contact sheet of the joins (frames either side of each cut) there.
Run: .venv/bin/python tests/studio/scale_through_smoke.py
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import journey, reel, storyboard_brief as sb  # noqa: E402
from studio.blender import build_shot  # noqa: E402
from studio.blender_ops import scale_through_core as st  # noqa: E402
from studio.camera_comfort import goal  # noqa: E402
from studio.camera_follow import follow, picture_pairs  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402
from studio.project import init_project, shot_path  # noqa: E402
from studio.recipe_moves import camera_with, recipe_move  # noqa: E402
from tests.decisions_fixture import settle  # noqa: E402

FPS, FRAMES = 30, 90   # three 3 s cuts

# One builder per thing, shared by every scene that holds it at its true size: the plate the box scene ends on is the plate
# the plate scene starts on; each deeper scene adds the detail the one above is too coarse to hold, and keeps what the
# cut before shows round the carried thing at the join (a first run without it opened onto empty background at the top
# of the frame at the join).
COMMON = '''import bpy, bmesh, random
def blocks(name, rows, sid=None):
    """One mesh object from boxes [(centre, size)]."""
    mesh = bpy.data.meshes.new(name); bm = bmesh.new()
    for (cx, cy, cz), (sx, sy, sz) in rows:
        r = bmesh.ops.create_cube(bm, size=1.0)
        for v in r['verts']:
            v.co.x = cx + v.co.x * sx; v.co.y = cy + v.co.y * sy; v.co.z = cz + v.co.z * sz
    bm.to_mesh(mesh); bm.free()
    o = bpy.data.objects.new(name, mesh); bpy.context.scene.collection.objects.link(o); o['studio_id'] = sid or name
    return o
TOP = 2.1                                  # the box's top
def box_top(span):                         # the box's top face and its studs (0.15 m), the plate's place left clear
    rows = [((0, 0, TOP - 1.0), (2.0, 2.0, 2.0))]
    for i in range(-6, 7):
        for j in range(-6, 7):
            x, y = i * 0.15, j * 0.15
            if max(abs(x), abs(y)) < 0.25 or max(abs(x), abs(y)) > span:
                continue
            rows.append(((x, y, TOP + 0.02), (0.06, 0.06, 0.04 + 0.02 * ((i * 7 + j * 3) % 3))))
    return rows
def plate():                               # 0.25 m plate with 2 cm studs; its middle (the grid's place) left plain
    rows = [((0, 0, TOP + 0.005), (0.25, 0.25, 0.01))]
    for i in range(-5, 6):
        for j in range(-5, 6):
            x, y = i * 0.022, j * 0.022
            if max(abs(x), abs(y)) < 0.02:
                continue
            rows.append(((x, y, TOP + 0.0115), (0.008, 0.008, 0.003 + 0.002 * ((i + 2 * j) % 2))))
    return blocks('plate', rows)
G = TOP + 0.01                             # the grid lies on the plate
def grid():                                # a 2.5 cm grid: ten 2.5 mm cells, 0.5 mm walls
    rows = [((0, 0, G + 0.0002), (0.025, 0.025, 0.0004))]
    for k in range(11):
        u = -0.0125 + k * 0.0025
        rows += [((u, 0, G + 0.0007), (0.0005, 0.025, 0.0006)), ((0, u, G + 0.0007), (0.025, 0.0005, 0.0006))]
    return blocks('grid', rows)
def yard():                                # 40 m of ground and scattered blocks round the box
    blocks('yard', [((0, 0, 0.05), (40, 40, 0.1))] + [((random.Random(i).uniform(-18, 18), random.Random(i + 99).uniform(-18, 18), 0.4),
           (0.8, 0.8, 0.6)) for i in range(120)])
def light():
    bpy.ops.object.light_add(type='SUN', location=(0, 0, 30)); bpy.context.object.rotation_euler = (0.5, 0.2, 0.3)
'''
SCENES = {
    'box': COMMON + '''
yard()
blocks('box', box_top(1.0))
plate()
light()
''',
    'plate': COMMON + '''
yard()
blocks('box', box_top(1.0))
plate()
grid()
light()
''',
    'grid': COMMON + '''
yard()
blocks('box', box_top(1.0))
plate()
grid()
blocks('cell', [((0.00125, 0.00125, G + 0.0009), (0.0018, 0.0018, 0.0008))] +
       [((0.00125 + dx, 0.00125 + dy, G + 0.00135), (0.0004, 0.0004, 0.0002)) for dx in (-0.0004, 0.0004) for dy in (-0.0004, 0.0004)])
light()
''',
}
# the reel's journey as the shot list says it (each place a world element built at its true size)
WORLD = {'elements': [{'id': i, 'function': 'subject', 'owner': {'shape': 'blender', 'look': 'blender'},
                       'blockout': {'kind': 'box', 'size_m': s}} for i, s in (('yard', [40, 40, 0.1]), ('box', [2, 2, 2]),
                       ('plate', [0.25, 0.25, 0.01]), ('grid', [0.025, 0.025, 0.001]), ('cell', [0.0025, 0.0025, 0.001]))]}
PLACES = [{'place': 'box', 'scale': 'object', 'why': 'the thing as it stands'},
          {'place': 'plate', 'scale': 'inside', 'why': 'what sits on it'},
          {'place': 'grid', 'scale': 'inside', 'why': 'what the plate is made of'},
          {'place': 'cell', 'scale': 'inside', 'why': 'one cell of it'}]
CUTS = [   # shot, journey part (place numbers), the places its scene passes, its target, dives on?, settle_at
    ('a', 0, 1, ['yard', 'box', 'plate'], 'plate', True, 1.0),
    ('b', 1, 2, ['plate', 'grid'], 'grid', True, 1.0),
    ('c', 2, 3, ['grid', 'cell'], 'cell', False, 0.7)]


def planned(sid, frm, to, scene_places, target, dives, settle_at):
    row = {'shot_id': sid, 'frame': {'focus': [target], 'size': 'close'},
           'show': [{'at': 'start', 'parts': [target], 'how': 'exterior', 'why': 'what the push goes into'}],
           'direction': {'journey': {'from': frm, 'to': to, 'role': 'travel', 'why': 'down into the next scale',
                                     'passes': [{'place': p, 'scale': 'inside'} for p in scene_places]},
                         'move': {'recipe': 'fly_in', 'reveals': f'the {target}', 'key_at': 0.9, 'settle_at': settle_at,
                                  'why': f'push into the {target} and on through it',
                                  'settings': {'height_share': 0.45, 'azimuth_deg': 20, 'elevation_deg': 60, 'lens_mm': 24}}}}
    if dives:
        row['handover'] = {'kind': 'scale_through', 'why': f'the {target} is the next scale: the push goes on inside it'}
    return row


def frame_png(video, index, out):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(video), '-vf', f'select=eq(n\\,{index}),scale=270:-2', '-vframes', '1', str(out)],
                   check=True)
    return out


def sheet(video, joins, out):
    """Each join as a row: frames -6, -3, -1 | +0, +3, +6, +9, +14 around the cut."""
    from PIL import Image, ImageDraw
    offsets = (-6, -3, -1, 0, 3, 6, 9, 14)
    with tempfile.TemporaryDirectory() as tmp:
        rows = []
        for j in joins:
            tiles = [Image.open(frame_png(video, j + o, Path(tmp) / f'{j}_{o}.png')).convert('RGB') for o in offsets]
            rows.append(tiles)
        w, h = rows[0][0].size
        board = Image.new('RGB', (w * len(offsets), (h + 24) * len(rows)), 'white')
        draw = ImageDraw.Draw(board)
        for r, tiles in enumerate(rows):
            for c, tile in enumerate(tiles):
                board.paste(tile, (c * w, r * (h + 24) + 24))
                draw.text((c * w + 6, r * (h + 24) + 6), f'cut {r + 1} {offsets[c]:+d}', fill='black' if offsets[c] < 0 else 'blue')
        board.save(out)
    return out


checks, numbers = [], {}
body = {'journey': PLACES, 'shots': [planned(*c) for c in CUTS]}
assert journey.lines(body, WORLD)[0] == [], journey.lines(body, WORLD)[0]
plain = {'journey': PLACES, 'shots': [planned('a', 0, 0, [], 'box', False, 0.7), planned('c', 2, 3, [], 'cell', False, 0.7)]}
assert any(e.startswith('JOURNEY_SCALE_JUMP') for e in journey.lines(plain, WORLD)[0]), journey.lines(plain, WORLD)   # box to grid: 80x
checks.append('journey_scale_is_read')

limit = goal('followable')['limits']['jerk']
with tempfile.TemporaryDirectory(prefix='scale-through-smoke-') as root:
    p = Path(init_project('scale_through', {'request': 'scale-through smoke', 'shots': [{'shot_id': c[0], 'frame_count': FRAMES} for c in CUTS]},
                          root)['project_path'])
    settle(p)
    for cut in CUTS:   # in order: each received cut starts from the built end of the one before
        sid, target = cut[0], cut[4]
        script = p / f'author_{sid}.py'
        script.write_text(SCENES[{'a': 'box', 'b': 'plate', 'c': 'grid'}[sid]])
        plan = planned(*cut)
        made = recipe_move(plan)
        shot = read_json(shot_path(p, sid))
        shot.update({k: plan[k] for k in ('frame', 'show', 'direction') if k in plan})
        if plan.get('handover'):
            shot['handover'] = plan['handover']
        shot['camera'] = camera_with(shot.get('camera'), made['move'])
        write_json(shot_path(p, sid), shot)
        build_shot(p, sid, script)
    versions = {c[0]: p / 'shots' / c[0] / 'versions' / read_json(shot_path(p, c[0]))['scene_version'] for c in CUTS}
    reports = {s: read_json(v / 'camera_move_report.json') for s, v in versions.items()}
    numbers['timing'] = {s: {k: r['scale_through'].get(k) for k in ('start_speed', 'cruise', 'span_ln', 'hands_over_rate')}
                         for s, r in reports.items()}
    assert reports['a']['scale_through']['out'] and not reports['a']['scale_through']['in']
    assert reports['b']['scale_through']['in']['id'] == 'plate' and reports['c']['scale_through']['in']['id'] == 'grid'
    checks.append('dive_ends_are_shaped')

    joined = {}
    for a, b in (('a', 'b'), ('b', 'c')):
        tail, head = read_json(versions[a] / 'scale_join.json'), read_json(versions[b] / 'scale_join.json')
        got = st.join(tail['tail']['samples'], head['head']['samples'], FPS, head['aspect'])
        joined[f'{a}->{b}'] = {k: got[k] for k in ('position_off', 'size_off', 'speed_ratio', 'problems')} | {
            'rate_before': got['a']['rate'], 'rate_after': got['b']['rate'], 'share_before': got['a']['share'], 'share_after': got['b']['share']}
        assert got['position_off'] <= 0.05 and not got['problems'], joined
        rel = lambda v: str(v.relative_to(p))   # noqa: E731
        brief = sb.join({'shot_id': a, 'last': {}, 'subject': [a], 'handover': {'kind': 'scale_through', 'why': 'w'},
                         'scale_join': rel(versions[a] / 'scale_join.json')},
                        {'shot_id': b, 'first': {}, 'subject': [b], 'plan': {}, 'scale_join': rel(versions[b] / 'scale_join.json')}, p)
        assert not [x for x in brief['problems'] if x.startswith('SCALE_JOIN_OFF')], brief['problems']
    numbers['joins'] = joined
    checks.append('carried_thing_holds_place_size_and_pace')

    made = reel.clay(p)
    clay = read_json(p / 'review/clay/clay.json')
    assert all(r['seconds'] == r['planned_s'] for r in clay['shots']), clay['shots']
    checks.append('clay_cuts_each_dive_on_its_matched_frame')

    from studio.screen_grammar import motion_of
    video = Path(made['reel'])
    (p / 'review/clay/motion').mkdir(parents=True, exist_ok=True)
    raw_pairs = motion_of(video, [0.0, 0.0, 1.0, 1.0], p / 'review/clay/motion')
    pairs = picture_pairs(raw_pairs)
    cuts = [FRAMES, 2 * FRAMES]
    span = round(0.75 * FPS)
    numbers['clay_join_jerk'] = {}
    for n, cut in enumerate(cuts):
        measured = follow(pairs[cut - span:cut + span], FPS)
        numbers['clay_join_jerk'][f'join{n + 1}'] = {'jerk': measured['jerk'], 'at_s': round((cut - span) / FPS + measured['jerk_at_s'], 2)}
        assert measured['jerk'] < limit, numbers
    lost = sum(1 for q in raw_pairs[cuts[0] - 3:cuts[0] + 12] + raw_pairs[cuts[1] - 3:cuts[1] + 12] if not q.get('ok'))
    numbers['probe_pairs_lost_near_joins'] = lost
    checks.append('no_camera_jerk_at_the_joins')
    numbers['per_cut_jerk'] = {s: (read_json(v / 'camera_dynamics.json').get('follow') or {}).get('jerk') for s, v in versions.items()
                               if (v / 'camera_dynamics.json').is_file()}
    if os.environ.get('SCALE_THROUGH_SHEET'):
        out = Path(os.environ['SCALE_THROUGH_SHEET'])
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet(video, cuts, out)
        keep = out.parent / 'V3_clay.mp4'
        keep.write_bytes(video.read_bytes())
        for s, v in versions.items():   # each cut's own clip, uncut: to see the join another way
            (out.parent / f'V3_{s}.mp4').write_bytes((v / 'animatic.mp4').read_bytes())
        numbers['sheet'] = str(out)

print(json.dumps({'ok': True, 'checks': checks, 'numbers': numbers, 'jerk_limit': limit}))
