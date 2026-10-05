"""Real-world scale QA and explicit, exposure-preserving rescale (ported from photoreal research 03).

audit_scale(scene) -> dict      automatic pass, report-only: never changes the scene.
rescale_scene(scene, factor)    explicit author-called API only; never invoked automatically.

Research finding: the hero scene was ~8.6x oversized, which made depth of field
meaningless (a 'close-up' framed metres). A uniform rescale with light power x f^2
kept image exposure within 0.1 %, and DOF then followed real optics.

Classification (every object needs a role): custom prop `studio_dim_role` (exact
category id from look_data/real_dimensions.json, or 'none' to exempt), then
`studio_role` (matched with the category regexes), then the object name (same
regexes). Objects nothing matches are reported as unclassified, never guessed.

Rescale principle (not a list of cases): every length-valued parameter is multiplied
by f, every per-length parameter by 1/f, every power-from-a-finite-emitter by f^2.
Anything whose unit has not been reviewed is reported in result['unreviewed'] instead
of being silently left alone (default-deny).
"""
import json
import math
import re
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

TABLE_PATH = Path(__file__).resolve().parent / 'look_data' / 'real_dimensions.json'
MEASURED_TYPES = {'MESH', 'CURVE', 'FONT', 'SURFACE', 'META'}
EXEMPT_ROLE = 'none'
REL_TOL = 1e-4                    # numeric slack on range ends
HIGH_FLAG_RATIO = 0.5             # above this the scene is probably mis-scaled as a whole
FACTOR_OK = (0.8, 1.25)           # suggested factor inside this band = scale is plausible


def _err(message):
    return ValueError('LOOK_SCALE: ' + message)


def load_table(path=None):
    with open(path or TABLE_PATH, encoding='utf-8') as handle:
        return json.load(handle)

# ---------------------------------------------------------------- audit ---

def _measures(o):
    dims = list(o.dimensions)  # object-aligned, includes object scale + modifiers
    a, b, c = sorted(dims)
    # prism/cylinder axis = the dim NOT in the most nearly-equal pair
    pairs = [((0, 1), 2), ((0, 2), 1), ((1, 2), 0)]
    best = max(pairs, key=lambda p: min(dims[p[0][0]], dims[p[0][1]]) / max(dims[p[0][0]], dims[p[0][1]], 1e-12))
    p0, p1 = dims[best[0][0]], dims[best[0][1]]
    zs = [(o.matrix_world @ Vector(v)).z for v in o.bound_box]
    return {
        'hex_across_flats': min(p0, p1),
        'round_diameter': max(p0, p1),
        'axis_length': dims[best[1]],
        'min_dim': a, 'mid_dim': b, 'max_dim': c,
        'height_z': max(zs) - min(zs),
    }


AMBIGUOUS = 'ambiguous'


def _regex_match(text, cats):
    """The single matching category, AMBIGUOUS when several match (never guess by table order), or None."""
    found = [c for c in cats if re.search(c['match'], text, re.I) and not (c.get('exclude') and re.search(c['exclude'], text, re.I))]
    if len(found) > 1:
        return AMBIGUOUS
    return found[0] if found else None


def classify(o, cats):
    """-> (category | None, source, note). source in dim_role|role|name|exempt|unknown_dim_role|none."""
    by_id = {c['id']: c for c in cats}
    dim_role = o.get('studio_dim_role')
    if dim_role is not None:
        dim_role = str(dim_role)
        if dim_role == EXEMPT_ROLE:
            return None, 'exempt', None
        if dim_role in by_id:
            return by_id[dim_role], 'dim_role', None
        return None, 'unknown_dim_role', dim_role   # explicit tag wins; a typo must not fall through to a guess
    role = o.get('studio_role')
    if role is not None:
        c = _regex_match(str(role), cats)
        if c == AMBIGUOUS:
            return None, 'ambiguous', str(role)
        if c:
            return c, 'role', None
    c = _regex_match(o.name, cats)
    if c == AMBIGUOUS:
        return None, 'ambiguous', o.name
    return (c, 'name', None) if c else (None, 'none', None)


