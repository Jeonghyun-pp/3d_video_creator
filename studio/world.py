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

import json
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
            warnings.append(f"{element['id']}: owners {element['owner']} differ from the rule's {suggestion}, kept as written (its why: "
                            f"{element['why'][:120]!r}) - if the why does not say why these owners, rewrite it to")
    if body['kind'] != 'none' and not body.get('shell'):
        from .blender_ops.env_shell_core import suggest
        warnings.append(f"SHELL_MISSING: a world with a place declares no shell (world.shell; its place suggests "
                        f"{json.dumps(suggest(body))}) - the engine builds the ground to the horizon and the sky only when they are "
                        'declared, so the camera and the clay review see them; without it every shot must declare scene.shell or stands in a void')
    for element in body['elements']:
        if element['function'] in ('set', 'context') and element['owner'] == {'shape': 'blender', 'look': 'generative'} \
                and not element.get('blockout') and not element.get('ids'):
            warnings.append(f"BLOCKOUT_SIZE_MISSING: {element['id']} is a clay stand-in the model paints but has no blockout "
                            '(kind, size_m from the facts) and no built ids - the engine cannot stand it in at its real size')
    for element in body['elements']:
        if element.get('blockout') and not element.get('ids') and not stand_in_allowed(element):
            warnings.append(f"ELEMENT_NEEDS_BUILD: {element['id']} has a blockout but may not stand in as a clay box ("
                            f"{'the subject' if element['function'] == 'subject' else 'its look or a requirement holds it'}): its size "
                            'is a start; build it from a subject spec (subject init, photos first) or world link the ids that build it')
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
    """A delegated run's world: the agent's decision with its reason (never an approval), recorded where every layer is
    (decisions.write: decisions/world.json, then world.json) - lint errors keep it a draft."""
    from .decisions import write as write_layer
    if not load_project(project_dir(project)).get('delegation'):
        raise StudioError('INPUT_INVALID', 'world write is for delegated runs; on the ladder use decide propose --layer world')
    return write_layer(project, 'world', body, agent_note=agent_note)


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
    style = read_json(path / 'style.json') if (path / 'style.json').is_file() else {}
    changed = []
    for entry in load_project(path)['shots']:
        shot = load_shot(path, entry['shot_id'])
        elements = (shot.get('screen') or {}).get('elements')
        if not world or not elements or (shot.get('route') or {}).get('mode') == 'generative':
            continue   # (a cut a model makes whole keeps no regions: it has no Blender pass to keep from - studio/maker.py)
        screen = shot.get('screen') or {}   # a cut planned with what it holds in place keeps it (described: the rest)
        hybrid = (shot.get('route') or {}).get('mode') == 'hybrid'   # only a model that paints over the build fills words in
        placed = set(elements) - set(screen['described']) if 'described' in screen and hybrid else None
        plan = shot_plan(world, elements, shell_for(shot, world, style), placed)
        updated = deepcopy(shot)
        if plan['generate_only']:
            updated['screen']['generate_only'] = plan['generate_only']
        else:
            updated['screen'].pop('generate_only', None)
        if placed is not None:   # an element the world now holds (made exact, linked, ...) is built again: it leaves the words
            updated['screen']['described'] = plan['described']   # [] still marks a cut planned with words
        else:   # rerouted to Blender since: nobody would paint them, so every stand-in is built again
            updated['screen'].pop('described', None)
        if updated != shot:
            _write_shot(path, shot['shot_id'], updated, shot['revision'])
            changed.append(shot['shot_id'])
    return changed


HELD_REQUIREMENTS = ('exact', 'labelled', 'motion_bound', 'recurring')   # an element these hold is built wherever it shows


