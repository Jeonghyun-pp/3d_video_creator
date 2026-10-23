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
    ranges (the range is the feel; outside it the move is another recipe). A recipe the engine has no make for is
    written as moments (MOVE_UNMADE otherwise: 2026-10-23 r3 s10 named 'against', no take of it could be built and the
    takes went on without it); moments carry their own framing (check_moments)."""
    recipes = recipes if recipes is not None else book()
    rid = (intent or {}).get('recipe')
    if rid not in recipes:
        return [f"recipe {rid!r} is not one of {sorted(recipes)} (library/camera_recipes.json)"]
    make = recipes[rid].get('make')
    settings = intent.get('settings') or {}
    if intent.get('moments'):
        return ([f"{rid}: moments carry their own size, view and lens - settings {sorted(settings)} choose nothing"] if settings else []) \
            + check_moments(intent['moments'])
    if not make:
        made = sorted(r for r, row in recipes.items() if row.get('make'))
        return [f"MOVE_UNMADE: recipe {rid} has no engine make - write its motion as moments (direction.move.moments: the "
                f"framings the camera passes through) or choose a recipe the engine makes ({', '.join(made)})"]
    out = []
    unsaid = [n for n in FRAMING_SAID if n in make.get('choose', {}) and n not in settings]
    if unsaid:   # the framing is the director's: no recipe default stands in for it (2026-10-23: the defaults framed the reel)
        out.append(f"MOVE_FRAMING_UNSAID: recipe {rid} leaves {', '.join(unsaid)} to nobody - choose them in settings (the sheet "
                   "says each azimuth in the subject's terms and the reference's size) or write the motion as moments")
    for name, value in settings.items():
        if name not in (make or {}).get('choose', {}):
            out.append(f"{rid}: {name} is not a value the recipe chooses ({sorted(make['choose'])})")
            continue
        low, high, _default = make['choose'][name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            out.append(f"{rid}: {name} {value!r} is outside {low}..{high} - past it the move is another feel; choose another recipe")
    return out


MOMENTS_MIN = 2   # a motion of moments passes through two framings at least (the same framing twice: the camera rests)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_moments(moments):
    """Errors of direction.move.moments: [{at, size, view: {elevation_deg, azimuth_deg}, lens_mm?, parts?, cut?, why}]
    in time order, at least MOMENTS_MIN."""
    if not isinstance(moments, list) or len(moments) < MOMENTS_MIN:
        return [f'moments: at least {MOMENTS_MIN} framings the camera passes through (the same one twice rests it)']
    out, last = [], -1.0
    for n, m in enumerate(moments):
        where = f'moments[{n}]'
        at = m.get('at')
        if not _number(at) or not 0.0 <= at <= 1.0 or at <= last:
            out.append(f'{where}: at {at!r} must be a share of the shot after the moment before it')
        else:
            last = at
        if m.get('hold'):
            if n == 0:
                out.append(f'{where}: the first moment has no camera before it to hold')
            if len(str(m.get('why') or '').strip()) < 3:
                out.append(f'{where}: why this framing here')
            continue   # it keeps the moment before's pose: its size and view are that moment's
        if not _number(m.get('size')) or m['size'] <= 0:
            out.append(f"{where}: size {m.get('size')!r} is how tall what it frames stands in the frame (> 0)")
        view = m.get('view') or {}
        if not _number(view.get('elevation_deg')) or not -89 <= view['elevation_deg'] <= 89 or not _number(view.get('azimuth_deg')):
            out.append(f'{where}: view needs elevation_deg (-89..89) and azimuth_deg')
        if 'lens_mm' in m and (not _number(m['lens_mm']) or not 8 <= m['lens_mm'] <= 400):
            out.append(f"{where}: lens_mm {m['lens_mm']!r} is outside 8..400")
        if not all(isinstance(p, str) and p for p in m.get('parts') or []):
            out.append(f'{where}: parts are the ids this moment must show')
        if len(str(m.get('why') or '').strip()) < 3:
            out.append(f'{where}: why this framing here')
    return out


def held_moments(moments):
    """The moments with every held one (hold: true) carrying the framing of the moment before it - the camera it keeps."""
    out, before = [], None
    for m in moments:
        if m.get('hold') and before is not None:
            m = {**before, 'at': m['at'], 'why': m['why'], 'hold': True}
        out.append(m)
        before = {k: v for k, v in m.items() if k != 'hold'}
    return out


FRAMING_SAID = ('height_share', 'azimuth_deg')   # what every move says itself: how big and from which side (MOVE_FRAMING_UNSAID)
FRAMING_VALUES = ('height_share', 'elevation_deg', 'azimuth_deg')   # a take's size and angle: the director's, or said as a
                                                                    # recipe default (storyboard TAKE_FRAMING_DEFAULT)
MOMENT_LENS_MM = 24.0   # a moment that names no lens: the recipes' common middle


def framing_of(intent, recipes=None):
    """(values, focus): the director's framing of a move as recipe values - its settings, or a motion of moments' framing
    at its key moment (the moment nearest key_at) - and what its size measures (the token its recipe frames, '$subject'
    for moments: a moment sizes the whole subject). What a compared take inherits (inherit)."""
    recipes = recipes if recipes is not None else book()
    if intent.get('moments'):
        key = float(intent.get('key_at', 0.5))
        m = min(held_moments(intent['moments']), key=lambda row: abs(float(row['at']) - key))
        return ({'height_share': m['size'], 'elevation_deg': m['view']['elevation_deg'], 'azimuth_deg': m['view']['azimuth_deg'],
                 'lens_mm': m.get('lens_mm', MOMENT_LENS_MM)}, '$subject')
    make = (recipes.get(intent.get('recipe')) or {}).get('make') or {}
    return dict(intent.get('settings') or {}), make.get('focus', '$subject')


def inherit(framing, rid, recipes=None):
    """(settings, outside): the director's framing (framing_of) as another recipe's chosen values - every value that
    recipe chooses and holds inside its range is kept; `outside` names those past its range ({name: (value, low, high)}).
    A size is kept only where it measures the same thing (a whole subject's size is no size for a key part: left to the
    recipe and said). A compared take varies the motion, not the framing the director chose (2026-10-23, r3: compared
    takes took the recipe's defaults and ten of fourteen picked cuts were framed by nobody)."""
    recipes = recipes if recipes is not None else book()
    values, focus = framing
    make = recipes[rid].get('make') or {}
    choose = make.get('choose') or {}
    same = make.get('focus', '$subject') == focus
    kept, outside = {}, {}
    for name, value in values.items():
        if name not in choose or (name in FOCUS_VALUES and not same):
            continue
        low, high, _default = choose[name]
        if low <= value <= high:
            kept[name] = value
        else:
            outside[name] = (value, low, high)
    return kept, outside


FOCUS_VALUES = ('height_share',)   # chosen values that size the move's framing pose (move.focus), not move params


def pattern_size(project, shot):
    """The reference's median height_share for the shot's role (library/patterns), or None without a library."""
    from .patterns import library_name, load, section
    try:
        library = load(library_name(project))
    except StudioError:
        return None
    item = section(library, (shot.get('screen') or {}).get('role') or 'all')[1]['items'].get('height_share') or {}
    return item.get('median')


SIDE_UNKNOWN_DEG = 90.0   # a 'side' default for a subject whose spec gives no horizontal length: the world's +X side, said


def _default(value, low, high, pattern, side=None):
    """A choose row's default: a number, 'pattern' - the reference's size for the role, kept inside the range (the
    recipe's middle without a library) - or 'side', the subject's broad side (views.side_azimuth; SIDE_UNKNOWN_DEG when
    its spec gives none): said as the default either way."""
    if value == 'pattern':
        value = pattern if pattern is not None else (low + high) / 2
    elif value == 'side':
        value = side if side is not None else SIDE_UNKNOWN_DEG
    return round(min(high, max(low, float(value))), 4)


def recipe_move(shot, intent=None, recipes=None, pattern=None, centre=None, side=None):
    """{move, chosen}: the camera move the shot's recipe makes from its scene. `intent` defaults to shot.direction.move
    (a take passes another recipe). Every chosen value says who chose it: the director (intent.settings) or the
    recipe's default - never silently. Raises RECIPE_UNMADE (no make: pictures or hand keys make it), RECIPE_NEEDS (the
    shot lacks what the recipe moves to) and INPUT_INVALID (a value outside the recipe's range). `pattern`: the
    reference's height_share for the shot's role (pattern_size), the default of a 'pattern' size."""
    recipes = recipes if recipes is not None else book()
    intent = intent if intent is not None else (shot.get('direction') or {}).get('move')
    sid = shot.get('shot_id')
    if not intent:
        raise StudioError('INPUT_INVALID', f'{sid}: no direction.move - the shot list says how the shot moves (recipe, reveals, key_at, why)')
    rid = (intent or {}).get('recipe')
    if rid in recipes and not recipes[rid].get('make') and not intent.get('moments'):
        raise StudioError('RECIPE_UNMADE', f'{sid}: recipe {rid} has no make - its motion is written as moments, comes from frame '
                          'pictures (frame_to_frame) or hand keys', recovery='write direction.move.moments, or choose a recipe with a make')
    errors = check_intent(intent, recipes)
    if errors:
        raise StudioError('INPUT_INVALID', f'{sid}: ' + '; '.join(errors))
    if intent.get('moments'):
        return moments_move(shot, intent, centre)
    make = recipes[rid].get('make')
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
    for name, (low, high, default) in make['choose'].items():
        chosen[name] = {'value': settings[name] if name in settings else _default(default, low, high, pattern, side),
                        'by': 'director' if name in settings else 'default'}
    move = {'type': make['move'], 'params': {**params, **{k: c['value'] for k, c in chosen.items()
                                                         if k != 'lens_mm' and k not in FOCUS_VALUES}}}
    if 'lens_mm' in chosen:
        move['lens_mm'] = chosen['lens_mm']['value']
    if make.get('focus'):   # where the camera stands is solved on the scene so this stands as tall as chosen (camera_frame)
        framed = values[make['focus']]
        move['focus'] = {'height_share': chosen['height_share']['value'], 'parts': framed if isinstance(framed, list) else [framed],
                         **({'cut': True} if intent.get('cut') else {})}
    move['recipe'] = {'id': rid, 'why': intent.get('why'), 'chosen': {k: c['by'] for k, c in chosen.items()}}
    return {'move': move, 'chosen': chosen}


def moments_move(shot, intent, centre=None):
    """{move, chosen}: a motion of moments as the camera's frame_to_frame move - each moment a framing the camera passes
    through (frames: at, size, view, lens, must-see parts; a cut moment runs the framed subject off the top and bottom),
    joined by the engine's path (params.path, follow). The director chose every framing. Why (2026-10-23): the user -
    "카메라 움직임이 직선적이기만 한것" - one recipe made one straight move per shot; approach, rest and turn round
    were not expressible. `centre`: where a framed subject's middle stands (the frame free of platform UI)."""
    subject = subject_of(shot)
    if not subject:
        raise StudioError('RECIPE_NEEDS', f"{shot.get('shot_id')}: moments frame the shot's subject and the shot names none (frame.focus)")
    middle = list(centre or (0.5, 0.5))
    from .blender_ops.show_core import held_at, opens, show_of
    rows = show_of(shot)

    def opening(at):   # the show row held at this moment, when it opens the subject: a framed moment cuts its own opening
        row = held_at(rows, at)   # (camera_moves_core.show_moments reads frames[].show; r3 s11: a tank behind the hull)
        return {'show': {k: row[k] for k in ('at', 'parts', 'how', 'why') if k in row}} if row and opens(row.get('how')) else {}
    frames = []
    for m in held_moments(intent['moments']):
        frames.append({'at': float(m['at']), 'height_share': float(m['size']), 'centre': list(m.get('centre') or middle),
                       'view': {'elevation_deg': float(m['view']['elevation_deg']), 'azimuth_deg': float(m['view']['azimuth_deg'])},
                       'lens_mm': float(m.get('lens_mm', MOMENT_LENS_MM)), 'must_see': list(m.get('parts') or []),
                       'cut_edges': ['top', 'bottom'] if m.get('cut') else [], **opening(float(m['at'])),
                       **({'hold': True} if m.get('hold') else {})})
    params = {'target': subject, 'path': intent.get('path', 'auto'), 'follow': float(intent.get('follow', 0.5))}
    chosen = {name: {'value': None, 'by': 'director'} for name in FRAMING_VALUES}
    move = {'type': 'frame_to_frame', 'params': params, 'frames': frames,
            'recipe': {'id': intent['recipe'], 'why': intent.get('why'), 'chosen': {k: c['by'] for k, c in chosen.items()}}}
    return {'move': move, 'chosen': chosen}


def camera_with(camera, move):
    """The shot's camera carrying a made move, as the schema has a moving camera: a rig the move compiles, no hand keys
    and no older rig. Why (2026-10-22, r3): a fresh shot's camera ('authored', keys) given a recipe's move was refused by
    every take - shots built from pictures had always been given a rig camera first."""
    return {**{k: v for k, v in (camera or {}).items() if k != 'rig'}, 'movement': 'rig', 'keys': [], 'move': move}


def _met(need, key, row):
    if need not in NEEDS:
        raise StudioError('INPUT_INVALID', f'recipe need {need!r} is not one of {NEEDS} (library make_rule)')
    if need == 'key':
        return bool(key)
    from .blender_ops.show_core import opens
    return bool(row) and opens(row.get('how'))
