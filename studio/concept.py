"""Concept frames: the picture a shot is built toward, made before any 3D - one per shot, picked by the user (or, in a
delegated run, by the agent with its reason).

Why (floor_noise, 2026-10-09): with no reference reel nothing said what a shot should look like, so the build was judged
by its checks alone and stopped at a blockout - "a doll's house floating in a factory". A concept frame is the shot's own
reference: the storyboard sheet shows it beside the built frames, reference_critique compares against it by default,
and qa collect measures the cut against it.

A concept is an image the agent made (Codex's built-in image tool: no key, the subscription) or any image inside the
project. It is copied to concepts/<shot>/cNN.png with its provenance. Since 2026-10-10 (the user: "모든 콘셉트 허용")
a picked concept may go to a paid model as a reference picture - a clay-filled shot needs it - while third-party
reference stills stay refused by hash (routing.never_sent). The pick is bound to the approved shot list (like a fill
brief) and to the image's hash.

Frame pictures (2026-10-11): a shot may also hold pictures of its first and last moment (`--frame start|end`, or a
`key` moment `--at` between). The user: "샷별로 첫 장면과 마지막 장면을 그림으로 그리고 그사이를 움직임으로 메꾸고".
They are composition targets: frame_targets.py measures where the subject sits in each (box, horizon, looking up or
down), the camera is solved to reproduce them, and the built frames at the same moments are measured against them.
The shape of the subject never comes from them (the Blender model keeps it). A beat picture that already shows the
moment is reused (`--from-beat b1/p01`) rather than drawn again.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .common import StudioError, check_id, file_hash, now, read_json, safe_path, write_json
from .project import load_project, load_shot, project_dir

THUMB = (240, 427)
# How the pictures of one moment differ, drawn by the code (story_core.pick_directions, the same draw the story uses):
# a model asked for three pictures of one moment draws one picture three times (2026-10-17: the rocket's concepts and
# boards came out as one upright booster mid-frame). How the moment shows its parts (the shot list's show) is decided
# already and is the same in every variant; these say where the camera stands and what is around.
CONCEPT_AXES = {
    'size': {'means': 'how much of the frame the subject takes', 'values': {
        'wide': {'means': 'the whole subject small in its place, what is around it showing how big it is', 'draw': 'a wide shot: the subject small, the place around it large'},
        'medium': {'means': 'the subject about half the frame, some of its place around it', 'draw': 'a medium shot: the subject about half the frame tall, room around it'},
        'close': {'means': 'the part that matters fills most of the frame', 'draw': 'a close shot: the part that matters fills most of the frame'}}},
    'view': {'means': 'where the camera looks from', 'values': {
        'high': {'means': 'from above, looking down', 'draw': 'seen from above, looking down on it'},
        'eye': {'means': 'level with it', 'draw': 'seen level with it'},
        'low': {'means': 'from below, looking up', 'draw': 'seen from below, looking up at it'}}},
    'place': {'means': 'what else is in the picture', 'values': {
        'with_env': {'means': 'its surroundings and something of known size', 'draw': 'its surroundings in view, with something of known size (a person, a vehicle, a building) for scale'},
        'isolated': {'means': 'the subject alone, nothing to distract', 'draw': 'the subject alone against a quiet background'}}},
}
VARIANTS = 3   # pictures drawn per moment when variants are asked for: the user's options page size (story.DIRECTION_COUNT)


def _folder(path, shot_id):
    return path / 'concepts' / shot_id


def _record_path(path, shot_id):
    return path / 'decisions' / 'concept' / f'{shot_id}.json'


def concepts(path, shot_id):
    folder = _folder(path, shot_id)
    return [read_json(p) for p in sorted(folder.glob('c*.json'))] if folder.is_dir() else []


FRAME_ROLES = {'start': 0.0, 'end': 1.0, 'key': None}   # a key moment says when (--at, strictly between)


def _frame(frame, at):
    if frame is None:
        if at is not None:
            raise StudioError('INPUT_INVALID', '--at belongs to a frame picture: give --frame start|end|key')
        return None
    if frame not in FRAME_ROLES:
        raise StudioError('INPUT_INVALID', f'--frame {frame!r}: one of {sorted(FRAME_ROLES)}')
    fixed = FRAME_ROLES[frame]
    if fixed is not None:
        if at is not None and abs(float(at) - fixed) > 1e-9:
            raise StudioError('INPUT_INVALID', f'a {frame} picture is at {fixed} of the shot; a moment between is --frame key --at')
        return {'role': frame, 'at': fixed}
    if at is None or not 0.0 < float(at) < 1.0:
        raise StudioError('INPUT_INVALID', 'a key frame picture needs --at strictly between 0 and 1 (the share of the shot)')
    return {'role': 'key', 'at': round(float(at), 4)}


def _beat_source(path, ref):
    """'b1/p01' -> the beat picture's file (it must be in the approved world body, by hash)."""
    beat_id, _, picture_id = str(ref).partition('/')
    if not picture_id:
        raise StudioError('INPUT_INVALID', f'--from-beat {ref!r}: a beat picture is <beat>/<picture> (b1/p01)')
    record_file = path / 'concepts' / 'world' / check_id(beat_id) / f'{check_id(picture_id)}.json'
    if not record_file.is_file():
        raise StudioError('INPUT_INVALID', f'--from-beat {ref!r}: no beat picture concepts/world/{beat_id}/{picture_id or "pNN"}')
    record = read_json(record_file)
    source = safe_path(path, record['path'])
    if file_hash(source) != record['sha256']:
        raise StudioError('CONCEPT_STALE', f'beat picture {ref} changed after it was added')
    return source, record


