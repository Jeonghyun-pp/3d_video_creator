"""The author sandbox judges a compile by the code that asked for it: an author script running code from a string is
refused; library code compiling its own strings while the author imports it (collections.namedtuple) is not
(2026-10-09: importing a studio module from an author script was refused through namedtuple).
Runs inside Blender: blender -b --factory-startup --python tests/studio/sandbox_compile_smoke.py"""
import json
from pathlib import Path
import runpy
import sys
import tempfile

import bpy  # noqa: F401  (runs inside Blender, like the build)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'studio' / 'blender_ops'))
import sandbox  # noqa: E402

tmp = Path(tempfile.mkdtemp())
author = tmp / 'author.py'
author.write_text("import collections\n"
                  "Point = collections.namedtuple('Point', 'x y')   # library code compiles a string: not the author's\n"
                  "run = getattr(__builtins__, 'ex' + 'ec') if not isinstance(__builtins__, dict) else __builtins__['ex' + 'ec']\n"
                  "run('value = 1 + 1')                              # the author running a string: refused\n")
sandbox.install(stage='smoke', author_files=[author], write_roots=[tmp], mode='record')
runpy.run_path(str(author))
events = [v for v in sandbox._State.violations if v['event'] == 'compile']
assert len(events) == 1, events   # exactly the author's own string, not namedtuple's
print('SANDBOX_COMPILE_SMOKE ' + json.dumps({'ok': True, 'refused': events[0]['reason']}))
