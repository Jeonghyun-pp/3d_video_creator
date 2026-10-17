"""Environment shell (Blender side): build the ground disc and the sky dome a shot declares (env_shell_core.resolve,
done on the host into job['shell']), then fit them to the camera once it is solved.

build(job)      in generate.prepare, before the camera: a ground disc (scene role environment_shell: the camera sees it,
                rays stop on it, it is in the clay and control passes) and a sky dome (scene role atmosphere: drawn in
                the clay review and the renders, out of every measure - the probe reads it as background, the look's
                bounds and meter never see it). A piece the author built already (an object tagged studio_env_shell
                with that kind, or a wide flat mesh for the ground) is left alone.
fit(job)        after the camera rig: the disc reaches the horizon of the highest camera and both stay inside its far
                clip; returns the report (env_shell_report.json).
Objects carry studio_id 'env_shell/<kind>' (one group 'env_shell' for keep masks and region ids) and studio_env_shell.
"""
import time

import bmesh
import bpy
from mathutils import Vector

import env_shell_core as core

TAG = 'studio_env_shell'
ROLE = 'studio_scene_role'
GROUP = 'env_shell'
DISC_SEGMENTS = 96
DOME_SEGMENTS, DOME_RINGS = 64, 96   # rings fine enough for SKY_RINGS' narrow bands near the horizon
PRE_FIT_HEIGHTS = 3.0   # before the camera is solved, the shell is sized for a camera this many subject heights up


def _material(name, colour):
    made = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    made.diffuse_color = (*colour, 1.0)   # what the Workbench (clay review, layout clips) draws
    made.use_nodes = True
    nodes = made.node_tree.nodes
    bsdf = nodes.get('Principled BSDF')
    if bsdf is not None:
        bsdf.inputs['Base Color'].default_value = (*colour, 1.0)
        bsdf.inputs['Roughness'].default_value = 0.9
    return made


def _own(kind):
    return [o for o in bpy.context.scene.objects if o.get(TAG) == kind and not o.get('studio_env_shell_engine')]


def _author_ground():
    """A wide flat mesh the author made (a sea box, a desert plane): the ground is theirs."""
    for o in bpy.context.scene.objects:
        if o.type != 'MESH' or o.get('studio_env_shell_engine') or o.hide_render:
            continue
        if core.is_author_ground(o.dimensions):
            return o
    return None


def _subject_centre():
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o.get('studio_subject_id') and not o.hide_render]
    if not meshes:
        return Vector((0.0, 0.0, 0.0))
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    return Vector([(min(p[i] for p in pts) + max(p[i] for p in pts)) / 2 for i in range(3)])


def _subject_top():
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o.get('studio_subject_id') and not o.hide_render]
    return max(((o.matrix_world @ Vector(c)).z for o in meshes for c in o.bound_box), default=0.0)


def _tag(obj, kind, role):
    obj[TAG] = kind
    obj[ROLE] = role
    obj['studio_id'] = f'{GROUP}/{kind}'
    obj['studio_env_shell_engine'] = True
    obj.visible_shadow = False
    obj.display.show_shadows = False   # Workbench's stencil shadows from kilometre-wide shells break up into patches
                                       # over the author's own sea (2026-10-18, rocket s03/s17 clay)


def _disc(colour, z):
    mesh = bpy.data.meshes.new('env_shell_ground')
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=True, segments=DISC_SEGMENTS, radius=1.0)
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new('env_shell_ground', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(_material('env_shell_ground', colour))
    obj.location.z = z
    return obj


def _dome(colours):
    """A sphere seen from inside, its faces coloured by height band (horizon haze to zenith)."""
    mesh = bpy.data.meshes.new('env_shell_sky')
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=DOME_SEGMENTS, v_segments=DOME_RINGS, radius=1.0)
    bmesh.ops.reverse_faces(bm, faces=bm.faces)   # normals inward: lit and seen from inside
    for face in bm.faces:
        face.material_index = core.band_of(face.calc_center_median().z)
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new('env_shell_sky', mesh)
    bpy.context.scene.collection.objects.link(obj)
    for i, colour in enumerate(colours):
        obj.data.materials.append(_material(f'env_shell_sky_{i}', colour))
    return obj


