"""Explainer graphics (shot.graphics): arrows, dimension lines, outlines, highlights, drawn-on lines - as Grease
Pencil, built at build time and rendered only into their own transparent layer (render_graphics.py).

They are never part of the beauty render, a control pass or a generative input: every graphic object is
studio_scene_role 'graphic' and hide_render in the saved scene. The edit composites the graphics layer over the
shot (under labels and captions), so they get no bloom, DOF or motion blur and a generated clip is never asked
to reproduce them. Positions come from anchors (AnchorIndex, at the graphic's first frame); the camera at that
frame orients arrow heads and dimension ticks. Visibility: the drawing exists from start_frame and an empty
keyframe at end_frame removes it; `draw_on_s` grows strokes with a Build modifier.

space 'screen' (arrows): drawn in the frame, not the world - a camera-parented drawing per frame on a plane just past
the near clip, so it keeps a constant on-screen size and never sits on the line the camera flies along. Measured on
samsung s01: a world arrow on the dive axis covered the focus of expansion (it reads as a seam opening and closing).
Legibility is checked per frame (LEGIBILITY): length, head/shaft ratio, distance from the focus of expansion and the
angle to the optical flow there; a failing arrow stops the build (GRAPHIC_ILLEGIBLE).
"""
from __future__ import annotations

import json

import bpy
from mathutils import Vector

from scene_index import AnchorIndex

ROLE = 'studio_scene_role'
LAYER = 'studio_graphic_layer'
TEXT = 'studio_graphic_text'  # a dimension's value: world end points + text, stamped in 2D after the layer renders  # built here, rendered only by render_graphics (an authored role-'graphic' mesh is not)
PREFIX = 'StudioGraphic_'
DEFAULT_COLOR = (0.95, 0.15, 0.10)
DEFAULT_RADIUS_M = 0.04


def _points(index, refs):
    out = []
    for ref in refs:
        if isinstance(ref, (list, tuple)):
            out.append(Vector(ref))
            continue
        obj, point = index.resolve(ref)
        if obj is None:
            raise ValueError(f'GRAPHICS: anchor not found: {ref}')
        out.append(Vector(point))
    return out


def _linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _material(name, rgba):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        bpy.data.materials.create_gpencil_data(mat)
    mat.grease_pencil.color = rgba
    return mat


def _object(spec):
    data = bpy.data.grease_pencils.new(PREFIX + spec['graphic_id'])
    obj = bpy.data.objects.new(PREFIX + spec['graphic_id'], data)
    bpy.context.scene.collection.objects.link(obj)
    obj[ROLE] = 'graphic'
    obj[LAYER] = True
    obj['studio_id'] = PREFIX + spec['graphic_id']
    obj.hide_render = True  # beauty render, control passes and previs never see it; render_graphics unhides
    colour = [_linear(c) for c in spec.get('color_srgb', DEFAULT_COLOR)]  # Grease Pencil colours are scene-linear
    data.materials.append(_material(f"{PREFIX}{spec['graphic_id']}", (*colour, float(spec.get('opacity', 1.0)))))
    return obj, data


def _layer(data, start, end):
    """A layer visible on [start, end): opacity keyed 0 -> 1 at start and back to 0 at end (CONSTANT), which
    also hides what a Line Art modifier writes into the layer outside the window."""
    layer = data.layers.new('graphic')
    layer.use_lights = False  # flat colour: the graphics render has no lights (measured: lit strokes came out near black)
    drawing = layer.frames.new(1).drawing
    for frame, value in ((1, 0.0), (start + 1, 1.0)) + (((end + 1, 0.0),) if end is not None else ()):
        layer.opacity = value
        data.keyframe_insert(f'layers["{layer.name}"].opacity', frame=frame)
    from scene_tools import curves
    for curve in curves(data.animation_data.action if data.animation_data else None):
        for point in curve.keyframe_points:
            point.interpolation = 'CONSTANT'
    return drawing


def _stroke(drawing, points, radius):
    drawing.add_strokes([len(points)])
    stroke = drawing.strokes[len(drawing.strokes) - 1]
    for point, position in zip(stroke.points, points):
        point.position = tuple(position)
        point.radius = radius
    return stroke


def _side(a, b, camera):
    """Unit vector across the segment a->b, in the plane facing the camera (heads and ticks read flat on screen)."""
    along = (b - a).normalized()
    view = (camera.matrix_world.translation - (a + b) / 2).normalized() if camera else Vector((0, 0, 1))
    side = along.cross(view)
    return side.normalized() if side.length > 1e-6 else along.orthogonal().normalized()


