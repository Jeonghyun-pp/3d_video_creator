"""Camera dynamics against the user's reference: how much our camera moves, on the same ruler as the reels the user
pointed at.

Why (2026-10-12, rocket_landing): the user wanted dynamic camera moves - "클로즈업이나 장소 이동에서 카메라 움직임이
역동적인 계정들이 보기 좋았던것같아서" - named one account as the model, and there was no way to check our shots
against it. Measured with the reference ruler (grammar_core.camera), that account is mid-pack in screen speed and
first in how long its moving shots run (3.1 s median): a speed limit alone would not say why it reads well, so the
profile keeps speed, parallax (travel through space), the push-and-settle rhythm and moving length, per kind of shot.

- learn: the user's chosen group(s) from the local measured study set -> .studio/camera_dynamics.json (local; the group
  label stays there and nowhere else - public files speak of "the user's reference profile").
- measure: one built version, measured in its own scene (blender_ops/grammar_synth.py: a clay frame is too flat for the
  pixel probe) -> versions/<v>/camera_dynamics.json.
- judge: a measurement against the profile -> kind, percentiles, CAMERA_FLAT / CAMERA_HARSH lines.
"""
from __future__ import annotations

import bisect
import math
from pathlib import Path

from .common import CODE, REPO, StudioError, blender_binary, now, read_json, run_command, write_json

PROFILE = REPO / '.studio' / 'camera_dynamics.json'   # local, never committed: the user's reference and its numbers
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.95)
FLAT_BELOW = 0.25      # below this percentile of the profile's own shots of the kind: reads flat beside them
HARSH_ABOVE = 0.95     # above this percentile: faster than nearly all of them (comfort first, the user's rule 2026-10-08)
ROLL_FLOOR_DEG = 2.0   # turning slower than this per second is never called harsh, whatever the reference
STREAK_FLOOR = 0.1     # less than a tenth of the picture streaking past is never called harsh
STREAK_LIVELY = 0.05   # a twentieth of the picture streaking past reads lively (smoke: thin posts two metres in front of a
                       # trucking camera 9.5 %, the same truck without them 0.7 %). Absolute, not the reference's percentile:
                       # the reference's streak comes from pixels, ours from the scene (grammar_core.PIXEL_STREAK_FLOOR)
BURST_LIVELY = 0.75    # a rush-and-settle whose head carries more of the motion than 75 % of the reference's shots of its kind
ITEMS = ('scale', 'travel', 'roll', 'parallax', 'streak', 'burst_share', 'hold_frac', 'length_s')
KINDS = {'still': 'hold', 'scale_in': 'push', 'scale_out': 'push', 'x': 'travel', 'y': 'travel', 'compound': 'travel', 'roll': 'travel'}


def kind_of(camera):
    return KINDS.get((camera or {}).get('axis'), 'unknown')


def values_of(camera, envelope=None, length_s=None, feel=None):
    """The numbers a shot is compared on (reference units: content-width shares and log-scale per second)."""
    r = (camera or {}).get('rate_per_s') or {}
    return {'scale': abs(r.get('scale', 0.0)), 'travel': math.hypot(r.get('x', 0.0), r.get('y', 0.0)), 'roll': abs(r.get('roll_deg', 0.0)),
            'parallax': (camera or {}).get('parallax_per_s', 0.0),
            **({'streak': feel['streak']} if feel and feel.get('streak') is not None else {}),
            **({'burst_share': envelope.get('burst_share'), 'hold_frac': envelope.get('hold_frac')} if envelope else {}),
            **({'length_s': length_s} if length_s is not None else {})}


def _quantiles(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    pick = lambda q: values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))]  # noqa: E731
    return {f'p{round(q * 100)}': round(pick(q), 5) for q in QUANTILES} | {'n': len(values), 'values': [round(v, 5) for v in values]}


def _shares(names):
    return {'n': len(names), 'shares': {k: round(names.count(k) / len(names), 3) for k in sorted(set(names))}} if names else None


