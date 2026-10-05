"""Shared scene kit for the Samsung-station column reel recreation (internal test, not published).

Every shot is built from the same environments with only its camera and overlays changing, so the
blockout and the photoreal version differ only in materials and lighting:
  MODE 'blockout'  -> flat Principled colours (like the jet blockout), sun + flat world
  MODE 'photoreal' -> look_materials catalog (CC0 texture sets, wear, grime) + the look preset's lighting
Units are metres, Z up. The underground station runs along +Y under Yeongdong-daero (road along +Y).
Facts on screen (80 columns, 2 -> 1 rows of main bars, 2028) follow the reference narration; the
station geometry is an explanatory model, not survey data.
"""
import json
import math
from pathlib import Path
import random

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

FONT_FACE = 'library/fonts/pretendard/Pretendard-ExtraBold.otf'   # bundled OFL font (same glyphs on every machine)
LEVEL_H = 7.0          # storey height (m) of the underground box
LEVELS = 5             # B1..B5
BOX_W, BOX_L = 34.0, 160.0
ROAD_W = 44.0
STATE = {'mode': 'blockout', 'library_root': None, 'mats': {}, 'detail': False, 'instances': 0, 'seed': 7, 'meshes': {}, 'clutter': {}}
FLAT = {  # blockout palette (linear-ish sRGB values, matte)
    'concrete': (0.62, 0.62, 0.60), 'concrete_dark': (0.38, 0.38, 0.37), 'slab_edge': (0.55, 0.55, 0.53),
    'steel': (0.30, 0.32, 0.35), 'rebar': (0.16, 0.10, 0.07), 'asphalt': (0.16, 0.16, 0.17),
    'lane_white': (0.85, 0.85, 0.82), 'lane_yellow': (0.85, 0.65, 0.12), 'sidewalk': (0.55, 0.53, 0.50),
    'building': (0.78, 0.78, 0.76), 'building_dark': (0.22, 0.24, 0.28), 'glass': (0.35, 0.45, 0.52),
    'car_white': (0.82, 0.82, 0.82), 'car_black': (0.05, 0.05, 0.06), 'car_grey': (0.40, 0.41, 0.43), 'car_yellow': (0.85, 0.65, 0.10),
    'tire': (0.03, 0.03, 0.03), 'vest': (0.95, 0.45, 0.05), 'helmet': (0.92, 0.92, 0.90), 'skin': (0.62, 0.45, 0.35),
    'workwear': (0.10, 0.12, 0.16), 'rail': (0.45, 0.45, 0.47), 'sleeper': (0.30, 0.30, 0.30), 'floor_tile': (0.80, 0.80, 0.78),
    'safety_line': (0.85, 0.70, 0.15), 'train': (0.75, 0.76, 0.78), 'rubble': (0.33, 0.31, 0.28), 'timber': (0.55, 0.42, 0.28),
    'tree': (0.20, 0.35, 0.18), 'trunk': (0.25, 0.18, 0.12), 'office_white': (0.85, 0.85, 0.85), 'office_dark': (0.20, 0.21, 0.23),
    'screen': (0.10, 0.20, 0.35), 'interior_concrete': (0.75, 0.75, 0.73), 'interior_slab': (0.78, 0.78, 0.76),
}
CATALOG = {  # photoreal: catalog kind + overrides (textured kinds are tinted with tex_tint, plain kinds with base_color)
    'concrete': ('concrete', {}), 'concrete_dark': ('concrete', {'tex_tint': [0.62, 0.62, 0.6]}),
    'slab_edge': ('concrete', {'tex_tint': [0.9, 0.9, 0.88]}), 'steel': ('painted_steel', {'base_color': [0.05, 0.055, 0.065]}),
    'rebar': ('cast_iron', {}), 'asphalt': ('concrete', {'tex_tint': [0.2, 0.2, 0.21]}),
    'sidewalk': ('concrete', {'tex_tint': [0.8, 0.77, 0.72]}), 'building': ('aluminium_panel', {}),
    'building_dark': ('aluminium_panel', {'base_color': [0.03, 0.035, 0.045]}), 'glass': ('glass', {}),
    'rail': ('brushed_stainless', {}), 'sleeper': ('concrete', {'tex_tint': [0.55, 0.55, 0.54]}),
    'floor_tile': ('concrete', {'tex_tint': [1.25, 1.25, 1.22]}), 'train': ('aluminium_panel', {}),
    'rubble': ('concrete', {'tex_tint': [0.5, 0.46, 0.4]}), 'timber': ('wood', {}),
    'office_white': ('painted_steel', {'base_color': [0.7, 0.7, 0.69]}), 'office_dark': ('painted_steel', {'base_color': [0.03, 0.035, 0.04]}),
    # section staging (s01): a cut read from outside needs light interior surfaces (measured albedo ~0.75)
    'interior_concrete': ('concrete', {'tex_tint': [1.35, 1.35, 1.32]}), 'interior_slab': ('concrete', {'tex_tint': [1.4, 1.4, 1.37]}),
}
EMISSIVE = {'light': ((1.0, 0.97, 0.90), 8.0), 'window_light': ((1.0, 0.82, 0.55), 6.0), 'red': ((0.95, 0.06, 0.04), 6.0),
            'holo': ((0.25, 0.75, 1.0), 1.2), 'white_text': ((1.0, 1.0, 1.0), 1.5), 'screen_glow': ((0.55, 0.75, 1.0), 2.5),
            'headlight': ((1.0, 0.95, 0.85), 10.0), 'taillight': ((1.0, 0.05, 0.03), 6.0)}


