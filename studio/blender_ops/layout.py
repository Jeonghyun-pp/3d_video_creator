"""Build a resolved declarative scene (studio/layout.py) before any author script.

Fixed order: world -> volumes -> kits (a street's sky sightline can come from the shot's camera move) -> instances
(pinned, edited exemplar specs) -> primitives (shared box meshes, one per size and material) -> lights -> levels ->
section (cut stage, front cutter, copied materials) -> bind (an object becomes a reveal or action target) ->
visibility keys. Nothing here is specific to one production: a new topic is new data.
"""
import json
import math

import bmesh
import bpy
from mathutils import Vector

STATE = {'materials': {}, 'meshes': {}}


def _world(spec, photoreal):
    """The blockout world (flat look): a photoreal look preset owns world and lights, so it is skipped there."""
    if (spec or {}).get('kind', 'none') != 'blockout' or photoreal:
        return
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = spec.get('samples', 16)
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 3
    if spec.get('fast_gi_m'):
        scene.cycles.use_fast_gi = True
    scene.render.use_motion_blur = False
    scene.view_settings.view_transform = 'Standard'
    world = scene.world or bpy.data.worlds.new('World')
    scene.world = world
    if spec.get('fast_gi_m'):
        world.light_settings.distance = spec['fast_gi_m']
    world.use_nodes = True
    background = world.node_tree.nodes.get('Background')
    background.inputs['Color'].default_value = (*spec.get('color', (0.55, 0.62, 0.72)), 1)
    background.inputs['Strength'].default_value = spec.get('strength', 0.5)
    if spec.get('sun'):
        sun = bpy.data.objects.new('sun', bpy.data.lights.new('sun', 'SUN'))
        scene.collection.objects.link(sun)
        sun.data.energy = spec['sun'].get('energy', 2.5)
        sun.data.color = spec['sun'].get('color', (1, 1, 1))
        sun.rotation_euler = tuple(math.radians(a) for a in spec['sun'].get('rot_deg', (40, 15, 30)))


def material(kind, kinds, photoreal, library_root):
    """The material for a kind name: the catalog (photoreal looks), an emissive, or a flat principled colour."""
    if kind in STATE['materials']:
        return STATE['materials'][kind]
    spec = kinds.get(kind, {})
    if photoreal and spec.get('catalog') and not spec.get('emission'):
        from look_materials import make_material
        made = make_material(f'layout_{kind}', spec['catalog'], library_root=library_root, overrides=spec.get('catalog_overrides') or None)
    else:
        made = bpy.data.materials.new(f'layout_{kind}')
        bsdf = made.node_tree.nodes.get('Principled BSDF')
        color = spec.get('emission', {}).get('color') or spec.get('color', (0.6, 0.6, 0.6))
        bsdf.inputs['Base Color'].default_value = (*color, 1)
        bsdf.inputs['Roughness'].default_value = spec.get('roughness', 0.85)
        if photoreal:
            bsdf.inputs['Metallic'].default_value = spec.get('metallic', 0.0)
            bsdf.inputs['Transmission Weight'].default_value = spec.get('transmission', 0.0)
        if spec.get('emission'):
            bsdf.inputs['Emission Color'].default_value = (*spec['emission']['color'], 1)
            bsdf.inputs['Emission Strength'].default_value = spec['emission']['strength']
        if 'alpha' in spec:
            bsdf.inputs['Alpha'].default_value = spec['alpha']
        made.diffuse_color = (*color, 1)
    STATE['materials'][kind] = made
    return made


def _box_mesh(size, mat):
    key = (tuple(round(v, 6) for v in size), mat.name)
    if key not in STATE['meshes']:
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=size, verts=bm.verts)
        mesh = bpy.data.meshes.new(f'layout_box_{len(STATE["meshes"])}')
        bm.to_mesh(mesh); bm.free()
        mesh.materials.append(mat)
        STATE['meshes'][key] = mesh
    return STATE['meshes'][key]


