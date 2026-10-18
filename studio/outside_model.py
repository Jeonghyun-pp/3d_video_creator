"""A model the studio asks - a reader of pictures (screen_grammar.codex_json) or a painter (picture3d.paint_into) - when
it cannot start one itself: the question is written down as a request, whoever runs the studio answers it with a fresh
agent and hands the answer in (`studio ask answer`), and the command run again takes the answer and goes on.

Why (2026-10-18): the studio started Codex (`codex exec`) as a child for every reading and painting. Inside an agent's
sandbox - Astra's, or the broker's (network denied) - the child cannot start ("failed to initialize in-process
app-server client: Operation not permitted"), and a delegated run stopped at its first beat picture. The agent running
the studio is itself a model that can look and paint: the studio asks, the agent answers through a fresh subagent that
sees only the request (not what the picture was meant to show), and the studio checks the answer as it checked
Codex's - the schema, the pictures unchanged since it asked, who answered. Measured the same day on the pilot's 55
fields (8 storyboard pictures): Codex 52/55, the same model as a fresh agent opening the files itself 52/55 with the
same 55 answers, a fresh Claude agent 54/55 with 2 answers different - so the reader's model is recorded and a project
whose readings come from different models is warned (READER_MIXED).

Where the studio may start a model itself is not a list of places: inside a sandbox (CODEX_SANDBOX: the child cannot
initialise there, network or not) or where the network is denied (CODEX_SANDBOX_NETWORK_DISABLED; a brokered command
always runs without it) the studio asks instead. STUDIO_ASK=1 asks everywhere; STUDIO_ASK=0 never asks.

Where requests live: `<project>/asks/<key of the target's path>/` (request.json, answer.json, a painting) - beside no
target, so a folder a command clears and makes again (frames proposals) keeps its answers, and no caller globbing its
own folder meets them (review 2026-10-18). A target outside any project (a reference measure in library/) asks under
`.studio/asks/`. A picture is known by its pixels, not its file's bytes: Blender stamps the render time into every PNG,
and a pass rendered again would have asked again for ever (review 2026-10-18).
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator

from .common import REPO, StudioError, file_hash, json_arg, now, read_json, stable_hash, write_json

KINDS = ('read', 'paint')
IN_SANDBOX = ('CODEX_SANDBOX',)                                           # set (any value) inside an agent's sandbox
NETWORK_DENIED = ('CODEX_SANDBOX_NETWORK_DISABLED', 'STUDIO_BROKERED')   # '1' where this process has no network
PAINT_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp')
MODEL_NAME = r'[a-z0-9][a-z0-9.\-]{1,60}'   # a model id as its maker writes it (gpt-6-astra, claude-opus-5-5)
# Where an answer may be written - default deny: a reading is a .json and a painting an image, inside a project (never
# its own records: project.json, a shot, decisions/, runs/, asks/) or, outside one, under the repository's data folders
# only (a reference measure's labels) - never the studio's code or skill. A request file is data the sandboxed side
# could write; whoever could forge one could write the file directly, so this keeps a mistaken request from reaching a
# record or the code, not more (review 2026-10-18).
ANSWER_SUFFIX = {'read': ('.json',), 'paint': PAINT_SUFFIXES}
NEVER_ANSWERED = ('project.json', 'shot.json')
NEVER_UNDER = ('decisions', 'runs', 'asks')
REPO_DATA = ('library', 'projects', '.studio')


def must_ask():
    """Whether the studio asks for a model's answer instead of starting the model here (module docstring)."""
    forced = os.environ.get('STUDIO_ASK')
    if forced in ('0', '1'):
        return forced == '1'
    return any(os.environ.get(name) for name in IN_SANDBOX) or any(os.environ.get(name) == '1' for name in NETWORK_DENIED)


