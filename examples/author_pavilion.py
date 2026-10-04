"""Original schematic pavilion: editable curved skins, real frame and bolted supports.
Run via studio shot build. Geometry isn't a surveyed building or an engineering model.
"""
import json, math, sys
from pathlib import Path
import bpy
from mathutils import Vector

job = json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
shot = job['shot']
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene['studio_authored_animation'] = True
scene['subject_mode'] = 'schematic'
scene['description'] = 'Original curved-shell pavilion; illustrative, not DDP survey'
N = shot.get('duration_frames', 180)
scene.frame_start, scene.frame_end = 1, N
scene.render.fps = 30
scene.render.engine = 'CYCLES'
scene.cycles.samples = 32
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 720,1280
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.world.color = (.07,.07,.07)
scene.world.use_nodes = True
scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.10,.14,.20,1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value=.35
scene.view_settings.view_transform='AgX'


def mat(name,col,metal=0,rough=.45,noise=0,emission=0):
    m=bpy.data.materials.new(name);m.diffuse_color=(*col,1);m.use_nodes=True
    n=m.node_tree.nodes; p=n.get('Principled BSDF');p.inputs['Base Color'].default_value=(*col,1)
    p.inputs['Metallic'].default_value=metal;p.inputs['Roughness'].default_value=rough
    if emission:p.inputs['Emission Color'].default_value=(*col,1);p.inputs['Emission Strength'].default_value=emission
    if noise:
        tex=n.new('ShaderNodeTexNoise');tex.inputs['Scale'].default_value=180
        bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=noise;bump.inputs['Distance'].default_value=.008
        m.node_tree.links.new(tex.outputs['Fac'],bump.inputs['Height']);m.node_tree.links.new(bump.outputs['Normal'],p.inputs['Normal'])
    return m
silver=mat('01 | satin anodized aluminium',(.40,.46,.51),.48,.46,.06)
steel=mat('02 | structural titanium grey',(.16,.23,.29),.65,.32)
dark=mat('03 | graphite gaskets',(.018,.026,.035),.15,.57)
boltmat=mat('04 | stainless fasteners',(.60,.65,.69),.88,.22)
concrete=mat('05 | honed limestone',(.24,.26,.27),0,.72,.18)
glass=mat('06 | midnight glazed envelope',(.025,.085,.11),.63,.14)
wood=mat('07 | warm oak floors',(.32,.16,.068),0,.5,.08)
red=mat('08 | vermilion structural accent',(.55,.025,.016),.25,.3)
warm=mat('09 | recessed linear light',(.95,.47,.17),0,.4,emission=3)
foliage=mat('10 | olive foliage',(.065,.12,.073),0,.82)
peoplemat=mat('11 | scale figures',(.12,.14,.15),0,.65)


def tag(o,ident):
    o['studio_id']='pavilion/'+ident;o['part_id']=ident;o['instance_id']='pavilion'
    o['studio_instance_id']='pavilion';o['studio_part_id']=ident;return o

def mesh(name,verts,faces,material,bevel=0):
    me=bpy.data.meshes.new(name);me.from_pydata(verts,[],faces);me.update()
    ob=bpy.data.objects.new(name,me);scene.collection.objects.link(ob);me.materials.append(material)
    tag(ob,name)
    if bevel:
        b=ob.modifiers.new('manufactured edge radius','BEVEL');b.width=bevel;b.segments=2
        ob.modifiers.new('weighted normals','WEIGHTED_NORMAL')
    return ob

def box(name,loc,scale,material,bevel=.025):
    bpy.ops.mesh.primitive_cube_add(size=1,location=loc);o=bpy.context.object;o.name=name;o.dimensions=scale
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(material);tag(o,name)
    if bevel:
        m=o.modifiers.new('edge highlight','BEVEL');m.width=bevel;m.segments=2;o.modifiers.new('weighted normals','WEIGHTED_NORMAL')
    return o

def tube(name,a,b,r,material,vertices=10):
    a,b=Vector(a),Vector(b);v=b-a
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices,radius=r,depth=v.length,location=(a+b)/2)
    o=bpy.context.object;o.name=name;o.rotation_euler=v.to_track_quat('Z','Y').to_euler();o.data.materials.append(material);tag(o,name)
    for p in o.data.polygons:p.use_smooth=len(p.vertices)==4
    return o

def sphere(name,loc,r,material):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12,ring_count=8,radius=r,location=loc)
    o=bpy.context.object;o.name=name;o.data.materials.append(material);tag(o,name)
    for p in o.data.polygons:p.use_smooth=True
    return o

def surface(t,p,offset=0):
    return Vector(((10+offset)*math.sin(t)*math.cos(p),(6.1+offset)*math.sin(t)*math.sin(p),2.8+(4.6+offset)*math.cos(t)))

