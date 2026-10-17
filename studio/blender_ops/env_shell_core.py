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
               ('space', 'space'), ('orbit', 'space'))

HORIZON_K = 20.0          # the ground disc reaches at least this many times the highest camera's height: its edge then
                          # sits atan(1/20) = 2.9 deg under eye level, which reads as the horizon (a real horizon dips
                          # sqrt(2h/R): 0.1 deg at 10 m, 0.8 deg at 600 m)
EARTH_R_M = 6_371_000.0   # beyond the true horizon distance sqrt(2 R h) nothing of a flat ground would be seen anyway
RADIUS_MIN_M = 400.0      # a disc never smaller than this: a pedestrian's horizon is ~4.5 km, so a small disc's edge shows
CLIP_SHARE = 0.9          # the shell stays inside the camera's far clip (a shell clipped by it shows a hard edge)
GROUND_MAX_ALT_M = 20_000.0   # above this a flat disc no longer reads as ground (the earth is a curved limb): the shell
                              # leaves the ground out and says so; the author's globe or the generated background owns it
AUTHOR_GROUND_SPAN_M = 300.0   # a mesh the author made at least this wide both ways and this flat is the author's own
AUTHOR_GROUND_FLAT = 0.05      # ground (a sea box, a desert plane): the shell's ground is not built under it
SKY_RINGS = (-0.15, 0.08, 0.35)   # sky dome bands by height over the ground, as a share of its radius: below the
                                  # horizon (ground-coloured haze), the horizon band, the middle, the zenith
WIDE_NAMES = {'land', 'sea', 'floor'}


def sky_for(world):
    """The sky kind world.time_light implies (day when it says nothing)."""
    light = (world or {}).get('time_light') or {}
    words = ' '.join(str(light.get(k) or '') for k in ('time_of_day', 'mood')).lower()
    for word, kind in SKY_BY_TIME:
        if word in words:
            return kind
    return 'day'


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


def disc_radius(top_height_m, clip_end_m):
    """(radius, why): the ground disc for cameras at most `top_height_m` over the ground with far clip `clip_end_m`."""
    h = max(0.0, float(top_height_m))
    wanted = max(RADIUS_MIN_M, HORIZON_K * h)
    true_horizon = math.sqrt(2.0 * EARTH_R_M * max(h, 1.0))
    radius, why = min(wanted, max(RADIUS_MIN_M, true_horizon)), 'horizon'
    cap = CLIP_SHARE * float(clip_end_m)
    if radius > cap:
        radius, why = cap, 'clip'   # the far clip is nearer than the horizon: the edge shows above the true horizon
    return radius, why


def horizon_dip_deg(top_height_m, radius_m):
    """How far under eye level the disc's edge shows from the highest camera (deg)."""
    return math.degrees(math.atan2(max(0.0, top_height_m), max(radius_m, 1e-6)))


def is_author_ground(size_xyz):
    """A mesh the author made wide and flat enough to be the ground (dimensions in metres)."""
    x, y, z = (abs(float(v)) for v in size_xyz)
    span = min(x, y)
    return span >= AUTHOR_GROUND_SPAN_M and z <= AUTHOR_GROUND_FLAT * span


def band_of(height_share):
    """Index of the sky band a dome face belongs to (0 below the horizon .. len(SKY_RINGS) the zenith)."""
    return sum(1 for edge in SKY_RINGS if height_share >= edge)


def band_colours(sky, ground):
    """Clay colours of the dome bands, below the horizon to the zenith: the ground's colour lightened toward the
    horizon (haze), the horizon colour, a blend, the zenith colour."""
    zenith, horizon, _ = SKIES[sky]
    if zenith is None:
        return None
    below = GROUNDS.get(ground, (None,))[0] or horizon
    mix = lambda a, b, t: tuple(round(x + (y - x) * t, 4) for x, y in zip(a, b))  # noqa: E731
    return [mix(below, horizon, 0.5), horizon, mix(horizon, zenith, 0.5), zenith]


def lit_by_look(look_preset, lighting_presets):
    """Does the look own the world and its light? A look with a lighting preset (an HDRI sky, a ground of its own) does;
    the shell is then held, not built over it (scene_roles has no row for a far ground that stays out of the look's
    lit bounds - that row is frozen code, the user's to approve)."""
    return bool((lighting_presets or {}).get(look_preset or 'flat_stylized'))
