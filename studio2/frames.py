"""A cut's pictures: its first, middle and last frames (and a moving thing drawn on its own), made by the image tool for
the cut as settled, each try kept, checked, and picked by the deciding agent - the engine guards, it does not choose.

Where: shots/<cut>/frames/<picture_sha>/ - one folder per version of what the cut's pictures are drawn from
(studio2/cuts.picture_sha): a cut drawn anew starts anew and an old picture never stands for a new one; a cut whose
camera, labels or timing changed keeps its pictures. In it <frame>_<n>.png with <frame>_<n>.json (what made it:
studio2/images.py), checks/ (studio2/checks.py), locate/ (studio2/locate.py) and picked.json.

Order: the first picture first; the middle and last are edits of the picked first (what changes, nothing else); a cut at
a place an earlier cut showed takes that cut's picked first as its place reference, and every other cut takes the reel's
first as its style reference - so pictures are made in cut order. A try found wrong is made again with what was wrong
(--reason), at most RETRIES times; past that it is a finding to write up, not another try.
"""
from __future__ import annotations

from pathlib import Path

from studio.common import StudioError, file_hash, now, read_json, write_json
from studio.project import project_dir, shot_path

RETRIES = 2                      # tries made again with a reason, after the first (the plan: at most 2, then a finding)
EDITS = ('middle', 'last')       # made from the picked first: the change and nothing else


def settled(path):
    from studio import decisions
    decisions.require(path, 'cut_pictures')
    return decisions.envelope(path, 'cuts')['body']


def cut_of(body, cut_id):
    cut = next((c for c in body['cuts'] if c['cut_id'] == cut_id), None)
    if cut is None:
        raise StudioError('INPUT_INVALID', f"no cut {cut_id} in the cuts layer ({', '.join(c['cut_id'] for c in body['cuts'])})")
    return cut


def folder(path, cut, body=None):
    """shots/<cut>/frames/<picture_sha>: one folder per version of what the cut's pictures are drawn from."""
    from studio import decisions
    from .cuts import picture_sha
    body = body or decisions.envelope(path, 'cuts')['body']
    return shot_path(path, cut['cut_id']).parent / 'frames' / picture_sha(cut, body)


def needed(cut):
    """The pictures a cut is made of, in the order they are made."""
    from .cuts import in_pictures
    out = ['first']
    if in_pictures(cut['change']):
        out += [f for f in EDITS if f in cut['frames']]
    element = cut['change'].get('element')
    if element and element['drawn'] == 'sprite':
        out.append('sprite')
    return out


def cross_window(cut):
    """[start, end] (shares of the cut) over which the cut's pictures cross from the first to the last, or None when it is
    one picture: a state's change, or a thing of the scene painted where it starts and where it ends (its own window)."""
    from .cuts import in_pictures
    change = cut['change']
    if not in_pictures(change):
        return None
    at = change.get('at') or [0.0, 1.0]
    element = change.get('element') or {}
    return list(element.get('at') or at) if element.get('drawn') == 'picture' else list(at)


def picture_at(cut, share):
    """The picture the cut shows at `share` of it: the first until its pictures cross, the last after, the middle at the
    crossing's half - the nearest of them inside it."""
    window = cross_window(cut)
    shown = [f for f in needed(cut) if f in ('first', *EDITS)]
    if window is None or len(shown) == 1:
        return 'first'
    a, b = window
    times = {'first': a, 'middle': (a + b) / 2, 'last': b}
    return min(shown, key=lambda f: (abs(times[f] - share), -times[f]))


def flow_picture(cut, n):
    """The picture flow n runs over: the one shown when it starts (its `at`; a flow that runs all along, the first)."""
    return picture_at(cut, (cut['cutaway']['flow'][n].get('at') or [0.0, 1.0])[0])


def tries(path, cut, frame):
    """[(n, png, record)] of a frame's tries, oldest first."""
    base = folder(path, cut)
    out = []
    for png in sorted(base.glob(f'{frame}_*.png'), key=lambda p: int(p.stem.rsplit('_', 1)[1])):
        record = png.with_suffix('.json')
        if record.is_file() and read_json(record).get('ok'):
            out.append((int(png.stem.rsplit('_', 1)[1]), png, read_json(record)))
    return out


def of_first(path, cut, record):
    """Whether an edit's try was made from the cut's picked first picture (its record names the picture it started
    from): an edit belongs to that first - made again from another, it is that first's no more (the fab proof's c14,
    2026-10-27: the old last stayed picked after a new first was)."""
    first = folder(path, cut) / f"first_{(picks(path, cut).get('first') or {}).get('try')}.png"
    started = ((record or {}).get('inputs') or [{}])[0].get('sha256')
    return first.is_file() and started == file_hash(first)


def current(path, cut, frame):
    """[(n, png, record)] of the tries that stand: every try of a first or a sprite, the edits of the picked first."""
    have = tries(path, cut, frame)
    return [t for t in have if of_first(path, cut, t[2])] if frame in EDITS else have


