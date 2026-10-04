"""Run inside Blender (headless): geometry accuracy + determinism checks for studio_blender_ops/modeling."""
from pathlib import Path
import json
import math
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
from modeling import (box, build_part, build_subject, clear_canopy, i_section, loft, mesh_hash, panel_lines,  # noqa: E402
                      profile_extrude, rebuild_parts, remove_subject, revolve, scene_parts, sweep, wall, wing)

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
report = {}


def bm_of(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    return bm


def watertight(obj):
    bm = bm_of(obj)
    ok = all(len(e.link_faces) == 2 for e in bm.edges) and not any(e.is_boundary for e in bm.edges)
    bm.free()
    return ok


def volume(obj):
    bm = bm_of(obj)
    v = bm.calc_volume(signed=True)
    bm.free()
    return v


def shoelace(pts):
    return 0.5 * abs(sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts))))


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


# 1. loft ---------------------------------------------------------------------------------------
a, b = 0.5, 0.3
tube = loft('t_tube', {'stations': [{'s': 0, 'section': {'type': 'ellipse', 'a': a, 'b': b}},
                                    {'s': 2, 'section': {'type': 'ellipse', 'a': a, 'b': b}}]})
bm = bm_of(tube)
cut = bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 1, 0), plane_no=(0, 1, 0))
pts = sorted({(round(v.co.x, 9), round(v.co.z, 9)) for v in cut['geom_cut'] if isinstance(v, bmesh.types.BMVert)},
             key=lambda p: math.atan2(p[1], p[0]))
bm.free()
area = shoelace(pts)
check('loft_mid_area_ratio', abs(area / (math.pi * a * b) - 1) < 0.01, round(area / (math.pi * a * b), 5))
check('loft_volume_ratio', abs(volume(tube) / (2 * math.pi * a * b) - 1) < 0.01, round(volume(tube) / (2 * math.pi * a * b), 5))
nose = loft('t_body', {'stations': [
    {'s': -1.0, 'section': {'type': 'ellipse', 'a': 0, 'b': 0}},
    {'s': -0.6, 'section': {'type': 'superellipse', 'a': 0.3, 'b': 0.35, 'n': 2.6, 'center': [0, 0.05]}},
    {'s': 0.4, 'section': {'type': 'rect', 'a': 0.4, 'b': 0.4}},
    {'s': 1.0, 'section': {'type': 'points', 'points': [[0.2, -0.1], [0.1, 0.3], [-0.2, 0.1], [-0.1, -0.2]]}},
    {'s': 1.6, 'section': {'type': 'ellipse', 'a': 0, 'b': 0, 'center': [0, 0.1]}}]})
check('loft_collapsed_tips_watertight', watertight(nose) and volume(nose) > 0, {'watertight': watertight(nose), 'volume': round(volume(nose), 5)})

# 2. wing ---------------------------------------------------------------------------------------
SPAN, RC, TC, DIH = 10.0, 2.0, 1.0, 5.0
w = wing('t_wing', {'span': SPAN, 'root_chord': RC, 'tip_chord': TC, 'sweep_deg': 10, 'dihedral_deg': DIH,
                    'airfoil': 'NACA2415', 'mirror': True, 'sections': 12, 'chord_points': 60})
verts = [w.matrix_world @ v.co for v in w.data.vertices]
xs = [v.x for v in verts]
check('wing_span', abs(max(xs) - min(xs) - SPAN) < 1e-4, round(max(xs) - min(xs), 7))
check('wing_watertight', watertight(w) and volume(w) > 0, watertight(w))


def ring_at(x):
    """Vertices of the section at span coordinate x, ordered by mesh connectivity."""
    ids = {v.index for v in w.data.vertices if abs(v.co.x - x) < 1e-7}
    nbr = {i: [] for i in ids}
    for e in w.data.edges:
        i, j = e.vertices
        if i in ids and j in ids:
            nbr[i].append(j)
            nbr[j].append(i)
    start = min(ids)
    loop, prev, cur = [start], None, start
    while True:
        nxt = [j for j in nbr[cur] if j != prev][0]
        if nxt == start:
            break
        loop.append(nxt)
        prev, cur = cur, nxt
    return [verts[i] for i in loop]


