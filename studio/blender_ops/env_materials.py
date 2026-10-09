"""Environment materials whose variation lives in the shader, not in geometry: thousands of windows cost one material.

window_grid  a facade in world coordinates: floors of `floor_h_m`, bays of `bay_w_m`, a window of `window_frac`
             (width, height share of the bay/floor) per cell. Each cell is hashed (cell, facade axis, object, seed):
             lit when the hash < `lit_ratio`, its colour drawn from `palette_k` (black-body kelvins), its brightness
             varied +-40 %. Unlit windows are dark glass; walls and roof/floor faces never emit. World coordinates keep
             the grid true on scaled or differently sized buildings; Object Info Random makes every building different.
emissive     a plain self-lit surface (lamp heads, car lights, sign panels): its own colour, not the base colour.
propagation  something spreading through a structure (a vibration, heat, water, a crack): a glowing front leaves
             `origin` at `start_frame` and runs outward over the surface at `speed_mps` (world distance), `band_m`
             wide, leaving a fading trail (`fade_s`) so the path it took stays readable; the surface keeps its own
             base colour. Time is a driver on the frame (no keys, no Python), so it renders the same on any worker.
             Why (floor_noise, 2026-10-09): the vibration that "runs down the walls" was a few hand-drawn lines.

Spec materials reach these through `shader: {kind, params}` (modeling.assemble). Registered kinds: SHADERS.
"""
from __future__ import annotations

import math

import bpy


def kelvin_rgb(kelvin):
    """Approximate linear-ish sRGB of a black body (Tanner Helland fit), 0-1."""
    t = kelvin / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    return tuple(max(0.0, min(1.0, c / 255.0)) for c in (r, g, b))


def _linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


class _Nodes:
    def __init__(self, mat):
        self.nt = mat.node_tree
        self.nt.nodes.clear()
        self.x = -1400

    def n(self, kind, **inputs):
        node = self.nt.nodes.new(kind)
        node.location = (self.x, 0)
        self.x += 160
        for key, value in inputs.items():
            if isinstance(key, str) and key.startswith('op_'):
                setattr(node, key[3:], value)
            else:
                node.inputs[key].default_value = value
        return node

    def math(self, op, a, b=None):
        node = self.n('ShaderNodeMath', op_operation=op)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                node.inputs[i].default_value = v
            else:
                self.nt.links.new(v, node.inputs[i])
        return node.outputs[0]

    def link(self, out, socket):
        self.nt.links.new(out, socket)


def _fresh(name):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    return mat


