import json
import os
from pathlib import Path
import runpy
import sys
import bpy

sys.path.insert(0, str(Path(__file__).parent))
from scene_tools import inspect, apply_actions, apply_camera
from preserve import capture, compare

job_path = Path(sys.argv[sys.argv.index('--') + 1])
job = json.loads(job_path.read_text())
os.environ['STUDIO_JOB_PATH'] = str(job_path)
scene = bpy.context.scene
output = Path(job['output_dir'])
field_tokens = {'scene_version', 'actions', 'asset_instances', 'narration', 'labels', 'render', 'duration_frames'}
constraints = [token for token in job['shot'].get('preserve', []) if token not in field_tokens] if job.get('base_version') else []
try:
    preserved = capture(constraints) if constraints else None
except ValueError as error:
    (output / 'preserve.json').write_text(json.dumps({'ok': False, 'constraints': constraints, 'issues': [{'reason': str(error)}]}, ensure_ascii=False, indent=2))
    raise
if job.get('base_version') is None:
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
# A declarative scene (shot.scene, resolved by studio/layout.py) is built first, on fresh builds only (a revision's
# checkpoint already holds it); an author script, when there is one, works on top of it.
if job.get('layout_path') and job.get('base_version') is None:
    import layout as declarative
    layout_report = declarative.build(job, json.loads(Path(job['layout_path']).read_text())['scene'])
    (output / 'layout_report.json').write_text(json.dumps(layout_report, indent=1))
# Author is a trusted local project script. External downloaded text is never executed here.
if job.get('script_path'):
    runpy.run_path(job['script_path'], init_globals={'STUDIO_JOB': job}, run_name='__main__')
scene = bpy.context.scene
if job.get('expect'):
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
# Authored checkpoint: the scene as the author (and any revision patch) left it. Everything below - fill, camera move
# and rig, reveals, simulations, graphics, look - is generated from it and the shot, so a revision opens this file and
# re-runs the whole chain: a revision equals a fresh build with the same inputs, for every generator, present or future.
bpy.ops.file.pack_all()
# Data the author made but has not used yet (a cap material a reveal will assign) has no users and would not be saved:
# keep every such block in the checkpoint, without changing the scene this build goes on with.
unused = [block for name in dir(bpy.data) if isinstance(getattr(bpy.data, name, None), bpy.types.bpy_prop_collection)
          for block in getattr(bpy.data, name) if isinstance(block, bpy.types.ID) and block.users == 0 and not block.use_fake_user]
for block in unused:
    block.use_fake_user = True
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'authored.blend'), copy=True)
for block in unused:
    block.use_fake_user = False
scene['studio_gate_severity'] = json.dumps(job.get('gate_severity') or {})   # gate_policy.py: which taste gates only warn
# Fill brief: what the topic puts on the declared levels (studio/fill.py), placed before the camera is compiled so
# clearance and pass-through see it.
if job['shot'].get('fill_brief'):
    import fill_brief
    fill_brief.apply(job)
scene.frame_start = 1; scene.frame_end = job['shot']['duration_frames']
scene.render.fps = job['fps']; scene.render.fps_base = 1
if job.get('output_size'):
    # Build-time framing (camera rig, anchors, screen-size checks) must use the delivery aspect, as the renderer will.
    scene.render.resolution_x, scene.render.resolution_y = job['output_size']; scene.render.resolution_percentage = 100
if not scene.get('studio_authored_animation', False):
    apply_camera(job['shot']); apply_actions(job['shot'])
# Mechanisms: drive actions turn joint pivots by their couplings, baked per frame, before the camera measures the scene.
if any(a['type'] == 'drive' for a in job['shot']['actions']):
    import kinematics
    drive_rows = kinematics.apply_drives(job['shot'], job['fps'])
    (output / 'kinematics_report.json').write_text(json.dumps({'drives': drive_rows}, indent=1))
    clashes = [c for r in drive_rows for c in r['interference']]
    if clashes:
        raise ValueError('MECHANISM_INTERFERENCE: ' + json.dumps(clashes[:5]))
