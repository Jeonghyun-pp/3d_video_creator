"""Fill briefs: what fills a shot's levels is decided by the topic, with the user - never by a list of what a place
"usually has".

The agent drafts `shot.fill_brief` from the request, the narration and the references; every item says why it is there
(`why`, traced) and what it is for (`role`):

  subject   what the shot explains (the rebar columns of a column story, the sprinkler mains of a fire story)
  identity  the fewest cues that tell where we are (platform screen doors, a train) - at most 2 kinds per level
  ambient   life (people, a few cars) - a low density that never covers the subject

`fill propose` checks the draft and writes a sheet (fill/<shot>/brief.md: levels, roles, reasons, which elements the
library has and which must still be modelled). The agent shows it and asks; the user's words go in verbatim with
`fill revise --user-words ... --add/--remove` (history kept, items they add are `source: user`); `fill approve
--user-words` binds the approval to the brief's hash. Builds work with a proposed brief (a warning); renders and paid
generation need it approved and unchanged (FILL_BRIEF_UNAPPROVED / FILL_BRIEF_STALE, routing.assert_route).
A new topic is new data and, when the sheet says so, new exemplars - never new code.
"""
from __future__ import annotations

from copy import deepcopy
import fnmatch
import re

from .common import REPO, StudioError, now, read_json, stable_hash, write_json
from .project import load_shot, project_dir, shot_path

ROLES = ('subject', 'identity', 'ambient')
IDENTITY_KINDS_PER_LEVEL = 2
AMBIENT_MAX_PER_100M2 = 4.0
APPROVAL_FIELDS = ('status', 'approval', 'history')
SHEET = 'fill'


def brief_sha256(brief):
    """Hash of what the brief asks for (not its status, approval or history)."""
    return stable_hash({k: v for k, v in brief.items() if k not in APPROVAL_FIELDS})


def _library_ids():
    index = REPO / 'library' / 'exemplars' / 'index.json'
    return sorted({e['exemplar_id'] for e in read_json(index)['exemplars']}) if index.is_file() else []


def resolve_element(element, path=None):
    """[exemplar ids] or ['subject:<id>'] the element stands for ([] = not available yet)."""
    if element.startswith('subject:'):
        sid = element.split(':', 1)[1]
        return [element] if path is not None and (project_dir(path) / 'subjects' / sid / 'spec.json').is_file() else []
    ids = _library_ids()
    return fnmatch.filter(ids, element) if '*' in element else [element] if element in ids else []


def lint(brief, path=None):
    """{errors, warnings, missing}: errors block approval; missing elements need modelling before the build fills them."""
    errors, warnings, missing = [], [], []
    from .gates import is_error, severity_for
    severity = severity_for(path)
    seen = set()
    for level in brief.get('levels', []):
        items = level.get('items', [])
        if level.get('void') and items:
            errors.append(f"level {level['level_id']}: declared void but has items")
        if level.get('void') and not level.get('note'):
            warnings.append(f"level {level['level_id']}: void without a note saying why")
        kinds = {i['element'] for i in items if i['role'] == 'identity'}
        if len(kinds) > IDENTITY_KINDS_PER_LEVEL:
            (errors if is_error('fill_identity_kinds', severity) else warnings).append(
                f"level {level['level_id']}: {len(kinds)} identity kinds (max {IDENTITY_KINDS_PER_LEVEL}) - identity is the fewest cues, not a catalogue")
        for item in items:
            key = (level['level_id'], item['item_id'])
            if key in seen:
                errors.append(f"level {level['level_id']}: duplicate item_id {item['item_id']}")
            seen.add(key)
            if item['role'] not in ROLES:
                errors.append(f"{item['item_id']}: unknown role {item['role']}")
            if len(item.get('why', '').strip()) < 3:
                errors.append(f"{item['item_id']}: no reason (why) - trace it to the request, narration, a reference or the user")
            if item['layout'] == 'density' and not item.get('density_per_100m2'):
                errors.append(f"{item['item_id']}: layout density needs density_per_100m2")
            if item['layout'] != 'density' and not item.get('count') and not item.get('pitch_m'):
                errors.append(f"{item['item_id']}: layout {item['layout']} needs count or pitch_m")
            if item['role'] == 'ambient' and (item.get('density_per_100m2') or 0) > AMBIENT_MAX_PER_100M2:
                (errors if is_error('fill_ambient_density', severity) else warnings).append(f"{item['item_id']}: ambient density {item['density_per_100m2']} > {AMBIENT_MAX_PER_100M2}/100 m2 - life, not a crowd that hides the subject")
            if not resolve_element(item['element'], path):
                missing.append({'level': level['level_id'], 'item_id': item['item_id'], 'element': item['element'], 'role': item['role']})
    if not any(i['role'] == 'subject' for level in brief.get('levels', []) for i in level.get('items', [])):
        warnings.append('no subject item: what does this shot explain? (a pure establishing shot may have none - say so in the topic)')
    excluded = {e['element'] for e in brief.get('excluded', [])}
    for level in brief.get('levels', []):
        for item in level.get('items', []):
            if item['element'] in excluded:
                errors.append(f"{item['item_id']}: {item['element']} is also in excluded")
    return {'errors': errors, 'warnings': warnings, 'missing': missing}


