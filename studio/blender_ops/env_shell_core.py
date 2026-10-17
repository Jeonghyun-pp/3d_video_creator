"""Environment shell (pure): the ground or sea reaching the horizon and the sky over it, built by the engine for every
shot whose world has a place - so the horizon, the ground line and how much sky a frame holds are decided in 3D, where
the camera, the frame checks and the clay review see them, and only their look is left to the generative model.

Why (2026-10-17): a 25-shot rocket reel read as a grey floor in a grey void. The world layer said "Blender shape,
generated look" for its sea and sky, but nothing in the engine built them: the far background was handed whole to the
model, so the camera could not keep the horizon off the subject, every shot's sea could come out different, and the
clay cut could not tell an empty frame from one waiting to be filled. The user's rule: what decides the composition
is the engine's (even as a blockout); texture and detail are the model's.

A shell is declared, never guessed: world.shell (every shot) or shot.scene.shell (one shot; wins). Kinds and colours are
tables with reasons; a new kind of place is a row. Shapes are built by env_shell.py; this file holds the rules.
"""
from __future__ import annotations

import math

# ground kind -> (clay colour (linear RGB), why). The colours only separate kinds in the clay review and the id passes;
# the look is the model's. Picked apart in value as well as hue so a grey-scale view still reads ground from sky.
GROUNDS = {
    'land':  ((0.32, 0.29, 0.24), 'open ground: fields, desert, a pad apron'),
    'sea':   ((0.10, 0.20, 0.30), 'open water to the horizon: the sea a ship or a platform floats on'),
    'floor': ((0.42, 0.42, 0.40), 'a large built floor: a hangar, a hall, a factory'),
    'none':  (None, 'no ground: space, mid-air, a subject on its own'),
}
# sky kind -> (zenith, horizon) clay colours (linear RGB), why. The horizon band is lighter than the zenith, as a real
# sky is (haze), so the horizon line reads in clay; night and space are dark at both ends.
SKIES = {
    'day':   ((0.30, 0.45, 0.70), (0.70, 0.78, 0.86), 'daylight: blue overhead, pale haze at the horizon'),
    'dusk':  ((0.18, 0.20, 0.38), (0.85, 0.55, 0.35), 'low sun: dark overhead, warm at the horizon'),
    'night': ((0.02, 0.03, 0.06), (0.06, 0.07, 0.12), 'night: near black, a faint glow at the horizon'),
    'space': ((0.0, 0.0, 0.0), (0.01, 0.01, 0.02), 'above the air: black'),
    'none':  (None, None, 'no sky: an interior, a stage tone'),
}
# (the time_light words a world uses) -> the sky kind, when world.shell names no sky. Why: the light is already decided
# with the user in world.time_light; the sky must agree with it.
SKY_BY_TIME = (('night', 'night'), ('dusk', 'dusk'), ('sunset', 'dusk'), ('dawn', 'dusk'), ('evening', 'dusk'),
               ('golden hour', 'dusk'), ('sunrise', 'dusk'), ('황혼', 'dusk'), ('노을', 'dusk'), ('밤', 'night'),
               ('space', 'space'), ('orbit', 'space'))

EARTH_R_M = 6_371_000.0   # the flat disc's edge sits where a real horizon is seen: sqrt(2h/R) under eye level (0.1 deg at
                          # 10 m, 0.8 deg at 600 m). On a flat disc that is the distance h / tan(sqrt(2h/R)) ~ sqrt(Rh/2);
                          # a nearer edge reads as a horizon too low in the frame (2026-10-18 review: a 20 h cap put it
                          # 2.9 deg down, 12 % of a 24 deg frame), a farther one rides up to eye level
RADIUS_MIN_M = 400.0      # a disc never smaller than this (a camera at the ground still sees its own horizon ~4.5 km off)
CLIP_SHARE = 0.9          # the shell stays inside the camera's far clip (a shell clipped by it shows a hard edge)
GROUND_MAX_ALT_M = 20_000.0   # above this a flat disc no longer reads as ground (the earth is a curved limb): the shell
                              # leaves the ground out and says so; the author's globe or the generated background owns it
