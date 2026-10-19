"""gpu status through the sealed MCP (studio/broker.py HERE, 2026-10-19): the broker lists the rented-GPU servers in its
own process, outside the sandbox that denies a brokered command the network. One real GET to RunPod (free); skipped
without a key. Needs a seal (studio freeze record).
Run: .venv/bin/python tests/studio/gpu_status_broker_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import remote_gpu, seal
from studio.common import StudioError

try:
    remote_gpu.api_key()
except StudioError:
    print('STUDIO_GPU_STATUS_BROKER_SMOKE ' + json.dumps({'ok': True, 'skipped': 'no RunPod key'}))
    sys.exit(0)
engine = seal.current()
assert engine is not None, 'no sealed engine: studio freeze record --user-words "..." first'
proc = subprocess.Popen(seal.launch_argv('mcp', engine=engine), cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
try:
    for n, (method, params) in enumerate([('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'smoke', 'version': '1'}}),
                                          ('tools/call', {'name': 'studio_run', 'arguments': {'args': ['gpu', 'status'], 'timeout_s': 120}})], 1):
        proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': n, 'method': method, 'params': params}) + '\n'); proc.stdin.flush()
        reply = json.loads(proc.stdout.readline())
    out = json.loads(reply['result']['content'][0]['text'])
finally:
    proc.stdin.close(); proc.wait(timeout=30)
assert out.get('ok') and out.get('checked_by') == 'broker' and isinstance(out.get('running'), list), out
assert remote_gpu.api_key() not in json.dumps(out)
print('STUDIO_GPU_STATUS_BROKER_SMOKE ' + json.dumps({'ok': True, 'running': len(out['running']), 'spent': out['spent_this_month_usd']}))
