"""Edits by JSON pointer - one grammar for every path that changes a shot from the user's words (storyboard revise,
workbench set_shot_value, fill revise): {op: set|add|remove, path, value | factor | delta}.

Every value is reachable, and nothing can be written that nothing reads:
- an existing value can always be changed (set / remove);
- a key that does not exist yet must be declared - by the schema at that point (following $ref, allOf, if/then and
  oneOf/anyOf branches that apply to the document as it is), or, inside an object the schema leaves open, by the
  declared-reads table of what the code reads there (camera move params: camera_moves_core.PARAMS);
- a missing object on the way is created when the schema declares it (/camera/move/framing/horizon_v);
- `add` appends to a list (`/-`) or inserts at an index; the item is checked when the shot is validated.
The whole shot is validated afterwards (validate_shot), which also refuses values a declared-reads table says are
never read in their context.
"""
from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator

from .blender_ops import action_params, camera_moves_core as moves_core, expressive_core   # pure, no Blender
from .common import REPO, StudioError, read_json
from .project import SHOT_CONTENT   # the content layer of the shot (project.SHOT_FIELD_LAYER)

OPS = ('set', 'add', 'remove')
_SCHEMAS = {}


def schema(name):
    if name not in _SCHEMAS:
        _SCHEMAS[name] = read_json(REPO / 'schemas' / 'studio-v1' / f'{name}.schema.json')
    return _SCHEMAS[name]


def pointer(text):
    if not isinstance(text, str) or not text.startswith('/'):
        raise StudioError('INPUT_INVALID', f'edit path must be a JSON pointer starting with / (got {text!r})')
    return [part.replace('~1', '/').replace('~0', '~') for part in text[1:].split('/')]


# --- the schema at a point, with the branches that apply ------------------------------------------------------------

def _deref(root, node):
    while isinstance(node, dict) and '$ref' in node:
        target = root
        for key in node['$ref'].lstrip('#/').split('/'):
            target = target[key]
        node = target
    return node if isinstance(node, dict) else {}


def _matches(root, condition, instance):
    return Draft202012Validator({**condition, '$defs': root.get('$defs', {})}).is_valid(instance)


def _expand(root, node, instance):
    """The node and every branch of it that applies to `instance` (if/then by evaluation; oneOf/anyOf all - the
    final validation decides between them)."""
    node = _deref(root, node)
    out = [node]
    conditionals = [node] + [_deref(root, s) for s in node.get('allOf', [])]
    for sub in conditionals:
        if sub is not node:
            out += _expand(root, {k: v for k, v in sub.items() if k not in ('if', 'then', 'else')}, instance)
        if 'if' in sub:
            branch = sub.get('then') if _matches(root, sub['if'], instance) else sub.get('else')
            if branch:
                out += _expand(root, branch, instance)
    for key in ('oneOf', 'anyOf'):
        for sub in node.get(key, []):
            out += _expand(root, sub, instance)
    return out


def _children(root, nodes, key, instance):
    """Schema nodes that apply to instance[key] (instance is the parent value)."""
    out = []
    for node in nodes:
        for branch in _expand(root, node, instance):
            if isinstance(instance, list):
                if 'items' in branch:
                    out.append(branch['items'])
            elif key in branch.get('properties', {}):
                out.append(branch['properties'][key])
            elif isinstance(branch.get('additionalProperties'), dict):
                out.append(branch['additionalProperties'])
    return out


def _declared(root, nodes, instance):
    """(declared keys, closed?) of an object value."""
    keys, closed = set(), False
    for node in nodes:
        for branch in _expand(root, node, instance):
            keys |= set(branch.get('properties', {}))
            closed = closed or branch.get('additionalProperties') is False
    return keys, closed


def _kind(root, nodes, instance):
    types = {t for node in nodes for b in _expand(root, node, instance) for t in ([b['type']] if isinstance(b.get('type'), str) else b.get('type', []))}
    return [] if 'array' in types else {}


# --- declared reads of open objects ---------------------------------------------------------------------------------

def shot_reads(parts, doc):
    """{key: default} the code reads in the open object at `parts` of a shot, or None when no table covers it."""
    if parts == ['camera', 'move', 'params']:
        kind = (doc.get('camera', {}).get('move') or {}).get('type')
        if kind not in moves_core.PARAMS:
            raise StudioError('INPUT_INVALID', f'camera.move type {kind!r} is not a move ({sorted(moves_core.PARAMS)})')
        return moves_core.PARAMS[kind]
    if len(parts) >= 3 and parts[0] == 'actions' and parts[2] == 'params' and parts[1].isdigit() and int(parts[1]) < len(doc.get('actions') or []):
        sub = tuple(p for p in parts[3:] if not p.isdigit())   # /params/drives/0/keys -> ('drives', 'keys')
        return action_params.reads(doc['actions'][int(parts[1])], sub)
    if parts[:2] == ['render', 'engine_settings'] and len(parts) == 3 and parts[2] in expressive_core.ENGINE_SETTINGS:
        return {key: None for key in expressive_core.ENGINE_SETTINGS[parts[2]]}
    if parts[:3] == ['render', 'compositor', 'ops'] and len(parts) == 4 and parts[3].isdigit():
        ops = ((doc.get('render') or {}).get('compositor') or {}).get('ops') or []
        row = expressive_core.COMPOSITOR_OPS.get(ops[int(parts[3])].get('op')) if int(parts[3]) < len(ops) else None
        return {'op': None, **{key: None for key in row[1]}} if row else None
    return None