def build_profile(shots, name, user_words, groups):
    """shots: [{camera, camera_envelope, len_s}] of the chosen reels -> the profile dict."""
    by_kind = {}
    for s in shots:
        cam = s.get('camera') or {}
        if 'rate_per_s' not in cam:
            continue
        by_kind.setdefault(kind_of(cam), []).append({**values_of(cam, s.get('camera_envelope'), s.get('len_s'), s.get('feel')),
                                                     'felt': bool(s.get('feel')),
                                                     'recipe': recipe(s['feel'], kind_of(cam))['recipe'] if s.get('feel') else None})
    total = sum(len(v) for v in by_kind.values())
    if not total:
        raise StudioError('INPUT_INVALID', 'no measured shots in the chosen group(s)')
    kinds = {}
    for kind, rows in by_kind.items():
        kinds[kind] = {'share': round(len(rows) / total, 3),
                       **{item: _quantiles([r.get(item) for r in rows]) for item in ITEMS}}
    every = [r.get('roll') for rows in by_kind.values() for r in rows]
    felt = [r['recipe'] for rows in by_kind.values() for r in rows if r['felt']]
    named = [r for r in felt if r]
    return {'schema_version': 1, 'name': name, 'groups': list(groups), 'user_words': user_words, 'shots': total, 'kinds': kinds,
            'roll': _quantiles(every),   # turning about the view: judged over all shots (no kind is about rolling)
            'streak': _quantiles([r.get('streak') for rows in by_kind.values() for r in rows]),   # judged over all shots too
            'joins': _shares([s.get('join_in') for s in shots if s.get('join_in')]),   # how its cuts carry the camera's motion
            # how it shows insides (the labeller's reveal_method, first run): the share a shot list is held against
            'reveals': _shares([s['labels'][0]['reveal_method'] for s in shots if s.get('labels')]),
            # the reference's mix of feels (pixels give only the recipes their known values can name - camera_recipes.json)
            'recipes': {'measured_shots': len(felt), 'shares': {k: round(named.count(k) / len(felt), 3) for k in sorted(set(named))},
                        'unnamed_share': round(1 - len(named) / len(felt), 3)} if felt else None,
            'learned_at': now()}


def _measured_dir():
    from .patterns import LOCAL, library_name
    index = LOCAL / f'{library_name()}.local.json'
    if not index.is_file():
        raise StudioError('INPUT_INVALID', 'no local measured study set (pattern build first)')
    full = Path(read_json(index)['full'])
    return full, full.parent


def learn(groups, name, user_words):
    if not user_words or len(user_words.strip()) < 4:
        raise StudioError('INPUT_INVALID', "pattern dynamics learn records whose reference this is: --user-words (the user's words)")
    full, measured = _measured_dir()
    by_sha = {c['sha256']: c['group'] for c in read_json(full)['clips'].values()}
    unknown = sorted(set(groups) - set(by_sha.values()))
    if unknown:
        raise StudioError('INPUT_INVALID', f'no such group(s) in the local study set: {unknown}')
    shots = []
    for grammar in sorted(measured.glob('*/grammar.json')):
        data = read_json(grammar)
        if by_sha.get(data.get('video_sha256')) in groups:
            shots += reference_feel(grammar.parent, data)
    profile = build_profile(shots, name, user_words.strip(), groups)
    if PROFILE.is_file():   # learning the same reference again keeps the user's lean (pattern dynamics lean)
        old = read_json(PROFILE)
        if sorted(old.get('groups') or []) == sorted(groups):
            profile.update({k: old[k] for k in ('flat_below', 'lean_words', 'lean_at') if k in old})
    write_json(PROFILE, profile)
    return {'profile': str(PROFILE), 'name': name, 'shots': profile['shots'],
            'kinds': {k: {'share': v['share'], **{i: (v[i] or {}).get('p50') for i in ('scale', 'travel', 'parallax', 'length_s')}}
                      for k, v in profile['kinds'].items()}}


FEEL_FIELDS = ('ok', 'tx', 'ty', 'ds', 'rot', 'residual')   # what the first probe wrote; a re-probe must give the same


def _video_of(folder, sha):
    """The local reel a measured folder came from (references/<group>/<code>.mp4 beside measured/), by its hash."""
    from .common import file_hash
    for video in sorted(folder.parent.parent.glob(f'references/*/{folder.name}.mp4')):
        if file_hash(video) == sha:
            return video
    raise StudioError('INPUT_INVALID', f'{folder.name}: its reel is not in the local references (feel needs the pixels again)')