def _phrases(path, shot):
    """Noun-ish phrases of the request and the shot's narration, for the agent and the user to trace items to."""
    project = read_json(project_dir(path) / 'project.json')
    texts = [str((project.get('brief') or {}).get('request', '')), str(shot.get('goal', ''))]
    narration = shot.get('narration') or {}   # one object per shot (shot schema); a list is accepted too
    texts += [str(n.get('text', '')) for n in (narration if isinstance(narration, list) else [narration]) if isinstance(n, dict)]
    words = re.findall(r'[가-힣A-Za-z0-9]{2,}', ' '.join(texts))
    return sorted(set(words))[:80]


def _sheet(path, shot, brief, report):
    from .exemplars import search
    lines = [f"# Fill brief — {shot['shot_id']} ({brief['status']})", '', f"**Topic:** {brief['topic']}", '']
    if brief.get('request_trace'):
        lines += ['**Traced from:** ' + '; '.join(f"{t['phrase']} ({t['source']})" for t in brief['request_trace']), '']
    lines += ['| level | role | element | layout | amount | why | source | in library |', '|---|---|---|---|---|---|---|---|']
    for level in brief['levels']:
        if level.get('void'):
            lines.append(f"| {level['level_id']} | void | — | — | — | {level.get('note', '')} | — | — |")
        for item in level['items']:
            amount = item.get('count') or (f"{item['density_per_100m2']}/100 m²" if item.get('density_per_100m2') else f"every {item.get('pitch_m')} m")
            have = ', '.join(resolve_element(item['element'], path)) or '**missing**'
            lines.append(f"| {level['level_id']} | {item['role']} | {item['element']} | {item['layout']} | {amount} | {item['why']} | {item['source']} | {have} |")
    if brief.get('excluded'):
        lines += ['', '**Left out:** ' + '; '.join(f"{e['element']} — {e['why']} ({e['source']})" for e in brief['excluded'])]
    if report['missing']:
        lines += ['', '**Not in the library yet (model as spec → fidelity → promote, or choose another element):**']
        for row in report['missing']:
            near = [r['exemplar_id'] for r in search(row['element'].replace('_', ' ').replace('*', ''), limit=3)['results']]
            lines.append(f"- {row['element']} ({row['role']}, {row['level']}) — closest in library: {', '.join(near) or 'none'}")
    for kind in ('errors', 'warnings'):
        if report[kind]:
            lines += ['', f'**{kind.title()}:**'] + [f'- {x}' for x in report[kind]]
    lines += ['', '**Request words** (trace items to these): ' + ', '.join(_phrases(path, shot)),
              '', 'Ask the user what to add or remove; record their words verbatim with `fill revise`, then `fill approve`.']
    if brief.get('history'):
        lines += ['', '**History:**'] + [f"- {h['at']}: \"{h['user_words']}\" → {h['change']}" for h in brief['history']]
    return '\n'.join(lines) + '\n'


