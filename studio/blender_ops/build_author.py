"""Build stage 1 of 2: the author step, in its own Blender process.

Fresh builds start from the factory scene with its objects removed and build the declared layout (shot.scene); a
revision starts from the base version's authored checkpoint. Then the author script (or revision patch) runs, under the
sandbox (blender_ops/sandbox.py). This process hands exactly one thing on: authored.raw.blend. It never judges anything -
the replay, preserve and every later gate run in stage 2 (build_scene.py), a fresh process the author's code never
touched, on a job file the host re-hashes in between. Monkeypatching a gate, registering a handler or editing
STUDIO_JOB here changes nothing the build trusts.
"""
import copy
import json
import os
from pathlib import Path
import runpy
import sys

import bpy

sys.path.insert(0, str(Path(__file__).parent))

job_path = Path(sys.argv[sys.argv.index('--') + 1])
job = json.loads(job_path.read_text())
os.environ['STUDIO_JOB_PATH'] = str(job_path)
output = Path(job['output_dir'])
if job.get('base_version') is None:
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
# A declarative scene (shot.scene, resolved by studio/layout.py) is built first, on fresh builds only (a revision's
# checkpoint already holds it); an author script, when there is one, works on top of it.
if job.get('layout_path') and job.get('base_version') is None:
    import layout as declarative
    layout_report = declarative.build(job, json.loads(Path(job['layout_path']).read_text())['scene'])
    (output / 'layout_report.json').write_text(json.dumps(layout_report, indent=1))
import author_audit
(output / 'pre_author_state.json').write_text(json.dumps(author_audit.state(), indent=1))
if job.get('script_path'):
    import sandbox
    rules = job['sandbox']
    sandbox.install(stage='author', author_files=rules['author_files'], write_roots=rules['write_roots'], protected=rules['protected'],
                    allowed_imports=set(rules['allowed_imports']), mode='enforce', report=output / 'sandbox_report.json')
    runpy.run_path(job['script_path'], init_globals={'STUDIO_JOB': copy.deepcopy(job)}, run_name='__main__')
bpy.ops.file.pack_all()
# Data the author made but has not used yet (a cap material a reveal will assign) has no users and would not be saved:
# keep every such block in the checkpoint; stage 2 clears the flag again after saving it (checkpoint_fake.json).
unused = [(name, block.name) for name in dir(bpy.data) if isinstance(getattr(bpy.data, name, None), bpy.types.bpy_prop_collection)
          for block in getattr(bpy.data, name) if isinstance(block, bpy.types.ID) and block.users == 0 and not block.use_fake_user]
for name, block in unused:
    getattr(bpy.data, name)[block].use_fake_user = True
(output / 'checkpoint_fake.json').write_text(json.dumps(unused))
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'authored.raw.blend'), copy=True)
