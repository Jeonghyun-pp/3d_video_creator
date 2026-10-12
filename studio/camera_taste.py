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

from .common import REPO, StudioError, blender_binary, now, read_json, run_command, write_json

PROFILE = REPO / '.studio' / 'camera_dynamics.json'   # local, never committed: the user's reference and its numbers
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.95)
FLAT_BELOW = 0.25      # below this percentile of the profile's own shots of the kind: reads flat beside them
HARSH_ABOVE = 0.95     # above this percentile: faster than nearly all of them (comfort first, the user's rule 2026-10-08)
ROLL_FLOOR_DEG = 2.0   # turning slower than this per second is never called harsh, whatever the reference
KINDS = {'still': 'hold', 'scale_in': 'push', 'scale_out': 'push', 'x': 'travel', 'y': 'travel', 'compound': 'travel', 'roll': 'travel'}


def kind_of(camera):
    return KINDS.get((camera or {}).get('axis'), 'unknown')


def values_of(camera, envelope=None, length_s=None):
    """The numbers a shot is compared on (reference units: content-width shares and log-scale per second)."""
    r = (camera or {}).get('rate_per_s') or {}
    return {'scale': abs(r.get('scale', 0.0)), 'travel': math.hypot(r.get('x', 0.0), r.get('y', 0.0)), 'roll': abs(r.get('roll_deg', 0.0)),
            'parallax': (camera or {}).get('parallax_per_s', 0.0),
            **({'burst_share': envelope.get('burst_share'), 'hold_frac': envelope.get('hold_frac')} if envelope else {}),
            **({'length_s': length_s} if length_s is not None else {})}


def _quantiles(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    pick = lambda q: values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))]  # noqa: E731
    return {f'p{round(q * 100)}': round(pick(q), 5) for q in QUANTILES} | {'n': len(values), 'values': [round(v, 5) for v in values]}