def _write(path, shot_id, shot, brief):
    from .routing import _write_shot
    updated = deepcopy(shot)
    updated['fill_brief'] = brief
    _write_shot(path, shot_id, updated, shot['revision'])
    report = lint(brief, path)
    out = project_dir(path) / SHEET / shot_id
    out.mkdir(parents=True, exist_ok=True)
    (out / 'brief.md').write_text(_sheet(path, load_shot(path, shot_id), brief, report), encoding='utf-8')
    write_json(out / 'brief.json', {**brief, 'brief_sha256': brief_sha256(brief), 'lint': report})
    return {'shot_id': shot_id, 'status': brief['status'], 'brief_sha256': brief_sha256(brief), 'sheet': str(out / 'brief.md'),
            **report, 'artifacts': [str(out / 'brief.md'), str(out / 'brief.json')]}


def propose(project, shot_id, brief_file=None):
    """Set (or re-check) the agent's draft: status proposed, approval cleared, sheet written."""
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    brief = read_json(brief_file) if brief_file else shot.get('fill_brief')
    if not brief:
        raise StudioError('INPUT_INVALID', f'{shot_id} has no fill_brief; draft one (--brief file) from the request, narration and references')
    brief = {**deepcopy(brief), 'status': 'proposed', 'approval': None}
    brief.setdefault('history', [])
    return _write(path, shot_id, shot, brief)


def _parse_add(token):
    """role:element@level[:layout[:count]]"""
    m = re.fullmatch(r'(subject|identity|ambient):([^@]+)@([^:]+)(?::([a-z_]+))?(?::(\d+))?', token)
    if not m:
        raise StudioError('INPUT_INVALID', f'--add {token!r}: use role:element@level[:layout[:count]]')
    role, element, level, layout, count = m.groups()
    return level, {'role': role, 'element': element, 'layout': layout or ('density' if role == 'ambient' else 'along_edge'),
                   **({'count': int(count)} if count else {'density_per_100m2': 1.0} if (layout or '') == 'density' or (not layout and role == 'ambient') else {'count': 4})}


RECORD_KEYS = ('status', 'approval', 'history')   # decisions about the brief, written by propose/revise/approve only


def revise(project, shot_id, user_words, add=(), remove=(), agent_note=None, ops=()):
    """Apply what the user asked, in their words: items added are source user, removed ones move to excluded, and `ops`
    change any other value of the brief by path (studio/shot_edit.py grammar, paths inside the brief:
    {"op": "set", "path": "/levels/0/items/1/count", "value": 6})."""
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'fill revise')
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    brief = deepcopy(shot.get('fill_brief') or {})
    if not brief:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no fill_brief to revise (fill propose first)')
    changes = []
    for token in add or ():
        level_id, item = _parse_add(token)
        level = next((lv for lv in brief['levels'] if lv['level_id'] == level_id), None)
        if level is None:
            level = {'level_id': level_id, 'items': []}; brief['levels'].append(level)
        level.pop('void', None)
        base = re.sub(r'[^a-z0-9]+', '_', item['element'].lower()).strip('_') or 'item'
        item_id = base
        while any(i['item_id'] == item_id for i in level['items']):
            item_id += '_x'
        level['items'].append({'item_id': item_id, **item, 'why': f'user: "{words}"', 'source': 'user'})
        brief['excluded'] = [e for e in brief.get('excluded', []) if e['element'] != item['element']]
        changes.append(f"+{item['role']} {item['element']} @{level_id}")
    for element in remove or ():
        found = False
        for level in brief['levels']:
            before = len(level['items'])
            level['items'] = [i for i in level['items'] if i['element'] != element and i['item_id'] != element]
            found |= len(level['items']) != before
        if not found:
            raise StudioError('INPUT_INVALID', f'--remove {element}: not in the brief')
        brief.setdefault('excluded', []).append({'element': element, 'why': f'user: "{words}"', 'source': 'user'})
        changes.append(f'-{element}')
    from . import shot_edit
    ops = read_json(ops) if isinstance(ops, str) else (ops or [])
    holder = {'fill_brief': brief}
    for op in ops:
        if shot_edit.pointer(op['path'])[0] in RECORD_KEYS:
            raise StudioError('INPUT_INVALID', f"fill revise: {op['path']} is a decision record (status/approval/history are written by propose, revise and approve)")
        changes.append(shot_edit.apply(holder, {**op, 'path': '/fill_brief' + op['path']}, shot_edit.schema('shot'), label='fill brief').replace('/fill_brief', '', 1))
    from .blender_ops.content_keys import content_unread
    never_read = content_unread({'fill_brief': brief})
    if never_read:
        raise StudioError('INPUT_INVALID', 'fill revise: nothing reads ' + '; '.join(never_read[:6]))
    brief.setdefault('history', []).append({'at': now(), 'user_words': words, 'change': '; '.join(changes) or (agent_note or 'noted, no item change')})
    brief.update({'status': 'proposed', 'approval': None})
    return _write(path, shot_id, shot, brief)


