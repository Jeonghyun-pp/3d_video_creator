"""Pattern library: how a measured study set of clips uses the frame, as distributions with a strength.

Input: grammar.json files (studio/screen_grammar.py) and a local manifest giving each clip its sample group (clips made
the same way). Output:
  library/patterns/<name>.json  the shareable part: per section (all shots, each role) and item - quartiles, support
                                (shots, groups) and strength; no sources, no per-group values
  <out>/patterns_full.json      (local) the same with per-group values and clip hashes, for audits
  <out>/exemplars.json          (local) per section, shots near its typical values from different groups: clip, shot,
                                strip and the labeller's note - the pictures Astra designs from
                                (references/screen_grammar.md section 6)
Nothing here says what a pattern is for: a section is a role or 'all', never a use.

Strength (initial rule, named below): an item is 'strong' when it has support in MIN_GROUPS or more groups, two
group-balanced random halves of the clips agree (median gap over SPLITS splits), and leaving any one group out does not
move it; otherwise 'weak' with the reasons. Weak items are shown, never used as targets.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

from .common import REPO, StudioError, now, read_json, write_json

LIBRARY = REPO / 'library' / 'patterns'
LOCAL = REPO / '.studio' / 'patterns'     # per machine: where this library's local examples live (never committed)
TASTE = REPO / '.studio' / 'taste.json'   # the user's taste, in the same item vocabulary (local; a person's, not the repo's)
# One role vocabulary for shot lists, shots and patterns (references/screen_grammar.md section 4.G). Older shot lists used
# other names; each maps to the role whose pattern it reads (None: no role of its own - it reads the 'all' section).
ROLES = ('hook', 'establish', 'explain', 'reveal', 'compare', 'consequence', 'close')
LEGACY_ROLES = {'detail': 'explain', 'outro': 'close', 'transition': None}
MIN_GROUPS = 4              # groups with at least MIN_PER_GROUP values
MIN_PER_GROUP = 3
SPLITS = 50                 # group-balanced random halves
SPLIT_GAP_IQR = 0.25        # numeric: typical half-to-half gap of the median at most this share of the IQR
LOO_SHIFT_IQR = 0.25        # numeric: dropping one group moves the median at most this share of the IQR
SHARE_GAP = (0.03, 0.25)    # categorical: gap at most max(3 points, 25 % of the share)
SHARE_LOO = (0.05, 0.30)    # categorical: leave-one-out shift at most max(5 points, 30 % of the share)
EXEMPLARS = 6               # per section, one per group first
SEED = 20261010


# --- rows -------------------------------------------------------------------------------------------------------
def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def shot_rows(grammar, group, reel):
    """One flat row per shot: machine items always, labelled items when the labeller read the shot. A shot the labeller
    saw as several scenes (missed boundaries) is marked and left out of every pattern."""
    window = grammar['canvas']['layout'] == 'window'
    rows = []
    for s in grammar['shots']:
        cam = s['camera']
        row = {'group': group, 'reel': reel, 'shot': s['shot'], 'len_s': s['len_s'], 'len_share': s['len_share'],
               'position': s['position'], 'transition_in': s.get('transition_in'), 'camera_axis': cam.get('axis'),
               'camera_speed': (abs(cam['rate_per_s']['x']) + abs(cam['rate_per_s']['y']) + abs(cam['rate_per_s']['scale']))
               if cam.get('rate_per_s') else None,
               'visual_change_rate': s['visual_change_rate'], 'labelled': bool(s['labels']), 'multi': False}
        env = s.get('camera_envelope') or {}
        if cam.get('axis') not in ('still', 'unknown', None):   # the shape of a move only means something when it moves
            row.update({'move_burst_share': env.get('burst_share'), 'move_hold_frac': env.get('hold_frac')})
        if s['labels']:
            label = s['labels'][0]
            row['multi'] = label.get('distinct_scenes', 1) >= 2
            comp = [c for c in label['composition'].values() if c]
            for key in ('center_x', 'center_y', 'height_share', 'width_share', 'area_share', 'edge_margin', 'thirds_distance'):
                row[key] = _median(c[key] for c in comp)
            a, d = label['composition'].get('A'), label['composition'].get('D')
            row['subject_growth'] = round(d['height_share'] / a['height_share'], 3) if a and d and a['height_share'] else None
            row['subject_travel'] = round(math.hypot(d['center_x'] - a['center_x'], d['center_y'] - a['center_y']), 4) if a and d else None
            anchors = [v for v in label['anchor'].values() if v]
            row.update({'role': label['role'], 'camera_move': s['camera_move']['move'], 'view_height': label['view_height'],
                        'change_kind': label['change_kind'], 'reveal_method': label['reveal_method'],
                        'reveal_at': {'A': 0.08, 'B': 0.35, 'C': 0.65, 'D': 0.92}.get(label['reveal_panel']),
                        'scale_anchor': label['scale_anchor'], 'anchor_height_share': _median(anchors),
                        'depth_layers': label['depth_layers'], 'subject_count': label['subject_count'],
                        'graphics_count': len(label['graphics']), 'graphic_kinds': sorted({g['kind'] for g in label['graphics']}),
                        'subtitle': label['subtitle']['present'],
                        'subtitle_center_y': label['subtitle']['center_y'] if label['subtitle']['present'] else None,
                        'subtitle_chars': label['subtitle']['chars'] if label['subtitle']['present'] else None,
                        'note': label.get('note', '')})
            if not window:   # in a window layout the labeller sees the bands as empty frame
                row['void_share'] = _median(v.get('share') for v in label['void'].values())
        rows.append(row)
    from .grammar_core import scale_step
    by_shot = {r['shot']: r for r in rows}
    for row in rows:   # the size flow: this shot's subject against the shot before it, when both were read
        before = by_shot.get(row['shot'] - 1)
        step = scale_step(before.get('height_share'), row.get('height_share')) if before and not before['multi'] else None
        if step and not row['multi']:
            row['scale_step'] = step['step']
    return rows


NUMERIC = ['len_s', 'len_share', 'camera_speed', 'visual_change_rate', 'move_burst_share', 'move_hold_frac', 'center_x',
           'center_y', 'height_share', 'width_share', 'area_share', 'edge_margin', 'thirds_distance', 'subject_growth', 'subject_travel',
           'reveal_at', 'anchor_height_share', 'depth_layers', 'subject_count', 'graphics_count', 'subtitle_center_y',
           'subtitle_chars', 'void_share']
CATEGORICAL = ['transition_in', 'camera_axis', 'camera_move', 'view_height', 'change_kind', 'reveal_method', 'scale_anchor',
               'subtitle', 'graphic_kinds', 'scale_step']   # scale_step: closer / wider / same as the shot before   # graphic_kinds: share of shots showing each kind (a shot may show several)
MACHINE = {'len_s', 'len_share', 'camera_speed', 'visual_change_rate', 'move_burst_share', 'move_hold_frac', 'transition_in', 'camera_axis'}


# --- statistics -------------------------------------------------------------------------------------------------
def _quartiles(values):
    v = sorted(values)
    if len(v) == 1:
        return v[0], v[0], v[0], v[0], v[0]
    q = statistics.quantiles(v, n=20, method='inclusive')
    return q[1], q[4], statistics.median(v), q[14], q[17]   # p10 p25 p50 p75 p90


def _shares(rows, item):
    counts, n = defaultdict(int), 0
    for r in rows:
        if r.get(item) is None:
            continue
        n += 1
        for value in (r[item] if isinstance(r[item], list) else [r[item]]):
            counts[str(value)] += 1
    return {k: c / n for k, c in counts.items()} if n else {}, n


def _halves(rows, rng):
    by_group = defaultdict(set)
    for r in rows:
        by_group[r['group']].add(r['reel'])
    first = set()
    for reels in by_group.values():
        reels = sorted(reels); rng.shuffle(reels)
        first |= set(reels[:len(reels) // 2])
    return [r for r in rows if r['reel'] in first], [r for r in rows if r['reel'] not in first]


def numeric_item(rows, item, rng):
    have = [r for r in rows if r.get(item) is not None]
    if len(have) < 5:
        return None
    values = [r[item] for r in have]
    p10, p25, p50, p75, p90 = _quartiles(values)
    per_group = defaultdict(list)
    for r in have:
        per_group[r['group']].append(r[item])
    iqr = max(p75 - p25, 1e-9)
    gaps = []
    for _ in range(SPLITS):
        a, b = _halves(have, rng)
        if a and b:
            gaps.append(abs(statistics.median(r[item] for r in a) - statistics.median(r[item] for r in b)))
    loo = [abs(statistics.median(r[item] for r in have if r['group'] != acct) - p50) for acct in per_group
           if any(r['group'] != acct for r in have)]
    seen = sum(1 for v in per_group.values() if len(v) >= MIN_PER_GROUP)
    gap, shift = (statistics.median(gaps) if gaps else None), (max(loo) if loo else None)
    reasons = []
    if seen < MIN_GROUPS:
        reasons.append(f'support in {seen} groups')
    if gap is None or gap > SPLIT_GAP_IQR * iqr:
        reasons.append('halves disagree')
    if shift is None or shift > LOO_SHIFT_IQR * iqr:
        reasons.append('one group moves it')
    return {'kind': 'numeric', 'n': len(values), 'groups': seen, 'p10': round(p10, 4), 'p25': round(p25, 4), 'median': round(p50, 4),
            'p75': round(p75, 4), 'p90': round(p90, 4),
            'per_group_median': {a: round(statistics.median(v), 4) for a, v in sorted(per_group.items())},
            'half_gap': round(gap, 4) if gap is not None else None, 'leave_one_out_shift': round(shift, 4) if shift is not None else None,
            'strength': 'weak' if reasons else 'strong', **({'weak_because': reasons} if reasons else {})}


def categorical_item(rows, item, rng):
    shares, n = _shares(rows, item)
    if n < 5:
        return None
    counts = defaultdict(int)
    for r in rows:
        if r.get(item) is not None:
            counts[r['group']] += 1
    seen = sum(1 for c in counts.values() if c >= MIN_PER_GROUP)
    out = {}
    for value, share in sorted(shares.items(), key=lambda kv: -kv[1]):
        gaps = []
        for _ in range(SPLITS):
            a, b = _halves(rows, rng)
            sa, sb = _shares(a, item)[0], _shares(b, item)[0]
            if sa and sb:
                gaps.append(abs(sa.get(value, 0.0) - sb.get(value, 0.0)))
        loo = [abs(_shares([r for r in rows if r['group'] != acct], item)[0].get(value, 0.0) - share) for acct in counts]
        per_group = {a: round(_shares([r for r in rows if r['group'] == a], item)[0].get(value, 0.0), 4) for a in sorted(counts)}
        present = sum(1 for v in per_group.values() if v > 0)
        gap, shift = statistics.median(gaps) if gaps else None, max(loo) if loo else None
        reasons = []
        if seen < MIN_GROUPS or present < MIN_GROUPS:
            reasons.append(f'support in {min(seen, present)} groups')
        if gap is None or gap > max(SHARE_GAP[0], SHARE_GAP[1] * share):
            reasons.append('halves disagree')
        if shift is None or shift > max(SHARE_LOO[0], SHARE_LOO[1] * share):
            reasons.append('one group moves it')
        out[value] = {'share': round(share, 4), 'per_group': per_group, 'half_gap': round(gap, 4) if gap is not None else None,
                      'leave_one_out_shift': round(shift, 4) if shift is not None else None,
                      'strength': 'weak' if reasons else 'strong', **({'weak_because': reasons} if reasons else {})}
    return {'kind': 'categorical', 'n': n, 'groups': seen, 'values': out}


def group_patterns(rows, seed=SEED):
    rng = random.Random(seed)
    items = {}
    for item in NUMERIC:
        result = numeric_item(rows, item, rng)
        if result:
            items[item] = result
    for item in CATEGORICAL:
        result = categorical_item(rows, item, rng)
        if result:
            items[item] = result
    return items


def exemplars(rows, items, limit=EXEMPLARS):
    """Shots closest to the group's typical length share and subject height, one group at a time."""
    keys = [k for k in ('len_share', 'height_share') if k in items]
    def distance(r):
        return sum(abs(r[k] - items[k]['median']) / max(items[k]['p75'] - items[k]['p25'], 1e-6) for k in keys if r.get(k) is not None)
    ranked = sorted((r for r in rows if r.get('note') is not None), key=distance)
    picked, used = [], set()
    for r in ranked:
        if r['group'] not in used:
            picked.append(r); used.add(r['group'])
        if len(picked) == limit:
            break
    return picked


