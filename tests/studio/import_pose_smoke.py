"""Blender-only regression: appended source poses and parent transforms survive import."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
from assets import import_prepared_asset

with tempfile.TemporaryDirectory(prefix='studio-import-pose-') as temporary:
    p=Path(temporary)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(location=(2,3,4))
    root=bpy.context.object;root['studio_id']='source/root';root.rotation_euler=(.1,.2,.3)
    bpy.ops.mesh.primitive_cube_add()
    child=bpy.context.object;child['studio_id']='source/child';child.parent=root
    child.matrix_parent_inverse=Matrix.Translation((.2,-.1,.3))
    child.location=(.5,1,2);child.scale=(.5,1,1.5)
    bpy.context.view_layer.update()
    expected={o['studio_id']:o.matrix_world.copy() for o in bpy.context.scene.objects}
    source=p/'source.blend';bpy.ops.wm.save_as_mainfile(filepath=str(source))
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    manifest=p/'asset.json'
    manifest.write_text(json.dumps({'asset_id':'pose_fixture','version':'v0001','status':'prepared',
        'prepared_scene':str(source),'prepared_scene_sha256':source_hash,
        'parts':[{'part_id':'assembly','kind':'leaf','root_object_id':'source/root',
                  'object_ids':['source/root','source/child'],'explode_vector':[0,0,1]}],'anchors':[]}))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    checks=[]
    for instance,transform in [('identity',{}),('transformed',{'location':[2,-1,.5],
        'rotation_euler':[.1,.2,.3],'scale':[1.2,.8,1.1]})]:
        import_prepared_asset(manifest,instance,transform)
        bpy.context.view_layer.update()
        instance_root=next(o for o in bpy.context.scene.objects if o.get('studio_id')==instance)
        for obj in bpy.context.scene.objects:
            if obj.get('studio_instance_id')!=instance or not obj.get('studio_source_object_id'):continue
            wanted=instance_root.matrix_world @ expected[obj['studio_source_object_id']]
            error=max(abs(obj.matrix_world[i][j]-wanted[i][j]) for i in range(4) for j in range(4))
            assert error<1e-5,(instance,obj['studio_source_object_id'],error)
            checks.append(instance+'/'+obj['studio_source_object_id'])
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
    print(json.dumps({'ok':True,'checked_poses':checks,'source_unchanged':True}))