def _best_factor(intervals):
    """intervals: [(lo, hi, w)] of acceptable factors -> geometric centre of the max weighted
    coverage interval in log space (robust: outlier categories simply do not overlap it)."""
    if not intervals:
        return None, None, 0.0, 0.0
    ev = []
    for lo, hi, w in intervals:
        ev.append((math.log(lo), 0, w)); ev.append((math.log(hi), 1, w))
    ev.sort(key=lambda e: (e[0], e[1]))
    cur = best = 0.0; best_lo = best_hi = None; tot = sum(w for _, _, w in intervals)
    for i, (x, kind, w) in enumerate(ev):
        cur += w if kind == 0 else -w
        if kind == 0 and cur > best + 1e-9:
            best = cur; best_lo = x
            best_hi = next((e[0] for e in ev[i + 1:] if e[1] == 1), x)
    return math.exp((best_lo + best_hi) / 2), (math.exp(best_lo), math.exp(best_hi)), best, tot


def dof_limits(lens_mm, fstop, focus_m, coc_mm):
    """Thin-lens near/far acceptable-sharpness limits (m)."""
    f = lens_mm / 1000.0; c = coc_mm / 1000.0
    H = f * f / (fstop * c) + f
    near = focus_m * (H - f) / (H + focus_m - 2 * f)
    far = focus_m * (H - f) / (H - focus_m) if focus_m < H else float('inf')
    return near, far


def framing(scene, fstops=(2.8, 5.6)):
    """Field width at focus distance and DOF depth. A field width of metres around a
    'close-up' of a bolt is the symptom of a mis-scaled set."""
    cam = scene.camera
    if not cam or cam.data.type != 'PERSP':
        return None
    d = cam.data
    if d.dof.focus_object:
        dist = (cam.matrix_world.translation - d.dof.focus_object.matrix_world.translation).length
    else:
        dist = d.dof.focus_distance
    sw = d.sensor_width if d.sensor_fit != 'VERTICAL' else d.sensor_height
    rx, ry = scene.render.resolution_x, scene.render.resolution_y
    horiz_sensor = sw if (d.sensor_fit == 'HORIZONTAL' or (d.sensor_fit == 'AUTO' and rx >= ry)) else sw * rx / ry
    coc = horiz_sensor / 1500.0  # common d/1500 criterion on the frame width
    out = {'focus_distance_m': round(dist, 4), 'lens_mm': round(d.lens, 4),
           'field_width_at_focus_m': round(dist * horiz_sensor / d.lens, 4), 'coc_mm': round(coc, 4)}
    for N in fstops:
        n, fr = dof_limits(d.lens, N, dist, coc)
        finite = fr != float('inf') and out['field_width_at_focus_m'] > 0
        out[f'dof_f{N}_m'] = round(fr - n, 4) if fr != float('inf') else 'inf'
        out[f'dof_f{N}_over_field_width'] = round((fr - n) / out['field_width_at_focus_m'], 3) if finite else 'inf'
    return out