def setup(job):
    look = job['shot']['render'].get('look_preset') or 'flat_stylized'
    STATE['mode'] = 'photoreal' if look.startswith('photoreal') else 'blockout'
    STATE['library_root'] = job.get('library_root')
    STATE['mats'] = {}
    STATE['meshes'], STATE['clutter'] = {}, {}
    # Detail variant (structure for hybrid restyles): building elements come from verified exemplar specs.
    STATE['detail'] = Path(job['project_dir']).name.endswith('_detail')
    STATE['instances'] = 0
    STATE['seed'] = 7  # each generator draws from its own random.Random(seed): editing one never shifts another
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.render.engine = 'CYCLES'
    scene['studio_authored_animation'] = True
    if STATE['mode'] == 'blockout':
        scene.cycles.samples = 16
        scene.cycles.use_denoising = True
        scene.cycles.max_bounces = 3
        # Fast GI (AO-approximated bounces): measured on s03, half the frame time and less blown-out interiors.
        # Cost (measured s01 v0011, 2026-10-05): indirect light takes the dusk world colour, so the whole frame
        # turns cool/blue and loses the warm bounce (3.8 s vs 6.1-9.0 s per frame). Blockout colour is not final colour.
        scene.cycles.use_fast_gi = True
        scene.render.use_motion_blur = False
        scene.view_settings.view_transform = 'Standard'
        world = scene.world or bpy.data.worlds.new('World')
        scene.world = world
        world.light_settings.distance = 6.0  # Fast GI reach (m): about one storey
        world.use_nodes = True
        bg = world.node_tree.nodes.get('Background')
        bg.inputs['Color'].default_value = (0.55, 0.62, 0.72, 1)
        bg.inputs['Strength'].default_value = 0.5
        sun = bpy.data.objects.new('sun', bpy.data.lights.new('sun', 'SUN'))
        scene.collection.objects.link(sun)
        sun.data.energy = 2.5
        sun.rotation_euler = (math.radians(40), math.radians(15), math.radians(30))
    return STATE['mode']


def mat(kind):
    if kind in STATE['mats']:
        return STATE['mats'][kind]
    if kind in EMISSIVE:
        color, strength = EMISSIVE[kind]
        m = bpy.data.materials.new(f'emit_{kind}')
        bsdf = m.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value = (*color, 1)
        bsdf.inputs['Emission Color'].default_value = (*color, 1)
        bsdf.inputs['Emission Strength'].default_value = strength
        if kind == 'holo':
            bsdf.inputs['Alpha'].default_value = 0.25
    elif STATE['mode'] == 'photoreal' and kind in CATALOG:
        from look_materials import make_material
        key, overrides = CATALOG[kind]
        m = make_material(f'samsung_{kind}', key, library_root=STATE['library_root'], overrides=overrides or None)
    else:
        m = bpy.data.materials.new(f'flat_{kind}')
        bsdf = m.node_tree.nodes.get('Principled BSDF')
        color = FLAT.get(kind, (0.6, 0.6, 0.6))
        bsdf.inputs['Base Color'].default_value = (*color, 1)
        bsdf.inputs['Roughness'].default_value = 0.85 if kind not in ('glass', 'car_white', 'car_black', 'car_grey', 'car_yellow', 'train', 'screen') else 0.25
        if kind in ('steel', 'rail', 'train'):
            bsdf.inputs['Metallic'].default_value = 0.6 if STATE['mode'] == 'photoreal' else 0.0
        if kind == 'glass' and STATE['mode'] == 'photoreal':
            bsdf.inputs['Transmission Weight'].default_value = 0.9
    viewport = EMISSIVE[kind][0] if kind in EMISSIVE else FLAT.get(kind, (0.6, 0.6, 0.6))
    m.diffuse_color = (*viewport, 1)
    STATE['mats'][kind] = m
    return m


# ---- exemplar elements (detail variant) -------------------------------------------------------------

def exemplar(name, root, rot_z_deg=0.0, edit=None):
    """Place a verified building-element exemplar (library/exemplars/<name>, latest version). `edit(spec)` may
    change its data (run length, counts) - parameters, never builder code. Returns the subject root."""
    from modeling import build_subject  # studio/blender_ops on sys.path during builds
    lib = Path(STATE['library_root']) / 'exemplars' / name
    spec = json.loads((sorted(lib.glob('v*'))[-1] / 'spec.json').read_text())
    STATE['instances'] += 1
    spec['subject_id'] = f"{name.replace('_', '-')}-{STATE['instances']:03d}"
    if edit:
        edit(spec)
    built = build_subject(spec, root_location=tuple(root))
    built['root'].rotation_euler.z = math.radians(rot_z_deg)
    return built['root']


def _builder(spec, part_id):
    return next(b for b in spec['builders'] if b['part_id'] == part_id)


def run_length(part_ids, length, axis=0):
    """edit(): stretch path arrays / sweeps of the given parts to `length` along their first leg's axis."""
    def edit(spec):
        for part in part_ids:
            b = _builder(spec, part)
            params = b['params']
            if b['builder'] == 'profile':
                params['length'] = length
                continue
            key = 'points' if b['builder'] == 'array' else 'path'
            pts = params[key]
            end = list(pts[-1]); end[axis] = pts[0][axis] + length
            params[key] = [pts[0], end]
            if b['builder'] == 'array' and 'count' in params:
                params['count'] = int((length - params.get('start_m', 0.0)) / params['pitch_m']) + 1
                if part == 'panels':
                    params['count'] -= 1
            if b['builder'] == 'profile':
                params['length'] = length
    return edit


