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


def frames(path, shot_id):
    """The shot's frame pictures in time order: the newest picture of each moment (an older one stays on file)."""
    latest = {}
    for record in concepts(project_dir(path), shot_id):
        if record.get('frame'):
            latest[record['frame']['at']] = record          # concepts() is in id order: a later one replaces
    return [latest[at] for at in sorted(latest)]


def add(project, shot_id, image=None, prompt=None, source='codex_image_gen', frame=None, at=None, from_beat=None, derived_from=None):
    from PIL import Image
    path = project_dir(project)
    load_shot(path, shot_id)                      # the shot exists
    moment = _frame(frame, at)
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
              **({'derived_from': derived} if derived else {})}
    write_json(folder / f'{concept_id}.json', record)
    return {**record, 'artifacts': [str(target)]}


def _neighbour(path, shot_id, step):
    """The shot before (step -1) or after (+1) this one in the timeline, or None."""
    order = [s['shot_id'] for s in load_project(path).get('shots', [])]
    if shot_id not in order:
        return None
    i = order.index(shot_id) + step
    return order[i] if 0 <= i < len(order) else None


def brief(project, shot_id, frame, at=None):
    """What to draw for one frame picture, decided before it is drawn: the prompt (the shot's lines and job, how this
    moment shows its parts - show_core.SHOW_METHODS - and how it continues from the shot before) and the pictures to
    attach (the beat picture, the shot before's last picture). The picture is drawn with the agent's image tool and added
    with concept add; frames measure then reads whether it shows what the shot list decided (FRAME_SHOW_DIFFERS).
    Why (2026-10-13): pictures were drawn shot by shot, from outside, and the camera hunted for an angle at which the
    inside showed; the user: draw the section from the start, and let the shots run on into each other."""
    from .blender_ops.show_core import CONTINUOUS, SHOW_METHODS, moment_at, show_of
    from .frame_targets import part_candidates
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    moment = _frame(frame, at)
    row = moment_at(show_of(shot), moment['at']) or {'how': 'exterior', 'parts': [], 'why': 'nothing decided for this moment'}
    words = part_candidates(path, shot)
    parts = '; '.join(f"{p} ({words.get(p, p)})" for p in row['parts']) or 'none named'
    direction = shot.get('direction') or {}
    out = load_project(path)['output']
    shape = 'vertical' if out['height'] > out['width'] else 'horizontal' if out['width'] > out['height'] else 'square'
    lines = [f"One {shape} frame of a short explainer video, the {moment['role']} moment of shot {shot_id} "
             f"(at {moment['at']:.2f} of its {shot['duration_frames']} frames).",
             f"What the shot is for: {shot.get('goal') or ''}",
             f"What is said over it: {(shot.get('narration') or {}).get('text') or '(no words)'}",
             f"Show it this way ({row['how']}): {SHOW_METHODS[row['how']]['draw']}.",
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
    previous = _neighbour(path, shot_id, -1) if moment['role'] == 'start' else None
    if previous:
        handover = load_shot(path, previous).get('handover') or {}
        before = [c for c in frames(path, previous) if c['frame']['role'] == 'end']
        kind = handover.get('kind')
        if kind in CONTINUOUS:
            lines.append(f"It continues from the shot before ({previous}, attached: its last picture) - {kind.replace('_', ' ')}: the "
                         'same subject, the same screen direction and a size that runs on from it' + (f" ({handover['why']})" if handover.get('why') else ''))
        elif kind == 'contrast':
            lines.append(f"It cuts from the shot before ({previous}, attached) on purpose to something different: another size, side "
                         'or pace' + (f" ({handover['why']})" if handover.get('why') else ''))
        elif kind == 'new_place':
            lines.append(f"It cuts from the shot before ({previous}) to another subject or place" + (f" ({handover['why']})" if handover.get('why') else ''))
        if before:
            attach.append({'path': before[-1]['path'], 'why': f'the last picture of {previous}, the shot this one follows'})
    if moment['role'] == 'end' and (shot.get('handover') or {}).get('kind') in CONTINUOUS:
        lines.append(f"The next shot runs on from this one ({shot['handover']['kind'].replace('_', ' ')}): leave the motion going.")
    lines.append('No text, no letters, no labels in the picture.')
    return {'shot_id': shot_id, 'frame': moment, 'how': row['how'], 'parts': row['parts'], 'prompt': '\n'.join(lines), 'attach': attach,
            'next': f"draw it with the image tool, then: studio concept add --project {path} --shot {shot_id} --frame {moment['role']}"
                    + (f" --at {moment['at']}" if moment['role'] == 'key' else '') + ' --image <file> --prompt <this prompt>'}


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
    write_json(_record_path(path, shot_id), out)
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
    if not decisions.adopted(path) or decisions.state(path, 'world')['state'] != 'approved':
        return None
    shots = ((decisions.envelope(path, 'shotlist') or {}).get('body') or {}).get('shots') or []
    entry = next((s for s in shots if s['shot_id'] == shot_id), None)
    beats = {b['beat_id']: b for b in decisions.envelope(path, 'world')['body']['beats']}
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
    return {'sheet': str(out), 'shots': {s: {'concepts': [i['concept_id'] for i in items], 'picked': (chosen or {}).get('concept_id')}
                                         for s, items, chosen in rows}, 'artifacts': [str(out), str(out.with_suffix('.md'))]}


def _brief_command(a):
    if bool(a.beat) == bool(a.shot):
        raise StudioError('INPUT_INVALID', 'concept brief: give --shot with --frame, or --beat')
    if a.beat:
        from .beat_pictures import brief as beat_brief
        return beat_brief(a.project, a.beat, a.things, a.user_words)
    if not a.frame:
        raise StudioError('INPUT_INVALID', 'concept brief --shot needs --frame start|end|key')
    return brief(a.project, a.shot, a.frame, a.at)


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
    p.set_defaults(handler=lambda a: add(a.project, a.shot, a.image, a.prompt, a.source, a.frame, a.at, a.from_beat, a.derived_from))
    p = commands.add_parser('brief', help='What to draw for one frame picture (--shot --frame) or one beat picture (--beat): the prompt '
                                          'and the pictures to attach (before drawing it)')
    p.add_argument('--project', required=True); p.add_argument('--shot'); p.add_argument('--beat')
    p.add_argument('--frame', choices=sorted(FRAME_ROLES)); p.add_argument('--at', type=float)
    p.add_argument('--things', type=lambda v: [x for x in v.split(',') if x], help='beats: what to draw (default: what its pictures miss)')
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
