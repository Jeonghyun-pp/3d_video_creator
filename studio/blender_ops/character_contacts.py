"""Character contacts (runs inside Blender): a person's hands and feet on what they hold or stand on, the body clear of
everything else (shot.scene.characters[].contacts and stance; host side studio/characters.py).

Why (2026-10-19, the bike reel): the mechanic's hands were put on the lever and the crank by a project's own author
script, and nothing kept the torso out of the frame - it went through it in six shots. A contact is declared on the
character: a limb, the thing (or a point) it is on, when. The engine turns each limb with inverse kinematics (two bones
of the arm or leg and the hand or foot itself, a pole for the elbow or the knee), the limb's end following its target
frame by frame as the target moves; a stance lowers and bends the body (crouch); and the body is moved, by the least
distance, to where no part of it is inside a solid thing while every contact still holds (stand_clear). What cannot hold
says so: CONTACT_UNREACHABLE (the target is farther than the limb), CONTACT_MISSED (the limb's end misses its target
by more than CONTACT_ERROR_M).

Runs in the build's prepare step (generate.prepare), after the author script, fill and drives: the targets are where
the author and the drives put them. A contact on a moving part (a crank) follows it; a point stays.
"""
from __future__ import annotations

import json
import math

import bpy
from mathutils import Matrix, Vector

import character_clearance as clearance

# limb -> (end bone in the rig's naming spellings, its chain: the end bone, forearm/shin, upper arm/thigh)
LIMBS = {'left_hand': ('LeftHand', 'hand.L'), 'right_hand': ('RightHand', 'hand.R'),
         'left_foot': ('LeftFoot', 'foot.L'), 'right_foot': ('RightFoot', 'foot.R')}
CHAIN = 3
STANCES = {   # the hips lowered by this share of the height, the spine bent forward this far (split over its bones)
    'stand': {'hips_drop': 0.0, 'bend_deg': 0.0},
    'crouch': {'hips_drop': 0.2, 'bend_deg': 45.0},    # knees well bent, the back over them: hands reach about knee height
                                                        # (the bike author's crouch, hips -0.20 m, still lowered the bike 0.25 m)
}
SPINE = ('Spine', 'Spine1', 'Spine2')
CONTACT_ERROR_M = 0.01    # a limb's end this close to its target holds it
REACH_SHARE = 0.98        # a target past this share of the limb's length is out of reach (a straight limb cannot bend)
CLEAR_STEP_M = 0.05       # stand_clear tries the body this much apart, ring by ring ...
CLEAR_REACH_M = 0.5       # ... up to this far from where it was declared (a body declared half into a crate: 0.45 m)
CLEAR_DIRECTIONS = 8
CLEAR_TURNS_DEG = (0, 20, -20, 45, -45, 90, -90)   # ... each place facing as declared, then turned (side-on to what it reaches)
SEARCH_FRAMES = 3   # frames each place is read at (the one found is confirmed at clearance.SAMPLES)
ARMS = (('left_hand', ('LeftArm', 'upper_arm.L')), ('right_hand', ('RightArm', 'upper_arm.R')))
HANG_FORWARD = 0.25   # a free arm of a lowered body hangs down, this much forward of straight down


def _bone(armature, names):
    for prefix in ('', 'mixamorig:'):
        for name in names:
            if armature.pose.bones.get(prefix + name):
                return armature.pose.bones[prefix + name]
    return None


def _target(ident):
    """(root object, its mesh objects) a contact's id names (scene_index.resolve_group), (None, []) when nothing is built."""
    from scene_index import AnchorIndex, resolve_group
    roots = resolve_group(ident, AnchorIndex())
    return (roots[0] if roots else None), [o for r in roots for o in (r, *r.children_recursive) if o.type == 'MESH']


