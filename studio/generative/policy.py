"""What a generated clip may be used for, decided by the shot's role (route.role) - one function for every caller.

  explain  the shot explains structure: exact parts, positions, labels, dimensions. A generated clip is usable only
           as a hybrid restyle that passed the structure gate (edge IoU + anchors, qa_generative.structure); labels and
           graphics then ride on its measured 2D anchors. Anything else goes back to the Blender pass.
  mood     the shot sets atmosphere (establishing aerials, interiors at large, transitions). The clip is usable with
           captions only; structure, flicker, morph and text results are recorded as warnings for the person who
           picks the take. Labels and graphics are refused (project.validate_shot, ROUTE_ROLE_CONFLICT).

No numeric structure floor for mood (measured 2026-10-05, BUILD_REPORT "H0"): edge preservation could not separate
a restyle that kept the layout but replaced surfaces (samsung A/B s03 Wan 0.29, coarse scale 0.40) from the same
clay shifted 5 % (0.55 / 0.67). The human take selection is the gate instead.

Default role: 'mood' for pure generative shots (they never had a structure input), 'explain' otherwise.
"""
from __future__ import annotations

ROLES = ('explain', 'mood')


def role_of(route):
    route = route or {}
    return route.get('role') or ('mood' if route.get('mode') == 'generative' else 'explain')


def policy_for(shot):
    route = shot.get('route') or {}
    role = role_of(route)
    return {'role': role, 'mode': route.get('mode', 'blender'),
            'structure_required': role == 'explain', 'overlays_allowed': role == 'explain'}


def judge(manifest, policy):
    """{usable, reasons, warnings} for one generated take under the shot's policy."""
    qa = manifest.get('qa') or {}
    structure = qa.get('structure') or manifest.get('structure_qa') or {}
    warnings = [w for key in ('flicker', 'morph', 'text', 'look_style') for w in (qa.get(key) or {}).get('warnings', [])]
    reasons = []
    if policy['structure_required']:
        if policy['mode'] != 'hybrid':
            reasons.append('explain shots need a hybrid restyle with a structure check; a pure generative clip cannot explain structure')
        elif structure.get('passed') is not True:
            reasons.append('structure gate failed: ' + '; '.join(structure.get('reasons') or ['not measured']))
    elif structure.get('passed') is False:
        warnings.append('structure (mood, not gated): ' + '; '.join(structure.get('reasons') or []))
    return {'role': policy['role'], 'usable': not reasons, 'reasons': reasons, 'warnings': warnings}
