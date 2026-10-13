"""Runtime line for code an agent writes that runs inside Blender (author scripts, revision patches, rig scripts).

Threat model, stated plainly: Blender has no real Python sandbox. The goal is that an agent-written script cannot
casually or by accident step around a gate, and that any attempt leaves a record. Three lines together:
  1. studio/author_lint.py refuses the constructs before Blender starts (host, ast);
  2. this audit hook judges what the script actually does while it runs (sys.addaudithook - it cannot be removed);
  3. the author runs in its own Blender process (build_author.py) that only hands a .blend to the trusted build, and
     the host re-hashes every input it wrote before the trusted build starts.
C-level writes from Blender itself (bpy.ops.wm.save_as_mainfile to any path) are outside Python's audit events;
lint refuses them and the host hash check catches a changed input. STUDIO_OS_SANDBOX=1 (macOS sandbox-exec) closes
that for the author process.

Only events raised while an author frame (a file in `author_files`) is on the stack are judged: Blender's and the
engine's own work is never touched. Imports are judged by the frame that asked for them.

Contrib entries (studio/contrib.py) are pure functions and get a stricter rule, default deny (add_pure, called by
blender_ops/contrib_loader.py before an entry runs - in a build, a workbench session and the host's contract and probe
children alike): while a frame of a registered entry folder is on the stack every audited event is refused, except an
import the 'pure' profile allows (already loaded, so nothing is read) and the few events library code raises for its own
work when the entry did not ask for them directly (dataclasses and namedtuple compile their generated methods). Files
(reading too), processes, network, changing the environment, code objects and the restricted attributes (__code__, frames) are
all refused. This rule is always enforced, whatever the stage's mode. Reading os.environ raises no audit event:
the environment is kept clean instead - every process that runs agent code gets common.blender_env's allow-list.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
DENIED = ('subprocess.Popen', 'os.system', 'os.exec', 'os.spawn', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'os.kill', 'os.killpg',
          'socket.', 'ctypes.', 'urllib.Request', 'http.client.', 'webbrowser.', 'sys.addaudithook', 'os.putenv', 'os.unsetenv',
          'sys.setprofile', 'sys.settrace', 'os.chmod', 'os.chown', 'os.link', 'os.symlink')
PATH_EVENTS = {'os.remove': (0,), 'os.unlink': (0,), 'os.rmdir': (0,), 'os.rename': (0, 1), 'os.replace': (0, 1), 'os.mkdir': (0,),
               'shutil.rmtree': (0,), 'shutil.copyfile': (1,), 'shutil.copytree': (1,), 'shutil.move': (0, 1), 'os.truncate': (0,)}
PREIMPORT = ('numpy', 'ctypes', 'json', 'hashlib', 'random', 'math', 'bisect', 'copy', 'collections', 'statistics', 'importlib.util')
IMPORT_REFUSED = 'studio.contrib.import'   # raised by contrib_loader's import when it refuses a module already loaded
PURE_EVENTS = ('builtins.id',)              # events of what the pure profile itself allows (id() is a builtin it lists)
# Events library code raises for its own work (generated methods read and set on a class, its caller's module name):
# allowed under the pure rule only when the frame that raised them is not entry code.
LIBRARY_WORK = ('compile', 'exec', 'sys._getframe', 'sys._getframemodulename', 'object.__getattr__', 'object.__setattr__',
                'object.__delattr__')


def _writes(event, args):
    if event == 'open':
        path, mode, flags = (list(args) + [None, None, None])[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and flags & WRITE_FLAGS)
        return [path] if writing else []
    return [args[i] for i in PATH_EVENTS.get(event, ()) if i < len(args)]


def judge(event, args, *, write_roots, protected, allowed_imports=None, importer_is_author=False):
    """The policy, pure: a refusal reason, or None. `importer_is_author`: the frame asking for an import is author code."""
    if event.startswith(DENIED) or event in DENIED:
        return f'{event}: processes, network, native code and the environment belong to the studio tools'
    if event == 'compile':
        source, filename = (list(args) + [None, None])[:2]
        if not (isinstance(filename, str) and Path(filename).is_file()):
            return 'compile: code from strings cannot be reviewed'
        if not _is_file_source(source, filename):   # a real file name on other text (2026-10-13: named itself after an engine file)
            return f'compile: the code is not the text of {filename}'
    if event == 'code.__new__':
        return 'code.__new__: code objects are made by compiling reviewed files, not built or relabelled'
    if event == 'import' and importer_is_author and allowed_imports is not None:
        top = str(args[0]).split('.')[0]
        if top not in allowed_imports:
            return f'import {args[0]}: not allowed (studio/author_lint.py lists what an author may import)'
    for target in _writes(event, args):
        if isinstance(target, int):
            continue
        if not isinstance(target, (str, bytes, os.PathLike)):
            return f'{event}: unknown target'
        path = Path(os.fsdecode(target)).resolve()
        if any(path == p for p in protected):
            return f'{event} {path}: an input the build wrote is read-only'
        if not any(path.is_relative_to(root) for root in write_roots):
            return f'{event} {path}: writes go only to the build output folder or the temp folder'
    return None


def _is_file_source(source, filename):
    """The text being compiled is exactly the file it names (bytes as read, or text as the import system decodes it)."""
    try:
        data = Path(filename).read_bytes()
    except OSError:
        return False
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source) == data
    if isinstance(source, str):
        import importlib.util
        return source == importlib.util.decode_source(data) or source == data.decode('utf-8', 'surrogateescape')
    return False   # an AST or anything else: not a reviewed file


def judge_pure(event, args, *, imports, asker_is_entry):
    """The pure rule (contrib entries), default deny: a refusal reason, or None. `asker_is_entry`: the frame that raised
    the event is entry code (not library code working for it)."""
    if event in ('import', IMPORT_REFUSED):
        top = str(args[0]).split('.')[0] if args else ''
        if top in imports and event == 'import':
            return None
        return f'import {args[0] if args else ""}: a contrib entry imports only what the pure profile lists (studio/author_lint.py)'
    if event in PURE_EVENTS or (event in LIBRARY_WORK and not asker_is_entry):
        return None
    return f'{event}: a contrib entry is a pure function - no files, processes, network, environment or dynamic code'


class _State:
    installed = False        # the stage hook (install)
    pure_add = None          # the pure hook is installed; adds a folder (add_pure)
    violations = []
    report = None


def caller_frame(depth):
    """The frame ``depth`` levels above the function calling this one, or None when the stack is not that deep - an
    audited event raised from C with no Python caller (interpreter shutdown) has no frame, so no author frame either."""
    try:
        return sys._getframe(depth + 1)
    except ValueError:   # sys._getframe's documented answer for a stack shallower than depth
        return None


def _on_stack(frame, test):
    while frame is not None:
        if test(frame.f_code.co_filename):
            return True
        frame = frame.f_back
    return False


_IMPORTLIB = Path(__import__('importlib').__file__).parent
IMPORT_MACHINERY = tuple(sorted({str(_IMPORTLIB) + '/', str(_IMPORTLIB.resolve()) + '/'}))   # the importlib package, as named and real


def _is_import_machinery(filename):
    """The import system's own frames: frozen bootstrap code or files of the importlib package - by location, never by
    a name containing 'importlib' (2026-10-13 re-audit: an entry folder named importlib_disc passed as library code)."""
    return filename.startswith('<frozen') or filename.startswith(IMPORT_MACHINERY)


def _asker(frame):
    """The frame that asked: past the import machinery. Library code compiling its own strings while an author import
    runs (collections.namedtuple at import time) is not the author writing code from strings (2026-10-09: importing
    frame_probe from an author script tripped 'code from strings' through namedtuple)."""
    while frame is not None and _is_import_machinery(frame.f_code.co_filename):
        frame = frame.f_back
    return frame


def _record(stage, event, reason):
    _State.violations.append({'stage': stage, 'event': event, 'reason': reason})
    _flush()


def _guarded(judged, frame_of=caller_frame):
    """An audit hook around `judged(event, args, frame) -> (reason, enforce) or None`: the frame that raised the event,
    never re-entered by the hook's own work. Its state and helpers are its closure, bound when it is installed: code
    that rebinds this module's names later changes nothing the hook does."""
    busy = [False]

    def hook(event, args):
        if busy[0]:
            return
        busy[0] = True
        try:
            verdict = judged(event, args, frame_of(1))
        finally:
            busy[0] = False
        if verdict and verdict[1]:
            raise PermissionError('STUDIO_SANDBOX: ' + verdict[0])
    return hook


