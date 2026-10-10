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

import random
import statistics
from collections import defaultdict
from pathlib import Path

from .common import REPO, StudioError, now, read_json, write_json

LIBRARY = REPO / 'library' / 'patterns'
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
    return rows


NUMERIC = ['len_s', 'len_share', 'camera_speed', 'visual_change_rate', 'move_burst_share', 'move_hold_frac', 'center_x',
           'center_y', 'height_share', 'width_share', 'area_share', 'edge_margin', 'thirds_distance', 'subject_growth',
           'reveal_at', 'anchor_height_share', 'depth_layers', 'subject_count', 'graphics_count', 'subtitle_center_y',
           'subtitle_chars', 'void_share']
CATEGORICAL = ['transition_in', 'camera_axis', 'camera_move', 'view_height', 'change_kind', 'reveal_method', 'scale_anchor',
               'subtitle', 'graphic_kinds']   # graphic_kinds: share of shots showing each kind (a shot may show several)
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
    return {'library': str(target), 'full': str(full), 'exemplars': str(examples), 'sections': {g: v['shots'] for g, v in sections.items()},
            'artifacts': [str(target), str(full), str(examples)]}


def register_commands(subparsers):
    parser = subparsers.add_parser('pattern', help='Pattern library: distributions of how a measured study set uses the frame')
    commands = parser.add_subparsers(dest='pattern_command', required=True)
    p = commands.add_parser('build', help='grammar.json files + local manifest -> library/patterns/<name>.json (+ local full file, exemplars)')
    p.add_argument('--measured', required=True); p.add_argument('--manifest', required=True); p.add_argument('--name', required=True)
    p.set_defaults(handler=lambda a: build(a.measured, a.manifest, a.name))