def picks(path, cut):
    file = folder(path, cut) / 'picked.json'
    return read_json(file) if file.is_file() else {}


def picked(path, cut, frame):
    """The picked try's picture of a frame, or None."""
    row = picks(path, cut).get(frame)
    if not row:
        return None
    png = folder(path, cut) / f"{frame}_{row['try']}.png"
    if frame in EDITS and png.is_file() and not of_first(path, cut, read_json(png.with_suffix('.json'))):
        return None   # an edit of another first picture
    return png if png.is_file() else None


def chain_ref(path, body, cut):
    """(place_ref, style_ref) a cut's first picture is drawn from: the picked first of the earliest cut before it at the
    same place, else the reel's first cut's picked first as a style reference; neither for the reel's first cut."""
    source = reference_cut(body, cut)
    if source is None:
        return None, None
    same = source['place'] == cut['place']
    ref = picked(path, source, 'first')
    if ref is None:
        raise StudioError('PLACE_REF_UNPICKED', f"{cut['cut_id']} is drawn from {source['cut_id']}'s first picture, which is not picked yet",
                          recovery=f"make, check and pick {source['cut_id']}'s first picture first (pictures are made in cut order)")
    return (ref, None) if same else (None, ref)


def reference_cut(body, cut):
    """The cut whose first picture is this cut's reference (chain_ref), or None."""
    order = [c['cut_id'] for c in body['cuts']]
    before = body['cuts'][:order.index(cut['cut_id'])]
    return next((c for c in before if c['place'] == cut['place']), None) or (before[0] if before else None)


def prompt_of(path, body, cut, frame, reason=None):
    from . import prompts
    from .grammar import load
    if frame == 'first':
        place_ref, style_ref = chain_ref(path, body, cut)
        source = reference_cut(body, cut)
        return prompts.first(cut, body, load(), place_ref, style_ref, reason, source['scale'] if source else None)
    if frame == 'sprite':
        return prompts.sprite(cut, body, reason)
    first = picked(path, cut, 'first')
    if first is None:
        raise StudioError('FIRST_UNPICKED', f"{cut['cut_id']} {frame}: an edit of the picked first picture - none is picked",
                          recovery=f"studio2 frames check, then frames pick --cut {cut['cut_id']} --frame first --try N --why ...")
    return prompts.edit(cut, frame, first, reason)


def make(project, cut_id, frame=None, reason=None, runner=None):
    """Make the cut's pictures not made yet (or one frame). A frame already made is made again only with a reason (what
    the last try got wrong), at most RETRIES times. An edit waits for the first to be picked."""
    from . import images
    path = project_dir(project)
    body = settled(path)
    cut = cut_of(body, cut_id)
    wanted = [frame] if frame else needed(cut)
    unknown = [f for f in wanted if f not in needed(cut)]
    if unknown:
        raise StudioError('INPUT_INVALID', f"{cut_id}: it is made of {needed(cut)}, not {unknown}")
    made, waiting = [], []
    for name in wanted:
        have = current(path, cut, name)
        if have and not reason:
            continue
        if have and len(have) > RETRIES:
            raise StudioError('TRIES_SPENT', f"{cut_id} {name}: {len(have)} tries - write up what keeps failing (the checks, the prompt) "
                              'instead of trying again', recovery='a finding with its evidence; or change the cut (cuts write) and start anew')
        try:
            prompt, inputs = prompt_of(path, body, cut, name, reason if have else None)
        except StudioError as error:
            if error.code == 'FIRST_UNPICKED' and frame is None:
                waiting.append(f'{name}: {error.message}')
                continue
            raise
        every = tries(path, cut, name)
        n = (every[-1][0] if every else 0) + 1
        target = folder(path, cut) / f'{name}_{n}.png'
        row = images.make(target, prompt, inputs, runner=runner, label=f'{cut_id}/{name}_{n}')
        made.append({'frame': name, 'try': n, 'path': str(target), 'seconds': row.get('seconds'), 'reason': reason if have else None})
        write_json(target.with_suffix('.why.json'), {'reason': reason if have else None, 'at': now()})
    return {'cut': cut_id, 'made': made, 'waiting': waiting, 'folder': str(folder(path, cut)),
            'next': ['studio2 frames check --cut ' + cut_id] + ([f'pick the first, then frames make --cut {cut_id} again'] if waiting else []),
            'artifacts': [m['path'] for m in made]}