def build(shot, cues=None):
    """Create every graphic of the shot; returns report rows. cues: camera cues for time_binding (cam-*)."""
    graphics = shot.get('graphics') or []
    if not graphics:
        return []
    scene = bpy.context.scene
    index = AnchorIndex()
    rows = []
    for spec in graphics:
        if spec.get('space') == 'screen':   # built after the look (its lens shift), by build_screen
            continue
        start, end = spec.get('start_frame', 0), spec.get('end_frame', shot['duration_frames'])
        binding = spec.get('time_binding')
        if binding and cues:
            start = cues[binding['start_cue_id']] + binding.get('start_offset_frames', 0)
            end = cues[binding['end_cue_id']] + binding.get('end_offset_frames', 0)
        if not 0 <= start < end <= shot['duration_frames']:
            raise ValueError(f"GRAPHICS: {spec['graphic_id']} resolves to [{start}, {end}) outside the shot")
        scene.frame_set(start + 1)
        obj, data = _object(spec)
        radius = float(spec.get('radius_m', DEFAULT_RADIUS_M))
        drawing = _layer(data, start, end if end < shot['duration_frames'] else None)
        kind = spec['kind']
        if kind == 'outline':
            target = index.resolve(spec['target'])[0] if isinstance(spec.get('target'), str) else None
            if target is None:
                raise ValueError(f"GRAPHICS: outline {spec['graphic_id']} needs a target object")
            art = obj.modifiers.new('outline', 'LINEART')
            art.source_type, art.source_object = 'OBJECT', target
            art.target_layer, art.target_material = data.layers[0].name, data.materials[0]
            art.radius = radius
        else:
            points = _points(index, spec['anchors'])
            if len(points) < 2:
                raise ValueError(f"GRAPHICS: {kind} {spec['graphic_id']} needs at least two anchors")
            _stroke(drawing, points, radius * (2.5 if kind == 'highlight' else 1.0))
            if kind == 'arrow':
                tip, before = points[-1], points[-2]
                back = (before - tip).normalized() * float(spec.get('head_m', 0.6))
                side = _side(before, tip, scene.camera) * float(spec.get('head_m', 0.6)) * 0.55
                _stroke(drawing, [tip + back + side, tip, tip + back - side], radius)
            elif kind == 'dimension':
                a, b = points[0], points[-1]
                tick = _side(a, b, scene.camera) * float(spec.get('tick_m', 0.4))
                _stroke(drawing, [a - tick, a + tick], radius); _stroke(drawing, [b - tick, b + tick], radius)
                if spec.get('text'):  # the value is drawn on the graphics layer next to the line (render_graphics + graphics.py)
                    text = f'{(b - a).length:.1f} m' if spec['text'] == 'auto' else spec['text']
                    shown = start + (max(1, round(spec['draw_on_s'] * scene.render.fps)) if spec.get('draw_on_s') else 0)
                    obj[TEXT] = json.dumps({'text': text, 'a': list(a), 'b': list(b), 'frames': [shown, end],
                                            'color_srgb': list(spec.get('color_srgb', DEFAULT_COLOR))})
        if spec.get('draw_on_s'):
            build_mod = obj.modifiers.new('draw_on', 'GREASE_PENCIL_BUILD')
            build_mod.mode, build_mod.transition = 'SEQUENTIAL', 'GROW'
            frames = max(1, round(spec['draw_on_s'] * scene.render.fps))
            build_mod.time_mode, build_mod.length = 'FRAMES', frames
            build_mod.use_restrict_frame_range = True
            build_mod.frame_start, build_mod.frame_end = start + 1, end + 1
        rows.append({'graphic_id': spec['graphic_id'], 'kind': kind, 'frames': [start, end], 'object': obj.name,
                     'strokes': len(drawing.strokes), **({'text': json.loads(obj[TEXT])['text']} if TEXT in obj else {})})
    scene.frame_set(1)
    return rows


LEGIBILITY = {'min_length_h': 0.08, 'head_ratio': (3.0, 4.5), 'min_foe_px_1080': 100.0, 'min_flow_angle_deg': 25.0}
FAN = 9   # strokes in an arrow head: EEVEE does not render Grease Pencil fills, a fan of strokes reads as a solid head