def _owner(target):
    """(the folder a target's asks are keyed from, its asks folder, the target's path from there): its project, else
    the repository."""
    target = Path(target).resolve()
    project = next((p for p in target.parents if (p / 'project.json').is_file()), None)
    if project is not None:
        return project, project / 'asks', target.relative_to(project)
    repo = Path(REPO).resolve()
    if target.is_relative_to(repo):
        return repo, repo / '.studio' / 'asks', target.relative_to(repo)
    raise StudioError('INPUT_INVALID', f'{target}: a model answer is kept only for a studio project or the repository')


def ask_dir(target):
    _root, asks, rel = _owner(target)
    return asks / stable_hash(str(rel))[:16]


def picture_key(path):
    """A picture's identity by what it shows (its decoded pixels, size and mode), not its file's bytes: a pass rendered
    again carries a new render-time stamp and the same picture. A file that is no image is known by its bytes."""
    from PIL import Image
    if not Path(path).is_file():
        raise StudioError('INPUT_INVALID', f'{path}: no picture to ask about')
    try:
        with Image.open(path) as image:
            return stable_hash({'size': list(image.size), 'mode': image.mode, 'pixels': __import__('hashlib').sha256(image.tobytes()).hexdigest()})
    except Exception:  # noqa: BLE001 - not a picture: its bytes
        return file_hash(path)


def _pictures(images):
    return [{'path': str(Path(p).resolve()), 'key': picture_key(p)} for p in images]


def _key(kind, pictures, prompt, schema=None):
    return stable_hash({'kind': kind, 'pictures': [p['key'] for p in pictures], 'prompt': prompt, 'schema': schema})


def answered(kind, target, images, prompt, schema=None, force=False):
    """The answer handed in for exactly this question (same pictures, prompt and schema), else None. A read answer is
    written to `target` as Codex's would be; a painting is returned with the path of its kept file. Forced (read again),
    only an answer to a forced request counts, once: the next forced call asks again (review 2026-10-18: a forced read
    in a sandbox asked for ever, and the answer it got was never taken)."""
    try:
        folder = ask_dir(target)
    except StudioError:   # a target in no project nor the repository: nothing is kept for it, nothing was answered
        return None
    record = folder / 'answer.json'
    if not record.is_file():
        return None
    data = read_json(record)
    if (data.get('asked') or {}).get('key') != _key(kind, _pictures(images), prompt, schema):
        return None
    if force:
        if not (data.get('asked') or {}).get('forced'):
            return None
        data['asked']['forced'] = False   # taken once
        write_json(record, data)
    if kind == 'paint':
        kept = folder / data['image']
        if not kept.is_file() or file_hash(kept) != data['sha256']:   # the kept painting gone or changed: not answered
            return None
        return {**data, 'image': str(kept)}
    write_json(Path(target), {k: data[k] for k in ('strips', 'answer', 'usage_events', 'reader', 'asked')})
    return read_json(Path(target))


def settled(target):
    """A model started here answered: an older request for the same target is no longer waiting."""
    try:
        (ask_dir(target) / 'request.json').unlink(missing_ok=True)
    except StudioError:
        pass


def ask(kind, target, images, prompt, schema=None, label='', force=False):
    """Write the request for `target` and stop the command: StudioError OUTSIDE_MODEL_ASKED names the request file."""
    raise asked_error([request_for(kind, target, images, prompt, schema, label, force)], label or kind)


def request_for(kind, target, images, prompt, schema=None, label='', force=False):
    pictures = _pictures(images)
    request = ask_dir(target) / 'request.json'
    write_json(request, {'schema_version': 1, 'kind': kind, 'target': str(Path(target).resolve()), 'label': label,
                         'pictures': [p['path'] for p in pictures], 'prompt': prompt, **({'schema': schema} if schema is not None else {}),
                         'key': _key(kind, pictures, prompt, schema), 'picture_keys': [p['key'] for p in pictures],
                         **({'forced': True} if force else {}), 'asked_at': now()})
    return request