def section_stats(loop):
    le = max(range(len(loop)), key=lambda i: loop[i].y)
    te = min(range(len(loop)), key=lambda i: loop[i].y)
    loop = loop[le:] + loop[:le]
    te = (te - le) % len(loop)
    c1, c2 = loop[:te + 1], loop[te:] + [loop[0]]
    chord = loop[0].y - loop[te].y
    if sum(p.z for p in c1) / len(c1) < sum(p.z for p in c2) / len(c2):
        c1, c2 = c2, c1

    def z_at(chain, y):
        chain = sorted(chain, key=lambda p: p.y)
        for p, q in zip(chain, chain[1:]):
            if p.y <= y <= q.y:
                return p.z if q.y == p.y else p.z + (q.z - p.z) * (y - p.y) / (q.y - p.y)
        return chain[-1].z
    best_t, best_c, best_x = 0, -1, 0
    for k in range(1, 400):
        y = loop[0].y - chord * k / 400
        zu, zl = z_at(c1, y), z_at(c2, y)
        best_t = max(best_t, zu - zl)
        cam = 0.5 * (zu + zl) - (loop[0].z + (loop[te].z - loop[0].z) * k / 400)
        if cam > best_c:
            best_c, best_x = cam, k / 400
    qc_z = loop[0].z + 0.25 * (loop[te].z - loop[0].z)
    return {'chord': chord, 't_c': best_t / chord, 'camber': best_c / chord, 'camber_x': best_x, 'qc_z': qc_z}


root, tip = section_stats(ring_at(0.0)), section_stats(ring_at(SPAN / 2))
check('wing_root_thickness', abs(root['t_c'] - 0.15) < 0.005, round(root['t_c'], 5))
check('wing_root_camber', abs(root['camber'] - 0.02) < 0.003 and abs(root['camber_x'] - 0.4) < 0.03,
      [round(root['camber'], 5), root['camber_x']])
check('wing_tip_chord_ratio', abs(tip['chord'] / root['chord'] / (TC / RC) - 1) < 0.01, round(tip['chord'] / root['chord'], 5))
dih = math.degrees(math.atan2(tip['qc_z'] - root['qc_z'], SPAN / 2))
check('wing_dihedral_deg', abs(dih - DIH) < 0.2, round(dih, 4))
w5 = wing('t_wing5', {'span': 4, 'root_chord': 1, 'tip_chord': 0.5, 'airfoil': 'NACA23015', 'tip': 'round',
                      'washout_deg': 3, 'elliptic': True})
xs5 = [(w5.matrix_world @ v.co).x for v in w5.data.vertices]
check('wing_5digit_round_tip', watertight(w5) and abs(max(xs5) - min(xs5) - 4) < 1e-4, round(max(xs5) - min(xs5), 7))

# 3. revolve ------------------------------------------------------------------------------------
cyl = revolve('t_cyl', {'profile': [[0.3, 0.0], [0.3, 0.5]], 'segments': 48})
vr = volume(cyl) / (math.pi * 0.3 ** 2 * 0.5)
check('revolve_cylinder_volume_ratio', abs(vr - 1) < 0.015 and watertight(cyl), round(vr, 5))
half = revolve('t_halfdome', {'profile': [[0, 0.4], [0.3, 0.2], [0.3, 0.0]], 'angle_deg': 180, 'axis': 'x'})
check('revolve_partial_watertight', watertight(half) and volume(half) > 0, round(volume(half), 5))
tyre = revolve('t_tyre', {'profile': [[0.25, -0.05], [0.3, -0.06], [0.33, 0], [0.3, 0.06], [0.25, 0.05]],
                          'closed_profile': True, 'axis': 'x'})
check('revolve_closed_profile_watertight', watertight(tyre) and volume(tyre) > 0, round(volume(tyre), 5))