def _when(text):
    from datetime import datetime, timezone
    try:
        stamp = datetime.fromisoformat(str(text))
    except ValueError:
        stamp = datetime.min
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _moment_key(at):
    return f'{float(at):.4f}'


def frames(path, shot_id):
    """The shot's frame pictures in time order: per moment, the latest decision about it - a picture added (newest
    wins, an older one stays on file), a variant chosen (concept pick, a redo), or a pick of the moment (concept pick of
    any picture there, kept in the pick record's moments). A variant drawn among others counts only once chosen; one
    redrawn (variant.replaced_by) never. Why (2026-10-17 review): a pick of an older picture lost to a newer variant."""
    path = project_dir(path)
    events = {}

    def offer(record, when):
        at, stamp = record['frame']['at'], _when(when)
        if at not in events or stamp >= events[at][0]:
            events[at] = (stamp, record)
    records = {c['concept_id']: c for c in concepts(path, shot_id)}
    for record in records.values():   # in id order: of two pictures added at once, the later id wins
        if not record.get('frame'):
            continue
        variant = record.get('variant')
        if variant:
            if variant.get('chosen') and not variant.get('replaced_by'):
                offer(record, variant['chosen'].get('at'))
            continue
        offer(record, record.get('created_at'))
    file = _record_path(path, shot_id)
    for row in ((read_json(file).get('moments') or {}).values() if file.is_file() else []):
        record = records.get(row['concept_id'])
        if record and record.get('frame') and record['sha256'] == row['sha256']:
            offer(record, row['decision']['at'])
    return [events[at][1] for at in sorted(events)]


def frame_at(path, shot_id, at):
    """The moment's current frame picture at time `at` (the newest one there), or None: a moment is named by its time,
    since a newer picture of it supersedes the older one's id (studio/picture3d.py records one per run)."""
    return next((c for c in frames(path, shot_id) if abs(float(c['frame']['at']) - float(at)) < 1e-6), None)


EXTRA_KEYS = ('pose', 'register', 'proposal', 'variant')   # what a picture painted over the 3D adds to its record (studio/picture3d.py);
# variant: the axis values it was drawn to (concept brief --variants)


def add(project, shot_id, image=None, prompt=None, source='codex_image_gen', frame=None, at=None, from_beat=None, derived_from=None,
        extra=None, redraw_of=None):
    """Copy a picture in as a concept of the shot. extra: EXTRA_KEYS of a picture painted over the 3D (its pose, its fit
    onto the clay, and that it is the agent's proposal - never a pick)."""
    from PIL import Image
    unknown = sorted(set(extra or {}) - set(EXTRA_KEYS))
    if unknown:
        raise StudioError('INPUT_INVALID', f'concept add: unknown record fields {unknown} (known: {list(EXTRA_KEYS)})')
    path = project_dir(project)
    load_shot(path, shot_id)                      # the shot exists
    moment = _frame(frame, at)
    if (extra or {}).get('variant'):
        if not moment:
            raise StudioError('INPUT_INVALID', 'a variant is a picture of a moment: give --frame')
        extra = {**extra, 'variant': _variant_extra(path, shot_id, moment, extra['variant'], redraw_of)}
    elif redraw_of:
        raise StudioError('INPUT_INVALID', '--redraw-of redraws a variant: give --variant too')
    beat = None
    if from_beat:
        if image:
            raise StudioError('INPUT_INVALID', 'give --image or --from-beat, not both')
        image, beat = _beat_source(path, from_beat)
        prompt, source = prompt or beat.get('prompt'), 'world_beat'
    if image is None:
        raise StudioError('INPUT_INVALID', 'concept add needs --image (or --from-beat b1/p01)')
    src = Path(image).expanduser().resolve()
    if not src.is_file():
        raise StudioError('INPUT_INVALID', f'no image {image}')
    try:
        with Image.open(src) as probe:
            size = list(probe.size)
    except OSError as exc:
        raise StudioError('INPUT_INVALID', f'{image} is not an image: {exc}') from None
    derived = None
    if derived_from:   # a picture painted over the built frame (frames.md: draw over the model) keeps what it was painted on
        base = Path(derived_from).expanduser().resolve()
        if not base.is_file() or not moment:
            raise StudioError('INPUT_INVALID', '--derived-from needs the built frame image it was painted over, and --frame')
        before = [c for c in frames(path, shot_id) if c['frame'] == moment]
        derived = {'path': str(base.relative_to(path)) if base.is_relative_to(path) else str(base), 'sha256': file_hash(base),
                   **({'supersedes': before[-1]['concept_id']} if before else {})}
    folder = _folder(path, shot_id)
    folder.mkdir(parents=True, exist_ok=True)
    taken = [int(p.stem[1:]) for p in folder.glob('c*.json') if p.stem[1:].isdigit()]
    concept_id = f'c{max(taken, default=0) + 1:02d}'   # never reuses an id, even after one was removed
    target = folder / f'{concept_id}{src.suffix.lower() or ".png"}'
    shutil.copyfile(src, target)
    record = {'schema_version': 1, 'concept_id': concept_id, 'shot_id': shot_id, 'path': str(target.relative_to(path)),
              'sha256': file_hash(target), 'size': size, 'prompt': prompt, 'source': source, 'source_path': str(src),
              'ai_generated': source != 'photo', 'licence': 'local_only', 'created_at': now(),
              **({'frame': moment} if moment else {}), **({'from_beat': {'ref': from_beat, 'sha256': beat['sha256']}} if beat else {}),
              **({'derived_from': derived} if derived else {}), **dict(extra or {})}
    write_json(folder / f'{concept_id}.json', record)
    if redraw_of:   # the redrawn picture leaves its set (it stays on file)
        old = read_json(folder / f'{redraw_of}.json')
        old['variant'] = {**old['variant'], 'replaced_by': concept_id}
        write_json(folder / f'{redraw_of}.json', old)
    return {**record, 'artifacts': [str(target)]}


