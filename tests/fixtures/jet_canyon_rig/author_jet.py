"""Jet canyon scene for the camera-rig port: canyon + aircraft only. The camera comes from shot.camera.rig."""
import bisect
import json
import math
import random
from pathlib import Path

import bpy

from mathutils import Matrix, Quaternion, Vector

FPS, COUNT, SPEED = 30, 219, 90.0
UP = Vector((0, 0, 1))
rng = random.Random(231)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene['studio_authored_animation'] = True
scene['subject_mode'] = 'illustrative procedural blockout'
scene['flight_speed_m_per_s'] = SPEED
scene.render.engine = 'CYCLES'
scene.cycles.samples = 8
scene.cycles.max_bounces = 2
scene.cycles.diffuse_bounces = 1
scene.cycles.glossy_bounces = 1
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
scene.render.fps = FPS
scene.frame_start, scene.frame_end = 1, COUNT
scene.render.image_settings.file_format = 'PNG'
scene.render.film_transparent = False
scene.render.use_motion_blur = False  # Crisp reference blockout; speed comes from actual parallax.
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'
scene.view_settings.exposure = 0.0
scene.view_settings.gamma = 1
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes.get('Background')
bg.inputs['Color'].default_value = (.26, .43, .68, 1)
bg.inputs['Strength'].default_value = .8


def mat(name, color, rough=1):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    m.use_nodes = True
    p = m.node_tree.nodes.get('Principled BSDF')
    p.inputs['Base Color'].default_value = (*color, 1)
    p.inputs['Roughness'].default_value = rough
    return m


rock = [mat('wall facet %02d' % i, c) for i, c in enumerate([
    (.58,.27,.115), (.70,.35,.17), (.53,.23,.105), (.79,.52,.29),
    (.73,.49,.30), (.49,.29,.18), (.37,.19,.11), (.64,.39,.21),
    (.84,.61,.38), (.58,.40,.26)])]
sand = [mat('sandy floor %d' % i, c) for i, c in enumerate([
    (.61,.49,.31), (.66,.54,.36), (.59,.46,.29), (.70,.57,.39)])]
airframe = mat('light blue grey airframe', (.38,.58,.68), .8)
white = mat('invasion stripe white', (.84,.86,.83))
black = mat('invasion stripe black and propeller', (.018,.023,.025))
canopy = mat('opaque dark navy blockout canopy', (.025,.055,.08), .4)
red = mat('small red spinner', (.55,.055,.025))


