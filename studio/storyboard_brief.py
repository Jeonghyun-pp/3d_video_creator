"""The reel's composition brief: what each shot shows on screen, how its camera moves, how it joins the shot before,
and where the build left the shot list's plan - one page the user reads before approving storyboards.

Why (2026-10-11, rocket_landing): the user, after 25 storyboard sheets, a 40 000 px board and long agent reports:
"실제 어떤 화면 구도들로 영상이 이루어질지에 대한 브리핑이 더 상세해야 할것같아". Nothing said in words what a
shot looks like, and s19 - planned "held, the same side view" - had become an arc into one leg without anyone saying
so. Everything here is read from data the build already wrote (camera_move_report, frame_report, the shot list,
the frame pictures, the latest sheet's frames); nothing is rendered.
"""
from __future__ import annotations

import html
import math
from pathlib import Path

from .common import StudioError, read_json, write_json
from .project import load_project, load_shot, project_dir, shot_path

# Screen size of what the shot explains, by its height in frame heights - measured past the frame edge (the camera
# report's `achieved.height_share`), so 2.0 means only half of it shows. Bands chosen so the words match what the
# rocket reel's sheets look like (0.10 a dot over the sea, 0.3 a whole booster with room, 0.7 filling the frame,
# 1.4 a cut body, 3 one leg): the boundaries are reading aids, not checks.
SIZE_CLASSES = ((0.15, '원경'), (0.45, '전체'), (0.9, '꽉 참'), (2.0, '클로즈업'), (math.inf, '익스트림 클로즈업'))
LEVEL_DEG = 5.0          # a view within this of horizontal reads as eye level (frame_targets.LEVEL_DEG)
TURN_DEG = 5.0           # a camera that turns less than this round the subject did not go round it
DIST_RATIO = 1.15        # nearer / farther than this ratio is a push in / pull back (patterns.REPEAT height_ratio)
TRAVEL_SHARE = 0.05      # the subject crossing less than this share of the frame stays put on screen
LAYOUT_CODES = ('FRAME_TARGET_MISS', 'FRAMING_REPEATED', 'FRAME_PATH_BLOCKED', 'DETAIL_NOT_SHOWN', 'FOCUS_FRAMING',
                'FRAME_SUBJECT_SMALL', 'FRAME_UNSOLVED', 'SCALE_FLOW_FLAT')   # composition warnings the brief carries
STEP_WORDS = {'closer': '가까워짐', 'wider': '멀어짐', 'same': '같은 크기'}
PACING_WORDS = {'subject': '속도는 대상에 맞춤', 'ease_in_out': '부드럽게 출발해 멈춤', 'linear': '일정한 속도',
                'burst_settle': '빠르게 치고 들어가 멈춤'}


def size_class(height_share):
    if height_share is None:
        return None
    return next(name for top, name in SIZE_CLASSES if height_share < top)


def place_words(centre):
    """Where on screen, in a 3 x 3 grid: '왼쪽 위' ... '가운데'."""
    if not centre:
        return None
    x, y = (min(1.0, max(0.0, v)) for v in centre)
    col = '왼쪽' if x < 1 / 3 else '오른쪽' if x > 2 / 3 else '가운데'
    row = '위' if y < 1 / 3 else '아래' if y > 2 / 3 else ''
    return f'{col} {row}'.strip() if (col, row) != ('가운데', '') else '가운데'


def angle_words(elevation_deg):
    """+ looks down (camera above), - looks up."""
    if elevation_deg is None:
        return None
    if abs(elevation_deg) < LEVEL_DEG:
        return f'눈높이 ({elevation_deg:+.0f}°)'
    return f"{'내려다봄' if elevation_deg > 0 else '올려다봄'} {abs(elevation_deg):.0f}°"


def cut_edges(box):
    if not box:
        return []
    names = (('left', box[0] < 0), ('top', box[1] < 0), ('right', box[2] > 1), ('bottom', box[3] > 1))
    return [{'left': '왼쪽', 'top': '위', 'right': '오른쪽', 'bottom': '아래'}[n] for n, out in names if out]


def _dist(a, b):
    return math.dist(a, b) if a and b else None


