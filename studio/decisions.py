"""The decision ladder: what the video says and shows is settled with the user, layer by layer, before expensive work.

  brief -> facts -> script -> shotlist -> look        (per shot: fill brief, studio/fill.py)

Every layer works the same way (the fill brief's pattern, generalised): the agent proposes a body, a sheet is written,
the user's words come back verbatim (`decide revise --user-words ... --ops ...`), and `decide approve --sheet rNN`
binds the user's approval to that body and to the approved bodies of its parents. Staleness is computed, never
stored: when a parent changes, every layer below it is stale until approved again. An approved layer is projected into
the contracts the tools already read (project.json brief, sources.json, the shots' narration and timeline, look
presets); a hand edit of those afterwards is drift.

A project uses the ladder once its first decision is proposed (decisions/ladder.json); projects without it keep the
older flow. Gates (require) refuse builds, renders, paid generation, final voice, candidates and delivery whose layers
are not approved and fresh.
"""
from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator

from .common import REPO, StudioError, lock, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir, shot_path, validate_schema, validate_shot

LAYERS = {   # layer -> the layers it is bound to; order is the order of the conversation
    'brief': (),
    'facts': ('brief',),
    'script': ('facts',),
    'shotlist': ('script',),
    'look': ('shotlist',),
}
GATES = {    # operation -> layers that must be approved and fresh
    'build': ('shotlist',),
    'render': ('shotlist',),
    'render_look': ('shotlist', 'look'),
    'generate': ('shotlist', 'look'),
    'audio_final': ('script',),
    'candidate': ('facts', 'script'),
    'deliver': ('facts', 'script'),
}
CHARS_PER_S = 7.0      # provisional: Korean scratch narration speed, for the script length check (a warning)
LENGTH_TOLERANCE = 0.15
ROUNDS_BEFORE_ASKING = 3


def _root(path):
    return project_dir(path) / 'decisions'


def adopted(path):
    return (_root(path) / 'ladder.json').is_file()


def envelope(path, layer):
    file = _root(path) / f'{_layer(layer)}.json'
    return read_json(file) if file.is_file() else None


def _layer(layer):
    if layer not in LAYERS:
        raise StudioError('INPUT_INVALID', f'Unknown decision layer {layer!r} (layers: {list(LAYERS)})')
    return layer


def body_sha256(body):
    return stable_hash(body)


def validate_body(layer, body):
    schema = read_json(REPO / 'schemas' / 'studio-v1' / 'decision.schema.json')
    errors = sorted(Draft202012Validator({'$ref': f'#/$defs/{layer}', '$defs': schema['$defs']}).iter_errors(body), key=lambda e: list(e.path))
    if errors:
        raise StudioError('INPUT_INVALID', f'{layer}: ' + '; '.join(f'{list(e.path)}: {e.message}' for e in errors[:6]))


def state(path, layer):
    """{'state': missing|proposed|approved|stale, 'reason'}; stale names what changed."""
    env = envelope(path, layer)
    if env is None:
        return {'state': 'missing', 'reason': f'{layer} not proposed yet'}
    if env['status'] != 'approved':
        return {'state': 'proposed', 'reason': f"{layer} waits for the user's approval (sheet {env['sheet']['rev']})"}
    if env['approval']['body_sha256'] != body_sha256(env['body']):
        return {'state': 'stale', 'reason': f'{layer} was edited after approval'}
    for parent in LAYERS[layer]:
        parent_state = state(path, parent)
        if parent_state['state'] != 'approved':
            return {'state': 'stale', 'reason': f"{parent} is {parent_state['state']}: {parent_state['reason']}"}
        if env['approval']['parents'].get(parent) != body_sha256(envelope(path, parent)['body']):
            return {'state': 'stale', 'reason': f'{parent} changed after {layer} was approved'}
    return {'state': 'approved', 'reason': ''}


# --- lint (errors block approval; warnings go on the sheet) -------------------------------------------------------

def _digits(text):
    return any(ch.isdigit() for ch in text)