# --- build ------------------------------------------------------------------------------------------------------
PRIVATE_KEYS = ('per_group_median', 'per_group')   # values per sample group stay in the local full file


def shareable(items):
    """An item set without per-group values: the distribution, its support and its strength."""
    out = {}
    for name, item in items.items():
        item = {k: v for k, v in item.items() if k not in PRIVATE_KEYS}
        if item['kind'] == 'categorical':
            item['values'] = {c: {k: v for k, v in x.items() if k not in PRIVATE_KEYS} for c, x in item['values'].items()}
        out[name] = item
    return out


def build(measured, manifest, name, out=None):
    measured, manifest = Path(measured), Path(manifest)
    out = Path(out) if out else measured
    by_clip = {e['reel']: e for e in read_json(manifest)}
    rows, clips = [], {}
    for path in sorted(measured.glob('*/grammar.json')):
        clip = path.parent.name
        if clip not in by_clip:
            continue
        grammar = read_json(path)
        clips[clip] = {'sha256': grammar['video_sha256'], 'group': by_clip[clip]['group'], 'labelled': bool(grammar['labeller']['runs']),
                       'aspect': round(grammar['media']['width'] / grammar['media']['height'], 3), 'layout': grammar['canvas']['layout']}
        rows += shot_rows(grammar, by_clip[clip]['group'], clip)
    if not rows:
        raise StudioError('INPUT_INVALID', f'no grammar.json under {measured}')
    clean = [r for r in rows if not r['multi']]
    labelled = [r for r in clean if r['labelled']]
    sections = {'all': {'shots': len(clean), 'labelled_shots': len(labelled),
                        'items': {**{k: v for k, v in group_patterns(clean).items() if k in MACHINE},
                                  **{k: v for k, v in group_patterns(labelled).items() if k not in MACHINE}}}}
    local = {}
    for role in sorted({r['role'] for r in labelled}):
        part = [r for r in labelled if r['role'] == role]
        items = group_patterns(part)
        sections[role] = {'shots': len(part), 'groups': len({r['group'] for r in part}), 'items': items}
        local[role] = exemplars(part, items)
    local['all'] = exemplars(labelled, sections['all']['items'])
    common = {'schema_version': 1, 'name': name, 'built_at': now(),
              'vocabulary': '.agents/skills/reel-production/references/screen_grammar.md',
              'support': {'groups': len({c['group'] for c in clips.values()}), 'clips': len(clips),
                          'labelled_clips': sum(c['labelled'] for c in clips.values()), 'shots': len(rows),
                          'excluded_multi_scene': len(rows) - len(clean), 'aspects': sorted({c['aspect'] for c in clips.values()})},
              'strength_rule': {'min_groups': MIN_GROUPS, 'min_per_group': MIN_PER_GROUP, 'splits': SPLITS, 'split_gap_iqr': SPLIT_GAP_IQR,
                                'loo_shift_iqr': LOO_SHIFT_IQR, 'share_gap': SHARE_GAP, 'share_loo': SHARE_LOO},
              'limits': ['shot boundaries: 91 % of hand-labelled boundaries found, ~1 in 8 extra (grammar_core boundaries)',
                         'machine items include shots the labeller never read; multi-scene shots are excluded only where it read them',
                         'composition is from labeller boxes: a box overstates a non-rectangular subject (our renders are measured the same way)',
                         'void_share is left out for window layouts']}
    LIBRARY.mkdir(parents=True, exist_ok=True)
    target = LIBRARY / f'{name}.json'
    write_json(target, {**common, 'sections': {k: {**v, 'items': shareable(v['items'])} for k, v in sections.items()}})
    full = out / 'patterns_full.json'
    write_json(full, {**common, 'clips': clips, 'sections': sections})
    examples = out / 'exemplars.json'
    write_json(examples, {role: [{'group': r['group'], 'clip': r['reel'], 'shot': r['shot'],
                                  'strip': str(measured / r['reel'] / 'strips' / f"shot{r['shot']:03d}.jpg"),
                                  'len_share': r['len_share'], 'height_share': r.get('height_share'), 'note': r['note']}
                                 for r in picked] for role, picked in local.items()})
    LOCAL.mkdir(parents=True, exist_ok=True)
    write_json(LOCAL / f'{name}.local.json', {'exemplars': str(examples.resolve()), 'full': str(full.resolve()), 'built_at': now()})
    return {'library': str(target), 'full': str(full), 'exemplars': str(examples), 'sections': {g: v['shots'] for g, v in sections.items()},
            'artifacts': [str(target), str(full), str(examples)]}