def stand_in_allowed(element):
    """Whether a clay box (its blockout, shot.scene.stand_ins) may stand for the element: only where a model paints over
    it (look generative, DEFAULT_OWNER's meaning) and nothing holds its shape - not the subject (it is built from its spec
    or linked ids: world.md), not a light, nothing a requirement holds. Why (submarine, 2026-10-21): the 83.5 m boat, its
    tanks and the stone stood in as 8-vertex boxes in every cut; every check passed and the clay reel was grey bars."""
    return (element['function'] not in ('subject', 'light') and (element.get('owner') or {}).get('look') == 'generative'
            and not set(element.get('requirements') or []) & set(HELD_REQUIREMENTS))


def element_ids(element):
    """The scene ids that build an element: those linked (world link), else its own id when a blockout may stand in for
    it (stand_in_allowed) - a stand-in (shot.scene.stand_ins) carries the element's id, and copies '<id>.n' answer to it
    as a group. A blockout of anything else is a size, not a build: [] until a spec or a link builds it."""
    return list(element.get('ids') or []) or ([element['id']] if element.get('blockout') and stand_in_allowed(element) else [])


def placed_ids(entry, facts=None, script=None):
    """The ids a cut holds in place: its focus (frame.focus), its show rows' parts and what its lines' claims are about.
    Why (the user, 2026-10-20: "나무 5개 정도 덩어리로 했다고 생성 모델이 나무 5개만 하면 별로 아니야?"): what is built is
    copied - five clay trees come back as five trees; what only sets the scene is better left to the model in words."""
    from .blender_ops.show_core import show_of
    from .explains import line_about
    ids = set((entry.get('frame') or {}).get('focus') or [])
    ids |= {p for r in show_of(entry) for p in r.get('parts') or []}
    if facts and script:
        ids |= set(line_about(facts, script, entry.get('line_ids') or []))
    return ids


def unbuilt_held(shot, world, inventory, fidelity=None):
    """[{code, text}] for the world elements a built shot shows (screen.elements, what it holds in place, what it points
    at) whose shape is Blender's and which no clay box may stand for (stand_in_allowed), judged on what the build made,
    never on the inputs (an author script, a kit or a character makes ids no input names):
    - the subject is judged by its measured shape: a fidelity report row of its spec (shot.subjects or placed by
      scene.instances), else SUBJECT_UNMEASURED - an object that only carries its id is a name, not the subject
      (2026-10-21: the submarine as a box, and a box named like it, would pass an id check). Never softened.
    - anything else must be in the inventory (an object with its id, a copy '<id>.n' or part '<id>/...', or a linked
      id), else ELEMENT_UNBUILT."""
    elements = {e['id']: e for e in (world or {}).get('elements', [])}
    made = {str(o.get('studio_id') or o.get('name') or '') for o in (inventory or {}).get('objects', [])}
    roots = {m.split('/')[0].split('.')[0] for m in made} | made
    measured = {r.get('placed_from') or r['subject_id'] for r in (fidelity or {}).get('subjects', [])}
    shown = set((shot.get('screen') or {}).get('elements') or []) | placed_ids(shot) | named_in(shot, set(elements))
    lines = []
    for ident in sorted(i for i in shown if i in elements):
        e = elements[ident]
        if (e.get('owner') or {}).get('shape') != 'blender' or stand_in_allowed(e):
            continue
        ids = {ident, *(e.get('ids') or [])}
        if e['function'] == 'subject':
            if not ids & measured:
                lines.append({'code': 'SUBJECT_UNMEASURED', 'text': f"{shot['shot_id']} shows {ident} (the subject) but no subject spec of "
                              'it was measured in this build: the subject is built from its spec (photos first) and measured, '
                              'never stood in by an object that carries its name'})
        elif not ids & roots:
            lines.append({'code': 'ELEMENT_UNBUILT', 'text': f"{shot['shot_id']} shows {ident} ({e['function']}) but the build made nothing "
                          'of that id: it is built (a subject spec or an author script), never left out or stood in by a clay box'})
    return lines