def mesh(name, verts, faces, materials, indices=None, parent=None):
    data = bpy.data.meshes.new(name)
    data.from_pydata(verts, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    for m in materials:
        data.materials.append(m)
    for i, p in enumerate(data.polygons):
        p.use_smooth = False
        if indices:
            p.material_index = indices[i]
    if parent:
        obj.parent = parent
    return obj


def center(y):
    # A sampled smooth sinusoidal spline, then arc-length parameterized for constant speed.
    return Vector((46 * math.sin(y / 97) + 13 * math.sin(y / 43 + .6), y, 0))


ys = [i * 1.0 - 160 for i in range(1501)]
arc = [0.0]
for a, b in zip(ys, ys[1:]):
    arc.append(arc[-1] + (center(b) - center(a)).length)


def route(s):
    k = max(0, min(len(arc) - 2, bisect.bisect_right(arc, s) - 1))
    frac = (s - arc[k]) / (arc[k + 1] - arc[k])
    return center(ys[k] + frac)


def basis(s):
    forward = (route(s + .5) - route(s - .5)).normalized()
    right = forward.cross(UP).normalized()
    return right, forward


def half_width(s):
    return 22 + 2.1 * math.sin(s / 64 + .3)


# Large cells and shared tier vertices: broad irregular facets rather than noisy micro triangles.
wall_s = list(range(0, int(arc[-1]), 18))
tiers = [0, 12, 28, 45, 63, 77]
for side in (-1, 1):
    verts = []
    for i, s in enumerate(wall_s):
        c, (right, _) = route(s), basis(s)
        for j, z in enumerate(tiers):
            lateral = half_width(s) + [0, .5, 2.4, 5.5, 10, 16][j]
            lateral += rng.uniform(-1.3, 1.3) if j else rng.uniform(-.5,.5)
            v = c + right * side * lateral
            v.z = z + (rng.uniform(-2.0,2.0) if j else 0)
            verts.append(tuple(v))
    faces, indices = [], []
    for i in range(len(wall_s)-1):
        for j in range(len(tiers)-1):
            a = i*len(tiers)+j
            b = (i+1)*len(tiers)+j
            quad = (a,b,b+1,a+1) if side == -1 else (a+1,b+1,b,a)
            color = [1,3,1,8,4][j]
            if rng.random() < .35:
                color = rng.randrange(len(rock))
            # Some diagonal faces reveal the flat triangulated geometry without confetti coloring.
            if (i+j) % 5 == 0:
                faces.extend([(quad[0],quad[1],quad[2]),(quad[0],quad[2],quad[3])])
                indices.extend([color,color if rng.random() < .75 else rng.randrange(len(rock))])
            else:
                faces.append(quad)
                indices.append(color)
    mesh('faceted canyon wall left' if side < 0 else 'faceted canyon wall right', verts, faces, rock, indices)
    # Upper land continues outside canyon; no floating edges at high camera moments.
    top, topfaces = [], []
    for s in wall_s:
        c, (right, _) = route(s), basis(s)
        for lateral in (half_width(s)+15, 150):
            v=c+right*side*lateral
            v.z=75
            top.append(tuple(v))
    for i in range(len(wall_s)-1):
        topfaces.append((2*i,2*i+1,2*i+3,2*i+2))
    mesh('plateau %d' % side, top, topfaces, rock, [7]*len(topfaces))

floorverts, floorfaces, floorindices = [], [], []
for s in wall_s:
    c, (right, _) = route(s), basis(s)
    for fraction in (-1,0,1):
        v=c+right*fraction*(half_width(s)+.7)
        v.z=-.03
        floorverts.append(tuple(v))
for i in range(len(wall_s)-1):
    for j in range(2):
        a=i*3+j
        floorfaces.append((a,a+1,a+4,a+3))
        floorindices.append(rng.randrange(len(sand)))
mesh('sandy canyon floor', floorverts, floorfaces, sand, floorindices)

bpy.ops.object.light_add(type='SUN', location=(0,0,100))
sun=bpy.context.object
sun.name='hard sunlight from camera left'
sun.data.energy=2.8
sun.data.angle=math.radians(.5)
sun.rotation_euler=(math.radians(28),math.radians(-35),math.radians(-25))


def prism(name, poly, thickness, material, parent):
    n=len(poly)
    verts=[(x,y,z-thickness/2) for x,y,z in poly]+[(x,y,z+thickness/2) for x,y,z in poly]
    faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]
    faces.extend([(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)])
    return mesh(name,verts,faces,[material],parent=parent)


def fighter(name):
    root=bpy.data.objects.new(name,None)
    scene.collection.objects.link(root)
    parts=[]
    rings=[(-4.5,.08,.10),(-3.5,.24,.28),(-1.7,.46,.46),(.7,.62,.58),(2.7,.55,.53),(3.7,.34,.35)]
    verts=[]
    for y,rx,rz in rings:
        for k in range(10):
            angle=2*math.pi*k/10
            verts.append((rx*math.cos(angle),y,.28+rz*math.sin(angle)))
    faces=[]
    for j in range(len(rings)-1):
        for k in range(10):
            a=j*10+k;b=j*10+(k+1)%10
            faces.append((a,b,b+10,a+10))
    faces.extend([tuple(reversed(range(10))),tuple(range(50,60))])
    parts.append(mesh(name+' tapered fuselage',verts,faces,[airframe],parent=root))
    # Piecewise tapered wings with actual per-face black/white invasion bands.
    edges=[0,1.8,2.15,2.50,2.85,3.20,3.55,4.6,5.65]
    for side in (-1,1):
        for k,(a,b) in enumerate(zip(edges,edges[1:])):
            def chord(x):
                return (1.40-.20*x, -.95+.035*x)
            af,ab=chord(a);bf,bb=chord(b)
            wingmat=black if k in (2,4,6) else white if k in (3,5) else airframe
            parts.append(prism(name+' wing %d %d'%(side,k),[(side*a,ab,.15),(side*b,bb,.15),(side*b,bf,.15),(side*a,af,.15)],.13,wingmat,root))
        parts.append(prism(name+' tailplane %d'%side,[(0,-3.3,.36),(side*2.05,-3.9,.36),(side*1.95,-2.9,.36),(0,-2.8,.36)],.09,airframe,root))
    parts.append(mesh(name+' vertical fin',[(0,-4,.3),(0,-3.85,1.65),(0,-2.65,.3),(.10,-4,.3),(.10,-3.85,1.65),(.10,-2.65,.3)],[(0,1,2),(5,4,3),(0,3,4,1),(1,4,5,2),(2,5,3,0)],[airframe],parent=root))
    # Low-poly greenhouse, opaque for deliberately simple reference shading.
    parts.append(mesh(name+' canopy',[(-.37,-1.1,.65),(.37,-1.1,.65),(-.42,.5,.7),(.42,.5,.7),(-.27,-.8,1.03),(.27,-.8,1.03),(-.25,.28,1.12),(.25,.28,1.12)],[(0,1,5,4),(2,6,7,3),(0,4,6,2),(1,3,7,5),(4,5,7,6)],[canopy],parent=root))
    bpy.ops.mesh.primitive_cone_add(vertices=10,radius1=.32,radius2=0,depth=.65,location=(0,4.0,.28),rotation=(-math.pi/2,0,0))
    spinner=bpy.context.object;spinner.name=name+' red spinner';spinner.parent=root;spinner.data.materials.append(red);parts.append(spinner)
    prop=bpy.data.objects.new(name+' propeller rotor',None);scene.collection.objects.link(prop);prop.parent=root;prop.location=(0,3.85,.28)
    for angle in (0,math.pi/2):
        blade=prism(name+' propeller blade',[(-.08,-.04,-.85),(.08,-.04,-.85),(.08,-.04,.85),(-.08,-.04,.85)],.05,black,prop)
        blade.rotation_euler[1]=angle
        parts.append(blade)
    return root,parts,prop