AUTHOR_GROUND_SPAN_M = 300.0   # a mesh (or one object's instances together) the author made at least this wide both ways,
AUTHOR_GROUND_FLAT = 0.05      # this flat (height <= share of its span), level with the declared ground (its top within
AUTHOR_GROUND_LEVEL_M = 5.0    # this of ground_z) is the author's own ground - a sea box, a desert plane, tiled water: the
                               # shell's ground is not built under it. World-space boxes: a tall card or a cloud deck high up
                               # is no ground (2026-10-18 review: object-axis sizes let a 300 m vertical card pass)
# The disc goes under every face the author made level with it (a deck at sea level, a pad): far enough that the depth
# buffer keeps them apart from the farthest camera. A step of a 24-bit depth buffer at distance d is about
# d^2 / (near * 2^24); DEPTH_SAFETY steps keep two surfaces from flickering. Never less than GROUND_CLEAR_MIN_M, never
# more than GROUND_CLEAR_MAX_M (a deeper gap would show as a step under a hull or a pad edge in a close shot - past it the
# report says the near clip is too small for the distance).
DEPTH_BITS = 24
DEPTH_SAFETY = 4.0
GROUND_CLEAR_MIN_M = 0.05
GROUND_CLEAR_MAX_M = 2.0
SKY_RINGS = (-0.01, 0.04, 0.10, 0.18, 0.30, 0.48)   # sky dome bands by height over the ground (share of its radius): below
                                  # the horizon the ground's own colour (far ground seen past a finite author ground reads
                                  # as more of it - 2026-10-18, a mixed haze band read as brown stripes over the sea), then
                                  # steps from the horizon colour to the zenith, fine near the horizon where the eye looks
DOME_MARGIN = 1.25       # the dome is at least this many times the farthest camera's distance from its centre: a camera
                         # outside the dome sees its outside, not the sky (2026-10-18, a camera 899 m up in a 900 m dome)
WIDE_NAMES = {'land', 'sea', 'floor'}


def sky_for(world):
    """The sky kind world.time_light implies (day when it says nothing)."""
    light = (world or {}).get('time_light') or {}
    words = ' '.join(str(light.get(k) or '') for k in ('time_of_day', 'mood')).lower()
    for word, kind in SKY_BY_TIME:
        if word in words:
            return kind
    return 'day'


# words in world.place -> the ground a shell starts from (a suggestion the agent edits, never applied unasked): the place
# is already settled from the beat pictures, so the shell need not be asked for again
GROUND_BY_PLACE = (('sea', 'sea'), ('ocean', 'sea'), ('바다', 'sea'), ('해상', 'sea'), ('ship', 'sea'), ('배 위', 'sea'),
                   ('space', 'none'), ('orbit', 'none'), ('우주', 'none'),
                   ('hall', 'floor'), ('hangar', 'floor'), ('factory', 'floor'), ('공장', 'floor'), ('격납고', 'floor'),
                   ('desert', 'land'), ('field', 'land'), ('pad', 'land'), ('사막', 'land'), ('들판', 'land'), ('발사대', 'land'))


def suggest(world):
    """The shell a world's own words point to: {ground, sky} (ground 'land' when the place names nothing known)."""
    place = str((world or {}).get('place') or '').lower()
    ground = next((g for word, g in GROUND_BY_PLACE if word in place), 'land')
    sky = 'space' if ground == 'none' and any(w in place for w in ('space', 'orbit', '우주')) else sky_for(world)
    return {'ground': ground, 'sky': sky}


def resolve(shot, world):
    """The shell a shot is built with: shot.scene.shell over world.shell, the sky filled from the world's light when the
    shell names no sky; None when neither declares one or the world has no place (kind none)."""
    world = world or {}
    if world.get('kind') == 'none':
        return None
    declared = ((shot or {}).get('scene') or {}).get('shell') or world.get('shell')
    if not declared:
        return None
    out = {'ground': declared.get('ground', 'none'), 'sky': declared.get('sky') or sky_for(world)}
    for key in ('ground_z_m', 'why'):
        if declared.get(key) is not None:
            out[key] = declared[key]
    if out['ground'] not in GROUNDS:
        raise ValueError(f"SHELL_INVALID: ground {out['ground']!r} (known: {sorted(GROUNDS)})")
    if out['sky'] not in SKIES:
        raise ValueError(f"SHELL_INVALID: sky {out['sky']!r} (known: {sorted(SKIES)})")
    return out


def horizon_m(height_m):
    """How far a flat ground must reach from a camera `height_m` up (a metre at least) for its edge to show where the
    curved earth's horizon shows: at the true dip sqrt(2h/R) under eye level."""
    h = max(float(height_m), 1.0)
    return h / math.tan(math.sqrt(2.0 * h / EARTH_R_M))


