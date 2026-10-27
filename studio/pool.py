"""Independent work at the same time: a bounded pool of threads for steps that wait on their own processes (a shot's
Blender builds and renders, a model call). Only work that is truly independent goes in - each item writes its own files
(a shot's versions, sheet and envelope; a model's own answer file) - and anything they share is taken in turn under
the lock that guards it (common.lock: threads of one command wait their turn; another process still finds it held).
Results come back in the order asked, so a run at once writes what the same run one by one writes.

How many at once is read from the machine: half its cores (Blender threads itself), no more than free memory holds at
GB_PER_WORKER each, and no more than MOST, the best measured on the reference Mac (STUDIO_WORKERS sets it).
Why (2026-10-26): an 8-cut clay reel took 2-3 hours with every shot's takes and every model call one after another."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
import time
from pathlib import Path
import re
import subprocess
from typing import Any

ENV = 'STUDIO_WORKERS'
GB_PER_WORKER = 2.0   # a clay Blender process with its scene and its render, with room to spare
MOST = 4              # measured 2026-10-26 on an M4 (10 cores, 16 GB): takes of 4 shots at once beat 2 at once; the
                      # layout renders still run one at a time (the render worker's GPU lock)
READERS_ENV = 'STUDIO_READERS'
READERS_MOST = 3      # model readers (screen_grammar.codex_json) at once on the machine, whatever asks: 2026-10-26, five
                      # codex readers started together failed and stalled; two at a time ran. 2026-10-28, the same 9 locate
                      # reads asked 2 vs 3 at a time (2 rounds each, then 45 more at 3): at 3 each read took 16.2 s against
                      # 13.3 s (median) but a batch ended in 50 s against 73 s, and 63 reads at 3 had no stall or failure
                      # (slowest 27 s, inside locate's 129 s limit). STUDIO_READERS sets it


@dataclass
class Done:
    item: Any
    value: Any = None
    error: BaseException | None = None


def free_memory_gb():
    """Memory the machine can give now, in GB - macOS: the kernel's free share (kern.memorystatus_level, what
    memory_pressure reports: reclaimable pages count) of hw.memsize; Linux: MemAvailable - else None."""
    try:
        meminfo = Path('/proc/meminfo')
        if meminfo.is_file():
            return int(re.search(r'MemAvailable:\s+(\d+) kB', meminfo.read_text()).group(1)) * 1024 / 1e9
        out = subprocess.run(['sysctl', '-n', 'kern.memorystatus_level', 'hw.memsize'], capture_output=True, text=True, timeout=5).stdout.split()
        return int(out[0]) / 100 * int(out[1]) / 1e9
    except Exception:  # noqa: BLE001 - unknown memory: the cores alone bound the pool
        return None


def limit():
    """How many at once: the setting (STUDIO_WORKERS), else what the machine holds now (module docstring)."""
    try:
        set_to = int(os.environ.get(ENV) or 0)
    except ValueError:
        set_to = 0
    if set_to >= 1:
        return set_to
    cores = max(1, (os.cpu_count() or 2) // 2)
    memory = free_memory_gb()
    by_memory = int(memory // GB_PER_WORKER) if memory is not None else cores
    return max(1, min(MOST, cores, by_memory))


def readers():
    """How many model readers at once on this machine (READERS_MOST; STUDIO_READERS sets it)."""
    try:
        set_to = int(os.environ.get(READERS_ENV) or 0)
    except ValueError:
        set_to = 0
    return set_to if set_to >= 1 else READERS_MOST


@contextmanager
def slot(name, count, folder=None, wait_s=3600.0, poll_s=1.0):
    """One of `count` slots named `name` on this machine, held while the block runs: threads of one command and other
    processes alike (a file lock per slot, each taking its own open file, so two threads of one process exclude each
    other too). Waits for a free one up to wait_s, then BUSY. The slots live with the user whose model runs (~/.config/studio/
    slots: codex runs from that home, whichever checkout asks; a run under another HOME, a test's, has its own)."""
    from .common import StudioError
    folder = Path(folder or Path.home() / '.config' / 'studio' / 'slots')
    folder.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    while True:
        for k in range(count):
            stream = (folder / f'{name}_{k}.lock').open('a')
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stream.close()
                continue
            try:
                yield k
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
                stream.close()
            return
        if time.monotonic() > deadline:
            raise StudioError('BUSY', f'all {count} {name} slots stayed held for {wait_s:.0f} s', retryable=True)
        time.sleep(poll_s)


def each(items, step, workers=None):
    """[Done(item, value | error)] of step(item) for every item, in the order given, at most `workers` (default: limit())
    at once. An item that fails is its Done's error; the others go on."""
    items = list(items)

    def one(item):
        try:
            return Done(item, step(item))
        except Exception as exc:  # noqa: BLE001 - said on its own row, never lost
            return Done(item, error=exc)
    size = min(len(items), workers or limit())
    if size <= 1:
        return [one(i) for i in items]
    with ThreadPoolExecutor(max_workers=size, thread_name_prefix='studio-pool') as running:
        return list(running.map(one, items))


def each_shot(shot_ids, step):
    """step(shot_id) for several shots at once, as one command's answer: {ok, shots, results (in the order asked),
    failed: [{shot_id, error}], each shot's warnings (prefixed by it), artifacts and next}. Each shot's Blender
    processes and model calls are its own rows in studio timings. A tool bug in one shot is the command's to raise."""
    from .common import StudioError
    from .timings import for_shot

    def one(shot_id):
        with for_shot(shot_id):
            return step(shot_id)
    workers = max(1, min(len(shot_ids), limit()))
    done = each(shot_ids, one, workers)
    for d in done:
        if d.error is not None and not isinstance(d.error, (StudioError, OSError, ValueError)):
            raise d.error
    results = [{'shot_id': d.item, **(d.value or {})} for d in done if d.error is None]
    failed = [{'shot_id': d.item, 'error': d.error.as_dict() if isinstance(d.error, StudioError) else
               {'code': 'INPUT_INVALID', 'message': str(d.error), 'retryable': False, 'affected_ids': [], 'recovery': ''}}
              for d in done if d.error is not None]
    return {'ok': not failed, 'shots': list(shot_ids), 'workers': workers, 'results': results, 'failed': failed,
            'warnings': [f"{r['shot_id']}: {w}" for r in results for w in r.get('warnings') or []]
            + [f"{f['shot_id']}: {f['error']['code']}: {f['error']['message'][:300]}" for f in failed],
            'artifacts': [a for r in results for a in r.get('artifacts') or []],
            'next': [r['next'] for r in results if r.get('next')]}
