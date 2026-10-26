"""v2 end to end through its CLI where no model may start (STUDIO_ASK=1, as inside an agent sandbox): every picture and
every reading is asked of the agent (v1's outside-model protocol) and answered here - pictures drawn locally, readings
shaped by each request's own schema - then: cuts settled, pictures made, checked and picked, marks located, clips drawn,
the preview reel cut by v1's edit with its scratch voice, and v1's reel see and reel judge run on it. Nothing paid,
nothing networked: it proves the plumbing, not the pictures.

Run: .venv/bin/python tests/studio/v2_preview_smoke.py"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PY = sys.executable


def run(module, *args, env):
    done = subprocess.run([PY, '-m', module, *args], cwd=ROOT, capture_output=True, text=True, env=env, timeout=900)
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SystemExit(f'{module} {args[:3]}: no JSON result: {done.stdout[-500:]} {done.stderr[-800:]}')


def fill(schema, n=0):
    """A minimal value of a JSON schema: the n-th enum value, true, numbers in range, arrays of their minItems."""
    if 'anyOf' in schema:
        return fill(next((s for s in schema['anyOf'] if s.get('type') != 'null'), schema['anyOf'][0]), n)
    if 'enum' in schema:
        return schema['enum'][n % len(schema['enum'])]
    kind = schema.get('type')
    kind = next((k for k in kind if k != 'null'), 'null') if isinstance(kind, list) else kind
    if kind == 'object':
        return {k: fill(v, n) for k, v in schema.get('properties', {}).items() if k in schema.get('required', [])}
    if kind == 'array':
        return [fill(schema.get('items', {}), i) for i in range(max(schema.get('minItems', 0), 0))]
    if kind == 'boolean':
        return True
    if kind == 'integer':
        return max(schema.get('minimum', 0), min(schema.get('maximum', n), n))
    if kind == 'number':
        low, high = schema.get('minimum', 0), schema.get('maximum', 1)
        return round(low + (high - low) * (0.3 + 0.1 * (n % 5)), 3)
    if kind == 'string':
        return 'a smoke answer'
    return None


def answer_all(error, root, env):
    from PIL import Image
    for request in error['affected_ids']:
        asked = json.loads(Path(request).read_text())
        if asked['kind'] == 'paint':
            image = root / f"paint_{abs(hash(request)) % 10 ** 8}.png"
            Image.new('RGB', (94, 167), (60 + len(asked['prompt']) % 150, 120, 170)).save(image)
            given = str(image)
        else:
            given = json.dumps(fill(asked['schema']))
        got = run('studio', 'ask', 'answer', '--request', request, '--answer', given, '--model', 'smoke-reader', '--by', 'the v2 smoke', env=env)
        assert got['ok'], got


def until_done(root, env, module, *args, rounds=8):
    for _ in range(rounds):
        got = run(module, *args, env=env)
        if got['ok']:
            return got
        if (got.get('error') or {}).get('code') != 'OUTSIDE_MODEL_ASKED':
            raise SystemExit(f'{module} {args[:3]}: {got.get("error")}')
        answer_all(got['error'], root, env)
    raise SystemExit(f'{module} {args[:3]}: still asking after {rounds} rounds')


def main():
    from studio.common import read_json, write_json
    from tests import studio2_fixture as fx
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = {**os.environ, 'STUDIO_ASK': '1'}
        path = fx.project(root, 'v2smoke')
        body = fx.body()
        body['cuts'][1]['duration_s'] = 5.0
        body['cuts'][0]['duration_s'] = 5.5
        file = fx.write_body(root, body)
        got = run('studio2', 'cuts', 'write', '--project', str(path), '--body', str(file), '--agent-note', 'the v2 smoke plans two cuts', env=env)
        assert got['ok'] and got['status'] == 'decided', got
        for cut in ('c1', 'c2'):
            until_done(root, env, 'studio2', 'frames', 'make', '--project', str(path), '--cut', cut)
            until_done(root, env, 'studio2', 'checks', 'run', '--project', str(path), '--cut', cut)
            got = run('studio2', 'frames', 'pick', '--project', str(path), '--cut', cut, '--frame', 'first', '--try', '1',
                      '--why', 'the smoke picks the only try it drew', env=env)
            assert got['ok'], got
        until_done(root, env, 'studio2', 'frames', 'make', '--project', str(path), '--cut', 'c2')   # its last: an edit of the picked first
        until_done(root, env, 'studio2', 'checks', 'run', '--project', str(path), '--cut', 'c2', '--frame', 'last')
        assert run('studio2', 'frames', 'pick', '--project', str(path), '--cut', 'c2', '--frame', 'last', '--try', '1',
                   '--why', 'the smoke picks the only try it drew', env=env)['ok']
        for cut in ('c1', 'c2'):
            until_done(root, env, 'studio2', 'locate', '--project', str(path), '--cut', cut)
            got = run('studio2', 'motion', 'render', '--project', str(path), '--cut', cut, env=env)
            assert got['ok'], got
        got = run('studio2', 'reel', 'preview', '--project', str(path), env=env)
        assert got['ok'] and Path(got['reel']).is_file(), got
        seen = until_done(root, env, 'studio2', 'reel', 'see', '--project', str(path))
        assert seen['cuts'] == ['c1', 'c2'], seen
        media = [r['medium'] for r in read_json(Path(path, 'review', 'clay', 'clay.json'))['shots']]
        assert media == ['pictures', 'pictures'], media   # the readers are told the cuts are pictures, not clay
        judged = until_done(root, env, 'studio2', 'reel', 'judge', '--project', str(path))
        assert judged['cuts'] == ['c1', 'c2'], judged
        timing = run('studio2', 'timings', '--project', str(path), env=env)
        assert any(k.startswith('v2.') for k in timing['by_command']), timing
        print(json.dumps({'ok': True, 'reel': got['reel'], 'commands': sorted(timing['by_command'])}))


if __name__ == '__main__':
    main()