def window_grid(name, *, floor_h_m=3.6, bay_w_m=3.0, window_frac=(0.7, 0.55), sill_frac=0.3, lit_ratio=0.45,
                palette_k=(2700, 3200, 4000, 5000), emission=6.0, wall_color=(0.32, 0.33, 0.36), glass_color=(0.03, 0.04, 0.06),
                base_z_m=0.0, seed=0, white_mix=0.0):
    """white_mix: share of white mixed into every window colour (0 = pure black body). Black-body kelvins below
    4000 K are strongly orange; a city at night photographed on daylight balance reads far less saturated
    (measured: s01 sat_mean 0.50 vs the reference style 0.21-0.24)."""
    mat = _fresh(name)
    g = _Nodes(mat)
    geo = g.n('ShaderNodeNewGeometry')
    pos = g.n('ShaderNodeSeparateXYZ'); g.link(geo.outputs['Position'], pos.inputs[0])
    nrm = g.n('ShaderNodeSeparateXYZ'); g.link(geo.outputs['Normal'], nrm.inputs[0])
    # facade axis: faces looking along x run their grid along y, and the other way round
    along_x = g.math('GREATER_THAN', g.math('ABSOLUTE', nrm.outputs['Y']), g.math('ABSOLUTE', nrm.outputs['X']))
    u = g.math('ADD', g.math('MULTIPLY', pos.outputs['X'], along_x),
               g.math('MULTIPLY', pos.outputs['Y'], g.math('SUBTRACT', 1.0, along_x)))
    v = g.math('SUBTRACT', pos.outputs['Z'], base_z_m)
    us, vs = g.math('DIVIDE', u, bay_w_m), g.math('DIVIDE', v, floor_h_m)
    cu, cv = g.math('FLOOR', us), g.math('FLOOR', vs)
    fu, fv = g.math('FRACT', us), g.math('FRACT', vs)
    wx, wy = window_frac
    inside_u = g.math('MULTIPLY', g.math('GREATER_THAN', fu, (1 - wx) / 2), g.math('LESS_THAN', fu, (1 + wx) / 2))
    inside_v = g.math('MULTIPLY', g.math('GREATER_THAN', fv, sill_frac), g.math('LESS_THAN', fv, sill_frac + wy))
    vertical = g.math('LESS_THAN', g.math('ABSOLUTE', nrm.outputs['Z']), 0.5)   # roofs and soffits never get windows
    window = g.math('MULTIPLY', g.math('MULTIPLY', inside_u, inside_v), vertical)
    # per-cell hash: cell, facade side, building (Object Info Random) and seed
    info = g.n('ShaderNodeObjectInfo')
    side = g.math('ADD', along_x, g.math('MULTIPLY', g.math('SIGN', g.math('ADD', nrm.outputs['X'], nrm.outputs['Y'])), 2.0))
    cell = g.n('ShaderNodeCombineXYZ')
    g.link(cu, cell.inputs['X']); g.link(cv, cell.inputs['Y']); g.link(side, cell.inputs['Z'])
    noise = g.n('ShaderNodeTexWhiteNoise', op_noise_dimensions='4D')
    g.link(cell.outputs[0], noise.inputs['Vector'])
    g.link(g.math('ADD', g.math('MULTIPLY', info.outputs['Random'], 977.0), float(seed % 997)), noise.inputs['W'])
    pick = noise.outputs['Value']
    lit = g.math('LESS_THAN', pick, float(lit_ratio))
    colour_pick = g.n('ShaderNodeSeparateColor'); g.link(noise.outputs['Color'], colour_pick.inputs[0])
    ramp = g.n('ShaderNodeValToRGB')
    ramp.color_ramp.interpolation = 'CONSTANT'
    stops = ramp.color_ramp.elements
    for i, k in enumerate(palette_k):
        rgb = tuple(_linear(c + (1.0 - c) * white_mix) for c in kelvin_rgb(k))
        if i < len(stops):
            stops[i].position = i / len(palette_k)
        else:
            stops.new(i / len(palette_k))
        stops[i].color = (*rgb, 1.0)
    while len(stops) > len(palette_k):
        stops.remove(stops[-1])
    g.link(colour_pick.outputs[0], ramp.inputs['Fac'])
    vary = g.math('ADD', 0.6, g.math('MULTIPLY', colour_pick.outputs[1], 0.8))   # +-40 % brightness per window
    strength = g.math('MULTIPLY', g.math('MULTIPLY', g.math('MULTIPLY', window, lit), vary), float(emission))
    bsdf = g.n('ShaderNodeBsdfPrincipled')
    mix = g.n('ShaderNodeMix', op_data_type='RGBA')
    mix.inputs[6].default_value = (*[_linear(c) for c in wall_color], 1.0)
    mix.inputs[7].default_value = (*[_linear(c) for c in glass_color], 1.0)
    g.link(window, mix.inputs[0])
    g.link(mix.outputs[2], bsdf.inputs['Base Color'])
    g.link(g.math('SUBTRACT', 0.8, g.math('MULTIPLY', window, 0.7)), bsdf.inputs['Roughness'])
    g.link(ramp.outputs['Color'], bsdf.inputs['Emission Color'])
    g.link(strength, bsdf.inputs['Emission Strength'])
    out = g.n('ShaderNodeOutputMaterial')
    g.link(bsdf.outputs[0], out.inputs['Surface'])
    mat.diffuse_color = (*[_linear(c) for c in wall_color], 1.0)
    mat['studio_shader'] = 'window_grid'
    return mat