def _neighbour(path, shot_id, step):
    """The shot before (step -1) or after (+1) this one in the timeline, or None."""
    order = [s['shot_id'] for s in load_project(path).get('shots', [])]
    if shot_id not in order:
        return None
    i = order.index(shot_id) + step
    return order[i] if 0 <= i < len(order) else None


def variant_axes(text):
    """'size=wide,view=low,place=with_env' -> {'size': 'wide', ...}, checked against CONCEPT_AXES."""
    out = dict(x.split('=', 1) for x in str(text).split(',') if '=' in x)
    bad = [f'{k}={v}' for k, v in out.items() if k not in CONCEPT_AXES or v not in CONCEPT_AXES[k]['values']]
    if bad or not out:
        raise StudioError('INPUT_INVALID', f'variant axes {bad or text!r}: axes {sorted(CONCEPT_AXES)} with their values')
    return out


REDRAWS_PER_ROUND = 1   # a set of variants read as one picture gets one picture redrawn; past that the user (or a
                        # delegated run) picks among what is there - a model that drew one picture twice tends to again,
                        # and each redraw costs minutes of the image tool


def _rounds_file(path, shot_id):
    return _folder(path, shot_id) / 'variants.json'


def _variant_rounds(path, shot_id):
    file = _rounds_file(path, shot_id)
    return read_json(file).get('rounds', []) if file.is_file() else []


def variants_of(path, shot_id, moment, count=VARIANTS, record=False):
    """Draw count different pictures of one moment on CONCEPT_AXES: one near the user's concept picks, the others as far
    apart as the axes allow and away from every earlier draw of this moment. record: keep the draw as the moment's next
    round of variants (concept brief --variants), so the pictures added for it are compared as one set."""
    from .story_core import pick_directions
    from .taste_picks import preference
    used = [c['variant']['axes'] for c in concepts(path, shot_id) if c.get('frame') == moment and c.get('variant')]
    used += [a for r in _variant_rounds(path, shot_id) if r['frame'] == moment for a in r['drawn']]
    drawn = pick_directions(CONCEPT_AXES, count, preference('concept'), used, seed=len(used))
    rnd = None
    if record:
        rounds = _variant_rounds(path, shot_id)
        rnd = max((r['round'] for r in rounds), default=0) + 1
        rounds.append({'round': rnd, 'frame': moment, 'drawn': [d['axes'] for d in drawn], 'at': now()})
        _folder(path, shot_id).mkdir(parents=True, exist_ok=True)
        write_json(_rounds_file(path, shot_id), {'schema_version': 1, 'rounds': rounds})
    return [{'variant': d['slot'], 'role': d['role'], 'axes': d['axes'], **({'round': rnd} if rnd else {}),
             'line': 'This version: ' + '; '.join(CONCEPT_AXES[a]['values'][v]['draw'] for a, v in d['axes'].items()) + '.'} for d in drawn]


def _set_of(record):
    """Which set of variants a picture belongs to: a redo's option round, or the moment's draw round."""
    v = record.get('variant') or {}
    return ('redo', v['redo']['round']) if v.get('redo') else ('round', v.get('round', 0))


def current_variants(path, shot_id, at):
    """The pictures of the moment's latest set of variants, without those redrawn since."""
    items = [c for c in concepts(path, shot_id) if c.get('variant') and c.get('frame') and abs(c['frame']['at'] - float(at)) < 1e-9]
    if not items:
        return []
    latest = _set_of(items[-1])
    return [c for c in items if _set_of(c) == latest and not c['variant'].get('replaced_by')]


def _variant_extra(path, shot_id, moment, variant, redraw_of=None):
    """The variant record of a picture being added: its round (the latest draw of the moment holding its axes), and
    when it redraws one of the set, that one (at most REDRAWS_PER_ROUND a set)."""
    variant = dict(variant)
    if not variant.get('redo') and 'round' not in variant:
        rounds = [r for r in _variant_rounds(path, shot_id) if r['frame'] == moment]
        holding = [r for r in rounds if variant['axes'] in r['drawn']]
        variant['round'] = (holding or rounds or [{'round': 0}])[-1]['round']
    if redraw_of:
        old = next((c for c in concepts(path, shot_id) if c['concept_id'] == redraw_of), None)
        if old is None or not old.get('variant') or old.get('frame') != moment:
            raise StudioError('INPUT_INVALID', f'--redraw-of {redraw_of}: not a variant of this moment')
        if old['variant'].get('replaced_by'):
            raise StudioError('INPUT_INVALID', f"{redraw_of} was redrawn already ({old['variant']['replaced_by']})")
        same_set = [c for c in concepts(path, shot_id) if c.get('variant') and _set_of(c) == _set_of(old)]
        if sum(bool(c['variant'].get('redraw_of')) for c in same_set) >= REDRAWS_PER_ROUND:
            raise StudioError('INPUT_INVALID', f'{shot_id}: this set of variants was redrawn {REDRAWS_PER_ROUND} time(s) already - pick among them',
                              recovery='concept sheet, then concept pick (the user, or a delegated run with its reason)')
        variant['redraw_of'] = redraw_of
        if old['variant'].get('redo'):
            variant['redo'] = old['variant']['redo']
            variant.pop('round', None)
        else:
            variant['round'] = old['variant'].get('round', 0)
    return variant


