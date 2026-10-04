"""Pure-Python relation and claim rules shared by the Blender builder (modeling/assemble.py) and the
host lint (studio/subjects.py): reference parsing, anchor names and the solve order."""
from __future__ import annotations

BUILTIN_ANCHORS = ('center', 'origin', '+x', '-x', '+y', '-y', '+z', '-z')
RELATION_TYPES = ('attach', 'align', 'through', 'on_surface', 'symmetric')
AXIS_RELATIONS = ('align', 'through')  # need an explicit axis
# Claim type -> (needs b, needs axis, needs value_m)
CLAIM_RULES = {'contact': (True, False, False), 'no_interference': (True, False, False), 'clearance': (True, False, True),
               'no_floating': (False, False, False), 'through': (True, True, False), 'cover': (True, False, True)}


def split_ref(ref):
    part, _, anchor = ref.partition('/')
    return part, anchor or 'center'


def parent_map(spec):
    """part_id -> parent part_id (mirrors default to their source's parent)."""
    builders = {b['part_id']: b for b in spec['builders']}
    out = {}
    for b in spec['builders']:
        parent = b.get('parent')
        if parent is None and b['builder'] == 'mirror':
            parent = builders.get(b['params'].get('source'), {}).get('parent')
        out[b['part_id']] = parent
    return out


def ancestors(parents, part_id):
    out, cur = [], parents.get(part_id)
    while cur is not None:
        if cur in out:
            raise ValueError(f'parent cycle at {cur}')
        out.append(cur)
        cur = parents.get(cur)
    return out


def relation_order(spec):
    """Relations sorted so each runs after every relation moving its target, its own ancestors or the
    target's ancestors; several relations on one part keep spec order. ValueError on a cycle."""
    relations = list(spec.get('relations', []))
    parents = parent_map(spec)
    moved_by = {}
    for i, r in enumerate(relations):
        moved_by.setdefault(split_ref(r['a'])[0], []).append(i)
    deps = {}
    for i, r in enumerate(relations):
        a, b = split_ref(r['a'])[0], split_ref(r['b'])[0]
        if a == b or a in ancestors(parents, b):
            raise ValueError(f'relation {i}: {a} cannot be placed relative to itself or its own descendant {b}')
        watched = [b] + ancestors(parents, b) + ancestors(parents, a)
        deps[i] = {j for p in watched for j in moved_by.get(p, []) if j != i}
        deps[i] |= {j for j in moved_by.get(a, []) if j < i}
    order, done = [], set()
    while len(order) < len(relations):
        ready = [i for i in range(len(relations)) if i not in done and deps[i] <= done]
        if not ready:
            raise ValueError(f'relation cycle among {sorted(set(range(len(relations))) - done)}')
        order.append(ready[0]); done.add(ready[0])
    return [relations[i] for i in order]