# A semantic move compiles into a rig here (path + timing from the style); the snapshot keeps the move.
# Reveals (and any action bound to camera cues) are applied once the camera's pass frames are known.
reveal_report = None
move_report = None
if job['shot']['camera'].get('move'):
    from camera_moves import compile_move
    import reveal

    def on_cues(cues):
        global reveal_report
        reveal_report = reveal.apply_camera_bound(job['shot'], cues)
    try:
        compiled, move_report = compile_move(job, job.get('motion_style'), on_cues=on_cues)
    except ValueError as error:
        (output / 'camera_move_report.json').write_text(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False, indent=2))
        raise
    (output / 'camera_move_report.json').write_text(json.dumps({'ok': True, **move_report, 'reveal': reveal_report}, ensure_ascii=False, indent=2))
    job['shot']['camera']['rig'] = compiled
elif any(a['type'] in ('reveal', 'simulate') for a in job['shot']['actions']):
    import reveal
    reveal.apply(job['shot'])
    if any(a['type'] == 'simulate' for a in job['shot']['actions']):
        import simulate
        (output / 'simulation_report.json').write_text(json.dumps({'simulations': simulate.apply(job['shot'])}, indent=2))
# The motion style's blur target reaches every shot that names a style (move or not); an explicit realism value wins.
if job.get('motion_style'):
    from camera_moves_core import realism_with_style
    job['shot']['camera']['realism'] = realism_with_style(job['shot']['camera'], job['motion_style'])
# A declared camera rig owns the camera even when the author animated everything else.
if job['shot']['camera'].get('rig'):
    from camera_rig import bake_camera_rig
    rig_report = bake_camera_rig(job)   # judged after the look below, on the scene that is saved
    (output / 'camera_rig_report.json').write_text(json.dumps(rig_report, ensure_ascii=False, indent=2))
# Explainer graphics (Grease Pencil, own render layer): built once the camera exists, hidden from every other pass.
if job['shot'].get('graphics'):
    import graphics
    cues = (move_report or {}).get('camera_cues') if job['shot']['camera'].get('move') else None
    (output / 'graphics_report.json').write_text(json.dumps({'graphics': graphics.build(job['shot'], cues)}, indent=2))
scene.frame_set(1)
if move_report:   # exposure keys may name camera cues
    job['camera_cues'] = move_report.get('camera_cues')
from look import apply_look
look_report = apply_look(job, scene)
(output / 'look_report.json').write_text(json.dumps(look_report, ensure_ascii=False, indent=2, default=str))
if look_report['gate_failures']:
    raise ValueError('LOOK_QA_FAILED: ' + json.dumps(look_report['gate_failures'][:10], ensure_ascii=False))
# Screen-space graphics: after the look, which may key the lens shift (two-point) that places them in the frame.
if any(g.get('space') == 'screen' for g in job['shot'].get('graphics') or []):
    import graphics
    cues = (move_report or {}).get('camera_cues') if job['shot']['camera'].get('move') else None
    world = json.loads((output / 'graphics_report.json').read_text())['graphics'] if (output / 'graphics_report.json').is_file() else []
    (output / 'graphics_report.json').write_text(json.dumps({'graphics': world + graphics.build_screen(job['shot'], cues)}, indent=2))
# Gates on the finished scene: the look may key a lens shift (two-point), add shake and move parts (perfection), so the
# framing guards, the fill gate and the subject measurements all read the scene that is saved, not the one before.
if job['shot']['camera'].get('rig'):
    from camera_rig import verify_after_look
    rig_report = verify_after_look(job, rig_report)
    (output / 'camera_rig_report.json').write_text(json.dumps(rig_report, ensure_ascii=False, indent=2))
    if rig_report['gate_failures']:
        raise ValueError('CAMERA_RIG_GUARD_FAILED: ' + json.dumps(rig_report['gate_failures'][:10], ensure_ascii=False))
if job['shot'].get('fill_brief'):   # seen levels carry their subject or identity, the subject is not hidden, nothing off-brief
    import fill_brief
    fill_brief.check(job, output)
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
# Spec-built subjects and when they are on screen: the generation prompt maps clay shapes to them (no shot.subjects needed).
from subject_index import subjects_on_screen
(output / 'subjects_index.json').write_text(json.dumps({'schema_version': 1, 'subjects': subjects_on_screen(scene)}, ensure_ascii=False, indent=1))
if scene.get('studio_environment'):   # environment kits used by the author (env_kits.street): counts, seeds, digests
    reports = json.loads(scene['studio_environment'])
    (output / 'environment_report.json').write_text(json.dumps({'schema_version': 1, 'streets': [r for r in reports if r.get('kind', 'street') == 'street'],
                                                                **({'fill': [r for r in reports if r.get('kind') == 'fill']} if any(r.get('kind') == 'fill' for r in reports) else {})}, indent=1))
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'scene.blend'))