def move_words(start, end, move_report):
    """The camera's move between the first and the last moment, in plain words."""
    if not start or not end:
        return None
    parts = []
    path = (move_report or {}).get('path')
    if path:
        parts.append('둘레로 돌며' if path == 'arc' else '곧장')
    elif ((move_report or {}).get('move') or {}).get('type'):
        parts.append(f"{move_report['move']['type']} 움직임")
    da, db = _dist(start.get('position'), start.get('aim')), _dist(end.get('position'), end.get('aim'))
    if da and db:
        ratio = da / db
        parts.append(f'다가감 ({da:.0f}→{db:.0f} m)' if ratio > DIST_RATIO else f'물러남 ({da:.0f}→{db:.0f} m)' if ratio < 1 / DIST_RATIO
                     else f'거리 유지 (약 {db:.0f} m)')
    if start.get('view') and end.get('view') and None not in (start['view'][1], end['view'][1]):
        turn = ((end['view'][1] - start['view'][1] + 180) % 360) - 180
        if abs(turn) >= TURN_DEG:
            parts.append(f"{'오른쪽' if turn > 0 else '왼쪽'}으로 {abs(turn):.0f}° 돎")
        rise = end['view'][0] - start['view'][0]
        if abs(rise) >= TURN_DEG:
            parts.append(f"카메라가 {'올라감' if rise > 0 else '내려감'} ({angle_words(start['view'][0])} → {angle_words(end['view'][0])})")
    follow = (move_report or {}).get('follow')
    if follow is not None:
        parts.append(f'대상 이동의 {round(follow * 100)}%를 따라감' if follow > 0 else '제자리(대상이 지나감)')
    pacing = (move_report or {}).get('pacing')
    pacing = pacing or (((move_report or {}).get('move') or {}).get('timing') or {}).get('profile')
    if pacing:
        parts.append(PACING_WORDS.get(pacing, f'속도 {pacing}'))
    return ', '.join(parts)


def _moment(row):
    """A camera report frame row -> what the brief says about that moment."""
    achieved = row.get('achieved') or {}
    view = row.get('view')
    h = achieved.get('height_share')
    return {'frame': row.get('frame'), 'at': row.get('at'), 'height_share': h, 'size': size_class(h),
            'place': place_words(achieved.get('centre')), 'cut': cut_edges(achieved.get('box')),
            'angle': angle_words(view[0] if view else None), 'view': view if view and view[0] is not None else None, 'lens_mm': row.get('lens_mm'),
            'position': row.get('position'), 'aim': row.get('aim'), 'must_see_px': row.get('must_see_px') or {},
            'offset_from_picture': row.get('offset_from_picture')}


def _rig_row(sample, aim=None):
    """A camera rig sample in the camera report's frame-row shape (pitch: + looks up; view elevation: + looks down)."""
    box = sample.get('target_box') or sample.get('subject_box')
    cam = sample.get('camera')
    azimuth = math.degrees(math.atan2(cam[1] - aim[1], cam[0] - aim[0])) if cam and aim else None
    return {'frame': sample.get('frame'), 'lens_mm': sample.get('lens_mm'), 'position': cam, 'aim': aim,
            'view': [-sample['pitch_deg'] if sample.get('pitch_deg') is not None else None, azimuth],
            'achieved': {'height_share': round(box[3] - box[1], 4), 'centre': [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2], 'box': box} if box else {}}


def _move_ids(shot):
    params = (((shot.get('camera') or {}).get('move') or {}).get('params') or {})
    return list(params.get('parts') or ([params['target']] if params.get('target') else []))


def _plan(path, shot_id):
    file = project_dir(path) / 'decisions' / 'shotlist.json'
    if not file.is_file():
        return {}
    body = read_json(file).get('body') or {}
    return next((s for s in body.get('shots', []) if s.get('shot_id') == shot_id), {})


def _images(path, shot, version_moments):
    """(first picture, first built frame, last picture, last built frame) as project-relative paths, None when absent.
    Collected reference photos never appear: pictures come from concept frames, frames from the shot's own sheet."""
    from .concept import frames as frame_pictures
    from .storyboard import envelope
    root = project_dir(path)
    pictures = frame_pictures(root, shot['shot_id'])
    first_pic = pictures[0]['path'] if pictures else None
    last_pic = pictures[-1]['path'] if len(pictures) > 1 else None
    sheet = ((envelope(root, shot['shot_id']) or {}).get('sheet') or {}).get('path')
    folder = (root / sheet).parent if sheet else None

    def built(frame):
        if folder is None or frame is None:
            return None
        file = folder / f'frame_{int(frame):06d}.png'
        return str(file.relative_to(root)) if file.is_file() else None
    first, last = (version_moments[0], version_moments[-1]) if version_moments else (None, None)
    return [first_pic, built(first['frame'] if first else None), last_pic, built(last['frame'] if last else None)]


