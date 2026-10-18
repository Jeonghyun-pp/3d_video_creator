"""Static lint for code an agent writes that runs inside Blender: author scripts (free bpy), revision patches and rig
scripts. Pure `ast` on the host, before Blender starts - the first of three lines (this lint, the runtime audit hook in
blender_ops/sandbox.py, and the separate author process in blender_ops/build_author.py).

Default deny: an import is allowed only when it is listed below, is an engine module (studio/blender_ops, except entry
scripts that read sys.argv), or is a module beside the script or at the top of the project (a production's own library).
Imported companion modules are linted with the same rules. Refused everywhere: dynamic code (exec, eval, compile,
__import__), reflection that hides a name (getattr/setattr/delattr with a computed or dunder name, globals, vars),
dunder attributes, Blender handlers / timers / driver namespace, saving or replacing files from bpy (save, export,
library write, preferences, add-ons, scripts). Linking and appending from .blend files is allowed (assets are data).

Profiles: 'author' (bpy allowed), 'rig' (camera_state scripts: pure Python, no bpy), 'pure' (contrib entries: no bpy,
no files). Rules are tables so a new case is one row, and every refusal says what to do instead.
"""
from __future__ import annotations

import ast
import re
import hashlib
from pathlib import Path

from .common import CODE, StudioError

OPS = CODE / 'studio' / 'blender_ops'
PURE_STDLIB = {'math', 'random', 'json', 'bisect', 'copy', 'hashlib', 'itertools', 'functools', 'collections', 'statistics',
               'dataclasses', 'typing', 'enum', 're', 'string', 'colorsys', 'heapq', 'operator', 'fractions', 'decimal',
               '__future__', 'textwrap', 'struct', 'time'}
PROFILES = {
    'author': {'imports': PURE_STDLIB | {'pathlib', 'sys', 'bpy', 'bmesh', 'mathutils', 'bpy_extras', 'numpy'}, 'engine': True, 'bpy': True},
    'rig': {'imports': PURE_STDLIB | {'sys', 'pathlib'}, 'engine': False, 'bpy': False},
    'pure': {'imports': PURE_STDLIB, 'engine': False, 'bpy': False},
}
SYS_ATTRS = {'path', 'argv', 'version_info', 'platform', 'float_info', 'maxsize', 'stdout', 'stderr'}
DUNDERS_OK = {'__name__', '__file__', '__doc__', '__init__', '__post_init__', '__repr__', '__eq__', '__hash__', '__lt__',
              '__iter__', '__len__', '__getitem__', '__enter__', '__exit__', '__future__', '__all__', '__slots__'}
PRIVATE_OK = {'_replace', '_asdict', '_fields', '_make', '_field_defaults'}   # namedtuple's public API is spelled with _
FOLLOWED = PURE_STDLIB | {'pathlib', 'sys'}
# Builtins pure and rig code may use (default deny: a builtin not listed - locals, open, input, help, exec... - is
# refused; 2026-10-12 second re-audit: locals()['__builtins__'] reached exec by string keys). Exception classes are
# allowed as a family (_builtin_ok); getattr/setattr/hasattr/delattr are judged by visit_Call.
BUILTINS_OK = {'abs', 'all', 'any', 'ascii', 'bin', 'bool', 'bytearray', 'bytes', 'callable', 'chr', 'classmethod', 'complex',
               'dict', 'dir', 'divmod', 'enumerate', 'filter', 'float', 'format', 'frozenset', 'getattr', 'hasattr', 'setattr',
               'delattr', 'hash', 'hex', 'id', 'int', 'isinstance', 'issubclass', 'iter', 'len', 'list', 'map', 'max', 'min',
               'next', 'object', 'oct', 'ord', 'pow', 'print', 'property', 'range', 'repr', 'reversed', 'round', 'set', 'slice',
               'sorted', 'staticmethod', 'str', 'sum', 'super', 'tuple', 'type', 'zip', 'NotImplemented', 'Ellipsis'}


def _builtin_ok(name):
    import builtins
    value = getattr(builtins, name, None)
    return name in BUILTINS_OK or (isinstance(value, type) and issubclass(value, BaseException))