VARIANT_HEIGHT = (0.5, 0.85)   # a picture's subject box height share: up to the first is wide, past the second close.
# The study set's lines are 0.33 / 0.66 of the focus box; a reader's subject box runs taller than the focus it frames,
# so the lines sit higher - two pictures on the same side of both read as one size


def _variant_key(reading):
    box = (reading or {}).get('subject_box')
    if not box or not reading.get('view_height'):
        return None
    h = box[3] - box[1]
    size = 'wide' if h <= VARIANT_HEIGHT[0] else 'close' if h > VARIANT_HEIGHT[1] else 'medium'
    return (size, reading['view_height'], reading.get('side'))


def variant_readings(path, shot_id, reader=None):
    """{concept_id: reading} of every current variant of every moment: kept readings first (frames.json, and earlier
    reads of variants in variants_read.json), the rest read now (frame_targets.read_pictures, one call) unless
    reader is False."""
    path = project_dir(path)
    store = path / 'concepts' / shot_id / 'frames.json'
    kept = (read_json(store).get('pictures') or {}) if store.is_file() else {}
    cache_file = _folder(path, shot_id) / 'variants_read.json'
    cache = read_json(cache_file) if cache_file.is_file() else {}
    moments = sorted({c['frame']['at'] for c in concepts(path, shot_id) if c.get('frame') and c.get('variant')})
    current = [c for at in moments for c in current_variants(path, shot_id, at)]
    out, todo = {}, []
    for c in current:
        row = kept.get(c['concept_id']) if (kept.get(c['concept_id']) or {}).get('sha256') == c['sha256'] else cache.get(c['concept_id'])
        if row and row.get('sha256') == c['sha256'] and row.get('read'):
            out[c['concept_id']] = row['read']
        else:
            todo.append(c)
    if todo and reader is not False:
        from .frame_targets import read_pictures
        read = (reader or read_pictures)(path, shot_id, todo)
        for c in todo:
            if c['concept_id'] in read:
                out[c['concept_id']] = read[c['concept_id']]
                cache[c['concept_id']] = {'sha256': c['sha256'], 'read': read[c['concept_id']], 'read_at': now()}
        write_json(cache_file, cache)
    return out


def concepts_one_idea(path, shot_id, reader=False):
    """CONCEPTS_ONE_IDEA: two pictures of a moment's current set of variants read as the same size, height and side -
    one picture drawn twice. Only read pictures are compared (a drawn axis says what was asked, not what was drawn);
    reader=None reads the unread ones now (concept check), False compares what is read already (the sheet)."""
    path = project_dir(path)
    readings = variant_readings(path, shot_id, reader)
    out = []
    for at in sorted({c['frame']['at'] for c in concepts(path, shot_id) if c.get('frame') and c.get('variant')}):
        items = current_variants(path, shot_id, at)
        seen = {}
        for c in items:
            key = _variant_key(readings.get(c['concept_id']))
            if key:
                seen.setdefault(key, []).append(c)
        for same in seen.values():
            if len(same) < 2:
                continue
            ids = ', '.join(c['concept_id'] for c in same)
            if any(c['variant'].get('redraw_of') for c in items):
                out.append(f'CONCEPTS_ONE_IDEA: {shot_id} at {at:.2f}: {ids} read as one picture again after a redraw - pick among them')
                continue
            last = same[-1]
            axes = ','.join(f'{k}={v}' for k, v in last['variant']['axes'].items())
            out.append(f"CONCEPTS_ONE_IDEA: {shot_id} at {at:.2f}: {ids} read as one picture "
                       f"({' '.join(str(x) for x in _variant_key(readings[last['concept_id']]) if x)}) - redraw {last['concept_id']} once, "
                       f"stressing its axes (concept add --variant {axes} --redraw-of {last['concept_id']}); past one redraw, pick among them")
        unread = [c['concept_id'] for c in items if c['concept_id'] not in readings]
        if unread and len(items) > 1:
            out.append(f"CONCEPTS_UNREAD: {shot_id} at {at:.2f}: {', '.join(unread)} not read yet - concept check reads the set and compares it")
    return out


def check(project, shot_id, reader=None):
    """Read a shot's current variants and compare them (concept check): the step between drawing a set and picking."""
    return {'shot_id': shot_id, 'warnings': concepts_one_idea(project, shot_id, reader=reader)}


