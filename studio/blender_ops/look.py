"""Build-time look orchestration: one preset name -> scale audit, surface imperfection,
camera realism, lighting and compositor, in a fixed order, with one deterministic report.

flat_stylized does nothing, so stylized shots (Otis) stay byte-identical. When the inputs
of a base version are unchanged the passes are skipped, so preserve checks pass naturally.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy

import preserve
from code_closure import module_closure

HERE = Path(__file__).parent
DATA = HERE / 'look_data'
MODULES = module_closure('look.py')   # every file the look runs (code_closure.py), hashed into its inputs
CLAY_COLORS = {'mechanical': (0.80, 0.42, 0.30), 'structure': (0.62, 0.62, 0.64), 'subject': (0.30, 0.52, 0.80), None: (0.55, 0.55, 0.55)}
ALWAYS_FATAL = ('hdri provenance', 'guard', 'camera_lens_distortion')   # licence, mechanical contact, labels off their anchors


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def presets():
    return json.loads((DATA / 'look_presets.json').read_text())['presets']


def resolve_preset(job):
    shot, style = job['shot'], job.get('style') or {}
    return shot['render'].get('look_preset') or (style.get('look') or {}).get('preset') or 'flat_stylized'


def inputs_hash(job, name, spec):
    style = job.get('style') or {}
    payload = {'preset': name, 'spec': spec, 'style': {k: style.get(k) for k in ('look', 'light_rig', 'world')},
               'realism': job['shot']['camera'].get('realism'), 'labels': sorted(l['anchor'] for l in job['shot'].get('labels', [])),
               'previs': ((job['shot'].get('route') or {}).get('generative') or {}).get('previs'),
               'atmosphere': job['shot']['render'].get('atmosphere'),
               'exposure_keys': exposure_keys(job),
               'code': {m: _sha(HERE / m) for m in MODULES},
               'data': {p.name: _sha(p) for p in sorted(DATA.glob('*.json'))}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def exposure_keys(job):
    """shot.render.exposure_keys resolved to frames: [{frame, transition_frames}] ([] when none). A key names a frame
    or a camera cue (+ offset); the camera cues come from the compiled move (build_scene puts them in the job)."""
    out = []
    for key in job['shot']['render'].get('exposure_keys') or []:
        if 'frame' in key:
            frame = key['frame']
        else:
            cues = job.get('camera_cues') or {}
            if key['cue'] not in cues:
                raise ValueError(f"LOOK_QA_FAILED: exposure key cue {key['cue']!r} is not a camera cue (known: {sorted(cues)})")
            frame = cues[key['cue']] + key.get('offset_frames', 0)
        out.append({'frame': max(0, min(job['shot']['duration_frames'] - 1, frame)), 'transition_frames': key.get('transition_frames', 16)})
    return out


def scene_state_sha256(scene):
    objects = {o.name: preserve.object_state(o) for o in sorted(scene.objects, key=lambda o: o.name)}
    world = preserve.tree_state(scene.world.node_tree) if scene.world and scene.world.node_tree else None
    view = {k: getattr(scene.view_settings, k) for k in ('view_transform', 'look', 'exposure', 'gamma')}
    return preserve.digest({'objects': objects, 'world': world, 'view': view})


def _revert(scene):
    import look_bake, look_camera, look_lighting, look_perfection
    look_bake.revert_bake(scene)
    look_camera.revert_camera_realism(scene)
    look_perfection.revert_perfection(scene)
    look_lighting.remove_lighting(scene)
    for material in [m for m in bpy.data.materials if m.name.startswith('StudioLook_Clay')]:
        bpy.data.materials.remove(material)
    scene.view_layers[0].material_override = None


ORIENT_FRONT, ORIENT_BACK = (0.85, 0.12, 0.08), (0.08, 0.22, 0.85)  # forward-facing / rear-facing faces (hybrid previs)
ORIENT_CUT = 0.5  # |normal . forward| above this counts as facing front/back


def _clay_material(name, color, oriented=False):
    """Matte clay; 'studio_orient' face attribute picks front/back tints; the object property
    'studio_placeholder' (keyframed 0/1) switches the object to black for generated regions."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nodes, links = m.node_tree.nodes, m.node_tree.links
    bsdf = nodes.get('Principled BSDF')
    bsdf.inputs['Roughness'].default_value = 1.0
    base = nodes.new('ShaderNodeRGB'); base.outputs[0].default_value = (*color, 1)
    colour = base.outputs[0]
    if oriented:
        attr = nodes.new('ShaderNodeAttribute'); attr.attribute_type = 'GEOMETRY'; attr.attribute_name = 'studio_orient'
        ramp = nodes.new('ShaderNodeValToRGB'); ramp.color_ramp.interpolation = 'CONSTANT'
        stops = ramp.color_ramp.elements
        stops[0].position, stops[0].color = 0.0, (*ORIENT_BACK, 1)
        stops[1].position, stops[1].color = (1 + ORIENT_CUT) / 2, (*ORIENT_FRONT, 1)
        middle = stops.new((1 - ORIENT_CUT) / 2); middle.color = (*color, 1)
        scale = nodes.new('ShaderNodeMapRange')
        scale.inputs['From Min'].default_value, scale.inputs['From Max'].default_value = -1.0, 1.0
        links.new(attr.outputs['Fac'], scale.inputs['Value']); links.new(scale.outputs['Result'], ramp.inputs['Fac'])
        colour = ramp.outputs['Color']
    hole = nodes.new('ShaderNodeAttribute'); hole.attribute_type = 'OBJECT'; hole.attribute_name = 'studio_placeholder'
    mix = nodes.new('ShaderNodeMix'); mix.data_type = 'RGBA'
    mix.inputs['B'].default_value = (0.0, 0.0, 0.0, 1)
    links.new(hole.outputs['Fac'], mix.inputs['Factor']); links.new(colour, mix.inputs['A'])
    links.new(mix.outputs['Result'], bsdf.inputs['Base Color'])
    return m


