"""What a collected reference photo is of, and whether it may be attached to a picture the agent draws.

A photo collected by `reference collect` (reference_fetch.py) is for comparison: it goes to no generation model without
the user's words. This module keeps the two things that decide use, beside the index (which the download worker owns):
- what each photo is of (`about`, the ids facts use: a world element, subject, subject/part or subject/feature) - by
  the files a `collect --about` job brought, or by file (`reference about`); a row's own subject_id counts too;
- which photos never go out (`reference exclude`: a logo, a real face), and the user's words that allow attaching the
  rest to the image tool (`reference attach-consent`, decisions/reference_attach.json).
concept brief --beat attaches, for each thing it asks for, photos about that thing - only with the consent, never an
excluded one - and asks for a collect when there is none.

Why (2026-10-13): the user, on drawing the rocket's insides: "관련 이미지를 찾아서 그린다 - 이 생각을 할 수 있을까?",
and allowed attaching ("진행해. 첨부도 가능") - logos and real faces left out.
"""
from __future__ import annotations

from pathlib import Path

from .common import StudioError, now, read_json, write_json
from .project import project_dir

STORE = 'references/photos/about.json'
CONSENT = 'decisions/reference_attach.json'
PER_THING = 2      # photos attached per thing a picture must show
PER_PICTURE = 4    # and per picture: the image tool reads a few references, not a stack


def _store(path):
    file = Path(path, STORE)
    store = read_json(file) if file.is_file() else {}
    return {'schema_version': 1, 'jobs': {}, 'files': {}, 'excluded': {}, **store}


def check_ids(path, ids):
    """Refuse ids that name nothing (a typo would never match a photo nor count toward a redraw limit)."""
    from .decisions import envelope
    from .explains import unknown
    missing = unknown(path, ids, (envelope(path, 'world') or {}).get('body'), bare=True)
    if missing:
        raise StudioError('INPUT_INVALID', f'{missing} name nothing: a world element, subject, subject/part or subject/feature')


def record_job(path, job_id, about):
    """collect --about: what this job's photos are of, until it finishes (the brokered worker runs later)."""
    if about:
        check_ids(path, about)
        store = _store(path)
        store['jobs'][job_id] = list(about)
        write_json(Path(path, STORE), store)


def record_result(path, job_id, result):
    """A finished collect job: every file it collected - new or already in the index (the worker reuses a known
    photo with its old query) - is about what the job was asked for. Files, not titles or queries: the source
    renames titles (File:, spaces, capitals)."""
    store = _store(path)
    about = store['jobs'].pop(job_id, None)
    if about is None or not result:
        return
    for row in result.get('collected') or []:
        store['files'][row['file']] = sorted(set(store['files'].get(row['file'], [])) | set(about))
    write_json(Path(path, STORE), store)


def _row(path, file):
    from .reference_fetch import index_rows
    row = next((r for r in index_rows(path) if r['file'] in (file, f'references/photos/{Path(file).name}')), None)
    if row is None:
        raise StudioError('INPUT_INVALID', f'{file} is no collected photo (references/photos/index.json)')
    return row


def about(project, file, ids):
    path = project_dir(project)
    check_ids(path, ids)
    row = _row(path, file)
    store = _store(path)
    store['files'][row['file']] = sorted(set(store['files'].get(row['file'], [])) | set(ids))
    write_json(Path(path, STORE), store)
    return {'file': row['file'], 'about': store['files'][row['file']]}


def exclude(project, file, note):
    if not note or len(note.strip()) < 4:
        raise StudioError('INPUT_INVALID', 'reference exclude needs --note (what shows: a logo, a real face)')
    path = project_dir(project)
    row = _row(path, file)
    store = _store(path)
    store['excluded'][row['file']] = {'note': note.strip(), 'at': now()}
    write_json(Path(path, STORE), store)
    return {'file': row['file'], 'excluded': store['excluded'][row['file']]}


def consent(project, user_words):
    """The user's words allowing collected photos to be attached to the agent's image tool (verbatim, at least 4 characters)."""
    from .gates import weakening   # the user's own words (check_user_words), never a delegated run's launch words
    path = project_dir(project)
    words = weakening(user_words, 'attach collected photos', 'attach-consent', path)['user_words']
    record = {'schema_version': 1, 'user_words': words, 'at': now(), 'scope': 'collected reference photos to the agent image tool, '
              'excluded files never'}
    write_json(Path(path, CONSENT), record)
    return record


def consented(path):
    file = Path(path, CONSENT)
    return read_json(file) if file.is_file() else None


def about_of(store, row):
    return {*store['files'].get(row['file'], []), *([row['subject_id']] if row.get('subject_id') else [])}


def photos_for(path, things):
    """{thing id: [index rows]} - photos about each thing (its id or one of its parts), excluded ones left out."""
    from .reference_fetch import index_rows
    store = _store(path)
    rows = [r for r in index_rows(path) if r['file'] not in store['excluded'] and (Path(path) / r['file']).is_file()]
    return {t['id']: [r for r in rows if {t['id'], *t['parts']} & about_of(store, r)] for t in things}


def attachments(path, things):
    """(attach [{path, why}], compare_only [paths], missing [thing ids]) for a picture that must show `things`."""
    found = photos_for(path, things)
    allowed = consented(path) is not None
    attach, compare, missing = [], [], []
    for thing in things:
        rows = found[thing['id']]
        if not rows:
            missing.append(thing['id'])
            continue
        for r in rows[:PER_THING]:
            why = f"a photo of {thing['id']} ({r.get('licence')}, {r.get('artist') or 'unknown author'}): its shape and parts, not its logos or faces"
            if allowed and len(attach) < PER_PICTURE:
                attach.append({'path': r['file'], 'why': why})
            else:
                compare.append(r['file'])
    return attach, compare, missing


def collect_hint(path, thing):
    """A collect command for a thing with no photo: the query from its own words (spec / world), never a typed name."""
    from .explains import _specs
    words = thing['words'].split(':', 1)[-1].split('(')[0].strip()
    identity = str((_specs(path).get(thing.get('subject') or '') or {}).get('identity') or '')
    words = words.split(',')[0]   # the first clause names it; a search of a whole description finds nothing
    query = ' '.join(list(dict.fromkeys(f"{' '.join(identity.split()[:3])} {words}".replace('_', ' ').split()))[:8])
    return f'studio reference collect --project {path} --about {thing["id"]} --query "{query}"'