def drop_parts(*ids):
    """edit(): remove parts (and the materials / features / dimensions that name them) from an exemplar."""
    def edit(spec):
        spec['builders'] = [b for b in spec['builders'] if b['part_id'] not in ids]
        for key in ('materials', 'features', 'dimensions'):
            rows = []
            for row in spec.get(key, []):
                row = dict(row, part_ids=[p for p in row.get('part_ids', []) if p not in ids])
                if row['part_ids'] or key == 'dimensions' and 'part_ids' not in row:
                    rows.append(row)
            spec[key] = rows
    return edit


def chain(*edits):
    def edit(spec):
        for e in edits:
            e(spec)
    return edit


def girders(span, count, pitch):
    """edit(): beam_grid_ceiling reduced to one direction of KS H-400x200 girders, `span` long, every `pitch`."""
    def edit(spec):
        drop_parts('slab', 'beams_y')(spec)
        params = _builder(spec, 'beams_x')['params']
        params.update({'counts': [count], 'pitch_m': [pitch], 'center': [0, 0, 0]})
        params['item']['params']['length'] = span
    return edit


def template(name, parts):
    """An empty at the origin holding `parts` (built at their offsets from it): one scatter source."""
    root = bpy.data.objects.new(f'{name}.template', None)
    bpy.context.scene.collection.objects.link(root)
    for part in parts:
        part.parent = root
    return root


def scatter_kit(name, sources, points, seed=0, scale=(1.0, 1.0), rotation_deg=(0.0, 360.0)):
    """Instance the sources at points through the engine's scatter (Geometry Nodes): repeated scenery that no
    label, reveal or anchor refers to."""
    from scatter import scatter  # studio/blender_ops on sys.path during builds
    return scatter(name, sources, points=points, seed=seed, scale=scale, rotation_deg=rotation_deg)


# ---- primitives ------------------------------------------------------------------------------------

CLUTTER_PREFIXES = ('dash.', 'lane.', 'zebra.', 'win.')  # road markings, window bands: many, small, static


def scene_role(name, kind):
    """studio_scene_role for kit objects (see studio/blender_ops/scene_roles.py); None = ordinary object."""
    if name.startswith(CLUTTER_PREFIXES):
        return 'clutter'
    if kind == 'light':
        return 'light_fixture'
    return None


# Primitives are built with bmesh into mesh datablocks shared by every object of the same shape and material
# (no bpy.ops in loops: no selection churn, no context dependence, one mesh for 400 lane dashes). Code that
# edits an object's mesh must call unique_data(obj) first. Static clutter (markings, window bands) is merged
# into one object per material and environment (flush_clutter).

def _shared(key, build, kind, smooth=False):
    if key not in STATE['meshes']:
        bm = bmesh.new()
        build(bm)
        me = bpy.data.meshes.new('kit_' + '_'.join(str(k) for k in key))
        bm.to_mesh(me); bm.free()
        me.materials.append(mat(kind))
        if smooth:  # round members read round, creases stay sharp
            me.shade_smooth(); me.set_sharp_from_angle(angle=math.radians(31.0))
        STATE['meshes'][key] = me
    return STATE['meshes'][key]


def unique_data(obj):
    """Give `obj` its own copy of a shared mesh before editing it (the engine's mesh_data rule)."""
    from mesh_data import unique_data as engine_unique  # studio/blender_ops on sys.path during builds
    return engine_unique(obj)


def _place(name, mesh, loc, rot=(0, 0, 0)):
    o = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(o)
    o.location, o.rotation_euler = loc, rot
    o['studio_id'] = name
    o['studio_dim_role'] = 'none'
    return o


def _r6(values):
    return tuple(round(float(v), 6) for v in values)


def _cube(bm, size):
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)


def box(name, size, loc, kind, rot=(0, 0, 0)):
    role = scene_role(name, kind)
    if role == 'clutter':  # merged later; nothing refers to a single marking
        STATE['clutter'].setdefault(kind, []).append((tuple(size), tuple(loc), tuple(rot)))
        return None
    o = _place(name, _shared(('box', _r6(size), kind), lambda bm: _cube(bm, size), kind), loc, rot)
    if role:
        o['studio_scene_role'] = role
    return o


def flush_clutter(group):
    """One object per clutter material: 'clutter.<group>.<kind>' (studio_scene_role clutter)."""
    for kind, items in sorted(STATE['clutter'].items()):
        bm = bmesh.new()
        for size, loc, rot in items:
            part = bmesh.new(); _cube(part, size)
            bmesh.ops.transform(part, matrix=Matrix.LocRotScale(Vector(loc), Euler(rot).to_quaternion(), None), verts=part.verts)
            me = bpy.data.meshes.new('tmp'); part.to_mesh(me); part.free(); bm.from_mesh(me); bpy.data.meshes.remove(me)
        me = bpy.data.meshes.new(f'clutter.{group}.{kind}'); bm.to_mesh(me); bm.free()
        me.materials.append(mat(kind))
        o = _place(f'clutter.{group}.{kind}', me, (0, 0, 0))
        o['studio_scene_role'] = 'clutter'
        o['studio_clutter_count'] = len(items)
    STATE['clutter'] = {}


def _cylinder(bm, r, depth, verts, caps=True):
    bmesh.ops.create_cone(bm, cap_ends=caps, cap_tris=False, segments=verts, radius1=r, radius2=r, depth=depth)


def cyl(name, r, depth, loc, kind, rot=(0, 0, 0), verts=24, caps=True):
    key = ('cyl', round(r, 6), round(depth, 6), verts, caps, kind)
    return _place(name, _shared(key, lambda bm: _cylinder(bm, r, depth, verts, caps), kind, smooth=verts >= 16), loc, rot)