def reference_feel(folder, grammar):
    """grammar['shots'] with 'feel' (grammar_core.feel) from a re-probe that also keeps streak (motion_feel.json beside
    motion.json; motion.json is the study set's and is left as it is). The re-probe must reproduce motion.json's fields."""
    from . import grammar_core
    target = folder / 'motion_feel.json'
    if not target.is_file():
        rect = grammar['canvas']['content_rect']
        run_command([blender_binary(), '--background', '--factory-startup', '--python-exit-code', '1', '--python',
                     CODE / 'studio/blender_ops/grammar_probe.py', '--', _video_of(folder, grammar['video_sha256']), target,
                     *[str(v) for v in rect]], timeout=3600)
        old, new = read_json(folder / 'motion.json')['pairs'], read_json(target)['pairs']
        differ = [i for i, (a, b) in enumerate(zip(old, new)) if any(a.get(k) != b.get(k) for k in FEEL_FIELDS)]
        if len(old) != len(new) or differ:
            target.unlink()
            raise StudioError('QA_FAILED', f'{folder.name}: the re-probe differs from motion.json ({len(differ)} pairs, {len(old)} vs {len(new)})')
    pairs, media, rect = read_json(target)['pairs'], grammar['media'], grammar['canvas']['content_rect']
    aspect = (rect[3] - rect[1]) * media['height'] / ((rect[2] - rect[0]) * media['width'])
    out, before = [], None
    for shot in grammar['shots']:
        start = int(round(shot['start_s'] * media['fps']))
        end = start + int(round(shot['len_s'] * media['fps']))
        seg = pairs[start:max(start, end - 1)]   # as screen_grammar.assemble cuts a shot's pairs
        cam = shot.get('camera') or {}
        # how the camera crosses into this shot, at plain cuts that follow the shot before directly (no gap, no transition)
        crossing = grammar_core.join(before[1], seg, media['fps'])['kind'] \
            if before and shot.get('transition_in') == 'cut' and abs(before[0] - start) <= 1 else None
        out.append({**shot, 'join_in': crossing,
                    'feel': grammar_core.feel(seg, media['fps'], cam, shot.get('camera_envelope'), aspect, pixels=True)
                    if 'rate_per_s' in cam else None})
        before = (end, seg)
    return out


def lean(flat_below, user_words):
    """Ask for more (or less) motion than the reference itself: a shot reads flat below this percentile of the reference's
    shots of its kind (default 0.25). 2026-10-12, the user: the reference's numbers will do, "약간 더 역동적이면 좋을것같고"."""
    if not 0.05 <= flat_below <= 0.9:
        raise StudioError('INPUT_INVALID', 'pattern dynamics lean --flat-below is a percentile share between 0.05 and 0.9')
    if not user_words or len(user_words.strip()) < 4:
        raise StudioError('INPUT_INVALID', "pattern dynamics lean needs the user's words (--user-words)")
    profile = load_profile()
    profile.update({'flat_below': round(flat_below, 3), 'lean_words': user_words.strip(), 'lean_at': now()})
    write_json(PROFILE, profile)
    return {'flat_below': profile['flat_below'], 'lean_words': profile['lean_words']}


def load_profile():
    if not PROFILE.is_file():
        raise StudioError('INPUT_INVALID', "no camera dynamics profile: pattern dynamics learn --groups ... --user-words \"<the user's words>\"")
    return read_json(PROFILE)


RECIPES = REPO / 'library' / 'camera_recipes.json'   # named feels (shared vocabulary, our own words)


def recipes():
    return read_json(RECIPES)['recipes']


def _holds(condition, values):
    """True / False, or None when the value is unknown (and no 'else' stands in for it)."""
    value = values.get(condition['value'])
    if value is None:
        return _holds(condition['else'], values) if condition.get('else') else None
    if 'in' in condition:
        return value in condition['in']
    return (condition.get('min') is None or value >= condition['min']) and (condition.get('max') is None or value <= condition['max'])


def recipe(feel, kind, book=None):
    """{recipe, also}: the named feel a shot is (library/camera_recipes.json 'rule'); recipe None when none holds or two
    hold on equally many known values (mixed - 'also' lists them)."""
    values = {**(feel or {}), 'kind': kind}
    held = []
    for r in book if book is not None else recipes():
        checks = [(c, _holds(c, values)) for c in r['when']]
        known = [h for _, h in checks if h is not None]
        if any(c.get('key') and h is None for c, h in checks):   # the value that defines the feel is not measured: not this
            continue
        if len(known) >= r.get('min_known', 1) and all(known):
            held.append((len(known), r['id']))
    held.sort(key=lambda h: -h[0])
    if not held:
        return {'recipe': None, 'also': []}
    if len(held) > 1 and held[0][0] == held[1][0]:
        return {'recipe': None, 'also': [h[1] for h in held]}
    return {'recipe': held[0][1], 'also': [h[1] for h in held[1:]]}


