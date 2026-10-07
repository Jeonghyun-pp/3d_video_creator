#!/usr/bin/env python3
"""Measure where an Astra (codex exec) run got stuck: read its rollout event stream(s) and count what it did.

Metrics per run: tool calls, shell commands, studio operations, studio error codes, failed commands (non-zero exit),
repeated identical commands, workbench use (MCP tool or `studio workbench ...`), author-script edits and lines, shot builds
and how many it took until the first successful one, wall time, own decisions recorded (decide note) and whether the run
ended on a question to the user. With --project, the outcome too: decision notes, fidelity detail failures and plain
declarations, photo IoU per view (latest version of each shot). Same metrics for every run, so a baseline and a
re-measure compare directly.

Usage:
  scripts/astra_run_metrics.py ROLLOUT.jsonl [...]            # metrics for the given rollouts
  scripts/astra_run_metrics.py --repo-runs [--since 2026-10-01] # every rollout whose cwd is this repository
  scripts/astra_run_metrics.py ROLLOUT.jsonl --project projects/harness_validation/engine_cutaway2
  scripts/astra_run_metrics.py LOG_DIR_OR_EVENTS.jsonl          # a launcher run (reel_agent.py --log-dir / codex exec --json):
                                                                # its thread id finds the main and every subagent rollout
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
QUESTION = re.compile(r'(\?|할까요|될까요|주세요|알려 ?주)\s*$')


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
    meta, start, end, last_message, tokens, context = {}, None, None, '', {}, {}
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
        elif event.get('type') == 'turn_context' and not context:
            context = {'model': payload.get('model'), 'effort': payload.get('effort')}
        elif kind == 'token_count' and (payload.get('info') or {}).get('total_token_usage'):
            tokens = payload['info']['total_token_usage']   # cumulative: the last one is the run's total
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
            'minutes': round((end - start).total_seconds() / 60, 1) if start else 0, **context,
            'tokens': {k: tokens.get(k, 0) for k in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')},
            'tool_calls': calls, 'commands': len(cmds), 'failed_commands': failed,
            'studio_ops': dict(ops.most_common()), 'error_codes': dict(codes.most_common()),
            'repeated_commands': dict(sorted(repeats.items(), key=lambda kv: -kv[1])[:10]),
            'workbench': dict(workbench), 'author_edits': author_edits, 'author_lines_added': author_lines,
            'shot_builds': len(builds), 'builds_until_first_ok': first_ok, 'decide_notes': ops.get('decide note', 0),
            'ended_on_question': bool(QUESTION.search(last_message.strip())), 'final_message': last_message[:600]}


def project_outcome(project):
    """What the run left in the project: its own decisions, detail failures / plain declarations and photo IoU of the
    latest version of each shot."""
    root = Path(project)
    log = root / 'decisions' / 'agent_log.jsonl'
    out = {'decision_notes': len([l for l in log.read_text().splitlines() if l.strip()]) if log.is_file() else 0, 'shots': {}}
    for shot in sorted((root / 'shots').glob('*/versions')):
        versions = sorted(p for p in shot.glob('v[0-9]*') if (p / 'fidelity_report.json').is_file())
        if not versions:
            continue
        report = json.loads((versions[-1] / 'fidelity_report.json').read_text())
        checks = [c for s in report.get('subjects', []) for c in s.get('checks', [])]
        out['shots'][shot.parent.name] = {
            'version': versions[-1].name, 'fidelity_passed': report.get('passed'),
            'detail_failed': sorted(c['id'] for c in checks if c['kind'] == 'detail' and c['passed'] is False),
            'plain': sorted(c['id'] for c in checks if c['kind'] == 'detail' and c['passed'] and str(c.get('note', '')).startswith('plain')),
            'photo_iou': {c['id']: c['measured'] for c in checks if c['kind'] == 'photo' and '.' not in c['id']}}
    return out


def launcher_thread(path):
    """The thread id of a launcher run: path is reel_agent.py's --log-dir or a `codex exec --json` events file (its first
    event is thread.started). None for anything else (a rollout file)."""
    path = Path(path)
    events = path / 'events.jsonl' if path.is_dir() else path
    try:
        first = json.loads(events.open(encoding='utf-8').readline())
    except (OSError, ValueError):
        return None
    return first.get('thread_id') if first.get('type') == 'thread.started' else None


def session_rollouts(thread_id):
    """Every rollout of one run: the main thread and its subagents (their session_meta.session_id is the main id)."""
    found = []
    for f in glob.glob(str(SESSIONS / '*' / '*' / '*' / 'rollout-*.jsonl')):
        try:
            with open(f, encoding='utf-8') as h:
                meta = json.loads(h.readline()).get('payload') or {}
        except (ValueError, OSError):
            continue
        if thread_id in (meta.get('id'), meta.get('session_id')):
            found.append((meta.get('id') != thread_id, f))   # the main thread first
    return [f for _, f in sorted(found)]


def run_group(path):
    """Metrics for one launcher run: each rollout (main first, then subagents) and their summed tokens."""
    thread_id = launcher_thread(path)
    rollouts = session_rollouts(thread_id)
    if not rollouts:
        raise SystemExit(f'no rollout under {SESSIONS} for thread {thread_id} ({path})')
    agents = [run_metrics(r) for r in rollouts]
    tokens = {k: sum(a['tokens'][k] for a in agents) for k in agents[0]['tokens']}
    return {'thread_id': thread_id, 'log': str(path), 'agents': len(agents), 'tokens_all_agents': tokens,
            'decide_notes_all_agents': sum(a['decide_notes'] for a in agents), 'main': agents[0],
            'subagents': [{k: a[k] for k in ('rollout', 'model', 'effort', 'minutes', 'tokens', 'tool_calls')} for a in agents[1:]]}


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
    parser.add_argument('--project', help='also report the outcome this run left in the project')
    parser.add_argument('--out', help='write the JSON here too')
    args = parser.parse_args()
    paths = args.rollouts + (repo_runs(args.since) if args.repo_runs else [])
    if not paths:
        parser.error('give rollout files or --repo-runs')
    groups = [run_group(p) for p in paths if launcher_thread(p)]
    runs = [run_metrics(p) for p in paths if not launcher_thread(p)] + [g['main'] for g in groups]
    total = collections.Counter()
    for r in runs:
        total.update(r['error_codes'])
    tokens = {k: sum(r['tokens'][k] for r in runs) for k in runs[0]['tokens']}
    result = {'runs': runs, 'summary': {'runs': len(runs), 'error_codes': dict(total.most_common()), 'tokens': tokens,
                                       'runs_using_workbench': sum(1 for r in runs if r['workbench']),
                                       'failed_commands': sum(r['failed_commands'] for r in runs),
                                       'decide_notes': sum(r['decide_notes'] for r in runs),
                                       'runs_ending_on_a_question': sum(1 for r in runs if r['ended_on_question'])}}
    if groups:
        result['launcher_runs'] = [{k: v for k, v in g.items() if k != 'main'} for g in groups]
    if args.project:
        result['outcome'] = project_outcome(args.project)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
