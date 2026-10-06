"""Subject specs: a request decomposed into measurable, sourced requirements (schemas/studio-v1/subject.schema.json).

Principle: every visual element of the user's words maps to a checkable item, every number carries a
source (or says 'assumed' openly), and geometry comes from builders driven by the spec, not from
numbers typed into author scripts.
"""
from __future__ import annotations

import re
from pathlib import Path

from .blender_ops.relations_core import BUILTIN_ANCHORS, CLAIM_RULES, relation_order, split_ref
from .common import REPO, StudioError, check_id, read_json, write_json
from .project import project_dir, validate_schema

SHOT_PREFIXES = ('shot.', 'camera.', 'motion.', 'look.', 'route.')  # request phrases may map to shot-level decisions
IMAGE_KINDS = {'drawing', 'photo'}
REAL_DIMENSIONS = REPO / 'studio/blender_ops/look_data/real_dimensions.json'
_MISSING = object()
DEVIATION_KEY = {'dimension': 'factor', 'proportion': 'factor', 'silhouette': 'min_iou', 'assembly': 'waive', 'feature': 'waive'}


def resolve_pointer(document, pointer):
    """RFC 6901 JSON pointer lookup; returns _MISSING when any step does not exist."""
    node = document
    for raw in pointer.split('/')[1:]:
        key = raw.replace('~1', '/').replace('~0', '~')
        if isinstance(node, list):
            if not key.isdigit() or int(key) >= len(node):
                return _MISSING
            node = node[int(key)]
        elif isinstance(node, dict):
            if key not in node:
                return _MISSING
            node = node[key]
        else:
            return _MISSING
    return node


def set_pointer(document, pointer, value):
    keys = [k.replace('~1', '/').replace('~0', '~') for k in pointer.split('/')[1:]]
    node = document
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        node[int(keys[-1])] = value
    else:
        node[keys[-1]] = value


def _dim_roles():
    return {c['id'] for c in read_json(REAL_DIMENSIONS)['categories']} | {'none'}


def spec_path(project, subject_id):
    return project_dir(project) / 'subjects' / check_id(subject_id) / 'spec.json'


def load_spec(project, subject_id):
    spec = read_json(spec_path(project, subject_id))
    validate_schema(spec, 'subject')
    return spec


def _uncovered(request, phrases):
    """Content characters of the request that no trace phrase covers (verbatim, case-insensitive)."""
    text = request.lower()
    covered = [False] * len(text)
    for phrase in phrases:
        p = phrase.lower().strip()
        if not p:
            continue
        for match in re.finditer(re.escape(p), text):
            for i in range(match.start(), match.end()):
                covered[i] = True
    gaps, current = [], ''
    for ch, ok in zip(text, covered):
        if not ok and (ch.isalnum() or '가' <= ch <= '힣'):
            current += ch
        elif current:
            gaps.append(current); current = ''
    if current:
        gaps.append(current)
    return gaps