def register_commands(subparsers):
    parser = subparsers.add_parser('pattern', help='Pattern library: distributions of how a measured study set uses the frame')
    commands = parser.add_subparsers(dest='pattern_command', required=True)
    p = commands.add_parser('build', help='grammar.json files + local manifest -> library/patterns/<name>.json (+ local full file, exemplars)')
    p.add_argument('--measured', required=True); p.add_argument('--manifest', required=True); p.add_argument('--name', required=True)
    p.set_defaults(handler=lambda a: build(a.measured, a.manifest, a.name))
    p = commands.add_parser('show', help="What a role's shots do in the library: strong ranges, weak items, example pictures")
    p.add_argument('--role', required=True, choices=sorted({*ROLES, *LEGACY_ROLES}))
    p.add_argument('--project'); p.add_argument('--library')
    p.set_defaults(handler=lambda a: show(a.role, a.project, a.library))
    p = commands.add_parser('taste', help="The user's taste in pattern items (local): add, list, remove")
    taste = p.add_subparsers(dest='taste_command', required=True)
    q = taste.add_parser('add', help="one entry from the user's words, read back to them first")
    q.add_argument('--sections', required=True, help='roles (comma separated) or all'); q.add_argument('--item', required=True)
    q.add_argument('--prefer', required=True, help='higher|lower for a number; values (comma separated) for a category')
    q.add_argument('--why', required=True); q.add_argument('--user-words', required=True)
    q.set_defaults(handler=lambda a: taste_add(a.sections, a.item, a.prefer, a.why, a.user_words))
    q = taste.add_parser('list'); q.set_defaults(handler=lambda a: {'entries': taste_entries()})
    q = taste.add_parser('remove'); q.add_argument('--id', required=True); q.set_defaults(handler=lambda a: taste_remove(a.id))
    from . import taste_picks   # what the user picked over what, and why (story, concept, take, redo)
    q = taste.add_parser('summary', help="the lines a brief carries as 'User taste:' (studio/taste_picks.py)")
    q.add_argument('--kinds', help='comma separated: ' + ','.join(taste_picks.KINDS))
    q.set_defaults(handler=lambda a: taste_picks.summary(a.kinds.split(',') if a.kinds else None))
    q = taste.add_parser('picks'); q.add_argument('--kind', choices=taste_picks.KINDS)
    q.set_defaults(handler=lambda a: {'picks': taste_picks.picks(a.kind)})
    from .camera_taste import register_commands as dynamics_commands   # pattern dynamics: the user's reference camera profile
    dynamics_commands(commands)
    from .speech_pace import register_commands as pacing_commands   # pattern pacing: how the user's reference speaks
    pacing_commands(commands)


