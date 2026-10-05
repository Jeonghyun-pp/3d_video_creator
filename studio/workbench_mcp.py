"""MCP (stdio JSON-RPC 2.0) front end for the workbench, so Codex/Astra calls typed tools natively.

Dependency-free: newline-delimited JSON-RPC on stdin/stdout (MCP stdio transport). It only wraps
studio.workbench: the allow-list, ownership rules and replayed commits stay in one place. ``exec`` is
never exposed here, whatever the session allows.

Run: .venv/bin/python -m studio.workbench_mcp   (configured in .codex/config.toml)
"""
from __future__ import annotations

import json
import sys

from . import workbench
from .common import StudioError

PROTOCOL = '2025-06-18'
HIDDEN = {'exec'}
OBJ = {'type': 'object'}
TOOLS = [
    {'name': 'workbench_start', 'description': 'Start a resident Blender session on a copy of a shot version (or empty, with subjects). Returns session_id.',
     'inputSchema': {'type': 'object', 'required': ['project'], 'properties': {'project': {'type': 'string'}, 'shot': {'type': 'string'},
                     'version': {'type': 'string'}, 'subjects': {'type': 'array', 'items': {'type': 'string'}}}}},
    {'name': 'workbench_call', 'description': 'Call one typed workbench tool: scene_graph, measure, subject_report, api_lookup, preview, '
                                              'build_subject, set_spec_param, set_spec, set_transform, set_modifier_input, set_material_param, '
                                              'set_camera_keys, checkpoint, restore, variant_save, variant_restore. Spec-owned data changes only via set_spec_param; '
                                              'save alternatives with variant_save and compare them before committing camera or motion choices.',
     'inputSchema': {'type': 'object', 'required': ['project', 'session', 'tool'], 'properties': {'project': {'type': 'string'},
                     'session': {'type': 'string'}, 'tool': {'type': 'string'}, 'args': OBJ}}},
    {'name': 'workbench_commit', 'description': 'Replay the session through shot build --base; succeeds only if the build reproduces the session measurements. After comparing variants, restore the chosen one first and pass chosen_variant + why.',
     'inputSchema': {'type': 'object', 'required': ['project', 'session'], 'properties': {'project': {'type': 'string'}, 'session': {'type': 'string'},
                     'diagnosis': {'type': 'string'}, 'chosen_variant': {'type': 'string'}, 'why': {'type': 'string'}}}},
    {'name': 'workbench_compare', 'description': 'Render >= 2 saved variants (workbench_call variant_save) at the same frames side by side; returns a contact sheet and their fidelity summaries. Exploration only: no version, no repair budget.',
     'inputSchema': {'type': 'object', 'required': ['project', 'session', 'names'], 'properties': {'project': {'type': 'string'}, 'session': {'type': 'string'},
                     'names': {'type': 'array', 'items': {'type': 'string'}}, 'frames': {'type': 'array', 'items': {'type': 'integer'}},
                     'views': {'type': 'array', 'items': {'type': 'string'}}}}},
    {'name': 'workbench_stop', 'description': 'Stop a session.',
     'inputSchema': {'type': 'object', 'required': ['project', 'session'], 'properties': {'project': {'type': 'string'}, 'session': {'type': 'string'}}}},
]


def run_tool(name, args):
    if name == 'workbench_start':
        return workbench.start(args['project'], args.get('shot'), args.get('version'), args.get('subjects'))
    if name == 'workbench_call':
        if args['tool'] in HIDDEN:
            raise StudioError('INPUT_INVALID', f"{args['tool']} is not available over MCP")
        return workbench.call(args['project'], args['session'], args['tool'], args.get('args') or {})
    if name == 'workbench_commit':
        return workbench.commit(args['project'], args['session'], args.get('diagnosis'), args.get('chosen_variant'), args.get('why'))
    if name == 'workbench_compare':
        return workbench.compare(args['project'], args['session'], args['names'], args.get('frames') or [1], args.get('views') or ['shot'])
    if name == 'workbench_stop':
        return workbench.stop(args['project'], args['session'])
    raise StudioError('INPUT_INVALID', f'unknown tool {name}')


def handle(message):
    method, mid = message.get('method'), message.get('id')
    if mid is None:
        return None  # notification (e.g. notifications/initialized)
    if method == 'initialize':
        result = {'protocolVersion': message.get('params', {}).get('protocolVersion', PROTOCOL),
                  'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'studio-workbench', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': TOOLS}
    elif method == 'tools/call':
        params = message.get('params', {})
        try:
            payload = run_tool(params.get('name'), params.get('arguments') or {})
            result = {'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False, default=str)}], 'isError': False}
        except StudioError as exc:
            result = {'content': [{'type': 'text', 'text': json.dumps(exc.as_dict(), ensure_ascii=False)}], 'isError': True}
        except (KeyError, ValueError, OSError) as exc:
            result = {'content': [{'type': 'text', 'text': f'{type(exc).__name__}: {exc}'}], 'isError': True}
    elif method == 'ping':
        result = {}
    else:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'method not found: {method}'}}
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


def serve(stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError as exc:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': str(exc)}}
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + '\n')
            stdout.flush()


if __name__ == '__main__':
    serve()