def audit_scale(scene, table_path=None):
    """Report-only scale QA. Deterministic (sorted by object name, no timestamps)."""
    cats = load_table(table_path)['categories']
    rows, unclassified, exempt, warnings = [], [], [], []
    for o in sorted(scene.objects, key=lambda x: x.name):
        if o.type not in MEASURED_TYPES or o.hide_render:
            continue
        from scene_roles import counts
        if not counts(o, 'bounds'):  # an earth shell or volume has no real-world size to audit
            exempt.append(o.name); continue
        c, source, note = classify(o, cats)
        if source == 'exempt':
            exempt.append(o.name); continue
        if source == 'unknown_dim_role':
            warnings.append(f'{o.name}: studio_dim_role {note!r} is not a category in real_dimensions.json')
        if source == 'ambiguous':
            warnings.append(f'{o.name}: {note!r} matches several categories; set studio_dim_role explicitly')
        if not c:
            unclassified.append(o.name); continue
        m = _measures(o)
        for chk in c['checks']:
            v = m[chk['measure']]
            lo, hi = chk['range_m']
            ok = lo * (1 - REL_TOL) <= v <= hi * (1 + REL_TOL)
            rows.append({'object': o.name, 'category': c['id'], 'classified_by': source,
                         'measure': chk['measure'], 'value_m': round(v, 5), 'range_m': [lo, hi],
                         'ok': ok, 'basis': c['basis'],
                         'factor_to_fit': None if ok else round((lo if v < lo else hi) / max(v, 1e-9), 4),
                         '_interval': (lo / max(v, 1e-9), hi / max(v, 1e-9))})
    # category-weighted consensus: each category counts once regardless of instance count
    per_cat = {}
    for r in rows:
        per_cat.setdefault(r['category'], []).append(r)
    intervals = []
    for rs in per_cat.values():
        w = (0.5 if rs[0]['basis'] == 'heuristic' else 1.0) / len(rs)
        intervals += [(r['_interval'][0], r['_interval'][1], w) for r in rs]
    f, band, cov, tot = _best_factor(intervals)
    for r in rows:
        r.pop('_interval')
    cat_fit = {}
    if f:
        for cat, rs in sorted(per_cat.items()):
            cat_fit[cat] = round(sum(1 for r in rs if r['range_m'][0] <= r['value_m'] * f <= r['range_m'][1]) / len(rs), 3)
    flagged = [r for r in rows if not r['ok']]
    flag_ratio = round(len(flagged) / max(len(rows), 1), 3)
    if unclassified:
        warnings.append(f'{len(unclassified)} object(s) have no dimension role (tag studio_dim_role, or "none" to exempt)')
    if not rows:
        warnings.append('no classified objects: scale cannot be audited')
    if flag_ratio > HIGH_FLAG_RATIO:
        warnings.append(f'flag_ratio {flag_ratio} > {HIGH_FLAG_RATIO}: scene is probably mis-scaled as a whole')
    if f and not FACTOR_OK[0] <= f <= FACTOR_OK[1]:
        warnings.append(f'suggested_uniform_factor {f:.4g} is outside {list(FACTOR_OK)}; '
                        'review, then call rescale_scene(scene, factor) explicitly if intended')
    if scene.unit_settings.scale_length != 1.0:
        warnings.append(f'unit scale_length is {scene.unit_settings.scale_length}; table assumes 1.0')
    return {
        'scene': scene.name, 'unit_scale_length': scene.unit_settings.scale_length,
        'framing': framing(scene),
        'n_checked': len(rows), 'n_flagged': len(flagged), 'flag_ratio': flag_ratio,
        'suggested_uniform_factor': round(f, 4) if f else None,
        'consensus_band': [round(band[0], 4), round(band[1], 4)] if band else None,
        'consensus_coverage': round(cov / tot, 3) if tot else None,
        'category_fit_at_suggested': cat_fit,
        'checks': rows,
        'unclassified': unclassified, 'exempt': exempt,
        'warnings': warnings,
    }

# --------------------------------------------------------------- rescale ---

# modifier field -> exponent of f (1 = length). Types listed with {} are reviewed & scale-free.
MOD_RULES = {
    'BEVEL': {'width': 1}, 'SOLIDIFY': {'thickness': 1}, 'WIREFRAME': {'thickness': 1},
    'ARRAY': {'constant_offset_displace': 1, 'merge_threshold': 1, 'fit_length': 1},
    'DISPLACE': {'strength': 1}, 'WELD': {'merge_threshold': 1}, 'MIRROR': {'merge_threshold': 1},
    'SCREW': {'screw_offset': 1, 'merge_threshold': 1}, 'SHRINKWRAP': {'offset': 1},
    'REMESH': {'voxel_size': 1}, 'HOOK': {'falloff_radius': 1}, 'CAST': {'radius': 1},
    'WEIGHTED_NORMAL': {}, 'SUBSURF': {}, 'TRIANGULATE': {}, 'BOOLEAN': {}, 'EDGE_SPLIT': {},
    'DECIMATE': {}, 'SMOOTH': {}, 'CORRECTIVE_SMOOTH': {}, 'LAPLACIANSMOOTH': {}, 'CURVE': {},
    'LATTICE': {}, 'ARMATURE': {}, 'MULTIRES': {}, 'SIMPLE_DEFORM': {},
}
# shader node input name -> exponent (only on these node types)
NODE_RULES = {
    'BUMP': {'Distance': 1, 'Filter Width': 0},
    'DISPLACEMENT': {'Scale': 1}, 'VECTOR_DISPLACEMENT': {'Scale': 1},
    'BSDF_PRINCIPLED': {'Subsurface Scale': 1}, 'SUBSURFACE_SCATTERING': {'Scale': 1},
    'BEVEL': {'Radius': 1}, 'AMBIENT_OCCLUSION': {'Distance': 1}, 'WIREFRAME': {'Size': 1},
    'PRINCIPLED_VOLUME': {'Density': -1}, 'VOLUME_ABSORPTION': {'Density': -1}, 'VOLUME_SCATTER': {'Density': -1},
    'VOLUME_COEFFICIENTS': {'Density': -1}, 'OUTPUT_MATERIAL': {'Thickness': 1},
}
# coordinate outputs measured in scene length units -> must be divided by f downstream
LENGTH_COORD_OUTPUTS = {('TEX_COORD', 'Object'), ('TEX_COORD', 'Camera'), ('NEW_GEOMETRY', 'Position')}
LENGTHY_INPUT_NAMES = {'Distance', 'Radius', 'Size', 'Width', 'Offset', 'Thickness', 'Height'}


