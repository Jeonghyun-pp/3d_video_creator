"""Run a studio command where Blender can run: outside the agent's sandbox, inside the studio's own.

The codex sandbox (seatbelt) denies the GPU (IOKit AGXDeviceUserClient / IOSurfaceRootUserClient); Blender then dies at
Metal start-up (measured 2026-10-06, exit 139), and the frame probe and previews need the GPU anyway. So Blender never
runs in the agent's sandbox: the studio MCP server (studio/workbench_mcp.py, tool `studio_run`), which codex starts
outside its sandbox, runs the command here, under a profile of our own:
  - GPU allowed; network denied (paid generation is impossible from here, and no API keys are passed: blender_env);
  - writes only inside the repository and the temp folders;
  - only `python -m studio <args>` - not a shell.
Every gate of the command itself still runs (the author isolation, the frame probe, the frozen-code check, the user's
words for approvals): the broker gives Blender access, not authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .common import REPO, StudioError, blender_env

BROKERED = 'STUDIO_BROKERED'
NO_BROKER = ('freeze', 'contrib')   # commands that judge or record the lines themselves are not run through the broker


def profile():
    """The macOS sandbox profile a brokered command runs under."""
    roots = {str(Path(REPO).resolve()), str(Path(tempfile.gettempdir()).resolve()), '/private/var/folders', '/private/tmp'}
    venv = Path(sys.executable).resolve().parents[1]
    roots.add(str(venv / 'lib'))   # __pycache__ of the studio environment
    writable = ' '.join(f'(subpath "{r}")' for r in sorted(roots))
    return ('(version 1)(allow default)(deny network*)(allow network* (remote unix-socket))(allow network* (local unix-socket))'
            f'(deny file-write*)(allow file-write* {writable} (literal "/dev/null") (regex #"^/dev/tty") (regex #"^/dev/dtracehelper"))')


def command(args):
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise StudioError('INPUT_INVALID', 'studio_run takes args: a list of strings, e.g. ["shot", "build", "--project", "...", "--shot", "s01"]')
    if args[0].startswith('-') or args[0] in NO_BROKER or args[0] == '_worker':
        raise StudioError('INPUT_INVALID', f'studio_run does not run {args[0]!r}' + (' (run it in your own shell: it needs no Blender)' if args[0] in NO_BROKER else ''))
    base = [sys.executable, '-m', 'studio', *args]
    if sys.platform == 'darwin':
        return ['sandbox-exec', '-p', profile(), *base]
    return base


def run(args, timeout=3600):
    """The command's JSON result (ok or not), plus its exit code."""
    env = {**blender_env(), BROKERED: '1', 'PYTHONPATH': ''}
    if os.environ.get('STUDIO_RENDER_DEVICE'):
        env['STUDIO_RENDER_DEVICE'] = os.environ['STUDIO_RENDER_DEVICE']
    try:
        done = subprocess.run(command(args), cwd=REPO, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise StudioError('TIMEOUT', f'studio {" ".join(args[:3])} ran over {timeout} s', retryable=True) from exc
    lines = [l for l in done.stdout.splitlines() if l.startswith('{')]
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    if result is None:
        result = {'ok': False, 'error': {'code': 'COMMAND_FAILED', 'message': (done.stdout + done.stderr)[-2000:]}}
    return {**result, 'exit_code': done.returncode}


def in_agent_sandbox():
    """True inside the codex sandbox, where Blender cannot start (and not inside the broker, which runs outside it)."""
    return bool(os.environ.get('CODEX_SANDBOX')) and not os.environ.get(BROKERED)