def sphere(name, r, loc, kind, segments=16, rings=8, ico=0):
    key = ('ico', round(r, 6), ico, kind) if ico else ('uv', round(r, 6), segments, rings, kind)
    build = (lambda bm: bmesh.ops.create_icosphere(bm, subdivisions=ico, radius=r)) if ico else \
        (lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=r))
    return _place(name, _shared(key, build, kind, smooth=not ico), loc)


def cone(name, r, depth, loc, kind, rot=(0, 0, 0), verts=16):
    key = ('cone', round(r, 6), round(depth, 6), verts, kind)
    return _place(name, _shared(key, lambda bm: bmesh.ops.create_cone(bm, cap_ends=True, segments=verts, radius1=r, radius2=0, depth=depth), kind),
                  loc, rot)


def text(name, body, loc, size, kind='white_text', extrude=0.05, rot=(math.radians(90), 0, 0), align='CENTER'):
    curve = bpy.data.curves.new(name, 'FONT')
    curve.body = body
    curve.font = bpy.data.fonts.load(str(Path(STATE['library_root']).parent / FONT_FACE), check_existing=True)
    curve.size = size
    curve.extrude = extrude
    curve.align_x = align
    curve.align_y = 'CENTER'
    o = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = rot
    o.data.materials.append(mat(kind))
    o['studio_id'] = name
    o['studio_dim_role'] = 'none'
    o['studio_scene_role'] = 'graphic'  # explanatory, not structure: out of control passes, clay, ray casts
    return o


def worker(name, loc, heading=0.0, vest=True, crouch=False):
    """Simple site worker (~1.75 m): legs, torso with hi-vis vest, arms, head, white helmet."""
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    root.location = loc
    root.rotation_euler = (0, 0, heading)
    h = 0.75 if crouch else 1.0
    parts = [box(f'{name}.leg_l', (0.14, 0.16, 0.85 * h), (-0.1, 0, 0.42 * h), 'workwear'),
             box(f'{name}.leg_r', (0.14, 0.16, 0.85 * h), (0.1, 0, 0.42 * h), 'workwear'),
             box(f'{name}.torso', (0.42, 0.24, 0.62), (0, 0, 0.85 * h + 0.31), 'vest' if vest else 'workwear'),
             box(f'{name}.arm_l', (0.11, 0.12, 0.6), (-0.27, 0.05, 0.85 * h + 0.3), 'workwear', rot=(0.3, 0, 0)),
             box(f'{name}.arm_r', (0.11, 0.12, 0.6), (0.27, 0.12, 0.85 * h + 0.32), 'workwear', rot=(0.9, 0, 0))]
    parts.append(sphere(f'{name}.head', 0.11, (0, 0, 0.85 * h + 0.75), 'skin'))
    hat = sphere(f'{name}.helmet', 0.135, (0, 0, 0.85 * h + 0.8), 'helmet'); hat.scale = (1, 1, 0.7); parts.append(hat)
    for p in parts:
        p.parent = root
        p['studio_id'] = p.name
    return root


def crowd(name, points, seed=0):
    """Background workers (standing / crouching, with and without vest) instanced at points: scenery no label names."""
    kinds = [worker(f'{name}.t{k}', (0, 0, 0), vest=vest, crouch=crouch) for k, (vest, crouch) in
             enumerate(((True, False), (False, False), (True, True)))]
    return scatter_kit(name, kinds, points, seed=seed, scale=(0.94, 1.06))


def car(name, x, y0, y1, frames, color, frame_offset=0):
    """Car driving from y0 to y1 over the frame range (linear), headlights forward (+y if y1 > y0)."""
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    heading = 0.0 if y1 > y0 else math.pi
    root.rotation_euler = (0, 0, heading)
    body = box(f'{name}.body', (1.8, 4.5, 0.7), (0, 0, 0.55), color)
    cabin = box(f'{name}.cabin', (1.6, 2.4, 0.55), (0, -0.2, 1.15), 'glass')
    parts = [body, cabin]
    for i, (wx, wy) in enumerate(((-0.85, 1.4), (0.85, 1.4), (-0.85, -1.4), (0.85, -1.4))):
        parts.append(cyl(f'{name}.wheel{i}', 0.33, 0.25, (wx, wy, 0.33), 'tire', rot=(0, math.radians(90), 0), verts=12))
    parts.append(box(f'{name}.hl', (1.4, 0.05, 0.12), (0, 2.26, 0.65), 'headlight'))
    parts.append(box(f'{name}.tl', (1.4, 0.05, 0.12), (0, -2.26, 0.7), 'taillight'))
    for p in parts:
        p.parent = root
    start, end = frames
    root.location = (x, y0, 0)
    root.keyframe_insert('location', frame=start + frame_offset)
    root.location = (x, y1, 0)
    root.keyframe_insert('location', frame=end + frame_offset)
    _linear(root)
    return root


def _linear(obj):
    from look_scale import _action_fcurves
    if obj.animation_data and obj.animation_data.action:
        for fc in _action_fcurves(obj.animation_data.action):
            for k in fc.keyframe_points:
                k.interpolation = 'LINEAR'


# ---- environments -----------------------------------------------------------------------------------