def _anchor(contact, root, objects, reach_from):
    """(object the contact rides on or None, point in its coordinates - or the world's): a point as given; `local` in the
    target's own coordinates (a pedal at the end of a crank arm); `offset_m` from its box centre; else the side of its
    surface facing the limb (the point nearest `reach_from`, the shoulder or hip: a hand takes a handle from where it
    comes). Riding on the object, the contact follows whatever moves it after - drives, cheats, an explode, a reveal."""
    if isinstance(contact['target'], list):
        return None, Vector(contact['target'])
    if contact.get('local') is not None:
        return root, Vector(contact['local'])
    corners = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    if contact.get('offset_m') is not None:
        return root, root.matrix_world.inverted() @ ((lo + hi) / 2 + Vector(contact['offset_m']))
    from mathutils.bvhtree import BVHTree
    dg = bpy.context.evaluated_depsgraph_get()
    best = None
    for obj in objects:
        found = BVHTree.FromObject(obj, dg).find_nearest(obj.matrix_world.inverted() @ reach_from)[0]
        if found is not None and (best is None or (obj.matrix_world @ found - reach_from).length < best[2]):
            best = (obj, found.copy(), (obj.matrix_world @ found - reach_from).length)
    return (best[0], best[1]) if best else (root, root.matrix_world.inverted() @ ((lo + hi) / 2))


def _empty(name, parent=None):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    obj['studio_scene_role'] = 'helper'
    obj.empty_display_size = 0.03
    if parent is not None:
        obj.parent = parent
    return obj


def _turn(armature, pb, axis, degrees):
    """Turn a pose bone about a world axis through its head (armature space), keeping its children with it."""
    to_arm = armature.matrix_world.inverted().to_3x3()
    head = pb.matrix.translation.copy()
    turn = Matrix.Rotation(math.radians(degrees), 4, (to_arm @ axis).normalized())
    pb.matrix = Matrix.Translation(head) @ turn @ Matrix.Translation(-head) @ pb.matrix
    bpy.context.view_layer.update()


def _facing(armature):
    """Where the body faces now, on the ground: from the heels to the toes of both feet (a rest pose and an action of
    the same library rig can face different ways - the bike author turned its rest pose a quarter turn by hand)."""
    total = Vector((0.0, 0.0, 0.0))
    for names in (('LeftFoot', 'foot.L'), ('RightFoot', 'foot.R')):
        foot = _bone(armature, names)
        if foot is not None:
            total += armature.matrix_world @ foot.tail - armature.matrix_world @ foot.head
    total.z = 0.0
    return total.normalized() if total.length > 1e-6 else Vector((0.0, -1.0, 0.0))


def _face(root, armature, row):
    """Turn the root so the pose now faces facing_deg (0 = +X, counter-clockwise), as characters.build turns an action."""
    want = math.radians(row.get('facing_deg', 0.0))
    now = _facing(armature)
    root.rotation_euler.z += want - math.atan2(now.y, now.x)
    bpy.context.view_layer.update()


