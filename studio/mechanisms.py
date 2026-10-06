"""Mechanism specs from their definitions: the agent names the mechanism (tooth counts, module, planets) and the code
computes every number a buildable subject spec needs - gear outlines, planet centres and phases, the ring's phase,
joints and couplings - through gear_core. The result is an ordinary subject spec (schematic) the agent then edits.

  subject planetary --project P --subject reducer --module 0.002 --sun 18 --planet 27 --ring 72 --planets 3
"""
from __future__ import annotations

import math

from .blender_ops import gear_core   # pure math, no Blender
from .common import StudioError, write_json


def planetary_spec(subject_id, module, sun, planet, ring, planets, face_width_m=None, request=None):
    """A single-stage planetary gearset, ring fixed: sun in, carrier out (ratio sun / (sun + ring))."""
    try:
        layout = gear_core.planetary_layout(module, sun, planet, ring, planets)
    except ValueError as error:
        raise StudioError('INPUT_INVALID', str(error)) from error
    width = face_width_m or round(module * 8, 4)
    m = module
    rim_in, rim_out = m * ring / 2 + 1.25 * m, m * ring / 2 + 3.25 * m
    request = request or f'planetary gearset: {sun}-tooth sun, {planets} planets, {ring}-tooth ring'
    builders = [
        {'part_id': 'sun', 'builder': 'profile', 'params': {'profile': {'gear': {'module': m, 'teeth': sun}}, 'length': width, 'axis': 'z', 'centered': True},
         'features': ['feat.sun']},
        {'part_id': 'ring_rim', 'builder': 'revolve', 'params': {'axis': 'z', 'segments': 96, 'closed_profile': True, 'cap_start': False, 'cap_end': False, 'profile': [[rim_in, -width / 2], [rim_out, -width / 2], [rim_out, width / 2], [rim_in, width / 2]]},
         'features': ['feat.ring']},
        {'part_id': 'ring_teeth', 'builder': 'array', 'params': {'pattern': 'rotational', 'count': ring, 'axis': 'z', 'start_deg': round(math.degrees(layout['ring_phase']), 6),
                                                                  'item': {'builder': 'profile', 'params': {'profile': {'internal_tooth': {'module': m, 'ring_teeth': ring}}, 'length': width, 'axis': 'z', 'centered': True}}},
         'features': ['feat.ring']},
        {'part_id': 'carrier', 'builder': 'revolve', 'params': {'axis': 'z', 'segments': 64, 'profile': [[0.0, -width / 2 - 2 * m], [layout['centre_distance'] + m * planet / 4, -width / 2 - 2 * m],
                                                                                                     [layout['centre_distance'] + m * planet / 4, -width / 2 - 0.5 * m], [0.0, -width / 2 - 0.5 * m]]},
         'features': ['feat.carrier']},
    ]
    joints = [{'id': 'j_sun', 'type': 'revolute', 'parent': 'root', 'child': 'sun', 'origin': [0, 0, 0], 'axis': [0, 0, 1]},
              {'id': 'j_carrier', 'type': 'revolute', 'parent': 'root', 'child': 'carrier', 'origin': [0, 0, 0], 'axis': [0, 0, 1]}]
    for i, p in enumerate(layout['planets']):
        builders.append({'part_id': f'planet_{i}', 'builder': 'profile', 'params': {'profile': {'gear': {'module': m, 'teeth': planet}}, 'length': width, 'axis': 'z', 'centered': True},
                         'transform': {'location': [round(p['centre'][0], 6), round(p['centre'][1], 6), 0.0], 'rotation_deg': [0, 0, round(math.degrees(p['phase']), 6)]},
                         'features': ['feat.planets']})
        joints.append({'id': f'j_planet_{i}', 'type': 'revolute', 'parent': 'carrier', 'child': f'planet_{i}',
                       'origin': [round(p['centre'][0], 6), round(p['centre'][1], 6), 0.0], 'axis': [0, 0, 1]})
    source = {'id': 'gear_geometry', 'kind': 'standard', 'license': 'calculation (ISO 21771 involute geometry, gear_core.py)',
              'note': f'module {m} m, 20 deg pressure angle, centre distance {layout["centre_distance"]:.6f} m, ratio {layout["ratio"]:.4f}'}
    return {
        'schema_version': 1, 'subject_id': subject_id, 'identity': f'planetary gearset {sun}/{planet}/{ring} x{planets} (schematic)',
        'subject_mode': 'schematic', 'request': request,
        'request_trace': [{'phrase': request, 'items': ['feat.sun', 'feat.planets', 'feat.ring', 'feat.carrier']}],
        'sources': [source], 'units': 'm',
        'dimensions': [{'id': 'dim.sun_tip', 'value_m': round(m * (sun + 2), 6), 'tol_pct': 2, 'source_id': 'gear_geometry', 'measure': 'x', 'part_ids': ['sun']},
                       {'id': 'dim.ring_outer', 'value_m': round(2 * rim_out, 6), 'tol_pct': 2, 'source_id': 'gear_geometry', 'measure': 'x', 'part_ids': ['ring_rim']}],
        'features': [{'id': 'feat.sun', 'description': f'{sun}-tooth sun gear', 'part_ids': ['sun'], 'verify': 'presence'},
                     {'id': 'feat.planets', 'description': f'{planets} planet gears of {planet} teeth', 'part_ids': [f'planet_{i}' for i in range(planets)], 'verify': 'count', 'count': planets},
                     {'id': 'feat.ring', 'description': f'{ring}-tooth internal ring gear', 'part_ids': ['ring_rim', 'ring_teeth'], 'verify': 'presence'},
                     {'id': 'feat.carrier', 'description': 'planet carrier (output)', 'part_ids': ['carrier'], 'verify': 'presence'}],
        'materials': [{'part_ids': ['sun'], 'color_srgb': [0.80, 0.55, 0.20], 'metallic': 1, 'roughness': 0.35, 'description': 'sun gear (input)'},
                      {'part_ids': [f'planet_{i}' for i in range(planets)], 'color_srgb': [0.55, 0.60, 0.68], 'metallic': 1, 'roughness': 0.35, 'description': 'planets'},
                      {'part_ids': ['ring_rim', 'ring_teeth'], 'color_srgb': [0.30, 0.32, 0.36], 'metallic': 1, 'roughness': 0.45, 'description': 'fixed ring'},
                      {'part_ids': ['carrier'], 'color_srgb': [0.20, 0.45, 0.75], 'metallic': 0.5, 'roughness': 0.4, 'description': 'carrier (output)'}],
        'builders': builders, 'joints': joints,
        'couplings': [{'id': 'stage', 'kind': 'planetary', 'sun': 'j_sun', 'carrier': 'j_carrier', 'planets': [f'j_planet_{i}' for i in range(planets)],
                       'teeth': {'sun': sun, 'planet': planet, 'ring': ring}, 'ring_part': 'ring_teeth'}],
    }


