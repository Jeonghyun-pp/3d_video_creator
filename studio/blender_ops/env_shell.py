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

The ground disc is a real surface (role environment_shell: raycast on): a camera move that dives under the sea or the
ground is refused (passes_through_geometry), and the guards' min_clearance counts the distance to it like any surface.
Cameras over it are never refused for it unless they come closer than the shot's own clearance - the same rule as for
an author's sea box. A shell is built only when declared (world.shell / scene.shell), so these refusals are new only
for a shot that asked for a ground.
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
DOME_SEGMENTS = 64
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


def _author_ground(ground_z):
    """The author's own ground (a sea box, a desert plane, water tiled by instances): one object's world-space box - its
    instances' boxes together - wide, flat and level with the declared ground (env_shell_core.is_author_ground)."""
    boxes = {}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for count, inst in enumerate(depsgraph.object_instances):
        if count >= core.AUTHOR_SCAN_MAX:
            _author_ground.cut = core.AUTHOR_SCAN_MAX   # read by build(): said in the report
            break
        obj = inst.object
        owner = (inst.parent if inst.is_instance and inst.parent is not None else obj).original
        if obj.type != 'MESH' or owner.get('studio_env_shell_engine') or owner.hide_render:
            continue
        pts = [inst.matrix_world @ Vector(c) for c in obj.bound_box]
        lo, hi = boxes.get(owner.name, ([1e18] * 3, [-1e18] * 3))
        boxes[owner.name] = ([min(lo[i], *(p[i] for p in pts)) for i in range(3)],
                             [max(hi[i], *(p[i] for p in pts)) for i in range(3)])
    for name, (lo, hi) in sorted(boxes.items()):
        if core.is_author_ground(lo, hi, ground_z):
            return bpy.data.objects[name]
    return None


_author_ground.cut = None   # instances left unread by the last scan (AUTHOR_SCAN_MAX), for the report


def _meshes(subject_only):
    return [o for o in bpy.context.scene.objects if o.type == 'MESH' and not o.hide_render and not o.get('studio_env_shell_engine')
            and (o.get('studio_subject_id') or not subject_only)]


def _subject_centre():
    """The centre of the subject's box - of every mesh the scene has when nothing is marked a subject (a set-only shot),
    the origin when there is nothing at all."""
    meshes = _meshes(True) or _meshes(False)
    if not meshes:
        return Vector((0.0, 0.0, 0.0))
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    return Vector([(min(p[i] for p in pts) + max(p[i] for p in pts)) / 2 for i in range(3)])


def _subject_top():
    meshes = _meshes(True) or _meshes(False)
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


RELIEF_SEGMENTS, RELIEF_RINGS = 192, 12   # round the ring, and across its band


