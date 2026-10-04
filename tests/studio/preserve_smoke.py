"""No-render regression for preservation guards and immutable failed revisions."""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, file_hash, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot, revise_shot

AUTHOR = '''import bpy
from mathutils import Vector
bpy.ops.mesh.primitive_cube_add()
o=bpy.context.object; o.name='panel'; o['studio_id']='asset/panel'; o['studio_instance_id']='asset'; o['studio_part_id']='panel'
m=bpy.data.materials.new('Metal'); m.use_nodes=True; o.data.materials.append(m)
bpy.ops.object.camera_add(location=(5,-6,4)); c=bpy.context.object; c.rotation_euler=(Vector((0,0,0))-c.location).to_track_quat('-Z','Y').to_euler(); bpy.context.scene.camera=c
bpy.context.scene['studio_authored_animation']=True
'''

with tempfile.TemporaryDirectory(prefix='preserve-smoke-') as root:
    p=Path(init_project('preserve_test', {'request':'Preserve regression','shots':[{'shot_id':'shot_01','frame_count':30}]}, root)['project_path'])
    author=p/'author.py'; author.write_text(AUTHOR)
    built=build_shot(p,'shot_01',author)
    original=p/'shots/shot_01/versions/v0001/scene.blend'; original_hash=file_hash(original)
    current=read_json(shot_path(p,'shot_01'))
    change=p/'change.json'; script=p/'patch.py'
    write_json(change,{'base_revision':current['revision'],'scope':'camera','targets':[],'change':{},'preserve':['geometry','materials','asset/panel']})
    script.write_text("import bpy\nbpy.context.scene.camera.location.x += 1\n")
    good=revise_shot(p,'shot_01',change,script)
    assert good['preserve_report']['ok']
    assert read_json(shot_path(p,'shot_01'))['preserve']==['geometry','materials','asset/panel']
    version=good['scene_version']; prior=read_json(shot_path(p,'shot_01'))
    for label,code,guard in [
        ('geometry',"bpy.data.objects['panel'].data.vertices[0].co.x += 1",[]),
        ('material',"bpy.data.materials['Metal'].node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=.9",[]),
        ('object_pose',"bpy.data.objects['panel'].location.x += 1",[]),
        ('camera',"bpy.context.scene.camera.data.lens += 1",['camera']),
        ('missing_id',"pass",['asset/missing'])]:
        write_json(change,{'base_revision':prior['revision'],'scope':'scene','targets':[],'change':{},'preserve':prior['preserve']+guard})
        script.write_text('import bpy\n'+code+'\n')
        try:
            revise_shot(p,'shot_01',change,script)
        except StudioError as error:
            assert error.code=='PRESERVE_VIOLATION',(label,error.code,str(error))
        else:
            raise AssertionError('Unsafe patch was accepted: '+label)
        assert read_json(shot_path(p,'shot_01'))==prior
        assert file_hash(original)==original_hash
    print(json.dumps({'ok':True,'checks':['camera_edit_preserves_mesh_material_and_object','geometry_change_rejected','material_change_rejected','object_pose_change_rejected','camera_lens_change_rejected','missing_semantic_id_rejected','failed_revision_never_promoted']},indent=2))
