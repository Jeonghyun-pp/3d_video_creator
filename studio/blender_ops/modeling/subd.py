"""Subdivision cage: a few control points drawn as a closed low-poly cage, smoothed into a molded surface (covers,
housings, ducts, handles) - the way a modeller blocks out a plastic or pressed part.

params = {
  verts: [[x, y, z], ...]           # cage points (m)
  faces: [[i, j, k, ...], ...]      # closed cage: every edge in exactly two faces (subjects.lint_spec checks it)
  creases: [[i, j, w], ...]         # edge i-j keeps sharpness w (0..1; 1 = a crisp moulded edge or parting line)
  levels: 2                         # subdivision levels (each x4 faces)
  smooth: true, sharp_angle_deg: SHARP_ANGLE_DEG (31)
}
The surface lies inside the cage (except creased edges, which it follows); move cage points to shape it.
"""
import bmesh

from .ops import _bake
from .primitives import apply_smoothing, mesh_object


def subd(name, params):
    verts, faces = params['verts'], params['faces']
    obj = mesh_object(name, verts, faces, {'smooth': True})
    mesh = obj.data
    creases = params.get('creases') or []
    if creases:
        index = {}
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        for e in bm.edges:
            index[frozenset(v.index for v in e.verts)] = e.index
        bm.free()
        values = [0.0] * len(mesh.edges)
        for i, j, w in creases:
            key = frozenset((int(i), int(j)))
            if key not in index:
                raise ValueError(f'{name}: crease {i}-{j} is not a cage edge')
            values[index[key]] = min(1.0, max(0.0, float(w)))
        layer = mesh.attributes.new('crease_edge', 'FLOAT', 'EDGE')
        layer.data.foreach_set('value', values)
    modifier = obj.modifiers.new('studio_subd', 'SUBSURF')
    modifier.levels = modifier.render_levels = int(params.get('levels', 2))
    _bake(obj, modifier)
    if 'crease_edge' in obj.data.attributes:
        obj.data.attributes.remove(obj.data.attributes['crease_edge'])
    apply_smoothing(obj.data, params)
    return obj