def _constant(default):
    return None if default in (moves_core.REQUIRED, moves_core.DERIVED) else default   # same sentinels in action_params


# --- the edit -------------------------------------------------------------------------------------------------------

def apply(doc, op, root, node=None, reads=None, label=None):
    """Apply one op to `doc` in place; returns 'path: before → after'. `root` is the schema (for $ref), `node` the schema
    node of `doc` (default: root), `reads(parts, doc)` the declared-reads tables for open objects."""
    kind = op.get('op', 'set')
    if kind not in OPS:
        raise StudioError('INPUT_INVALID', f'edit op {kind!r} is not one of {OPS}')
    parts = pointer(op['path'])
    if not parts or parts == ['']:
        raise StudioError('INPUT_INVALID', 'edit path names no value')
    path = op['path']
    nodes, value, walked = [node if node is not None else root], doc, []
    for key in parts[:-1]:
        child_nodes = _children(root, nodes, key, value)
        if isinstance(value, list):
            if not key.isdigit() or int(key) >= len(value):
                raise StudioError('INPUT_INVALID', f'{path}: no item {key} (the list has {len(value)})')
            value = value[int(key)]
        elif isinstance(value, dict):
            if value.get(key) is None:   # absent, or an optional field held as null (a shot's unset concealed_parts)
                keys, closed = _declared(root, nodes, value)
                table = reads(walked, doc) if reads else None
                if key not in keys and not (table and key in table):
                    raise StudioError('INPUT_INVALID', f'{path}: no {key!r} on the way, and nothing declares it here')
                value[key] = _kind(root, child_nodes, None) if child_nodes else {}
            value = value[key]
        else:
            raise StudioError('INPUT_INVALID', f'{path}: {"/".join(walked) or "the document"} is a value, not a container')
        nodes, walked = child_nodes, walked + [key]
    last = parts[-1]
    if isinstance(value, list):
        if kind == 'add':
            index = len(value) if last == '-' else int(last) if last.isdigit() and int(last) <= len(value) else None
            if index is None:
                raise StudioError('INPUT_INVALID', f'{path}: add takes /- or an index up to {len(value)}')
            if 'value' not in op:
                raise StudioError('INPUT_INVALID', f'{path}: add needs a value')
            value.insert(index, deepcopy(op['value']))
            return f'{path}: added'
        if not last.isdigit() or int(last) >= len(value):
            raise StudioError('INPUT_INVALID', f'{path}: no item {last} (the list has {len(value)}; add with /-)')
        index = int(last)
        if kind == 'remove':
            value.pop(index)
            return f'{path}: removed'
        before = value[index]
        value[index] = _new_value(op, before, path)
        return f'{path}: {_short(before)} → {_short(value[index])}'
    if not isinstance(value, dict):
        raise StudioError('INPUT_INVALID', f'{path}: {"/".join(walked)} is a value, not a container')
    table = reads(walked, doc) if reads else None
    if table is not None and last not in table:
        raise StudioError('INPUT_INVALID', f"{path}: nothing reads {last!r} here; it reads {sorted(table)}")
    exists = last in value
    if kind == 'remove':
        if not exists:
            raise StudioError('INPUT_INVALID', f'{path}: nothing to remove')
        value.pop(last)
        return f'{path}: removed'
    if kind == 'add' and exists:
        raise StudioError('INPUT_INVALID', f'{path}: already set; use set')
    if not exists and table is None:
        keys, closed = _declared(root, nodes, value)
        if last not in keys:
            raise StudioError('INPUT_INVALID', f'{path}: not a value this {label or "document"} declares here'
                              + (f' (it declares {sorted(keys)})' if keys else ' (only existing keys of an open object can be changed)'))
    before = value[last] if exists else _constant(table[last]) if table is not None else None
    value[last] = _new_value(op, before, path)
    return f'{path}: {_short(before)} → {_short(value[last])}'


def _new_value(op, before, path):
    if 'value' in op:
        return deepcopy(op['value'])
    if not isinstance(before, (int, float)) or isinstance(before, bool):
        raise StudioError('INPUT_INVALID', f'{path} has no number to scale ({_short(before)}; derived from the geometry when unset) - give an absolute value')
    if 'factor' in op:
        return round(before * op['factor'], 4)
    if 'delta' in op:
        return round(before + op['delta'], 4)
    raise StudioError('INPUT_INVALID', f'{path}: give value, factor or delta')


def _short(value):
    text = repr(value) if not isinstance(value, (int, float, str)) or isinstance(value, bool) else str(value)
    return text if len(text) <= 60 else text[:57] + '...'


def edit_shot(content, op):
    """One op on a shot's content (project.SHOT_CONTENT, in place)."""
    parts = pointer(op['path'])
    if parts[0] not in SHOT_CONTENT or len(parts) < 2 and op.get('op', 'set') != 'set':
        raise StudioError('INPUT_INVALID', f"edit path must start with one of {['/' + k for k in SHOT_CONTENT]} (got {op['path']})")
    return apply(content, op, schema('shot'), reads=shot_reads, label='shot')
