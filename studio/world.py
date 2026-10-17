"""The world a video happens in, settled before the shot list - and, for each thing in it, who makes its shape and who
makes its look.

Why (2026-10-10): a floor-noise reel was built as two rooms in a box floating in the dark. Nothing asked for a place:
the explained structure had specs, sources and checks, the world around it had nothing, so it was left out - and the
concept picture was a bare block too. A generated take of it looked like a dollhouse ("주변에 배경도 없고 박스안에
그려놓은것같고"). The user's answer: see the whole minute as pictures first, settle the world from them, build the
camera and the core subject in Blender, leave the rest as clay stand-ins, and let the generative model fill them.

Kinds (the user: "세계관이 이어지면 연속적이어야 할거고, 아니면 굳이고 ... 세계관이라는게 없는 설명하는 영상이라면
필요도 없어보이는데"):
  continuous   one place the shots share - one world reference picture goes to every generated shot
  independent  each shot its own place
  none         an explanation with no world (a mechanism, a body section): a stage tone and its light only

Elements are named by what they do on screen, never by topic: subject (what is explained), set (where it stands),
context (what tells where we are), agent (people, animals and their action), phenomenon (water, smoke, a vibration),
light. Each has two owners: `shape` (geometry and motion) and `look` (appearance), each 'blender' or 'generative'.
Requirements decide what is forced (OWNER_RULES); everything else defaults to a Blender clay stand-in with a generated
look - the user's "구도랑 핵심 대상만 블렌더로 하고 나머지 화이트점토로 해서 그 부분 생성모델한테 채우라고".
"""
from __future__ import annotations

from pathlib import Path

from .common import StudioError, file_hash, now, read_json, write_json
from .project import load_project, project_dir

WORLD_FILE = 'world.json'
# requirement -> what it forces (a set of allowed owners per axis). Why each: an exact claim or a label anchor needs
# the very pixels Blender computed (11 whole-frame restyles kept no edges; labels ride on exact anchors); a motion the
# narration times (the thump) must keep Blender's timing; one thing seen alike in several shots is Blender's unless a
# continuous world's reference picture keeps it alike; a far background has no shape worth modelling - its ground and
# sky are the engine's environment shell (world.shell, blender_ops/env_shell_core.py: the horizon is decided in 3D), its
# look and everything past the horizon the model's.
OWNER_RULES = {
    'exact': {'shape': {'blender'}, 'look': {'blender'}},
    'labelled': {'shape': {'blender'}, 'look': {'blender'}},
    'motion_bound': {'shape': {'blender'}},
}
DEFAULT_OWNER = {'shape': 'blender', 'look': 'generative'}   # a clay stand-in the model fills


def suggested_owner(element, kind):
    """The owners the rules leave: forced axes as forced, the rest the default; a far background generated whole."""
    reqs = set(element.get('requirements') or [])
    owner = dict(DEFAULT_OWNER)
    if 'far_background' in reqs and not reqs & {'exact', 'labelled', 'motion_bound'}:
        owner = {'shape': 'generative', 'look': 'generative'}
    if 'recurring' in reqs and kind != 'continuous':
        owner['shape'] = 'blender'
    for req in reqs:
        for axis, allowed in OWNER_RULES.get(req, {}).items():
            if owner[axis] not in allowed:
                owner[axis] = sorted(allowed)[0]
    return owner


def owner_conflicts(element, kind):
    """Owner choices the requirements forbid."""
    reqs = set(element.get('requirements') or [])
    out = []
    for req in sorted(reqs):
        for axis, allowed in OWNER_RULES.get(req, {}).items():
            if element['owner'][axis] not in allowed:
                out.append(f"OWNER_CONFLICT: {element['id']} is {req} but its {axis} is {element['owner'][axis]} "
                           f"(it must be {'/'.join(sorted(allowed))})")
    if 'recurring' in reqs and kind != 'continuous' and element['owner']['shape'] == 'generative':
        out.append(f"OWNER_CONFLICT: {element['id']} recurs across shots in a {kind} world with no shared reference picture: "
                   'its shape must be blender')
    return out


