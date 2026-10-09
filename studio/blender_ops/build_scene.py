"""Build stage 2 of 2: the trusted build. Stage 1 (build_author.py) ran the author in its own process and handed on
authored.raw.blend; this process opens it (after capturing the base for preserve on revisions), judges what the author
did (replay, preserve, author_audit), saves the authored checkpoint, and generates everything else from it and the shot.
The job is read from disk here - the host re-hashed it after stage 1 - so nothing the author did to its copy reaches it.
"""
import json
import os
from pathlib import Path
import sys
import bpy

sys.dont_write_bytecode = True   # a build writes only into its output folder: no __pycache__ beside engine, project or contrib code
sys.path.insert(0, str(Path(__file__).parent))
from scene_tools import inspect
from preserve import capture, compare

job_path = Path(sys.argv[sys.argv.index('--') + 1])
job = json.loads(job_path.read_text())
os.environ['STUDIO_JOB_PATH'] = str(job_path)
output = Path(job['output_dir'])
field_tokens = {'scene_version', 'actions', 'asset_instances', 'narration', 'labels', 'render', 'duration_frames'}
constraints = [token for token in job['shot'].get('preserve', []) if token not in field_tokens] if job.get('base_version') else []
try:
    preserved = capture(constraints) if constraints else None   # on the base checkpoint this process opened
except ValueError as error:
    (output / 'preserve.json').write_text(json.dumps({'ok': False, 'constraints': constraints, 'issues': [{'reason': str(error)}]}, ensure_ascii=False, indent=2))
    raise
raw = output / 'authored.raw.blend'
if Path(bpy.data.filepath).resolve() != raw.resolve():
    bpy.ops.wm.open_mainfile(filepath=str(raw), load_ui=False)
scene = bpy.context.scene
import sandbox   # the rig's procedural script runs in this stage (camera_rig._procedural): judged, recorded first
rules = job.get('sandbox') or {}
if rules.get('rig_files'):
    sandbox.install(stage='engine', author_files=rules['rig_files'], write_roots=rules['write_roots'], protected=rules['protected'],
                    allowed_imports=set(rules['rig_allowed_imports']), mode=rules.get('engine_mode', 'record'), report=output / 'sandbox_engine_report.json')
if (job.get('expect') or {}).get('subjects') is not None:
    # Workbench commit: the replayed patch must reproduce what the session measured, or there is no version.
    import workbench_tools   # a module name, not `compare`: the preserve check below uses preserve.compare
    actual = workbench_tools.snapshot(job['expect']['subjects'], job['expect']['objects'])
    issues = workbench_tools.compare(job['expect'], actual)
    (output / 'replay_report.json').write_text(json.dumps({'ok': not issues, 'issues': issues[:200], 'actual': actual}, indent=2))
    if issues:
        raise ValueError('WORKBENCH_REPLAY_MISMATCH: ' + json.dumps(issues[:10]))
# Preserve judges what this revision's author/patch changed, before the generators below re-derive their output.
if preserved is not None:
    bpy.context.view_layer.update()   # the patch's data edits reach the evaluated scene the capture reads
    try:
        report = compare(preserved, capture(constraints, preserved))
    except ValueError as error:
        report = {'ok': False, 'constraints': constraints, 'issues': [{'reason': str(error)}]}
    (output / 'preserve.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['ok']:
        raise ValueError('PRESERVE_VIOLATION: ' + json.dumps(report['issues'], ensure_ascii=False))
# What the author step changed: recorded, and 3D text / missing linked files refused (author_audit.py).
import author_audit
audit_errors, _ = author_audit.audit(job, output)
if audit_errors:
    raise ValueError('AUTHOR_AUDIT_FAILED: ' + json.dumps(audit_errors))
# Linked data becomes local and packed: a checkpoint and its versions never depend on a file outside them.
if bpy.data.libraries:
    bpy.ops.object.make_local(type='ALL')
    for library in list(bpy.data.libraries):
        if not library.users_id:
            bpy.data.libraries.remove(library)
# Authored checkpoint: the scene as the author (and any revision patch) left it. Everything below - fill, camera move
# and rig, reveals, simulations, graphics, look - is generated from it and the shot, so a revision opens this file and
# re-runs the whole chain: a revision equals a fresh build with the same inputs, for every generator, present or future.
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'authored.blend'), copy=True)
# Blocks stage 1 kept with a fake user (made but not used yet) go back to normal for the scene this build goes on with.
for collection, name in json.loads((output / 'checkpoint_fake.json').read_text()):
    block = getattr(bpy.data, collection).get(name)
    if block is not None:
        block.use_fake_user = False