def named_in(shot, ids):
    """Which of these element ids the shot refers to as a thing the build must find (an id reference, itself or a copy
    '<id>.n' / part '<id>/...'): a subject built from its spec, screen.subject, a screen target or detail, a key part,
    a keep declared by hand, a label anchor, what the camera follows, a character's contact. A stand-in such a reference names is held - else the
    reference would match nothing. Read from the reference fields only: placement rows, shell names and prose are not
    references (a value equal to an id there says nothing)."""
    screen, camera = shot.get('screen') or {}, shot.get('camera') or {}
    rig, move = camera.get('rig') or {}, camera.get('move') or {}
    refs = [s['subject_id'] for s in shot.get('subjects') or []] + list(screen.get('subject') or [])
    refs += [t['of'] for t in screen.get('targets') or []] + [d['id'] for d in screen.get('details') or []]
    refs += [k['id'] for k in shot.get('key_parts') or []]
    refs += sorted(set(screen.get('keep') or []) - set(screen.get('keep_from_shotlist') or []))   # a keep declared by hand
    for label in shot.get('labels') or []:
        anchor = label.get('anchor')
        refs += list(anchor.values()) if isinstance(anchor, dict) else [anchor]
    refs += [rig.get('subject'), rig.get('look_target'), move.get('look_target'), (move.get('params') or {}).get('target')]
    refs += [c['target'] for row in (shot.get('scene') or {}).get('characters') or [] for c in row.get('contacts') or []]
    refs = [r for r in refs if isinstance(r, str)]
    return {i for i in ids for r in refs if r == i or r.startswith((i + '.', i + '/'))}


def described_in(world, shown_ids, placed):
    """The elements a cut shows but does not hold in place - built nowhere in it, left to the model in words. Only what
    the cut would build as a plain clay stand-in can be (a generated look, no linked or author-built ids, not the
    subject, not a light, nothing held by a requirement or a scale reference): anything else is in the scene whether or
    not it is named, and leaving it out of the regions would keep its Blender pixels. `placed` (placed_ids, plus what is
    built from its own spec): what the cut holds, itself or through a part of it."""
    held = lambda e: e['id'] in placed or any(p.startswith(e['id'] + '/') for p in placed)   # noqa: E731
    return [e['id'] for e in world['elements'] if e['id'] in set(shown_ids) and stand_in_allowed(e)
            and not e.get('ids') and not (e.get('blockout') or {}).get('scale_ref') and not held(e)]


def described_for(entry, shot, world, facts=None, script=None, path=None):
    """What a hybrid cut leaves to the model in words, from its shot-list entry and the shot it builds: what it shows
    that it does not hold (placed_ids), the shot does not point at (named_in) and the build does not make from its own
    subject spec (subjects/<id>/spec.json, added to the shot at materialize). One answer for the build (decisions
    materialize) and the sheet (maker.table)."""
    from pathlib import Path
    placed = placed_ids(entry, facts, script) | named_in(shot or {}, {e['id'] for e in world['elements']})
    if path is not None:
        placed |= {e for e in entry.get('elements') or [] if Path(path, 'subjects', e, 'spec.json').is_file()}
    return described_in(world, entry.get('elements') or [], placed), placed