def lint(path, body, script_body):
    """(errors, warnings) of a world body against the approved script."""
    errors, warnings = [], []
    if script_body is not None:   # a delegated run has no script layer: its beats name what they tell in their own words
        lines = [l['line_id'] for l in script_body.get('lines') or []]
        told = [i for b in body['beats'] for i in b['line_ids']]
        errors += [f'script line {i} is in no beat' for i in lines if i not in told]
        errors += [f'unknown script line {i} in a beat' for i in sorted(set(told) - set(lines))]
    for beat in body['beats']:
        for picture in beat['pictures']:
            file = Path(path) / picture['path']
            if not file.is_file() or file_hash(file) != picture['sha256']:
                errors.append(f"beat {beat['beat_id']}: picture {picture['path']} is missing or changed")
    ids = [e['id'] for e in body['elements']]
    errors += [f'duplicate element {i}' for i in sorted(set(ids)) if ids.count(i) > 1]
    if body['kind'] != 'none' and not any(e['function'] == 'context' for e in body['elements']):
        errors.append('CONTEXT_MISSING: a world with a place needs at least one context element (what tells where we are: '
                      'the building, the street, the sky) - without one the subject floats in the dark')
    if body['kind'] == 'continuous' and not body.get('reference'):
        errors.append('WORLD_REFERENCE_MISSING: a continuous world keeps its shots alike with one reference picture '
                      '(world.reference) - add one from the beat pictures')
    for element in body['elements']:
        errors += owner_conflicts(element, body['kind'])
        suggestion = suggested_owner(element, body['kind'])
        if element['function'] != 'light' and element['owner'] != suggestion and not owner_conflicts(element, body['kind']):
            warnings.append(f"{element['id']}: owners {element['owner']} differ from the rule's {suggestion} - say why in its why")
    if body['kind'] != 'none' and not body.get('shell'):
        warnings.append('SHELL_MISSING: a world with a place declares no shell (world.shell: ground land|sea|floor|none, sky) - '
                        'the engine builds the ground to the horizon and the sky only when they are declared, so the camera '
                        'and the clay review see them; without it every shot must declare scene.shell or stands in a void')
    if not any(e['function'] == 'subject' for e in body['elements']):
        warnings.append('no subject element: what does the video explain or follow?')
    return errors, warnings


def load(path):
    file = project_dir(path) / WORLD_FILE
    return read_json(file) if file.is_file() else None


def materialize(path, body, decided_by='user', note=None):
    """world.json: what the tools read (shot list lint, shot materialization, light projection, generation inputs)."""
    path = project_dir(path)
    record = {'schema_version': 1, **body, 'decided_by': decided_by, **({'agent_note': note} if note else {}), 'written_at': now()}
    write_json(path / WORLD_FILE, record)
    return {'world': str(path / WORLD_FILE)}


def write(project, body, agent_note):
    """A delegated run's world (the agent's decision with its reason, never an approval): checked like the layer."""
    from .decisions import envelope, validate_body
    path = project_dir(project)
    if not load_project(path).get('delegation'):
        raise StudioError('INPUT_INVALID', 'world write is for delegated runs; on the ladder use decide propose --layer world')
    validate_body('world', body)
    script = (envelope(path, 'script') or {}).get('body')
    errors, warnings = lint(path, body, script)
    if errors:
        raise StudioError('DECISION_INVALID', 'world: ' + '; '.join(errors[:6]))
    return {**materialize(path, body, 'agent', agent_note), 'warnings': warnings}


def element(path, element_id):
    world = load(path)
    found = next((e for e in (world or {}).get('elements', []) if e['id'] == element_id), None)
    if found is None:
        raise StudioError('INPUT_INVALID', f'no world element {element_id}')
    return found


def link(project, element_id, ids):
    """Record the scene ids that build an element (its parts or stand-ins) in world.json - data the build reads."""
    path = project_dir(project)
    world = load(path)
    if world is None:
        raise StudioError('INPUT_INVALID', 'no world.json yet')
    for e in world['elements']:
        if e['id'] == element_id:
            e['ids'] = list(dict.fromkeys(list(e.get('ids') or []) + list(ids)))
            write_json(path / WORLD_FILE, world)
            return {'element': element_id, 'ids': e['ids'], 'shots': resync(path)}
    raise StudioError('INPUT_INVALID', f'no world element {element_id}')