def _write_orientation(obj, forward_in_object):
    from mesh_data import unique_data
    mesh = unique_data(obj).data
    values = [p.normal.dot(forward_in_object) for p in mesh.polygons]
    attr = mesh.attributes.get('studio_orient') or mesh.attributes.new('studio_orient', 'FLOAT', 'FACE')
    attr.data.foreach_set('value', values)


def _clay(scene, job=None):
    """Matte role-coloured override for hybrid previs: shape and motion only, no look.

    route.generative.previs adds the hybrid conventions: orientation_colors tints each subject's faces by
    where they point (spec length axis = forward: red front, blue back) so the video model keeps the
    heading; placeholders key objects black in frame windows where the model should generate content."""
    from mathutils import Vector
    bpy.context.view_layer.update()  # world matrices are read below
    previs = (((job or {}).get('shot') or {}).get('route') or {}).get('generative', {}) or {}
    previs = previs.get('previs') or {}
    materials = {role: _clay_material(f'StudioLook_Clay_{role or "default"}', color) for role, color in CLAY_COLORS.items()}
    oriented = {}
    forward = {}
    if previs.get('orientation_colors'):
        for subject_id, spec_path in ((job or {}).get('subject_spec_paths') or {}).items():
            spec = json.loads(Path(spec_path).read_text())
            axis = {'length': 'y', **spec.get('axes', {})}['length']
            vector = Vector((0.0, 0.0, 0.0)); vector['xyz'.index(axis)] = 1.0
            root = next((o for o in scene.objects if o.get('studio_id') == subject_id and not o.get('studio_part_id')), None)
            if root is not None:
                forward[subject_id] = (root, vector)
    count = 0
    from scene_roles import counts
    for obj in scene.objects:
        if obj.type != 'MESH' or not counts(obj, 'clay'):  # helpers and volumes keep their own (hidden) state
            continue
        role = 'mechanical' if obj.get('studio_mechanical') else obj.get('studio_role') if obj.get('studio_role') in CLAY_COLORS else None
        if obj.get('studio_subject_id') in forward:
            root, vector = forward[obj['studio_subject_id']]
            # n_root . f == n_local . (M^-1 f) for the rotation part M of object -> subject root
            to_object = (root.matrix_world.inverted() @ obj.matrix_world).to_3x3().inverted()
            _write_orientation(obj, (to_object @ vector).normalized())
            if role not in oriented:
                oriented[role] = _clay_material(f'StudioLook_Clay_oriented_{role or "default"}', CLAY_COLORS[role], oriented=True)
            material = oriented[role]
        else:
            material = materials[role]
        from mesh_data import unique_data
        if list(obj.data.materials) != [material]:
            unique_data(obj)
        obj.data.materials.clear(); obj.data.materials.append(material); count += 1
    blacked = []
    for slot in previs.get('placeholders', []):
        for key in slot.get('part_ids', []):
            subject_id, _, part_id = key.partition('/')
            targets = [o for o in scene.objects if o.type == 'MESH' and (o.get('studio_id') == key or
                       (o.get('studio_subject_id') == subject_id and o.get('studio_part_id') == part_id))]
            if not targets:
                raise ValueError(f'LOOK_QA_FAILED: placeholder part {key} not found in the scene')
            for obj in targets:
                if 'studio_placeholder' not in obj:
                    obj['studio_placeholder'] = 0.0
                    obj.keyframe_insert('["studio_placeholder"]', frame=scene.frame_start)
                # shot frames are 0-based half-open; Blender frames are 1-based
                for frame, value in ((slot['start_frame'] + 1, 1.0), (slot['end_frame'] + 1, 0.0)):
                    obj['studio_placeholder'] = value
                    obj.keyframe_insert('["studio_placeholder"]', frame=frame)
                from look_scale import _action_fcurves  # legacy and 5.x layered actions
                for fcurve in _action_fcurves(obj.animation_data.action):
                    if 'studio_placeholder' in fcurve.data_path:
                        for point in fcurve.keyframe_points:
                            point.interpolation = 'CONSTANT'
                blacked.append(obj.name)
    scene.render.use_motion_blur = False
    if scene.camera:
        scene.camera.data.dof.use_dof = False
    return {'objects': count, 'oriented_subjects': sorted(forward), 'placeholders': sorted(set(blacked))}