# --- reading a library ------------------------------------------------------------------------------------------
def role_of(role):
    """A shot-list role in the current vocabulary (legacy names mapped), or None."""
    if role in ROLES:
        return role
    return LEGACY_ROLES.get(role)


def library_name(project=None, name=None):
    """--library, else the project's pattern_library, else the only library there is."""
    if name:
        return name
    if project:
        from .project import load_project, project_dir
        chosen = load_project(project_dir(project)).get('pattern_library')
        if chosen:
            return chosen
    names = sorted(p.stem for p in LIBRARY.glob('*.json')) if LIBRARY.is_dir() else []
    if len(names) == 1:
        return names[0]
    raise StudioError('INPUT_INVALID', f'choose a pattern library ({names or "none built"})',
                      recovery='--library NAME, or set pattern_library in project.json')


def load(name):
    path = LIBRARY / f'{name}.json'
    if not path.is_file():
        raise StudioError('INPUT_INVALID', f'no pattern library {name} ({path})')
    return read_json(path)


def section(library, role):
    """The section a role reads: its own when the library has it, else 'all'."""
    role = role_of(role)
    return (role if role in library['sections'] else 'all'), library['sections'][role if role in library['sections'] else 'all']


def _strong_values(item):
    return {v: x['share'] for v, x in item['values'].items() if x['strength'] == 'strong'}


def show(role, project=None, name=None):
    """What a role's shots do in the library: strong items as ranges, weak ones with their reasons, and local example
    shots (pictures and notes) when this machine has them. Material to judge with - never targets by itself."""
    name = library_name(project, name)
    library = load(name)
    key, part = section(library, role)
    strong, weak = {}, {}
    for item, data in part['items'].items():
        if data['kind'] == 'numeric':
            row = {k: data[k] for k in ('p10', 'p25', 'median', 'p75', 'p90', 'n')}
            (strong if data['strength'] == 'strong' else weak)[item] = row if data['strength'] == 'strong' else {**row, 'weak_because': data.get('weak_because', [])}
        else:
            values = _strong_values(data)
            if values:
                strong[item] = {'shares': values, 'n': data['n']}
            others = {v: {'share': x['share'], 'weak_because': x.get('weak_because', [])} for v, x in data['values'].items() if x['strength'] != 'strong'}
            if others:
                weak[item] = others
    examples, note = [], None
    index = LOCAL / f'{name}.local.json'
    if index.is_file():
        local = read_json(index)
        if Path(local.get('exemplars', '')).is_file():
            examples = [e for e in read_json(local['exemplars']).get(key, []) if Path(e['strip']).is_file()]
            examples = [{k: e[k] for k in ('strip', 'note', 'len_share', 'height_share')} for e in examples]
    if not examples:
        note = 'no example pictures on this machine: numbers only'
    taste = taste_for(key, part['items'])
    return {'library': name, 'section': key, 'role': role_of(role), 'support': {k: part[k] for k in ('shots', 'groups') if k in part},
            'strong': strong, 'weak': weak, 'taste': taste, 'examples': examples, **({'note': note} if note else {}),
            'artifacts': [e['strip'] for e in examples]}


