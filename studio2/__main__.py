"""python -m studio2 - v2's commands, run the way v1 runs its own: one JSON result, the same exit codes, and one row per
command in the project's runs/timings.jsonl (studio timings reads them; v2's are named v2.<command>)."""
from __future__ import annotations

import argparse
import importlib

MODULES = ('project', 'images', 'grammar', 'cuts', 'frames', 'checks', 'locate', 'motion', 'reel')


def parser():
    top = argparse.ArgumentParser(prog='python -m studio2', description='v2: cutaway explainer reels made from generated pictures. '
                                                                       'All results are JSON (as python -m studio).')
    subparsers = top.add_subparsers(dest='command', required=True)
    for name in MODULES:
        try:
            module = importlib.import_module('studio2.' + name)
        except ModuleNotFoundError as exc:
            if exc.name == 'studio2.' + name:   # a phase not built yet
                continue
            raise
        module.register_commands(subparsers)
    from studio import timings   # where the time went, v1 and v2 rows alike
    timings.register_commands(subparsers)
    return top


def main(argv=None):
    from studio.__main__ import _run
    from studio.timings import command as timed_command
    args = parser().parse_args(argv)
    operation = '.'.join([args.command] + [getattr(args, k) for k in vars(args) if k.endswith('_command') and getattr(args, k)])
    operation = operation if operation == 'timings' else 'v2.' + operation
    timing, code = timed_command(args, operation), 3
    try:
        code = _run(args, operation)
        return code
    finally:
        timing.end(code == 0)


if __name__ == '__main__':
    raise SystemExit(main())