def pure_run_policy():
    """What pure code may use when it runs (blender_ops/contrib_loader.run, blender_ops/sandbox.add_pure): this profile's
    builtins and imports - the lint's own lists, so the lint and the run cannot drift apart."""
    import builtins
    return {'builtins': sorted(name for name in dir(builtins) if _builtin_ok(name)), 'imports': sorted(PROFILES['pure']['imports'])}


def _is_builtin(name):
    import builtins
    return hasattr(builtins, name)


METHODS_OK = {'compile'}   # a method of this name is not the builtin (re.compile); the builtin itself stays refused
REFUSED_CALLS = {
    'exec': 'dynamic code cannot be reviewed: write the code in the script',
    'eval': 'dynamic code cannot be reviewed: parse values with json, or write the expression',
    'compile': 'dynamic code cannot be reviewed',
    '__import__': 'import modules with an import statement',
    'globals': 'name the variable directly',
    'vars': 'name the attribute directly',
    'breakpoint': 'no debugger in a build',
}
# (parent attribute, attribute) pairs, matched on any chain (bpy.ops.wm.save_as_mainfile, ops.wm.save_as_mainfile, ...)
REFUSED_PAIRS = {
    ('app', 'handlers'): 'handlers run inside later build steps: do the work in the script body',
    ('app', 'timers'): 'timers run later, outside the author step',
    ('app', 'driver_namespace'): 'driver functions run in later steps: use plain keyframes or drivers with built-in expressions',
    ('wm', 'save_mainfile'): 'the build saves the scene itself (authored.blend, scene.blend)',
    ('wm', 'save_as_mainfile'): 'the build saves the scene itself (authored.blend, scene.blend)',
    ('wm', 'read_homefile'): 'starting over would drop the declared layout: delete objects instead',
    ('wm', 'read_factory_settings'): 'starting over would drop the declared layout: delete objects instead',
    ('wm', 'revert_mainfile'): 'starting over would drop the declared layout',
    ('wm', 'url_open'): 'no network from a build',
    ('wm', 'quit_blender'): 'the build decides when Blender stops',
    ('ops', 'script'): 'no script reloading from a build',
    ('ops', 'preferences'): 'preferences and add-ons: declare shot.render.addons (bundled add-ons only)',
    ('ops', 'extensions'): 'downloaded extensions never run in a build',
    ('text', 'run_script'): 'dynamic code cannot be reviewed',
    ('libraries', 'write'): 'the build saves files itself',
    ('image', 'save'): 'the build packs images itself; write data files only inside the build output',
    ('image', 'save_as'): 'the build packs images itself',
}
REFUSED_ATTRS = {'as_module': 'text blocks as modules are dynamic code', 'save_render': 'renders are made by render jobs'}


# The runtime line's own code is not vocabulary: an author that imports it could rebind what it judges with.
GUARD_MODULES = {'sandbox'}


def engine_modules():
    """Engine modules an author may import: studio/blender_ops/*.py and the modeling package, minus entry scripts and the
    runtime line (GUARD_MODULES)."""
    names = set()
    for path in OPS.glob('*.py'):
        tree = ast.parse(path.read_text())
        entry = any(isinstance(n, ast.Attribute) and n.attr == 'argv' and isinstance(n.value, ast.Name) and n.value.id == 'sys'
                    for stmt in tree.body if not isinstance(stmt, (ast.FunctionDef, ast.ClassDef)) for n in ast.walk(stmt))
        if not entry and path.stem not in GUARD_MODULES:
            names.add(path.stem)
    return names | {p.name for p in OPS.iterdir() if p.is_dir() and (p / '__init__.py').is_file()}


def _chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def _modules_along(module, attrs):
    """Every module an attribute chain off an allowed standard-library import passes through, in order, after the import
    itself: `collections.abc.sys` -> ['collections.abc', 'sys']. Only FOLLOWED modules are imported to look (engine and
    local modules are linted as source); [] when the chain cannot be followed here."""
    import importlib
    import types
    if module.split('.')[0] not in FOLLOWED:
        return []
    try:
        node = importlib.import_module(module)
    except Exception:   # noqa: BLE001 - a module that does not import here cannot be followed; nothing to report
        return []
    reached = []
    for attr in attrs:
        node = getattr(node, attr, None)
        if node is None:
            break
        if isinstance(node, types.ModuleType):
            reached.append(node.__name__)
    return reached