# --- comparing our shots ----------------------------------------------------------------------------------------
COMPOSITION = ('center_x', 'center_y', 'height_share', 'width_share', 'area_share', 'edge_margin', 'thirds_distance')


def focus_of(shot):
    """(ids, source): what the shot is about, as declared - screen.subject, else declared key parts, else the parts screen
    targets measure; else the probe's whole subject class (everything shot.subjects names: often the whole set)."""
    screen = shot.get('screen') or {}
    if screen.get('subject'):
        return list(screen['subject']), 'screen.subject'
    declared = [k['id'] for k in shot.get('key_parts', []) if k.get('source', 'declared') == 'declared']
    if declared:
        return declared, 'key_parts'
    targeted = list(dict.fromkeys(t['of'] for t in screen.get('targets', []) if t['of'] not in ('subject', 'all')))
    if targeted:
        return targeted, 'screen.targets'
    return [], 'subject_class'


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None


FULL_FRAME = (0.95, 0.9)   # height_share, area_share at or above which an undeclared focus is the whole picture, not a subject


def measure_shot(frame_report, shot, fps):
    """What can be measured on our own shot without a labeller, on the shot's focus (focus_of): its composition (the
    frame probe's id-pass boxes, through the same formulas as study clips - grammar_core.composition), how far it travels
    across the shot (subject_travel, as study clips measure it between their first and last keyframe), and the shot's
    length in seconds. Returns (values, about) - about says what was measured."""
    from .grammar_core import composition
    ids, source = focus_of(shot)
    classes = [f'key:{i}' for i in ids] if ids else ['subject']
    rows = []
    for frame in frame_report.get('frames', []):
        shapes = frame.get('shapes') or {}
        box = _union([shapes[c]['bbox'] for c in classes if (shapes.get(c) or {}).get('bbox')])
        if box:
            rows.append(composition(box))
    values = {k: round(statistics.median(r[k] for r in rows), 4) for k in COMPOSITION} if rows else {}
    if len(rows) >= 2:
        values['subject_travel'] = round(math.hypot(rows[-1]['center_x'] - rows[0]['center_x'], rows[-1]['center_y'] - rows[0]['center_y']), 4)
    if fps:
        values['len_s'] = round(shot['duration_frames'] / fps, 3)
    about = {'focus': ids or ['subject'], 'source': source, 'frames_measured': len(rows)}
    if ids:
        about['framing'] = framing(frame_report, ids, shot['duration_frames'])
    details = {d['id'] for d in (shot.get('screen') or {}).get('details', [])}
    if source == 'screen.subject' and details and set(ids) <= details:
        about['note'] = ('FOCUS_IS_DETAIL: the focus is the detail itself - make screen.subject the structure the narration '
                         'explains (the parts of the feature that holds the detail) and keep the detail readable with its min_px')
    if source == 'subject_class' and values.get('height_share', 0) >= FULL_FRAME[0] and values.get('area_share', 0) >= FULL_FRAME[1]:
        about['note'] = ('FOCUS_UNDECLARED: no focus declared and the whole subject class fills the frame - composition says '
                         'nothing about what the shot is about; declare screen.subject')
    return values, about


SETTLED_FROM = 0.6   # the settled part of a shot: its last 40 % (burst_settle holds for hold_frac 0.4 by default)
EDGE = 0.002         # a box this close to a frame edge touches it
MAJORITY = 0.5       # a framing finding holds when it is true on at least half the settled frames


def framing(frame_report, ids, frame_count):
    """Too close, judged on the settled frames, on the focus's union box (0..1, top-left origin):
    cut - on an axis it touches one frame edge only (the focus runs off one side: part of what is explained is lost);
    fills_frame - it touches all four edges (nothing around it is left: too close); part_out - a focus part shows 0 px.
    A focus touching both edges of one axis runs across the frame (a band seen whole in depth) and is fine."""
    settled = [f for f in frame_report.get('frames', []) if f['frame'] >= SETTLED_FROM * max(0, frame_count - 1)]
    counts = {'cut': [], 'fills_frame': [], 'part_out': []}
    measured = {i for f in settled for i in (f.get('key_px') or {})}
    unmeasured = sorted(set(ids) - measured)   # the build measured another focus: rebuild to judge these
    if unmeasured:
        return {'settled_frames': [f['frame'] for f in settled], 'found': {}, 'unmeasured': unmeasured}
    for frame in settled:
        shapes = frame.get('shapes') or {}
        box = _union([shapes[f'key:{i}']['bbox'] for i in ids if (shapes.get(f'key:{i}') or {}).get('bbox')])
        out = [i for i in ids if not (frame.get('key_px') or {}).get(i)]
        if out:
            counts['part_out'].append((frame['frame'], out))
        if not box:
            continue
        touch = [box[0] <= EDGE, box[1] <= EDGE, box[2] >= 1 - EDGE, box[3] >= 1 - EDGE]   # left, top, right, bottom
        if all(touch):
            counts['fills_frame'].append((frame['frame'], None))
        elif touch[0] != touch[2] or touch[1] != touch[3]:
            sides = [name for name, t, other in (('left', touch[0], touch[2]), ('top', touch[1], touch[3]),
                                                 ('right', touch[2], touch[0]), ('bottom', touch[3], touch[1])) if t and not other]
            counts['cut'].append((frame['frame'], sides))
    need = max(1, math.ceil(MAJORITY * len(settled)))
    found = {}
    for kind, hits in counts.items():
        if len(hits) >= need:
            detail = sorted({x for _, v in hits for x in (v or [])})
            found[kind] = {'frames': [f for f, _ in hits], **({'what': detail} if detail else {})}
    return {'settled_frames': [f['frame'] for f in settled], 'found': found}