def brief(project, shot_id, frame, at=None, variants=None):
    """What to draw for one frame picture, decided before it is drawn: the prompt (the shot's lines and job, how this
    moment shows its parts - show_core.SHOW_METHODS - and how it continues from the shot before) and the pictures to
    attach (the beat picture, the shot before's last picture). The picture is drawn with the agent's image tool and added
    with concept add; frames measure then reads whether it shows what the shot list decided (FRAME_SHOW_DIFFERS).
    Why (2026-10-13): pictures were drawn shot by shot, from outside, and the camera hunted for an angle at which the
    inside showed; the user: draw the section from the start, and let the shots run on into each other."""
    from .blender_ops.show_core import CONTINUOUS, SHOW_METHODS, SHOWN_THROUGH, held_at, opens, show_of
    from .frame_targets import part_candidates
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    moment = _frame(frame, at)
    row = held_at(show_of(shot), moment['at']) or {'how': 'exterior', 'parts': [], 'why': 'nothing decided for this moment'}
    words = part_candidates(path, shot)
    parts = '; '.join(f"{p} ({words.get(p, p)})" for p in row['parts']) or 'none named'
    direction = shot.get('direction') or {}
    out = load_project(path)['output']
    shape = 'vertical' if out['height'] > out['width'] else 'horizontal' if out['width'] > out['height'] else 'square'
    from .beat_pictures import ONE_SCENE, picture_rules
    lines = [f"One {shape} frame of a short explainer video, the {moment['role']} moment of shot {shot_id} "
             f"(at {moment['at']:.2f} of its {shot['duration_frames']} frames): {ONE_SCENE}.",
             f"What the shot is for: {shot.get('goal') or ''}",
             f"What is said over it: {(shot.get('narration') or {}).get('text') or '(no words)'}",
             f"Show it this way ({row['how']}): {SHOW_METHODS[row['how']]['draw']}"
             + (f"; {SHOWN_THROUGH['draw']}." if len(row['parts']) > 1 and opens(row['how']) else '.'),
             f"The parts this moment must show clearly: {parts} - because {row['why']}."]
    lines += explained_lines(path, shot_id)
    lines += [f"Camera: {direction['camera']}" for _ in [0] if direction.get('camera')]
    lines += [f"Action: {direction['action']}" for _ in [0] if direction.get('action')]
    depth = direction.get('depth') or {}
    if depth:
        lines.append('Depth: ' + '; '.join(f'{k}: {v}' for k, v in depth.items()))
    attach = []
    beat = picked(path, shot_id)
    if beat and (path / beat['path']).is_file():
        attach.append({'path': beat['path'], 'why': 'the world this shot happens in (its beat picture): keep its place, light and look'})
    earlier = [c for c in frames(path, shot_id) if c['frame']['at'] < moment['at'] and (path / c['path']).is_file()]
    if earlier:   # one shot is one camera running on: a later moment keeps what the earlier one drew (2026-10-13: an end
        # picture drawn without its start came out as another vehicle)
        lines.append(f"It is the same shot as the attached earlier picture ({earlier[-1]['frame']['role']}, a moment before): the "
                     'same place, light and things with the same shapes; only what the action says changes')
        attach.append({'path': earlier[-1]['path'], 'why': f"the {earlier[-1]['frame']['role']} picture of this same shot, a moment before"})
    previous = _neighbour(path, shot_id, -1) if moment['role'] == 'start' else None
    if previous:
        handover = load_shot(path, previous).get('handover') or {}
        before = [c for c in frames(path, previous) if c['frame']['role'] == 'end']
        kind = handover.get('kind')
        if kind in CONTINUOUS:
            lines.append(f"It continues from the shot before ({previous}, attached: its last picture) - {kind.replace('_', ' ')}: the "
                         'same subject, the same screen direction and a size that runs on from it' + (f" ({handover['why']})" if handover.get('why') else ''))
        elif kind == 'contrast':
            lines.append(f"It cuts from the shot before ({previous}) on purpose to something different: another size, side "
                         'or pace' + (f" ({handover['why']})" if handover.get('why') else ''))
        elif kind == 'new_place':
            lines.append(f"It cuts from the shot before ({previous}) to another subject or place" + (f" ({handover['why']})" if handover.get('why') else ''))
        if before and kind in CONTINUOUS:   # a cut to something else takes nothing from the picture before (2026-10-13: a
            # 1960s stage drawn after the modern booster's picture came out as the booster)
            attach.append({'path': before[-1]['path'], 'why': f'the last picture of {previous}, the shot this one follows'})
    following = _neighbour(path, shot_id, 1)
    if (path / 'concepts' / shot_id / 'picture3d' / 'text_only.json').is_file() and following:
        # drawn from text only (picture3d generate-only): it takes its look from the pictures around it, so it does not
        # stand apart from the 3D shots it sits between
        after = [c for c in frames(path, following) if c['frame']['role'] == 'start' and (path / c['path']).is_file()]
        if after:
            attach.append({'path': after[-1]['path'], 'why': f'the first picture of {following}, the shot after this one: the same look'})
    if moment['role'] == 'end' and (shot.get('handover') or {}).get('kind') in CONTINUOUS:
        lines.append(f"The next shot runs on from this one ({shot['handover']['kind'].replace('_', ' ')}): leave the motion going.")
    lines += picture_rules(path)
    lines.append('No text, no letters, no labels in the picture.')
    out = {'shot_id': shot_id, 'frame': moment, 'how': row['how'], 'parts': row['parts'], 'prompt': '\n'.join(lines), 'attach': attach,
           'next': f"draw it with the image tool, then: studio concept add --project {path} --shot {shot_id} --frame {moment['role']}"
                   + (f" --at {moment['at']}" if moment['role'] == 'key' else '') + ' --image <file> --prompt <this prompt>'}
    if variants:   # several different pictures of the moment, each with its own line (the base prompt stays the shot's)
        from .taste_picks import summary
        drawn = variants_of(path, shot_id, moment, int(variants), record=True)
        out['variants'] = [{**d, 'prompt': '\n'.join(lines[:-1] + [d['line'], lines[-1]])} for d in drawn]
        out['next'] += (' --variant <size=..,view=..,place=..> (one per variant), then concept check (reads the set: one picture drawn twice '
                        'is redrawn once with --redraw-of), then concept sheet and pick')
        taste = summary(['concept'])['text']
        if taste:
            out['taste'] = taste   # what the user picked before: for a delegated run's pick, never for the draw of the explore one
    return out


