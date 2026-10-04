"""Search the installed Blender's API (library/api/blender-<version>.json from scripts/build_bpy_index.py).

Agents look names up here before writing bpy code: Blender 5.x renamed and moved enough (node-modifier
inputs, smoothing, EEVEE settings) that recalled 3.x/4.x API is a common failure. `api show` returns
one type/operator exactly; `api search` ranks types, properties, functions and operators by token match.
"""
from __future__ import annotations

from functools import lru_cache
import re

from .common import REPO, StudioError, read_json

API_DIR = REPO / 'library' / 'api'


def index_path(version=None):
    found = sorted(API_DIR.glob(f'blender-{version or "*"}.json'))
    if not found:
        raise StudioError('ENVIRONMENT_MISSING', 'No Blender API index', recovery='blender -b --factory-startup --python scripts/build_bpy_index.py -- library/api/blender-<version>.json')
    return found[-1]


@lru_cache(maxsize=2)
def _load(path):
    return read_json(path)


def _tokens(text):
    parts = re.split(r'[^0-9a-zA-Z]+', re.sub(r'([a-z])([A-Z])', r'\1 \2', text))
    return {p.lower() for p in parts if p}


def search(query, limit=15, version=None):
    data = _load(str(index_path(version)))
    wanted = _tokens(query)
    if not wanted:
        raise StudioError('INPUT_INVALID', 'empty query')
    rows = []
    def score(name, desc=''):
        name_tokens, desc_tokens = _tokens(name), _tokens(desc or '')
        return 3 * len(wanted & name_tokens) + len(wanted & desc_tokens)
    for type_name, info in data['types'].items():
        s = score(type_name, info.get('desc'))
        if s:
            rows.append((s + 1, {'kind': 'type', 'path': f'bpy.types.{type_name}', 'desc': info.get('desc', '')}))
        for p in info['props']:
            s = score(p['id'], p.get('desc')) + (1 if wanted & _tokens(type_name) else 0)
            if s >= 3:
                rows.append((s, {'kind': 'property', 'path': f"bpy.types.{type_name}.{p['id']}", 'type': p['type'], 'desc': p.get('desc', ''),
                                 **({'items': p['items'][:12]} if p.get('items') else {}), **({'of': p['of']} if p.get('of') else {})}))
        for f in info['funcs']:
            s = score(f['id'], f.get('desc')) + (1 if wanted & _tokens(type_name) else 0)
            if s >= 3:
                rows.append((s, {'kind': 'function', 'path': f"bpy.types.{type_name}.{f['id']}", 'desc': f.get('desc', ''),
                                 'params': [p['id'] for p in f['params']]}))
    for op, info in data['operators'].items():
        s = score(op.replace('.', ' '), info.get('desc'))
        if s >= 3:
            rows.append((s, {'kind': 'operator', 'path': f'bpy.ops.{op}', 'desc': info.get('desc', ''), 'params': [p['id'] for p in info['params']][:20]}))
    for op, doc in data['bmesh_ops'].items():
        s = score(op, doc[:200])
        if s >= 3:
            rows.append((s, {'kind': 'bmesh_op', 'path': f'bmesh.ops.{op}', 'desc': doc.split('\n')[0]}))
    rows.sort(key=lambda r: (-r[0], len(r[1]['path'])))
    return {'blender_version': data['blender_version'], 'query': query, 'results': [dict(r[1], score=r[0]) for r in rows[:limit]]}


def show(path, version=None):
    data = _load(str(index_path(version)))
    name = path.removeprefix('bpy.types.').removeprefix('bpy.ops.').removeprefix('bmesh.ops.')
    if path.startswith('bpy.ops.') or name in data['operators']:
        info = data['operators'].get(name)
        if info:
            return {'blender_version': data['blender_version'], 'path': f'bpy.ops.{name}', **info}
    if path.startswith('bmesh.ops.') and name in data['bmesh_ops']:
        return {'blender_version': data['blender_version'], 'path': path, 'doc': data['bmesh_ops'][name]}
    type_name, _, member = name.partition('.')
    info = data['types'].get(type_name)
    if info is None:
        close = sorted(t for t in data['types'] if type_name.lower() in t.lower())[:20]
        raise StudioError('INPUT_INVALID', f'{path} not in the Blender {data["blender_version"]} API', recovery=f'similar: {close}')
    if member:
        props = [p for p in info['props'] if p['id'] == member]
        funcs = [f for f in info['funcs'] if f['id'] == member]
        if not props and not funcs:
            raise StudioError('INPUT_INVALID', f'{type_name} has no member {member} in Blender {data["blender_version"]}',
                              recovery=f"members: {[p['id'] for p in info['props']] + [f['id'] for f in info['funcs']]}")
        return {'blender_version': data['blender_version'], 'path': f'bpy.types.{name}', 'properties': props, 'functions': funcs}
    chain, base = [type_name], info.get('base')
    while base and base in data['types'] and len(chain) < 10:
        chain.append(base); base = data['types'][base].get('base')
    return {'blender_version': data['blender_version'], 'path': f'bpy.types.{type_name}', 'bases': chain[1:], **info}


def register_commands(subparsers):
    parser = subparsers.add_parser('api', help='Look up the installed Blender API (index built from the running version)')
    commands = parser.add_subparsers(dest='api_command', required=True)
    p = commands.add_parser('search'); p.add_argument('--query', required=True); p.add_argument('--limit', type=int, default=15)
    p.set_defaults(handler=lambda a: search(a.query, a.limit))
    p = commands.add_parser('show'); p.add_argument('--path', required=True)
    p.set_defaults(handler=lambda a: show(a.path))
