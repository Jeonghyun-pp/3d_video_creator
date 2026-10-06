"""Run inside Blender, no renderer/GPU required. Covers all six semantic actions."""
from pathlib import Path
import sys
import json
import bpy
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'studio/blender_ops'))
from scene_tools import apply_actions, apply_camera, anchors_for_frame

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene; scene.render.fps=30; scene.frame_end=60

def cube(name, loc):
    bpy.ops.mesh.primitive_cube_add(size=1,location=loc)
    obj=bpy.context.object; obj.name=name; obj['studio_id']='test/'+name; obj['studio_instance_id']='test'; obj['studio_part_id']=name; obj['studio_explode_vector']=[0,0,1]
    return obj

panel=cube('panel',(0,0,0)); stack=cube('stack',(2,0,0)); shell=cube('shell',(4,0,0)); cutter=cube('cutter',(4,.4,0)); lamp=cube('lamp',(6,0,0))
material=bpy.data.materials.new('cap'); material.use_nodes=True
lamp.data.materials.append(material)
curve=bpy.data.curves.new('path','CURVE'); curve.dimensions='3D'
spline=curve.splines.new('POLY'); spline.points.add(1); spline.points[0].co=(0,2,0,1); spline.points[1].co=(2,2,0,1)
path=bpy.data.objects.new('path',curve); scene.collection.objects.link(path)

def action(name, kind, part, start, end, params):
    return {'action_id':name,'type':kind,'targets':[{'instance_id':'test','part_id':part}],
            'start_frame':start,'end_frame':end,'easing':'linear','params':params}

shot={'duration_frames':60,'camera':{'projection':'perspective','keys':[{'frame':0,'location':[10,-12,8],'target':[2,0,0],'lens_mm':45}]},
      'actions':[action('peel','peel','panel',0,10,{'direction_source':'asset','distance_m':1,'order':'asset_order'}),
                 action('return','assemble','panel',10,20,{'source_action_id':'peel'}),
                 action('explode','explode','stack',0,10,{'direction_source':'axis','axis':[0,0,1],'distance_m':2}),
                 action('cut','cutaway','shell',0,30,{'cutter_object_id':'test/cutter','cap_material_id':'cap'}),
                 action('flow','flow','panel',0,30,{'path_object_id':'path','speed_mps':1,'marker_count':3}),
                 action('glow','highlight','lamp',0,10,{'color_srgb':[1,.2,.1],'strength':2,'restore':False})]}
apply_camera(shot); apply_actions(shot)
scene.frame_set(10); assert abs(panel.location.z-1)<1e-5,panel.location
scene.frame_set(20); assert abs(panel.location.z)<1e-5,panel.location
scene.frame_set(10); assert abs(stack.location.z-2)<1e-5,stack.location
scene.frame_set(20)
node=next(n for n in lamp.material_slots[0].material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
assert abs(node.inputs['Emission Strength'].default_value-2)<1e-5
apply_actions(shot)
assert len([o for o in scene.objects if o.get('studio_flow_action')=='flow'])==3
assert len([m for m in shell.modifiers if m.type=='BOOLEAN'])==1
scene.frame_set(10); assert abs(panel.location.z-1)<1e-5
scene.frame_set(20); assert abs(panel.location.z)<1e-5
rows=anchors_for_frame([{'label_id':'test_label','anchor':'test/panel/center','start_frame':0,'end_frame':60}],19)
assert rows[0]['anchor_id']=='test/panel/center' and rows[0]['depth']>0
shot['actions'] = []
apply_actions(shot)
scene.frame_set(10)
assert abs(panel.location.z)<1e-5 and abs(stack.location.z)<1e-5
assert not any(o.get('studio_flow_action') for o in scene.objects)
assert not any(m.name.startswith('StudioCutaway_') for m in shell.modifiers)
assert lamp.material_slots[0].material == material
print('STUDIO_ACTION_SMOKE '+json.dumps({'ok':True,'checks':['peel','assemble','explode','cutaway','flow','highlight_restore_false','reapply_no_duplicates','center_anchor','deleted_actions_restore_rest']}))