def _folder_names(folder):
    return {str(folder).rstrip('/') + '/', str(Path(folder).resolve()).rstrip('/') + '/'}   # code names files as given; /var vs /private/var


def add_pure(folder, imports):
    """Put an entry folder under the pure rule; its hook is installed once per process, apart from (and whatever the
    mode of) any stage hook. Only ever stricter: a folder is never taken off, and the import list is the one profile.
    The folders live in the hook's closure, not in module state code could rebind."""
    _install_pure()(folder, frozenset(imports))


def _install_pure():
    """The pure hook, once per process; returns the function that adds a folder. A stage installs it with its own hook
    (install), so an entry loaded later while author code is on the stack never adds a hook then (sys.addaudithook is
    refused to author code)."""
    if _State.pure_add is not None:
        return _State.pure_add
    dirs, profile = [()], []

    def is_pure(filename):
        return filename.startswith(dirs[0])

    def add(folder, imports):
        if not profile:
            for name in sorted(imports):   # loaded now, outside entry code: an entry's import then reads nothing
                __import__(name)
            profile.append(imports)
        elif imports != profile[0]:
            raise PermissionError('STUDIO_SANDBOX: one pure import list per process (studio/author_lint.py)')
        dirs[0] = tuple(sorted(set(dirs[0]) | _folder_names(folder)))

    def judged(event, args, frame, on_stack=_on_stack, asker=_asker, rule=judge_pure, record=_record):
        if not dirs[0] or not on_stack(frame, is_pure):
            return None
        first = asker(frame)
        reason = rule(event, args, imports=profile[0], asker_is_entry=first is not None and is_pure(first.f_code.co_filename))
        if reason:
            record('contrib', event, reason)
            return reason, True
        return None
    sys.addaudithook(_guarded(judged))
    _State.pure_add = add
    return add