def percentile(q, value):
    """Share of the profile's values below `value` (0..1), None without data."""
    if not q or value is None:
        return None
    values = q['values']
    return round(bisect.bisect_left(values, value) / len(values), 3)


MAIN = {'push': 'scale', 'travel': 'travel'}   # the number a kind of shot is mostly about


def judge(profile, measured):
    """{kind, percentiles, lines, words}: where a shot's camera sits among the profile's shots of its kind."""
    kind = kind_of(measured['camera'])
    feel = measured.get('feel')
    values = values_of(measured['camera'], measured.get('envelope'), measured.get('length_s'), feel)
    named = recipe(feel, kind) if feel else {'recipe': None, 'also': []}
    ref = profile['kinds'].get(kind)
    lines, words = [], [kind]
    if kind == 'unknown' or ref is None:
        return {'kind': kind, 'values': values, 'percentiles': {}, 'lines': [], 'words': f'{kind} (no reference shots of this kind)',
                'feel': feel, **named}
    pct = {item: percentile(ref.get(item), values.get(item)) for item in ITEMS if item != 'roll'}
    main = MAIN.get(kind)
    if kind == 'hold':   # a held shot is not flat by itself (the reference holds in some of its shots): the reel's share is judged
        words.append(f"the reference holds in {round(ref['share'] * 100)} % of its shots")
    elif main and pct.get(main) is not None:
        words.append(f"{main} p{round(pct[main] * 100)}")
        flat_below = profile.get('flat_below', FLAT_BELOW)   # the user may ask for more than the reference (pattern dynamics lean)
        # lively by speed, by things streaking past, or by a strong rush-and-settle: the same speed can feel unlike
        # (2026-10-12, the user: "숫자는 같아도 다른 느낌") - flat only when none of them is there
        lively = [pct[main] >= flat_below, (values.get('streak') or 0.0) >= STREAK_LIVELY,
                  (feel or {}).get('rhythm') == 'burst_settle' and (pct.get('burst_share') or 0.0) >= BURST_LIVELY]
        if not any(lively):
            lines.append(f"CAMERA_FLAT: {kind} {main} {values[main]:.3f}/s, below {round(flat_below * 100)} % of the reference's {kind} shots "
                         f"(median {ref[main]['p50']}), nothing streaks past and no rush-and-settle - move further or faster toward what "
                         "the shot explains, pass something near the lens, or say why it holds")
        elif pct[main] > HARSH_ABOVE:
            lines.append(f"CAMERA_HARSH: {kind} {main} {values[main]:.3f}/s, above {round(HARSH_ABOVE * 100)} % of the reference's {kind} shots "
                         f"(p95 {ref[main]['p95']}) - slow it or shorten the fast part")
    roll_pct = percentile(profile.get('roll'), values.get('roll'))
    if roll_pct is not None and roll_pct > HARSH_ABOVE and values['roll'] > ROLL_FLOOR_DEG:
        lines.append(f"CAMERA_HARSH: the picture turns {values['roll']:.1f} deg/s, above {round(HARSH_ABOVE * 100)} % of the reference's shots "
                     f"(p95 {profile['roll']['p95']}) - roll less, or over a longer stretch")
    streak_pct = percentile(profile.get('streak'), values.get('streak'))
    if streak_pct is not None and streak_pct > HARSH_ABOVE and values['streak'] > STREAK_FLOOR:
        lines.append(f"CAMERA_HARSH: {round(values['streak'] * 100)} % of the picture streaks past, above {round(HARSH_ABOVE * 100)} % of the "
                     f"reference's shots (p95 {profile['streak']['p95']}) - pass farther from what is near the lens, or slower")
    for item, label in (('parallax', 'parallax'), ('streak', 'streak'), ('length_s', 'length')):
        if pct.get(item) is not None:
            words.append(f"{label} p{round(pct[item] * 100)}")
    return {'kind': kind, 'values': values, 'percentiles': pct, 'lines': lines, 'words': ', '.join(words), 'feel': feel, **named}


def reel_lines(profile, measured):
    """The reel as a whole against the profile: a share of held shots well above the reference's reads static."""
    kinds = [kind_of(m['camera']) for m in measured if m]
    if not kinds or 'hold' not in profile['kinds']:
        return []
    ours, theirs = kinds.count('hold') / len(kinds), profile['kinds']['hold']['share']
    return [f"CAMERA_FLAT: {round(ours * 100)} % of the shots hold, the reference {round(theirs * 100)} % - give the held shots a "
            'push or a travel toward what they explain'] if ours > theirs + HOLD_MARGIN else []