def harmonic_spec(subject_id, module, flex_teeth, face_width_m=None, request=None):
    """A strain wave (harmonic) gear, circular spline fixed: wave generator in, flexspline out (ratio -2 / flex_teeth).
    Tooth shape, deflection and phases from gear_core (HARMONIC: measured to mesh without overlap)."""
    zf = int(flex_teeth)
    zc = zf + 2
    if zf < 40 or zf % 2:
        raise StudioError('INPUT_INVALID', f'harmonic: the flexspline needs an even tooth count >= 40 (got {zf}); its ellipse meets the ring at two ends')
    m = module
    h = gear_core.HARMONIC
    width = face_width_m or round(m * zf * 0.12, 5)
    deflection = h['deflection'] * m
    flex_wall, ring_wall = 1.2 * m, 4.0 * m
    bore = m * zf / 2 - h['dedendum'] * m - flex_wall                  # the flexspline's plain inner circle
    ratio = gear_core.harmonic_ratio(zf, zc)
    request = request or f'strain wave gear: {zf}-tooth flexspline in a {zc}-tooth circular spline, wave generator in'
    builders = [
        {'part_id': 'circular_spline', 'builder': 'toothed_ring', 'features': ['feat.circular'],
         'params': {'module': m, 'teeth': zc, 'external': False, 'length': width, 'wall_m': ring_wall, 'dedendum': 0.75,
                    'phase_deg': round(180.0 / zc, 6)}},       # a gap faces the flexspline tooth on the major axis
        {'part_id': 'flexspline', 'builder': 'toothed_ring', 'features': ['feat.flex'],
         'params': {'module': m, 'teeth': zf, 'external': True, 'length': width, 'wall_m': flex_wall}},
        {'part_id': 'wave_generator', 'builder': 'profile', 'features': ['feat.wave'],
         'params': {'profile': {'wave_cam': {'inner_radius': round(bore, 7), 'deflection': deflection, 'clearance': round(0.3 * m, 7)}},
                    'length': round(width * 0.9, 6), 'axis': 'z', 'centered': True}},
    ]
    joints = [{'id': 'j_wave', 'type': 'revolute', 'parent': 'root', 'child': 'wave_generator', 'origin': [0, 0, 0], 'axis': [0, 0, 1]},
              {'id': 'j_flex', 'type': 'revolute', 'parent': 'root', 'child': 'flexspline', 'origin': [0, 0, 0], 'axis': [0, 0, 1]}]
    source = {'id': 'gear_geometry', 'kind': 'standard', 'license': 'calculation (strain wave geometry, gear_core.HARMONIC)',
              'note': f'module {m} m, flexspline {zf}, circular spline {zc}, deflection {deflection:.6f} m, ratio {ratio:.4f}'}
    return {
        'schema_version': 1, 'subject_id': subject_id, 'identity': f'strain wave gear {zf}/{zc} (schematic)', 'subject_mode': 'schematic',
        'request': request, 'request_trace': [{'phrase': request, 'items': ['feat.circular', 'feat.flex', 'feat.wave']}],
        'sources': [source], 'units': 'm',
        'dimensions': [{'id': 'dim.ring_outer', 'value_m': round(2 * (m * zc / 2 + 0.75 * m + ring_wall), 6), 'tol_pct': 2, 'source_id': 'gear_geometry',
                        'measure': 'x', 'part_ids': ['circular_spline']}],
        'features': [{'id': 'feat.circular', 'description': f'{zc}-tooth circular spline (fixed)', 'part_ids': ['circular_spline'], 'verify': 'presence'},
                     {'id': 'feat.flex', 'description': f'{zf}-tooth flexspline (output, flexes)', 'part_ids': ['flexspline'], 'verify': 'presence'},
                     {'id': 'feat.wave', 'description': 'elliptical wave generator (input)', 'part_ids': ['wave_generator'], 'verify': 'presence'}],
        'materials': [{'part_ids': ['circular_spline'], 'color_srgb': [0.30, 0.32, 0.36], 'metallic': 1, 'roughness': 0.45, 'description': 'fixed ring'},
                      {'part_ids': ['flexspline'], 'color_srgb': [0.70, 0.72, 0.76], 'metallic': 1, 'roughness': 0.3, 'description': 'flexspline (output)'},
                      {'part_ids': ['wave_generator'], 'color_srgb': [0.85, 0.50, 0.15], 'metallic': 0.8, 'roughness': 0.35, 'description': 'wave generator (input)'}],
        'builders': builders, 'joints': joints,
        'couplings': [{'id': 'wave', 'kind': 'harmonic', 'driver': 'j_wave', 'driven': 'j_flex', 'teeth': {'flex': zf, 'circular': zc},
                       'deform_part': 'flexspline', 'deflection_m': deflection}],
    }