def build(job):
    """Build what job['shell'] declares and the author did not; returns {'built': [...], 'kept': [...], 'held': why?}."""
    shell = job.get('shell')
    if not shell:
        return None
    started = time.perf_counter()
    report = {'shell': {k: v for k, v in shell.items() if k != 'held'}, 'built': [], 'kept': []}
    if shell.get('held'):
        report['held'] = shell['held']
        return report
    scene = bpy.context.scene
    for o in [o for o in scene.objects if o.get('studio_env_shell_engine')]:   # a session re-running the chain: made again
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and not data.users:
            bpy.data.meshes.remove(data)
    centre = _subject_centre()
    levels = []
    for o in scene.objects:   # the faces the author made level with the ground (a deck, a pad): the disc goes under them
        if o.type == 'MESH' and not o.hide_render and not o.get('studio_env_shell_engine'):
            zs = [(o.matrix_world @ Vector(c)).z for c in o.bound_box]
            levels += [min(zs), max(zs)]
    z = core.ground_height(shell.get('ground_z_m', 0.0), levels)
    report['ground_z_m'] = round(z, 4)
    colour = core.GROUNDS[shell['ground']][0]
    if colour is not None:
        author = _author_ground()
        theirs = _own('ground') or ([author] if author else [])
        if theirs:
            report['kept'].append({'kind': 'ground', 'objects': sorted(o.name for o in theirs)})
        else:
            disc = _disc(colour, z)
            disc.location.x, disc.location.y = centre.x, centre.y
            _tag(disc, 'ground', 'environment_shell')
            report['built'].append('ground')
    colours = core.band_colours(shell['sky'], shell['ground'])
    if colours is not None:
        theirs = _own('sky')
        if theirs:
            report['kept'].append({'kind': 'sky', 'objects': sorted(o.name for o in theirs)})
        else:
            dome = _dome(colours)
            dome.location = (centre.x, centre.y, z)
            _tag(dome, 'sky', 'atmosphere')
            # seen by the camera only: a closed sphere would otherwise shut the world's light out of a Cycles render
            for ray in ('visible_diffuse', 'visible_glossy', 'visible_transmission', 'visible_volume_scatter', 'visible_shadow'):
                setattr(dome, ray, False)
            report['built'].append('sky')
        world = scene.world or bpy.data.worlds.new('World')
        scene.world = world
        world.color = colours[1]   # where nothing renders (past the dome's clip, a render without it): the horizon colour
    # Sized now for a camera a few subject heights up (fit() sizes them again to the solved camera): what runs before
    # the camera - frames check, the still passes - never sees a one-metre disc.
    guess = core.disc_radius(PRE_FIT_HEIGHTS * max(1.0, _subject_top() - z), scene.camera.data.clip_end if scene.camera else 1000.0)[0]
    for o in scene.objects:
        if o.get('studio_env_shell_engine'):
            o.scale = (guess * 0.98, guess * 0.98, 1.0) if o[TAG] == 'ground' else (guess, guess, guess)
    report['radius_m'] = round(guess, 1)
    report['seconds'] = round(time.perf_counter() - started, 3)
    scene['studio_env_shell'] = True
    return report


def _camera_points(scene):
    """The camera's positions over the shot's frames (every 4th) and its nearest far clip."""
    cam = scene.camera
    if cam is None:
        return [], 1000.0
    points, clip = [], cam.data.clip_end
    for frame in range(scene.frame_start, scene.frame_end + 1, 4):
        scene.frame_set(frame)
        points.append(cam.matrix_world.translation.copy())
        clip = min(clip, cam.data.clip_end)
    scene.frame_set(scene.frame_start)
    return points, clip


def fit(job, report, eyes=None, clip_end=None):
    """Size the engine's disc and dome to the solved camera (or to `eyes`, camera points a still pass poses, with their
    far clip); adds the numbers to `report` (and returns it)."""
    if not report or not report.get('built'):
        return report
    scene = bpy.context.scene
    z = float(report.get('ground_z_m', job['shell'].get('ground_z_m', 0.0)))
    if eyes:
        points, clip = [Vector(e) for e in eyes], float(clip_end or 1000.0)
    else:
        points, clip = _camera_points(scene)
    centre = next((o.location.copy() for o in scene.objects if o.get('studio_env_shell_engine')), Vector((0.0, 0.0, z)))
    top = max([0.0] + [p.z - z for p in points])
    farthest = max([0.0] + [(p - centre).length for p in points])
    # the ground reaches the horizon; the dome holds the disc and every camera; the shot camera's far clip is moved out
    # to them (only ever out: what the camera sees nearer is unchanged). A still pass (eyes) keeps its clip: capped by it.
    cam = scene.camera
    keyed = bool(cam is not None and cam.data.animation_data and cam.data.animation_data.action and any(
        c.data_path == 'clip_end' for c in getattr(cam.data.animation_data.action, 'fcurves', [])))
    movable = cam is not None and not eyes and not keyed
    radius, why = core.disc_radius(top, 1e12 if movable else clip)
    dome, needs = core.dome_radius(radius, farthest)
    raised = None
    if movable and needs > cam.data.clip_end:
        raised = [round(cam.data.clip_end, 2), round(needs, 2)]
        cam.data.clip_end = needs
    elif not movable and needs > clip:
        dome = min(dome, core.CLIP_SHARE * clip)
    report.update({'camera_top_m': round(top, 2), 'camera_far_m': round(farthest, 2), 'clip_end_m': round(clip, 2),
                   'radius_m': round(radius, 1), 'radius_by': why, 'dome_m': round(dome, 1),
                   'horizon_dip_deg': round(core.horizon_dip_deg(top, radius), 2), **({'clip_end_raised': raised} if raised else {})})
    for o in scene.objects:
        if not o.get('studio_env_shell_engine'):
            continue
        if o[TAG] == 'ground':
            if top > core.GROUND_MAX_ALT_M:   # a flat disc seen from this high is no ground: left out, said so
                o.hide_render = True; o.hide_viewport = True
                report['ground_left_out'] = f'camera {round(top)} m over the ground (> {core.GROUND_MAX_ALT_M:.0f} m): a flat disc is no horizon there'
                continue
            o.scale = (radius * 0.98, radius * 0.98, 1.0)   # inside the dome's horizon band
        elif o[TAG] == 'sky':
            o.scale = (dome, dome, dome)
    return report