def build_profile(shots, name, user_words, groups):
    """shots: [{camera, camera_envelope, len_s}] of the chosen reels -> the profile dict."""
    by_kind = {}
    for s in shots:
        cam = s.get('camera') or {}
        if 'rate_per_s' not in cam:
            continue
        by_kind.setdefault(kind_of(cam), []).append(values_of(cam, s.get('camera_envelope'), s.get('len_s')))
    total = sum(len(v) for v in by_kind.values())
    if not total:
        raise StudioError('INPUT_INVALID', 'no measured shots in the chosen group(s)')
    kinds = {}
    for kind, rows in by_kind.items():
        kinds[kind] = {'share': round(len(rows) / total, 3),
                       **{item: _quantiles([r.get(item) for r in rows]) for item in ('scale', 'travel', 'roll', 'parallax', 'burst_share', 'hold_frac', 'length_s')}}
    every = [r.get('roll') for rows in by_kind.values() for r in rows]
    return {'schema_version': 1, 'name': name, 'groups': list(groups), 'user_words': user_words, 'shots': total, 'kinds': kinds,
            'roll': _quantiles(every),   # turning about the view: judged over all shots (no kind is about rolling)
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
            shots += data['shots']
    profile = build_profile(shots, name, user_words.strip(), groups)
    write_json(PROFILE, profile)
    return {'profile': str(PROFILE), 'name': name, 'shots': profile['shots'],
            'kinds': {k: {'share': v['share'], **{i: (v[i] or {}).get('p50') for i in ('scale', 'travel', 'parallax', 'length_s')}}
                      for k, v in profile['kinds'].items()}}


def lean(flat_below, user_words):
    """Ask for more (or less) motion than the reference itself: a shot reads flat below this percentile of the reference's
    shots of its kind (default 0.25). 2026-10-12, the user: "건축해부도 숫자로 써도 될것같긴한데, 약간 더 역동적이면 좋을것같고"."""
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
    values = values_of(measured['camera'], measured.get('envelope'), measured.get('length_s'))
    ref = profile['kinds'].get(kind)
    lines, words = [], [kind]
    if kind == 'unknown' or ref is None:
        return {'kind': kind, 'values': values, 'percentiles': {}, 'lines': [], 'words': f'{kind} (no reference shots of this kind)'}
    pct = {item: percentile(ref.get(item), values.get(item)) for item in ('scale', 'travel', 'parallax', 'burst_share', 'hold_frac', 'length_s')}
    main = MAIN.get(kind)
    if kind == 'hold':   # a held shot is not flat by itself (the reference holds in some of its shots): the reel's share is judged
        words.append(f"the reference holds in {round(ref['share'] * 100)} % of its shots")
    elif main and pct.get(main) is not None:
        words.append(f"{main} p{round(pct[main] * 100)}")
        flat_below = profile.get('flat_below', FLAT_BELOW)   # the user may ask for more than the reference (pattern dynamics lean)
        if pct[main] < flat_below:
            lines.append(f"CAMERA_FLAT: {kind} {main} {values[main]:.3f}/s, below {round(flat_below * 100)} % of the reference's {kind} shots "
                         f"(median {ref[main]['p50']}) - move further or faster toward what the shot explains, or say why it holds")
        elif pct[main] > HARSH_ABOVE:
            lines.append(f"CAMERA_HARSH: {kind} {main} {values[main]:.3f}/s, above {round(HARSH_ABOVE * 100)} % of the reference's {kind} shots "
                         f"(p95 {ref[main]['p95']}) - slow it or shorten the fast part")
    roll_pct = percentile(profile.get('roll'), values.get('roll'))
    if roll_pct is not None and roll_pct > HARSH_ABOVE and values['roll'] > ROLL_FLOOR_DEG:
        lines.append(f"CAMERA_HARSH: the picture turns {values['roll']:.1f} deg/s, above {round(HARSH_ABOVE * 100)} % of the reference's shots "
                     f"(p95 {profile['roll']['p95']}) - roll less, or over a longer stretch")
    for item, label in (('parallax', 'parallax'), ('length_s', 'length')):
        if pct.get(item) is not None:
            words.append(f"{label} p{round(pct[item] * 100)}")
    return {'kind': kind, 'values': values, 'percentiles': pct, 'lines': lines, 'words': ', '.join(words)}


def reel_lines(profile, measured):
    """The reel as a whole against the profile: a share of held shots well above the reference's reads static."""
    kinds = [kind_of(m['camera']) for m in measured if m]
    if not kinds or 'hold' not in profile['kinds']:
        return []
    ours, theirs = kinds.count('hold') / len(kinds), profile['kinds']['hold']['share']
    return [f"CAMERA_FLAT: {round(ours * 100)} % of the shots hold, the reference {round(theirs * 100)} % - give the held shots a "
            'push or a travel toward what they explain'] if ours > theirs + HOLD_MARGIN else []


HOLD_MARGIN = 0.15   # held shots above the reference's share by more than this read as a static reel


def measure_version(project, shot_id, version=None, sky=True):
    """Measure one built version in its own scene (grammar_synth) -> versions/<v>/camera_dynamics.json, judged against
    the profile when there is one."""
    from . import grammar_core
    from .motion_style import shot_envelope
    from .project import load_shot, project_dir, shot_path
    root = project_dir(project)
    shot = load_shot(root, shot_id)
    version = version or shot.get('scene_version')
    folder = shot_path(root, shot_id).parent / 'versions' / str(version)
    scene = folder / 'scene.blend'
    if not scene.is_file():
        raise StudioError('INPUT_INVALID', f'{shot_id} {version}: no built scene')
    raw = folder / 'grammar_synth.json'
    run_command([blender_binary(), '--background', str(scene), '--python-exit-code', '1', '--python',
                 REPO / 'studio/blender_ops/grammar_synth.py', '--', raw, '192', '1', str(shot['duration_frames']),
                 'sky' if sky else 'nosky'], timeout=1800)
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
    if PROFILE.is_file():
        measured['judged'] = judge(load_profile(), measured)
    write_json(folder / 'camera_dynamics.json', measured)
    return measured


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
    q = sub.add_parser('measure', help='one built version, measured in its scene and judged against the profile')
    q.add_argument('--project', required=True); q.add_argument('--shot', required=True); q.add_argument('--version')
    q.set_defaults(handler=lambda a: measure_version(a.project, a.shot, a.version))