def _visibility(obj, visible):
    for o in [obj, *obj.children_recursive]:
        if 'from' in visible:
            o.hide_render = True; o.keyframe_insert('hide_render', frame=1)
            o.hide_render = False; o.keyframe_insert('hide_render', frame=visible['from'] + 1)
        if 'until' in visible:
            o.hide_render = True; o.keyframe_insert('hide_render', frame=visible['until'] + 1)


def _sightline(kit, job, volumes):
    """A street's sky rule seen from where the camera move starts (env_kits sightline)."""
    move = job['shot']['camera'].get('move')
    if not (kit.get('sightline') or {}).get('from_move') or not move:
        return None
    import camera_moves_core
    start = camera_moves_core.plan(move, {'points': {}, 'boxes': volumes})['waypoints'][0]
    framing = move.get('framing', {})
    forward = kit['sightline'].get('forward') or (0.0, 1.0)
    return {'camera': start, 'forward': tuple(forward), 'horizon_v': framing.get('horizon_v', 0.40),
            'keep_sky_v': kit['sightline'].get('keep_sky_v', 0.33), 'lens_mm': move.get('lens_mm', 24)}


def _decal(row):
    """A card size_m wide (height from the image) at `at`, facing `normal`, optionally parented to an object (`on`) so it
    moves with it; the image's alpha cuts the card; `glow` makes a lit sign emit its own colours."""
    from mathutils import Vector
    from scene_index import resolve_group
    width = row['size_m']
    height = width * row['aspect']
    mesh = bpy.data.meshes.new(row['id'])
    mesh.from_pydata([(-width / 2, 0, -height / 2), (width / 2, 0, -height / 2), (width / 2, 0, height / 2), (-width / 2, 0, height / 2)], [], [(0, 1, 2, 3)])
    mesh.uv_layers.new(name='UVMap')
    for loop, uv in zip(mesh.uv_layers[0].data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = uv
    mat = bpy.data.materials.new(f"{row['id']}/decal")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = bpy.data.images.load(row['image_path'], check_existing=True)
    tex.image.pack()
    nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    nt.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
    bsdf.inputs['Roughness'].default_value = row.get('roughness', 0.55)
    if row.get('glow'):
        nt.links.new(tex.outputs['Color'], bsdf.inputs['Emission Color'])
        bsdf.inputs['Emission Strength'].default_value = float(row['glow'])
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(row['id'], mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = tuple(row['at'])
    normal = Vector(row.get('normal', (0, -1, 0))).normalized()
    obj.rotation_euler = normal.to_track_quat('-Y', 'Z').to_euler()   # the card's front (-Y) faces along the normal
    obj['studio_id'], obj['studio_scene_role'], obj['studio_dim_role'] = row['id'], 'decal', 'none'
    if row.get('on'):
        found = resolve_group(row['on'])
        if not found:
            raise ValueError(f"LAYOUT: decal {row['id']} is on {row['on']}, which the scene did not make")
        bpy.context.view_layer.update()   # matrix_world is stale until the depsgraph has seen the new location
        world = obj.matrix_world.copy()
        obj.parent = found[0]
        obj.matrix_world = world
    return obj


def build(job, scene_spec):
    """Build the resolved scene; returns the report written to layout_report.json."""
    STATE['materials'], STATE['meshes'] = {}, {}
    scene = bpy.context.scene
    look = job['shot']['render'].get('look_preset') or 'flat_stylized'
    photoreal = look.startswith('photoreal')
    kinds = scene_spec.get('materials', {})
    library_root = job['library_root']
    report = {'schema_version': 1, 'built': {}, 'photoreal_materials': photoreal}
    _world(scene_spec.get('world'), photoreal)
    volumes = {}
    for volume in scene_spec.get('volumes', []):
        lo, hi = volume['box']
        marker = bpy.data.objects.new(volume['id'], None)
        marker.empty_display_type = 'CUBE'; marker.empty_display_size = 1
        marker.location = tuple((lo[i] + hi[i]) / 2 for i in range(3)); marker.scale = tuple((hi[i] - lo[i]) / 2 for i in range(3))
        marker['studio_id'] = volume['id']
        scene.collection.objects.link(marker)
        volumes[volume['id']] = (tuple(lo), tuple(hi))
    for kit in scene_spec.get('kits', []):
        import env_kits
        args = dict(kit.get('args', {}))
        sightline = _sightline(kit, job, volumes)
        if sightline:
            args['sightline'] = sightline
        args.setdefault('frames', (1, job['shot']['duration_frames']))
        import env_kits_space
        kits = {'street': env_kits.street, 'hall': env_kits_space.hall, 'strata': env_kits_space.strata,
                'vegetation': env_kits_space.vegetation, 'water': env_kits_space.water}   # the host's KITS (studio/layout.py) lists the same
        fn = kits[kit['kit']]
        made = fn(kit['id'], library_root=library_root, **args) if 'library_root' in fn.__code__.co_varnames else fn(kit['id'], **args)
        report.setdefault('kits', []).append({'id': kit['id'], **(made if isinstance(made, dict) else {})})
    from modeling import build_subject
    for row in scene_spec.get('instances', []):
        root = build_subject(row['spec'], root_location=tuple(row['at']))['root']
        root.rotation_euler.z = math.radians(row.get('rot_z_deg', 0.0))
        root['studio_layout_id'] = row['id']
        if row.get('visible'):
            _visibility(root, row['visible'])
    for row in scene_spec.get('links', []):   # appended, not linked: the version keeps its own copy (path + hash in the layout)
        with bpy.data.libraries.load(row['path'], link=False) as (src, dst):
            if row['name'] not in getattr(src, row['data_type']):
                raise ValueError(f"LAYOUT: {row['file']} has no {row['data_type']} {row['name']!r}")
            setattr(dst, row['data_type'], [row['name']])
        block = getattr(dst, row['data_type'])[0]
        if row['data_type'] == 'collections':
            root = bpy.data.objects.new(row['id'], None)
            root.instance_type, root.instance_collection = 'COLLECTION', block
            scene.collection.objects.link(root)
        else:
            root = block
            scene.collection.objects.link(root)
        root.location = Vector(root.location) + Vector(row.get('at', (0, 0, 0)))
        root.rotation_euler.z += math.radians(row.get('rot_z_deg', 0.0))
        root['studio_id'] = row['id']
    relation = (scene_spec.get('backdrop') or {}).get('relation') or {}
    if relation.get('support') and relation['support']['kind'] != 'none':
        _support(relation['support'], relation.get('view') or {}, kinds, photoreal, library_root)
    for row in scene_spec.get('primitives', []):
        mat = material(row['material'], kinds, photoreal, library_root)
        mesh = _box_mesh(row['size'], mat)
        if row.get('flip_normals'):
            mesh = mesh.copy()
            for polygon in mesh.polygons:
                polygon.flip()
        obj = bpy.data.objects.new(row['id'], mesh)
        scene.collection.objects.link(obj)
        obj.location = tuple(row['at'])
        obj.rotation_euler = tuple(math.radians(a) for a in row.get('rot_deg', (0, 0, 0)))
        obj['studio_id'] = row['id']
        obj['studio_dim_role'] = 'none'
        if row.get('role'):
            obj['studio_scene_role'] = row['role']
        if row.get('visible'):
            _visibility(obj, row['visible'])
    for row in scene_spec.get('lights', []):
        light = bpy.data.lights.new(row['id'], row['type'])
        light.energy = row['energy']
        if row.get('size') is not None and hasattr(light, 'size'):
            light.size = row['size']
        if row.get('color'):
            light.color = row['color']
        obj = bpy.data.objects.new(row['id'], light)
        scene.collection.objects.link(obj)
        obj.location = tuple(row['at'])
        obj.rotation_euler = tuple(math.radians(a) for a in row.get('rot_deg', (0, 0, 0)))
    bpy.context.view_layer.update()   # world matrices of everything placed above, before passes that read them (section poché)
    if scene_spec.get('levels'):
        import fill_brief
        fill_brief.declare_levels(scene_spec['levels'])
    section_spec = scene_spec.get('section')
    if section_spec:
        import section
        staged = section.stage(section_spec['id'], volumes[section_spec['box']], ceilings=section_spec.get('ceilings', ()),
                               **section_spec.get('options', {}))
        scene['studio_section'] = str(staged)
        cutter = section_spec.get('front_cutter')
        if cutter:
            section.front_cutter(cutter['id'], volumes[section_spec['box']][0][1], reach_m=cutter['reach_m'], half_w_m=cutter['half_w_m'],
                                 z_lo=cutter['z_lo'], z_hi=cutter['z_hi'])
        for copy in section_spec.get('copy_materials', []):
            made = bpy.data.materials[copy['from']].copy(); made.name = copy['id']
    for row in scene_spec.get('bind', []):
        obj = bpy.data.objects.get(row['select'])
        if obj is None:
            raise ValueError(f"LAYOUT: bind selects {row['select']}, which the scene did not make")
        obj['studio_instance_id'], obj['studio_part_id'] = row['instance_id'], row['part_id']
    for row in scene_spec.get('decals', []):   # words on a surface: a lit card, role decal (studio/decals.py drew the image)
        _decal(row)
    if scene_spec.get('characters'):   # library people, each with its own rig, phase and walk (blender_ops/characters.py)
        import characters
        report['characters'] = characters.build(scene_spec['characters'], job['shot']['duration_frames'], job['fps'])
    report['built'] = {key: len(scene_spec.get(key, [])) for key in ('volumes', 'kits', 'instances', 'primitives', 'lights', 'bind', 'characters')}
    report['objects'] = len(bpy.data.objects)
    scene['studio_layout'] = json.dumps(report['built'])
    return report


def _support(support, view, kinds, photoreal, library_root):
    """What the subject stands on (backdrop.relation.support): a bench top or a floor whose top is the lowest point of the
    instances - scene geometry, so camera moves and rig guards see it and the subject gets its contact shadow and
    reflection. It reaches `margin` subject sizes toward the view and to the sides, but only `behind` sizes away from it:
    the place (the backdrop) shows past its back edge, as in a product shot on the edge of a bench (measured on
    robot_joint: a bench even on all sides filled a 30-degree macro frame and hid the backdrop)."""
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    corners = [o.matrix_world @ Vector(c) for o in scene.objects if o.type == 'MESH' and o.get('studio_subject_id') for c in o.bound_box]
    if not corners:
        raise ValueError('LAYOUT: backdrop.relation.support needs instances to stand on it')
    lo = Vector([min(c[i] for c in corners) for i in range(3)]); hi = Vector([max(c[i] for c in corners) for i in range(3)])
    size = max(hi.x - lo.x, hi.y - lo.y)
    margin = float(support.get('margin', 3.0)) * (12 if support['kind'] == 'floor' else 1)
    behind = float(support.get('behind', 0.6))
    thick = size * (0.08 if support['kind'] == 'bench' else 0.02)
    width, depth = size * (1 + 2 * margin), size * (1 + margin + behind)
    mesh = _box_mesh((width, depth, thick), material(support.get('material') or '_support', kinds, photoreal, library_root))
    obj = bpy.data.objects.new('StudioSupport', mesh)
    scene.collection.objects.link(obj)
    # local +y points away from the view (azimuth 0 = camera on -Y): shift the box toward the camera by (margin - behind) / 2
    a = math.radians(float(view.get('azimuth_deg', 0.0)))
    away = Vector((math.sin(a), -math.cos(a), 0.0)) * -1
    centre = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z - thick / 2)) - away * size * (margin - behind) / 2
    obj.location = centre
    obj.rotation_euler.z = a
    obj['studio_id'] = 'support'
    obj['studio_dim_role'] = 'none'   # a bench or floor has no catalogued real size (the scale audit read 'Stud' in its name as a stud)
    return obj