def shot_card(path, shot_id, timing=None):
    """Everything the brief says about one shot (a dict; no rendering)."""
    root = project_dir(path)
    shot = load_shot(root, shot_id)
    plan = _plan(root, shot_id)
    fps = (timing or {}).get('fps') or 30
    start = (timing or {}).get('start_frame') or 0
    count = shot['duration_frames']
    card = {'shot_id': shot_id, 'start_s': round(start / fps, 2), 'end_s': round((start + count) / fps, 2),
            'length_s': round(count / fps, 2), 'narration': (shot.get('narration') or {}).get('text'),
            'purpose': plan.get('purpose') or shot.get('goal'), 'role': (shot.get('screen') or {}).get('role') or plan.get('role'),
            'subject': (shot.get('screen') or {}).get('subject') or _move_ids(shot),
            'details': [{'id': d['id'], 'why': d.get('why'), 'min_px': d.get('min_px')} for d in (shot.get('screen') or {}).get('details', [])],
            'plan': plan.get('direction') or {}, 'version': shot.get('scene_version'), 'built': False,
            'first': None, 'last': None, 'camera_move': None, 'screen_travel': None, 'issues': [], 'departs': [], 'images': [None] * 4}
    version = shot_path(root, shot_id).parent / 'versions' / str(shot.get('scene_version'))
    move = read_json(version / 'camera_move_report.json') if (version / 'camera_move_report.json').is_file() else None
    probe = read_json(version / 'frame_report.json') if (version / 'frame_report.json').is_file() else None
    rows = sorted((move or {}).get('frames') or [], key=lambda r: r.get('frame', 0))
    if not rows:   # any other move: the rig's first and last samples (camera, pitch, the target's box)
        rig = read_json(version / 'camera_rig_report.json') if (version / 'camera_rig_report.json').is_file() else {}
        aim = ((move or {}).get('focus') or {}).get('aim')
        rows = [_rig_row(sample, aim) for sample in (rig.get('samples') or [])[:1] + (rig.get('samples') or [])[-1:]]
    moments = [_moment(r) for r in rows]
    if moments:
        card.update({'built': True, 'first': moments[0], 'last': moments[-1], 'camera_move': move_words(moments[0], moments[-1], move)})
    if probe:
        from .frame_targets import _box_at
        ids = card['subject']
        probed = sorted(probe.get('frames', []), key=lambda f: f['frame'])
        boxes = [b for b in (_box_at(f, ids) for f in (probed[0], probed[-1])) if b] if probed else []
        if len(boxes) == 2:
            (a, b) = [((x0 + x1) / 2, (y0 + y1) / 2) for x0, y0, x1, y1 in boxes]
            travel = math.dist(a, b)
            card['screen_travel'] = round(travel, 3)
            card['screen_travel_words'] = '화면 안에서 거의 그대로' if travel < TRAVEL_SHARE else \
                f"화면을 {'오른쪽' if b[0] > a[0] else '왼쪽'}{'·아래' if b[1] - a[1] > TRAVEL_SHARE else '·위' if a[1] - b[1] > TRAVEL_SHARE else ''}로 가로지름 ({round(travel * 100)}%)"
        card['built'] = True
    for source in (move or {}, probe or {}):
        for warning in source.get('warnings') or []:
            text = warning if isinstance(warning, str) else (warning.get('message') or str(warning))
            if any(text.startswith(code) for code in LAYOUT_CODES) and text not in card['issues']:
                card['issues'].append(text)
    from .frame_targets import _store, moments as picture_moments
    rig = read_json(version / 'camera_rig_report.json') if (version / 'camera_rig_report.json').is_file() else {}
    card['pictures'] = [{'concept_id': m['concept_id'], 'at': m['at'], 'problems': m['problems'], 'departs': len(m['departs']),
                         'abandoned': m['abandoned']} for m in picture_moments(root, shot, probe or {}, rig, move or {})] if probe else []
    for m in card['pictures']:
        if m['abandoned']:
            card['issues'].insert(0, f"FRAME_PICTURE_ABANDONED: picture {m['concept_id']} kept different in {m['departs']} ways "
                                     "without the user's words - the build no longer follows it")
    for concept_id, row in sorted((_store(root, shot_id).get('pictures') or {}).items()):
        card['departs'] += [{'picture': concept_id, 'item': d['item'], 'why': d['why']} for d in row.get('departs') or []]
    from .storyboard import envelope
    env = envelope(root, shot_id) or {}
    card['status'] = env.get('status') or 'no sheet'
    card['approval'] = (env.get('approval') or {}).get('user_words')
    card['images'] = _images(root, shot, moments)
    return card