# Site, genuinely dimensional steps and floor lines.
box('site', (0,0,-.55),(2000,2000,.4),mat('asphalt',(.035,.048,.06),0,.9),0)
for i in range(3):box('terrace_%02d'%i,(0,0,-.24+i*.13),(25-i*.9,17-i*.9,.14),concrete,.045)
for x in range(-12,13):box('paving_x_%s'%x,(x,0,.091),(.009,16,.009),dark,0)
for y in range(-8,9):box('paving_y_%s'%y,(0,y,.092),(24,.009,.009),dark,0)
# Elliptical floor decks, full cylinder scaled to oval.
for z in (.24,2.55):
    bpy.ops.mesh.primitive_cylinder_add(vertices=96,radius=1,depth=.2,location=(0,0,z));o=bpy.context.object;o.name='floor_%s'%z;o.scale=(8.8,5.1,1);o.data.materials.append(concrete);tag(o,o.name)
    bpy.ops.mesh.primitive_cylinder_add(vertices=96,radius=1,depth=.025,location=(0,0,z+.112));o=bpy.context.object;o.name='oak_deck_%s'%z;o.scale=(8.65,4.95,1);o.data.materials.append(wood);tag(o,o.name)
# Glazed ground enclosure with slender mullions.
for i in range(48):
    p0=2*math.pi*i/48;p1=2*math.pi*(i+1)/48
    a=(8.7*math.cos(p0),5*math.sin(p0));b=(8.7*math.cos(p1),5*math.sin(p1))
    mesh('glazing_%02d'%i,[(a[0],a[1],.4),(b[0],b[1],.4),(b[0],b[1],2.4),(a[0],a[1],2.4)],[(0,1,2,3)],glass)
    tube('mullion_%02d'%i,(*a,.35),(*a,2.55),.033,steel,8)
    if i%2==0:tube('light_%02d'%i,(*a,2.40),(*b,2.40),.018,warm,6)
# Radial steel structure under curved panels, each visible member is real geometry.
RINGS=7;SECTORS=24;t0=.10;t1=1.44
nodes={}
for j in range(RINGS+1):
    t=t0+(t1-t0)*j/RINGS
    for i in range(SECTORS):
        p=2*math.pi*i/SECTORS;nodes[j,i]=surface(t,p,-.17)
        if j>0:tube('rib_%02d_%02d'%(j,i),nodes[j-1,i],nodes[j,i],.055,steel)
        if i>0:tube('ring_%02d_%02d'%(j,i),nodes[j,i-1],nodes[j,i],.045,steel)
    tube('ring_close_%02d'%j,nodes[j,SECTORS-1],nodes[j,0],.045,steel)
for j in range(1,RINGS+1):
    for i in range(SECTORS):
        if (i+j)%2==0:tube('diagonal_%02d_%02d'%(j,i),nodes[j-1,i],nodes[j,(i+1)%SECTORS],.027,steel,8)
        pos=nodes[j,i];sphere('joint_%02d_%02d'%(j,i),pos,.095,boltmat)
        if j==RINGS and i%3==0:tube('column_%02d'%i,(pos.x*.84,pos.y*.84,.4),pos,.105,steel)
# Curved panel sheets have seams, a real thickness, substructure, and a stable local root.
panels=[]
for j in range(RINGS):
    for i in range(SECTORS):
        p0=2*math.pi*i/SECTORS+.004;p1=2*math.pi*(i+1)/SECTORS-.004
        ta=t0+(t1-t0)*j/RINGS+.003;tb=t0+(t1-t0)*(j+1)/RINGS-.003
        center=surface((ta+tb)/2,(p0+p1)/2)
        verts=[surface(ta+(tb-ta)*v/3,p0+(p1-p0)*u/3)-center for v in range(4) for u in range(4)]
        faces=[(v*4+u,v*4+u+1,(v+1)*4+u+1,(v+1)*4+u) for v in range(3) for u in range(3)]
        o=mesh('panel_%02d_%02d'%(j,i),verts,[tuple(reversed(f)) for f in faces],silver,0);o.location=center
        sol=o.modifiers.new('panel thickness 35mm','SOLIDIFY');sol.thickness=.035
        edge=o.modifiers.new('panel edge radius','BEVEL');edge.width=.008;edge.segments=2
        for f in o.data.polygons:f.use_smooth=True
        o['rest_location']=list(center);o['explode_vector']=list(Vector((center.x*.11,center.y*.15,1)).normalized())
        o['studio_explode_vector']=list(o['explode_vector'])
        o['part_group']='panels';panels.append(o)
        # Low-profile support shoe is intentionally legible in closeup.
        if j>=3:
            pos=nodes[j,i];cube=box('bracket_%02d_%02d'%(j,i),pos+Vector((0,0,.095)),(.19,.24,.07),boltmat,.012)
            for dx in (-.055,.055):tube('bolt_%02d_%02d_%s'%(j,i,dx),pos+Vector((dx,0,.10)),pos+Vector((dx,0,.16)),.024,dark,6)