# 4. sweep --------------------------------------------------------------------------------------
R, r, SEG = 1.0, 0.05, 16
path = [[R * math.cos(t), R * math.sin(t), 0.2 * t] for t in (0.5 * math.pi * k / 48 for k in range(49))]
pipe = sweep('t_pipe', {'profile': {'type': 'circle', 'r': r}, 'path': path, 'segments': SEG})
pv = [v.co for v in pipe.data.vertices]
nan = any(not math.isfinite(c) for v in pv for c in v)
radii = [(pv[k * SEG + j] - Vector(path[k])).length for k in range(len(path)) for j in range(SEG)]
check('sweep_radius', not nan and max(abs(x / r - 1) for x in radii) < 0.01 and watertight(pipe),
      [round(min(radii) / r, 6), round(max(radii) / r, 6)])
ring_loop = sweep('t_ring', {'profile': {'points': [[0.02, 0], [0, 0.03], [-0.02, 0], [0, -0.03]]}, 'closed': True,
                             'path': [[math.cos(t), math.sin(t), 0] for t in (2 * math.pi * k / 40 for k in range(40))]})
check('sweep_closed_watertight', watertight(ring_loop), True)

# 5. array --------------------------------------------------------------------------------------
blade = {'builder': 'wing', 'params': {'span': 0.9, 'root_chord': 0.25, 'tip_chord': 0.12, 'airfoil': 'NACA4412',
                                       'mirror': False, 'twist_deg': [[0, 35], [1, 12]], 'chord_axis': 'z',
                                       'thickness_axis': '-y', 'sections': 8, 'chord_points': 20},
         'transform': {'location': [0.1, 0, 0]}}
prop = build_part({'part_id': 'prop', 'builder': 'array', 'params': {'count': 4, 'axis': 'y', 'center': [0, 0, 0], 'item': blade}}, 'arraytest')
kids = sorted(prop.children, key=lambda o: o.name)
bpy.context.view_layer.update()
angles = sorted(round(math.degrees(math.atan2((k.matrix_world.to_3x3() @ Vector((1, 0, 0))).z,
                                              (k.matrix_world.to_3x3() @ Vector((1, 0, 0))).x)) % 360, 4) for k in kids)
check('array_four_blades_90deg', len(kids) == 4 and all(abs(a - 90 * i) < 1e-3 for i, a in enumerate(angles))
      and all(watertight(k) for k in kids), angles)

# 6. box + details ------------------------------------------------------------------------------
bx = box('t_box', {'size': [1, 2, 0.5], 'bevel_m': 0.05})
d = bx.dimensions
check('box_dims', max(abs(d.x - 1), abs(d.y - 2), abs(d.z - 0.5)) < 1e-6 and watertight(bx), [round(c, 6) for c in d])
paint = bpy.data.materials.new('t_paint')
panel_lines(paint, spacing_m=0.5)
panel_lines(paint, spacing_m=0.6)  # idempotent re-apply
n_tag = len([n for n in paint.node_tree.nodes if n.name.startswith('StudioPanelLines')])
glass = clear_canopy('t_glass')
check('details', n_tag > 0 and n_tag == len({n.name for n in paint.node_tree.nodes if n.name.startswith('StudioPanelLines')})
      and glass.node_tree.nodes['Principled BSDF'].inputs['Transmission Weight'].default_value == 1.0, n_tag)