def asked_error(requests, what):
    """One stop for every request a command made (a loop asks all of its questions before it stops)."""
    listed = ', '.join(str(r) for r in requests[:4]) + (f' and {len(requests) - 4} more' if len(requests) > 4 else '')
    return StudioError('OUTSIDE_MODEL_ASKED',
                       f'{what}: the studio cannot start a model here (an agent sandbox) - it asks you instead: {listed}',
                       recovery=('give a fresh subagent only the request file (a read: answer as its schema says; a paint: one '
                                 'image from its pictures as its prompt says) - not what the picture was meant to show - then '
                                 'for each: studio ask answer --request <file> --answer <its JSON or image> --model <model id> '
                                 '--by "a fresh subagent", and run this command again'),
                       retryable=True, affected_ids=[str(r) for r in requests])


def gather(items, step):
    """step(item) for every item; the asks among them are made together and raised once at the end (a loop of frames
    is answered in one round, not one round per frame). Any other error stops at once."""
    out, requests = [], []
    for item in items:
        try:
            out.append(step(item))
        except StudioError as exc:
            if exc.code != 'OUTSIDE_MODEL_ASKED':
                raise
            requests += exc.affected_ids
            out.append(None)
    if requests:
        raise asked_error(requests, f'{len(requests)} questions')
    return out


def _check_target(request, asked):
    """The target an answer goes to is the one the request's own folder stands for, and never a record the studio keeps
    its decisions or runs in: a request file is data, not trusted to name where an answer is written."""
    target = Path(asked['target']).resolve()
    root, asks, rel = _owner(target)
    if request.resolve() != (asks / stable_hash(str(rel))[:16] / 'request.json').resolve():
        raise StudioError('INPUT_INVALID', f'{request} does not stand for {target} (a request answers only the target its folder is keyed by)')
    if target.suffix.lower() not in ANSWER_SUFFIX.get(asked.get('kind'), ()):
        raise StudioError('INPUT_INVALID', f"{target}: a {asked.get('kind')} answer is written only as {ANSWER_SUFFIX.get(asked.get('kind'))}")
    in_project = (root / 'project.json').is_file()
    if target.name in NEVER_ANSWERED or any(part in NEVER_UNDER for part in rel.parts[:-1]) \
            or (not in_project and rel.parts[0] not in REPO_DATA):
        raise StudioError('INPUT_INVALID', f"{target}: an answer is never written over the studio's own records or code")
    return target


