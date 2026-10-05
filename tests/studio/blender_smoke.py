"""Opt-in real Blender render/revision/recovery smoke. No network or API use.
Run: .venv/bin/python tests/studio/blender_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import file_hash, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot
from studio.jobs import submit_render, job_status, cancel_job, resume_job, find_job, verify_render

AUTHOR = '''import bpy
from mathutils import Vector
from pathlib import Path
import json,sys
job=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
bpy.ops.mesh.primitive_cube_add(size=1)
cube=bpy.context.object; cube.name='panel'; cube['studio_id']='test/panel'; cube['studio_part_id']='panel'; cube['studio_instance_id']='test'; cube['studio_explode_vector']=[0,0,1]
bpy.ops.object.camera_add(location=(4,-6,4)); camera=bpy.context.object
camera.rotation_euler=(Vector((0,0,0))-camera.location).to_track_quat('-Z','Y').to_euler(); bpy.context.scene.camera=camera
bpy.ops.object.light_add(type='AREA',location=(2,-3,5)); bpy.context.object.data.energy=400
bpy.context.scene.render.engine='CYCLES'
'''


def wait(project, job, limit=90):
    deadline=time.monotonic()+limit
    while time.monotonic()<deadline:
        result=job_status(project,job)
        if result['status'] not in ('queued','running'):
            assert result['status']=='complete',result
            return result
        time.sleep(.25)
    raise AssertionError('Worker deadline exceeded')


def main():
    # Keep all author scripts under their project so the trusted-script boundary is exercised.
    with tempfile.TemporaryDirectory(prefix='studio-smoke-') as root:
        result=init_project('smoke',{'request':'Integration smoke','shots':[{'shot_id':'shot_01','frame_count':6}]},root)
        project=Path(result['project_path']); script=project/'author.py'; script.write_text(AUTHOR)
        path=shot_path(project,'shot_01'); shot=read_json(path)
        shot['render'].update(engine='CYCLES',samples=1)
        shot['actions']=[{'action_id':'open','type':'peel','targets':[{'instance_id':'test','part_id':'panel'}],
                          'start_frame':0,'end_frame':6,'easing':'linear','params':{'direction_source':'asset','distance_m':.5,'order':'asset_order'}}]
        write_json(path,shot)
        built=build_shot(project,'shot_01',script)
        version=project/'shots/shot_01/versions/v0001/scene.blend'; digest=file_hash(version)
        empty=project/'patch.py'; empty.write_text('# Retain geometry; reapply saved semantic motion.\n')
        revised=build_shot(project,'shot_01',empty,'v0001')
        assert revised['scene_version']=='v0002' and file_hash(version)==digest
        inv=read_json(project/'shots/shot_01/versions/v0002/inventory.json')
        panel=next(o for o in inv['objects'] if o['name']=='panel')
        assert abs(panel['location'][2])<1e-6, panel
        job=submit_render(project,'shot_01','v0002','layout')
        completed=wait(project,job['job_id'])
        data=read_json(find_job(project,job['job_id'])); assert verify_render(data)
        frames=Path(data['output_dir'])/'frames'
        original=file_hash(frames/'frame_000000.png'); (frames/'frame_000005.png').unlink()
        resume_job(project,job['job_id']); wait(project,job['job_id'])
        assert file_hash(frames/'frame_000000.png')==original
        manifest=read_json(Path(data['output_dir'])/'render.json'); assert manifest['rendered_frames']==1,manifest
        assert submit_render(project,'shot_01','v0002','layout')['cache_hit'] is True
        # Cancel before GPU acquisition or during render, then resume the same immutable job.
        pending=submit_render(project,'shot_01','v0002','look',frames=[0,5])
        cancel_job(project,pending['job_id'])
        assert job_status(project,pending['job_id'])['status']=='cancelled'
        # Wait for process group shutdown; resume must not start a second active writer.
        time.sleep(.5)
        resume_job(project,pending['job_id']); wait(project,pending['job_id'])
        print(json.dumps({'ok':True,'checks':['immutable_revision','idempotent_motion','real_6_frame_render','partial_frame_recovery','cache_hit','cancel_resume']},indent=2))


if __name__=='__main__':
    main()
