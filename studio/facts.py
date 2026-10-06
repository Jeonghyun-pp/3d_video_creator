"""Facts behind the narration: every sentence a viewer hears either rests on a sourced claim or is marked illustrative.

sources.json is the one record: sources [{source_id, title, url | citation, retrieved_at, excerpt}] and claims
[{claim_id, text, source_ids}]. Each shot's narration.sentence_claims lines up with its sentences (split on sentence
punctuation) and gives, per sentence, {claim_ids: [...]} or {illustrative: "<why this is not a claim>"}.
An illustrative sentence carries no digits: a number said aloud is a claim. Candidates and delivery refuse problems;
rough edits only warn.
"""
from __future__ import annotations

import re

from .common import StudioError, file_hash, read_json
from .project import load_project, load_shot, project_dir

SPLIT = re.compile(r'(?<=[.!?。？！…])\s+')
DIGIT = re.compile(r'\d')


def sentences(text):
    return [part.strip() for part in SPLIT.split((text or '').strip()) if part.strip()]


def load_sources(path):
    file = project_dir(path) / 'sources.json'
    return read_json(file) if file.is_file() else {'schema_version': 1, 'sources': [], 'claims': []}


def sources_sha256(path):
    file = project_dir(path) / 'sources.json'
    return file_hash(file) if file.is_file() else None


def check(path, shots=None):
    """[{code, shot_id?, sentence?, detail}] for the project's narration (or the given shot snapshots)."""
    path = project_dir(path)
    record = load_sources(path)
    source_ids = {s.get('source_id') for s in record.get('sources', [])}
    claims = {c.get('claim_id'): c for c in record.get('claims', [])}
    problems = []
    for claim_id, claim in claims.items():
        missing = [s for s in claim.get('source_ids', []) if s not in source_ids]
        if not claim.get('source_ids') or missing:
            problems.append({'code': 'FACTS_SOURCE_MISSING', 'claim_id': claim_id, 'detail': f'sources {missing or "none"} not in sources.json'})
    if shots is None:
        shots = [load_shot(path, e['shot_id']) for e in load_project(path)['shots']]
    for shot in shots:
        narration = shot.get('narration') or {}
        said = sentences(narration.get('text', ''))
        if not said:
            continue
        links = narration.get('sentence_claims')
        if links is None:
            problems += [{'code': 'FACTS_UNSOURCED', 'shot_id': shot['shot_id'], 'sentence': s, 'detail': 'no claim and not marked illustrative'} for s in said]
            continue
        if len(links) != len(said):
            problems.append({'code': 'FACTS_MISALIGNED', 'shot_id': shot['shot_id'],
                             'detail': f'{len(said)} sentences but {len(links)} sentence_claims entries; relink after editing the text'})
            continue
        for sentence, link in zip(said, links):
            if 'illustrative' in link:
                if DIGIT.search(sentence):
                    problems.append({'code': 'FACTS_ILLUSTRATIVE_NUMBER', 'shot_id': shot['shot_id'], 'sentence': sentence,
                                     'detail': 'a number said aloud is a claim: source it or drop the number'})
            elif link.get('claim_ids'):
                problems += [{'code': 'FACTS_UNKNOWN_CLAIM', 'shot_id': shot['shot_id'], 'sentence': sentence, 'detail': f'claim {c} not in sources.json'}
                             for c in link['claim_ids'] if c not in claims]
            else:
                problems.append({'code': 'FACTS_UNSOURCED', 'shot_id': shot['shot_id'], 'sentence': sentence, 'detail': 'empty link'})
    return problems


def require(path, shots=None, purpose='candidate'):
    problems = check(path, shots)
    if problems:
        first = problems[0]
        raise StudioError(first['code'], f'{purpose} blocked: {len(problems)} narration fact problem(s); first: '
                          f"{first.get('shot_id', '')} {first.get('sentence', first.get('claim_id', ''))!r} - {first['detail']}",
                          recovery='Link each sentence to a sourced claim in sources.json, or mark it illustrative (no numbers); run facts check')
    return {'status': 'ok', 'sources_sha256': sources_sha256(path)}


def register_commands(subparsers):
    parser = subparsers.add_parser('facts', help='Narration sentences vs sourced claims').add_subparsers(dest='facts_command', required=True)
    sub = parser.add_parser('check'); sub.add_argument('--project', required=True)
    sub.set_defaults(handler=lambda a: {'problems': check(a.project), 'sources_sha256': sources_sha256(a.project)})