import sys
sys.path.insert(0, STUDIO_JOB['project_dir'])
from choreography import high  # shared with the procedural camera so both use one phase schedule
sys.path.insert(0, str(Path(STUDIO_JOB['project_dir']).parents[2] / 'studio/blender_ops'))
from scene_tools import curves


def gap(t):
    # Leader gap closes 55 -> 32 m during high moments; built from high() so the leader never reverses.
    return 55 - 23 * high(t)


def flight(t, ahead=0.0, leader=False):
    s = 220 + SPEED * t + ahead
    c = route(s); right, forward = basis(s)
    h = high(t)
    weave = (-.8 + .5 * math.sin(t * 1.7) if leader else 1.55 * math.sin(t * 2.4)) - 10 * h
    pos = c + right * weave + UP * (8.8 + 1.0 * math.sin(t * 1.9 + ahead * .03))
    f0 = basis(s - 2)[1]; f1 = basis(s + 2)[1]
    curvature = math.atan2(f0.cross(f1).z, f0.dot(f1)) / 4
    bank = max(-math.radians(68), min(math.radians(68), -math.atan(SPEED * SPEED * curvature / 9.81)))
    bank += math.radians(8) * math.sin(t * 3.1 + ahead * .07)
    bank *= 1 - .52 * h
    if leader:
        pos.z = pos.z * (1 - h) + 3.5 * h
    return pos, Matrix((right, forward, UP)).transposed().to_quaternion() @ Quaternion((0, 1, 0), bank)


pursuer, _, pursuer_prop = fighter('pursuer')
leader, _, leader_prop = fighter('leader')
pursuer['studio_id'] = 'pursuer'; leader['studio_id'] = 'leader'
for f in range(1, COUNT + 1):
    t = (f - 1) / FPS
    for obj, (pos, rot) in ((pursuer, flight(t)), (leader, flight(t, gap(t), True))):
        obj.location = pos; obj.rotation_mode = 'QUATERNION'; obj.rotation_quaternion = rot
        obj.keyframe_insert('location', frame=f); obj.keyframe_insert('rotation_quaternion', frame=f)
    for prop in (pursuer_prop, leader_prop):
        prop.rotation_euler[1] = f * math.radians(57); prop.keyframe_insert('rotation_euler', frame=f)
for obj in (pursuer, leader, pursuer_prop, leader_prop):
    for curve in curves(obj.animation_data.action):
        for key in curve.keyframe_points:
            key.interpolation = 'LINEAR'
cam_data = bpy.data.cameras.new('rig camera'); cam_data.sensor_width = 36; cam_data.clip_start = .2; cam_data.clip_end = 2000
cam = bpy.data.objects.new('rig camera', cam_data); scene.collection.objects.link(cam); scene.camera = cam
for obj in scene.objects:
    if obj.name.startswith('faceted canyon wall'):
        obj['studio_id'] = 'wall_left' if 'left' in obj.name else 'wall_right'