# Additional detail at the close-up joint: gusset, screw head, red load-bearing connector.
pos=nodes[5,19];tag(box('connection_plate',pos+Vector((0,0,.10)),(.38,.32,.07),red,.015),'connection_plate')
for dx in (-.13,.13):
    for dy in (-.10,.10):tube('macro_fastener_%s_%s'%(dx,dy),pos+Vector((dx,dy,.10)),pos+Vector((dx,dy,.19)),.038,boltmat,6)
# Sparse landscaping + scale figures, no fake typography baked into geometry.
for x,y in [(-11,-5),(11,4),(-10,6),(10,-6)]:
    tube('tree_trunk_%s'%x,(x,y,.15),(x,y,1.6),.07,wood)
    crown=sphere('tree_crown_%s'%x,(x,y,2.0),.7,foliage);crown.scale=(1,1,1.5)
    box('bench_%s'%x,(x+.8,y,.55),(1.5,.45,.10),wood)
for x,y in [(-4,-6.2),(2,-6.9),(7,5.8)]:
    tube('person_body_%s'%x,(x,y,.4),(x,y,1.28),.13,peoplemat)
    sphere('person_head_%s'%x,(x,y,1.48),.13,peoplemat)
# Lighting: physically sized sources produce panel gradients and separable metal edges.
def area(name,loc,target,power,color,size):
    d=bpy.data.lights.new(name,'AREA');d.energy=power;d.color=color;d.shape='DISK';d.size=size
    o=bpy.data.objects.new(name,d);scene.collection.objects.link(o);o.location=loc;o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler()
area('soft sky key',(-10,-10,18),(0,0,3),4200,(.69,.81,1),12)
area('warm edge',(6,10,12),(0,0,4),5000,(1,.72,.42),9)
area('front fill',(12,-16,8),(0,0,4),2700,(.68,.84,1),10)
d=bpy.data.lights.new('late afternoon sun','SUN');d.energy=1.1;d.angle=.15;o=bpy.data.objects.new('late afternoon sun',d);scene.collection.objects.link(o);o.rotation_euler=(.35,-.45,-.5)
# Authors retain frame keys in the saved scene; revisions copy it and change only target keys.
bpy.ops.object.camera_add();cam=bpy.context.object;cam.name='Camera';tag(cam,'camera');scene.camera=cam
cam.data.lens=28;cam.data.clip_end=500
which=shot.get('shot_id','shot_01')
reverse=shot.get('demo',{}).get('reverse_panels',False)
delay=shot.get('demo',{}).get('delay_frames',0)
for k,o in enumerate(panels):
    j,i=divmod(k,SECTORS);side=(SECTORS-1-i if reverse else i)
    start=18+int(side*.6)+j+delay;end=min(start+85,N-8)
    rest=Vector(o['rest_location']);direction=Vector(o['explode_vector'])
    # Reveal the whole structural layer without hiding any panels abruptly.
    amount=3.3 if which=='shot_01' else 4.2
    if which=='shot_01':
        o.location=rest;o.keyframe_insert(data_path='location',frame=1);o.keyframe_insert(data_path='location',frame=start)
        o.location=rest+direction*amount;o.keyframe_insert(data_path='location',frame=end);o.keyframe_insert(data_path='location',frame=N)
    else:o.location=rest+direction*amount
if which=='shot_02':
    target=pos+Vector((.03,-.03,.14));c0=target+Vector((2.8,-4.0,2.5));c1=target+Vector((1.85,-2.75,1.85));cam.data.lens=52
else:
    target=Vector((0,0,3.6));c0=Vector((19,-25,18));c1=Vector((23,-21,16))
    if which=='shot_03':c0=Vector((14,-19,10));c1=Vector((21,-13,12));target=Vector((0,0,4.7));cam.data.lens=42
for f,c in [(1,c0),(N,c1)]:
    cam.location=c;cam.rotation_euler=(target-c).to_track_quat('-Z','Y').to_euler();cam.keyframe_insert(data_path='location',frame=f);cam.keyframe_insert(data_path='rotation_euler',frame=f)
scene.frame_set(1)
# Allow direct smoke/hero rendering before harness integration.
if job.get('standalone_output'):
    bpy.ops.wm.save_as_mainfile(filepath=job['standalone_output'])
if job.get('hero_output'):
    scene.render.resolution_x=job.get('width',540);scene.render.resolution_y=job.get('height',960)
    scene.render.engine=job.get('engine','CYCLES');scene.cycles.samples=job.get('samples',24)
    scene.frame_set(job.get('frame',90));scene.render.filepath=job['hero_output'];bpy.ops.render.render(write_still=True)