def framing_lines(result):
    """FOCUS_FRAMING lines (taste) for a compare_shot result: the focus cut, filling the frame, or out of it."""
    found = ((result or {}).get('measured_on') or {}).get('framing', {}).get('found') or {}
    words = {'cut': lambda w: f"cut at the {'/'.join(w.get('what', []))} edge", 'fills_frame': lambda w: 'fills the whole frame (too close)',
             'part_out': lambda w: f"part(s) {w.get('what')} off screen"}
    return [f"FOCUS_FRAMING: when the shot settles, {', '.join(result['measured_on']['focus'])} " + '; '.join(words[k](v) for k, v in found.items())
            + ' - frame the whole focus: change the angle (elevation/azimuth), then camera.move.focus height_share, then the '
              'lens; or redefine screen.subject with the user'] if found else []


# Items never compared with our shots, and why: a share of the reel depends on how many shots the reel has (the first
# comparison, 2026-10-10: a 5-shot reel read every shot as 'outside' against study clips of 20-50 shots).
NOT_COMPARED = {'len_share': 'depends on how many shots the reel has', 'position': 'depends on how many shots the reel has'}


def _position(value, data):
    if value < data['p10'] or value > data['p90']:
        return 'outside'
    if data['p25'] <= value <= data['p75']:
        return 'inside'
    return 'edge'


def compare(items, measured, departs=(), taste=()):
    """Rows comparing measured values with a section's items: position inside (p25-p75) / edge (p10-p90) / outside,
    the item's strength, and the declared departure if any. Weak items are compared but marked; only strong items
    with no declared departure count as off."""
    declared = {d['item']: d['why'] for d in departs}
    rows = []
    for item, value in measured.items():
        data = items.get(item)
        if value is None or not data or item in NOT_COMPARED:
            continue
        if data['kind'] == 'numeric':
            pos = _position(value, data)
            row = {'item': item, 'value': value, 'typical': [data['p25'], data['p75']], 'outer': [data['p10'], data['p90']],
                   'median': data['median'], 'position': pos, 'strength': data['strength']}
        else:
            x = data['values'].get(str(value))
            pos = 'common' if x and x['share'] >= 0.2 else ('seen' if x else 'unseen')
            row = {'item': item, 'value': value, 'share': x['share'] if x else 0.0, 'position': pos,
                   'strength': x['strength'] if x else 'weak'}
        if item in declared:
            row['declared_depart'] = declared[item]
        mine = [t for t in taste if t['item'] == item]
        if mine:   # the user's taste for this item: shown beside the pattern, never a warning
            row['taste'] = [{'id': t['id'], 'prefer': t['prefer'], 'met': taste_met(t, value)} for t in mine]
        row['off'] = row['strength'] == 'strong' and row['position'] in ('outside', 'unseen') and item not in declared
        rows.append(row)
    return rows


def compare_shot(path, shot, version_dir, name=None):
    """compare() for one built shot, or None when the shot declares no role. Never raises: a library problem is
    reported in the result (the comparison is advice)."""
    role = (shot.get('screen') or {}).get('role')
    if not role:
        return None
    try:
        library_key = library_name(path, name)
        key, part = section(load(library_key), role)
        report = read_json(Path(version_dir) / 'frame_report.json') if (Path(version_dir) / 'frame_report.json').is_file() else {}
        from .project import load_project, project_dir
        measured, about = measure_shot(report, shot, load_project(project_dir(path))['output']['fps'])
        return {'library': library_key, 'section': key, 'measured_on': about,
                'rows': compare(part['items'], measured, (shot.get('screen') or {}).get('pattern_departs', []), taste_for(key, part['items']))}
    except StudioError as error:
        return {'error': f'{error.code}: {error.message}'}


def off_lines(result):
    """SCREEN_PATTERN_OFF lines for a compare_shot result: strong items outside the section, not declared departures."""
    if not result or 'rows' not in result:
        return []
    off = [r for r in result['rows'] if r['off']]
    if not off:
        return []
    detail = '; '.join(f"{r['item']} {r['value']} (typical {r['typical'][0]}-{r['typical'][1]}, outer {r['outer'][0]}-{r['outer'][1]})"
                       if 'typical' in r else f"{r['item']} {r['value']} (unseen)" for r in off)
    return [f"SCREEN_PATTERN_OFF: {result['section']} shots in {result['library']}: {detail} - change it, or declare why in "
            f"screen.pattern_departs"]


def sheet_lines(result):
    """Markdown for a storyboard sheet: our values against the section, strong and weak marked."""
    if not result:
        return []
    if 'error' in result:
        return ['', f"Patterns: not compared ({result['error']})"]
    about = result.get('measured_on') or {}
    lines = ['', f"**Against {result['section']} shots ({result['library']})** - advice, not targets; weak items are for reference only.",
             f"Measured on {', '.join(about.get('focus', []))} ({about.get('source', '?')})." + (f" {about['note']}" if about.get('note') else '')
             + ''.join(f' {line}' for line in framing_lines(result)), '',
             '| item | ours | typical (p25-p75) | outer (p10-p90) | position | strength | declared departure | your taste |', '|---|---|---|---|---|---|---|---|']
    for r in result['rows']:
        taste = ', '.join(f"{t['prefer']} ({'met' if t['met'] else 'not met' if t['met'] is False else '?'})" for t in r.get('taste', []))
        lines.append(f"| {r['item']} | {r['value']} | {r.get('typical', '')} | {r.get('outer', '')} | {r['position']} | {r['strength']} | {r.get('declared_depart', '')} | {taste} |")
    return lines


FLAT_RUN = 2   # this many 'same' steps in a row (three shots at one size) reads as one long shot of the same picture