# Everything generated from the authored scene and the shot (the workbench runs the same function on its session).
import generate
generate.generate(job, output)
if (job.get('expect') or {}).get('after'):
    # A session that edited the shot ran this same chain: the build must end where the session ended (camera at sample
    # frames, subjects and touched objects), or the agent previewed something no version has.
    import workbench_tools
    after = job['expect']['after']
    actual = workbench_tools.generated_snapshot([int(f) for f in after['camera']], after['subjects'], after['objects'])
    issues = workbench_tools.compare(after, actual)
    (output / 'replay_report.json').write_text(json.dumps({'ok': not issues, 'stage': 'after generators', 'issues': issues[:200], 'actual': actual}, indent=2))
    if issues:
        raise ValueError('WORKBENCH_REPLAY_MISMATCH: ' + json.dumps(issues[:10]))
if job['shot'].get('subjects'):     # raw measurements of spec-built subjects; the host judges them against the spec
    from fidelity import measure_subject
    measured = []
    for ref in job['shot']['subjects']:
        spec = json.loads(Path(job['subject_spec_paths'][ref['subject_id']]).read_text())
        measured.append(measure_subject(spec, job['shot'], job.get('output_size', (scene.render.resolution_x, scene.render.resolution_y))))
    (output / 'fidelity_geometry.json').write_text(json.dumps({'subjects': measured}))
scene.frame_set(1)
if not scene.camera:
    raise ValueError('Scene has no active camera')
# Render workers render any frame: a simulation must be baked into this file, never live or on disk.
import simulate
unbaked = simulate.check_baked(scene)
if unbaked:
    raise ValueError('SIMULATION_NOT_BAKED: ' + '; '.join(unbaked))
# Packed textures make a version portable and prevent changing external files beneath renders.
bpy.ops.file.pack_all()
inventory = inspect()
if inventory['missing_files']:
    raise ValueError('Missing external assets: ' + str(inventory['missing_files']))
output = Path(job['output_dir'])
(output / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2))
# Objects declared larger or smaller than life (look_scale.display_scale): the host asks for an on-screen disclosure.
(output / 'display_scale.json').write_text(json.dumps([{'id': str(o.get('studio_id', o.name)), 'factor': o['studio_display_scale'],
                                                        'reason': str(o.get('studio_display_reason', ''))}
                                                       for o in sorted(scene.objects, key=lambda x: x.name) if 'studio_display_scale' in o.keys()],
                                                      ensure_ascii=False, indent=1))
# Spec-built subjects and when they are on screen: the generation prompt maps clay shapes to them (no shot.subjects needed).
from subject_index import subjects_on_screen
(output / 'subjects_index.json').write_text(json.dumps({'schema_version': 1, 'subjects': subjects_on_screen(scene)}, ensure_ascii=False, indent=1))
if scene.get('studio_environment'):   # environment kits used by the author (env_kits.street): counts, seeds, digests
    reports = json.loads(scene['studio_environment'])
    (output / 'environment_report.json').write_text(json.dumps({'schema_version': 1, 'streets': [r for r in reports if r.get('kind', 'street') == 'street'],
                                                                **({'fill': [r for r in reports if r.get('kind') == 'fill']} if any(r.get('kind') == 'fill' for r in reports) else {})}, indent=1))
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'scene.blend'))
raw.unlink()
# The picture, judged the same way whatever made it (author script, declared scene, workbench edit). After the save:
# what the probe changes (colours, render settings) never reaches scene.blend.
import frame_probe
frame_probe.probe(job, output)