# 7. build_subject determinism -----------------------------------------------------------------
spec = {
    'subject_id': 'mixed.test',
    'builders': [
        {'part_id': 'body', 'builder': 'loft', 'features': ['fuselage'],
         'params': {'stations': [{'s': 3.0, 'section': {'type': 'ellipse', 'a': 0, 'b': 0}},
                                 {'s': 2.4, 'section': {'type': 'ellipse', 'a': 0.45, 'b': 0.5}},
                                 {'s': 0.0, 'section': {'type': 'superellipse', 'a': 0.5, 'b': 0.6, 'n': 2.4}},
                                 {'s': -3.0, 'section': {'type': 'ellipse', 'a': 0.1, 'b': 0.15, 'center': [0, 0.2]}}]}},
        {'part_id': 'wing_r', 'builder': 'wing', 'parent': 'body', 'features': ['wing'],
         'params': {'span': 4.0, 'root_chord': 1.6, 'tip_chord': 0.8, 'dihedral_deg': 6, 'mirror': False,
                    'airfoil': 'NACA2415', 'washout_deg': 2},
         'transform': {'location': [0.3, 0.2, -0.3]}},
        {'part_id': 'wing_l', 'builder': 'mirror', 'features': ['wing'], 'params': {'source': 'wing_r', 'axis': 'x'}},
        {'part_id': 'prop', 'builder': 'array', 'parent': 'body', 'features': ['propeller'],
         'params': {'count': 4, 'axis': 'y', 'center': [0, 3.1, 0], 'item': blade}},
        {'part_id': 'wheel', 'builder': 'revolve', 'parent': 'body', 'features': ['wheel'],
         'params': {'profile': [[0, -0.08], [0.25, -0.08], [0.3, 0], [0.25, 0.08], [0, 0.08]], 'axis': 'x'},
         'transform': {'location': [0.9, 1.0, -1.1], 'rotation_deg': [0, 0, 0]}},
        {'part_id': 'deck', 'builder': 'box', 'parent': 'body', 'params': {'size': [0.4, 0.6, 0.05], 'bevel_m': 0.01},
         'transform': {'location': [0, 0.5, 0.6], 'rotation_deg': [5, 0, 0]}},
    ],
    'materials': [{'part_ids': ['body', 'wing_r', 'wing_l'], 'color_srgb': [0.7, 0.72, 0.75], 'metallic': 1, 'roughness': 0.35},
                  {'part_ids': ['prop'], 'catalog_key': 'painted_metal_black'}],
}


def all_objs(res):
    out = []
    stack = [res['root']]
    while stack:
        o = stack.pop()
        out.append(o)
        stack.extend(o.children)
    return out


res1 = build_subject(spec, root_location=(5, 0, 0))
h1 = mesh_hash(all_objs(res1))
tagged = [o for o in all_objs(res1) if o is not res1['root']]
props_ok = all(o.get('studio_id', '').startswith('mixed.test/') and isinstance(json.loads(o['studio_features']), list)
               and o.get('studio_dim_role') == 'none' and o.get('studio_subject_id') == 'mixed.test' for o in tagged)
wr, wl = res1['parts']['wing_r'], res1['parts']['wing_l']
bpy.context.view_layer.update()
xr = [(wr.matrix_world @ v.co).x for v in wr.data.vertices]
xl = [(wl.matrix_world @ v.co).x for v in wl.data.vertices]
mirror_ok = abs(max(xr) - 5 + (min(xl) - 5)) < 1e-5 and abs(min(xr) - 5 + (max(xl) - 5)) < 1e-5 and watertight(wl) and volume(wl) > 0
smooth_ok = all(any(p.use_smooth for p in o.data.polygons) and o.data.attributes.get('sharp_edge') is not None
                for o in tagged if o.type == 'MESH')
check('smooth_by_angle', smooth_ok, 'shade_smooth + set_sharp_from_angle(30deg)')
n_objects = len(bpy.data.objects)
try:
    build_subject(spec, root_location=(5, 0, 0))
    refused = False
except ValueError:
    refused = True
res2 = build_subject(spec, root_location=(5, 0, 0), replace=True)
h2 = mesh_hash(all_objs(res2))
dupes = [o.name for o in bpy.data.objects if '.0' in o.name.rsplit('/', 1)[-1] and o.name[-4] == '.' and o.name[-3:].isdigit()]
check('build_subject_deterministic', h1 == h2 and props_ok and mirror_ok and res1['catalog_materials'] == [{'part_ids': ['prop'], 'catalog_key': 'painted_metal_black'}]
      and len(res2['parts']['prop'].children) == 4 and refused and len(bpy.data.objects) == n_objects and not dupes,
      {'hash': h1[:16], 'parts': sorted(res2['parts']), 'objects': len(tagged), 'mirror_ok': mirror_ok, 'refused_double_build': refused, 'dupes': dupes})