def scale_flow(shares, usual=None):
    """The reel's size flow from each shot's focus height_share in cut order: the steps (closer / wider / same, as study
    reels are read - grammar_core.scale_step), their mix against the study reels' mix (`usual`: the 'all' section's
    scale_step shares), and SCALE_FLOW_FLAT when FLAT_RUN 'same' steps run in a row while 'same' is not what study reels
    mostly do. Advice for the reel as a whole: no shot's size is forced."""
    from .grammar_core import scale_step
    steps = [(scale_step(a, b) or {}).get('step') for a, b in zip(shares, shares[1:])]
    known = [s for s in steps if s]
    mix = {k: round(known.count(k) / len(known), 3) for k in ('closer', 'same', 'wider')} if known else {}
    run = longest = 0
    for s in steps:
        run = run + 1 if s == 'same' else 0
        longest = max(longest, run)
    same_usual = ((usual or {}).get('same') or {}).get('share')
    flat = longest >= FLAT_RUN and (same_usual is None or same_usual < 0.5)
    lines = [f"SCALE_FLOW_FLAT: {longest + 1} shots in a row at one size (steps {steps}); study reels change size "
             f"{round((1 - same_usual) * 100) if same_usual is not None else '?'} % of the time - vary camera.move.focus "
             "height_share between them (wide -> close, or a wide breath before the next close)"] if flat else []
    return {'steps': steps, 'mix': mix, 'usual': {k: (v or {}).get('share') for k, v in (usual or {}).items()}, 'warnings': lines}


REPEAT = {'centre': 0.05, 'height_ratio': 1.15, 'view_deg': 15.0}   # two cut-together frames this alike read as one picture


def framing_repeats(ends):
    """FRAMING_REPEATED lines: at each cut, the last frame of a shot against the first of the next - the focus box
    (centre and height) and the camera's up/down angle. ends = [{shot_id, first: (box, elevation), last: (box,
    elevation), handover}] in cut order (handover: the shot list's kind, show_core.HANDOVER_KINDS); a missing box or
    angle is not compared. The rocket reel's board had six shots of the booster upright mid-frame at eye level cut
    together; nothing measured the cut itself. A hand-over that runs the picture on (continue, match_action) means it."""
    from .blender_ops.show_core import CONTINUOUS
    lines = []
    for a, b in zip(ends, ends[1:]):
        if not a.get('last') or not b.get('first') or not a['last'][0] or not b['first'][0]:
            continue
        if a.get('handover') in CONTINUOUS:
            continue
        (box_a, view_a), (box_b, view_b) = a['last'], b['first']
        centre = lambda box: ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)  # noqa: E731
        ha, hb = box_a[3] - box_a[1], box_b[3] - box_b[1]
        if min(ha, hb) <= 0:
            continue
        alike = (math.dist(centre(box_a), centre(box_b)) <= REPEAT['centre'] and max(ha, hb) / min(ha, hb) <= REPEAT['height_ratio']
                 and (view_a is None or view_b is None or abs(view_a - view_b) <= REPEAT['view_deg']))
        if alike:
            lines.append(f"FRAMING_REPEATED: {a['shot_id']} ends and {b['shot_id']} starts on one composition (focus at "
                         f"({centre(box_b)[0]:.2f}, {centre(box_b)[1]:.2f}), {hb:.2f} tall"
                         + (f", camera {view_b:+.0f} deg" if view_b is not None else '') + ") - change the place, size or angle of one "
                         "side (its frame picture), or hand it over as continue / match_action in the shot list")
    return lines


def compare_cut(project_dir, video, snapshot_rows, out_dir, fps=30, name=None):
    """A rough cut against the patterns (advice for qa collect and the cut sheet): the cut measured like a study clip at
    the edit's own cuts (machine items only - no labeller, no cost) plus each shot's composition from its build, per
    role section; and the reel's mix of camera moves against the 'all' section. None when no shot declares a role."""
    roles = {r['shot_id']: ((r.get('shot_snapshot') or {}).get('screen') or {}).get('role') for r in snapshot_rows}
    if not any(roles.values()):
        return None
    from .screen_grammar import measure
    try:
        library_key = library_name(project_dir, name)
        library = load(library_key)
        starts = [r['start_frame'] / fps for r in snapshot_rows]
        measure(video, out_dir, runs=0, labelled=False, cuts=starts[1:])
        grammar = read_json(Path(out_dir) / 'grammar.json')
    except StudioError as error:
        return {'error': f'{error.code}: {error.message}'}
    rows = shot_rows(grammar, 'ours', 'cut')
    shots = []
    for snap in snapshot_rows:
        row = min(rows, key=lambda r: abs(r['position'] * grammar['media']['duration'] - snap['start_frame'] / fps)) if rows else {}
        shot = snap.get('shot_snapshot') or {}
        role = roles.get(snap['shot_id'])
        key, part = section(library, role or 'transition')
        version = shot.get('scene_version')
        report = Path(project_dir) / 'shots' / snap['shot_id'] / 'versions' / str(version) / 'frame_report.json'
        measured = {k: row.get(k) for k in MACHINE if row.get(k) is not None}
        ours, about = measure_shot(read_json(report) if report.is_file() else {}, {**shot, 'duration_frames': snap['frame_count']}, fps)
        measured.update(ours)
        shots.append({'shot_id': snap['shot_id'], 'role': role, 'section': key, 'measured_on': about,
                      'rows': compare(part['items'], measured, (shot.get('screen') or {}).get('pattern_departs', []), taste_for(key, part['items']))})
    axes = defaultdict(int)
    for r in rows:
        axes[r['camera_axis']] += 1
    pattern_axes = library['sections']['all']['items'].get('camera_axis', {}).get('values', {})
    reel = {'camera_axis': {a: {'ours': round(c / len(rows), 3), 'usual': (pattern_axes.get(a) or {}).get('share')}
                            for a, c in sorted(axes.items())} if rows else {}}
    shares = [next((r['value'] for r in s['rows'] if r['item'] == 'height_share'), None) for s in shots]
    reel['scale_flow'] = scale_flow(shares, library['sections']['all']['items'].get('scale_step', {}).get('values'))
    off = [f"{s['shot_id']} {r['item']}" for s in shots for r in s['rows'] if r['off']]
    return {'library': library_key, 'shots': shots, 'reel': reel, 'off': off, 'advice': reel['scale_flow']['warnings']}


