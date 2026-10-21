"""What a generated clip may be used for, decided by the shot's role (route.role) - one function for every caller.

  explain  the shot explains structure: exact parts, positions, labels, dimensions. A generated clip is usable as a
           hybrid take whose declared key parts survived: each part the take lost (qa_generative.parts against the
           clay) is put back from Blender pixels (keep.auto_keep), and a part neither kept nor checked is not trusted.
           Whole-frame edges (qa_generative.structure) are said, not gated - since 2026-10-10, see below. Labels ride
           on measured 2D anchors (whole-frame structure passed) or on parts kept as Blender pixels, whose 3D anchors
           are exact by construction; graphics still need the whole-frame pass.
  mood     the shot sets atmosphere (establishing aerials, interiors at large, transitions). The clip is usable with
           captions only; structure, flicker, morph and text results are recorded as warnings for the person who
           picks the take. Labels and graphics are refused (project.validate_shot, ROUTE_ROLE_CONFLICT).

Why explain stopped gating on whole-frame edges (2026-10-10): 11 paid whole-frame restyles, 0 kept the frame's edges,
while the user's rule is "구조를 완벽하게 지키는것보다 봤을때 보기에 좋아야": the model fills the frame, the structure
the shot explains is the declared key parts, and those are measured per part and put back when lost.

No numeric structure floor for mood (measured 2026-10-05, BUILD_REPORT "H0"): edge preservation could not separate
a restyle that kept the layout but replaced surfaces (samsung A/B s03 Wan 0.29, coarse scale 0.40) from the same
clay shifted 5 % (0.55 / 0.67). The human take selection is the gate instead.

Default role: 'mood' for pure generative shots (they never had a structure input), 'explain' otherwise.

A project may let the user pick a hybrid explain take whose structure is not verified (project.policy.explain_generated
'pick_without_overlays'): only when the shot carries no labels or graphics, and only through the user's own words
(generate select --user-words). The miss is kept as a warning; adding a label later makes the take unusable again.
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


UNVERIFIED = 'structure not verified'


def judge(manifest, policy, picked=False):
    """{usable, reasons, warnings, pickable} for one generated take under the shot's policy. `picked`: the user chose
    this take in their own words (only that can carry an unverified structure on an explain shot, and only if allowed)."""
    qa = manifest.get('qa') or {}
    structure = qa.get('structure') or manifest.get('structure_qa') or {}
    parts = qa.get('parts') or {}
    kept_record = manifest.get('kept') or {}
    if kept_record.get('mode') == 'generate_only':   # only the named regions came from the take: every other part is Blender
        generated = [g for g in kept_record.get('generate_only') or [] if g != '@background']
        named = set((parts.get('parts') or {})) | set(parts.get('lost') or [])
        kept = {i for i in named if not any(i == g or i.startswith(g + '/') for g in generated)}
    else:
        kept = set(kept_record.get('parts') or [])
    # a part too small or hidden on every sampled frame was not measured: it is not "held" (2026-10-10: a 54 px layer
    # fell below the check's sampling width and read as kept)
    unmeasured = sorted(i for i, row in (parts.get('parts') or {}).items() if row.get('ratio') is None and i not in kept)
    # what carries the structure is a declared part put back from Blender, never a decal or a label pasted on (2026-10-21:
    # a take with no part measured and failed edges passed because a label was put back)
    held = kept & set(parts.get('parts') or {})
    warnings = [w for key in ('flicker', 'morph', 'text', 'judder', 'look_style', 'aspect') for w in (qa.get(key) or {}).get('warnings', [])]
    reasons = []
    edges_missed = 'whole-frame edges: ' + '; '.join(structure.get('reasons') or []) if structure.get('passed') is False else None
    if policy['structure_required']:
        if policy['mode'] != 'hybrid':
            reasons.append('explain shots need a hybrid restyle with a structure check; a pure generative clip cannot explain structure')
        elif structure.get('passed') is not True and (unmeasured or not parts.get('parts')) and not (held and not unmeasured):
            # neither the frame's edges nor any declared part was checked: nothing says the structure is there
            miss = (f"{UNVERIFIED}: {edges_missed or 'whole-frame edges not measured'}; key parts: "
                    + (f"{unmeasured} not measured (too small or hidden on the sampled frames)" if unmeasured else parts.get('note') or 'not checked'))
            if policy.get('human_pick_allowed') and picked:
                warnings.append(miss + " (used by the user's pick; no labels or graphics ride on it)")
            else:
                reasons.append(miss)
        elif edges_missed:
            warnings.append(edges_missed + ' (said, not gated: the key parts carry the structure)')
        if structure.get('passed') is not True:
            labels = 'labels' in policy['overlays'] and manifest.get('anchors_source') != 'kept'
            if labels or 'graphics' in policy['overlays']:
                reasons.append('labels need 2D anchors: keep the labelled parts (generate keep, automatic for labels) or pass the '
                               'whole-frame structure gate' if labels else 'graphics need the whole-frame structure gate passed')
    elif edges_missed:
        warnings.append('structure (mood, not gated): ' + '; '.join(structure.get('reasons') or []))
    lost = sorted(set(parts.get('lost') or []) - kept)
    if lost:   # a part the take dropped and nothing put back: broken on an explain shot (gates kind 'broken'), said on mood
        miss = f"part(s) lost in the take: {lost} (kept no better than the clay shifted 5 %; keep them with shot.screen.keep)"
        (reasons if policy['structure_required'] else warnings).append(miss)
    angle = (qa.get('light') or {}).get('angle_deg')
    if angle is not None and angle > LIGHT_TURN_DEG:
        warnings.append(f'light turned {angle} deg from the look render (fitted on the normal pass)')
    pickable = bool(reasons) and policy.get('human_pick_allowed', False) and policy['mode'] == 'hybrid' and \
        all(r.startswith(UNVERIFIED) for r in reasons)
    return {'role': policy['role'], 'usable': not reasons, 'reasons': reasons, 'warnings': warnings, 'pickable': pickable}