# 8. anchors, partial rebuild, teardown ------------------------------------------------------------
body = res2['parts']['body']
anchors = json.loads(body['studio_anchors'])
need = {f'mixed.test/body/{k}' for k in ('center', 'origin', '+x', '-x', '+y', '-y', '+z', '-z')}
check('anchors_written', need <= set(anchors) and abs(anchors['mixed.test/body/+y'][1] - 3.0) < 1e-6
      and all('studio_anchors' in o for o in res2['parts'].values()), sorted(anchors)[:4])
changed = json.loads(json.dumps(spec))
changed['builders'][1]['params']['span'] = 5.0
before_children = sorted(c.name for c in body.children)
rebuilt = rebuild_parts(changed, ['wing_r'])
bpy.context.view_layer.update()
parts_now = scene_parts('mixed.test')
xl2 = [(parts_now['wing_l'].matrix_world @ v.co).x for v in parts_now['wing_l'].data.vertices]
check('rebuild_parts_follows_mirror', rebuilt['rebuilt'] == ['wing_l', 'wing_r'] and abs((5 - min(xl2)) - 5.3) < 1e-4
      and sorted(c.name for c in parts_now['body'].children) == before_children and len(bpy.data.objects) == n_objects,
      {'rebuilt': rebuilt['rebuilt'], 'wing_l_reach': round(5 - min(xl2), 5)})
restored = rebuild_parts(spec, ['wing_r'])
check('rebuild_parts_reversible', mesh_hash(all_objs({'root': restored['root']})) == h1, 'same hash after restoring the spec')
removed = remove_subject('mixed.test')
check('remove_subject', removed > 0 and not [o for o in bpy.data.objects if o.get('studio_subject_id') == 'mixed.test']
      and not [m for m in bpy.data.materials if m.name.startswith('mixed.test/')], removed)

# 9. relations ------------------------------------------------------------------------------------
rel = {'subject_id': 'rel.test', 'builders': [
    {'part_id': 'drum', 'builder': 'revolve', 'params': {'axis': 'x', 'profile': [[0, -0.3], [0.15, -0.3], [0.15, 0.3], [0, 0.3]]}},
    {'part_id': 'flange', 'builder': 'revolve', 'params': {'axis': 'x', 'profile': [[0, 0], [0.22, 0], [0.22, 0.04], [0, 0.04]]}},
    {'part_id': 'flange_b', 'builder': 'mirror', 'params': {'source': 'flange', 'axis': 'x'}},
    {'part_id': 'axle', 'builder': 'revolve', 'params': {'axis': 'x', 'profile': [[0, 0], [0.03, 0], [0.03, 1.0], [0, 1.0]]},
     'transform': {'location': [0, 0.4, 0.2]}},
    {'part_id': 'base', 'builder': 'box', 'params': {'size': [1.2, 0.6, 0.05]}, 'transform': {'location': [0, 0, -1.0]}},
    {'part_id': 'post', 'builder': 'box', 'params': {'size': [0.05, 0.05, 0.3]}, 'transform': {'location': [0.4, 0, 1.0]}}],
    'relations': [{'type': 'attach', 'a': 'flange/-x', 'b': 'drum/+x'},
                  {'type': 'through', 'a': 'axle/center', 'b': 'drum/center', 'axis': 'x'},
                  {'type': 'align', 'a': 'axle/center', 'b': 'drum/center', 'axis': 'x'},
                  {'type': 'attach', 'a': 'base/+z', 'b': 'drum/-z', 'offset_m': [0, 0, -0.1]},
                  {'type': 'on_surface', 'a': 'post/-z', 'b': 'base', 'axis': '-z'}]}