def explained_lines(path, shot_id):
    """What the shot's lines explain (their claims' about, studio/explains.py), and which of it sits inside its subject:
    the moment's `how` (above) is how it opens."""
    from .decisions import envelope
    from .explains import line_things, need
    body = {layer: (envelope(path, layer) or {}).get('body') for layer in ('facts', 'script', 'world', 'shotlist')}
    entry = next((s for s in (body['shotlist'] or {}).get('shots', []) if s['shot_id'] == shot_id), None)
    if entry is None:
        return []
    out = []
    for thing in line_things(path, entry['line_ids'], body['facts'], body['script'], body['world']):
        n = need(path, thing)
        out.append(f"Its lines explain {thing['id']} ({thing['words']})"
                   + (f": it sits inside {', '.join(n['hidden_by']) or 'its subject'} and shows only opened" if n['inside'] else ''))
    return out


def add_beat(project, beat_id, image, prompt=None, source='codex_image_gen', for_things=None):
    """A picture of one beat of the minute, before any shot exists (the world layer, studio/world.py): copied to
    concepts/world/<beat>/pNN with its provenance; the world body names it by path and sha256. for_things: what it was
    drawn to show (concept brief --beat), so redraws of the same thing are counted (beat_pictures.MAX_DRAWS)."""
    from PIL import Image
    from .common import check_id
    path = project_dir(project)
    check_id(beat_id)
    if for_things:
        from .reference_use import check_ids
        check_ids(path, for_things)
    src = Path(image).expanduser().resolve()
    try:
        with Image.open(src) as probe:
            size = list(probe.size)
    except OSError as exc:
        raise StudioError('INPUT_INVALID', f'{image} is not an image: {exc}') from None
    folder = path / 'concepts' / 'world' / beat_id
    folder.mkdir(parents=True, exist_ok=True)
    taken = [int(p.stem[1:]) for p in folder.glob('p*.json') if p.stem[1:].isdigit()]
    picture_id = f'p{max(taken, default=0) + 1:02d}'
    target = folder / f'{picture_id}{src.suffix.lower() or ".png"}'
    shutil.copyfile(src, target)
    record = {'schema_version': 1, 'beat_id': beat_id, 'picture_id': picture_id, 'path': str(target.relative_to(path)),
              'sha256': file_hash(target), 'size': size, 'prompt': prompt, 'source': source, 'ai_generated': source != 'photo',
              'created_at': now(), **({'for': list(for_things)} if for_things else {})}
    write_json(folder / f'{picture_id}.json', record)
    return {**record, 'picture': {'path': record['path'], 'sha256': record['sha256'], **({'prompt': prompt} if prompt else {})},
            'artifacts': [str(target)]}


def _parents(path):
    from . import decisions
    if not decisions.adopted(path):
        return {}
    if decisions.state(path, 'shotlist')['state'] != 'approved':
        raise StudioError('DECISION_UNAPPROVED', 'approve the shot list before picking concept frames', recovery='decide approve --layer shotlist')
    return {'shotlist': decisions.body_sha256(decisions.envelope(path, 'shotlist')['body'])}


def pick(project, shot_id, concept_id, user_words=None, agent_note=None):
    """The user's pick in their words; in a delegated run the agent's pick with its reason (never written as approval)."""
    path = project_dir(project)
    record = next((c for c in concepts(path, shot_id) if c['concept_id'] == concept_id), None)
    if record is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no concept {concept_id} (have {[c["concept_id"] for c in concepts(path, shot_id)]})')
    if file_hash(path / record['path']) != record['sha256']:
        raise StudioError('CONCEPT_STALE', f'{shot_id} {concept_id}: the image changed after it was added; concept add it again')
    if user_words:
        from .generative.review import check_user_words
        decision = {'by': 'user', 'user_words': check_user_words(user_words, 'concept pick')}
    elif agent_note and load_project(path).get('delegation'):
        decision = {'by': 'agent', 'agent_note': agent_note}
    else:
        raise StudioError('INPUT_INVALID', "concept pick needs the user's words (--user-words); only a delegated run lets the agent pick "
                                           '(--agent-note, its reason)')
    out = {'schema_version': 1, 'shot_id': shot_id, 'concept_id': concept_id, 'path': record['path'], 'sha256': record['sha256'],
           'parents': _parents(path), 'decision': {**decision, 'at': now()}}
    # one pick per moment (a shot's start and end are picked apart); the top level stays the shot's latest pick, as it
    # was before moments were kept (picked(), require())
    previous = read_json(_record_path(path, shot_id)) if _record_path(path, shot_id).is_file() else {}
    moments = dict(previous.get('moments') or {})
    if record.get('frame'):
        moments[_moment_key(record['frame']['at'])] = {k: out[k] for k in ('concept_id', 'path', 'sha256', 'decision')}
    out['moments'] = moments
    _record_path(path, shot_id).parent.mkdir(parents=True, exist_ok=True)
    write_json(_record_path(path, shot_id), out)
    if record.get('variant'):   # the moment's picture is now this variant; its siblings stay on file, unpicked
        siblings = [c for c in concepts(path, shot_id) if c.get('variant') and c.get('frame') == record.get('frame')]
        for c in siblings:
            c['variant'] = {k: v for k, v in c['variant'].items() if k != 'chosen'}
            if c['concept_id'] == concept_id:
                c['variant']['chosen'] = {'by': decision['by'], 'at': out['decision']['at']}
            write_json(_folder(path, shot_id) / f"{c['concept_id']}.json", c)
        if decision['by'] == 'user':   # the user's pick among variants of the moment is taste
            from .taste_picks import record as record_pick
            others = [c['variant']['axes'] for c in siblings if c['concept_id'] != concept_id]
            record_pick('concept', record['variant']['axes'], others, decision['user_words'], path, ref=f'{shot_id} {concept_id}')
    return {**out, 'artifacts': [str(_record_path(path, shot_id))]}