def disc_radius(top_height_m, clip_end_m, reach_m=0.0):
    """(radius, why): the ground disc for cameras at most `top_height_m` over the ground, as far as `reach_m` across it
    from the disc's centre, with far clip `clip_end_m`: every camera sees ground out to its own horizon."""
    radius, why = max(RADIUS_MIN_M, float(reach_m) + horizon_m(top_height_m)), 'horizon'
    cap = CLIP_SHARE * float(clip_end_m)
    if radius > cap:
        radius, why = cap, 'clip'   # the far clip is nearer than the horizon: the edge shows above the true horizon
    return radius, why


def ground_clear(near_m, far_m):
    """(drop, capped): how far the disc goes under an author face level with it, for a camera with near clip `near_m`
    seeing it from as far as `far_m`."""
    step = float(far_m) ** 2 / (max(float(near_m), 1e-6) * 2 ** DEPTH_BITS)
    want = max(GROUND_CLEAR_MIN_M, DEPTH_SAFETY * step)
    return min(want, GROUND_CLEAR_MAX_M), want > GROUND_CLEAR_MAX_M


def ground_height(declared_z, author_levels, clear=GROUND_CLEAR_MIN_M):
    """The disc's height: the declared one, moved `clear` under any author face level (box tops and bottoms) within
    `clear` of it - repeatedly, so a stack of levels near it is cleared too."""
    z = float(declared_z)
    levels = sorted({round(float(v), 4) for v in author_levels}, reverse=True)
    for level in levels:
        if abs(level - z) < clear:
            z = level - clear
    return z


def horizon_dip_deg(top_height_m, radius_m):
    """How far under eye level the disc's edge shows from the highest camera (deg)."""
    return math.degrees(math.atan2(max(0.0, top_height_m), max(radius_m, 1e-6)))


def is_author_ground(lo, hi, ground_z=0.0):
    """A world-space box (`lo`, `hi` corners, metres) the author made wide, flat and level enough with the declared
    ground to be the ground."""
    x, y, z = (float(b) - float(a) for a, b in zip(lo, hi))
    span = min(x, y)
    return (span >= AUTHOR_GROUND_SPAN_M and z <= AUTHOR_GROUND_FLAT * span
            and abs(float(hi[2]) - float(ground_z)) <= AUTHOR_GROUND_LEVEL_M)


def band_of(height_share):
    """Index of the sky band a dome face belongs to (0 below the horizon .. len(SKY_RINGS) the zenith)."""
    return sum(1 for edge in SKY_RINGS if height_share >= edge)


def band_colours(sky, ground):
    """Clay colours of the dome bands, below the horizon to the zenith: the ground's own colour (the horizon colour when
    there is no ground), then len(SKY_RINGS) steps from the horizon colour to the zenith's."""
    zenith, horizon, _ = SKIES[sky]
    if zenith is None:
        return None
    below = GROUNDS.get(ground, (None,))[0] or horizon
    mix = lambda a, b, t: tuple(round(x + (y - x) * t, 4) for x, y in zip(a, b))  # noqa: E731
    steps = len(SKY_RINGS)
    return [tuple(below)] + [mix(horizon, zenith, i / max(1, steps - 1)) for i in range(steps)]


def dome_radius(ground_radius, farthest_camera_m):
    """(radius, clip_end the camera needs): the dome holds the ground disc and every camera with DOME_MARGIN to spare;
    the camera's far clip must reach past it."""
    radius = max(float(ground_radius), DOME_MARGIN * float(farthest_camera_m))
    return radius, radius / CLIP_SHARE


def look_lighting(shot, style, look_presets):
    """The lighting preset name a build lights the shot with - as blender_ops/look.py resolves it: the shot's look over
    the style's (flat_stylized when neither names one), then the look's lighting, which the style's light_rig.preset
    overrides. None when the look lights nothing."""
    style = style or {}
    look = ((shot or {}).get('render') or {}).get('look_preset') or (style.get('look') or {}).get('preset') or 'flat_stylized'
    spec = (look_presets or {}).get(look) or {}
    if not (isinstance(spec, dict) and spec.get('lighting')):
        return None
    return (style.get('light_rig') or {}).get('preset') or spec['lighting']