def _stance(root, armature, row):
    """Hold the rest pose with the stance's hips drop and spine bend (keyed once: a contact pose is not an action);
    returns {foot limb: where its toes stood before the hips came down} - the feet stay planted there - or {} standing."""
    stance = STANCES[row.get('stance') or 'stand']
    if not stance['hips_drop'] and not stance['bend_deg']:
        bpy.context.scene.frame_set(1)
        _face(root, armature, row)   # the action's own first pose faces facing_deg (a library's actions differ)
        return {}   # standing: the action plays on; the contacts' IK takes the limbs over
    ad = armature.animation_data or armature.animation_data_create()
    ad.use_nla = False
    ad.action = bpy.data.actions.new(f"{row['id']}_stance")
    for pb in armature.pose.bones:
        pb.matrix_basis.identity()
    bpy.context.view_layer.update()
    _face(root, armature, row)
    height = float(row.get('height_m', 1.75))
    forward = _facing(armature)
    side = Vector((0.0, 0.0, 1.0)).cross(forward).normalized()   # the body's left: turning about it brings the head forward
    feet = {limb: armature.matrix_world @ chain[0].tail for limb in ('left_foot', 'right_foot') if (chain := _limb(armature, limb))}
    hips = _bone(armature, ('Hips', 'hips'))
    if hips is not None and stance['hips_drop']:
        drop = armature.matrix_world.inverted().to_3x3() @ Vector((0.0, 0.0, -stance['hips_drop'] * height))
        hips.matrix = Matrix.Translation(drop) @ hips.matrix
        bpy.context.view_layer.update()
    spine = [b for b in (_bone(armature, (n,)) for n in SPINE) if b is not None]
    for pb in spine:
        _turn(armature, pb, side, stance['bend_deg'] / max(1, len(spine)))
    held = {c['limb'] for c in row.get('contacts') or []}
    for limb, names in ARMS:   # an arm that holds nothing hangs (the rest pose holds it out level)
        arm = _bone(armature, names)
        if arm is None or limb in held:
            continue
        now = (armature.matrix_world @ arm.tail - armature.matrix_world @ arm.head).normalized()
        want = (Vector((0.0, 0.0, -1.0)) + forward * HANG_FORWARD).normalized()
        axis = now.cross(want)
        if axis.length > 1e-6:
            _turn(armature, arm, axis.normalized(), math.degrees(now.angle(want)))
    for pb in armature.pose.bones:
        pb.keyframe_insert('location', frame=1)
        pb.keyframe_insert('rotation_quaternion' if pb.rotation_mode == 'QUATERNION' else 'rotation_euler', frame=1)
    return feet


def _limb(armature, limb):
    end = _bone(armature, LIMBS[limb])
    chain = [end]
    while end is not None and len(chain) < CHAIN and chain[-1].parent is not None:
        chain.append(chain[-1].parent)
    return chain if end is not None and len(chain) == CHAIN else None


def _pole(root, armature, chain, limb):
    """Where the elbow or the knee points: an elbow back and out from the body, a knee forward."""
    forward = _facing(armature)
    side = Vector((0.0, 0.0, 1.0)).cross(forward).normalized() * (1 if limb.startswith('left') else -1)   # out from the body
    joint = armature.matrix_world @ chain[1].head
    reach = (armature.matrix_world @ chain[2].head - armature.matrix_world @ chain[0].head).length or 0.3
    return joint + (forward * reach if limb.endswith('foot') else (-forward * 0.6 + side * 0.4) * reach)


def _limb_length(armature, chain):
    scale = armature.matrix_world.to_scale()[0]
    return sum(b.length for b in chain) * scale


