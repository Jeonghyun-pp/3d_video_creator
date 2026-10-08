"""Environment kits for spaces that are not streets (2026-10-08, archcut3: a station hall and a ground section were
hand-built per project, the soil a single flat block): hall (a columned interior), strata (layered ground for a
section), vegetation (trees scattered in an area) and water (a surface). Each takes a box or area and preset-like
arguments, builds from library exemplars and catalog materials (a photoreal look gets the catalog material, any other
look the flat colour - modeling.assemble._catalog_material), and returns a report. Nothing here knows a project.
"""
from __future__ import annotations

import fnmatch
import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

import env_fill
import env_fill_core as core

ROLE = 'studio_scene_role'


def _surface(name, kind, color_srgb, roughness=0.85):
    from modeling.assemble import _catalog_material
    return _catalog_material({'catalog_key': kind, 'color_srgb': color_srgb, 'roughness': roughness}, f'{name}/material')


def _block(name, lo, hi, material, role=None, dim_role='none'):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(hi) - Vector(lo), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(Vector(lo) + Vector(hi)) / 2, verts=bm.verts)
    bm.to_mesh(mesh); bm.free()
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['studio_id'] = name
    obj['studio_dim_role'] = dim_role
    if role:
        obj[ROLE] = role
    return obj


def _grid(lo, hi, bay):
    """Column positions on a bay grid inside a rectangle, inset half a bay from each side."""
    nx = max(1, round((hi[0] - lo[0]) / bay[0]))
    ny = max(1, round((hi[1] - lo[1]) / bay[1]))
    sx, sy = (hi[0] - lo[0]) / nx, (hi[1] - lo[1]) / ny
    return [(lo[0] + sx * (i + 0.5), lo[1] + sy * (j + 0.5)) for i in range(nx) for j in range(ny)], (sx, sy)