def install(*, stage, author_files, write_roots, protected=(), allowed_imports=None, mode='enforce', report=None):
    """Install the stage hook once per process. mode 'enforce' raises PermissionError('STUDIO_SANDBOX: ...'); 'record'
    only logs."""
    if _State.installed:
        return
    _install_pure()
    for name in PREIMPORT:   # imported now so their native loading is not judged as author work
        __import__(name)
    authors = {str(f) for f in author_files} | {str(Path(f).resolve()) for f in author_files}   # code names files as given; /var vs /private/var

    def is_author(filename):
        return filename in authors
    roots = [Path(r).resolve() for r in write_roots]
    guarded = {Path(p).resolve() for p in protected}
    _State.report = report

    def judged(event, args, frame, on_stack=_on_stack, asker=_asker, rule=judge, record=_record, enforce=mode == 'enforce'):
        if event in ('object.__getattr__', 'object.__setattr__', 'sys._getframe', 'sys._getframemodulename', 'marshal.loads', 'exec'):
            return None   # too frequent to judge one by one; dynamic code is caught at compile (and code.__new__)
        if event in ('import', 'compile'):
            first = asker(frame)
            if first is None or not is_author(first.f_code.co_filename):
                return None
            reason = rule(event, args, write_roots=roots, protected=guarded, allowed_imports=allowed_imports, importer_is_author=True)
        elif not on_stack(frame, is_author):
            return None
        else:
            reason = rule(event, args, write_roots=roots, protected=guarded)
        if reason:
            record(stage, event, reason)
            return reason, enforce
        return None
    sys.addaudithook(_guarded(judged))
    _State.installed = True


def _flush():
    if _State.report:
        try:
            Path(_State.report).write_text(json.dumps({'violations': _State.violations[-200:]}, indent=1))
        except OSError:
            pass


def violations():
    return list(_State.violations)