def apply(job):
    """Pose every character with contacts or a stance; returns (report rows, {character id: {'grips': [(the holding
    limb's end bone, its target empty)]}} - character_clearance lets a holding hand close round what it holds)."""
    rows = [r for r in ((job['shot'].get('scene') or {}).get('characters') or []) if r.get('contacts') or (r.get('stance') or 'stand') != 'stand']
    report, held = [], {}
    scene = bpy.context.scene
    count = job['shot']['duration_frames']
    for row in rows:
        root, armature = bpy.data.objects.get(row['id']), bpy.data.objects.get(f"{row['id']}.rig")
        if root is None or armature is None:
            raise ValueError(f"CONTACT_UNREACHABLE: character {row['id']} is not in the scene (scene.characters builds it)")
        feet = _stance(root, armature, row)
        placement = _placement(row, root)
        targets, limbs = [], []
        for side, at in feet.items():   # a lowered body keeps its feet where they stood, unless a contact moves them
            if any(c['limb'] == side for c in row.get('contacts') or []):
                continue
            planted = _empty(f"{row['id']}/plant_{side}", parent=root)
            planted.matrix_world = Matrix.Translation(at)
            limbs.append((side, _limb(armature, side), planted, None))
        for contact in row.get('contacts') or []:
            chain = _limb(armature, contact['limb'])
            if chain is None:
                raise ValueError(f"CONTACT_UNREACHABLE: {row['id']} has no {contact['limb']} bones ({LIMBS[contact['limb']]})")
            point_target = isinstance(contact['target'], list)
            on, objects = (None, []) if point_target else _target(contact['target'])
            if not point_target and on is None:
                raise ValueError(f"CONTACT_UNREACHABLE: {row['id']}'s {contact['limb']} target {contact['target']} is not in the scene")
            scene.frame_set(1)
            rides, at = _anchor(contact, on, objects, armature.matrix_world @ chain[2].head)
            target = _empty(f"{row['id']}/{contact['limb']}_on", parent=rides)
            target.location = at   # in the coordinates of what it rides on (it moves with it), or the world's
            held.setdefault(row['id'], {'grips': []})['grips'].append((chain[0].name, target.name))
            limbs.append((contact['limb'], chain, target, contact))
            targets.append((contact, chain, target))
        for limb, chain, target, contact in limbs:
            pole = _empty(f"{row['id']}/{limb}_pole", parent=root)
            pole.matrix_world = Matrix.Translation(_pole(root, armature, chain, limb))
            ik = chain[0].constraints.new('IK')
            ik.target, ik.pole_target = target, pole
            ik.chain_count, ik.use_stretch, ik.iterations, ik.use_tail = CHAIN, False, 128, True
            ik.pole_angle = math.radians(-90)
        scene.frame_set(1)
        moved = stand_clear(row, placement, armature, targets, held.get(row['id']), count)
        errors = contact_errors(armature, targets, count)   # where it stands now: the place found, else the nearest that
        far = [e for e in errors if e['error_m'] > CONTACT_ERROR_M]   # held every contact, else where it was declared
        report.append({'id': row['id'], 'stance': row.get('stance') or 'stand',
                       **({'search': moved} if moved.get('found') is False else {'moved': moved}),
                       'contacts': [{'limb': c['limb'], 'target': c['target']} for c, _, _ in targets], 'errors': errors})
        if far:
            worst = max(far, key=lambda e: e['error_m'])
            reach = worst.get('reach_m')
            code = 'CONTACT_UNREACHABLE' if reach and worst['distance_m'] > REACH_SHARE * reach else 'CONTACT_MISSED'
            raise ValueError(f"{code}: {row['id']}'s {worst['limb']} is {worst['error_m']:.3f} m from {worst['target']} at shot frame "
                             f"{worst['frame']} (its shoulder or hip {worst['distance_m']:.2f} m away, the limb {reach:.2f} m long) - place "
                             'the character nearer (at), lower it (stance) or touch a nearer point')
    scene.frame_set(1)
    return report, held


def contact_errors(armature, targets, count, frames=None):
    """Per contact, its worst frame: the limb end's distance to its target, the root of the limb to it, the limb's length."""
    scene = bpy.context.scene
    out = []
    for contact, chain, target in targets:
        worst = None
        for f in frames or clearance.frames_of(count):
            scene.frame_set(f + 1)
            end = armature.matrix_world @ chain[0].tail
            base = armature.matrix_world @ chain[2].head
            goal = target.matrix_world.translation
            row = {'limb': contact['limb'], 'target': contact['target'], 'frame': f, 'error_m': round((end - goal).length, 4),
                   'distance_m': round((goal - base).length, 4), 'reach_m': round(_limb_length(armature, chain), 4)}
            if worst is None or row['error_m'] > worst['error_m']:
                worst = row
        if worst:
            out.append(worst)
    scene.frame_set(1)
    return out