def _strays(origin, reached):
    """The first module in `reached` that is not a submodule of the one before it (origin first): a module reached
    through another one's namespace instead of by its own import."""
    previous = origin
    for name in reached:
        if not name.startswith(previous + '.'):
            return name
        previous = name
    return None


class _Linter(ast.NodeVisitor):
    """Besides the tables above, rules close what an allowed import reaches (2026-10-12 audit and re-audit:
    `random._os.system`, `typing.sys.modules["os"]`, `__builtins__.exec`, `from random import _os`,
    `getattr(typing, "sys")`, `t, u = typing, 1` passed the 'pure' profile):
      - a module is only ever used as `module.name`: never a value (assigned, passed, returned, listed), so every chain
        off it is visible here, whatever binding or reflection would carry it;
      - along that chain (and in `from module import name`) every module reached is a submodule of the one before it,
        and no name is private (_x);
      - no dunder name outside DUNDERS_OK, and attrgetter / methodcaller names are judged like attributes;
      - in the profiles without a runtime hook ('pure', 'rig') no private attribute on anything but self/cls."""
    def __init__(self, file, profile, local):
        self.file, self.profile, self.local = file, PROFILES[profile], local
        self.errors, self.warnings, self.companions = [], [], []
        self.engine = engine_modules() if self.profile['engine'] else set()
        self.modules = {}   # a name bound by an import to a module -> the module's dotted name
        self.inner = set()  # attribute nodes inside a chain already judged at its outer end
        self.dotted = set() # nodes that are the value of an attribute access (x in x.y)

    def visit_Subscript(self, node):   # x["__builtins__"]: a dunder by string key is a dunder
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.startswith('__') and key.value.endswith('__'):
            self._add(node, 'dunder', f'[{key.value!r}]: dunder names are refused, by key too')
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id.startswith('__') and node.id.endswith('__') and node.id not in DUNDERS_OK:
            self._add(node, 'dunder', f'{node.id}: dunder names are refused')
        if not self.profile['bpy'] and node.id not in self.modules:
            self._builtin(node, node.id)
        if (isinstance(node.ctx, ast.Load) and node.id in self.modules and id(node) not in self.dotted
                and self.modules[node.id].split('.')[0] in FOLLOWED):
            self._module_value(node, node.id)
        self.generic_visit(node)

    def _builtin(self, node, name):
        """A builtin pure/rig code may not use is refused wherever its name is read or bound: no binding of it anywhere
        hides the builtin elsewhere (2026-10-12 third re-audit: `def f(locals=None)` freed locals() for the file)."""
        if not name.startswith('__') and _is_builtin(name) and not _builtin_ok(name):   # dunders have their own rule
            self._add(node, 'builtin', f'{name}: not a builtin this code may use or rebind (files, input, namespaces and dynamic code '
                                       'belong to the studio tools)')

    def visit_arg(self, node):
        if not self.profile['bpy']:
            self._builtin(node, node.arg)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        if not self.profile['bpy']:
            self._builtin(node, node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        if not self.profile['bpy']:
            self._builtin(node, node.name)
        self.generic_visit(node)

    def visit_Constant(self, node):   # a dunder in text: format fields ("{0.__globals__}"), dict.get("__builtins__"), ...
        if not self.profile['bpy'] and isinstance(node.value, str) and re.search(r'__\w+__', node.value):
            self._add(node, 'dunder', f'{node.value[:40]!r}: no dunder names in text here (format fields and keys reach attributes too)')
        self.generic_visit(node)

    def _module_value(self, node, text):
        self._add(node, 'module', f'{text} is a module used as a value: use module.name directly, so the import rules see what is reached')

    def _add(self, node, rule, hint, warn=False):
        (self.warnings if warn else self.errors).append({'file': str(self.file), 'line': getattr(node, 'lineno', 0),
                                                         'col': getattr(node, 'col_offset', 0), 'rule': rule, 'hint': hint})

    def _module(self, node, name):
        top = name.split('.')[0]
        if top in self.profile['imports']:
            if top in ('bpy', 'bmesh', 'mathutils', 'bpy_extras') and not self.profile['bpy']:
                self._add(node, 'import', f'{top}: this script runs without Blender (pure Python only)')
            return
        if top in self.engine:
            return
        if top in self.local:
            self.companions.append(self.local[top])
            return
        self._add(node, 'import', f"{name}: not allowed here (allowed: {', '.join(sorted(self.profile['imports']))}, engine modules, "
                                  'modules beside the script or at the project top level). Files, processes and network belong to the studio tools.')

    def visit_Import(self, node):
        for alias in node.names:
            self._module(node, alias.name)
            if alias.name.split('.')[0] in self.profile['imports']:   # engine and local modules are linted as source
                self.modules[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]

    def visit_ImportFrom(self, node):
        if node.level:
            self._add(node, 'import', 'relative imports: import the module by name')
        elif node.module:
            self._module(node, node.module)
            if node.module.split('.')[0] in self.profile['imports']:
                for alias in node.names:   # from x import y: y is judged like the chain x.y
                    if alias.name.startswith('_') and alias.name not in DUNDERS_OK:
                        self._add(node, 'private', f'{node.module}.{alias.name}: private names of a module reach what its import does not allow')
                        continue
                    reached = _modules_along(node.module, [alias.name])
                    stray = _strays(node.module, reached)
                    if stray:
                        self._add(node, 'module', f'{node.module}.{alias.name} is the {stray} module reached through {node.module}: '
                                                  'import it by name, so the import rules see it')
                    elif reached and reached[-1] == f'{node.module}.{alias.name}':   # from collections import abc: a module
                        self.modules[alias.asname or alias.name] = reached[-1]

    def visit_Call(self, node):
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
        if isinstance(node.func, ast.Attribute) and name in METHODS_OK:   # re.compile, pattern methods: not the builtins
            name = None
        if name is not None:
            if name in REFUSED_CALLS:
                self._add(node, f'call:{name}', REFUSED_CALLS[name])
            if name in ('getattr', 'setattr', 'delattr', 'hasattr') and len(node.args) > 1:
                arg = node.args[1]
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    if name != 'hasattr':
                        self._add(node, f'call:{name}', 'a computed attribute name hides what is touched: name it, or use a dict / RNA path '
                                                       'with obj.path_resolve("...") / keyframe_insert(data_path=...)')
                elif arg.value.startswith('__'):
                    self._add(node, f'call:{name}', 'dunder attributes are refused')
                elif not self.profile['bpy'] and arg.value.startswith('_') and arg.value not in PRIVATE_OK:
                    self._add(node, 'private', f'{arg.value}: no private attributes here (only on self/cls)')
            if name in ('format', 'format_map') and isinstance(node.func, ast.Attribute) and not self.profile['bpy']:
                fmt = node.func.value   # a format string's fields read attributes ("{0.x}"): only a literal one is visible here
                if not (isinstance(fmt, ast.Constant) and isinstance(fmt.value, str)):
                    self._add(node, f'call:{name}', 'format a literal string ("...".format) or use an f-string; a computed format '
                                                    'string can read any attribute')
            if name in ('Formatter', 'vformat', 'get_field') and not self.profile['bpy']:
                self._add(node, f'call:{name}', 'string.Formatter reads attributes from text: use a literal "...".format or an f-string')
            if name in ('attrgetter', 'methodcaller'):   # operator's getattr: its names are attribute chains too
                names = node.args if name == 'attrgetter' else node.args[:1]
                for arg in names:
                    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                        self._add(node, f'call:{name}', 'a computed attribute name hides what is touched: name it')
                        continue
                    for part in arg.value.split('.'):
                        if part.startswith('__'):
                            self._add(node, 'dunder', f'{part}: dunder attributes are refused')
                        elif part.startswith('_') and not self.profile['bpy'] and part not in PRIVATE_OK:
                            self._add(node, 'private', f'{part}: no private attributes here (only on self/cls)')
            if name == 'open' and not self.profile['bpy']:
                self._add(node, 'call:open', 'this script reads nothing from disk: pass data through its inputs')
            elif name == 'open':
                mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == 'mode'), None)
                if mode is not None and not (isinstance(mode, ast.Constant) and set(str(mode.value)) <= set('rbt')):
                    self._add(node, 'call:open', 'writing a file: only the build output folder is allowed (enforced at run time)', warn=True)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        self.dotted.add(id(node.value))
        chain = _chain(node)
        attr = node.attr
        if attr.startswith('__') and attr not in DUNDERS_OK:
            self._add(node, 'dunder', f'{attr}: dunder attributes are refused')
        if len(chain) >= 2 and (chain[-2], chain[-1]) in REFUSED_PAIRS:
            self._add(node, f'bpy:{chain[-2]}.{chain[-1]}', REFUSED_PAIRS[(chain[-2], chain[-1])])
        if attr in REFUSED_ATTRS:
            self._add(node, f'bpy:{attr}', REFUSED_ATTRS[attr])
        if len(chain) >= 2 and chain[-2] == 'ops' and attr.startswith('export'):
            self._add(node, f'bpy:ops.{attr}', 'exports are made by the studio tools from the version, not by the author')
        if chain[:1] == ['sys'] and len(chain) >= 2 and chain[1] not in SYS_ATTRS:
            self._add(node, f'sys.{chain[1]}', f"sys: only {', '.join(sorted(SYS_ATTRS))}")
        if chain[:1] == ['os']:
            self._add(node, 'os', 'os: files, processes and the environment belong to the studio tools')
        outer = id(node) not in self.inner
        sub = node.value
        while isinstance(sub, ast.Attribute):   # the chain is judged once, at its outermost attribute
            self.inner.add(id(sub)); sub = sub.value
        judged = False
        if outer and chain and chain[0] in self.modules and len(chain) >= 2:
            rest, origin = chain[1:], self.modules[chain[0]]
            if any(a.startswith('_') and not (a.startswith('__') and a in DUNDERS_OK) for a in rest):
                self._add(node, 'private', f"{'.'.join(chain)}: private names of a module reach what its import does not allow")
                judged = True
            else:
                reached = _modules_along(origin, rest)
                stray = _strays(origin, reached)
                if stray:   # its own submodules (collections.abc) are the module itself; anything else came through it
                    self._add(node, 'module', f"{'.'.join(chain)} is the {stray} module reached through {origin}: import what you use "
                                              'by name, so the import rules see it')
                elif reached and reached[-1] == '.'.join([origin] + rest):
                    self._module_value(node, '.'.join(chain))   # the chain ends on a module (collections.abc) used as a value
        private = [a for a in chain[1:] if a.startswith('_') and not (a.startswith('__') and a in DUNDERS_OK) and a not in PRIVATE_OK]
        if outer and not judged and not self.profile['bpy'] and private and not (chain[:1] in (['self'], ['cls']) and len(chain) == 2):
            self._add(node, 'private', f"{'.'.join(chain)}: no private attributes here (only on self/cls)")
        self.generic_visit(node)


