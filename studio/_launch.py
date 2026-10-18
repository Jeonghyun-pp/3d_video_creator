"""Start the studio from this engine copy, isolated from the checkout it serves (studio/seal.py).

Run as `python -I -B -X pycache_prefix=<dir> <engine>/studio/_launch.py <what> [args...]`: -I ignores the working
folder, PYTHON* variables and the user's site folder, so no file planted beside the checkout (a json.py) shadows a module;
-B with pycache_prefix reads and writes no __pycache__ the agent could plant a compiled file in. This file appends its
engine's root after the standard library, names the data root (STUDIO_DATA_ROOT: the caller's, else the seal's
manifest, else the engine itself) and runs <what>:
  studio <args>      the CLI (python -m studio <args>)
  mcp                the MCP server (studio.workbench_mcp)
  reel_agent <args>  scripts/reel_agent.py of this engine
"""
import json
import os
from pathlib import Path
import runpy
import sys

ENGINE = Path(__file__).resolve().parents[1]


def main():
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        sys.stderr.write('studio/_launch.py runs only as python -I -B (studio/seal.launch_argv)\n')
        return 2
    sys.path.append(str(ENGINE))
    if not os.environ.get('STUDIO_DATA_ROOT'):
        manifest = ENGINE / 'seal.json'
        os.environ['STUDIO_DATA_ROOT'] = json.loads(manifest.read_text())['data_root'] if manifest.is_file() else str(ENGINE)
    what, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ('', [])
    if what == 'studio':
        from studio.__main__ import main as studio_main
        return studio_main(args)
    if what == 'mcp':
        os.chdir(os.environ['STUDIO_DATA_ROOT'])
        sys.argv = ['studio.workbench_mcp']
        runpy.run_module('studio.workbench_mcp', run_name='__main__')
        return 0
    if what == 'reel_agent':
        sys.argv = [str(ENGINE / 'scripts' / 'reel_agent.py'), *args]
        runpy.run_path(str(ENGINE / 'scripts' / 'reel_agent.py'), run_name='__main__')
        return 0
    sys.stderr.write(f'studio/_launch.py: run what? (studio | mcp | reel_agent), not {what!r}\n')
    return 2


if __name__ == '__main__':
    sys.exit(main())
