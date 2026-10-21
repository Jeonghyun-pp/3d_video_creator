"""Every wait observes what it waits for, records what it saw, and when it gives up says why and undoes what it started.

Why (remote jobs audit, 2026-10-21): a server that never booted was waited for 900 s with nothing recorded (its state
then unknowable: the server was gone), a clip wait gave up on a job it had submitted and left the job and its rented
server running, and the relay loop had no bound at all. One rule for every wait instead of a limit per call site: a new
wait written with wait_until records and explains itself without a line of its own.
"""
from __future__ import annotations

import json
import time

from .common import StudioError


def wait_until(observe, *, limit_s, stall_s=None, record=None, on_give_up=None, what='wait', code='WAIT_GAVE_UP',
               every_s=5.0, sleep=None, clock=None):
    """Call observe() until it reports done; return its value.

    observe() -> (done, seen): `done` is returned once truthy; `seen` is a JSON-able dict of what was observed (the
    evidence). Each new `seen` goes to record(row) with its time. Gives up after limit_s in all, or after stall_s with
    `seen` unchanged (no progress, whatever the total) - then on_give_up(reason) runs (undo what this wait started) and
    StudioError(code) says the reason with the last observation."""
    sleep, clock = sleep or time.sleep, clock or time.monotonic   # looked up per call (tests and callers replace them)
    start = changed = clock()
    last = None
    while True:
        done, seen = observe()
        now = clock()
        if seen != last:
            last, changed = seen, now
            if record:
                record({'at_s': round(now - start, 1), **(seen or {})})
        if done:
            return done
        if now - start > limit_s:
            reason = f'{what}: no result in {limit_s:.0f} s'
        elif stall_s is not None and now - changed > stall_s:
            reason = f'{what}: nothing changed for {stall_s:.0f} s'
        else:
            sleep(every_s)
            continue
        reason += f'; last seen: {json.dumps(last, default=str)[:600]}'
        if on_give_up:
            on_give_up(reason)
        raise StudioError(code, reason, retryable=True)