def _resolve_frames(spec, shot, cues):
    start, end = spec.get('start_frame', 0), spec.get('end_frame', shot['duration_frames'])
    binding = spec.get('time_binding')
    if binding and cues:
        start = cues[binding['start_cue_id']] + binding.get('start_offset_frames', 0)
        end = cues[binding['end_cue_id']] + binding.get('end_offset_frames', 0)
    if not 0 <= start < end <= shot['duration_frames']:
        raise ValueError(f"GRAPHICS: {spec['graphic_id']} resolves to [{start}, {end}) outside the shot")
    return start, end


def _frame_plane(camera, scene, depth):
    """Camera-local corners of the rendered frame at `depth` (lens shift included): top-left, top-right, bottom-left."""
    corners = [Vector(c) for c in camera.data.view_frame(scene=scene)]   # tr, br, bl, tl at the sensor distance
    k = depth / abs(corners[0].z)
    tr, br, bl, tl = (c * k for c in corners)
    return tl, tr, bl


def _to_local(plane, x, y):
    tl, tr, bl = plane
    return tl + (tr - tl) * x + (bl - tl) * y


def _foe(scene, camera, frame):
    """Focus of expansion (normalized frame coords, top-left) of the camera's motion at `frame`, or None when still."""
    from bpy_extras.object_utils import world_to_camera_view
    scene.frame_set(frame)
    a = camera.matrix_world.translation.copy()
    scene.frame_set(frame + 1)
    b = camera.matrix_world.translation.copy()
    scene.frame_set(frame)
    move = b - a
    if move.length < 1e-6:
        return None
    ahead = world_to_camera_view(scene, camera, a + move.normalized() * 1e4)
    if ahead.z <= 0:   # moving backwards: the focus of contraction behind the camera mirrors through the centre
        ahead = world_to_camera_view(scene, camera, a - move.normalized() * 1e4)
    return (ahead.x, 1 - ahead.y)


def _legibility(spec, scene, camera, frames, tail, tip, shaft_h, head_len_h):
    import math
    w, h = scene.render.resolution_x, scene.render.resolution_y
    rows, fails = [], []
    for f in frames:
        foe = _foe(scene, camera, f)
        mid = ((tail[0] + tip[0]) / 2 * w, (tail[1] + tip[1]) / 2 * h)
        direction = ((tip[0] - tail[0]) * w, (tip[1] - tail[1]) * h)
        length_h = math.hypot(*direction) / h
        row = {'frame': f, 'length_h': round(length_h, 4)}
        if foe:
            radial = (mid[0] - foe[0] * w, mid[1] - foe[1] * h)
            row['foe_px_1080'] = round(math.dist(mid, (foe[0] * w, foe[1] * h)) * 1080 / w, 1)
            # distance from the FOE to the arrow's line (the arrow must not lie on the flight line)
            seg = math.hypot(*direction)
            cross = abs(direction[0] * (foe[1] * h - tail[1] * h) - direction[1] * (foe[0] * w - tail[0] * w)) / max(seg, 1e-9)
            row['foe_line_px_1080'] = round(cross * 1080 / w, 1)
            n = math.hypot(*radial)
            if n > 1e-6:
                cosang = abs(radial[0] * direction[0] + radial[1] * direction[1]) / (n * seg)
                row['flow_angle_deg'] = round(math.degrees(math.acos(min(1.0, cosang))), 1)
        rows.append(row)
    ratio = head_len_h / shaft_h
    if min(r['length_h'] for r in rows) < LEGIBILITY['min_length_h']:
        fails.append(f"shorter than {LEGIBILITY['min_length_h']} of the frame height")
    if not LEGIBILITY['head_ratio'][0] <= ratio <= LEGIBILITY['head_ratio'][1]:
        fails.append(f'head/shaft {ratio:.2f} outside {LEGIBILITY["head_ratio"]}')
    near = [r['frame'] for r in rows if r.get('foe_line_px_1080', 1e9) < LEGIBILITY['min_foe_px_1080']]
    if near:
        fails.append(f"lies on the flight line (focus of expansion < {LEGIBILITY['min_foe_px_1080']} px of 1080) at frames {near[:5]}")
    parallel = [r['frame'] for r in rows if r.get('flow_angle_deg', 90) < LEGIBILITY['min_flow_angle_deg']]
    if parallel:
        fails.append(f"runs with the optical flow (< {LEGIBILITY['min_flow_angle_deg']} deg) at frames {parallel[:5]}")
    return {'head_ratio': round(ratio, 2), 'min_length_h': min(r['length_h'] for r in rows),
            'min_foe_line_px_1080': min((r['foe_line_px_1080'] for r in rows if 'foe_line_px_1080' in r), default=None),
            'min_flow_angle_deg': min((r['flow_angle_deg'] for r in rows if 'flow_angle_deg' in r), default=None), 'failures': fails}


