"""Patch saved pavilion from project/revision_request.json, preserving all geometry."""
import hashlib,json,sys
from pathlib import Path
import bpy
from mathutils import Vector
job=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
if not job.get('base_version'):raise ValueError('This patch requires --base')
p=Path(job['project_dir'])/'revision_request.json'
request=json.loads(p.read_text()) if p.exists() else {}
allowed={'reverse_panels','delay_frames','camera_offset','roughness','normalize_ids','lens_mm'}
if set(request)-allowed:raise ValueError('Unknown revision fields')
scene=bpy.context.scene;N=job['shot']['duration_frames'];scene.frame_set(1)
def geometry_hash():
    h=hashlib.sha256()
    for obj in sorted(scene.objects,key=lambda o:o.name):
        if obj.type=='MESH':
            h.update(obj.name.encode());h.update(json.dumps([[round(c,6) for c in v.co] for v in obj.data.vertices]).encode())
            h.update(json.dumps([list(f.vertices) for f in obj.data.polygons]).encode())
    return h.hexdigest()
before=geometry_hash();count=len(scene.objects)
if request.get('normalize_ids',True):
    for obj in scene.objects:
        if obj.get('studio_id'):
            ident=obj.get('part_id',obj.name)
            obj['studio_id']='pavilion/'+ident;obj['studio_instance_id']='pavilion';obj['studio_part_id']=ident
            if obj.get('explode_vector'):obj['studio_explode_vector']=list(obj['explode_vector'])
            center=sum((Vector(c) for c in obj.bound_box),Vector())/8 if obj.type=='MESH' else Vector()
            obj['studio_anchors']=json.dumps({'pavilion/'+ident+'/center':list(center)})
if 'reverse_panels' in request or 'delay_frames' in request:
    for obj in scene.objects:
        if not obj.name.startswith('panel_'):continue
        _,j,i=obj.name.split('_');j,i=int(j),int(i)
        side=23-i if request.get('reverse_panels',False) else i
        start=18+int(side*.6)+j+int(request.get('delay_frames',0));end=min(start+85,N-8)
        if not 1<=start<end<=N:raise ValueError('TIMING_CONFLICT')
        rest=Vector(obj['rest_location']);delta=Vector(obj['explode_vector'])*3.3
        obj.animation_data_clear()
        for frame,position in [(1,rest),(start,rest),(end,rest+delta),(N,rest+delta)]:
            obj.location=position;obj.keyframe_insert(data_path='location',frame=frame)
if 'camera_offset' in request or 'lens_mm' in request:
    cam=scene.camera;offset=Vector(request.get('camera_offset',[0,0,0]));poses=[]
    for frame in (1,N):
        scene.frame_set(frame);old=cam.location.copy();forward=cam.rotation_euler.to_quaternion()@Vector((0,0,-1))
        target=old+forward*(old-Vector((0,0,3.6))).length
        new=old+offset;poses.append((frame,new,(target-new).to_track_quat('-Z','Y').to_euler()))
    cam.animation_data_clear()
    for frame,location,rotation in poses:
        cam.location=location;cam.rotation_euler=rotation;cam.keyframe_insert('location',frame=frame);cam.keyframe_insert('rotation_euler',frame=frame)
    if 'lens_mm' in request:cam.data.lens=float(request['lens_mm'])
if 'roughness' in request:
    value=float(request['roughness'])
    if not 0<=value<=1:raise ValueError('roughness out of range')
    bpy.data.materials['01 | satin anodized aluminium'].node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=value
scene.frame_set(1);after=geometry_hash()
if before!=after or count!=len(scene.objects):raise RuntimeError('Preserved geometry changed')
Path(job['output_dir'],'revision_check.json').write_text(json.dumps({'request':request,'geometry_before':before,'geometry_after':after,'geometry_unchanged':True,'object_count':count,'base_version':job['base_version']},indent=2))
