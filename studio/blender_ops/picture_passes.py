"""The passes a 3D-first picture is painted over (host: studio/picture3d.passes): on the authored checkpoint, the scene
prepared as frames candidates prepares it (prepare -> recorded cheats -> show openings), each pictured moment shot from
its chosen pose at its frame - nothing saved, nothing heavy rendered:
  clay.png    Workbench studio light, one grey: what the painter paints over
  depth.png   Workbench Z as disparity (near white, empty black): where things stand
  lines.png   Freestyle outlines on flat white (else lines where the depth steps): what the painting must follow
  region.png  the outlines of the subject and the named things, for registering a painting onto the lines
  id.json     the probe-format id row of the moment (frame_probe: shapes per key class) and each named id's visible pixels
  state.json  each claimed thing's state from geometry (part_state_core) and whether it renders in frame
Usage: blender -b <authored.blend> --python picture_passes.py -- <job.json>
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import time

import bpy
from mathutils import Vector
import numpy as np
import OpenImageIO as oiio

sys.path.insert(0, str(Path(__file__).parent))
import part_state_core as states  # noqa: E402

VERTS_PER_OBJECT = 400   # vertices a state is measured over, per object (evenly strided)
LINE_PX = 1.5            # Freestyle line width at the pass's size
CREASE_DEG = 140         # Freestyle crease angle: edges sharper than this are lines


def _gray(path, arr):
    a = np.clip(arr, 0, 1)
    spec = oiio.ImageSpec(a.shape[1], a.shape[0], 1, oiio.UINT8)
    out = oiio.ImageOutput.create(str(path))
    out.open(str(path), spec)
    out.write_image(np.ascontiguousarray((a * 255 + 0.5).astype(np.uint8)[:, :, None]))
    out.close()


def _render(scene, path, fmt='PNG'):
    r = scene.render
    r.image_settings.media_type = 'MULTI_LAYER_IMAGE' if fmt == 'OPEN_EXR_MULTILAYER' else 'IMAGE'
    r.image_settings.file_format = fmt
    if fmt == 'PNG':
        _png(r, 'RGB')
    r.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def _png(render, mode):
    """An 8-bit display-referred PNG. The depth pass's EXR leaves linear float output settings behind; a PNG written
    with them stores linear values, and the id pass's palette (display values) then decodes grey as the key colour
    (2026-10-17: a sea read as part of the subject)."""
    s = render.image_settings
    s.media_type, s.file_format, s.color_mode = 'IMAGE', 'PNG', mode
    s.color_depth = '8'
    if hasattr(s, 'color_management'):
        s.color_management = 'FOLLOW_SCENE'


def _clay(scene, path):
    r, sh, vs = scene.render, scene.display.shading, scene.view_settings
    r.engine = 'BLENDER_WORKBENCH'
    vs.view_transform, vs.look, vs.exposure, vs.gamma = 'Standard', 'None', 0.0, 1.0
    sh.light, sh.color_type, sh.single_color = 'STUDIO', 'SINGLE', (0.6, 0.6, 0.6)
    sh.show_object_outline = sh.show_cavity = sh.show_shadows = False
    sh.show_specular_highlight = True
    scene.world.color = (0.18, 0.18, 0.18)
    scene.display.render_aa = '8'
    _render(scene, path)


def _read(path):
    image = oiio.ImageInput.open(str(path))
    try:
        return image.read_image(format=oiio.FLOAT)
    finally:
        image.close()


def _clay_through(scene, path, skins, alpha):
    """The clay pass with see-through skins drawn see-through: rendered with them and without them, mixed by how
    see-through they are (show_scene._wrap: the surface times 1 - alpha over what is behind). Why (2026-10-17): the
    stills left every skin out, so a part a moment shows faded round another (show_core.SHOWN_THROUGH) was not in the
    picture painted over it."""
    if not skins:
        _clay(scene, path)
        return
    drawn = Path(path).with_name(Path(path).stem + '_drawn.png')
    _clay(scene, drawn)
    for o in skins:
        o.hide_render = True
    _clay(scene, path)
    for o in skins:
        o.hide_render = False
    mixed = (1.0 - alpha) * _read(drawn) + alpha * _read(path)
    spec = oiio.ImageSpec(mixed.shape[1], mixed.shape[0], mixed.shape[2], oiio.UINT8)
    out = oiio.ImageOutput.create(str(path))
    out.open(str(path), spec)
    out.write_image(np.ascontiguousarray((np.clip(mixed, 0, 1) * 255 + 0.5).astype(np.uint8)))
    out.close()
    drawn.unlink()


def _depth(scene, path):
    """Disparity over the visible range (near detail kept when a far backdrop is in frame); surfaces lighter than the
    empty background. -> (depth array, foreground mask)."""
    vl = bpy.context.view_layer
    vl.use_pass_z = True
    exr = path.with_suffix('.exr')
    _render(scene, exr, 'OPEN_EXR_MULTILAYER')
    z = None
    probe = oiio.ImageBuf(str(exr))
    for sub in range(max(1, probe.nsubimages)):
        buf = oiio.ImageBuf(str(exr), sub, 0)
        hit = [i for i, c in enumerate(buf.spec().channelnames) if c.endswith('Depth.Z') or c == 'Z']
        if hit:
            z = buf.get_pixels(oiio.FLOAT)[:, :, hit[0]]
            break
    exr.unlink()
    fg = z < 1e9
    near, far = (float(np.percentile(z[fg], 0.5)), float(np.percentile(z[fg], 99.5))) if fg.any() else (1.0, 2.0)
    inv = 1.0 / np.clip(z, near, far)
    d = np.where(fg, (inv - 1.0 / far) / max(1.0 / near - 1.0 / far, 1e-9), 0.0)
    d = np.where(fg, 0.15 + 0.85 * d, 0.0)
    _gray(path, d)
    return d, fg


def _lines(scene, path, depth, fg):
    """Freestyle outlines on a flat white frame; when Freestyle fails, lines where depth steps. -> how they were made."""
    r, vl, vs = scene.render, bpy.context.view_layer, scene.view_settings
    try:
        r.engine = 'BLENDER_EEVEE'
        white = bpy.data.materials.get('picture3d_white') or bpy.data.materials.new('picture3d_white')
        white.use_nodes = True
        nt = white.node_tree
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        em = nt.nodes.new('ShaderNodeEmission')
        em.inputs['Color'].default_value, em.inputs['Strength'].default_value = (1, 1, 1, 1), 1.0
        nt.links.new(em.outputs[0], nt.nodes.new('ShaderNodeOutputMaterial').inputs['Surface'])
        vl.material_override = white
        scene.world.use_nodes = True
        bg = scene.world.node_tree.nodes.get('Background')
        if bg:
            bg.inputs['Color'].default_value, bg.inputs['Strength'].default_value = (1, 1, 1, 1), 1.0
        vs.view_transform = 'Standard'
        r.use_freestyle = True
        r.line_thickness_mode, r.line_thickness = 'ABSOLUTE', LINE_PX
        vl.use_freestyle = True
        fs = vl.freestyle_settings
        fs.crease_angle = math.radians(CREASE_DEG)
        ls = fs.linesets[0] if fs.linesets else fs.linesets.new('picture3d')
        ls.select_by_visibility, ls.visibility, ls.select_by_edge_types = True, 'VISIBLE', True
        for attr in ('select_silhouette', 'select_border', 'select_crease', 'select_contour', 'select_external_contour'):
            setattr(ls, attr, True)
        ls.linestyle.color, ls.linestyle.thickness = (0, 0, 0), LINE_PX
        _render(scene, path)
        mode = 'freestyle'
    except Exception as exc:  # noqa: BLE001 - said in id.json; lines from depth steps stand in
        edge = np.zeros_like(fg)
        edge[:, 1:] |= np.abs(depth[:, 1:] - depth[:, :-1]) > 0.04
        edge[1:, :] |= np.abs(depth[1:, :] - depth[:-1, :]) > 0.04
        _gray(path, 1.0 - edge.astype(np.float32))
        mode = f'depth steps (freestyle failed: {exc!r})'[:200]
    finally:
        vl.material_override = None
        r.use_freestyle = False
    return mode


def _points(objs, depsgraph):
    out = []
    for o in objs:
        if o.type != 'MESH' or o.hide_render:
            continue
        ev = o.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            mw = ev.matrix_world
            step = max(1, len(mesh.vertices) // VERTS_PER_OBJECT)
            out += [tuple(mw @ mesh.vertices[i].co) for i in range(0, len(mesh.vertices), step)]
        finally:
            ev.to_mesh_clear()
    return out


def _support_top(point, scene_roles):
    """The top of the support (scene role counting for 'floor') under a point: its box top where the box covers the
    point's x, y; None when nothing supports it there."""
    tops = []
    for o in bpy.context.scene.objects:
        if o.type != 'MESH' or o.hide_render or not scene_roles.counts(o, 'floor'):
            continue
        corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
        xs, ys, zs = [c.x for c in corners], [c.y for c in corners], [c.z for c in corners]
        if min(xs) <= point[0] <= max(xs) and min(ys) <= point[1] <= max(ys):
            tops.append(max(zs))
    return max(tops) if tops else None