def hall(name, box, *, library_root, bay_m=(8.0, 8.0), column_m=1.0, column_shape='square', beam_depth_m=0.9, beam_width_m=0.6,
         slab_m=0.4, walls=('x-', 'x+'), floor_kind='concrete', ceiling_kind='concrete', column_kind='concrete', wall_kind='concrete_wall',
         floor_srgb=(0.55, 0.55, 0.53), concrete_srgb=(0.62, 0.61, 0.58), light_rows=True, light_emission=6.0, rail=None,
         frames=(1, 120), seed=0):
    """A columned interior inside box [[x0, y0, z0], [x1, y1, z1]] (floor at z0, ceiling underside at z1): floor and
    ceiling slabs, columns on a bay grid, a downstand beam grid along both bay directions, linear lights between the
    beams, walls on the listed sides ('x-', 'x+', 'y-', 'y+'; the others stay open for the camera), and an optional
    glass railing row (rail: {'y': at, 'x_range': [x0, x1]})."""
    lo, hi = Vector(box[0]), Vector(box[1])
    built = []
    floor_mat, ceiling_mat = _surface(f'{name}.floor', floor_kind, floor_srgb), _surface(f'{name}.ceiling', ceiling_kind, concrete_srgb)
    column_mat, wall_mat = _surface(f'{name}.column', column_kind, concrete_srgb), _surface(f'{name}.wall', wall_kind, concrete_srgb)
    built.append(_block(f'{name}.floor', (lo.x, lo.y, lo.z - slab_m), (hi.x, hi.y, lo.z), floor_mat))
    built.append(_block(f'{name}.ceiling', (lo.x, lo.y, hi.z), (hi.x, hi.y, hi.z + slab_m), ceiling_mat))
    points, (sx, sy) = _grid((lo.x, lo.y), (hi.x, hi.y), bay_m)
    height = hi.z - lo.z - beam_depth_m
    for k, (x, y) in enumerate(points):
        if column_shape == 'round':
            bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=column_m / 2, depth=height, location=(x, y, lo.z + height / 2))
            col = bpy.context.object; col.name = f'{name}.columns.{k}'; col.data.materials.append(column_mat)
            col['studio_id'], col['studio_dim_role'] = col.name, 'none'
        else:
            col = _block(f'{name}.columns.{k}', (x - column_m / 2, y - column_m / 2, lo.z), (x + column_m / 2, y + column_m / 2, lo.z + height), column_mat)
        built.append(col)
    xs = sorted({round(p[0], 4) for p in points}); ys = sorted({round(p[1], 4) for p in points})
    for i, x in enumerate(xs):
        built.append(_block(f'{name}.beams_y.{i}', (x - beam_width_m / 2, lo.y, hi.z - beam_depth_m), (x + beam_width_m / 2, hi.y, hi.z), ceiling_mat))
    for j, y in enumerate(ys):
        built.append(_block(f'{name}.beams_x.{j}', (lo.x, y - beam_width_m / 2, hi.z - beam_depth_m), (hi.x, y + beam_width_m / 2, hi.z), ceiling_mat))
    lights = 0
    if light_rows:
        lamp = bpy.data.materials.get(f'{name}.lamp') or bpy.data.materials.new(f'{name}.lamp')
        lamp.use_nodes = True
        bsdf = next(n for n in lamp.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        bsdf.inputs['Emission Color'].default_value = (1.0, 0.97, 0.92, 1.0); bsdf.inputs['Emission Strength'].default_value = light_emission
        for i in range(1, len(xs)):   # one strip in the middle of every bay, under the slab between the beams
            x = (xs[i - 1] + xs[i]) / 2
            strip = _block(f'{name}.lights.{i}', (x - 0.12, lo.y + 0.5, hi.z - 0.08), (x + 0.12, hi.y - 0.5, hi.z), lamp, role='light_fixture')
            built.append(strip); lights += 1
    for side in walls:
        axis, sign = side[0], side[1]
        t = 0.4
        if axis == 'x':
            x = lo.x if sign == '-' else hi.x
            built.append(_block(f'{name}.wall_{side}', (x - (t if sign == '-' else 0), lo.y, lo.z), (x + (0 if sign == '-' else t), hi.y, hi.z), wall_mat))
        else:
            y = lo.y if sign == '-' else hi.y
            built.append(_block(f'{name}.wall_{side}', (lo.x, y - (t if sign == '-' else 0), lo.z), (hi.x, y + (0 if sign == '-' else t), hi.z), wall_mat))
    if rail:
        source = env_fill.source('glass_railing', library_root, name=f'{name}-rail-src')
        x0, x1 = rail['x_range']
        n = max(1, round((x1 - x0) / 6.0))
        env_fill.at_positions(f'{name}.rail', [source], [((x0 + (x1 - x0) * (i + 0.5) / n, rail['y'], lo.z), 0.0) for i in range(n)], seed=seed)
    return {'kit': 'hall', 'columns': len(points), 'bays': [round(sx, 3), round(sy, 3)], 'beams': len(xs) + len(ys), 'light_rows': lights,
            'walls': list(walls), 'objects': len(built)}


def strata(name, box, *, layers=None, frames=(1, 120), seed=0):
    """Layered ground filling box [[x0, y0, z0], [x1, y1, z1]] from the top down: layers [{thickness_m, kind, color_srgb}]
    (catalog kinds: soil, gravel, brick, concrete ...); the last layer takes what is left. For a section through the
    ground: each stratum its own block and material, so the cut reads as geology, not one dark wall."""
    lo, hi = Vector(box[0]), Vector(box[1])
    layers = layers or [{'thickness_m': 1.5, 'kind': 'soil', 'color_srgb': [0.42, 0.33, 0.24]},
                        {'thickness_m': 4.0, 'kind': 'soil', 'color_srgb': [0.55, 0.45, 0.33]},
                        {'thickness_m': 6.0, 'kind': 'gravel', 'color_srgb': [0.5, 0.48, 0.44]},
                        {'kind': 'concrete', 'color_srgb': [0.4, 0.38, 0.36]}]
    top, rows = hi.z, []
    for i, layer in enumerate(layers):
        bottom = lo.z if i == len(layers) - 1 else max(lo.z, top - layer['thickness_m'])
        if top - bottom <= 1e-4:
            break
        mat = _surface(f'{name}.layer_{i}', layer.get('kind', 'soil'), layer.get('color_srgb', (0.5, 0.42, 0.32)), 0.95)
        _block(f'{name}.layer_{i}', (lo.x, lo.y, bottom), (hi.x, hi.y, top), mat, role='environment_shell')
        rows.append({'layer': i, 'top_z': round(top, 3), 'bottom_z': round(bottom, 3), 'kind': layer.get('kind', 'soil')})
        top = bottom
    return {'kit': 'strata', 'layers': rows}


def vegetation(name, area, *, library_root, kinds=('bare_tree_*',), per_100m2=1.0, scale=(0.8, 1.2), keep_clear=(), frames=(1, 120), seed=0):
    """Trees (library exemplars matching kinds) scattered in area [[x0, y0], [x1, y1], z], none inside keep_clear
    rectangles [[x0, y0, x1, y1]]."""
    (x0, y0), (x1, y1), z = area[0], area[1], area[2] if len(area) > 2 else 0.0
    lib = Path(library_root) / 'exemplars'
    names = sorted({p.name for k in kinds for p in lib.iterdir() if p.is_dir() and fnmatch.fnmatch(p.name, k)})
    if not names:
        raise ValueError(f'KIT: no exemplar matches {list(kinds)}')
    count = max(1, round((x1 - x0) * (y1 - y0) * per_100m2 / 100))
    r = core.rng(seed, name, 'trees')
    rows = []
    for _ in range(count * 3):
        if len(rows) >= count:
            break
        x, y = r.uniform(x0, x1), r.uniform(y0, y1)
        if any(a <= x <= c and b <= y <= d for a, b, c, d in keep_clear):
            continue
        rows.append(((x, y, z), r.uniform(-math.pi, math.pi)))
    sources = [env_fill.source(n, library_root, name=f'{name}-{n.replace("_", "-")}-src') for n in names]
    env_fill.at_positions(f'{name}.trees', sources, rows, seed=seed, scale=sum(scale) / 2)
    return {'kit': 'vegetation', 'trees': len(rows), 'kinds': names}


def water(name, area, *, z=0.0, color_srgb=(0.12, 0.2, 0.22), roughness=0.04, wave_scale=6.0, wave_strength=0.15, frames=(1, 120), seed=0):
    """A water surface over area [[x0, y0], [x1, y1]] at z: a transmissive, glossy surface with a fine wave bump
    (procedural, no texture), tinted by color_srgb."""
    (x0, y0), (x1, y1) = area[0], area[1]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], [], [(0, 1, 2, 3)])
    mat = bpy.data.materials.new(f'{name}/material')
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    lin = tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color_srgb)
    bsdf.inputs['Base Color'].default_value = (*lin, 1.0)
    bsdf.inputs['Roughness'].default_value = roughness
    bsdf.inputs['Transmission Weight'].default_value = 0.85
    bsdf.inputs['IOR'].default_value = 1.333
    noise = nt.nodes.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value = wave_scale
    bump = nt.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = wave_strength
    nt.links.new(noise.outputs['Fac'], bump.inputs['Height']); nt.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    mat.diffuse_color = (*lin, 1.0)
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['studio_id'], obj['studio_dim_role'] = name, 'none'
    return {'kit': 'water', 'area_m2': round((x1 - x0) * (y1 - y0), 2)}