def pick(project, cut_id, frame, try_n, why):
    """The deciding agent's pick of a frame's try, with why. Refused when the try failed a calibrated floor
    (studio2/checks.py FLOORS) or was never checked."""
    from .checks import floor_failures, result_of
    path = project_dir(project)
    body = settled(path)
    cut = cut_of(body, cut_id)
    if not why or len(why.strip()) < 12:
        raise StudioError('INPUT_INVALID', 'frames pick --why: a sentence - what the picture shows that the cut needs')
    png = folder(path, cut) / f'{frame}_{try_n}.png'
    if not png.is_file():
        raise StudioError('INPUT_INVALID', f'{cut_id} {frame}: no try {try_n}')
    if frame in EDITS and not of_first(path, cut, read_json(png.with_suffix('.json'))):
        raise StudioError('EDIT_STALE', f'{cut_id} {frame}_{try_n} was made from another first picture than the picked one',
                          recovery=f'studio2 frames make --cut {cut_id} (an edit of the picked first), then pick it')
    result = result_of(path, cut, frame, try_n)
    if result is None:
        raise StudioError('PICTURE_UNCHECKED', f'{cut_id} {frame}_{try_n} was not checked', recovery=f'studio2 frames check --cut {cut_id}')
    failed = floor_failures(result)
    if failed:
        raise StudioError('PICTURE_FLOOR', f"{cut_id} {frame}_{try_n} fails {failed}: make it again with the reason "
                          f"(frames make --cut {cut_id} --frame {frame} --reason ...)")
    data = picks(path, cut)
    data[frame] = {'try': try_n, 'why': why.strip(), 'at': now(), 'by': 'agent'}
    write_json(folder(path, cut) / 'picked.json', data)
    return {'cut': cut_id, 'picked': data, 'warnings': [f"{frame}_{try_n}: {w}" for w in result.get('warnings', [])]}


def ready(project):
    """What can be drawn now, so the image tool keeps drawing while the readers check and locate another cut (the image
    tool draws one picture at a time on the machine, the readers read apart from it): {ready: the pictures not made whose
    reference is picked, in cut order; made: tries standing that are not picked yet (check and pick them); waiting: the
    others, each with the picture it waits on}. A first waits on the picked first of its reference cut (chain_ref), an edit
    on its own cut's first; a picked picture is done."""
    path = project_dir(project)
    body = settled(path)
    out = {'ready': [], 'made': [], 'waiting': []}
    for cut in body['cuts']:
        for name in needed(cut):
            if picked(path, cut, name) is not None:
                continue
            standing = current(path, cut, name)
            if standing:
                out['made'].append({'cut': cut['cut_id'], 'frame': name, 'tries': [n for n, _p, _r in standing]})
                continue
            if name in EDITS:
                on = None if picked(path, cut, 'first') is not None else cut['cut_id']
            elif name == 'first':
                source = reference_cut(body, cut)
                on = source['cut_id'] if source is not None and picked(path, source, 'first') is None else None
            else:
                on = None
            row = {'cut': cut['cut_id'], 'frame': name}
            if on:
                out['waiting'].append({**row, 'on': f'{on} first'})
            else:
                out['ready'].append(row)
    out['next'] = ([f"studio2 frames make --cut {out['ready'][0]['cut']} (in the background while the readers work)"] if out['ready'] else []) \
        + [f"studio2 checks run --cut {m['cut']}" for m in out['made'][:1]]
    return out


def status(project):
    from .checks import result_of
    path = project_dir(project)
    body = settled(path)
    out = []
    for cut in body['cuts']:
        rows = {}
        for name in needed(cut):
            rows[name] = {'tries': [{'try': n, 'checked': result_of(path, cut, name, n) is not None,
                                     'failures': (result_of(path, cut, name, n) or {}).get('failures')} for n, _p, _r in tries(path, cut, name)],
                          'picked': (picks(path, cut).get(name) or {}).get('try')}
        out.append({'cut': cut['cut_id'], 'frames': rows})
    return {'cuts': out}


def register_commands(subparsers):
    parser = subparsers.add_parser('frames', help="A cut's first, middle and last pictures: made, checked, picked")
    commands = parser.add_subparsers(dest='frames_command', required=True)
    p = commands.add_parser('prompt', help='the prompt a frame would be made from (nothing is made)')
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True); p.add_argument('--frame', default='first')
    p.add_argument('--reason')

    def prompt_handler(a):
        path = project_dir(a.project)
        body = settled(path)
        cut = cut_of(body, a.cut)
        prompt, inputs = prompt_of(path, body, cut, a.frame, a.reason)
        return {'prompt': prompt, 'inputs': [str(i) for i in inputs]}
    p.set_defaults(handler=prompt_handler)
    p = commands.add_parser('make', help="make the cut's pictures not made yet (or --frame); --reason makes a try again with what was wrong")
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True); p.add_argument('--frame')
    p.add_argument('--reason')
    p.set_defaults(handler=lambda a: make(a.project, a.cut, a.frame, a.reason))
    p = commands.add_parser('pick', help="the deciding agent's pick of a checked try, with why")
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True); p.add_argument('--frame', required=True)
    p.add_argument('--try', dest='try_n', type=int, required=True); p.add_argument('--why', required=True)
    p.set_defaults(handler=lambda a: pick(a.project, a.cut, a.frame, a.try_n, a.why))
    p = commands.add_parser('status'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: status(a.project))
    p = commands.add_parser('ready', help='the pictures that can be drawn now (their reference picked), the tries waiting for a '
                                          'pick, and what the rest wait on')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: ready(a.project))
    return commands
