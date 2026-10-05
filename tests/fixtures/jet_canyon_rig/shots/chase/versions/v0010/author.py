"""Jet canyon scene: canyon + spec-built P-51D aircraft (subjects/p51d/spec.json). The camera comes from shot.camera.rig."""
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
scene['subject_mode'] = 'spec-built P-51D (subjects/p51d/spec.json, specific_real)'
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


import copy
from modeling import build_subject
# Geometry comes only from the sourced spec; no aircraft numbers live in this script.
spec = json.loads(Path(STUDIO_JOB['subject_spec_paths']['p51d']).read_text())  # the version's snapshot
built = build_subject(spec)
pursuer, pursuer_prop = built['root'], built['parts']['prop']
leader_spec = copy.deepcopy(spec); leader_spec['subject_id'] = 'p51d_leader'  # second instance, same geometry
built_leader = build_subject(leader_spec)
leader, leader_prop = built_leader['root'], built_leader['parts']['prop']
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
