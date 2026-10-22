"""A shot's motion chosen as a motion: the director names a recipe (a feel, library/camera_recipes.json) with what it
reveals, when its key moment comes and why (shot list direction.move); the engine makes the camera move of that recipe
from the shot's own scene - its subject, the parts it shows at the key moment - with no picture in between.

Why (2026-10-22, submarine r2): the first and last picture of each shot set its camera, and the motion between them was
whatever joined two stills - nobody designed it, and seven of twelve cuts came out a thin side-on strip. The user: "그림은
카메라가 고정인 상태만 그릴수 있는데 우리는 카메라가 움직일수 있는 괴리". A recipe's 'make' is data: a new recipe is a
library row, never a branch here (tests/test_recipe_moves.py reads the code for recipe ids).
"""
from __future__ import annotations

from .common import StudioError

TOKENS = ('$subject', '$key')            # what a recipe's params may name: the shot's subject, its key parts
NEEDS = ('key', 'key_opens')             # what a recipe may need of a shot (make_rule in the library)


def book():
    from .camera_taste import recipes
    return {r['id']: r for r in recipes()}


def subject_of(shot):
    """The shot's subject id: what its frame is about (frame.focus), else what its camera is aimed at."""
    focus = (shot.get('frame') or {}).get('focus') or []
    if focus:
        return focus[0]
    params = ((shot.get('camera') or {}).get('move') or {}).get('params') or {}
    return params.get('target') or (shot.get('screen') or {}).get('subject')


def key_of(shot, key_at):
    """(ids, row): the parts the shot shows at its key moment - the show row held there (its focus, else its parts) - or
    ([], None) when no row is held yet."""
    from .blender_ops.show_core import held_at, show_of
    row = held_at(show_of(shot), float(key_at))
    if row is None:
        return [], None
    return list(row.get('focus') or row.get('parts') or []), row


def check_intent(intent, recipes=None):
    """Errors of a direction.move as written: a known recipe, and chosen values that are the recipe's own and inside its
    ranges (the range is the feel; outside it the move is another recipe)."""
    recipes = recipes if recipes is not None else book()
    rid = (intent or {}).get('recipe')
    if rid not in recipes:
        return [f"recipe {rid!r} is not one of {sorted(recipes)} (library/camera_recipes.json)"]
    make = recipes[rid].get('make')
    settings = intent.get('settings') or {}
    if settings and not make:
        return [f"recipe {rid} has no make (it is built from pictures or by hand): settings {sorted(settings)} choose nothing"]
    out = []
    for name, value in settings.items():
        if name not in (make or {}).get('choose', {}):
            out.append(f"{rid}: {name} is not a value the recipe chooses ({sorted(make['choose'])})")
            continue
        low, high, _default = make['choose'][name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            out.append(f"{rid}: {name} {value!r} is outside {low}..{high} - past it the move is another feel; choose another recipe")
    return out


def recipe_move(shot, intent=None, recipes=None):
    """{move, chosen}: the camera move the shot's recipe makes from its scene. `intent` defaults to shot.direction.move
    (a take passes another recipe). Every chosen value says who chose it: the director (intent.settings) or the
    recipe's default - never silently. Raises RECIPE_UNMADE (no make: pictures or hand keys make it), RECIPE_NEEDS (the
    shot lacks what the recipe moves to) and INPUT_INVALID (a value outside the recipe's range)."""
    recipes = recipes if recipes is not None else book()
    intent = intent if intent is not None else (shot.get('direction') or {}).get('move')
    sid = shot.get('shot_id')
    if not intent:
        raise StudioError('INPUT_INVALID', f'{sid}: no direction.move - the shot list says how the shot moves (recipe, reveals, key_at, why)')
    errors = check_intent(intent, recipes)
    if errors:
        raise StudioError('INPUT_INVALID', f'{sid}: ' + '; '.join(errors))
    rid = intent['recipe']
    make = recipes[rid].get('make')
    if not make:
        raise StudioError('RECIPE_UNMADE', f'{sid}: recipe {rid} has no make - its motion comes from frame pictures (frame_to_frame) '
                          'or hand keys', recovery='choose a recipe with a make for an engine-made take, or picture the shot')
    subject = subject_of(shot)
    if not subject:
        raise StudioError('RECIPE_NEEDS', f'{sid}: {rid} moves about the shot\'s subject and the shot names none (frame.focus)')
    key, row = key_of(shot, intent.get('key_at', 0.5))
    key = [k for k in key if k != subject]
    unmet = [n for n in make.get('needs') or [] if not _met(n, key, row)]
    if unmet:
        raise StudioError('RECIPE_NEEDS', f"{sid}: {rid} needs {unmet} at its key moment t={intent.get('key_at', 0.5)} "
                          f"(show row held there: {row and {k: row.get(k) for k in ('at', 'parts', 'how')}})",
                          recovery='move key_at to the moment that shows it, add the show row, or choose another recipe')
    values = {'$subject': subject, '$key': key[0] if len(key) == 1 else key}
    params = {name: values[v] if isinstance(v, str) and v in TOKENS else v for name, v in make['params'].items()}
    settings = intent.get('settings') or {}
    chosen = {}
    for name, (_low, _high, default) in make['choose'].items():
        chosen[name] = {'value': settings.get(name, default), 'by': 'director' if name in settings else 'default'}
    move = {'type': make['move'], 'params': {**params, **{k: c['value'] for k, c in chosen.items() if k != 'lens_mm'}}}
    if 'lens_mm' in chosen:
        move['lens_mm'] = chosen['lens_mm']['value']
    move['recipe'] = {'id': rid, 'why': intent.get('why'), 'chosen': {k: c['by'] for k, c in chosen.items()}}
    return {'move': move, 'chosen': chosen}


def _met(need, key, row):
    if need not in NEEDS:
        raise StudioError('INPUT_INVALID', f'recipe need {need!r} is not one of {NEEDS} (library make_rule)')
    if need == 'key':
        return bool(key)
    from .blender_ops.show_core import opens
    return bool(row) and opens(row.get('how'))