def register(subparsers_of_subject):
    p = subparsers_of_subject.add_parser('planetary', help='Write a planetary gearset spec computed from its tooth counts (gear_core)')
    p.add_argument('--project', required=True); p.add_argument('--subject', required=True)
    for name in ('sun', 'planet', 'ring', 'planets'):
        p.add_argument(f'--{name}', type=int, required=True)
    p.add_argument('--module', type=float, required=True, help='metres (0.002 = module 2)'); p.add_argument('--face-width', type=float)

    def run(a):
        from .subjects import spec_path
        spec = planetary_spec(a.subject, a.module, a.sun, a.planet, a.ring, a.planets, a.face_width)
        target = spec_path(a.project, a.subject)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, spec)
        return {'spec': str(target), 'ratio': round(a.sun / (a.sun + a.ring), 6)}
    p.set_defaults(handler=run)
    p = subparsers_of_subject.add_parser('harmonic', help='Write a strain wave (harmonic) gear spec from its flexspline tooth count (gear_core)')
    p.add_argument('--project', required=True); p.add_argument('--subject', required=True)
    p.add_argument('--flex-teeth', type=int, required=True, help='even, >= 40; the circular spline has two more')
    p.add_argument('--module', type=float, required=True, help='metres (0.0005 = module 0.5)'); p.add_argument('--face-width', type=float)

    def run_harmonic(a):
        from .subjects import spec_path
        spec = harmonic_spec(a.subject, a.module, a.flex_teeth, a.face_width)
        target = spec_path(a.project, a.subject)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, spec)
        return {'spec': str(target), 'ratio': round(gear_core.harmonic_ratio(a.flex_teeth, a.flex_teeth + 2), 6)}
    p.set_defaults(handler=run_harmonic)