r = build_subject(rel, root_location=(0, 3, 0))
bpy.context.view_layer.update()
P = r['parts']
def wbox(obj):
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]
fl, fh = wbox(P['flange']); dl, dh = wbox(P['drum']); bl, bh = wbox(P['flange_b']); al, ah = wbox(P['axle'])
basel, baseh = wbox(P['base']); pl, ph = wbox(P['post'])
check('relations', abs(fl[0] - dh[0]) < 1e-6 and abs(bh[0] - dl[0]) < 1e-6 and abs((al[0] + ah[0]) / 2 - (dl[0] + dh[0]) / 2) < 1e-6
      and abs((al[1] + ah[1]) / 2 - 3) < 1e-6 and abs((al[2] + ah[2]) / 2) < 1e-6 and abs(baseh[2] - (dl[2] - 0.1)) < 1e-6
      and abs(pl[2] - baseh[2]) < 1e-5,
      {'flange_gap': round(fl[0] - dh[0], 9), 'mirror_gap': round(bh[0] - dl[0], 9), 'axle_mid_x': round((al[0] + ah[0]) / 2, 9),
       'post_on_base': round(pl[2] - baseh[2], 9), 'moved': [x['moved_m'] for x in r['relations']]})
try:
    build_subject({'subject_id': 'cyc', 'builders': rel['builders'][:2],
                   'relations': [{'type': 'attach', 'a': 'drum', 'b': 'flange'}, {'type': 'attach', 'a': 'flange', 'b': 'drum'}]})
    cycle = False
except ValueError as e:
    cycle = 'cycle' in str(e)
check('relation_cycle_refused', cycle, cycle)

# 10. profile / wall / grid ------------------------------------------------------------------------
heb = profile_extrude('t_heb', {'profile': {'table': 'EN 10365', 'designation': 'HEB 300'}, 'length': 1.0})
kss = profile_extrude('t_ks', {'profile': {'table': 'KS D 3502', 'designation': 'H-300x300x10x15'}, 'length': 2.0, 'axis': 'z'})
check('profile_section_area', abs(volume(heb) / 1.0 / 0.01491 - 1) < 0.005 and abs(volume(kss) / 2.0 / 0.01198 - 1) < 0.005
      and watertight(heb) and watertight(kss), [round(volume(heb) * 1e4, 3), round(volume(kss) / 2 * 1e4, 3)])
plate = profile_extrude('t_plate', {'profile': [[-0.1, -0.2], [0.1, -0.2], [0.1, 0.2], [-0.1, 0.2]], 'length': 0.02, 'centered': True})
check('profile_exact_corners', len(plate.data.vertices) == 8 and abs(volume(plate) - 0.2 * 0.4 * 0.02) < 1e-9, len(plate.data.vertices))
wl_ = wall('t_wall', {'length': 6, 'height': 3, 'thickness': 0.2, 'openings': [{'x': 1, 'z': 0, 'w': 1, 'h': 2.1}, {'x': 3, 'z': 1, 'w': 2, 'h': 1.2}]})
expect = 6 * 3 * 0.2 - (1 * 2.1 + 2 * 1.2) * 0.2
check('wall_openings', watertight(wl_) and abs(volume(wl_) - expect) < 1e-6, [round(volume(wl_), 6), round(expect, 6)])
try:
    wall('t_bad', {'length': 2, 'height': 2, 'thickness': 0.1, 'openings': [{'x': 0.5, 'z': 0.5, 'w': 1, 'h': 1}, {'x': 1, 'z': 1, 'w': 0.5, 'h': 0.5}]})
    overlap = False
except ValueError:
    overlap = True
grid = build_part({'part_id': 'mullions', 'builder': 'array', 'dim_role': 'structure',
                   'params': {'pattern': 'grid', 'counts': [4, 3], 'pitch_m': [1.5, 3.0], 'axes': ['x', 'z'],
                              'item': {'builder': 'box', 'params': {'size': [0.05, 0.1, 3.0]}}}}, 'cw')
kids = sorted(grid.children, key=lambda o: o.name)
idx = sorted(tuple(k['studio_array_index']) for k in kids)
check('grid_array', len(kids) == 12 and idx[0] == (0, 0) and idx[-1] == (3, 2) and overlap
      and all(k['studio_dim_role'] == 'structure' for k in kids)
      and abs(max(k.matrix_basis.translation.x for k in kids) - 4.5) < 1e-9, {'n': len(kids), 'last': idx[-1]})

print('STUDIO_MODELING_SMOKE ' + json.dumps({'ok': True, 'checks': report}, sort_keys=True))