def lint_spec(spec, project=None):
    """Machine checks beyond the schema. Returns {'errors': [...], 'warnings': [...]}."""
    validate_schema(spec, 'subject')
    errors, warnings = [], []
    part_ids = {b['part_id'] for b in spec['builders']}
    sources = {s['id']: s for s in spec['sources']}
    item_ids = part_ids | {d['id'] for d in spec['dimensions']} | {f['id'] for f in spec['features']} | \
        {p['id'] for p in spec.get('proportions', [])}
    phrases = [t['phrase'] for t in spec['request_trace']]
    for phrase in phrases:
        if phrase.lower() not in spec['request'].lower():
            errors.append(f'trace phrase not in the request verbatim: {phrase!r}')
    gaps = _uncovered(spec['request'], phrases)
    if gaps:
        errors.append(f'request words not traced to any spec item: {gaps}')
    for trace in spec['request_trace']:
        if not trace['items']:
            errors.append(f"phrase {trace['phrase']!r} maps to nothing; add the requirement it implies or a note why it needs none")
        for item in trace['items']:
            if item not in item_ids and not item.startswith(SHOT_PREFIXES):
                errors.append(f"phrase {trace['phrase']!r} maps to unknown item {item!r}")
    real = spec['subject_mode'] == 'specific_real'
    dimension_sources = set()
    for dim in spec['dimensions']:
        if dim['source_id'] == 'assumed':
            (errors if real else warnings).append(f"dimension {dim['id']} is assumed (no source)")
        elif dim['source_id'] not in sources:
            errors.append(f"dimension {dim['id']} cites unknown source {dim['source_id']}")
        else:
            dimension_sources.add(dim['source_id'])
        for part in dim.get('part_ids', []):
            if part not in part_ids:
                errors.append(f"dimension {dim['id']} refers to unknown part {part}")
    if real and len(dimension_sources) < 2:
        errors.append(f'specific_real subjects need dimensions from at least 2 independent sources (have {sorted(dimension_sources)})')
    dims = {d['id'] for d in spec['dimensions']}
    for prop in spec.get('proportions', []):
        for side in ('numerator', 'denominator'):
            if prop[side] not in dims:
                errors.append(f"proportion {prop['id']} uses unknown dimension {prop[side]}")
    views = {s['view'] for s in spec.get('silhouettes', [])}
    for feature in spec['features']:
        for part in feature['part_ids']:
            if part not in part_ids:
                errors.append(f"feature {feature['id']} refers to unknown part {part}")
        if feature['verify'] == 'count' and 'count' not in feature:
            errors.append(f"feature {feature['id']} verify=count needs count")
        if feature['verify'] == 'dimension' and not any(set(d.get('part_ids', [])) & set(feature['part_ids']) for d in spec['dimensions']):
            errors.append(f"feature {feature['id']} verify=dimension but no dimension covers its parts")
        if feature['verify'] == 'silhouette' and not views:
            errors.append(f"feature {feature['id']} verify=silhouette but the spec has no silhouettes")
        if feature['verify'] == 'visual':
            warnings.append(f"feature {feature['id']} is only visually reviewed; it must appear in the review feature_checks")
    roles = _dim_roles()
    anchors = {b['part_id']: set(BUILTIN_ANCHORS) | set(b.get('anchors', {})) for b in spec['builders']}
    mirrors = {b['part_id'] for b in spec['builders'] if b['builder'] == 'mirror'}
    for builder in spec['builders']:
        if builder.get('parent') and builder['parent'] not in part_ids:
            errors.append(f"builder {builder['part_id']} has unknown parent {builder['parent']}")
        if builder['builder'] == 'mirror' and builder['params'].get('source') not in part_ids:
            errors.append(f"mirror {builder['part_id']} has unknown source")
        if builder.get('dim_role', 'none') not in roles:
            errors.append(f"builder {builder['part_id']} dim_role {builder['dim_role']!r} is not a real_dimensions.json category (or 'none')")
        for free in builder.get('free', []):
            value = resolve_pointer(builder, free['pointer'])
            if value is _MISSING or isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"builder {builder['part_id']} free pointer {free['pointer']} does not resolve to a number")
            elif not free['min'] < free['max'] or not free['min'] <= value <= free['max']:
                errors.append(f"builder {builder['part_id']} free {free['pointer']}: need min < max and min <= {value} <= max")
    for i, relation in enumerate(spec.get('relations', [])):
        for side in ('a', 'b'):
            part, anchor = split_ref(relation[side])
            if part not in part_ids:
                errors.append(f'relation {i} {side} refers to unknown part {part}')
            elif anchor not in anchors[part]:
                errors.append(f'relation {i} {side} uses unknown anchor {part}/{anchor} (builtin {list(BUILTIN_ANCHORS)} or the builder anchors)')
            elif part in mirrors:
                errors.append(f'relation {i} names mirror part {part}; place its source instead (the mirror follows)')
        if relation['type'] in ('align', 'through') and 'axis' not in relation:
            errors.append(f"relation {i} {relation['type']} needs axis")
    if spec.get('relations') and not any(e.startswith('relation') for e in errors):
        try:
            relation_order(spec)
        except ValueError as error:
            errors.append(f'relations: {error}')
    for i, claim in enumerate(spec.get('assembly_claims', [])):
        needs_b, needs_axis, needs_value = CLAIM_RULES[claim['type']]
        for side in ('a', 'b'):
            if claim.get(side) and claim[side] not in part_ids:
                errors.append(f"assembly claim {i} {side} refers to unknown part {claim[side]}")
        if claim['type'] != 'no_floating' and not claim.get('a'):
            errors.append(f"assembly claim {i} {claim['type']} needs a")
        if needs_b != bool(claim.get('b')):
            errors.append(f"assembly claim {i} {claim['type']} {'needs' if needs_b else 'takes no'} b")
        if needs_axis and 'axis' not in claim:
            errors.append(f"assembly claim {i} {claim['type']} needs axis")
        if needs_value and 'value_m' not in claim:
            errors.append(f"assembly claim {i} {claim['type']} needs value_m")
    claimed = {p for c in spec.get('assembly_claims', []) for p in (c.get('a'), c.get('b')) if p} | \
        (part_ids if any(c['type'] == 'no_floating' and not c.get('a') for c in spec.get('assembly_claims', [])) else set())
    for feature in spec['features']:
        if feature['verify'] == 'assembly' and not set(feature['part_ids']) & claimed:
            errors.append(f"feature {feature['id']} verify=assembly but no assembly claim names its parts")
    for material in spec.get('materials', []):
        for part in material['part_ids']:
            if part not in part_ids:
                errors.append(f'material refers to unknown part {part}')
    for source in spec['sources']:
        if source['kind'] in IMAGE_KINDS and source['license'].strip().lower() in ('', 'unknown', 'unclear'):
            errors.append(f"image source {source['id']} has no usable licence")
    for silhouette in spec.get('silhouettes', []):
        if silhouette['source_id'] not in sources:
            errors.append(f"silhouette {silhouette['view']} cites unknown source")
        if silhouette.get('datum') and not silhouette.get('px_per_m'):
            errors.append(f"silhouette {silhouette['view']} datum needs px_per_m")
    targets = {'dimension': {d['id'] for d in spec['dimensions']}, 'proportion': {p['id'] for p in spec.get('proportions', [])},
               'feature': {f['id'] for f in spec['features']}, 'silhouette': views,
               'assembly': {c['id'] for c in spec.get('assembly_claims', []) if c.get('id')}}
    seen = set()
    for deviation in spec.get('deviations', []):
        kind, _, target = deviation['check'].partition(':')
        label = f"deviation {deviation['id']}"
        if target not in targets[kind]:
            errors.append(f"{label}: no {kind} check {target!r}" + (' (assembly deviations need the claim id)' if kind == 'assembly' else ''))
        keys = [k for k in ('factor', 'min_iou', 'waive') if k in deviation]
        if keys != [DEVIATION_KEY[kind]]:
            errors.append(f"{label}: a {kind} deviation takes exactly {DEVIATION_KEY[kind]} (got {keys or 'none'})")
        if deviation['check'] in seen:
            errors.append(f"{label}: {deviation['check']} already has a deviation")
        seen.add(deviation['check'])
        large = deviation.get('waive') or not 0.8 <= deviation.get('factor', 1.0) <= 1.25 or deviation.get('min_iou', 1.0) < 0.7
        if real and large and not deviation.get('user_evidence'):
            errors.append(f"{label}: a large change to a real subject needs user_evidence (the user's own words)")
        else:
            warnings.append(f"{label}: {deviation['check']} intentionally changed ({deviation['reason']})")
    by_view = {s['view']: s for s in spec.get('silhouettes', [])}
    for view in spec.get('fit', {}).get('views', []):
        if view not in by_view:
            errors.append(f'fit view {view} has no silhouette')
        elif project is not None and not (project_dir(project) / by_view[view]['image']).is_file():
            errors.append(f"silhouette image missing: {by_view[view]['image']}")
    return {'status': 'ok' if not errors else 'errors', 'errors': errors, 'warnings': warnings}