def lint(path, layer, body):
    errors, warnings = [], []
    parent = {p: (envelope(path, p) or {}).get('body') for p in LAYERS[layer]}
    if layer == 'facts':
        sources = {s['source_id'] for s in body['sources']}
        brief = (envelope(path, 'brief') or {}).get('body') or {}
        for claim in body['claims']:
            missing = [s for s in claim['source_ids'] if s not in sources]
            if not claim['source_ids'] or missing:
                errors.append(f"claim {claim['claim_id']}: sources {missing or 'none'} not listed")
            elif brief.get('subject_mode') == 'specific_real' and _digits(claim['text']) and len(set(claim['source_ids'])) < 2:
                errors.append(f"claim {claim['claim_id']}: a number about a real subject needs 2 independent sources")
    if layer == 'script':
        claims = {c['claim_id'] for c in ((parent.get('facts') or {}).get('claims') or [])}
        for line in body['lines']:
            if line.get('claim_ids') and line.get('illustrative'):
                errors.append(f"line {line['line_id']}: either claims or illustrative, not both")
            elif line.get('claim_ids'):
                errors += [f"line {line['line_id']}: claim {c} is not in the approved facts" for c in line['claim_ids'] if c not in claims]
            elif line.get('illustrative'):
                if _digits(line['text']):
                    errors.append(f"line {line['line_id']}: a number said aloud is a claim - source it in facts or drop it")
            else:
                errors.append(f"line {line['line_id']}: link it to claims or mark it illustrative")
        brief = (envelope(path, 'brief') or {}).get('body') or {}
        if brief.get('length_s'):
            seconds = sum(len(l['text'].replace(' ', '')) for l in body['lines']) / body.get('chars_per_s', CHARS_PER_S)
            if abs(seconds - brief['length_s']) > LENGTH_TOLERANCE * brief['length_s']:
                warnings.append(f"spoken length about {seconds:.0f} s against the brief's {brief['length_s']} s")
    if layer == 'shotlist':
        lines = [l['line_id'] for l in ((parent.get('script') or {}).get('lines') or [])]
        used = [i for s in body['shots'] for i in s['line_ids']]
        errors += [f'script line {i} is in no shot' for i in lines if i not in used]
        errors += [f'line {i} is in {used.count(i)} shots' for i in sorted(set(used)) if used.count(i) > 1]
        errors += [f'unknown script line {i}' for i in sorted(set(used) - set(lines))]
        ids = [s['shot_id'] for s in body['shots']]
        errors += [f'duplicate shot {i}' for i in sorted(set(ids)) if ids.count(i) > 1]
        brief = (envelope(path, 'brief') or {}).get('body') or {}
        total = sum(s['duration_s'] for s in body['shots'])
        if brief.get('length_s') and abs(total - brief['length_s']) > LENGTH_TOLERANCE * brief['length_s']:
            warnings.append(f"shots add up to {total:.1f} s against the brief's {brief['length_s']} s")
    if layer == 'look':
        presets = set(read_json(REPO / 'studio/blender_ops/look_data/look_presets.json')['presets'])
        names = [body['preset'], *body.get('per_shot', {}).values()]
        errors += [f'unknown look preset {n}' for n in names if n not in presets]
        shots = {s['shot_id'] for s in (((envelope(path, 'shotlist') or {}).get('body') or {}).get('shots') or [])}
        errors += [f'per_shot names unknown shot {s}' for s in body.get('per_shot', {}) if shots and s not in shots]
    return {'errors': errors, 'warnings': warnings}


# --- the sheet the user reads -------------------------------------------------------------------------------------

def _render_body(layer, body):
    if layer == 'brief':
        return [f"- **{k}**: {v}" for k, v in body.items()]
    if layer == 'facts':
        return (['**Sources**'] + [f"- `{s['source_id']}` {s['title']} {s.get('url') or s.get('citation') or ''}" for s in body['sources']]
                + ['', '**Claims**'] + [f"- `{c['claim_id']}` {c['text']} ← {', '.join(c['source_ids']) or 'no source'}" for c in body['claims']])
    if layer == 'script':
        return [f"{i + 1}. {l['text']}  _({'claims ' + ', '.join(l['claim_ids']) if l.get('claim_ids') else 'illustrative: ' + l.get('illustrative', '?')})_"
                for i, l in enumerate(body['lines'])]
    if layer == 'shotlist':
        return [f"- **{s['shot_id']}** {s['duration_s']} s — {s['purpose']} (lines {', '.join(s['line_ids']) or 'none'})" for s in body['shots']]
    return [f"- preset **{body['preset']}**"] + [f"- {k}: {v}" for k, v in body.get('per_shot', {}).items()]


