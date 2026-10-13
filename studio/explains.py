"""What is said must be seen: what each script line explains, and whether that can be seen from outside.

A claim in facts says what it is about (`about`: a world element, a subject, subject/part or subject/feature). A line
explains what its claims are about; the beat pictures and the shots of that line must show it, and what sits inside its
subject (measured: studio/blender_ops/exposure_core.py, `studio subject exposure`) must be shown opened - by any row of
show_core.SHOW_METHODS that opens, never a method named here.

Why (2026-10-13): the rocket's beat pictures drew a closed booster for "유압 팔이 엔진을 기울여"; nothing knew what the
line was about nor that the arms sit inside. The user: "단면 등 하드코딩하지 말고 규칙으로 적용할 방법 고민해봐".
"""
from __future__ import annotations

from pathlib import Path

from .common import file_hash, read_json

EXPOSURE = 'exposure.json'   # subjects/<id>/exposure.json, written by `studio subject exposure`


def _specs(path):
    return {spec.parent.name: read_json(spec) for spec in sorted(Path(path, 'subjects').glob('*/spec.json'))}


def _part_words(spec, part_id):
    said = [f['description'] for f in spec.get('features', []) if part_id in f.get('part_ids', [])]
    return f"{part_id.replace('_', ' ')}" + (f" ({'; '.join(said[:2])})" if said else '')


def resolve(path, about_id, world=None, specs=None):
    """{id, kind: element|subject|part|feature, subject, parts: [subject/part], words}, or None when nothing is called that."""
    specs = _specs(path) if specs is None else specs
    if '/' in about_id:
        sid, rest = about_id.split('/', 1)
        spec = specs.get(sid)
        if spec is None:
            return None
        if rest in {b['part_id'] for b in spec.get('builders', [])}:
            return {'id': about_id, 'kind': 'part', 'subject': sid, 'parts': [about_id], 'words': f"{sid}: {_part_words(spec, rest)}"}
        feature = next((f for f in spec.get('features', []) if f['id'] == rest), None)
        if feature is None:
            return None
        return {'id': about_id, 'kind': 'feature', 'subject': sid, 'parts': [f'{sid}/{p}' for p in feature['part_ids']],
                'words': f"{sid}: {feature['description']}"}
    if about_id in specs:
        return {'id': about_id, 'kind': 'subject', 'subject': about_id, 'parts': [], 'words': str(specs[about_id].get('identity') or about_id)}
    element = next((e for e in (world or {}).get('elements', []) if e['id'] == about_id), None)
    if element is not None:
        return {'id': about_id, 'kind': 'element', 'subject': None, 'parts': [], 'words': element['description']}
    return None


def unknown(path, ids, world=None, bare=False):
    """The ids nothing is called. Without a world (the facts layer comes first) a bare name that is no subject may still
    become a world element: only `bare=True` (the world known) counts those."""
    specs = _specs(path)
    return [i for i in ids if resolve(path, i, world, specs) is None and (bare or '/' in i or i in specs)]


def things(path, ids, world=None):
    specs, out, seen = _specs(path), [], set()
    for i in ids:
        thing = resolve(path, i, world, specs)
        if thing is not None and thing['id'] not in seen:
            seen.add(thing['id']); out.append(thing)
    return out


def line_about(facts, script, line_ids):
    """The about ids of the claims the lines say, in the script's order. A claim with no `about` (facts approved before
    2026-10-13) explains nothing checkable."""
    claims = {c['claim_id']: c for c in (facts or {}).get('claims', [])}
    lines = {l['line_id']: l for l in (script or {}).get('lines', [])}
    out = []
    for line_id in line_ids:
        for claim_id in lines.get(line_id, {}).get('claim_ids') or []:
            out += [a for a in claims.get(claim_id, {}).get('about') or [] if a not in out]
    return out


def line_things(path, line_ids, facts, script, world=None):
    return things(path, line_about(facts, script, line_ids), world)


def exposure(path, subject_id):
    """The subject's survey and its state: (record or None, None | 'EXPOSURE_MISSING' | 'EXPOSURE_STALE')."""
    spec = Path(path, 'subjects', subject_id, 'spec.json')
    file = spec.parent / EXPOSURE
    if not file.is_file():
        return None, 'EXPOSURE_MISSING'
    record = read_json(file)
    return record, (None if record.get('spec_sha256') == file_hash(spec) else 'EXPOSURE_STALE')


def need(path, thing):
    """{inside: True|False|None, hidden_by: [words], problem}. None: not measured (a world element, or no survey yet -
    problem says which). A feature is inside only when every part of it is: one part outside can show it."""
    if thing['kind'] in ('element', 'subject'):
        return {'inside': None if thing['kind'] == 'element' else False, 'hidden_by': [], 'problem': None}
    record, problem = exposure(path, thing['subject'])
    if problem:
        return {'inside': None, 'hidden_by': [], 'problem': problem}
    from .blender_ops.exposure_core import inside
    rows = [record['parts'].get(p.split('/', 1)[1]) for p in thing['parts']]
    if not rows or any(r is None for r in rows):
        return {'inside': None, 'hidden_by': [], 'problem': 'EXPOSURE_STALE'}
    rows = [r for r in rows if r['best_share'] is not None]   # a part with nothing that renders shows nothing either way
    if not rows:
        return {'inside': None, 'hidden_by': [], 'problem': None}
    hidden = all(inside(r['best_share'], record['threshold']) for r in rows)
    by = []
    for r in rows:
        by += [h for h in r.get('hidden_by', []) if h not in by]
    return {'inside': hidden, 'hidden_by': by[:3] if hidden else [], 'problem': None}


def exposure_command(subject_id, project='<project>'):
    return f'studio subject exposure --project {project} --subject {subject_id}'


def open_words():
    """How the lints and the briefs name the ways to show an inside: every opening row of the table."""
    from .blender_ops.show_core import opening_methods
    return ', '.join(opening_methods())