def apply_look(job, scene):
    name = resolve_preset(job)
    table = presets()
    if name not in table:
        raise ValueError(f'LOOK_QA_FAILED: unknown look preset {name}')
    spec = table[name]
    digest = inputs_hash(job, name, spec)
    report = {'preset': name, 'inputs_hash': digest, 'applied': [], 'skipped': [], 'passes': {}, 'warnings': [], 'gate_failures': []}
    previous = scene.get('studio_look_inputs_hash')
    if spec is None:
        if previous:
            _revert(scene); del scene['studio_look_inputs_hash']; report['applied'].append('revert_to_flat')
        report['scene_state_sha256'] = scene_state_sha256(scene)
        return report
    if previous:
        _revert(scene)
    import look_camera, look_lighting, look_perfection, look_scale
    scale = look_scale.audit_scale(scene)
    report['passes']['scale'] = scale; report['applied'].append('scale_audit')
    fail_on = set(((job.get('style') or {}).get('look') or {}).get('qa', {}).get('fail_on', []))
    if scale.get('flag_ratio', 0) > 0.2:
        (report['gate_failures'] if 'scale' in fail_on else report['warnings']).append(
            f"scale: {scale.get('flag_ratio')} of tagged objects outside real dimensions; suggested factor {scale.get('suggested_uniform_factor')}")
    if spec.get('clay'):
        report['passes']['clay'] = _clay(scene, job); report['applied'].append('clay')
    else:
        passes = {**spec['passes'], **(((job.get('style') or {}).get('look') or {}).get('passes') or {})}  # style overrides preset
        if scene.get('studio_guard_frames'):  # contact that only happens late in a shot (e.g. pawl catching the rack)
            passes['guard_frames'] = json.loads(scene['studio_guard_frames'])
        perfection = look_perfection.apply_perfection(scene, passes)
        report['passes']['perfection'] = perfection; report['applied'].append('perfection')
        report['gate_failures'] += [f'guard: {g}' for g in perfection.get('gate_failures', [])]
        report['warnings'] += perfection.get('warnings', [])
    realism = {**(spec.get('camera_default') or {}), **(job['shot']['camera'].get('realism') or {})}
    if scene.get('studio_camera_rig_shake'):
        realism['shake'] = None
    camera = look_camera.apply_camera_realism(scene, realism, labels=job['shot'].get('labels', []))
    report['passes']['camera'] = camera; report['applied'].append('camera_realism')
    report['warnings'] += camera.get('warnings', [])
    report['gate_failures'] += camera.get('gate_failures', [])
    if spec.get('lighting'):
        style = job.get('style') or {}
        light_rig = style.get('light_rig') or {}
        preset_name = light_rig.get('preset') or spec['lighting']
        lighting = look_lighting.apply_lighting(scene, preset_name, library_root=job['library_root'],
                                                style_light_rig=light_rig, style_world=style.get('world') or {},
                                                atmosphere=job['shot']['render'].get('atmosphere'), exposure_keys=exposure_keys(job))
        report['passes']['lighting'] = lighting; report['applied'].append('lighting')
        report['warnings'] += lighting.get('warnings', [])
    bake = None if spec.get('clay') else passes.get('bake')
    if bake:  # opt-in: after lighting (bakes see the final light-independent surface), before the compositor
        import look_bake
        report['passes']['bake'] = look_bake.apply_bake(scene, bake); report['applied'].append('bake')
    # style.look.compositor (schema'd, previously unread) overrides the preset's compositor
    compositor = ((job.get('style') or {}).get('look') or {}).get('compositor') or spec.get('compositor') or 'off'
    report['passes']['compositor'] = look_camera.compositor_setup(scene, compositor); report['applied'].append('compositor')
    scene['studio_look_inputs_hash'] = digest
    for failure in list(report['gate_failures']):
        if not any(token in failure for token in ALWAYS_FATAL) and not any(failure.startswith(f) for f in fail_on):
            report['gate_failures'].remove(failure); report['warnings'].append(failure)
    report['scene_state_sha256'] = scene_state_sha256(scene)
    return report