def _relief(colour, z, relief):
    """The declared hills (core.relief_height) as one mesh in metres round the place, at the ground's height."""
    import math
    mesh = bpy.data.meshes.new('env_shell_relief')
    bm = bmesh.new()
    start, outer = relief['start_m'], relief['start_m'] * core.RELIEF_OUTER
    rows = []
    for i in range(RELIEF_RINGS + 1):
        r = start + (outer - start) * i / RELIEF_RINGS
        row = []
        for j in range(RELIEF_SEGMENTS):
            theta = 2 * math.pi * j / RELIEF_SEGMENTS
            row.append(bm.verts.new((r * math.cos(theta), r * math.sin(theta), core.relief_height(r, theta, relief['height_m'], start))))
        rows.append(row)
    for i in range(RELIEF_RINGS):
        for j in range(RELIEF_SEGMENTS):
            k = (j + 1) % RELIEF_SEGMENTS
            bm.faces.new((rows[i][j], rows[i + 1][j], rows[i + 1][k], rows[i][k]))   # counter-clockwise from above: facing up
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new('env_shell_relief', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(_material('env_shell_relief', tuple(c * 0.85 for c in colour)))
    obj.location.z = z
    return obj


def _dome(sky, ground, under=False):
    """A sphere seen from inside, each ring of faces coloured at its height (core.sky_colour: horizon haze to zenith;
    under a water ground, core.under_colour: the ceiling's colour fading to the deep)."""
    mesh = bpy.data.meshes.new('env_shell_sky')
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=DOME_SEGMENTS, v_segments=core.DOME_RINGS, radius=1.0)
    bmesh.ops.reverse_faces(bm, faces=bm.faces)   # normals inward: lit and seen from inside
    colours = {}
    for face in bm.faces:
        height = face.calc_center_median().z
        colour = core.under_colour(ground, height) if under else core.sky_colour(sky, ground, height)
        face.material_index = colours.setdefault(colour, len(colours))
    bm.to_mesh(mesh); bm.free()
    if under:   # one smooth gradient under water: flat faces each catch the clay's studio light and stripe the water
        for poly in mesh.polygons:
            poly.use_smooth = True
    obj = bpy.data.objects.new('env_shell_sky', mesh)
    bpy.context.scene.collection.objects.link(obj)
    for colour, i in sorted(colours.items(), key=lambda kv: kv[1]):
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
    levels, level_boxes = [], []
    declared_z = float(shell.get('ground_z_m', 0.0))
    for o in scene.objects:   # the faces the author made level with the ground (a deck, a pad): the disc goes under them
        if o.type == 'MESH' and not o.hide_render and not o.get('studio_env_shell_engine'):
            pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
            zs = [p.z for p in pts]
            levels += [min(zs), max(zs)]
            if min(abs(min(zs) - declared_z), abs(max(zs) - declared_z)) < core.GROUND_CLEAR_MAX_M + 0.01:
                level_boxes.append([[round(min(p[i] for p in pts), 3) for i in range(3)], [round(max(p[i] for p in pts), 3) for i in range(3)]])
    z = core.ground_height(declared_z, levels)   # cleared again in fit() for the solved camera's depth precision
    report['ground_z_m'] = round(z, 4)
    report['declared_z_m'] = declared_z
    report['author_levels'] = sorted({round(v, 4) for v in levels if abs(v - declared_z) < core.GROUND_CLEAR_MAX_M + 0.01})
    report['levels_all'] = sorted({round(v, 4) for v in levels})   # a chain of drops in fit() checks every one
    report['level_boxes'] = level_boxes                             # the faces near the ground: how far they are seen
    under = shell.get('camera') == 'under'   # filmed from under the ground (water): its disc is the ceiling, the dome the water
    report['camera'] = 'under' if under else 'over'
    colour = core.GROUNDS[shell['ground']][0]
    if under:
        # filmed from inside the water: no disc - its surface is the dome's ceiling. A flat disc seen from under, almost
        # edge on, shades dark in the clay's studio light and meets the water at a line (2026-10-23: the horizon again);
        # the dome's inside faces the camera everywhere. A camera leaving the water is refused (CAMERA_OUT_OF_SPACE).
        report['ground_under'] = 'the surface is the dome\'s ceiling (filmed under it)'
        colour = None
    if colour is not None:
        _author_ground.cut = None
        author = _author_ground(declared_z)
        if _author_ground.cut:
            report['author_scan_cut'] = (f'the author-ground scan stopped at {_author_ground.cut} instances: a ground made of more '
                                         'pieces may be missed and the disc built under it')
        theirs = _own('ground') or ([author] if author else [])
        if theirs:
            report['kept'].append({'kind': 'ground', 'objects': sorted(o.name for o in theirs)})
        else:
            disc = _disc(colour, z)
            disc.location.x, disc.location.y = centre.x, centre.y
            _tag(disc, 'ground', 'environment_shell')
            report['built'].append('ground')
        if shell.get('relief'):   # the hills round the place, in metres (never scaled with the disc)
            hills = _relief(colour, z, shell['relief'])
            hills.location.x, hills.location.y = centre.x, centre.y
            _tag(hills, 'relief', 'environment_shell')
            report['built'].append('relief')
    horizon = core.UNDER[shell['ground']]['deep'] if under else core.sky_colour(shell['sky'], shell['ground'], core.SKY_STOPS[0])
    if horizon is not None:   # (the horizon colour itself; under water, the deep is what shows where nothing renders)
        theirs = _own('sky')
        if theirs:
            report['kept'].append({'kind': 'sky', 'objects': sorted(o.name for o in theirs)})
        else:
            dome = _dome(shell['sky'], shell['ground'], under)
            dome.location = (centre.x, centre.y, z)
            _tag(dome, 'sky', 'atmosphere')
            # seen by the camera only: a closed sphere would otherwise shut the world's light out of a Cycles render
            for ray in ('visible_diffuse', 'visible_glossy', 'visible_transmission', 'visible_volume_scatter', 'visible_shadow'):
                setattr(dome, ray, False)
            report['built'].append('sky')
        world = scene.world
        if world is None:   # a new world lights nothing: a lit render without a world stays as it was (black, no ambient)
            world = scene.world = bpy.data.worlds.new('World')
            background = world.node_tree.nodes.get('Background') if world.node_tree else None
            if background is not None:
                background.inputs['Strength'].default_value = 0.0
        # world.color is what Workbench (the clay review, layout clips) draws where nothing renders; lit renders read the
        # world's node tree (always on since Blender 5), which this leaves alone
        world.color = horizon
    # Sized now for a camera a few subject heights up (fit() sizes them again to the solved camera): what runs before
    # the camera - frames check, the still passes - never sees a one-metre disc.
    guess = core.disc_radius(PRE_FIT_HEIGHTS * max(1.0, _subject_top() - z), scene.camera.data.clip_end if scene.camera else 1000.0)[0]
    for o in scene.objects:
        if o.get('studio_env_shell_engine') and o[TAG] in ('ground', 'sky'):   # the relief stands in metres
            o.scale = (guess * 0.98, guess * 0.98, 1.0) if o[TAG] == 'ground' else (guess, guess, guess)
    report['radius_m'] = round(guess, 1)
    report['seconds'] = round(time.perf_counter() - started, 3)
    scene['studio_env_shell'] = True
    return report


