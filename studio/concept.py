"""Concept frames: the picture a shot is built toward, made before any 3D - one per shot, picked by the user (or, in a
delegated run, by the agent with its reason).

Why (floor_noise, 2026-10-09): with no reference reel nothing said what a shot should look like, so the build was judged
by its checks alone and stopped at a blockout - "a doll's house floating in a factory". A concept frame is the shot's own
reference: the storyboard sheet shows it beside the built frames, reference_critique compares against it by default,
and qa collect measures the cut against it.

A concept is an image the agent made (Codex's built-in image tool: no key, the subscription) or any image inside the
project. It is copied to concepts/<shot>/cNN.png with its provenance. concepts/ is not a folder the paid-generation
routing may send (routing.reference_problems allows renders/, generated/, control/, stills/): a concept stays local.
The pick is bound to the approved shot list (like a fill brief) and to the image's hash.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .common import StudioError, file_hash, now, read_json, write_json
from .project import load_project, load_shot, project_dir

THUMB = (240, 427)


def _folder(path, shot_id):
    return path / 'concepts' / shot_id


def _record_path(path, shot_id):
    return path / 'decisions' / 'concept' / f'{shot_id}.json'


def concepts(path, shot_id):
    folder = _folder(path, shot_id)
    return [read_json(p) for p in sorted(folder.glob('c*.json'))] if folder.is_dir() else []


def add(project, shot_id, image, prompt=None, source='codex_image_gen'):
    from PIL import Image
    path = project_dir(project)
    load_shot(path, shot_id)                      # the shot exists
    src = Path(image).expanduser().resolve()
    if not src.is_file():
        raise StudioError('INPUT_INVALID', f'no image {image}')
    try:
        with Image.open(src) as probe:
            size = list(probe.size)
    except OSError as exc:
        raise StudioError('INPUT_INVALID', f'{image} is not an image: {exc}') from None
    folder = _folder(path, shot_id)
    folder.mkdir(parents=True, exist_ok=True)
    taken = [int(p.stem[1:]) for p in folder.glob('c*.json') if p.stem[1:].isdigit()]
    concept_id = f'c{max(taken, default=0) + 1:02d}'   # never reuses an id, even after one was removed
    target = folder / f'{concept_id}{src.suffix.lower() or ".png"}'
    shutil.copyfile(src, target)
    record = {'schema_version': 1, 'concept_id': concept_id, 'shot_id': shot_id, 'path': str(target.relative_to(path)),
              'sha256': file_hash(target), 'size': size, 'prompt': prompt, 'source': source, 'source_path': str(src),
              'ai_generated': source != 'photo', 'licence': 'local_only', 'created_at': now()}
    write_json(folder / f'{concept_id}.json', record)
    return {**record, 'artifacts': [str(target)]}


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
    file = _record_path(project_dir(path), shot_id)
    return read_json(file) if file.is_file() else None


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
            label = item['concept_id'] + ((' PICKED (user)' if chosen['decision']['by'] == 'user' else ' PICKED (agent)') if mark else '')
            draw.text((x, y + h + 6), label, fill='#ffd84a' if mark else 'white')
            lines.append(f"- {item['concept_id']}: {item.get('prompt') or ''} ({item['path']})")
        lines.append('')
    out = path / 'concepts' / 'sheet.png'
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    (out.with_suffix('.md')).write_text('\n'.join(lines), encoding='utf-8')
    return {'sheet': str(out), 'shots': {s: {'concepts': [i['concept_id'] for i in items], 'picked': (chosen or {}).get('concept_id')}
                                         for s, items, chosen in rows}, 'artifacts': [str(out), str(out.with_suffix('.md'))]}


def register_commands(subparsers):
    parser = subparsers.add_parser('concept', help='Concept frames: the picture each shot is built toward (made before 3D, picked by the user)')
    commands = parser.add_subparsers(dest='concept_command', required=True)
    p = commands.add_parser('add', help='Copy an image (e.g. from ~/.codex/generated_images) in as a concept of a shot')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--image', required=True)
    p.add_argument('--prompt'); p.add_argument('--source', default='codex_image_gen')
    p.set_defaults(handler=lambda a: add(a.project, a.shot, a.image, a.prompt, a.source))
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