def picked(path, shot_id):
    """The shot's picked concept; without a pick of its own, the first picture of its beat in an approved world (the
    user approved one picture per beat on the world sheet, and the approved shot list names each shot's beats -
    asking again per shot would repeat that decision)."""
    path = project_dir(path)
    file = _record_path(path, shot_id)
    if file.is_file():
        return read_json(file)
    return _beat_picture(path, shot_id)


def _beat_picture(path, shot_id):
    from . import decisions
    world = decisions.settled(path, 'world')   # approved on the ladder, or decided off it (a delegated run)
    if world is None:
        return None
    shots = ((decisions.envelope(path, 'shotlist') or {}).get('body') or {}).get('shots') or []
    entry = next((s for s in shots if s['shot_id'] == shot_id), None)
    beats = {b['beat_id']: b for b in world['beats']}
    import re
    words = lambda text: {w for w in re.findall(r'[a-z]{4,}', (text or '').lower())}   # noqa: E731
    wanted = words(' '.join(str(v) for v in [(entry or {}).get('purpose'), *(((entry or {}).get('direction') or {}).values())]))
    for beat_id in (entry or {}).get('beat_ids') or []:
        pictures = (beats.get(beat_id) or {}).get('pictures') or []
        if pictures:   # of the beat's approved pictures, the one whose prompt shares most words with what the shot does
            best = max(pictures, key=lambda pic: len(wanted & words(pic.get('prompt'))))
            return {'schema_version': 1, 'shot_id': shot_id, 'concept_id': f'beat:{beat_id}', 'path': best['path'],
                    'sha256': best['sha256'], 'parents': {}, 'decision': {'by': 'world', 'note': f'approved world beat {beat_id}'}}
    return None


def required(path):
    """A delegated project, or one whose decision ladder was adopted with concept frames, builds every shot toward a
    picked concept (a ladder adopted earlier keeps its rules)."""
    from . import decisions
    path = project_dir(path)
    return decisions.ladder_requires(path, 'concept_frames') or bool(load_project(path).get('delegation'))


def require(path, shot_id):
    """Storyboards (and so builds and renders behind them) of a shot need its picked, unchanged concept."""
    path = project_dir(path)
    if not required(path):
        return None
    record = picked(path, shot_id)
    if record is None:
        raise StudioError('CONCEPT_UNPICKED', f'{shot_id}: no concept frame picked',
                          recovery='make 2-3 concept images (Codex image_gen), concept add, concept sheet, then concept pick')
    image = path / record['path']
    if not image.is_file() or file_hash(image) != record['sha256']:
        raise StudioError('CONCEPT_STALE', f'{shot_id}: the picked concept image changed or is gone', recovery='concept pick again')
    bound = record.get('parents', {}).get('shotlist')
    if bound:
        from . import decisions
        shotlist = decisions.envelope(path, 'shotlist')
        if shotlist is None or decisions.body_sha256(shotlist['body']) != bound:
            raise StudioError('CONCEPT_STALE', f'{shot_id}: the shot list changed after this concept was picked', recovery='concept pick again')
    return record


