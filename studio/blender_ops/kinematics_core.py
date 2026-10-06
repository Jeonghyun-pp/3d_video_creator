"""Joint values from the joints that drive them, pure Python (no bpy).

A subject spec declares joints (revolute: degrees about an axis; prismatic: metres along it) and couplings between
them. Each coupling kind is one row of COUPLINGS: the fields it needs and how its driven joints follow its driver.
An unknown kind is refused (default deny); a joint driven twice, or a cycle, is refused.

  gear            driven = -driver * teeth[0] / teeth[1]   (external mesh reverses)
  internal_gear   driven = +driver * teeth[0] / teeth[1]   (a gear inside a ring turns the same way)
  belt            driven = +driver * ratio
  rack            driven (m) = driver (deg) * radius_m * pi / 180
  planetary       ring fixed: carrier = sun * zs / (zs + zr); each planet turns, relative to the carrier it rides,
                  by -(sun - carrier) * zs / zp
  harmonic        circular spline fixed: flexspline = -wave_generator * (zc - zf) / zf; the flexspline part is pushed
                  into the wave generator's ellipse (deform_part, deflection_m - kinematics.py keys its shape)
"""
from __future__ import annotations

import math

COUPLINGS = {
    'gear': {'fields': ('driver', 'driven', 'teeth')},
    'internal_gear': {'fields': ('driver', 'driven', 'teeth')},
    'belt': {'fields': ('driver', 'driven', 'ratio')},
    'rack': {'fields': ('driver', 'driven', 'radius_m')},
    'planetary': {'fields': ('sun', 'carrier', 'planets', 'teeth')},
    'harmonic': {'fields': ('driver', 'driven', 'teeth', 'deform_part', 'deflection_m')},
}


def _outputs(c):
    return [c['carrier'], *c['planets']] if c['kind'] == 'planetary' else [c['driven']]


def _input(c):
    return c['sun'] if c['kind'] == 'planetary' else c['driver']


def check(joints, couplings):
    """Problems with the declared mechanism ([] = solvable)."""
    problems = []
    ids = {j['id'] for j in joints}
    driven = {}
    for c in couplings:
        row = COUPLINGS.get(c.get('kind'))
        if row is None:
            problems.append(f"coupling {c.get('id')}: unknown kind {c.get('kind')!r} (known: {sorted(COUPLINGS)})")
            continue
        missing = [f for f in row['fields'] if f not in c]
        if missing:
            problems.append(f"coupling {c['id']}: {c['kind']} needs {missing}")
            continue
        for joint in [_input(c), *_outputs(c)]:
            if joint not in ids:
                problems.append(f"coupling {c['id']}: no joint {joint}")
        for joint in _outputs(c):
            if joint in driven:
                problems.append(f"joint {joint} is driven by both {driven[joint]} and {c['id']}")
            driven[joint] = c['id']
    if not problems:
        try:
            order(couplings)
        except ValueError as error:
            problems.append(str(error))
    return problems


def order(couplings):
    """Couplings in an order where every driver is known before it drives."""
    pending, known, out = list(couplings), set(), []
    driven = {j for c in couplings for j in _outputs(c)}
    known |= {_input(c) for c in couplings if _input(c) not in driven}
    while pending:
        ready = [c for c in pending if _input(c) in known]
        if not ready:
            raise ValueError(f"couplings form a cycle: {[c['id'] for c in pending]}")
        for c in ready:
            out.append(c); pending.remove(c); known |= set(_outputs(c))
    return out


def solve(couplings, inputs):
    """{joint: value} for every joint reachable from the driven inputs {joint: value}."""
    values = dict(inputs)
    for c in order(couplings):
        x = values.get(_input(c))
        if x is None:
            continue
        kind = c['kind']
        if kind == 'gear':
            values[c['driven']] = -x * c['teeth'][0] / c['teeth'][1]
        elif kind == 'internal_gear':
            values[c['driven']] = x * c['teeth'][0] / c['teeth'][1]
        elif kind == 'belt':
            values[c['driven']] = x * c['ratio']
        elif kind == 'rack':
            values[c['driven']] = math.radians(x) * c['radius_m']
        elif kind == 'harmonic':
            zf, zc = c['teeth']['flex'], c['teeth']['circular']
            values[c['driven']] = -x * (zc - zf) / zf
        elif kind == 'planetary':
            zs, zp, zr = c['teeth']['sun'], c['teeth']['planet'], c['teeth']['ring']
            carrier = x * zs / (zs + zr)
            values[c['carrier']] = carrier
            for planet in c['planets']:
                values[planet] = -(x - carrier) * zs / zp
    return values


def drive_value(drive, u, seconds):
    """A drive's input at progress u (0..1) of its action: keyed values (linear between keys) or a constant speed."""
    if 'rpm' in drive:
        return drive['rpm'] * 360.0 / 60.0 * seconds * u
    keys = sorted(drive['keys'], key=lambda k: k['t'])
    if u <= keys[0]['t']:
        return keys[0]['value']
    for a, b in zip(keys, keys[1:]):
        if u <= b['t']:
            w = (u - a['t']) / max(1e-9, b['t'] - a['t'])
            if drive.get('profile') == 'ease':
                w = w * w * (3 - 2 * w)
            return a['value'] + (b['value'] - a['value']) * w
    return keys[-1]['value']
