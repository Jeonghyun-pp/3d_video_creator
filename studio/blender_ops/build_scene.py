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
# Author is a trusted local project script. External downloaded text is never executed here.
runpy.run_path(job['script_path'], init_globals={'STUDIO_JOB': job}, run_name='__main__')
scene = bpy.context.scene
if job.get('expect'):
    # Workbench commit: the replayed patch must reproduce what the session measured, or there is no version.
    from workbench_tools import compare, snapshot
    actual = snapshot(job['expect']['subjects'], job['expect']['objects'])
    issues = compare(job['expect'], actual)
    (output / 'replay_report.json').write_text(json.dumps({'ok': not issues, 'issues': issues[:200], 'actual': actual}, indent=2))
    if issues:
        raise ValueError('WORKBENCH_REPLAY_MISMATCH: ' + json.dumps(issues[:10]))
scene.frame_start = 1; scene.frame_end = job['shot']['duration_frames']
scene.render.fps = job['fps']; scene.render.fps_base = 1
if job.get('output_size'):
    # Build-time framing (camera rig, anchors, screen-size checks) must use the delivery aspect, as the renderer will.
    scene.render.resolution_x, scene.render.resolution_y = job['output_size']; scene.render.resolution_percentage = 100
if not scene.get('studio_authored_animation', False):
    apply_camera(job['shot']); apply_actions(job['shot'])
# A declared camera rig owns the camera even when the author animated everything else.
if job['shot']['camera'].get('rig'):
    from camera_rig import bake_camera_rig
    rig_report = bake_camera_rig(job)
    (output / 'camera_rig_report.json').write_text(json.dumps(rig_report, ensure_ascii=False, indent=2))
    if rig_report['gate_failures']:
        raise ValueError('CAMERA_RIG_GUARD_FAILED: ' + json.dumps(rig_report['gate_failures'][:10], ensure_ascii=False))
# Subject fidelity: raw measurements of spec-built subjects; the host judges them against the spec.
if job['shot'].get('subjects'):
    from fidelity import measure_subject
    measured = []
    for ref in job['shot']['subjects']:
        spec = json.loads(Path(job['subject_spec_paths'][ref['subject_id']]).read_text())
        measured.append(measure_subject(spec, job['shot'], job.get('output_size', (scene.render.resolution_x, scene.render.resolution_y))))
    (output / 'fidelity_geometry.json').write_text(json.dumps({'subjects': measured}))
scene.frame_set(1)
from look import apply_look
look_report = apply_look(job, scene)
(output / 'look_report.json').write_text(json.dumps(look_report, ensure_ascii=False, indent=2, default=str))
if look_report['gate_failures']:
    raise ValueError('LOOK_QA_FAILED: ' + json.dumps(look_report['gate_failures'][:10], ensure_ascii=False))
scene.frame_set(1)
if preserved is not None:
    try:
        report = compare(preserved, capture(constraints, preserved))
    except ValueError as error:
        report = {'ok': False, 'constraints': constraints, 'issues': [{'reason': str(error)}]}
    (output / 'preserve.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['ok']:
        raise ValueError('PRESERVE_VIOLATION: ' + json.dumps(report['issues'], ensure_ascii=False))
if not scene.camera:
    raise ValueError('Scene has no active camera')
# Packed textures make a version portable and prevent changing external files beneath renders.
bpy.ops.file.pack_all()
inventory = inspect()
if inventory['missing_files']:
    raise ValueError('Missing external assets: ' + str(inventory['missing_files']))
output = Path(job['output_dir'])
(output / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2))
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'scene.blend'))
