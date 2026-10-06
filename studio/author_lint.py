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
import hashlib
from pathlib import Path

from .common import REPO, StudioError

OPS = REPO / 'studio' / 'blender_ops'
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
              '__iter__', '__len__', '__getitem__', '__enter__', '__exit__', '__future__'}
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


def _engine_modules():
    """Engine modules an author may import: studio/blender_ops/*.py and the modeling package, minus entry scripts."""
    names = set()
    for path in OPS.glob('*.py'):
        tree = ast.parse(path.read_text())
        entry = any(isinstance(n, ast.Attribute) and n.attr == 'argv' and isinstance(n.value, ast.Name) and n.value.id == 'sys'
                    for stmt in tree.body if not isinstance(stmt, (ast.FunctionDef, ast.ClassDef)) for n in ast.walk(stmt))
        if not entry:
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


class _Linter(ast.NodeVisitor):
    def __init__(self, file, profile, local):
        self.file, self.profile, self.local = file, PROFILES[profile], local
        self.errors, self.warnings, self.companions = [], [], []
        self.engine = _engine_modules() if self.profile['engine'] else set()

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

    def visit_ImportFrom(self, node):
        if node.level:
            self._add(node, 'import', 'relative imports: import the module by name')
        elif node.module:
            self._module(node, node.module)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            name = node.func.id
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
            if name == 'open' and not self.profile['bpy']:
                self._add(node, 'call:open', 'this script reads nothing from disk: pass data through its inputs')
            elif name == 'open':
                mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == 'mode'), None)
                if mode is not None and not (isinstance(mode, ast.Constant) and set(str(mode.value)) <= set('rbt')):
                    self._add(node, 'call:open', 'writing a file: only the build output folder is allowed (enforced at run time)', warn=True)
        self.generic_visit(node)

    def visit_Attribute(self, node):
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