def answer(request, given, model, by):
    """Hand in a fresh agent's answer to a request: checked against the request (its schema; the pictures unchanged
    since it was asked; the target its folder stands for) and kept where the asking command reads it."""
    request = Path(request)
    if not request.is_file():
        raise StudioError('INPUT_INVALID', f'no request at {request}')
    asked = read_json(request)
    if not isinstance(model, str) or not re.fullmatch(MODEL_NAME, model.strip().lower()):
        raise StudioError('INPUT_INVALID', f'--model is the model id that answered (e.g. gpt-6-astra), not {model!r}')
    if not isinstance(by, str) or len(by.strip()) < 3:
        raise StudioError('INPUT_INVALID', 'say who answered (--by "a fresh subagent")')
    target = _check_target(request, asked)
    for p, key in zip(asked['pictures'], asked['picture_keys']):
        if not Path(p).is_file() or picture_key(p) != key:
            raise StudioError('INPUT_INVALID', f'{p} changed since it was asked about - run the command again for a new request')
    folder = request.parent
    reader = {'model': model.strip().lower(), 'by': by.strip(), 'how': 'answered a request (studio ask answer)', 'at': now()}
    stamp = {'key': asked['key'], 'request': str(request), **({'forced': True} if asked.get('forced') else {})}
    if asked['kind'] == 'read':
        data = json_arg(given)
        errors = sorted(Draft202012Validator(asked['schema']).iter_errors(data), key=lambda e: list(e.path))
        if errors:
            raise StudioError('INPUT_INVALID', 'the answer does not fit the request: ' + '; '.join(f'{list(e.path)}: {e.message}' for e in errors[:6]),
                              recovery='answer again in the shape the schema says')
        record = {'strips': [Path(p).name for p in asked['pictures']], 'answer': data, 'usage_events': [], 'reader': reader, 'asked': stamp}
        write_json(folder / 'answer.json', record)
        write_json(target, record)
    else:
        from PIL import Image
        image = Path(str(given))
        if not image.is_file() or image.suffix.lower() not in PAINT_SUFFIXES:
            raise StudioError('INPUT_INVALID', f'the painting {image} is not an image file ({", ".join(PAINT_SUFFIXES)})')
        try:
            with Image.open(image) as opened:
                opened.verify()
        except Exception as exc:  # noqa: BLE001 - a file named .png that is no picture
            raise StudioError('INPUT_INVALID', f'the painting {image} does not open as an image: {exc}') from None
        kept = folder / ('painting' + image.suffix.lower())
        shutil.copy2(image, kept)
        write_json(folder / 'answer.json', {'image': kept.name, 'sha256': file_hash(kept), 'painter': reader, 'asked': stamp})
    request.unlink()
    mixed = readers_in(target) if asked['kind'] == 'read' else {}
    warnings = [f"READER_MIXED: the pictures here were read by {sorted(mixed)} - one picture may read differently by "
                'another model (2026-10-18: 2 of 55 answers differed); read the rest with one of them'] if len(mixed) > 1 else []
    return {'answered': str(target), 'kind': asked['kind'], 'model': reader['model'], 'warnings': warnings,
            'next': 'run the command that asked again'}


def pending(project):
    """Requests in a project still waiting for an answer (stale: a picture changed since - run the command again)."""
    from .project import project_dir
    out = []
    for p in sorted((project_dir(project) / 'asks').glob('*/request.json')):
        data = read_json(p)
        stale = any(not Path(f).is_file() or picture_key(f) != k for f, k in zip(data['pictures'], data['picture_keys']))
        out.append({'request': str(p), 'kind': data.get('kind'), 'label': data.get('label'), 'stale': stale})
    return out


def reader_of(record):
    """The model that read a reading record: an answered request names it; a reading made here is Codex's."""
    if (record or {}).get('reader'):
        return record['reader'].get('model') or record['reader'].get('by', '').split(',')[0].strip().lower()
    from .screen_grammar import LABELLER_MODEL
    return LABELLER_MODEL


def readers_in(target):
    """{model: readings} of the reading records (a JSON with strips and an answer: codex_json's shape) in the project
    the target lies in."""
    root, _asks, _rel = _owner(target)
    if not (root / 'project.json').is_file():
        return {}
    found = {}
    for f in root.rglob('*.json'):
        if f.relative_to(root).parts[0] in ('runs', 'asks', 'shots'):
            continue
        try:
            data = read_json(f)
        except StudioError:
            continue
        if isinstance(data, dict) and 'strips' in data and 'answer' in data:
            model = reader_of(data)
            found[model] = found.get(model, 0) + 1
    return found


def register_commands(subparsers):
    parser = subparsers.add_parser('ask', help="Requests the studio wrote for a model it could not start here: list them, "
                                               "answer them (a fresh subagent's reading or painting)")
    commands = parser.add_subparsers(dest='ask_command', required=True)
    p = commands.add_parser('pending', help="a project's requests waiting for an answer")
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: {'pending': pending(a.project)})
    p = commands.add_parser('answer', help='hand in the answer: a JSON reading (read) or an image file (paint)')
    p.add_argument('--request', required=True); p.add_argument('--answer', required=True)
    p.add_argument('--model', required=True, help='the model id that answered (gpt-6-astra, claude-opus-5-5)')
    p.add_argument('--by', required=True, help='who answered: a fresh subagent')
    p.set_defaults(handler=lambda a: answer(a.request, a.answer, a.model, a.by))