def shot_plan(world, shown_ids, shell=None, placed=None):
    """For a shot showing these elements: the regions the model fills, the route mode and the role. A generated look
    becomes a region (its linked ids; a generated far background is '@background'). No generated look - blender; a
    Blender shape anywhere (the camera rides a Blender pass: clay stand-ins at least) - hybrid; nothing of Blender's -
    generative. The role is explain when a Blender-look element is on screen (exact, labelled), else mood.
    `shell` (shell_for: the engine's ground and sky this shot is built with): in a shot the model paints, the shell is
    the model's too ('@background' - keep_masks paints the shell with it), so no clay sky reaches the final pixels; a
    blender shot keeps it as built (the build says so: ENV_SHELL_CLAY)."""
    described = set(described_in(world, shown_ids, placed)) if placed is not None else set()
    elements = [e for e in world['elements'] if e['id'] in set(shown_ids) and e['function'] != 'light']
    # a described element is the model's in this cut, shape and look: nothing of it is built here (placed: what the cut holds)
    elements = [{**e, 'owner': {'shape': 'generative', 'look': 'generative'}} if e['id'] in described else e for e in elements]
    generated = [e for e in elements if e['owner']['look'] == 'generative']
    regions = []
    for e in generated:
        if e['owner']['shape'] == 'generative':
            regions.append('@background')
        if e['id'] not in described:   # a described element has no built ids in this cut: the background holds it
            regions += element_ids(e)
    blender_look = [e for e in elements if e['owner']['look'] == 'blender']
    blender_shape = [e for e in elements if e['owner']['shape'] == 'blender']
    mode = 'blender' if not generated else ('hybrid' if blender_shape else 'generative')
    if mode != 'blender' and shell_built(shell):
        regions.append('@background')
    unbuilt = [e['id'] for e in generated if e['owner']['shape'] == 'blender' and not element_ids(e)]
    return {'mode': mode, 'role': 'explain' if blender_look else 'mood', 'generate_only': list(dict.fromkeys(regions)), 'unbuilt': unbuilt,
            'described': sorted(described)}


def shell_built(shell):
    """Does the engine build any of this shell (a ground or a sky, not held under a lit look)?"""
    return bool(shell) and not shell.get('held') and (shell.get('ground') != 'none' or shell.get('sky') != 'none')


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
    from .common import CODE
    if not look_preset:
        return {}
    looks = read_json(CODE / 'studio/blender_ops/look_data/look_presets.json')['presets'].get(look_preset) or {}
    name = looks.get('lighting') if isinstance(looks, dict) else None
    return read_json(CODE / 'studio/blender_ops/look_data/lighting_presets.json')['presets'].get(name or '', {})


def shell_for(shot, world, style=None):
    """The environment shell a build makes for this shot (blender_ops/env_shell_core.resolve) - with `held` when the
    shot's look lights the scene itself: the look the build resolves (the shot's, else the style's) has a lighting
    preset, or the style's light_rig names one (env_shell_core.look_lighting, as blender_ops/look.py resolves it). Its
    preset owns the sky and the ground's light; the shell's far ground would enter the look's lit bounds, a frozen
    scene_roles row the user has not approved. None when none is declared."""
    from .common import CODE
    from .blender_ops import env_shell_core
    try:
        shell = env_shell_core.resolve(shot, world)
    except env_shell_core.ShellInvalid as error:
        raise StudioError(error.code, f"shot {(shot or {}).get('shot_id', '?')}: the environment shell's {error}",
                          recovery=f"use a known ground {sorted(env_shell_core.GROUNDS)} and sky {sorted(env_shell_core.SKIES)} "
                                   'in world.shell or scene.shell') from None
    if shell:
        looks = read_json(CODE / 'studio/blender_ops/look_data/look_presets.json')['presets']
        lighting = env_shell_core.look_lighting(shot, style, looks)
        if lighting:
            shell['held'] = (f'ENV_SHELL_HELD: the look lights the scene (lighting preset {lighting} owns the sky); the engine '
                             'shell is not built under it until scene_roles has a far-ground row (frozen code: the user\'s words)')
    return shell


# Things of a size everyone knows (metres): a shot opening a new place shows one so the viewer can tell how big the rest
# is (study set: half its shots hold a person or a vehicle). The suggestion names the closest the world already has.
SCALE_REFS = {'person': (1.75, 'a standing adult'), 'car': (4.5, 'a family car, length'), 'bus': (12.0, 'a city bus, length'),
              'container': (12.2, 'a 40 ft shipping container, length'), 'door': (2.1, 'a house door, height')}