def _camera_points(scene):
    """The camera's positions over the shot's frames (every 4th, and always the first and last - a rising move peaks at
    its end) and its nearest far clip."""
    cam = scene.camera
    if cam is None:
        return [], 1000.0
    points, clip = [], cam.data.clip_end
    for frame in sorted(set(range(scene.frame_start, scene.frame_end + 1, 4)) | {scene.frame_end}):
        scene.frame_set(frame)
        points.append(cam.matrix_world.translation.copy())
        clip = min(clip, cam.data.clip_end)
    scene.frame_set(scene.frame_start)
    return points, clip


def _keyed_paths(block):
    """The data paths keyed on `block` (layered actions keep their curves in the channelbags of the action's layers)."""
    action = getattr(getattr(block, 'animation_data', None), 'action', None)
    if action is None:
        return set()
    curves = getattr(action, 'fcurves', None)
    if curves is None:
        curves = [c for layer in getattr(action, 'layers', []) for strip in layer.strips for bag in getattr(strip, 'channelbags', []) for c in bag.fcurves]
    return {c.data_path for c in curves}


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
    keyed = cam is not None and 'clip_end' in _keyed_paths(cam.data)
    movable = cam is not None and not eyes and not keyed
    reach = max([0.0] + [(p.to_2d() - centre.to_2d()).length for p in points])   # the farthest camera across the disc
    under = job['shell'].get('camera') == 'under'
    depth = max([0.0] + [z - p.z for p in points])   # the deepest camera under the surface
    if under:   # the ceiling reaches past where a camera looking up meets it; capped by the far clip like a ground
        radius, why = core.under_radius(depth, reach), 'under'
        if not movable and radius > core.CLIP_SHARE * clip:
            radius, why = core.CLIP_SHARE * clip, 'clip'
    else:
        radius, why = core.disc_radius(top, 1e12 if movable else clip, reach)
    hills = (job['shell'].get('relief') or {}).get('start_m', 0.0) * core.RELIEF_OUTER   # the dome holds the hills too
    dome, needs = core.dome_radius(max(radius, hills), farthest)
    raised = None
    if movable and needs > cam.data.clip_end:
        raised = [round(cam.data.clip_end, 2), round(needs, 2)]
        cam.data.clip_end = needs
    elif not movable and needs > clip:
        dome = min(dome, core.CLIP_SHARE * clip)
        if dome < farthest:   # a camera outside the dome sees its outside, not the sky
            report['dome_short'] = (f"the camera's far clip {clip:g} m is {'keyed' if keyed else 'fixed by the pass'}: the sky "
                                    f'dome stays {round(dome)} m out, inside the camera range ({round(farthest)} m) - a camera '
                                    'past it sees no sky; unkey clip_end or bring the camera in')
    # under the author's faces level with the ground, by the depth precision of the farthest camera's view of them
    near = cam.data.clip_start if cam is not None else 0.1
    seen_at = core.level_reach([tuple(p) for p in points], report.get('level_boxes') or [])
    drop, short = core.ground_clear(near, seen_at or farthest)
    new_z = core.ground_height(report.get('declared_z_m', z), report.get('levels_all') or report.get('author_levels') or [], drop)
    if new_z != z:
        for o in scene.objects:
            if o.get('studio_env_shell_engine') and o[TAG] in ('ground', 'relief'):   # the hills stand on the ground
                o.location.z = new_z
        report['ground_z_m'] = round(new_z, 4)
    if short and report.get('author_levels'):
        report['ground_clear_short'] = (f'the near clip {near:g} m leaves too little depth precision {round(seen_at or farthest)} m out: '
                                        f'the ground goes {core.GROUND_CLEAR_MAX_M} m under the faces at its height, not '
                                        'the full step - they may flicker in a lit render')
    report['levels_seen_at_m'] = round(seen_at, 2)
    if under:
        report['camera_depth_m'] = round(depth, 2)
        space = [core.out_of_space(job['shell'], float(p.z - z + float(job['shell'].get('ground_z_m', 0.0)))) for p in points]
        report['out_of_space'] = next((w for w in space if w), None)
    report.update({'camera_top_m': round(top, 2), 'camera_far_m': round(farthest, 2), 'camera_reach_m': round(reach, 2), 'clip_end_m': round(clip, 2),
                   'radius_m': round(radius, 1), 'radius_by': why, 'dome_m': round(dome, 1),
                   'horizon_dip_deg': round(core.horizon_dip_deg(top, radius), 2), **({'clip_end_raised': raised} if raised else {})})
    for o in scene.objects:
        if not o.get('studio_env_shell_engine'):
            continue
        if o[TAG] == 'relief' and top > core.GROUND_MAX_ALT_M:   # left out with the ground it stands on
            o.hide_render = True; o.hide_viewport = True
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