def init_spec(project, subject_id, identity, request, subject_mode='specific_real'):
    path = spec_path(project, subject_id)
    if path.exists():
        raise StudioError('REVISION_CONFLICT', f'Spec exists: {path}')
    template = read_json(Path(__file__).resolve().parents[1] / 'templates' / 'subject_spec.json')
    template.update({'subject_id': subject_id, 'identity': identity, 'request': request, 'subject_mode': subject_mode})
    write_json(path, template)
    return {'status': 'created', 'spec_path': str(path), 'note': 'Fill sources, dimensions, features and builders, then run subject lint'}


def show_spec(project, subject_id):
    spec = load_spec(project, subject_id)
    rows = ['| item | value | source |', '|---|---|---|']
    rows += [f"| {d['id']} | {d['value_m']} m ±{d['tol_pct']}% | {d['source_id']} |" for d in spec['dimensions']]
    rows += [f"| {f['id']} | {f['verify']}{' ×' + str(f['count']) if f.get('count') else ''}: {f['description']} | {f.get('source_id', '')} |" for f in spec['features']]
    return {'subject_id': subject_id, 'identity': spec['identity'], 'table': '\n'.join(rows), 'lint': lint_spec(spec, project)}


def register_commands(subparsers):
    parser = subparsers.add_parser('subject', help='Subject specs: request -> sourced, measurable requirements')
    commands = parser.add_subparsers(dest='subject_command', required=True)
    p = commands.add_parser('init'); p.add_argument('--project', required=True); p.add_argument('--subject', required=True)
    p.add_argument('--identity', required=True); p.add_argument('--request', required=True)
    p.add_argument('--mode', default='specific_real', choices=['specific_real', 'schematic', 'fictional'])
    p.add_argument('--from-exemplar', help='start from a promoted exemplar spec (library/exemplars)')
    p.set_defaults(handler=lambda a: __import__('studio.exemplars', fromlist=['init_from_exemplar']).init_from_exemplar(
        a.project, a.subject, a.from_exemplar, a.identity, a.request) if a.from_exemplar else init_spec(a.project, a.subject, a.identity, a.request, a.mode))
    p = commands.add_parser('promote', help='copy a spec whose current version passed fidelity into library/exemplars')
    p.add_argument('--project', required=True); p.add_argument('--subject', required=True)
    p.set_defaults(handler=lambda a: __import__('studio.exemplars', fromlist=['promote']).promote(a.project, a.subject))
    p = commands.add_parser('exemplars', help='search promoted exemplar specs')
    p.add_argument('--query', required=True); p.add_argument('--limit', type=int, default=5)
    p.set_defaults(handler=lambda a: __import__('studio.exemplars', fromlist=['search']).search(a.query, a.limit))
    p = commands.add_parser('lint'); p.add_argument('--project', required=True); p.add_argument('--subject', required=True)
    p.set_defaults(handler=lambda a: lint_spec(load_spec(a.project, a.subject), a.project))
    p = commands.add_parser('show'); p.add_argument('--project', required=True); p.add_argument('--subject', required=True)
    p.set_defaults(handler=lambda a: show_spec(a.project, a.subject))
    p = commands.add_parser('trace', help='Read loft stations / wing planforms off the registered drawing (candidate; --apply merges)')
    p.add_argument('--project', required=True); p.add_argument('--subject', required=True); p.add_argument('--part', action='append', default=[])
    p.add_argument('--view', default='top', choices=['top', 'side', 'front']); p.add_argument('--register', action='store_true'); p.add_argument('--apply', action='store_true')
    p.set_defaults(handler=lambda a: __import__('studio.subject_trace', fromlist=['trace_command']).trace_command(
        a.project, a.subject, a.part, a.view, a.register, a.apply))
    p = commands.add_parser('fit', help='Fit builders[].free parameters to the silhouettes, keeping sourced dimensions')
    p.add_argument('--project', required=True); p.add_argument('--subject', required=True); p.add_argument('--view', action='append')
    p.add_argument('--max-evals', type=int); p.add_argument('--starts', type=int); p.add_argument('--apply', action='store_true')
    p.add_argument('--from-trace', help='trace candidate json whose patch seeds one start')
    p.set_defaults(handler=lambda a: __import__('studio.subject_fit', fromlist=['fit']).fit(
        a.project, a.subject, a.view, a.max_evals, a.starts, a.apply, read_json(a.from_trace)['patch'] if a.from_trace else None))
    p = commands.add_parser('from-dxf', help='CAD outline -> exact silhouette (datum known) + profile/wall candidates')
    p.add_argument('--project', required=True); p.add_argument('--subject', required=True); p.add_argument('--dxf', required=True)
    p.add_argument('--layer', required=True); p.add_argument('--view', default='front', choices=['front', 'side', 'top'])
    p.add_argument('--units', default='auto', choices=['auto', 'mm', 'cm', 'm', 'in']); p.add_argument('--px-per-m', type=float)
    p.add_argument('--license'); p.add_argument('--apply', action='store_true')
    p.set_defaults(handler=lambda a: __import__('studio.subject_dxf', fromlist=['from_dxf']).from_dxf(
        a.project, a.subject, a.dxf, a.layer, a.view, a.units, a.px_per_m, a.license, a.apply))
