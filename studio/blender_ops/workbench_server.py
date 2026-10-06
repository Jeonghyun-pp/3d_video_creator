"""Resident Blender for fast, typed scene work: one request per Unix-socket connection, JSON lines.

blender -b --factory-startup --disable-autoexec [scene_copy.blend] --python workbench_server.py -- session.json

Request: {"token": "...", "tool": "<allow-listed name>", "args": {...}}
Reply:   {"ok": true, "result": {...}, "ms": 1.2} | {"ok": false, "error": "..."}
Every call is appended to ops.jsonl (tool, args, kind, ok, ms, result digest); the host builds the
commit patch from it. Only tools in workbench_tools.TOOLS exist; ``exec`` needs --allow-exec at start
and marks the session non-replayable. The server opens a copy of a version, never the version itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).parent))
import bpy  # noqa: E402

import workbench_tools as tools  # noqa: E402

session_path = Path(sys.argv[sys.argv.index('--') + 1])
session = json.loads(session_path.read_text())
session_dir = session_path.parent
token = Path(session['token_path']).read_text().strip()
state = {'specs': {}, 'session_dir': str(session_dir), 'project_dir': session['project_dir'], 'allow_exec': session.get('allow_exec', False),
         'output_size': session.get('output_size'), 'shot': session.get('shot'), 'fps': session.get('fps', 30), 'checkpoints': {}}
for subject_id, path in session.get('spec_paths', {}).items():
    state['specs'][subject_id] = json.loads(Path(path).read_text())
ops_path = session_dir / 'ops.jsonl'


def digest(result):
    return hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()[:16]


def handle(request):
    if not isinstance(request, dict) or not hmac.compare_digest(str(request.get('token', '')), token):
        return {'ok': False, 'error': 'bad token'}
    tool, args = request.get('tool'), request.get('args') or {}
    if tool == 'shutdown':
        return {'ok': True, 'result': {'stopping': True}, 'stop': True}
    started = time.perf_counter()
    try:
        result, kind = tools.call(state, tool, args)
        ok, error = True, None
    except Exception as exc:  # report, keep serving
        result, kind, ok, error = None, tools.TOOLS.get(tool, (None, 'unknown'))[1], False, f'{type(exc).__name__}: {exc}'
        (session_dir / 'last_error.txt').write_text(traceback.format_exc())
    ms = round((time.perf_counter() - started) * 1000, 2)
    with ops_path.open('a') as stream:
        stream.write(json.dumps({'tool': tool, 'args': args, 'kind': kind, 'ok': ok, 'ms': ms, 'result_sha': digest(result) if ok else None,
                                 'non_replayable': bool(state.get('non_replayable'))}, default=str) + '\n')
    return {'ok': True, 'result': result, 'ms': ms} if ok else {'ok': False, 'error': error, 'ms': ms}


def serve():
    sock_path = session['socket']
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    os.chmod(sock_path, 0o600)
    server.listen(4)
    (session_dir / 'ready').write_text(json.dumps({'pid': os.getpid(), 'blender': bpy.app.version_string}))
    try:
        while True:
            conn, _ = server.accept()
            with conn:
                data = b''
                while not data.endswith(b'\n'):
                    chunk = conn.recv(1 << 16)
                    if not chunk:
                        break
                    data += chunk
                try:
                    reply = handle(json.loads(data.decode() or '{}'))
                except Exception as exc:  # noqa: BLE001 - a malformed request is answered, the session keeps serving
                    reply = {'ok': False, 'error': f'bad request: {type(exc).__name__}: {exc}'}
                stop = reply.pop('stop', False)
                try:
                    conn.sendall((json.dumps(reply, default=str) + '\n').encode())
                except OSError:   # the client went away; the session stays up
                    pass
                if stop:
                    break
    finally:
        server.close()
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        (session_dir / 'stopped').write_text(time.strftime('%Y-%m-%dT%H:%M:%S'))


serve()