def join(previous, card):
    """How a cut from `previous` (its last moment) into `card` (its first) reads, and whether the shot list's
    scale_step for this shot holds (BRIEF_PLAN_DIFFERS)."""
    from .grammar_core import scale_step
    if not previous or not previous.get('last') or not card.get('first'):
        return {'words': None, 'step': None, 'problems': []}
    a, b = previous['last'], card['first']
    same_subject = sorted(previous.get('subject') or []) == sorted(card.get('subject') or [])
    # sizes compare only on one subject: a leg at 0.3 after a whole booster at 1.5 is not "wider"
    step = (scale_step(a.get('height_share'), b.get('height_share')) or {}).get('step') \
        if same_subject and a.get('height_share') and b.get('height_share') else None
    words = [f"{a.get('size') or '?'} → {b.get('size') or '?'}" + (f" ({STEP_WORDS.get(step, step)})" if step else '')
             + ('' if same_subject else f" (대상이 바뀜: {subject_label(previous.get('subject'))} → {subject_label(card.get('subject'))})")]
    if a.get('view') and b.get('view'):
        words.append(f"각도 {a['view'][0]:+.0f}° → {b['view'][0]:+.0f}°")
    if a.get('place') and b.get('place') and a['place'] != b['place']:
        words.append(f"위치 {a['place']} → {b['place']}")
    if card['plan'].get('transition'):
        words.append(f"연결 계획: {card['plan']['transition']}")
    planned = (card['plan'].get('scale_step') or '').strip().lower()
    problems = []
    if planned in STEP_WORDS and step and planned != step:
        problems.append(f"BRIEF_PLAN_DIFFERS: {card['shot_id']} was planned {planned} from {previous['shot_id']} and is {step} "
                        f"({a.get('height_share')} -> {b.get('height_share')} frame heights) - change the shot list or the shot")
    return {'words': ', '.join(words), 'step': step, 'problems': problems}


def cards(path, shots=None):
    root = project_dir(path)
    project = load_project(root)
    fps = project['output']['fps']
    out, previous = [], None
    for entry in project['shots']:
        if shots and entry['shot_id'] not in shots:
            previous = None
            continue
        card = shot_card(root, entry['shot_id'], {'fps': fps, 'start_frame': entry.get('start_frame')})
        card['join'] = join(previous, card)
        card['issues'] = card['join']['problems'] + card['issues']
        out.append(card)
        previous = card
    return out