def _in_frame(scene, cam, objs, depsgraph):
    """Whether any rendering object of `objs` has its box corner or centre inside the camera's frame, in front."""
    from bpy_extras.object_utils import world_to_camera_view
    for o in objs:
        if o.hide_render:
            continue
        corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
        if max((a - b).length for a in corners for b in corners) < 1e-6:
            continue
        centre = sum(corners, Vector()) / 8
        for p in [centre, *corners]:
            v = world_to_camera_view(scene, cam, p)
            if v.z > 0 and 0 <= v.x <= 1 and 0 <= v.y <= 1:
                return True
    return False


def _states(scene, cam, things, index, depsgraph):
    """{thing id: {extension, contact, render, in_frame, parts}} for the claimed things (job picture3d.states), each
    measure of its own kind (part_state_core.KIND): extension for a part or a feature's parts against its owner's body;
    contact for the lowest point of anything named against the support under it; render on (it renders in frame), off
    (none of it renders at all) or unknown (it renders, out of frame: whether it is lit cannot be seen)."""
    import scene_roles
    from scene_index import resolve_ref
    out = {}
    for thing_id, want in things.items():
        objs = [o for i in want.get('ids') or [] for o in ((resolve_ref(i, index) or {}).get('objects') or [])]
        row = {'kind': want['kind'], 'built': bool(objs), 'extension': 'unknown', 'contact': 'unknown', 'render': 'unknown'}
        row['in_frame'] = _in_frame(scene, cam, objs, depsgraph) if objs else False
        if objs:
            row['render'] = 'on' if row['in_frame'] else ('off' if all(o.hide_render for o in objs) else 'unknown')
        parts = {}
        if want['kind'] in ('part', 'feature') and want.get('owner'):
            owner_objs = [o for o in scene.objects if o.get('studio_subject_id') == want['owner'] and o not in objs]
            owner = _points(owner_objs, depsgraph)
            for pid in want.get('parts') or []:
                pobjs = (resolve_ref(pid, index) or {}).get('objects') or []
                pts = _points(pobjs, depsgraph)
                ext, share = states.extension(pts, owner)
                low = min(pts, key=lambda p: p[2]) if pts else None
                touch, gap = states.contact(low[2] if low else None, _support_top(low, scene_roles) if low else None)
                parts[pid] = {'extension': ext, 'reach_share': share, 'contact': touch, 'gap_m': gap, 'built': bool(pobjs)}
            # the thing's state: what all of its measured parts agree on (a feature of four legs, two folded: unknown)
            for key in ('extension', 'contact'):
                votes = [p[key] for p in parts.values() if p[key] != 'unknown']
                row[key] = votes[0] if votes and all(v == votes[0] for v in votes) else 'unknown'
        elif objs:   # a subject or an element: how its lowest point stands on what supports it
            pts = _points(objs, depsgraph)
            low = min(pts, key=lambda p: p[2]) if pts else None
            row['contact'], row['gap_m'] = states.contact(low[2] if low else None, _support_top(low, scene_roles) if low else None)
        row['parts'] = parts
        out[thing_id] = row
    return out


