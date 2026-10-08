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

A project may let the user pick a hybrid explain take that failed the structure gate (project.policy.explain_generated
'pick_without_overlays'): only when the shot carries no labels or graphics, and only through the user's own words
(generate select --user-words). The failure is kept as a warning; adding a label later makes the take unusable again,
because overlays need the measured 2D anchors only a structure-passed take has.
"""
from __future__ import annotations

ROLES = ('explain', 'mood')
LIGHT_TURN_DEG = 45.0   # provisional (2026-10-07, no measured takes yet): past this the take's light is said, never gated


def project_policy(path):
    """project.policy of the project at `path` ({} without a project file)."""
    from pathlib import Path
    import json
    file = Path(path) / 'project.json'
    return (json.loads(file.read_text(encoding='utf-8')).get('policy') or {}) if file.is_file() else {}


def role_of(route):
    route = route or {}
    return route.get('role') or ('mood' if route.get('mode') == 'generative' else 'explain')


def policy_for(shot, project_policy=None):
    route = shot.get('route') or {}
    role = role_of(route)
    overlays = [k for k in ('labels', 'graphics') if shot.get(k)]
    pick = (project_policy or {}).get('explain_generated', 'gate') == 'pick_without_overlays'
    return {'role': role, 'mode': route.get('mode', 'blender'), 'overlays': overlays,
            'structure_required': role == 'explain', 'overlays_allowed': role == 'explain',
            'human_pick_allowed': role == 'explain' and route.get('mode') == 'hybrid' and not overlays and pick}


def judge(manifest, policy, picked=False):
    """{usable, reasons, warnings, pickable} for one generated take under the shot's policy. `picked`: the user chose
    this take in their own words (only that can carry a structure miss on an explain shot, and only if allowed)."""
    qa = manifest.get('qa') or {}
    structure = qa.get('structure') or manifest.get('structure_qa') or {}
    warnings = [w for key in ('flicker', 'morph', 'text', 'judder', 'look_style', 'aspect') for w in (qa.get(key) or {}).get('warnings', [])]
    reasons = []
    if policy['structure_required']:
        if policy['mode'] != 'hybrid':
            reasons.append('explain shots need a hybrid restyle with a structure check; a pure generative clip cannot explain structure')
        elif structure.get('passed') is not True:
            miss = 'structure gate failed: ' + '; '.join(structure.get('reasons') or ['not measured'])
            if policy.get('human_pick_allowed') and picked:
                warnings.append(miss + " (used by the user's pick; no labels or graphics ride on it)")
            else:
                reasons.append(miss)
    elif structure.get('passed') is False:
        warnings.append('structure (mood, not gated): ' + '; '.join(structure.get('reasons') or []))
    lost = sorted(set((qa.get('parts') or {}).get('lost') or []) - set((manifest.get('kept') or {}).get('parts') or []))
    if lost:   # a part the take dropped and nothing put back: broken on an explain shot (gates kind 'broken'), said on mood
        miss = f"part(s) lost in the take: {lost} (kept no better than the clay shifted 5 %; keep them with shot.screen.keep)"
        (reasons if policy['structure_required'] else warnings).append(miss)
    angle = (qa.get('light') or {}).get('angle_deg')
    if angle is not None and angle > LIGHT_TURN_DEG:
        warnings.append(f'light turned {angle} deg from the look render (fitted on the normal pass)')
    pickable = bool(reasons) and policy.get('human_pick_allowed', False) and policy['mode'] == 'hybrid' and \
        all(r.startswith('structure gate failed') for r in reasons)
    return {'role': policy['role'], 'usable': not reasons, 'reasons': reasons, 'warnings': warnings, 'pickable': pickable}