# ---- the page ---------------------------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1f23;--soft:#5d636e;--line:#e3e4e8;--warn:#b4401c;--ok:#2f7a3d;--chip:#eef0f4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121417;--card:#1b1e23;--ink:#e9eaee;--soft:#a3a8b3;--line:#2c3038;--warn:#ff8a65;--ok:#7ccf8a;--chip:#262a31;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121417;--card:#1b1e23;--ink:#e9eaee;--soft:#a3a8b3;--line:#2c3038;--warn:#ff8a65;--ok:#7ccf8a;--chip:#262a31;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{max-width:1100px;margin:0 auto;padding-block:24px 64px;padding-inline:16px}.card,.strip figure,dd{min-width:0}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px}
.sub{color:var(--soft);margin:0 0 20px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--soft);font-weight:600}.wrap{overflow-x:auto}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}
.head{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline}.head b{font-size:18px}.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--soft)}
.narr{font-size:16px;margin:8px 0 12px}.strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.strip figure{margin:0}.strip img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:6px;background:var(--chip)}
.strip figcaption{font-size:11px;color:var(--soft);margin-top:2px}.miss{display:flex;align-items:center;justify-content:center;aspect-ratio:9/16;border-radius:6px;background:var(--chip);color:var(--soft);font-size:12px}
dl{display:grid;grid-template-columns:140px 1fr;gap:4px 12px;margin:14px 0 0}dt{color:var(--soft)}dd{margin:0}.warn{color:var(--warn)}.ok{color:var(--ok)}
@media (max-width:640px){.strip{grid-template-columns:repeat(2,1fr)}dl{grid-template-columns:1fr}dt{margin-top:6px}}
"""


def _e(value):
    return html.escape(str(value)) if value not in (None, '') else '—'


def subject_label(ids):
    """What the size is measured on, short: 'booster' or 'leg_0·leg_foot_0'."""
    return '·'.join(i.rsplit('/', 1)[-1] for i in ids) if ids else None


def _moment_line(m, ids=None):
    if not m:
        return '아직 빌드 없음'
    label = subject_label(ids)
    bits = [f"{m['size']} ({m['height_share']:.2f}{', ' + label if label else ''})" if m.get('height_share') is not None else None, m.get('angle'),
            f"{m['lens_mm']:.0f} mm" if m.get('lens_mm') else None, f"화면 {m['place']}" if m.get('place') else None,
            f"잘림: {'·'.join(m['cut'])}" if m.get('cut') else None]
    return ', '.join(b for b in bits if b)


def match_words(card):
    """How the built moments follow the pictures, short: match, n differences, or the picture dropped (HTML)."""
    pictures = card.get('pictures') or []
    if not pictures:
        return '—'
    if any(m['abandoned'] for m in pictures):
        return "<span class='warn'>그림 버림</span>"
    n = sum(len(m['problems']) for m in pictures)
    return "<span class='ok'>일치</span>" if n == 0 else f"<span class='warn'>차이 {n}</span>"


def page(cards_, title):
    rows = []
    for c in cards_:
        size = f"{(c['first'] or {}).get('size') or '—'} → {(c['last'] or {}).get('size') or '—'}"
        angle = f"{(c['first'] or {}).get('angle') or '—'} → {(c['last'] or {}).get('angle') or '—'}"
        flag = ' <span class="warn">●</span>' if c['issues'] else ''
        rows.append(f"<tr><td><a href='#{c['shot_id']}'>{c['shot_id']}</a>{flag}</td><td>{c['start_s']:.1f}–{c['end_s']:.1f}s</td>"
                    f"<td>{match_words(c)}</td>"
                    f"<td>{_e(subject_label(c['subject']))}</td><td>{_e(size)}</td><td>{_e(angle)}</td><td>{_e(c.get('camera_move'))}</td><td>{_e(c['narration'])}</td></tr>")
    body = [f"<h1>{_e(title)}</h1><p class='sub'>샷 {len(cards_)}개 · 첫 장면과 끝 장면, 카메라, 앞 샷과의 연결, 계획과 다른 점. "
            "크기는 설명 대상의 높이를 화면 높이로 나눈 값(1보다 크면 화면 밖으로 잘림).</p>",
            "<h2>전체 흐름</h2><div class='wrap'><table><tr><th>샷</th><th>시간</th><th>그림 일치</th><th>대상</th><th>크기 첫→끝</th><th>높이각 첫→끝</th><th>카메라</th><th>내레이션</th></tr>",
            *rows, '</table></div>', '<h2>샷별 카드</h2>']
    for c in cards_:
        labels = ('첫 그림', '첫 장면(실제)', '끝 그림', '끝 장면(실제)')
        figs = ''.join(f"<figure><img src='{html.escape(src)}' alt='{c['shot_id']} {label}' loading='lazy'><figcaption>{label}</figcaption></figure>"
                       if src else f"<figure><div class='miss'>{label} 없음</div><figcaption>{label}</figcaption></figure>"
                       for src, label in zip(c.get('image_urls') or [None] * 4, labels))
        plan = c['plan']
        plan_words = '; '.join(f"{k}: {v}" for k, v in (('카메라', plan.get('camera')), ('동작', plan.get('action')), ('크기', plan.get('scale_step'))) if v)
        details = '; '.join(f"{d['id']} ({d.get('why') or ''})" for d in c['details'])
        departs = ''.join(f"<div>{_e(d['picture'])} {_e(d['item'])}: {_e(d['why'])}</div>" for d in c['departs'])
        issues = ''.join(f"<div class='warn'>{_e(i)}</div>" for i in c['issues'])
        status = f"<span class='ok'>승인: {_e(c['approval'])}</span>" if c.get('approval') else _e(c.get('status'))
        body.append(
            f"<section class='card' id='{c['shot_id']}'><div class='head'><b>{c['shot_id']}</b><span>{c['start_s']:.1f}–{c['end_s']:.1f}s ({c['length_s']:.1f}s)</span>"
            f"<span class='chip'>{_e(c['role'])}</span><span class='chip'>{_e(c['version'])}</span><span class='chip'>{status}</span></div>"
            f"<div class='narr'>“{_e(c['narration'])}”</div><div class='strip'>{figs}</div><dl>"
            f"<dt>보여 줄 것</dt><dd>{_e(c['purpose'])}{('<br>' + _e(details)) if details else ''}</dd>"
            f"<dt>첫 장면</dt><dd>{_e(_moment_line(c['first'], c['subject']))}</dd><dt>끝 장면</dt><dd>{_e(_moment_line(c['last'], c['subject']))}</dd>"
            f"<dt>카메라 움직임</dt><dd>{_e(c.get('camera_move'))}</dd><dt>대상 움직임</dt><dd>{_e(c.get('screen_travel_words'))}"
            f"{('<br>계획: ' + _e(plan.get('action'))) if plan.get('action') else ''}</dd>"
            f"<dt>앞 샷과 연결</dt><dd>{_e(c['join'].get('words'))}</dd><dt>샷리스트 계획</dt><dd>{_e(plan_words)}</dd>"
            f"<dt>그림과 비교</dt><dd>{match_words(c)}{''.join('<div>' + _e(q) + '</div>' for m in c.get('pictures') or [] for q in m['problems'])}</dd>"
            f"<dt>그림과 다르게 간 점</dt><dd>{departs or '—'}</dd><dt>남은 문제</dt><dd>{issues or '<span class=ok>없음</span>'}</dd></dl></section>")
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title><style>{CSS}</style></head><body><main>{''.join(body)}</main></body></html>")


def brief(project, shots=None):
    """Write decisions/sheets/brief/bNN/{brief.html, brief.md, brief.json, img/} and return their paths and the
    BRIEF_PLAN_DIFFERS lines."""
    from PIL import Image
    root = project_dir(project)
    made = cards(root, shots)
    if not made:
        raise StudioError('INPUT_INVALID', 'no shots to brief')
    base = root / 'decisions' / 'sheets' / 'brief'
    rev = f"b{len(list(base.glob('b*'))) + 1:02d}"
    out = base / rev
    (out / 'img').mkdir(parents=True, exist_ok=True)
    for c in made:
        urls = []
        for i, rel in enumerate(c['images']):
            if not rel or 'references/' in rel:   # pictures and our own frames only; collected photos stay out
                urls.append(None)
                continue
            name = f"img/{c['shot_id']}_{i}.jpg"
            with Image.open(root / rel) as src:
                thumb = src.convert('RGB')
                thumb.thumbnail((360, 640))
                thumb.save(out / name, quality=82)
            urls.append(name)
        c['image_urls'] = urls
    title = f"{load_project(root).get('project_id') or root.name} 구도 브리핑"
    (out / 'brief.html').write_text(page(made, title), encoding='utf-8')
    write_json(out / 'brief.json', {'schema_version': 1, 'cards': made})
    differs = [p for c in made for p in c['join']['problems']]
    lines = [f'# {title}', '', f'Open brief.html ({len(made)} shots).', ''] + [
        f"- {c['shot_id']} {c['start_s']:.1f}-{c['end_s']:.1f}s: {_moment_line(c['first'], c['subject'])} -> {_moment_line(c['last'], c['subject'])}; {c.get('camera_move') or ''}"
        for c in made] + ([''] + differs if differs else [])
    (out / 'brief.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'rev': rev, 'html': str(out / 'brief.html'), 'brief': str(out / 'brief.md'), 'warnings': differs,
            'artifacts': [str(out / 'brief.html'), str(out / 'brief.md'), str(out / 'brief.json')]}