HOLD_MARGIN = 0.15   # held shots above the reference's share by more than this read as a static reel
MONOTONE_SHARE = 0.5     # one recipe in more than half the reel's shots ...
MONOTONE_MARGIN = 0.15   # ... and this far above the reference's largest recipe share reads as one feel throughout


def recipe_mix(names):
    named = [n for n in names if n]
    return {k: round(named.count(k) / len(names), 3) for k in sorted(set(named))} if names else {}


ROUGH = ('reverse', 'turn')   # cuts where the camera keeps moving and changes direction. Not 'stop': the user's reference
                               # stops into a quarter of its cuts and holds or restarts at over half (2026-10-12) - a
                               # settle is its grammar; changing course mid-motion it does at 13 %
ROUGH_MARGIN = 0.15            # the reel's share of them this far above the reference's reads disconnected


def join_lines(profile, kinds):
    """JOINS_ROUGH: more of the reel's cuts snap back or stop dead than the reference's (2026-10-12, the user: "전체적으로
    뭔가 연결성이 없는건")."""
    theirs = (profile.get('joins') or {}).get('shares')
    if not kinds or theirs is None:
        return []
    ours = sum(kinds.count(k) for k in ROUGH) / len(kinds)
    ref = sum(theirs.get(k, 0.0) for k in ROUGH)
    return [f"JOINS_ROUGH: {round(ours * 100)} % of the cuts change the moving camera's direction, the reference {round(ref * 100)} % - "
            'settle before the cut, carry the direction over it, or make neighbouring shots one camera (camera.move.then)'] \
        if ours > ref + ROUGH_MARGIN else []


def recipe_lines(profile, names):
    """The reel's mix of feels against the reference's: RECIPE_MONOTONE when one recipe dominates far beyond any of the
    reference's (2026-10-12: same numbers, different feels - a reel of one feel reads flat whatever its speeds)."""
    ours = recipe_mix(names)
    theirs = ((profile.get('recipes') or {}).get('shares') or {})
    if not ours:
        return []
    top, share = max(ours.items(), key=lambda kv: kv[1])
    ceiling = max(theirs.values()) if theirs else 0.0
    return [f"RECIPE_MONOTONE: {round(share * 100)} % of the shots feel {top}, the reference's largest recipe {round(ceiling * 100)} % - "
            'make some of them another recipe'] if share > MONOTONE_SHARE and share > ceiling + MONOTONE_MARGIN else []


SYNTH_VERSION = 3   # grammar_synth.json schema this code reads in full (3: inside_view, camera_inside)


def measure_all(project, force=False):
    """Every shot's current version (what the brief's joins and feel lines read); a version measured by the current
    grammar_synth is kept unless force."""
    from .project import load_project, load_shot, project_dir, shot_path
    root = project_dir(project)
    done, kept, failed = [], [], []
    for entry in load_project(root)['shots']:
        shot = load_shot(root, entry['shot_id'])
        version = shot.get('scene_version')
        if not version:
            continue
        raw = shot_path(root, entry['shot_id']).parent / 'versions' / str(version) / 'grammar_synth.json'
        if not force and raw.is_file() and read_json(raw).get('schema_version') == SYNTH_VERSION:
            kept.append(entry['shot_id'])
            continue
        try:
            measure_version(root, entry['shot_id'], version)
            done.append(entry['shot_id'])
        except StudioError as e:
            failed.append({'shot_id': entry['shot_id'], 'error': str(e)[:300]})
    return {'measured': done, 'kept': kept, 'failed': failed}


def measured_ids(root, shot):
    """(subjects, parts) a scene measurement reads: the shot's subjects by the frame probe's one rule (blender.probe_inputs:
    the camera's target, rig subject, look target, shot.subjects, scene instances, screen.subject), and what it explains -
    screen.details, else the move's parts, else screen.subject. 2026-10-12: read from the move's target alone, every
    waypoints shot measured no subject and no inside."""
    from .blender import probe_inputs
    fps = load_project_fps(root)
    subjects = probe_inputs(root, shot, fps)['subjects']
    screen = shot.get('screen') or {}
    params = ((shot.get('camera') or {}).get('move') or {}).get('params') or {}
    parts = [d['id'] for d in screen.get('details', [])] or list(params.get('parts') or []) or list(screen.get('subject', []))
    return subjects, parts


def load_project_fps(root):
    from .project import load_project
    return load_project(root)['output']['fps']