def _placement(row, root):
    """An empty the character's root hangs from, at where it was declared: stand_clear moves and turns it, so the root
    keeps any keys an author gave it (2026-10-19 review: a keyed root put back every frame the search set)."""
    pivot = root.matrix_world.translation.copy()
    placement = _empty(f"{row['id']}/placement")
    placement.matrix_world = Matrix.Translation(pivot)
    bpy.context.view_layer.update()
    world = root.matrix_world.copy()
    root.parent = placement
    root.matrix_parent_inverse = placement.matrix_world.inverted()
    root.matrix_world = world
    placement['studio_pivot'] = list(pivot)
    return placement


def _place(placement, offset, yaw):
    pivot = Vector(placement['studio_pivot'])
    placement.matrix_world = Matrix.Translation(pivot + offset) @ Matrix.Rotation(math.radians(yaw), 4, 'Z')
    bpy.context.view_layer.update()


def stand_clear(row, placement, armature, targets, held, count):
    """Move the body by the least distance (rings of CLEAR_STEP_M up to CLEAR_REACH_M, CLEAR_DIRECTIONS a ring, each
    facing as declared and then turned by CLEAR_TURNS_DEG) to where it passes through nothing solid and every contact
    holds: each place read at SEARCH_FRAMES frames, the one found confirmed at every frame the clearance check reads.
    {offset_m, distance_m, turned_deg}; or, when no place works, {found: False, tried, contacts_lost, closest} with the
    body left at the place that held every contact and passed through least (the clearance check then says where it is
    through) - or where it was declared, when no place held them (the contacts then say they cannot reach)."""
    meshes = clearance.characters().get(row['id'], [])
    if not meshes:
        return {}
    grips = [(end, bpy.data.objects[name]) for end, name in (held or {}).get('grips', [])]
    every = clearance.frames_of(count)
    quick = clearance.frames_of(count, SEARCH_FRAMES)
    cache = {}   # the solids at a frame: moving the body moves none of them
    tried = [(0.0, Vector((0.0, 0.0, 0.0)))]
    ring = CLEAR_STEP_M
    while ring <= CLEAR_REACH_M + 1e-9:
        tried += [(ring, Vector((ring * math.cos(2 * math.pi * k / CLEAR_DIRECTIONS), ring * math.sin(2 * math.pi * k / CLEAR_DIRECTIONS), 0.0)))
                  for k in range(CLEAR_DIRECTIONS)]
        ring += CLEAR_STEP_M
    unreached, closest = 0, None
    for distance, offset in tried:
        for yaw in CLEAR_TURNS_DEG:   # a body turned sideways keeps its knees off what it reaches into
            _place(placement, offset, yaw)
            if any(e['error_m'] > CONTACT_ERROR_M for e in contact_errors(armature, targets, count, quick)):
                unreached += 1
                continue
            if not clearance.measure(row['id'], meshes, quick, grips, cache, first=True):
                if any(e['error_m'] > CONTACT_ERROR_M for e in contact_errors(armature, targets, count, every)):
                    continue
                through = clearance.measure(row['id'], meshes, every, grips, cache)
                if not through:
                    return {'offset_m': [round(x, 3) for x in offset], 'distance_m': round(distance, 3), 'turned_deg': yaw}
            else:
                through = clearance.measure(row['id'], meshes, quick, grips, cache)
            depth = max((r['depth_m'] if r['depth_m'] is not None else 1.0) for r in through)
            if closest is None or depth < closest['depth_m']:
                closest = {'offset': offset.copy(), 'turned_deg': yaw, 'depth_m': round(depth, 3), 'through': through[:2]}
    if closest:
        _place(placement, closest['offset'], closest['turned_deg'])
    else:
        _place(placement, Vector((0.0, 0.0, 0.0)), 0)
    shown = {**closest, 'offset': [round(x, 3) for x in closest['offset']]} if closest else None
    return {'found': False, 'tried': len(tried) * len(CLEAR_TURNS_DEG), 'contacts_lost': unreached, 'closest': shown}


def write(output, report):
    (output / 'character_pose.json').write_text(json.dumps({'characters': report}, ensure_ascii=False, indent=1))