def lint(path, profile='author', search_roots=()):
    """{'errors': [...], 'warnings': [...], 'modules': {file: sha256}} for a script and every companion module it imports."""
    path = Path(path).resolve()
    roots = [path.parent] + [Path(r).resolve() for r in search_roots]
    local = {}
    for root in reversed(roots):
        for module in root.glob('*.py'):
            local[module.stem] = module
    errors, warnings, modules, queue, seen = [], [], {}, [path], set()
    while queue:
        file = queue.pop(0)
        if file in seen:
            continue
        seen.add(file)
        source = file.read_text(encoding='utf-8')
        modules[str(file)] = hashlib.sha256(source.encode()).hexdigest()
        try:
            tree = ast.parse(source, str(file))
        except SyntaxError as error:
            errors.append({'file': str(file), 'line': error.lineno or 0, 'col': error.offset or 0, 'rule': 'syntax', 'hint': str(error.msg)})
            continue
        linter = _Linter(file, profile, {k: v for k, v in local.items() if v != file})
        linter.visit(tree)
        errors += linter.errors
        warnings += linter.warnings
        queue += linter.companions
    return {'errors': errors, 'warnings': warnings, 'modules': modules}


def require_clean(path, profile='author', search_roots=()):
    result = lint(path, profile, search_roots)
    if result['errors']:
        rows = '; '.join(f"{Path(e['file']).name}:{e['line']} {e['rule']} - {e['hint']}" for e in result['errors'][:8])
        raise StudioError('AUTHOR_SCRIPT_REFUSED', f'{len(result["errors"])} refused construct(s): {rows}',
                          recovery='Rewrite those lines with the allowed means the hints name (studio/author_lint.py); '
                                   'data the scene needs goes in shot.scene or the subject spec')
    return result