SCALE_REF_MIN_PX = 24   # a reference shorter than this on screen (output pixels, its projected height) is not read as one:
                        # a standing person under 24 px of a 1920 frame is a speck (frame_probe_core.PROBE_HEIGHT_PX grid)


WIDE_HEIGHT = 0.5   # the subject at most half the frame tall: seen wide (reel_flow WIDE_HEIGHT_MAX, the study set's wide and medium)


def past_known(frame_report):
    """Is the subject larger than every size a viewer knows (SCALE_REFS) and seen wide - its focus box at most WIDE_HEIGHT
    of the frame tall at the median? Then nothing in it tells how big it is. Why (2026-10-20, canal): a 970 m lock read as
    a toy in every wide shot; only shots opening a place were asked for something of known size."""
    span = ((frame_report or {}).get('summary') or {}).get('subject_span_m')
    if not span or span <= max(metres for metres, _why in SCALE_REFS.values()):
        return False
    heights = sorted(b[3] - b[1] for b in (((f.get('shapes') or {}).get('subject') or {}).get('bbox') for f in (frame_report or {}).get('frames') or []) if b)
    return bool(heights) and heights[len(heights) // 2] <= WIDE_HEIGHT


def opens_place(shot):
    """Does the shot open a new place (its handover says so, or its role is establish)?"""
    return (shot.get('handover') or {}).get('kind') == 'new_place' or (shot.get('screen') or {}).get('role') == 'establish'


def scale_ref_lines(shot, frame_report, world):
    """SCALE_REF_MISSING (taste) for a shot opening a new place in a world with a place: no thing of known size (a library
    person, a stand-in whose blockout is a scale_ref) shows at SCALE_REF_MIN_PX - the part of it the camera sees
    (frame_probe._scale_refs). Departed on purpose with screen.pattern_departs item 'scale_ref_missing'. Every library
    person counts (characters.py tags them), so a project that places people in a new-place shot already passes; one
    that opens a place without anyone of known size now hears it - a taste warning, never a stop."""
    if not world or world.get('kind') == 'none' or not (opens_place(shot) or past_known(frame_report)):
        return []
    if 'scale_ref_missing' in [d.get('item') for d in (shot.get('screen') or {}).get('pattern_departs', [])]:
        return []
    refs = ((frame_report or {}).get('summary') or {}).get('scale_refs')
    if refs is None:   # measured before the probe read references: nothing is said
        return []
    best = max(refs.values(), default=0)
    if best >= SCALE_REF_MIN_PX:
        return []
    known = [e['id'] for e in world.get('elements', []) if e['function'] == 'agent' or (e.get('blockout') or {}).get('scale_ref')]
    sizes = ', '.join(f"{name} {metres:g} m" for name, (metres, _why) in SCALE_REFS.items())
    offer = (f"the world's {', '.join(known)}" if known else 'a person (scene.characters) or a stand-in with blockout.scale_ref') + \
        f' (a size everyone knows: {sizes})'
    what = 'opens a new place' if opens_place(shot) else \
        f"shows a subject {((frame_report.get('summary') or {}).get('subject_span_m') or 0):.0f} m long wide"
    return [f"SCALE_REF_MISSING: {shot['shot_id']} {what} but nothing of known size shows (largest "
            f"{round(best)} px, under {SCALE_REF_MIN_PX}) - add {offer} where the camera sees it, or depart (scale_ref_missing)"]


def context_void(frame_report, shot, world, usual_p90, style=None):
    """CONTEXT_VOID (taste): a world with a place whose shot shows mostly nothing - the empty share of the frame (no
    object renders there) above what study reels show at their 90th percentile, with no generated background to fill it.
    The first frame counts too: the floor-noise opening was a box in the dark."""
    if not world or world.get('kind') == 'none' or usual_p90 is None:
        return []
    if '@background' in ((shot.get('screen') or {}).get('generate_only') or []):
        return []
    shell = shell_for(shot, world, style)
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