def approve(project, shot_id, user_words):
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'fill approve')
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    brief = deepcopy(shot.get('fill_brief') or {})
    if not brief:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no fill_brief to approve')
    report = lint(brief, path)
    if report['errors']:
        raise StudioError('FILL_BRIEF_INVALID', '; '.join(report['errors'][:6]), recovery='Fix the brief (fill revise / propose) before approval')
    parents = {}
    from . import decisions   # on the ladder, a fill brief is bound to the approved shot list it fills
    if decisions.adopted(path):
        if decisions.state(path, 'shotlist')['state'] != 'approved':
            raise StudioError('DECISION_UNAPPROVED', f"{shot_id}: approve the shot list before its fill briefs",
                              recovery='decide approve --layer shotlist')
        parents = {'shotlist': decisions.body_sha256(decisions.envelope(path, 'shotlist')['body'])}
    brief.update({'status': 'approved', 'approval': {'user_words': words, 'brief_sha256': brief_sha256(brief), 'at': now(),
                                                     **({'parents': parents} if parents else {})}})
    return _write(path, shot_id, shot, brief)


def require_approved(shot, path=None):
    """Renders and paid generation of a shot with a fill brief need the approved, unchanged brief (and, on the decision
    ladder, the shot list it was approved against)."""
    brief = shot.get('fill_brief')
    if not brief:
        return
    if brief.get('status') != 'approved' or not brief.get('approval'):
        raise StudioError('FILL_BRIEF_UNAPPROVED', f"{shot['shot_id']}: the fill brief is {brief.get('status')}; show the sheet and get the user's approval",
                          recovery=f"fill propose → ask → fill revise --user-words … → fill approve --user-words …")
    if brief['approval']['brief_sha256'] != brief_sha256(brief):
        raise StudioError('FILL_BRIEF_STALE', f"{shot['shot_id']}: the fill brief changed after approval", recovery='fill approve again in the user\'s words')
    bound = brief['approval'].get('parents') or {}
    if bound and path is not None:
        from . import decisions
        shotlist = decisions.envelope(path, 'shotlist')
        if shotlist is None or decisions.body_sha256(shotlist['body']) != bound.get('shotlist'):
            raise StudioError('FILL_BRIEF_STALE', f"{shot['shot_id']}: the shot list changed after this fill brief was approved",
                              recovery='show the fill sheet again and fill approve in the user\'s words')


def show(project, shot_id):
    path = project_dir(project)
    brief = load_shot(path, shot_id).get('fill_brief')
    return {'shot_id': shot_id, 'fill_brief': brief, **(lint(brief, path) if brief else {}),
            'sheet': str(project_dir(path) / SHEET / shot_id / 'brief.md')}


def register_commands(subparsers):
    parser = subparsers.add_parser('fill', help='Fill briefs: what fills a shot by topic, decided with the user (HITL)')
    commands = parser.add_subparsers(dest='fill_command', required=True)
    p = commands.add_parser('propose'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--brief', help='JSON draft written by the agent (default: the shot\'s current fill_brief)')
    p.set_defaults(handler=lambda a: propose(a.project, a.shot, a.brief))
    p = commands.add_parser('revise'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--user-words', required=True); p.add_argument('--add', action='append', help='role:element@level[:layout[:count]]')
    p.add_argument('--remove', action='append', help='element or item_id'); p.add_argument('--agent-note')
    p.add_argument('--ops', help='JSON list of edits on any value of the brief: {op: set|add|remove, path: "/levels/0/items/1/count", value|factor|delta}')
    p.set_defaults(handler=lambda a: revise(a.project, a.shot, a.user_words, a.add, a.remove, a.agent_note, a.ops))
    p = commands.add_parser('approve'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--user-words', required=True)
    p.set_defaults(handler=lambda a: approve(a.project, a.shot, a.user_words))
    p = commands.add_parser('show'); p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: show(a.project, a.shot))