def resync(project):
    """Each shot's generate_only from its elements' owners and linked ids (stand-ins are linked after the shot list)."""
    from copy import deepcopy
    from .project import load_shot
    from .routing import _write_shot
    path = project_dir(project)
    world = load(path)
    changed = []
    for entry in load_project(path)['shots']:
        shot = load_shot(path, entry['shot_id'])
        elements = (shot.get('screen') or {}).get('elements')
        if not world or not elements:
            continue
        plan = shot_plan(world, elements)
        updated = deepcopy(shot)
        if plan['generate_only']:
            updated['screen']['generate_only'] = plan['generate_only']
        else:
            updated['screen'].pop('generate_only', None)
        if updated != shot:
            _write_shot(path, shot['shot_id'], updated, shot['revision'])
            changed.append(shot['shot_id'])
    return changed


def shot_plan(world, element_ids):
    """For a shot showing these elements: the regions the model fills, the route mode and the role. A generated look
    becomes a region (its linked ids; a generated far background is '@background'). No generated look - blender; a
    Blender shape anywhere (the camera rides a Blender pass: clay stand-ins at least) - hybrid; nothing of Blender's -
    generative. The role is explain when a Blender-look element is on screen (exact, labelled), else mood."""
    elements = [e for e in world['elements'] if e['id'] in set(element_ids) and e['function'] != 'light']
    generated = [e for e in elements if e['owner']['look'] == 'generative']
    regions = []
    for e in generated:
        if e['owner']['shape'] == 'generative':
            regions.append('@background')
        regions += list(e.get('ids') or [])
    blender_look = [e for e in elements if e['owner']['look'] == 'blender']
    blender_shape = [e for e in elements if e['owner']['shape'] == 'blender']
    mode = 'blender' if not generated else ('hybrid' if blender_shape else 'generative')
    unbuilt = [e['id'] for e in generated if e['owner']['shape'] == 'blender' and not e.get('ids')]
    return {'mode': mode, 'role': 'explain' if blender_look else 'mood', 'generate_only': list(dict.fromkeys(regions)), 'unbuilt': unbuilt}


DEFAULT_GENERATIVE = {   # mode -> the adapter a world-planned shot starts with (changed like any route, before review)
    'hybrid': {'model': 'seedance-2.5', 'operation': 'video_to_video'},   # follows the clay pass, copies its reference pictures
    'generative': {'model': 'veo-3.1', 'operation': 'text_to_video'},
}


def planned_route(shot, plan, features=(), fps=30):
    """A proposed route from the world plan (status proposed: a review sheet and the user's words still decide)."""
    route = {'mode': plan['mode'], 'features': list(features), 'rule_id': 'W_world_owners',
             'reason': f"world elements: {plan['mode']} ({'regions ' + ', '.join(plan['generate_only']) if plan['generate_only'] else 'no generated look'})",
             'confidence': 'medium', 'decided_by': 'agent', 'status': 'proposed', 'approved_at': None, 'approval_evidence': None}
    if plan['mode'] != 'blender':
        route['role'] = plan['role']
        route['generative'] = {'provider': 'fal', **DEFAULT_GENERATIVE[plan['mode']], 'prompt_ref': f"prompts/{shot['shot_id']}.txt",
                               'duration_seconds': round(shot['duration_frames'] / fps, 3), 'usd_per_second': None, 'max_attempts': 1,
                               'text_in_frame': False, 'ai_disclosure': True, 'inputs': []}
    return route


def lit(shot, world):
    """The shot with the world's light filled in where the shot sets none (the shot's own values win): the sun's
    direction and colour for a preset with a sun, the practicals' colour for one with practicals, the camera white
    balance when the world names it. Read at build, never written back: one light for Blender and for the prompt."""
    light = (world or {}).get('time_light') or {}
    if not light:
        return shot
    from copy import deepcopy
    out = deepcopy(shot)
    render = out.setdefault('render', {})
    lighting = render.setdefault('lighting', {})
    preset = _lighting_preset(render.get('look_preset'))
    if preset.get('sun') is not None:
        sun = lighting.setdefault('sun', {})
        if light.get('temperature_k'):
            sun.setdefault('temperature_k', light['temperature_k'])
        for key in ('elevation_deg', 'azimuth_deg'):
            if (light.get('sun') or {}).get(key) is not None:
                sun.setdefault(key, light['sun'][key])
    if preset.get('practicals') is not None and light.get('temperature_k'):
        lighting.setdefault('practicals', {}).setdefault('temperature_k', light['temperature_k'])
    if light.get('white_balance_k'):
        render.setdefault('grade', {}).setdefault('white_balance_k', light['white_balance_k'])
    if not lighting:
        render.pop('lighting')
    return out