def city(day=True, y_range=(-150, 450), cars=60, hole=None, frames=(1, 120), max_height=130, keep_clear=None, crossings=(150.0, 330.0),
         sightline=None, bare=None):
    """Yeongdong-daero through the engine's city kit (env_kits.street): 4+4 lanes of 3.4 m (the rest of the 44 m is
    shoulder / bus lane), double yellow centre line, signalised crossings, lit facades, lamps with light pools, bare
    winter trees, people, rooftop plant, signs and traffic. Samsung-only here: the 44 m road and the hole walls
    around the station box. hole=(y0, y1): open excavation (no road, no traffic); keep_clear=(y0, y1): road present
    but no vehicle crosses it (a reveal opens it during the shot); crossings: y of the signalised intersections
    (skipped when they would touch the hole). sightline: env_kits sky rule (camera_hint, keep_sky_v).
    bare=(y0, y1): no lamps, trees or people (ground a section cut removes during the shot)."""
    import env_kits
    y0, y1 = y_range
    length = y1 - y0
    path = [(0.0, float(y0), 0.0), (0.0, float(y1), 0.0)]
    avoid = [(hole[0] - y0, hole[1] - y0)] if hole else []
    clear = [(keep_clear[0] - y0, keep_clear[1] - y0)] if keep_clear else []
    junctions = [{'s': cy - y0} for cy in crossings if y0 + 60 < cy < y1 - 60 and not (hole and hole[0] - 45 < cy < hole[1] + 45)
                 and not (keep_clear and keep_clear[0] < cy < keep_clear[1] + 15)]
    overrides = {'height_m': [min(25, max_height * 0.5), max_height], 'cars_per_100m_lane': round(cars / (length / 100 * 8), 3)}
    env_kits.street('city', path, library_root=STATE['library_root'], road_w_m=ROAD_W, lanes_per_direction=4, sidewalk_w_m=8.0,
                    night=not day, avoid=avoid, keep_clear=clear, frames=frames, seed=STATE['seed'], overrides=overrides,
                    intersections=junctions, sightline=sightline, bare=[(bare[0] - y0, bare[1] - y0)] if bare else ())
    for obj in [o for o in bpy.data.objects if o.name.startswith('city.road.')]:   # authors and reveals name the slab road.N
        obj.name = obj['studio_id'] = obj.name[len('city.'):]
    if hole:
        for side in (-1, 1):
            box(f'hole_wall.{side}', (0.6, hole[1] - hole[0], 2.0), (side * (BOX_W / 2 + 0.3), (hole[0] + hole[1]) / 2, -1.0), 'concrete_dark')
    if not day and STATE['mode'] == 'blockout':
        scene = bpy.context.scene
        bg = scene.world.node_tree.nodes.get('Background')
        bg.inputs['Color'].default_value = (0.20, 0.24, 0.40, 1)
        for o in scene.objects:
            if o.type == 'LIGHT' and o.data.type == 'SUN':
                o.data.energy = 0.6
                o.data.color = (1.0, 0.6, 0.35)
    flush_clutter('city')


def station_box(y0=60.0, cutaway=True, atrium=True, tracks=True, lights=True, holo=False, bright=False):
    """Five-level underground box under the road: slabs, column rows, central atrium with escalators,
    GTX platform + tracks at B5. cutaway: the -Y end wall is open so the levels read as a section.
    bright: light interior surfaces for a section seen from outside (engine section.stage lights it)."""
    interior = {'concrete': 'interior_concrete', 'slab_edge': 'interior_slab'} if bright else {}
    kind = (lambda k: 'holo') if holo else (lambda k: interior.get(k, k))
    depth = LEVELS * LEVEL_H
    yc = y0 + BOX_L / 2
    # outer walls
    box('st.wall_l', (1.2, BOX_L, depth), (-BOX_W / 2, yc, -depth / 2), kind('concrete_dark'))
    box('st.wall_r', (1.2, BOX_L, depth), (BOX_W / 2, yc, -depth / 2), kind('concrete_dark'))
    box('st.base', (BOX_W, BOX_L, 1.5), (0, yc, -depth - 0.75), kind('concrete_dark'))
    if not cutaway:
        box('st.end', (BOX_W, 1.2, depth), (0, y0, -depth / 2), kind('concrete_dark'))
    box('st.end_far', (BOX_W, 1.2, depth), (0, y0 + BOX_L, -depth / 2), kind('concrete_dark'))
    atrium_w = 9.0 if atrium else 0.0
    for lvl in range(LEVELS):
        z = -lvl * LEVEL_H
        for side in (-1, 1):
            w = (BOX_W - atrium_w) / 2
            x = side * (atrium_w / 2 + w / 2)
            if lvl > 0 or not atrium:
                box(f'st.slab{lvl}.{side}', (w, BOX_L, 0.8), (x, yc, z - 0.4), kind('slab_edge'))
            for j in range(int(BOX_L / 9)):
                yy = y0 + 6 + j * 9
                cx = side * (atrium_w / 2 + 1.0)
                box(f'st.col{lvl}.{side}.{j}', (1.0, 1.0, LEVEL_H - 0.8), (cx, yy, z - 0.8 - (LEVEL_H - 0.8) / 2), kind('concrete'))
                if lights and lvl > 0 and j % 1 == 0 and not (STATE['detail'] and not holo):
                    box(f'st.light{lvl}.{side}.{j}', (w * 0.7, 0.18, 0.06), (x, yy + 4.5, z - 0.85), 'light' if not holo else 'holo')
            if STATE['detail'] and not holo and lvl > 0:  # girders under the slab above, linear light rows, from exemplars
                exemplar('beam_grid_ceiling', (x, y0 + 6, z + LEVEL_H - 1.2) if lvl > 1 else (x, y0 + 6, -1.2),
                         rot_z_deg=0, edit=girders(w, int(BOX_L / 9), 9.0))
                for k in (-1, 1):
                    exemplar('light_row', (x + k * w / 4, y0 + 5, z + LEVEL_H - 1.7 if lvl > 1 else -1.7), edit=run_length(['fixtures'], BOX_L - 10, axis=1))
        if atrium and lvl < LEVELS - 1 and lvl > 0:
            for side in (-1, 1):  # glass balustrade on atrium edge
                if STATE['detail'] and not holo:
                    exemplar('glass_railing', (side * atrium_w / 2, y0 + 1, z), rot_z_deg=90,
                             edit=run_length(['posts', 'panels', 'top_rail'], BOX_L - 2.5))
                else:
                    box(f'st.rail{lvl}.{side}', (0.05, BOX_L, 1.1), (side * atrium_w / 2, yc, z + 0.55), kind('glass'))
    if atrium:  # escalator pairs down the atrium
        for lvl in range(1, LEVELS - 1):
            z_top = -lvl * LEVEL_H
            for side in (-1, 1):
                if STATE['detail'] and not holo:  # S1000 escalator exemplar: 7 m rise at 30 deg = one storey
                    exemplar('escalator', (side * 2.4, y0 + 30 + lvl * 4 - LEVEL_H / math.tan(math.radians(30)) / 2, z_top - LEVEL_H))
                    continue
                esc = box(f'st.esc{lvl}.{side}', (1.6, 14.0, 0.6), (side * 2.4, y0 + 30 + lvl * 4, z_top - LEVEL_H / 2), kind('steel'),
                          rot=(math.atan2(LEVEL_H, 14.0), 0, 0))
    if tracks:
        zb = -depth
        box('st.platform', (10.0, BOX_L - 10, 1.1), (0, yc, zb + 0.55), kind('floor_tile'))
        for side in (-1, 1):
            box(f'st.edge{side}', (0.35, BOX_L - 10, 0.02), (side * 4.6, yc, zb + 1.11), kind('safety_line'))
            if STATE['detail'] and not holo:  # KS 50N rails at 1435 mm on PC sleepers (track exemplar)
                exemplar('track', (side * 8.5, y0, zb), edit=run_length(['rail_l', 'sleepers'], BOX_L - 0.5, axis=1))
                continue
            for r in (-0.75, 0.75):
                box(f'st.rail{side}{r}', (0.08, BOX_L, 0.16), (side * 8.5 + r, yc, zb + 0.1), kind('rail'))
            for k in range(0, int(BOX_L), 2):
                box(f'st.sleeper{side}.{k}', (2.6, 0.3, 0.12), (side * 8.5, y0 + k, zb + 0.02), kind('sleeper'))
    return {'y0': y0, 'depth': depth}


