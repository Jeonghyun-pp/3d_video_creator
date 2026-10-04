"""Photoreal material library for author scripts (called explicitly, never an automatic pass).

    from look_materials import make_material, assign, texture
    mat = make_material('frame', 'galvanized_steel', library_root=job['library_root'])
    assign(obj, mat)

Every catalog kind = Principled BSDF + (CC0 library texture set where suitable) + roughness and
colour variation + edge wear + top-facing dust + crevice grime. All patterns are driven by
OBJECT-LOCAL real-metre coordinates (Object coords x object scale): no UVs needed, nothing
swims on moving parts (prototype swim test: 0.06 % vs 2.31 % for world coords) and unapplied
object scale keeps real tile size (scale test: 16 px per 10 cm cell on a scale-(2,1,1) cube).
Image maps use Box projection; height goes through a Bump node, never a tangent-space normal
map (box projection has no valid UV tangents).

Distance LOD: every procedural pattern is band-limited by camera distance (Camera Data ->
View Distance). A feature of size s metres keeps full contrast while it spans >= lod.full_px
pixels and fades to its mean once it spans <= lod.zero_px pixels; noise octaves are dropped
the same way. Thin members far from camera therefore stop shimmering / reading as fully worn.

Textures come only from the studio library: the manifest must say use_status == 'cleared' and
every file must match its manifest sha256 before it is packed into the .blend.

Cycles-only (degrade silently in EEVEE): Bevel-node edge mask, AO only_local grime.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy

CATALOG_PATH = Path(__file__).parent / 'look_data' / 'material_catalog.json'
PREFIX = 'StudioMat_'
SRGB_ROLES = {'base_color'}          # every other map role is data -> Non-Color
# Fallback when a manifest file carries no map role (Poly Haven files are stored as 'primary'):
# Poly Haven's file naming convention <asset>_<map>_<res>.<ext>. Unrecognised tokens map to nothing.
POLYHAVEN_TOKENS = {'diff': 'base_color', 'nor_gl': 'normal', 'rough': 'roughness', 'metal': 'metallic',
                    'disp': 'displacement', 'ao': 'ambient_occlusion'}
MAP_ROLES = set(POLYHAVEN_TOKENS.values()) | {'opacity'}


def _fail(msg):
    raise ValueError('LOOK_QA_FAILED: ' + msg)


def catalog():
    return json.loads(CATALOG_PATH.read_text())


# ---------------------------------------------------------------------------- textures

def _asset_dir(library_root, asset_id, version=None):
    root = Path(library_root)
    root = root if root.name == 'assets' else root / 'assets'
    base = root / asset_id
    if version is None:
        versions = sorted(p.name for p in base.glob('v[0-9]*') if (p / 'asset.json').is_file())
        if not versions:
            raise FileNotFoundError(f'library asset {asset_id!r} not found under {root}')
        version = versions[-1]
    return base / version


def _role(entry):
    role = entry.get('role')
    if role in MAP_ROLES:
        return role
    stem = Path(entry['relative_path']).stem.lower()
    for token, mapped in POLYHAVEN_TOKENS.items():
        if f'_{token}_' in f'_{stem}_':
            return mapped
    return None


def resolve_map(asset_id, map_role, *, library_root, version=None):
    """Return (asset_dir, manifest, file_entry) for one map role, or raise."""
    adir = _asset_dir(library_root, asset_id, version)
    manifest = json.loads((adir / 'asset.json').read_text())
    status = (manifest.get('source') or {}).get('use_status')
    if status != 'cleared':
        _fail(f'texture provenance: {asset_id} use_status={status!r}, need cleared')
    hits = [f for f in manifest.get('files', []) if _role(f) == map_role]
    if len(hits) != 1:
        _fail(f'texture provenance: {asset_id} has {len(hits)} files for role {map_role!r}')
    return adir, manifest, hits[0]


def texture(asset_id, map_role, *, library_root, version=None):
    """Load one verified, packed library map. base_color -> sRGB, all others Non-Color."""
    adir, manifest, entry = resolve_map(asset_id, map_role, library_root=library_root, version=version)
    rel = entry['relative_path']
    path = (adir / 'original' / rel).resolve()
    if not path.is_relative_to((adir / 'original').resolve()) or not path.is_file():
        _fail(f'texture provenance: {asset_id} file {rel!r} missing')
    blob = path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != entry.get('sha256'):
        _fail(f'texture provenance: {asset_id}/{rel} sha256 {digest[:12]} != manifest {str(entry.get("sha256"))[:12]}')
    colorspace = 'sRGB' if map_role in SRGB_ROLES else 'Non-Color'
    for img in bpy.data.images:
        if img.get('studio_sha256') == digest and img.packed_file and img.colorspace_settings.name == colorspace:
            return img
    img = bpy.data.images.load(str(path), check_existing=False)
    img.pack(data=blob, data_len=len(blob))           # pack the bytes that were verified
    img.filepath_raw = f'//studio_textures/{asset_id}/{manifest.get("version", adir.name)}/{rel}'
    img.colorspace_settings.name = colorspace
    img.name = f'StudioTex_{asset_id}_{map_role}'
    img['studio_asset_id'] = asset_id
    img['studio_asset_version'] = manifest.get('version', adir.name)
    img['studio_map_role'] = map_role
    img['studio_relative_path'] = rel
    img['studio_sha256'] = digest
    img['studio_license_id'] = manifest['source'].get('license_id')
    return img


# ---------------------------------------------------------------------------- node builder

class _G:
    def __init__(self, nt, lod):
        self.nt, self.x, self.lod_cfg, self._fp = nt, -1600, lod, None

    def n(self, kind, **props):
        node = self.nt.nodes.new(kind)
        for k, v in props.items():
            setattr(node, k, v)
        node.location = (self.x, len(self.nt.nodes) * -40)
        self.x += 30
        return node

    def link(self, a, b):
        self.nt.links.new(a, b)

    def math(self, op, a, b=None, clamp=False):
        m = self.n('ShaderNodeMath', operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                m.inputs[i].default_value = v
            else:
                self.link(v, m.inputs[i])
        return m.outputs[0]

    def maprange(self, v, a, b, c, d):
        m = self.n('ShaderNodeMapRange', clamp=True)
        self.link(v, m.inputs['Value'])
        m.inputs['From Min'].default_value, m.inputs['From Max'].default_value = a, b
        m.inputs['To Min'].default_value, m.inputs['To Max'].default_value = c, d
        return m.outputs['Result']

    def mix(self, dtype, fac, a, b, blend='MIX'):
        m = self.n('ShaderNodeMix', data_type=dtype, blend_type=blend, clamp_factor=True)
        suf = {'FLOAT': 'Float', 'RGBA': 'Color'}[dtype]
        ins = {s.identifier: s for s in m.inputs}
        for sock, v in ((ins['Factor_Float'], fac), (ins['A_' + suf], a), (ins['B_' + suf], b)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            elif isinstance(v, (tuple, list)):
                sock.default_value = (*v[:3], 1.0)
            else:
                self.link(v, sock)
        return {s.identifier: s for s in m.outputs}['Result_' + suf]

    # --- distance LOD -----------------------------------------------------------------
    def footprint(self):
        """Metres covered by one output pixel at the shading point (view distance x rad/px)."""
        if self._fp is None:
            cam = self.n('ShaderNodeCameraData')
            self._fp = self.math('MULTIPLY', cam.outputs['View Distance'], self.lod_cfg['rad_per_px'])
        return self._fp

    def lod(self, feature_m):
        """1 while feature spans >= full_px pixels, 0 once it spans <= zero_px pixels."""
        c = self.lod_cfg
        return self.maprange(self.footprint(), feature_m / c['full_px'], feature_m / c['zero_px'], 1.0, 0.0)

    def band(self, value, feature_m, mean):
        """Fade a pattern value to its mean once its feature size is sub-pixel."""
        return self.mix('FLOAT', self.lod(feature_m), mean, value)

    def noise(self, vec, scale, detail=4.0, rough=0.55):
        """Band-limited fBm noise Fac: octaves finer than lod.full_px pixels are dropped."""
        t = self.n('ShaderNodeTexNoise')
        self.link(vec, t.inputs['Vector'])
        t.inputs['Scale'].default_value, t.inputs['Roughness'].default_value = scale, rough
        # octave k has feature size 1/(scale*2^k); keep k <= log2(1/(scale*footprint*full_px))
        octaves = self.math('LOGARITHM', self.math('DIVIDE', 1.0 / (scale * self.lod_cfg['full_px']),
                                                   self.math('MAXIMUM', self.footprint(), 1e-9)), 2.0)
        self.link(self.math('MINIMUM', self.math('MAXIMUM', octaves, 0.0), float(detail)), t.inputs['Detail'])
        return self.band(t.outputs['Fac'], 1.0 / scale, 0.5)


# ---------------------------------------------------------------------------- materials

def _params(entry, scale_m, wear):
    p = dict(entry)
    if scale_m is not None:
        if not p.get('texset'):
            raise ValueError('scale_m given but catalog kind has no texture set')
        if scale_m <= 0:
            raise ValueError('scale_m must be > 0')
        p['tile_m'] = float(scale_m)
    if wear is not None:
        if wear < 0:
            raise ValueError('wear must be >= 0')
        for key in ('edge_wear', 'dust', 'grime'):
            p[key] = p[key] * float(wear)
    return p


def make_material(name, catalog_key, *, library_root, scale_m=None, wear=None, overrides=None):
    """Build (or rebuild in place) material 'StudioMat_<name>' from a catalog kind.

    scale_m:   real-world tile size in metres of the kind's texture set (default: catalog).
    wear:      multiplier on edge_wear, dust and grime (0 = factory clean, 1 = catalog).
    overrides: catalog parameter overrides, e.g. {'base_color': [0.45, 0.03, 0.02]};
               only keys the catalog kind already defines are accepted.
    """
    cat = catalog()
    if catalog_key not in cat['kinds']:
        raise ValueError(f'unknown material kind {catalog_key!r}; known: {sorted(cat["kinds"])}')
    entry = cat['kinds'][catalog_key]
    unknown = sorted(set(overrides or {}) - set(entry))
    if unknown:
        raise ValueError(f'unknown material parameter(s) {unknown}; known: {sorted(entry)}')
    p = _params({**entry, **(overrides or {})}, scale_m, wear)
    lod = {**cat['lod'], **{k[4:]: v for k, v in (overrides or {}).items() if k.startswith('lod_')}}

    mat_name = name if name.startswith(PREFIX) else PREFIX + name
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    g = _G(nt, lod)
    out = g.n('ShaderNodeOutputMaterial')
    bsdf = g.n('ShaderNodeBsdfPrincipled')
    g.link(bsdf.outputs[0], out.inputs['Surface'])
    provenance = []

    # --- coordinates: object-local metres (immune to object motion and to object scale) ---
    tc = g.n('ShaderNodeTexCoord')
    sc = g.n('ShaderNodeAttribute', attribute_type='OBJECT', attribute_name='scale')
    vm = g.n('ShaderNodeVectorMath', operation='MULTIPLY')
    g.link(tc.outputs['Object'], vm.inputs[0])
    g.link(sc.outputs['Vector'], vm.inputs[1])
    P = vm.outputs[0]
    geo = g.n('ShaderNodeNewGeometry')

    # --- base colour / roughness / height sources ---
    color, rough, height = p['base_color'], p['roughness'], None
    ts = cat['texsets'].get(p['texset']) if p['texset'] else None
    if p['texset'] and ts is None:
        raise ValueError(f'catalog kind {catalog_key!r} names unknown texture set {p["texset"]!r}')
    if ts:
        mp = g.n('ShaderNodeMapping')
        g.link(P, mp.inputs['Vector'])
        s = 1.0 / p.get('tile_m', ts['tile_m'])
        mp.inputs['Scale'].default_value = (s, s, s)

        def tex(slot):
            img = texture(ts['asset_id'], ts['maps'][slot], library_root=library_root)
            node = g.n('ShaderNodeTexImage', projection='BOX', projection_blend=0.25, interpolation='Linear')
            node.image = img
            g.link(mp.outputs[0], node.inputs['Vector'])
            provenance.append({k: img[f'studio_{k}'] for k in ('asset_id', 'asset_version', 'map_role', 'relative_path', 'sha256')}
                              | {'colorspace': img.colorspace_settings.name})
            return node

        c = tex('color')
        color = g.mix('RGBA', 1.0, c.outputs['Color'], tuple(p['tex_tint']), blend='MULTIPLY')
        lo, hi = p['tex_rough_range'] or (0.0, 1.0)
        rough = g.maprange(tex('rough').outputs['Color'], 0.0, 1.0, lo, hi)
        if ts['maps'].get('height'):
            height = tex('height').outputs['Color']

    # spangle (galvanized zinc crystals): irregular Voronoi cells, per-cell brightness + roughness
    if p['spangle'] > 0:
        cell_m = 1.0 / p['spangle_scale']
        vo = g.n('ShaderNodeTexVoronoi', feature='F1')
        g.link(P, vo.inputs['Vector'])
        vo.inputs['Scale'].default_value = p['spangle_scale']
        vo.inputs['Randomness'].default_value = 1.0
        cellv = g.n('ShaderNodeSeparateColor')
        g.link(vo.outputs['Color'], cellv.inputs[0])
        cell = g.band(cellv.outputs[0], cell_m, 0.5)
        cell_r = g.band(cellv.outputs[1], cell_m, 0.5)
        color = g.mix('RGBA', g.math('MULTIPLY', cell, 0.18 * p['spangle']), color,
                      tuple(min(1, c * 1.15) for c in p['base_color']))
        rough = g.math('ADD', rough, g.math('MULTIPLY', g.math('SUBTRACT', cell_r, 0.5), 0.20 * p['spangle']))
        edge_vo = g.n('ShaderNodeTexVoronoi', feature='DISTANCE_TO_EDGE')   # cell boundaries as faint ridges
        g.link(P, edge_vo.inputs['Vector'])
        edge_vo.inputs['Scale'].default_value = p['spangle_scale']
        ridge = g.math('MULTIPLY', g.maprange(edge_vo.outputs['Distance'], 0.0, 0.05, 0.0, 1.0), 0.3)
        height = g.math('MULTIPLY', ridge, g.lod(cell_m))
        p['bump'] = max(p['bump'], 0.05)

    # large-scale colour variation (breaks tiling / flatness)
    if p['color_var'] > 0:
        nz = g.noise(P, p['color_var_scale'], 3.0, 0.5)
        f = g.maprange(nz, 0.3, 0.7, 1.0 - p['color_var'], 1.0 + p['color_var'])
        cv = g.n('ShaderNodeCombineColor')
        for i in range(3):
            g.link(f, cv.inputs[i])
        color = g.mix('RGBA', 1.0, color, cv.outputs[0], blend='MULTIPLY')

    if p['rough_var'] > 0:
        rn = g.noise(P, p['rough_var_scale'], 6.0, 0.6)
        rough = g.math('ADD', rough, g.maprange(rn, 0.25, 0.75, -p['rough_var'], p['rough_var']))
    if p['streak'] > 0:      # rolling-direction streaks along local X (anisotropic noise scale)
        mp2 = g.n('ShaderNodeMapping')
        g.link(P, mp2.inputs['Vector'])
        mp2.inputs['Scale'].default_value = tuple(p['streak_scale'])
        st = g.noise(mp2.outputs[0], 1.0, 2.0, 0.5)
        st = g.band(st, 1.0 / max(p['streak_scale']), 0.5)   # streak width is set by the finest axis
        rough = g.math('ADD', rough, g.maprange(st, 0.3, 0.7, -p['streak'], p['streak']))
    if p['micro_height_scale'] > 0:   # orange peel / sand-cast surface
        op = g.noise(P, p['micro_height_scale'], 2.0, 0.5)
        height = op if height is None else g.math('ADD', height, op)
    if p['pits_scale'] > 0 and p['pits_strength'] > 0:
        pit = g.n('ShaderNodeTexVoronoi', feature='F1')
        g.link(P, pit.inputs['Vector'])
        pit.inputs['Scale'].default_value = p['pits_scale']
        pits = g.math('MULTIPLY', g.maprange(pit.outputs['Distance'], 0.0, 0.4, 0.0, 1.0), p['pits_strength'])
        pits = g.math('MULTIPLY', pits, g.lod(1.0 / p['pits_scale']))
        height = pits if height is None else g.math('ADD', height, pits)

    metallic = p['metallic']

    # --- grime in crevices (AO, only_local -> stable on moving parts) ---
    if p['grime'] > 0:
        ao = g.n('ShaderNodeAmbientOcclusion', only_local=p['grime_local'], inside=False, samples=8)
        ao.inputs['Distance'].default_value = p['grime_dist']
        gm = g.maprange(ao.outputs['AO'], 0.35, 0.95, 1.0, 0.0)
        gn = g.noise(P, 18.0, 4.0, 0.6)
        gmask = g.math('MULTIPLY', g.math('MULTIPLY', gm, g.maprange(gn, 0.35, 0.7, 0.3, 1.0)), p['grime'], clamp=True)
        color = g.mix('RGBA', gmask, color, tuple(p['grime_color']))
        rough = g.mix('FLOAT', gmask, rough, 0.85)

    # --- edge wear (bevel-normal deviation; faded once the worn band is sub-pixel) ---
    if p['edge_wear'] > 0 and p['edge_source'] != 'none':
        if p['edge_source'] == 'pointiness':
            emask = g.maprange(geo.outputs['Pointiness'], 0.5, 0.56, 0.0, 1.0)
        else:
            bv = g.n('ShaderNodeBevel', samples=8)
            bv.inputs['Radius'].default_value = p['edge_radius']
            dp = g.n('ShaderNodeVectorMath', operation='DOT_PRODUCT')
            g.link(bv.outputs['Normal'], dp.inputs[0])
            g.link(geo.outputs['Normal'], dp.inputs[1])
            emask = g.maprange(dp.outputs['Value'], 0.995, 0.93, 0.0, 1.0)
        en = g.noise(P, 40.0, 8.0, 0.7)
        emask = g.math('MULTIPLY', g.math('MULTIPLY', emask, g.maprange(en, 0.42, 0.62, 0.0, 1.0)), p['edge_wear'], clamp=True)
        emask = g.math('MULTIPLY', emask, g.lod(p['edge_radius']))
        color = g.mix('RGBA', emask, color, tuple(p['edge_color']))
        rough = g.mix('FLOAT', emask, rough, p['edge_rough'])
        if p['edge_metallic'] is not None:
            metallic = g.mix('FLOAT', emask, metallic, p['edge_metallic'])

    # --- top-facing dust (world-up normal; pattern object-local) ---
    if p['dust'] > 0:
        nz_ = g.n('ShaderNodeSeparateXYZ')
        g.link(geo.outputs['Normal'], nz_.inputs[0])
        up = g.maprange(nz_.outputs['Z'], 0.55, 0.95, 0.0, 1.0)
        dn = g.noise(P, p['dust_scale'], 8.0, 0.65)
        dmask = g.math('MULTIPLY', g.math('MULTIPLY', up, g.maprange(dn, 0.4, 0.75, 0.15, 1.0)), p['dust'], clamp=True)
        color = g.mix('RGBA', dmask, color, tuple(p['dust_color']))
        rough = g.mix('FLOAT', dmask, rough, 0.92)
        metallic = g.mix('FLOAT', dmask, metallic, 0.0)

    # --- bump (height -> Bump node; no tangent-space normal maps under box projection) ---
    if height is not None and p['bump'] > 0:
        bp = g.n('ShaderNodeBump')
        bp.inputs['Strength'].default_value = p['bump']
        bp.inputs['Distance'].default_value = p['bump_dist']
        g.link(height, bp.inputs['Height'])
        g.link(bp.outputs['Normal'], bsdf.inputs['Normal'])

    def setin(sock, v):
        if isinstance(v, (int, float)):
            bsdf.inputs[sock].default_value = v
        elif isinstance(v, (tuple, list)):
            bsdf.inputs[sock].default_value = (*v[:3], 1.0)
        else:
            g.link(v, bsdf.inputs[sock])

    setin('Base Color', color)
    setin('Roughness', rough if isinstance(rough, (int, float)) else g.math('MAXIMUM', rough, 0.02))
    setin('Metallic', metallic)
    bsdf.inputs['IOR'].default_value = p['ior']
    bsdf.inputs['Transmission Weight'].default_value = p['transmission']
    mat['studio_catalog_key'] = catalog_key
    mat['studio_params'] = json.dumps(p, sort_keys=True)
    mat['studio_lod'] = json.dumps(lod, sort_keys=True)
    mat['studio_textures'] = json.dumps(provenance, sort_keys=True)
    mat['studio_cycles_only'] = json.dumps([x for x, on in (
        ('bevel_edge', p['edge_wear'] > 0 and p['edge_source'] == 'bevel'),
        ('pointiness_edge', p['edge_wear'] > 0 and p['edge_source'] == 'pointiness'),
        ('ao_only_local_grime', p['grime'] > 0)) if on])
    return mat


def assign(obj, material):
    """Put material on every slot of obj (adds one slot when the object has none)."""
    if not material.name.startswith(PREFIX):
        raise ValueError(f'assign expects a {PREFIX}* material, got {material.name!r}')
    if obj.data is None or not hasattr(obj.data, 'materials'):
        raise ValueError(f'object {obj.name!r} cannot hold materials')
    if not obj.material_slots:
        obj.data.materials.append(material)
    for slot in obj.material_slots:
        slot.material = material
    return obj