def _lighting_preset(look_preset):
    from .common import REPO
    if not look_preset:
        return {}
    looks = read_json(REPO / 'studio/blender_ops/look_data/look_presets.json')['presets'].get(look_preset) or {}
    name = looks.get('lighting') if isinstance(looks, dict) else None
    return read_json(REPO / 'studio/blender_ops/look_data/lighting_presets.json')['presets'].get(name or '', {})


def shell_for(shot, world):
    """The environment shell a build makes for this shot (blender_ops/env_shell_core.resolve) - with `held` when the
    shot's look lights the scene itself (its lighting preset owns the sky and the ground's light; the shell's far ground
    would enter the look's lit bounds, a frozen scene_roles row the user has not approved). None when none is declared."""
    from .blender_ops import env_shell_core
    shell = env_shell_core.resolve(shot, world)
    if shell and _lighting_preset((shot.get('render') or {}).get('look_preset')):
        shell['held'] = ('ENV_SHELL_HELD: the look lights the scene (its lighting preset owns the sky); the engine shell is '
                         'not built under it until scene_roles has a far-ground row (frozen code: the user\'s words)')
    return shell


def context_void(frame_report, shot, world, usual_p90):
    """CONTEXT_VOID (taste): a world with a place whose shot shows mostly nothing - the empty share of the frame (no
    object renders there) above what study reels show at their 90th percentile, with no generated background to fill it.
    The first frame counts too: the floor-noise opening was a box in the dark."""
    if not world or world.get('kind') == 'none' or usual_p90 is None:
        return []
    if '@background' in ((shot.get('screen') or {}).get('generate_only') or []):
        return []
    shell = shell_for(shot, world)
    if shell and not shell.get('held') and shell['ground'] != 'none' and shell['sky'] != 'none':
        return []   # the engine's ground and sky fill the frame to the horizon (the sky reads as background in the probe)
    rows = [f for f in frame_report.get('frames', []) if 'opaque_share' in f]
    if not rows:
        return []
    voids = sorted(1 - f['opaque_share'] for f in rows)
    worst, median = voids[-1], voids[len(voids) // 2]
    if worst <= usual_p90:
        return []
    return [f"CONTEXT_VOID: up to {round(worst * 100)} % of the frame shows nothing (median {round(median * 100)} %; study reels "
            f"stay under {round(usual_p90 * 100)} %) in a {world['kind']} world - build the context as clay stand-ins or hand the "
            "background to the model (a context element with far_background)"]


def look_words(world):
    """The prompt's Look sentence from the same light the render uses."""
    light = (world or {}).get('time_light') or {}
    words = [w for w in (light.get('time_of_day'), light.get('mood'), world.get('place') if world else None) if w]
    return ', '.join(words)


def register_commands(subparsers):
    parser = subparsers.add_parser('world', help="The video's world: kind, place, light, and who makes each element's shape and look")
    commands = parser.add_subparsers(dest='world_command', required=True)
    p = commands.add_parser('write', help="A delegated run's world (the agent's decision, with --agent-note)")
    p.add_argument('--project', required=True); p.add_argument('--body', required=True); p.add_argument('--agent-note', required=True)
    p.set_defaults(handler=lambda a: write(a.project, read_json(Path(a.body)), a.agent_note))
    p = commands.add_parser('link', help='Record the scene ids that build an element (parts or clay stand-ins)')
    p.add_argument('--project', required=True); p.add_argument('--element', required=True); p.add_argument('--ids', required=True)
    p.set_defaults(handler=lambda a: link(a.project, a.element, [i for i in a.ids.split(',') if i]))
    p = commands.add_parser('show', help='world.json and each element with its suggested owners')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: {'world': load(a.project), 'suggested': {e['id']: suggested_owner(e, load(a.project)['kind'])
                                                                              for e in (load(a.project) or {}).get('elements', [])}})
