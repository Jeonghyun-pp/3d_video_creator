"""Region light targets: the brightness each part of the picture should have (shot.screen.light.regions, or what a
reference critique proposes), solved as light-group multipliers from the light probe's per-group contributions.

Why: archcut3 s02/s03 came out with a blue cast, a ceiling 30 % too bright and a torch pool nobody could see, and
the only light check was the key's direction. Light adds linearly, so once the probe (blender_ops/light_probe.py) has
measured what every group gives each region alone, the multipliers that move each region toward its target are a
small least-squares problem - no render per guess.

The solve: region r now reads L*_r on our lit preview and should read T_r; the linear ratio it needs is q_r =
Y(T_r) / Y(L*_r) (display ratios stand in for scene-linear ones: the tone curve bends them, so the result is a step
to re-measure, not an answer). Multipliers m_g >= 0 minimise sum_r ((sum_g m_g C_gr) / (sum_g C_gr) - q_r)^2 +
REGULARISE * sum_g (m_g - 1)^2 - the smallest change that does it. When every group would scale the same way the
answer is exposure, said as such. Proposals use the shot edit grammar for the look's rig lights; other groups (the
sun, the practicals, the world, author lights, emissive materials) get a hint naming the value to change.
"""
from __future__ import annotations

import math

from .blender_ops.regions_core import is_region, l_star_to_y

REGULARISE = 0.05
BOUNDS = (0.05, 20.0)
STEPS = 4000
UNIFORM_SPREAD = 0.08     # multipliers within 8 % of each other: one exposure change, not a relight


def needed_ratios(current, targets):
    """{region: q} for the targets whose region was measured."""
    out = {}
    for target in targets:
        region = target['region']
        if region in current:
            out[region] = l_star_to_y(target['luminance']) / max(l_star_to_y(current[region]), 1e-5)
    return out


def solve(contributions, ratios):
    """{group: multiplier} for {group: {region: Y}} and {region: q}; groups that light none of the regions stay 1."""
    regions = [r for r in ratios if sum(c.get(r, 0.0) for c in contributions.values()) > 0]
    groups = [g for g, c in contributions.items() if any(c.get(r, 0.0) > 0 for r in regions)]
    totals = {r: sum(contributions[g].get(r, 0.0) for g in contributions) for r in regions}
    m = {g: 1.0 for g in groups}
    if not regions or not groups:
        return {g: 1.0 for g in contributions}, 0.0
    lo, hi = BOUNDS
    rate = 0.5

    def residual(r):
        return sum(m[g] * contributions[g].get(r, 0.0) for g in groups) / totals[r] + \
            sum(contributions[g].get(r, 0.0) for g in contributions if g not in m) / totals[r] - ratios[r]
    for _ in range(STEPS):
        grads = {g: 2 * REGULARISE * (m[g] - 1) + sum(2 * residual(r) * contributions[g].get(r, 0.0) / totals[r] for r in regions) for g in groups}
        for g in groups:
            m[g] = min(hi, max(lo, m[g] - rate * grads[g]))
    error = math.sqrt(sum(residual(r) ** 2 for r in regions) / len(regions))
    return {**{g: 1.0 for g in contributions}, **{g: round(v, 3) for g, v in m.items()}}, round(error, 4)


def proposals(multipliers, rig_rows, contributions, ratios=None):
    """Shot edit ops for the look's rig lights, hints for every other group whose multiplier moved. When every region
    needs the same ratio, that is exposure (one value), not a relight."""
    needed = list((ratios or {}).values())
    if needed and (max(needed) - min(needed)) / max(needed) < UNIFORM_SPREAD and abs(math.log2(sum(needed) / len(needed))) >= 0.07:
        ev = math.log2(sum(needed) / len(needed))
        return [{'op': 'set', 'path': '/render/grade/exposure_offset_ev', 'delta': round(ev, 2),
                 'why': f'every region needs the same change ({sum(needed) / len(needed):.2f}x): that is exposure'}]
    moved = {g: v for g, v in multipliers.items() if abs(v - 1) >= 0.05}
    if not moved:
        return []
    rig = {row['name']: row for row in rig_rows or []}
    out = []
    for group, factor in sorted(moved.items(), key=lambda kv: -abs(math.log(kv[1]))):
        if group in rig and rig[group].get('irradiance'):
            out.append({'op': 'set', 'path': f'/render/lighting/rig/{group}/irradiance', 'value': round(rig[group]['irradiance'] * factor, 4),
                        'why': f'{group} x{factor:.2f}'})
        elif group == 'emission':
            out.append({'hint': f'emissive materials (windows, panels, signs) x{factor:.2f}: their emission strength'})
        elif group == 'world':
            out.append({'hint': f'the world (sky / HDRI) x{factor:.2f}: the look preset env_irradiance or the world strength'})
        elif group.startswith('author:'):
            out.append({'hint': f'author light {group[7:]} x{factor:.2f}: its energy in the author script'})
        else:
            out.append({'hint': f'look light {group} x{factor:.2f}: not a rig light the shot can override yet (sun / practicals)'})
    return out


def check(targets):
    problems = [f"{t.get('region')!r} is not a region (bands like top-left, or class:<id>)" for t in targets if not is_region(t.get('region'))]
    return problems


def workbench_targets(project, call, frame=None, targets=None, size=160, samples=16, shot=None, session_dir=None):
    """light_targets (workbench): the shot's screen.light.regions (or `targets`) measured on our lit preview of a frame,
    the light probe's contributions, the multipliers that move each region to its target, and the shot values to set."""
    from PIL import Image
    from .common import StudioError, read_json
    from .critique import class_regions, grid_regions, measure, _edges, _lab
    targets = targets or (((shot or {}).get('screen') or {}).get('light') or {}).get('regions') or []
    if not targets:
        raise StudioError('INPUT_INVALID', 'no region targets: set shot.screen.light.regions or pass targets [{region, luminance, tol}]')
    problems = check(targets)
    if problems:
        raise StudioError('INPUT_INVALID', '; '.join(problems))
    frame_args = {'frame': int(frame)} if frame is not None else {}
    probe = call('light_contributions', {**frame_args, 'size': int(size), 'samples': int(samples)})
    lit = call('preview', {'views': ['shot'], 'passes': ['lit', 'id'], 'size': int(size), **frame_args})
    images = lit['images']['shot']
    picture = Image.open(images['lit']).convert('RGB')
    regions = {**grid_regions(picture.size), **class_regions(images['id'], lit['palette'], picture.size)}
    lab, edges = _lab(picture), _edges(picture)
    current = {}
    for t in targets:
        if t['region'] in regions and (m := measure(picture, lab, edges, regions[t['region']])):
            current[t['region']] = m['L']
    ratios = needed_ratios(current, targets)
    multipliers, error = solve(probe['groups'], ratios)
    rig_rows = []
    if session_dir:
        report = session_dir / 'generated' / 'look_report.json'
        if report.is_file():
            rig_rows = ((read_json(report).get('passes') or {}).get('lighting') or {}).get('rig') or []
    rows = [{'region': t['region'], 'target': t['luminance'], 'tol': t.get('tol', 4.0), 'measured': round(current[t['region']], 1) if t['region'] in current else None,
             'missed': t['region'] in current and abs(current[t['region']] - t['luminance']) > t.get('tol', 4.0),
             'ratio_needed': round(ratios[t['region']], 3) if t['region'] in ratios else None} for t in targets]
    return {'regions': rows, 'multipliers': multipliers, 'fit_error': error, 'groups': probe['lights'],
            'proposals': proposals(multipliers, rig_rows, probe['groups'], ratios), 'probe_seconds': probe['seconds']}