def column_hall(n_rows=2, n_cols=9, spacing=9.0, width=1.1, height=7.0, round_cols=False, capitals=True,
                beams=True, floor='floor_tile', ceiling_lights=True, origin=(0, 0, 0), gap=12.0):
    """A platform-level hall: rows of columns (square or round) along +Y, beams, slab, floor."""
    ox, oy, oz = origin
    L = n_cols * spacing + 6
    box('hall.floor', ((n_rows - 1) * gap + 30, L + 20, 0.4), (ox, oy + L / 2, oz - 0.2), floor)
    box('hall.ceiling', ((n_rows - 1) * gap + 30, L + 20, 0.8), (ox, oy + L / 2, oz + height + 0.4), 'concrete')
    cols = []
    for r in range(n_rows):
        x = ox + (r - (n_rows - 1) / 2) * gap
        for c in range(n_cols):
            y = oy + 3 + c * spacing
            if round_cols:
                o = cyl(f'hall.col.{r}.{c}', width / 2, height, (x, y, oz + height / 2), 'concrete', verts=32)
            else:
                o = box(f'hall.col.{r}.{c}', (width, width, height), (x, y, oz + height / 2), 'concrete')
            cols.append(o)
            if capitals:
                box(f'hall.cap.{r}.{c}', (width * 1.8, width * 1.8, 0.5), (x, y, oz + height - 0.25), 'concrete')
        if beams:
            box(f'hall.beam.{r}', (0.9, L, 1.0), (x, oy + L / 2, oz + height - 0.5), 'concrete')
    if beams:
        for c in range(n_cols):
            box(f'hall.xbeam.{c}', ((n_rows - 1) * gap + 30, 0.7, 0.8), (ox, oy + 3 + c * spacing, oz + height - 0.4), 'concrete')
    if ceiling_lights:
        for r in range(n_rows + 1):
            x = ox + (r - n_rows / 2) * gap
            if STATE['detail']:
                exemplar('light_row', (x, oy + 2, oz + height - 1.05), edit=run_length(['fixtures'], L - 4, axis=1))
            else:
                box(f'hall.lightstrip.{r}', (0.15, L, 0.05), (x, oy + L / 2, oz + height - 1.05), 'light')
    if STATE['detail']:  # secondary girders between the column lines and a track beside each outer row
        for c in range(n_cols - 1):
            exemplar('beam_grid_ceiling', (ox, oy + 3 + (c + 0.5) * spacing, oz + height - 0.2), rot_z_deg=0,
                     edit=girders((n_rows - 1) * gap + 28, 1, 1.0))
        for side in (-1, 1):
            exemplar('track', (ox + side * ((n_rows - 1) * gap / 2 + 6.5), oy - 8, oz), edit=run_length(['rail_l', 'sleepers'], L + 16, axis=1))
    return cols