def emissive(name, *, color_srgb=(1.0, 0.95, 0.85), strength=8.0, base_srgb=None):
    mat = _fresh(name)
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    lin = tuple(_linear(c) for c in color_srgb)
    bsdf.inputs['Base Color'].default_value = (*[_linear(c) for c in (base_srgb or color_srgb)], 1.0)
    bsdf.inputs['Emission Color'].default_value = (*lin, 1.0)
    bsdf.inputs['Emission Strength'].default_value = float(strength)
    mat.diffuse_color = (*lin, 1.0)
    mat['studio_shader'] = 'emissive'
    return mat


def propagation(name, *, origin=(0.0, 0.0, 0.0), speed_mps=4.0, start_frame=0, band_m=0.3, color_srgb=(0.25, 0.95, 0.9),
                strength=8.0, fade_s=1.5, trail=0.35, base_srgb=(0.6, 0.6, 0.58), roughness=0.8):
    if float(speed_mps) <= 0 or float(band_m) <= 0 or float(fade_s) <= 0:
        raise ValueError(f'{name}: propagation needs speed_mps, band_m and fade_s above 0')
    mat = _fresh(name)
    g = _Nodes(mat)
    geometry = g.n('ShaderNodeNewGeometry')
    offset = g.n('ShaderNodeVectorMath', op_operation='SUBTRACT')
    offset.inputs[1].default_value = tuple(float(v) for v in origin)
    g.link(geometry.outputs['Position'], offset.inputs[0])
    length = g.n('ShaderNodeVectorMath', op_operation='LENGTH')
    g.link(offset.outputs[0], length.inputs[0])
    dist = length.outputs['Value']
    front_node = g.n('ShaderNodeValue')
    front_node.label = 'studio_front_m'
    driver = front_node.outputs[0].driver_add('default_value').driver
    driver.type = 'SCRIPTED'
    for var_name, path in (('fps', 'render.fps'), ('base', 'render.fps_base')):   # read when evaluated: the project's rate,
        var = driver.variables.new()                                                # not the one at build time (24 at
        var.name, var.type = var_name, 'SINGLE_PROP'                                # factory startup)
        var.targets[0].id_type = 'SCENE'
        var.targets[0].id = bpy.context.scene
        var.targets[0].data_path = path
    # shot frame f is Blender frame f + 1: the front leaves the origin on the shot's start_frame
    driver.expression = f'(frame - 1 - {float(start_frame)}) * base / fps * {float(speed_mps)}'   # a simple expression: no Python
    front = front_node.outputs[0]
    # the front: 1 at the front, 0 a band away
    ring = g.math('MAXIMUM', g.math('SUBTRACT', 1.0, g.math('DIVIDE', g.math('ABSOLUTE', g.math('SUBTRACT', dist, front)), float(band_m))), 0.0)
    # the trail: what the front already passed, fading with the time since it passed
    behind = g.math('LESS_THAN', dist, front)
    since_s = g.math('DIVIDE', g.math('SUBTRACT', front, dist), float(speed_mps))
    decay = g.math('EXPONENT', g.math('DIVIDE', g.math('MULTIPLY', since_s, -1.0), max(1e-3, float(fade_s))))
    glow = g.math('ADD', ring, g.math('MULTIPLY', g.math('MULTIPLY', behind, decay), float(trail)))
    bsdf = g.n('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Base Color'].default_value = (*[_linear(c) for c in base_srgb], 1.0)
    bsdf.inputs['Roughness'].default_value = float(roughness)
    bsdf.inputs['Emission Color'].default_value = (*[_linear(c) for c in color_srgb], 1.0)
    g.link(g.math('MULTIPLY', glow, float(strength)), bsdf.inputs['Emission Strength'])
    out = g.n('ShaderNodeOutputMaterial')
    g.link(bsdf.outputs[0], out.inputs['Surface'])
    mat.diffuse_color = (*[_linear(c) for c in base_srgb], 1.0)
    mat['studio_shader'] = 'propagation'
    return mat


SHADERS = {'window_grid': window_grid, 'emissive': emissive, 'propagation': propagation}


def make(kind, name, params):
    if kind not in SHADERS:
        raise ValueError(f'unknown shader kind {kind!r} (known: {sorted(SHADERS)})')
    return SHADERS[kind](name, **(params or {}))