def _mul_socket(sock, k, log, where):
    if sock.is_linked:
        log['unreviewed'].append(f"{where}: input '{sock.name}' is linked; needs x{k:g} upstream")
        return
    v = sock.default_value
    sock.default_value = [x * k for x in v] if hasattr(v, '__len__') else v * k


def _fix_tree(nt, f, log, seen):
    if nt is None or nt.as_pointer() in seen:
        return
    seen.add(nt.as_pointer())
    for n in list(nt.nodes):
        where = f'{nt.name}/{n.name}'
        if n.type == 'GROUP':
            _fix_tree(n.node_tree, f, log, seen); continue
        rule = NODE_RULES.get(n.type)
        if rule is not None:
            for name, e in rule.items():
                if name in n.inputs and e:
                    _mul_socket(n.inputs[name], f ** e, log, where)
            log['nodes'] += 1
        elif n.type.startswith('TEX_') or n.type in {'MAPPING', 'TEX_COORD', 'NEW_GEOMETRY', 'VECT_MATH', 'MATH'}:
            pass  # coordinate-space handled below; texture 'Scale' is per-coordinate-unit
        else:
            lengthy = [s.name for s in n.inputs if s.name in LENGTHY_INPUT_NAMES and not s.is_linked]
            if lengthy:
                log['unreviewed'].append(f'{where} ({n.type}): {lengthy}')
        for out in n.outputs:
            if (n.type, out.name) in LENGTH_COORD_OUTPUTS and out.is_linked:
                sc = nt.nodes.new('ShaderNodeVectorMath'); sc.operation = 'SCALE'
                sc.inputs['Scale'].default_value = 1.0 / f
                sc.label = 'look_scale 1/f'; sc.location = n.location + Vector((150, -200))
                for link in list(out.links):
                    to = link.to_socket; nt.links.remove(link); nt.links.new(sc.outputs[0], to)
                nt.links.new(out, sc.inputs[0])
                log['coord_fix'] += 1


def _action_fcurves(act):
    if act is None:
        return []
    fcs = list(getattr(act, 'fcurves', []) or [])  # legacy actions
    for layer in getattr(act, 'layers', []):        # Blender 4.4+/5.x layered actions
        for strip in layer.strips:
            for cb in getattr(strip, 'channelbags', []):
                fcs += list(cb.fcurves)
    return fcs


def _has_keyframes(id_data):
    ad = getattr(id_data, 'animation_data', None)
    if not ad:
        return False
    if any(len(fc.keyframe_points) for fc in _action_fcurves(ad.action)):
        return True
    return any(len(fc.keyframe_points) for t in ad.nla_tracks for s in t.strips for fc in _action_fcurves(s.action))


def _camera_animation_guard(scene):
    """A baked camera path (camera, its data, or any parent moving it) would not survive a rescale."""
    cam = scene.camera
    if cam is None:
        return
    owners = [cam, cam.data]
    parent = cam.parent
    while parent is not None:
        owners.append(parent); parent = parent.parent
    keyed = [o.name for o in owners if _has_keyframes(o)]
    if keyed:
        raise _err(f'active camera animation has keyframes on {keyed}; rescale would break baked camera paths. '
                   'Rescale before baking the camera, then re-bake.')


def _scale_fcurves(id_data, paths_exp, f, log):
    ad = getattr(id_data, 'animation_data', None)
    if not ad:
        return
    if ad.drivers:
        log['unreviewed'].append(f'{id_data.name}: has drivers (not rescaled)')
    if ad.nla_tracks:
        log['unreviewed'].append(f'{id_data.name}: NLA strips (not rescaled)')
    for fc in _action_fcurves(ad.action):
        e = paths_exp.get(fc.data_path)
        if e:
            k = f ** e
            for kp in fc.keyframe_points:
                kp.co.y *= k; kp.handle_left.y *= k; kp.handle_right.y *= k
            log['fcurves'] += 1


