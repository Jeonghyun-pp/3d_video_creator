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
PREIMPORT = ('numpy', 'ctypes', 'json', 'hashlib', 'random', 'math', 'bisect', 'copy', 'collections', 'statistics')


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
        filename = args[1] if len(args) > 1 else None
        if not (isinstance(filename, str) and Path(filename).is_file()):
            return 'compile: code from strings cannot be reviewed'
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


class _State:
    installed = False
    violations = []
    report = None


def install(*, stage, author_files, write_roots, protected=(), allowed_imports=None, mode='enforce', report=None, author_roots=()):
    """Install the hook once per process. mode 'enforce' raises PermissionError('STUDIO_SANDBOX: ...'); 'record' only logs."""
    if _State.installed:
        return
    for name in PREIMPORT:   # imported now so their native loading is not judged as author work
        __import__(name)
    authors = {str(f) for f in author_files} | {str(Path(f).resolve()) for f in author_files}   # code names files as given; /var vs /private/var
    # folders whose code is agent-written (contrib entries a session may load later than the hook is installed)
    author_dirs = tuple(sorted({str(r).rstrip('/') + '/' for r in author_roots} | {str(Path(r).resolve()).rstrip('/') + '/' for r in author_roots}))

    def is_author(filename):
        return filename in authors or (bool(author_dirs) and filename.startswith(author_dirs))
    roots = [Path(r).resolve() for r in write_roots]
    guarded = {Path(p).resolve() for p in protected}
    _State.report = report

    def author_on_stack():
        frame = sys._getframe(2)
        while frame is not None:
            if is_author(frame.f_code.co_filename):
                return True
            frame = frame.f_back
        return False

    def importer_is_author():
        frame = sys._getframe(2)
        while frame is not None and (frame.f_code.co_filename.startswith('<frozen') or 'importlib' in frame.f_code.co_filename):
            frame = frame.f_back
        return frame is not None and is_author(frame.f_code.co_filename)

    def hook(event, args):
        if event in ('object.__getattr__', 'object.__setattr__', 'sys._getframe', 'marshal.loads', 'code.__new__', 'exec'):
            return   # too frequent to judge one by one; dynamic code is caught at compile
        if event == 'import':
            if not importer_is_author():
                return
            reason = judge(event, args, write_roots=roots, protected=guarded, allowed_imports=allowed_imports, importer_is_author=True)
        else:
            if not author_on_stack():
                return
            reason = judge(event, args, write_roots=roots, protected=guarded)
        if reason:
            _State.violations.append({'stage': stage, 'event': event, 'reason': reason})
            _flush()
            if mode == 'enforce':
                raise PermissionError('STUDIO_SANDBOX: ' + reason)

    sys.addaudithook(hook)
    _State.installed = True


def _flush():
    if _State.report:
        try:
            Path(_State.report).write_text(json.dumps({'violations': _State.violations[-200:]}, indent=1))
        except OSError:
            pass


def violations():
    return list(_State.violations)