def _outlines(labels, classes):
    """Where the named things' outlines run (each class's edge against anything else): the region a painting is laid
    back by. Outlines, not areas - a deck or a sea in the frame is painted with glints and waves inside it, and those
    edges must not pull the fit (2026-10-17: a landing deck's painted reflections dragged every fit to the search's edge)."""
    out = np.zeros(labels.shape, bool)
    for c in classes:
        mask = labels == c
        inner = mask.copy()
        inner[1:, :] &= mask[:-1, :]
        inner[:-1, :] &= mask[1:, :]
        inner[:, 1:] &= mask[:, :-1]
        inner[:, :-1] &= mask[:, 1:]
        out |= mask & ~inner
    return out


def _camera(scene):
    data = bpy.data.cameras.new('picture3d')
    if scene.camera is not None:
        for attr in ('sensor_width', 'sensor_height', 'sensor_fit', 'clip_start', 'clip_end'):
            setattr(data, attr, getattr(scene.camera.data, attr))
    else:
        data.sensor_width, data.sensor_fit = 36.0, 'AUTO'
    cam = bpy.data.objects.new('picture3d', data)
    scene.collection.objects.link(cam)
    return cam


def run(job):
    from frame_candidates import _pose
    import generate
    import cheat_scene
    import reveal
    import show_scene
    from scene_index import AnchorIndex
    started = time.monotonic()
    p3 = job['picture3d']
    out_root = Path(job['output_dir'])
    out_root.mkdir(parents=True, exist_ok=True)
    generate.prepare(job, out_root)
    move = job['shot']['camera']['move']
    if any(f.get('cheats') for f in move.get('frames') or []):
        cheat_scene.apply(job)
    shows = show_scene.apply(job)   # the openings this scene applies: each moment's picture shows the subject that way
    if shows:
        reveal.apply_camera_bound(job['shot'], {})
    count = job['shot']['duration_frames']
    shows = [{**r, 'open': r.get('open') or [round(float(r['at']) * (count - 1))] * 2} for r in shows or []]
    # see-through skins (an xray or transparency opening, a see_neighbours cheat) are faded after the look in a build
    # (show_scene.see_through); these stills have no look, so the skins open at a moment are left out of its renders
    see = list(job['shot'].get('_see_through') or [])

    def open_rows(frame):
        return [r for r in see if r['open'][1] <= frame and not (r.get('close') and frame >= r['close'][0])]

    def skins(frame):
        names = {n for r in open_rows(frame) for n in r['objects']}
        return [o for o in (bpy.data.objects.get(n) for n in names) if o is not None and not o.hide_render]

    def see_alpha(frame):   # the least see-through of the skins open at the frame (the more solid reading)
        return min([float(r['see_through']['alpha']) for r in open_rows(frame) if r.get('see_through')] or [1.0])
    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new('picture3d_world')
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = int(p3['width']), int(p3['height']), 100
    r.use_compositing = r.use_sequencer = False
    r.dither_intensity = 0.0
    cam = _camera(scene)
    scene.camera = cam
    index = AnchorIndex()
    results = []
    for m in p3['moments']:   # clay, depth, lines and states of every moment before the id passes recolour the scene
        folder = Path(m['out'])
        folder.mkdir(parents=True, exist_ok=True)
        scene.frame_set(m['frame'] + 1)
        _pose(cam, m['eye'], m['aim'], m['lens_mm'], m.get('roll_deg') or 0.0)
        hidden = skins(m['frame'])
        _clay_through(scene, folder / 'clay.png', hidden, see_alpha(m['frame']))
        for o in hidden:
            o.hide_render = True
        depth, fg = _depth(scene, folder / 'depth.png')
        mode = _lines(scene, folder / 'lines.png', depth, fg)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        found = _states(scene, cam, p3.get('states') or {}, index, depsgraph)
        (folder / 'state.json').write_text(json.dumps({'frame': m['frame'], 'things': found}, ensure_ascii=False, indent=1))
        for o in hidden:
            o.hide_render = False
        results.append({'moment': m, 'lines': mode, 'skins': [o.name for o in hidden]})
    import frame_probe
    probe = {'subjects': list(p3.get('subjects') or []), 'key_parts': [{'id': i} for i in p3.get('ids') or []], 'allow_unknown': True}
    classes, keys, concealed = frame_probe._classes(probe)
    colour = frame_probe._setup(scene, classes, keys, concealed)
    W, H = int(p3['width']), int(p3['height'])
    names_of = lambda colour: list(colour)   # noqa: E731 - frame_probe._decode's class order
    for res in results:
        m = res['moment']
        folder = Path(m['out'])
        scene.frame_set(m['frame'] + 1)
        _pose(cam, m['eye'], m['aim'], m['lens_mm'], m.get('roll_deg') or 0.0)
        hidden = [bpy.data.objects[n] for n in res['skins']]
        # a key part that is itself see-through is read where it is drawn, the rest through the skins (frame_probe's rule)
        found = frame_probe._faded_labels(scene, folder / 'id_drawn.png', hidden, classes, colour)
        faded = {f'key:{k}' for k in found}
        drawn = {n: v for k, labels_f in found.items() for n, v in frame_probe._shapes(labels_f, names_of(colour), [0.0, 0.0, 1.0, 1.0]).items()
                 if n == f'key:{k}'}
        for o in hidden:
            o.hide_render = True
        labels, names = frame_probe._decode(frame_probe._render(scene, folder / 'id.png'), colour)
        for o in hidden:
            o.hide_render = False
        shapes = {**frame_probe._shapes(labels, names, [0.0, 0.0, 1.0, 1.0]), **{k: v for k, v in drawn.items() if k in faded}}
        h, w = labels.shape
        scale = (H / h) * (W / w)
        visible = {}
        for name, shape in shapes.items():
            if name.startswith('key:') and shape.get('px'):
                box = shape['bbox']
                visible[name[4:]] = {'px': round(shape['px'] * scale), 'box': box,
                                     'thick_px': round(min((box[2] - box[0]) * W, (box[3] - box[1]) * H), 1)}
        named = [i for i, n in enumerate(names) if n == 'subject' or n.startswith('key:')]
        region = _outlines(labels, named)
        ys = np.minimum((np.arange(H) * h / H).astype(int), h - 1)
        xs = np.minimum((np.arange(W) * w / W).astype(int), w - 1)
        _gray(folder / 'region.png', region[ys][:, xs].astype(np.float32))
        import show_core
        info = {'frame': m['frame'], 'concept_id': m.get('concept_id'), 'at': m['at'],
                'opening': show_core.applied_at(shows, m['frame']),   # as applied here, read by frame_targets.derive_from_passes
                'pose': {k: m[k] for k in ('eye', 'aim', 'lens_mm', 'roll_deg', 'view') if k in m},
                'size': [W, H], 'row': {'frame': m['frame'], 'shapes': shapes}, 'visible': visible, 'keys': keys,
                'lines': res['lines']}
        (folder / 'id.json').write_text(json.dumps(info, ensure_ascii=False, indent=1))
    summary = {'shot_id': job['shot_id'], 'moments': [r['moment']['at'] for r in results], 'keys': keys,
               'seconds': round(time.monotonic() - started, 1)}
    (out_root / 'passes.json').write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == '__main__' and '--' in sys.argv:
    run(json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text()))
