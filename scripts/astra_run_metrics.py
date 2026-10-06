#!/usr/bin/env python3
"""Measure where an Astra (codex exec) run got stuck: read its rollout event stream(s) and count what it did.

Metrics per run: tool calls, shell commands, studio operations, studio error codes, failed commands (non-zero exit),
repeated identical commands, workbench use (MCP tool or `studio workbench ...`), author-script edits and lines, shot builds
and how many it took until the first successful one, and wall time. Same metrics for every run, so a baseline (P0) and a
re-measure (P6) compare directly.

Usage:
  scripts/astra_run_metrics.py ROLLOUT.jsonl [...]            # metrics for the given rollouts
  scripts/astra_run_metrics.py --repo-runs [--since 2026-10-01] # every rollout whose cwd is this repository
"""
import argparse
import collections
import glob
import json
import os
import re
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SESSIONS = Path.home() / '.codex' / 'sessions'
CMD = re.compile(r'cmd\s*:\s*"((?:[^"\\]|\\.)*)"')
STUDIO_OP = re.compile(r'-m studio ((?:[a-z][a-z0-9_-]*)(?: [a-z][a-z0-9_-]*)?)')
ERROR_CODE = re.compile(r'"code":\s*"([A-Z][A-Z0-9_]+)"')
EXIT = re.compile(r'"exit_code":\s*(-?\d+)')
AUTHOR_FILE = re.compile(r'(?:author[^\s"\']*\.py)')


def _text(output):
    if isinstance(output, str):
        return output
    return ''.join(x.get('text', '') for x in output if isinstance(x, dict))


def _unescape(text):
    try:   # tool output carries a JSON string with the command output inside; decode one level when it parses
        out = json.loads(text[text.index('{'):])['output']
        return out if isinstance(out, str) else json.dumps(out)
    except Exception:
        return text.replace('\\"', '"').replace('\\n', '\n')


def run_metrics(path):
    calls, cmds, ops, codes, failed = 0, [], collections.Counter(), collections.Counter(), 0
    workbench, author_edits, author_lines, builds = collections.Counter(), 0, 0, []
    meta, start, end, last_message = {}, None, None, ''
    pending = {}
    for line in open(path, encoding='utf-8'):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        stamp = event.get('timestamp')
        if stamp:
            t = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            start = start or t; end = t
        payload = event.get('payload') or {}
        kind = payload.get('type')
        if event.get('type') == 'session_meta':
            meta = {'cwd': payload.get('cwd'), 'cli': payload.get('cli_version'), 'id': payload.get('id')}
        elif kind == 'task_complete':
            last_message = payload.get('last_agent_message') or ''
        elif kind in ('custom_tool_call', 'function_call'):
            calls += 1
            body = payload.get('input') or payload.get('arguments') or ''
            name = payload.get('name') or ''
            if 'workbench' in name or 'studio_workbench' in body:
                workbench['mcp'] += 1
            found = [bytes(c, 'utf-8').decode('unicode_escape', 'ignore') for c in CMD.findall(body)]
            if name == 'exec_command' and not found:
                try:
                    found = [json.loads(body).get('cmd', '')]
                except ValueError:
                    pass
            if 'apply_patch' in body or 'apply_patch' in name:
                for file in set(AUTHOR_FILE.findall(body)):
                    author_edits += 1
                author_lines += sum(1 for l in body.split('\\n') if l.startswith('+') and not l.startswith('+++')) if AUTHOR_FILE.search(body) else 0
            for c in found:
                cmds.append(c)
                for op in STUDIO_OP.findall(c):
                    ops[op] += 1
                    if op.startswith('workbench'):
                        workbench['cli'] += 1
                if AUTHOR_FILE.search(c) and re.search(r'(cat|tee)\s*>|<<', c):
                    author_edits += 1
            pending[payload.get('call_id')] = [c for c in found if re.search(r'-m studio shot build|studio shot build', c)]
        elif kind in ('custom_tool_call_output', 'function_call_output'):
            text = _text(payload.get('output'))
            for exit_code in EXIT.findall(text):
                failed += int(exit_code) != 0
            inner = _unescape(text)
            for code in ERROR_CODE.findall(inner):
                codes[code] += 1
            for _ in pending.pop(payload.get('call_id'), []) or []:
                builds.append('"ok": true' in inner and '"operation": "shot.build"' in inner)
    repeats = {c: n for c, n in collections.Counter(cmds).items() if n >= 3}
    first_ok = next((i + 1 for i, ok in enumerate(builds) if ok), None)
    return {'rollout': str(path), **meta,
            'minutes': round((end - start).total_seconds() / 60, 1) if start else 0,
            'tool_calls': calls, 'commands': len(cmds), 'failed_commands': failed,
            'studio_ops': dict(ops.most_common()), 'error_codes': dict(codes.most_common()),
            'repeated_commands': dict(sorted(repeats.items(), key=lambda kv: -kv[1])[:10]),
            'workbench': dict(workbench), 'author_edits': author_edits, 'author_lines_added': author_lines,
            'shot_builds': len(builds), 'builds_until_first_ok': first_ok, 'final_message': last_message[:600]}


def repo_runs(since=None):
    rows = []
    for f in glob.glob(str(SESSIONS / '*' / '*' / '*' / 'rollout-*.jsonl')):
        try:
            with open(f, encoding='utf-8') as h:
                cwd = json.loads(h.readline()).get('payload', {}).get('cwd') or ''
        except (ValueError, OSError):
            continue
        if cwd.startswith(str(REPO)) and (not since or os.path.basename(f)[8:18] >= since):
            rows.append(f)
    return sorted(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('rollouts', nargs='*')
    parser.add_argument('--repo-runs', action='store_true', help='every rollout whose cwd is this repository')
    parser.add_argument('--since', help='YYYY-MM-DD (with --repo-runs)')
    parser.add_argument('--out', help='write the JSON here too')
    args = parser.parse_args()
    paths = args.rollouts + (repo_runs(args.since) if args.repo_runs else [])
    if not paths:
        parser.error('give rollout files or --repo-runs')
    runs = [run_metrics(p) for p in paths]
    total = collections.Counter()
    for r in runs:
        total.update(r['error_codes'])
    result = {'runs': runs, 'summary': {'runs': len(runs), 'error_codes': dict(total.most_common()),
                                       'runs_using_workbench': sum(1 for r in runs if r['workbench']),
                                       'failed_commands': sum(r['failed_commands'] for r in runs)}}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