def _sheet(path, layer, env, report, rev):
    lines = [f"# {layer.title()} — sheet {rev}", '', f"Status: {env['status']}. Bound to: {', '.join(LAYERS[layer]) or 'nothing (first layer)'}.", '']
    lines += _render_body(layer, env['body'])
    for kind in ('errors', 'warnings'):
        if report[kind]:
            lines += ['', f'**{kind.title()}:**'] + [f'- {x}' for x in report[kind]]
    if env.get('history'):
        lines += ['', '**What you asked → what changed:**'] + [f"- \"{h['user_words']}\" → {h['change']}" for h in env['history']]
    lines += ['', f'Approve exactly this sheet: `decide approve --layer {layer} --sheet {rev} --user-words "<the user\'s words>"`']
    return '\n'.join(lines) + '\n'


def _write(path, layer, env, report, event):
    root = _root(path)
    rev = f"r{len(list((root / 'sheets' / layer).glob('r*'))) + 1:02d}"
    sheet = root / 'sheets' / layer / rev / 'sheet.md'
    sheet.parent.mkdir(parents=True, exist_ok=True)
    env['sheet'] = {'rev': rev, 'path': str(sheet.relative_to(project_dir(path)))}
    sheet.write_text(_sheet(path, layer, env, report, rev), encoding='utf-8')
    write_json(root / f'{layer}.json', env)
    with (root / 'log.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'at': now(), 'layer': layer, 'event': event, 'rev': rev, 'body_sha256': body_sha256(env['body'])}, ensure_ascii=False) + '\n')
    return {'layer': layer, 'status': env['status'], 'sheet': str(sheet), 'rev': rev, **report, 'state': state(path, layer),
            'artifacts': [str(sheet), str(root / f'{layer}.json')]}


# --- propose / revise / approve -----------------------------------------------------------------------------------

def propose(project, layer, body):
    """The agent's draft (a dict or a JSON file): validated, linted, written as a sheet; status proposed."""
    path = project_dir(project)
    body = deepcopy(read_json(body) if not isinstance(body, dict) else body)
    validate_body(_layer(layer), body)
    root = _root(path); root.mkdir(parents=True, exist_ok=True)
    if not adopted(path):
        write_json(root / 'ladder.json', {'schema_version': 1, 'layers': list(LAYERS), 'adopted_at': now()})
    previous = envelope(path, layer) or {}
    env = {'schema_version': 1, 'layer': layer, 'status': 'proposed', 'body': body, 'approval': None,
           'history': previous.get('history', [])}
    return _write(path, layer, env, lint(path, layer, body), 'propose')


def _pointer(path_text):
    return [part.replace('~1', '/').replace('~0', '~') for part in path_text.lstrip('/').split('/')]


def apply_ops(body, ops):
    """[{op: set|add|remove, path: '/json/pointer', value}] on a copy of the body. The layer schema decides what a path
    may hold (validated after), so an edit outside the contract is refused."""
    body = deepcopy(body)
    for op in ops:
        parts = _pointer(op['path'])
        try:
            parent = body
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            key = parts[-1]
            if isinstance(parent, list):
                index = len(parent) if key == '-' else int(key)
                if op['op'] == 'remove':
                    parent.pop(index)
                elif op['op'] == 'add':
                    parent.insert(index, op['value'])
                else:
                    parent[index] = op['value']
            elif op['op'] == 'remove':
                parent.pop(key)
            else:
                parent[key] = op['value']
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise StudioError('INPUT_INVALID', f"edit {op.get('op')} {op['path']}: no such place ({type(error).__name__}: {error})") from None
    return body


def revise(project, layer, user_words, ops=None, body=None, agent_note=None):
    """What the user asked, in their words, applied as ops (or a whole new body); back to proposed with a new sheet."""
    from .generative.review import check_user_words
    words = check_user_words(user_words, f'{layer} revise')
    path = project_dir(project)
    env = envelope(path, _layer(layer))
    if env is None:
        raise StudioError('INPUT_INVALID', f'{layer}: nothing to revise (decide propose first)')
    ops = read_json(ops) if isinstance(ops, str) else (ops or [])
    for op in ops:
        validate_schema_op(op)
    new_body = deepcopy(read_json(body) if isinstance(body, str) else body) if body is not None else apply_ops(env['body'], ops)
    validate_body(layer, new_body)
    rounds = env.get('rounds_since_approval', 0) + 1
    env['rounds_since_approval'] = rounds
    env['history'] = env.get('history', []) + [{'at': now(), 'user_words': words, 'ops': ops, 'agent_note': agent_note,
                                                'change': agent_note or (f'{len(ops)} edit(s): ' + '; '.join(f"{o['op']} {o['path']}" for o in ops) if ops else 'new draft'),
                                                'before_sha256': body_sha256(env['body']), 'after_sha256': body_sha256(new_body)}]
    env.update({'status': 'proposed', 'approval': None, 'body': new_body})
    report = lint(path, layer, new_body)
    if rounds > ROUNDS_BEFORE_ASKING:
        report['warnings'].append(f'DECISION_ROUNDS: {rounds} revisions of {layer} without approval - ask a framing question instead of iterating')
    return _write(path, layer, env, report, 'revise')


def validate_schema_op(op):
    schema = read_json(REPO / 'schemas' / 'studio-v1' / 'decision.schema.json')
    errors = list(Draft202012Validator({'$ref': '#/$defs/op', '$defs': schema['$defs']}).iter_errors(op))
    if errors:
        raise StudioError('INPUT_INVALID', f'edit op {op}: {errors[0].message}')


def approve(project, layer, user_words, sheet_rev):
    """Bind the user's approval to the body on the sheet they saw (the latest) and to the parents' approved bodies;
    then project it into the contracts the tools read."""
    from .generative.review import check_user_words
    words = check_user_words(user_words, f'{layer} approve')
    path = project_dir(project)
    env = envelope(path, _layer(layer))
    if env is None:
        raise StudioError('INPUT_INVALID', f'{layer}: nothing to approve')
    if sheet_rev != env['sheet']['rev']:
        raise StudioError('DECISION_STALE', f"{layer}: the latest sheet is {env['sheet']['rev']}, not {sheet_rev}; show it and ask again")
    report = lint(path, layer, env['body'])
    if report['errors']:
        raise StudioError('DECISION_INVALID', f'{layer}: ' + '; '.join(report['errors'][:6]), recovery='Fix the draft (decide revise) before approval')
    parents = {}
    for parent in LAYERS[layer]:
        parent_state = state(path, parent)
        if parent_state['state'] != 'approved':
            raise StudioError('DECISION_UNAPPROVED', f"{layer} is bound to {parent}, which is {parent_state['state']}: {parent_state['reason']}",
                              recovery=f'Approve {parent} first')
        parents[parent] = body_sha256(envelope(path, parent)['body'])
    env['rounds_since_approval'] = 0
    env.update({'status': 'approved', 'approval': {'user_words': words, 'body_sha256': body_sha256(env['body']), 'parents': parents,
                                                   'sheet_rev': sheet_rev, 'at': now()}})
    result = _write(path, layer, env, report, 'approve')
    result['materialized'] = MATERIALIZE[layer](path, env['body'])
    return result


# --- projection into the contracts the tools read -----------------------------------------------------------------

def _project_update(path, change):
    with lock(path / '.project.lock', blocking=False):
        project = load_project(path)
        change(project)
        project['revision'] += 1
        validate_schema(project, 'project')
        write_json(path / 'project.json', project)
    return project


def _brief_projection(body):
    return {'key_message': body['key_message'], 'subject_mode': body['subject_mode'], 'request': body['topic']}


def _materialize_brief(path, body):
    def change(project):
        project['brief'].update(_brief_projection(body))
        project['output']['target_seconds'] = body['length_s']
    _project_update(path, change)
    return ['project.json brief', 'output.target_seconds']


def _materialize_facts(path, body):
    write_json(path / 'sources.json', {'schema_version': 1, 'sources': body['sources'], 'claims': body['claims']})
    return ['sources.json']


def _sentence_links(line):
    from .facts import sentences
    link = {'claim_ids': line['claim_ids']} if line.get('claim_ids') else {'illustrative': line.get('illustrative', 'framing line')}
    return [deepcopy(link) for _ in sentences(line['text'])]


def _materialize_shotlist(path, body):
    """Shots and timeline from the approved shot list and script: each shot's narration is its lines, each sentence
    linked as the script linked it. Shots that already have versions are updated, never deleted."""
    from .routing import propose_route
    project = load_project(path)
    fps = project['output']['fps']
    lines = {l['line_id']: l for l in envelope(path, 'script')['body']['lines']}
    policy = project.get('route_policy', {'allow_generative': True, 'budget_usd': 0, 'turnaround_required': True})
    timeline, offset, written = [], 0, []
    for entry in body['shots']:
        frames = max(1, round(entry['duration_s'] * fps))
        text = ' '.join(lines[i]['text'].strip() for i in entry['line_ids'])
        links = [link for i in entry['line_ids'] for link in _sentence_links(lines[i])]
        file = shot_path(path, entry['shot_id'])
        if file.is_file():
            shot = load_shot(path, entry['shot_id'])
            shot['revision'] += 1
        else:
            from .project import default_shot
            shot = default_shot(entry['shot_id'], frames, project['brief'])
            if entry.get('route_features'):
                shot['route'] = {'features': list(entry['route_features'])}
                proposed = propose_route(shot, policy)
                del shot['route']
                if proposed['mode'] == 'blender':
                    shot['route'] = proposed
        shot['duration_frames'] = frames
        shot['goal'] = entry['purpose']
        shot['narration'] = {**shot['narration'], 'text': text, 'sentence_claims': links}
        validate_shot(shot)
        write_json(file, shot)
        written.append(entry['shot_id'])
        timeline.append({'shot_id': entry['shot_id'], 'start_frame': offset, 'frame_count': frames})
        offset += frames
    removed = [e['shot_id'] for e in project['shots'] if e['shot_id'] not in written]

    def change(project):
        project['shots'] = timeline
    _project_update(path, change)
    return {'shots': written, 'removed_from_timeline': removed}


def _materialize_look(path, body):
    from .routing import _write_shot
    for entry in load_project(path)['shots']:
        shot = load_shot(path, entry['shot_id'])
        wanted = body.get('per_shot', {}).get(entry['shot_id'], body['preset'])
        if shot['render'].get('look_preset') != wanted:
            updated = deepcopy(shot); updated['render']['look_preset'] = wanted
            _write_shot(path, entry['shot_id'], updated, shot['revision'])
    return ['shots render.look_preset']


MATERIALIZE = {'brief': _materialize_brief, 'facts': _materialize_facts, 'script': lambda path, body: [],
               'shotlist': _materialize_shotlist, 'look': _materialize_look}


def drift(path, layer):
    """Where the live contracts no longer say what the approved layer says (a hand edit after approval)."""
    env = envelope(path, layer)
    if env is None or env['status'] != 'approved':
        return []
    body, project = env['body'], load_project(path)
    if layer == 'brief':
        live = {k: project['brief'].get(k) for k in _brief_projection(body)}
        return [f'project.brief.{k}' for k, v in _brief_projection(body).items() if live[k] != v]
    if layer == 'facts':
        sources = read_json(path / 'sources.json') if (path / 'sources.json').is_file() else {}
        return [] if (sources.get('sources'), sources.get('claims')) == (body['sources'], body['claims']) else ['sources.json']
    if layer == 'shotlist':
        planned = [s['shot_id'] for s in body['shots']]
        return [] if [e['shot_id'] for e in project['shots']] == planned else ['project.shots timeline']
    if layer == 'look':
        return [f"{e['shot_id']} look_preset" for e in project['shots']
                if load_shot(path, e['shot_id'])['render'].get('look_preset') != body.get('per_shot', {}).get(e['shot_id'], body['preset'])]
    return []


def require(path, operation):
    """Gate: the layers this operation stands on are approved, fresh and not drifted (only for projects on the ladder)."""
    path = project_dir(path)
    if not adopted(path):
        return
    for layer in GATES[operation]:
        layer_state = state(path, layer)
        if layer_state['state'] in ('missing', 'proposed'):
            raise StudioError('DECISION_UNAPPROVED', f"{operation} needs the {layer} approved: {layer_state['reason']}",
                              recovery=f'decide propose / show the sheet / decide approve --layer {layer}')
        if layer_state['state'] == 'stale':
            raise StudioError('DECISION_STALE', f"{operation}: {layer} is stale - {layer_state['reason']}",
                              recovery=f'Show the {layer} sheet again and get a new approval')
        changed = drift(path, layer)
        if changed:
            raise StudioError('DECISION_DRIFT', f"{operation}: {', '.join(changed)} no longer match the approved {layer}",
                              recovery=f'Put the change through decide revise --layer {layer} (or undo the hand edit)')


def status(project):
    path = project_dir(project)
    rows = []
    for layer in LAYERS:
        env = envelope(path, layer)
        rows.append({'layer': layer, **state(path, layer), 'sheet': (env or {}).get('sheet'), 'drift': drift(path, layer)})
    return {'adopted': adopted(path), 'layers': rows,
            'next': next((f"{r['layer']}: {r['reason']}" for r in rows if r['state'] != 'approved'), 'all layers approved')}


def adopt(project):
    """Draft every layer from an existing project's contracts (proposed): the user approves each before the gates
    pass. For projects made before the ladder."""
    from .facts import sentences
    path = project_dir(project)
    project_data = load_project(path)
    brief = project_data['brief']
    shots = [load_shot(path, e['shot_id']) for e in project_data['shots']]
    fps = project_data['output']['fps']
    sources = read_json(path / 'sources.json') if (path / 'sources.json').is_file() else {'sources': [], 'claims': []}
    lines, shot_rows = [], []
    for shot in shots:
        said = sentences(shot['narration'].get('text', ''))
        links = shot['narration'].get('sentence_claims') or [{} for _ in said]
        ids = []
        for k, (sentence, link) in enumerate(zip(said, links + [{}] * (len(said) - len(links)))):
            line_id = f"{shot['shot_id']}-{k + 1}"
            lines.append({'line_id': line_id, 'text': sentence, **({'claim_ids': link['claim_ids']} if link.get('claim_ids')
                                                                   else {'illustrative': link.get('illustrative', 'adopted: not yet linked')})})
            ids.append(line_id)
        shot_rows.append({'shot_id': shot['shot_id'], 'purpose': shot.get('goal') or 'adopted shot', 'line_ids': ids,
                          'duration_s': round(shot['duration_frames'] / fps, 3)})
    drafts = {'brief': {'topic': brief.get('request') or project_data['project_id'], 'audience': 'general', 'length_s': project_data['output']['target_seconds'],
                        'key_message': brief.get('key_message') or brief.get('request') or 'to be agreed', 'subject_mode': brief.get('subject_mode', 'schematic')},
              'facts': {'sources': [s for s in sources.get('sources', []) if s.get('source_id') and s.get('title')],
                        'claims': [c for c in sources.get('claims', []) if c.get('claim_id')]},
              'script': {'lines': lines or [{'line_id': 'l1', 'text': '-', 'illustrative': 'no narration'}]},
              'shotlist': {'shots': shot_rows}}
    return {'adopted': [propose(path, layer, body)['layer'] for layer, body in drafts.items()],
            'note': 'every layer is proposed: show each sheet and approve in the user\'s words, in order'}


def register_commands(subparsers):
    parser = subparsers.add_parser('decide', help='The decision ladder: brief, facts, script, shot list, look - settled with the user')
    commands = parser.add_subparsers(dest='decide_command', required=True)
    p = commands.add_parser('status'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: status(a.project))
    p = commands.add_parser('propose'); p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.add_argument('--body', required=True, help='JSON draft of the layer body (schemas/studio-v1/decision.schema.json)')
    p.set_defaults(handler=lambda a: propose(a.project, a.layer, a.body))
    p = commands.add_parser('revise'); p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.add_argument('--user-words', required=True); p.add_argument('--ops', help='JSON list of {op, path, value}'); p.add_argument('--body')
    p.add_argument('--agent-note')
    p.set_defaults(handler=lambda a: revise(a.project, a.layer, a.user_words, a.ops, a.body, a.agent_note))
    p = commands.add_parser('approve'); p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.add_argument('--user-words', required=True); p.add_argument('--sheet', required=True, help='The sheet revision the user saw (rNN)')
    p.set_defaults(handler=lambda a: approve(a.project, a.layer, a.user_words, a.sheet))
    p = commands.add_parser('show'); p.add_argument('--project', required=True); p.add_argument('--layer', required=True, choices=list(LAYERS))
    p.set_defaults(handler=lambda a: {**(envelope(a.project, a.layer) or {}), 'state': state(a.project, a.layer)})
    p = commands.add_parser('adopt'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: adopt(a.project))
