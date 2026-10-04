"""Generic shader-level surface details for spec-built bodies.

panel_lines: thin grooves where the surface crosses planes spaced ``spacing_m``
apart along the chosen object-local axes (frames along the length, stringers
across), rendered as a bump + slight darkening. Works on any skinned body.
clear_canopy: a simple glass material (transmission).
"""
import bpy

TAG = 'StudioPanelLines'


def _principled(material):
    if material.node_tree is None:  # Blender 5 materials always have one; use_nodes is deprecated
        material.use_nodes = True
    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            return node
    raise ValueError(f'material {material.name!r} has no Principled BSDF')


def panel_lines(material, spacing_m=0.6, width_m=0.004, depth=0.3, axes='xyz', darken=0.35):
    """Add object-local panel grooves to ``material`` in place; returns it. Re-calling replaces them."""
    if spacing_m <= 0 or width_m <= 0:
        raise ValueError('spacing_m and width_m must be positive')
    bsdf = _principled(material)
    nt = material.node_tree
    for node in [n for n in nt.nodes if n.name.startswith(TAG)]:
        if node.name == TAG + '_Mix' or node.name == TAG + '_Bump':
            # restore what the detail replaced
            src = node.inputs['A' if node.type == 'MIX' else 'Normal']
            if src.is_linked:
                target = bsdf.inputs['Base Color' if node.type == 'MIX' else 'Normal']
                nt.links.new(src.links[0].from_socket, target)
            elif node.type == 'MIX':
                bsdf.inputs['Base Color'].default_value = node.inputs['A'].default_value
        nt.nodes.remove(node)

    def add(kind, label, **inputs):
        node = nt.nodes.new(kind)
        node.name = node.label = f'{TAG}_{label}'
        node.location = (-900, -300)
        for key, value in inputs.items():
            node.inputs[key].default_value = value
        return node

    coord = add('ShaderNodeTexCoord', 'Coord')
    sep = add('ShaderNodeSeparateXYZ', 'Sep')
    nt.links.new(coord.outputs['Object'], sep.inputs[0])
    mask = None
    for axis in axes.lower():
        # distance to nearest plane k*spacing: |fract(c/s + 0.5) - 0.5| * s
        div = add('ShaderNodeMath', f'Div{axis}')
        div.operation = 'MULTIPLY_ADD'
        nt.links.new(sep.outputs[axis.upper()], div.inputs[0])
        div.inputs[1].default_value = 1.0 / spacing_m
        div.inputs[2].default_value = 0.5
        fr = add('ShaderNodeMath', f'Fract{axis}')
        fr.operation = 'FRACT'
        nt.links.new(div.outputs[0], fr.inputs[0])
        dist = add('ShaderNodeMath', f'Dist{axis}')
        dist.operation = 'ABSOLUTE'
        sub = add('ShaderNodeMath', f'Sub{axis}')
        sub.operation = 'SUBTRACT'
        nt.links.new(fr.outputs[0], sub.inputs[0])
        sub.inputs[1].default_value = 0.5
        nt.links.new(sub.outputs[0], dist.inputs[0])
        line = add('ShaderNodeMapRange', f'Line{axis}')
        line.interpolation_type = 'SMOOTHSTEP'
        nt.links.new(dist.outputs[0], line.inputs['Value'])
        line.inputs['From Min'].default_value = 0.0
        line.inputs['From Max'].default_value = 0.5 * width_m / spacing_m
        line.inputs['To Min'].default_value = 1.0
        line.inputs['To Max'].default_value = 0.0
        if mask is None:
            mask = line.outputs['Result']
        else:
            mx = add('ShaderNodeMath', f'Max{axis}')
            mx.operation = 'MAXIMUM'
            nt.links.new(mask, mx.inputs[0])
            nt.links.new(line.outputs['Result'], mx.inputs[1])
            mask = mx.outputs[0]
    if mask is None:
        raise ValueError('axes must name at least one of x, y, z')

    bump = add('ShaderNodeBump', 'Bump', Strength=float(depth), Distance=width_m)
    bump.invert = True
    nt.links.new(mask, bump.inputs['Height'])
    if bsdf.inputs['Normal'].is_linked:
        nt.links.new(bsdf.inputs['Normal'].links[0].from_socket, bump.inputs['Normal'])
    nt.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])

    mix = nt.nodes.new('ShaderNodeMix')
    mix.name = mix.label = f'{TAG}_Mix'
    mix.data_type = 'RGBA'
    mix.blend_type = 'MULTIPLY'
    base = bsdf.inputs['Base Color']
    if base.is_linked:
        nt.links.new(base.links[0].from_socket, mix.inputs['A'])
    else:
        mix.inputs['A'].default_value = base.default_value
    shade = 1.0 - float(darken)
    mix.inputs['B'].default_value = (shade, shade, shade, 1.0)
    nt.links.new(mask, mix.inputs['Factor'])
    nt.links.new(mix.outputs['Result'], base)
    return material


def clear_canopy(name, tint_srgb=(0.92, 0.95, 0.97), roughness=0.03, ior=1.49):
    """Return (or rebuild) a glass-like Principled material named ``name``."""
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    bsdf = _principled(material)
    bsdf.inputs['Base Color'].default_value = (*(srgb_to_linear(c) for c in tint_srgb), 1.0)
    bsdf.inputs['Metallic'].default_value = 0.0
    bsdf.inputs['Roughness'].default_value = float(roughness)
    bsdf.inputs['IOR'].default_value = float(ior)
    bsdf.inputs['Transmission Weight'].default_value = 1.0
    bsdf.inputs['Coat Weight'].default_value = 0.3
    material.diffuse_color = (*(srgb_to_linear(c) for c in tint_srgb), 0.3)
    return material


def srgb_to_linear(c):
    c = float(c)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