def rebar_column(name, origin, size=1.2, height=6.0, rows=2, cut=True, bar_r=0.024, spiral=False):
    """Concrete column with its main bars and ties exposed: cut=True removes the front half of the
    concrete so the cage reads. rows=2 is the design (outer + inner ring), rows=1 as built."""
    ox, oy, oz = origin
    if cut:
        box(f'{name}.conc_back', (size, size / 2, height), (ox, oy + size / 4, oz + height / 2), 'concrete')
    else:
        box(f'{name}.conc', (size, size, height), (ox, oy, oz + height / 2), 'concrete')
    cover = 0.06
    bars = []
    for ring in range(rows):
        inset = cover + ring * 0.09
        half = size / 2 - inset
        n = 5 if ring == 0 else 4
        for i in range(n):
            # inner ring staggered half a spacing so a front view shows the doubled bar count
            t = -half + 2 * half * i / 4 + (half / 4 if ring else 0)
            for px, py in ((t, -half), (t, half), (-half, t), (half, t)):
                if cut and py > 0.01 and abs(px) < half - 0.01:
                    continue
                bars.append(cyl(f'{name}.bar{ring}.{len(bars)}', bar_r, height - 0.2, (ox + px, oy + py, oz + height / 2), 'rebar', verts=8))
    tie_half = size / 2 - cover + 0.012
    for k in range(int(height / 0.45)):
        z = oz + 0.2 + k * 0.45
        if spiral:
            continue
        for (sx, sy, lx, ly) in ((0, -tie_half, 2 * tie_half, 0.02), (-tie_half, 0, 0.02, 2 * tie_half), (tie_half, 0, 0.02, 2 * tie_half)):
            box(f'{name}.tie{k}.{sx}.{sy}', (max(lx, 0.02), max(ly, 0.02), 0.02), (ox + sx, oy + sy, z), 'rebar')
    if spiral:
        pts = [(ox + tie_half * math.cos(t), oy + tie_half * math.sin(t), oz + 0.2 + (height - 0.4) * t / (2 * math.pi * height / 0.25))
               for t in [i * 0.2 for i in range(int(2 * math.pi * height / 0.25 / 0.2))]]
        curve = bpy.data.curves.new(f'{name}.spiral', 'CURVE'); curve.dimensions = '3D'; curve.bevel_depth = 0.012
        spl = curve.splines.new('POLY'); spl.points.add(len(pts) - 1)
        for p, (x, y, z) in zip(spl.points, pts):
            p.co = (x, y, z, 1)
        o = bpy.data.objects.new(f'{name}.spiral', curve); bpy.context.scene.collection.objects.link(o); o.data.materials.append(mat('rebar'))
    return bars


def arrow(name, loc, length=1.6, kind='red', down=True):
    shaft = box(f'{name}.shaft', (0.12, 0.12, length), (loc[0], loc[1], loc[2] + (length / 2 if down else -length / 2)), kind)
    head = cone(f'{name}.head', 0.28, 0.5, loc, kind, rot=(math.pi if down else 0, 0, 0))
    for part in (shaft, head):
        part['studio_scene_role'] = 'graphic'
    return shaft, head


def tunnel(length=200.0, r=3.6, origin=(0, 0, 0), segments_per_ring=8, ring_w=1.5):
    """Shield-TBM tunnel along +Y: segmented lining rings (with joints), track bed, rails, cable trays."""
    ox, oy, oz = origin
    ring = cyl('tun.ring', r + 0.3, ring_w - 0.04, (0, 0, 0), 'concrete', rot=(math.radians(90), 0, 0), verts=48, caps=False)
    ring.modifiers.new('hollow', 'SOLIDIFY').thickness = 0.35  # open tube: the lining only
    rings = [(ox, oy + k * ring_w + ring_w / 2, oz + r) for k in range(int(length / ring_w))]
    scatter_kit('tunnel_rings', [ring], rings, seed=0, rotation_deg=(0.0, 0.0))
    for k in range(int(length / ring_w)):
        if k % 4 == 0:
            box(f'tun.lamp{k}', (0.1, 0.8, 0.12), (ox + r * 0.75, oy + k * ring_w, oz + r * 1.55), 'light')
    box('tun.bed', (r * 1.5, length, 0.6), (ox, oy + length / 2, oz + 0.3), 'concrete_dark')
    for xr in (-0.75, 0.75):
        box(f'tun.rail{xr}', (0.08, length, 0.16), (ox + xr, oy + length / 2, oz + 0.68), 'rail')
    scatter_kit('tunnel_sleepers', [box('tun.sl', (2.4, 0.25, 0.1), (0, 0, 0), 'sleeper')],
                [(ox, oy + k, oz + 0.62) for k in range(0, int(length), 1)], seed=0, rotation_deg=(0.0, 0.0))
    for i, zz in enumerate((1.8, 2.0, 2.2)):
        cyl(f'tun.cable{i}', 0.05, length, (ox + r * 0.85, oy + length / 2, oz + zz), 'rebar' if i == 0 else 'steel', rot=(math.radians(90), 0, 0), verts=8)