def rescale_scene(scene, factor):
    """Uniformly scale the file about the world origin, baking scale into data (object
    scale untouched). Invariant: framing (lens/sensor/f-stop), image exposure (light
    power x f^2), procedural texture look. Changed on purpose: DOF relative to subject.

    Explicit author-called API only. Raises ValueError('LOOK_SCALE: ...') if the factor is
    not a finite positive number or the active camera path is keyframed.
    """
    f = float(factor)
    if not math.isfinite(f) or f <= 0:
        raise _err(f'factor must be a finite positive number, got {factor!r}')
    _camera_animation_guard(scene)
    log = {'factor': f, 'objects': 0, 'data': 0, 'modifiers': 0, 'nodes': 0, 'coord_fix': 0, 'lights': 0,
           'fcurves': 0, 'light_energy_law': 'non-sun energy x f^2; sun strength unchanged', 'unreviewed': []}
    if len(bpy.data.scenes) > 1:
        log['unreviewed'].append('file has >1 scene; all objects in file were scaled')
    S = Matrix.Scale(f, 4)
    done_data = set()
    for o in bpy.data.objects:
        if o.library:
            log['unreviewed'].append(f'{o.name}: linked library object skipped'); continue
        o.location = o.location * f
        o.delta_location = o.delta_location * f
        mpi = o.matrix_parent_inverse.copy(); mpi.translation *= f; o.matrix_parent_inverse = mpi
        _scale_fcurves(o, {'location': 1, 'delta_location': 1}, f, log)
        log['objects'] += 1
        if o.constraints:
            log['unreviewed'].append(f'{o.name}: constraints {[c.type for c in o.constraints]}')
        for ps in o.particle_systems:
            log['unreviewed'].append(f'{o.name}: particle system {ps.name}')
        for m in o.modifiers:
            rule = MOD_RULES.get(m.type)
            if rule is None:
                log['unreviewed'].append(f"{o.name}: modifier {m.type} '{m.name}'"); continue
            for attr, e in rule.items():
                if m.type == 'BEVEL' and getattr(m, 'offset_type', '') == 'PERCENT':
                    continue
                v = getattr(m, attr)
                setattr(m, attr, [x * f ** e for x in v] if hasattr(v, '__len__') else v * f ** e)
            log['modifiers'] += 1
        if o.type == 'EMPTY':
            o.empty_display_size *= f
        d = o.data
        if d is None or d.as_pointer() in done_data:
            continue
        done_data.add(d.as_pointer())
        if o.type == 'LIGHT':
            if d.type != 'SUN':
                d.energy *= f * f          # radiance-preserving: P / area, or I / d^2
                _scale_fcurves(d, {'energy': 2, 'shadow_soft_size': 1, 'size': 1, 'size_y': 1}, f, log)
            else:
                _scale_fcurves(d, {}, f, log)
            d.shadow_soft_size *= f
            if d.type == 'AREA':
                d.size *= f; d.size_y *= f
            if hasattr(d, 'cutoff_distance'):
                d.cutoff_distance *= f
            if getattr(d, 'use_nodes', False) and d.node_tree:
                _fix_tree(d.node_tree, f, log, set())
            log['lights'] += 1
        elif o.type == 'CAMERA':
            d.dof.focus_distance *= f
            d.clip_start *= f; d.clip_end *= f; d.ortho_scale *= f
            d.stereo.interocular_distance *= f; d.stereo.convergence_distance *= f
            _scale_fcurves(d, {'dof.focus_distance': 1, 'ortho_scale': 1, 'clip_start': 1, 'clip_end': 1}, f, log)
        elif o.type == 'FONT':
            for a in ('size', 'extrude', 'bevel_depth', 'offset', 'offset_x', 'offset_y'):
                if hasattr(d, a):
                    setattr(d, a, getattr(d, a) * f)
        elif hasattr(d, 'transform'):
            try:
                d.transform(S, shape_keys=True) if o.type == 'MESH' else d.transform(S)
            except TypeError:
                d.transform(S)
            if o.type == 'CURVE':
                # Curve.transform() already scales per-point radius, which multiplies
                # bevel_depth -> do NOT scale bevel_depth again (verified 2026-10-03, 5.2.2).
                d.extrude *= f; d.offset *= f
            log['data'] += 1
        elif o.type != 'EMPTY':
            log['unreviewed'].append(f'{o.name}: data type {o.type} not transformed')
    seen = set()
    for m in bpy.data.materials:
        if m.node_tree:
            _fix_tree(m.node_tree, f, log, seen)
    for w in bpy.data.worlds:
        if w.node_tree:
            _fix_tree(w.node_tree, f, log, seen)
        w.mist_settings.start *= f; w.mist_settings.depth *= f
    eev = getattr(scene, 'eevee', None)
    if eev is not None and hasattr(eev, 'gtao_distance'):
        eev.gtao_distance *= f
    log['unreviewed'] = sorted(set(log['unreviewed']))
    return log