def measure_version(project, shot_id, version=None, sky=True):
    """Measure one built version in its own scene (grammar_synth) -> versions/<v>/camera_dynamics.json, judged against
    the profile when there is one."""
    from . import grammar_core
    from .motion_style import shot_envelope
    from .project import load_shot, project_dir, shot_path
    root = project_dir(project)
    version = version or load_shot(root, shot_id).get('scene_version')
    folder = shot_path(root, shot_id).parent / 'versions' / str(version)
    scene = folder / 'scene.blend'
    if not scene.is_file():
        raise StudioError('INPUT_INVALID', f'{shot_id} {version}: no built scene')
    # the shot as that version was built, not as it reads now (2026-10-12: a 90-frame version measured over the 135 frames
    # of a later plan); the scene's own frame range bounds the measurement
    shot = read_json(folder / 'shot.snapshot.json')
    subjects, parts = measured_ids(root, shot)
    raw = folder / 'grammar_synth.json'
    from .world import element_links, load as load_world
    links = folder / 'grammar_links.json'   # the ids it measures may be world elements built as a subject's parts
    write_json(links, {'element_links': element_links(load_world(root))})
    run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(scene), '--python-exit-code', '1', '--python',
                 CODE / 'studio/blender_ops/grammar_synth.py', '--', raw, '192', '-', '-',
                 'sky' if sky else 'nosky', ','.join(subjects) or '-', ','.join(parts) or '-', links], timeout=1800)
    data = read_json(raw)
    camera = grammar_core.camera(data['pairs'], data['fps'])
    series = grammar_core.motion_series(data['pairs'])
    try:
        envelope = shot_envelope([v * 1000 for v in series], data['fps']) if len(series) >= 4 else None
    except StudioError:
        envelope = None
    measured = {'shot_id': shot_id, 'version': version, 'camera': camera,
                'envelope': {k: envelope[k] for k in ('burst_share', 'peak_t', 'hold_frac', 'dwell_t')} if envelope else None,
                'length_s': round(shot['duration_frames'] / data['fps'], 3), 'measured_at': now(), 'method': data['method']}
    measured['feel'] = grammar_core.feel(data['pairs'], data['fps'], camera, measured['envelope'], data['height'] / data['width'])
    from .camera_comfort import lines as comfort_lines, peaks
    fastest = peaks(data['pairs'], data['fps'])   # the camera's own fastest half second, against the comfort goal
    measured['comfort'] = {**fastest, 'lines': comfort_lines(fastest)} if fastest else None
    if PROFILE.is_file():
        measured['judged'] = judge(load_profile(), measured)
    write_json(folder / 'camera_dynamics.json', measured)
    return measured


def _measure_command(a):
    if a.all:
        return measure_all(a.project, a.force)
    if not a.shot:
        raise StudioError('INPUT_INVALID', 'pattern dynamics measure: --shot S or --all')
    return measure_version(a.project, a.shot, a.version)


def register_commands(commands):
    """`pattern dynamics ...` (registered by patterns.register_commands)."""
    p = commands.add_parser('dynamics', help="Camera dynamics against the user's reference profile: learn, show, measure")
    sub = p.add_subparsers(dest='dynamics_command', required=True)
    q = sub.add_parser('learn', help='the reference profile from local measured reels of the given group(s)')
    q.add_argument('--groups', required=True, help='local group labels, comma separated'); q.add_argument('--name', required=True)
    q.add_argument('--user-words', required=True)
    q.set_defaults(handler=lambda a: learn([g.strip() for g in a.groups.split(',') if g.strip()], a.name, a.user_words))
    q = sub.add_parser('lean', help="read shots as flat below this percentile of the reference (the user's words): more, or less, motion")
    q.add_argument('--flat-below', type=float, required=True); q.add_argument('--user-words', required=True)
    q.set_defaults(handler=lambda a: lean(a.flat_below, a.user_words))
    q = sub.add_parser('show')
    q.set_defaults(handler=lambda a: {k: v for k, v in load_profile().items() if k != 'groups'} | {
        'kinds': {k: {i: (x if not isinstance(x, dict) else {kk: vv for kk, vv in x.items() if kk != 'values'}) for i, x in v.items()}
                  for k, v in load_profile()['kinds'].items()}})
    q = sub.add_parser('measure', help='one built version (or --all: every shot\'s current one), measured in its scene and judged against the profile')
    q.add_argument('--project', required=True); q.add_argument('--shot'); q.add_argument('--version')
    q.add_argument('--all', action='store_true'); q.add_argument('--force', action='store_true', help='with --all: measure again what is current')
    q.set_defaults(handler=_measure_command)