def _screen_arrow(spec, scene, start, end):
    import math
    camera = scene.camera
    obj, data = _object(spec)
    obj.parent = camera
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis.identity()
    depth = max(0.02, camera.data.clip_start * 1.5)
    layer = data.layers.new('graphic')
    layer.use_lights = False
    (tx, ty), (hx, hy) = spec['points_2d']
    w, h = scene.render.resolution_x, scene.render.resolution_y
    shaft_h = float(spec.get('shaft_frac', 0.0075))            # stroke width as a share of the frame height
    head_len_h = shaft_h * float(spec.get('head_ratio', 3.5))
    head_w_h = shaft_h * 3.0
    fade = int(spec.get('fade_frames', 8))
    draw = max(1, round(spec['draw_on_s'] * scene.render.fps)) if spec.get('draw_on_s') else 1
    layer.frames.new(1)                                        # empty before the arrow starts
    span = (hx - tx) * w, (hy - ty) * h
    length_px = math.hypot(*span)
    ux, uy = span[0] / length_px, span[1] / length_px          # pixel-space unit vector tail -> tip
    for f in range(start, end):
        grow = min(1.0, (f - start + 1) / draw)
        scene.frame_set(f + 1)
        plane = _frame_plane(camera, scene, depth)             # lens shift may be keyed per frame
        frame_h_local = (plane[2] - plane[0]).length
        radius = shaft_h * frame_h_local / 2
        tip = (tx + (hx - tx) * grow, ty + (hy - ty) * grow)
        head_px = head_len_h * h
        base = (tip[0] - ux * head_px / w, tip[1] - uy * head_px / h)
        drawing = layer.frames.new(f + 1).drawing
        to = lambda p: _to_local(plane, p[0], p[1])
        _stroke(drawing, [to((tx, ty)), to(base if grow * length_px > head_px else (tx, ty))], radius)
        nx, ny = -uy, ux
        for k in range(FAN):
            t = k / (FAN - 1) - 0.5
            corner = (base[0] + nx * t * head_w_h * h / w, base[1] + ny * t * head_w_h)
            _stroke(drawing, [to(tip), to(corner)], radius * 0.9)
    if end + 1 <= scene.frame_end + 1:
        layer.frames.new(end + 1)                              # removed at end_frame
    for frame, value in ((start + 1, 0.0), (start + 1 + fade, 1.0), (max(start + 1 + fade, end + 1 - fade), 1.0), (end + 1, 0.0)):
        layer.opacity = value
        data.keyframe_insert(f'layers["{layer.name}"].opacity', frame=frame)
    from scene_tools import curves
    for curve in curves(data.animation_data.action):
        for point in curve.keyframe_points:
            point.interpolation = 'LINEAR'                     # fades, not pops (a pop reads as a cut in the road)
    legibility = _legibility(spec, scene, camera, list(range(start + 1, end + 1)), (tx, ty), (hx, hy), shaft_h, head_len_h)
    if legibility['failures']:
        raise ValueError(f"GRAPHIC_ILLEGIBLE: {spec['graphic_id']}: " + '; '.join(legibility['failures']))
    return obj, data, legibility


def build_screen(shot, cues=None):
    """Screen-space graphics (space 'screen'), after the look has keyed the camera's lens shift. Report rows."""
    rows = []
    scene = bpy.context.scene
    for spec in shot.get('graphics') or []:
        if spec.get('space') != 'screen':
            continue
        if spec['kind'] != 'arrow':
            raise ValueError(f"GRAPHICS: {spec['graphic_id']}: space 'screen' draws arrows only")
        start, end = _resolve_frames(spec, shot, cues)
        obj, data, legibility = _screen_arrow(spec, scene, start, end)
        rows.append({'graphic_id': spec['graphic_id'], 'kind': 'arrow', 'space': 'screen', 'frames': [start, end], 'object': obj.name,
                     'legibility': legibility})
    scene.frame_set(1)
    return rows