# --- the user's taste -------------------------------------------------------------------------------------------
# A taste entry moves where a section's shots should sit, in the items the patterns use - never a subject ("the jump"),
# so it carries to the next topic. It comes from the user's own words, read back to them before it is added.
def _taste_file():
    return read_json(TASTE) if TASTE.is_file() else {}


def taste_entries():
    return _taste_file().get('entries', [])


def taste_add(sections, item, prefer, why, user_words):
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'taste')
    sections = [x.strip() for x in sections.split(',')] if isinstance(sections, str) else list(sections)
    unknown = [x for x in sections if x not in ROLES and x != 'all']
    if unknown:
        raise StudioError('INPUT_INVALID', f'unknown sections {unknown} (roles: {list(ROLES)} or all)')
    if item in NUMERIC:
        if prefer not in ('higher', 'lower'):
            raise StudioError('INPUT_INVALID', f'{item} is a number: prefer higher or lower')
    elif item in CATEGORICAL:
        prefer = [x.strip() for x in prefer.split(',')] if isinstance(prefer, str) else list(prefer)
    else:
        raise StudioError('INPUT_INVALID', f'unknown item {item} (references/screen_grammar.md)')
    if len(why.strip()) < 8:
        raise StudioError('INPUT_INVALID', 'say why in a sentence')
    entries = taste_entries()
    entry = {'id': f't{max([int(e["id"][1:]) for e in entries] + [0]) + 1:02d}', 'sections': sections, 'item': item, 'prefer': prefer,
             'why': why.strip(), 'user_words': words, 'added_at': now()}
    TASTE.parent.mkdir(parents=True, exist_ok=True)
    write_json(TASTE, {**_taste_file(), 'schema_version': 1, 'entries': entries + [entry]})   # the picks beside them stay
    return {'added': entry, 'artifacts': [str(TASTE)]}


def taste_remove(entry_id):
    entries = taste_entries()
    kept = [e for e in entries if e['id'] != entry_id]
    if len(kept) == len(entries):
        raise StudioError('INPUT_INVALID', f'no taste entry {entry_id}')
    write_json(TASTE, {**_taste_file(), 'schema_version': 1, 'entries': kept})
    return {'removed': entry_id, 'artifacts': [str(TASTE)]}


def taste_for(section_key, items):
    """Entries that apply to a section, each with the aim it implies on that section's distribution."""
    out = []
    for e in taste_entries():
        if section_key not in e['sections'] and 'all' not in e['sections']:
            continue
        data = items.get(e['item'])
        aim = None
        if data and data['kind'] == 'numeric':
            aim = [data['median'], data['p90']] if e['prefer'] == 'higher' else [data['p10'], data['median']]
        out.append({**{k: e[k] for k in ('id', 'item', 'prefer', 'why')}, **({'aim': aim} if aim else {})})
    return out


def taste_met(entry, value):
    if isinstance(entry['prefer'], list):
        return str(value) in entry['prefer']
    if not entry.get('aim'):
        return None
    return value >= entry['aim'][0] if entry['prefer'] == 'higher' else value <= entry['aim'][1]   # past the median, the user's side


# --- the shot's own record --------------------------------------------------------------------------------------
def record_problems(shot, siblings=()):
    """Advice on a shot's pattern record (it lives on the shot, so delegated runs keep it too): a shot with a role and
    no record has not read its patterns (PATTERN_UNREAD); one carried record repeated on half the shots or more was
    pasted, not read (PATTERN_RECORD_REPEATED). siblings: (shot_id, carried) of every shot of the project."""
    screen = shot.get('screen') or {}
    if not screen.get('role'):
        return []
    if not screen.get('pattern_carried') and not screen.get('pattern_departs'):
        return [f"PATTERN_UNREAD: {shot['shot_id']} ({screen['role']}) records nothing from studio pattern show --role {screen['role']} "
                f"(screen.pattern_carried / pattern_departs)"]
    group = repeated_records(siblings).get(shot['shot_id'])
    return [f"PATTERN_RECORD_REPEATED: {', '.join(group)} carry the same record - read each shot's own role and examples"] if group else []


def _record_key(carried):
    return tuple(sorted((r['item'], json.dumps(r.get('value'), sort_keys=True), r['why'].strip()) for r in carried))


def repeated_records(shots):
    """{shot_id: [ids sharing its record]} for carried records repeated on at least two shots and half the shots."""
    groups = defaultdict(list)
    shots = list(shots)
    for shot_id, carried in shots:
        if carried:
            groups[_record_key(carried)].append(shot_id)
    out = {}
    for ids in groups.values():
        if len(ids) >= 2 and len(ids) >= len(shots) / 2:
            out.update({i: ids for i in ids})
    return out


def project_records(path, shot):
    """record_problems for a shot against every shot of its project (the shot as given wins over its saved copy)."""
    from .project import load_project, load_shot, project_dir
    path = project_dir(path)
    siblings = []
    for entry in load_project(path).get('shots', []):
        other = shot if entry['shot_id'] == shot['shot_id'] else load_shot(path, entry['shot_id'])
        siblings.append((entry['shot_id'], (other.get('screen') or {}).get('pattern_carried') or []))
    return record_problems(shot, siblings)