def sheet(project):
    """One page of every shot's concept frames (the first human checkpoint): a row per shot, the pick marked."""
    from PIL import Image, ImageDraw, ImageOps
    path = project_dir(project)
    shots = [e['shot_id'] for e in load_project(path)['shots']]
    rows = [(s, concepts(path, s), picked(path, s)) for s in shots]
    columns = max([len(c) for _, c, _ in rows] + [1])
    w, h = THUMB
    pad, label = 12, 28
    image = Image.new('RGB', (pad + columns * (w + pad) + 90, pad + len(rows) * (h + label + pad)), '#14171c')
    draw = ImageDraw.Draw(image)
    lines = ['# Concept frames', '']
    for r, (shot_id, items, chosen) in enumerate(rows):
        y = pad + r * (h + label + pad)
        draw.text((pad, y + h // 2), shot_id, fill='white')
        by = (chosen or {}).get('decision', {}).get('by')
        lines.append(f"## {shot_id}: " + (f"picked {chosen['concept_id']} by {'the user' if by == 'user' else 'the agent (delegated run)'}" if chosen else 'not picked'))
        for c, item in enumerate(items):
            x = 90 + c * (w + pad)
            with Image.open(path / item['path']) as src:
                thumb = ImageOps.contain(src.convert('RGB'), (w, h))
            image.paste(thumb, (x + (w - thumb.width) // 2, y + (h - thumb.height) // 2))
            mark = chosen and chosen['concept_id'] == item['concept_id']
            if mark:
                draw.rectangle((x - 3, y - 3, x + w + 3, y + h + 3), outline='#ffd84a', width=4)
            caption = item['concept_id'] + ((' PICKED (user)' if chosen['decision']['by'] == 'user' else ' PICKED (agent)') if mark else '')
            draw.text((x, y + h + 6), caption, fill='#ffd84a' if mark else 'white')
            lines.append(f"- {item['concept_id']}: {item.get('prompt') or ''} ({item['path']})")
        lines.append('')
    out = path / 'concepts' / 'sheet.png'
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    (out.with_suffix('.md')).write_text('\n'.join(lines), encoding='utf-8')
    warnings = [w for s, _, _ in rows for w in concepts_one_idea(path, s)]
    return {'sheet': str(out), 'shots': {s: {'concepts': [i['concept_id'] for i in items], 'picked': (chosen or {}).get('concept_id')}
                                         for s, items, chosen in rows}, 'warnings': warnings, 'artifacts': [str(out), str(out.with_suffix('.md'))]}


def _brief_command(a):
    if bool(a.beat) == bool(a.shot):
        raise StudioError('INPUT_INVALID', 'concept brief: give --shot with --frame, or --beat')
    if a.beat:
        from .beat_pictures import brief as beat_brief
        return beat_brief(a.project, a.beat, a.things, a.user_words)
    if not a.frame:
        raise StudioError('INPUT_INVALID', 'concept brief --shot needs --frame start|end|key')
    return brief(a.project, a.shot, a.frame, a.at, a.variants)


def register_commands(subparsers):
    parser = subparsers.add_parser('concept', help='Concept frames: the picture each shot is built toward (made before 3D, picked by the user)')
    commands = parser.add_subparsers(dest='concept_command', required=True)
    p = commands.add_parser('add', help='Copy an image (e.g. from ~/.codex/generated_images) in as a concept of a shot; with --frame, '
                                        'a picture of its first (start), last (end) or a key moment - the camera is solved to it')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--image')
    p.add_argument('--prompt'); p.add_argument('--source', default='codex_image_gen')
    p.add_argument('--frame', choices=sorted(FRAME_ROLES)); p.add_argument('--at', type=float, help='key moments: share of the shot (0-1)')
    p.add_argument('--from-beat', help='reuse an approved beat picture, e.g. b1/p01')
    p.add_argument('--derived-from', help='the built frame (clay) this picture was painted over: frames derive then reads it from the build')
    p.add_argument('--variant', help='the axis values it was drawn to, from concept brief --variants: size=..,view=..,place=..')
    p.add_argument('--redraw-of', help='a variant of the same set read as one picture with another (concept check): this replaces it, once a set')
    p.set_defaults(handler=lambda a: add(a.project, a.shot, a.image, a.prompt, a.source, a.frame, a.at, a.from_beat, a.derived_from,
                                         {'variant': {'axes': variant_axes(a.variant)}} if a.variant else None, a.redraw_of))
    p = commands.add_parser('check', help="read a shot's current variants and say which are one picture drawn twice (CONCEPTS_ONE_IDEA)")
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: check(a.project, a.shot))
    p = commands.add_parser('brief', help='What to draw for one frame picture (--shot --frame) or one beat picture (--beat): the prompt '
                                          'and the pictures to attach (before drawing it)')
    p.add_argument('--project', required=True); p.add_argument('--shot'); p.add_argument('--beat')
    p.add_argument('--frame', choices=sorted(FRAME_ROLES)); p.add_argument('--at', type=float)
    p.add_argument('--things', type=lambda v: [x for x in v.split(',') if x], help='beats: what to draw (default: what its pictures miss)')
    p.add_argument('--variants', type=int, help=f'shots: this many different pictures of the moment, drawn on CONCEPT_AXES (default {VARIANTS} when given)')
    p.add_argument('--user-words', help="beats, after DRAW_LIMIT: the user's own words on how to show it (starts the count again)")
    p.set_defaults(handler=_brief_command)
    p = commands.add_parser('add-beat', help='Copy a picture of one beat of the minute in, before the shot list (the world layer)')
    p.add_argument('--project', required=True); p.add_argument('--beat', required=True); p.add_argument('--image', required=True)
    p.add_argument('--prompt'); p.add_argument('--source', default='codex_image_gen')
    p.add_argument('--for', dest='for_things', type=lambda v: [x for x in v.split(',') if x], help='what it was drawn to show (from concept brief --beat)')
    p.set_defaults(handler=lambda a: add_beat(a.project, a.beat, a.image, a.prompt, a.source, a.for_things))
    p = commands.add_parser('read-beat', help='Read a beat\'s pictures for what they show of what its lines explain (the world lint holds them to it)')
    p.add_argument('--project', required=True); p.add_argument('--beat', required=True)
    p.add_argument('--picture', action='append', help='a picture not in the world yet (project-relative path); default: the beat\'s pictures')
    p.add_argument('--force', action='store_true')
    p.set_defaults(handler=lambda a: __import__('studio.beat_pictures', fromlist=['read']).read(a.project, a.beat, a.picture, a.force))
    p = commands.add_parser('mark-beat', help='Correct a beat picture\'s reading by hand (the reader missed or invented something)')
    p.add_argument('--project', required=True); p.add_argument('--beat', required=True); p.add_argument('--picture', required=True)
    p.add_argument('--shows', type=lambda v: [x for x in v.split(',') if x]); p.add_argument('--opened', type=lambda v: [x for x in v.split(',') if x])
    p.add_argument('--how'); p.add_argument('--note', required=True)
    p.set_defaults(handler=lambda a: __import__('studio.beat_pictures', fromlist=['mark']).mark(a.project, a.beat, a.picture, a.shows, a.opened, a.how, a.note))
    p = commands.add_parser('sheet', help='Every shot\'s concepts on one page (show it to the user)')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: sheet(a.project))
    p = commands.add_parser('pick')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--concept', required=True)
    p.add_argument('--user-words'); p.add_argument('--agent-note', help='delegated runs only: the agent\'s reason')
    p.set_defaults(handler=lambda a: pick(a.project, a.shot, a.concept, a.user_words, a.agent_note))
    p = commands.add_parser('show')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: {'concepts': concepts(project_dir(a.project), a.shot), 'picked': picked(a.project, a.shot)})