def office(origin=(0, 0, 0)):
    ox, oy, oz = origin
    box('off.floor', (16, 18, 0.2), (ox, oy + 6, oz - 0.1), 'concrete')
    box('off.ceiling', (16, 18, 0.3), (ox, oy + 6, oz + 4.2), 'concrete')
    for x in (-8, 8):
        box(f'off.wall{x}', (0.3, 18, 4.2), (ox + x, oy + 6, oz + 2.1), 'concrete')
    box('off.back_l', (5, 0.3, 4.2), (ox - 5.5, oy + 15, oz + 2.1), 'concrete'); box('off.back_r', (5, 0.3, 4.2), (ox + 5.5, oy + 15, oz + 2.1), 'concrete')
    box('off.back_top', (6, 0.3, 1.4), (ox, oy + 15, oz + 3.5), 'concrete'); box('off.window', (6, 0.05, 2.2), (ox, oy + 15, oz + 1.7), 'glass')
    for k in range(5):
        box(f'off.beam{k}', (16, 0.3, 0.45), (ox, oy + k * 3.5, oz + 3.95), 'steel')
        box(f'off.lamp{k}', (0.18, 2.2, 0.06), (ox - 3, oy + k * 3.5 + 1.5, oz + 3.3), 'light')
        box(f'off.lamp{k}b', (0.18, 2.2, 0.06), (ox + 3, oy + k * 3.5 + 1.5, oz + 3.3), 'light')
    for i, (dx, dy) in enumerate(((0, 9.5), (2.5, 3.0))):
        box(f'off.desk{i}', (2.4, 1.2, 0.05), (ox + dx, oy + dy, oz + 0.75), 'office_white')
        for lx in (-1.1, 1.1):
            box(f'off.desk{i}.leg{lx}', (0.05, 1.1, 0.72), (ox + dx + lx, oy + dy, oz + 0.37), 'office_dark')
        box(f'off.mon{i}', (0.7, 0.04, 0.42), (ox + dx, oy + dy + 0.35, oz + 1.05), 'screen_glow')
        box(f'off.chair{i}', (0.55, 0.55, 0.5), (ox + dx, oy + dy - 0.8, oz + 0.5), 'office_dark')
    box('off.binder', (0.5, 0.35, 0.04), (ox + 2.1, oy + 2.7, oz + 0.8), 'office_white')
    for i, x in enumerate((-7.2, 7.2)):
        for j in range(3):
            box(f'off.cab{i}{j}', (0.6, 1.0, 2.0), (ox + x, oy + 4 + j * 2.2, oz + 1.0), 'office_dark')
    box('off.printer', (0.7, 0.6, 1.1), (ox - 6.6, oy + 1.0, oz + 0.55), 'office_white')


def excavation(origin=(0, 0, 0), depth=40.0, w=24.0, l=30.0):
    """Open-cut shaft: soldier-pile retaining walls, timber lagging, steel struts, rubble floor."""
    rng = random.Random(STATE['seed'])
    ox, oy, oz = origin
    for side in (-1, 1):
        box(f'exc.wall{side}', (0.4, l, depth), (ox + side * w / 2, oy + l / 2, oz - depth / 2), 'timber')
        for k in range(int(l / 2.5)):
            box(f'exc.pile{side}.{k}', (0.35, 0.35, depth), (ox + side * (w / 2 - 0.3), oy + 1 + k * 2.5, oz - depth / 2), 'steel')
    box('exc.back', (w, 0.4, depth), (ox, oy + l, oz - depth / 2), 'timber')
    for lvl in range(1, int(depth / 8)):
        for k in range(4):
            box(f'exc.strut{lvl}.{k}', (w, 0.5, 0.5), (ox, oy + 4 + k * 7, oz - lvl * 8), 'steel')
    rocks = []
    for i in range(160):  # instanced, so the floor can be properly covered
        rng.uniform(0.3, 1.0)
        rocks.append((ox + rng.uniform(-w / 2, w / 2), oy + rng.uniform(0, l), oz - depth + 0.2))
    scatter_kit('rubble', [sphere(f'exc.rock_t{k}', size, (0, 0, 0), 'rubble', ico=1) for k, size in enumerate((0.35, 0.6, 0.95))],
                rocks, seed=STATE['seed'], scale=(0.6, 1.4))
    box('exc.floor', (w, l, 0.5), (ox, oy + l / 2, oz - depth - 0.25), 'rubble')


# ---- camera ----------------------------------------------------------------------------------------

def camera(keys, sensor=36.0, dof=None):
    """keys: [(frame, (x, y, z), (tx, ty, tz), lens_mm)], Bezier between keys; frame 1-based."""
    scene = bpy.context.scene
    cam = bpy.data.objects.new('camera', bpy.data.cameras.new('camera'))
    scene.collection.objects.link(cam)
    cam.data.sensor_width = sensor
    cam.data.clip_start = 0.05
    cam.data.clip_end = 2000
    scene.camera = cam
    for frame, loc, target, lens in keys:
        cam.location = loc
        direction = Vector(target) - Vector(loc)
        cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
        cam.keyframe_insert('location', frame=frame)
        cam.keyframe_insert('rotation_euler', frame=frame)
        cam.data.lens = lens
        cam.data.keyframe_insert('lens', frame=frame)
    from look_scale import _action_fcurves
    for data in (cam, cam.data):
        if data.animation_data and data.animation_data.action:
            for fc in _action_fcurves(data.animation_data.action):
                for k in fc.keyframe_points:
                    k.interpolation = 'BEZIER'
                    k.easing = 'AUTO'
    return cam


def photoreal_lamps(points, energy=2500, size=2.0):
    """Area fill lamps for underground interiors so emissive practicals are not the only light
    (blockout gets a third of the power: its flat world already lifts the shadows)."""
    if STATE['mode'] != 'photoreal':
        energy = energy / 3
    for i, p in enumerate(points):
        light = bpy.data.lights.new(f'fill{i}', 'AREA')
        light.energy = energy
        light.size = size
        o = bpy.data.objects.new(f'fill{i}', light)
        bpy.context.scene.collection.objects.link(o)
        o.location = p
        o.rotation_euler = (0, 0, 0)


def underground(center=(0, 0, 0), size=(400, 400, 200)):
    """Earth around an underground interior: a closed inward-facing shell so no sky or HDRI backdrop
    shows through open model edges (the look preset's world then only lights what it can reach).
    Photoreal only: the blockout lights interiors with its flat world, which the shell would block."""
    if STATE['mode'] != 'photoreal':
        return None
    o = unique_data(box('earth', size, center, 'concrete_dark'))
    for p in o.data.polygons:
        p.flip()
    o['studio_scene_role'] = 'environment_shell'  # not part of what is lit, measured or perfected
    return o
