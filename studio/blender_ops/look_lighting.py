"""Look lighting: HDRI world + physically scaled lamps + metered AgX exposure (runs inside Blender).

Ported from the photoreal research prototype (04_lighting/lighting.py). Principles; presets only supply numbers:
  * Irradiance, not watts, is the authored quantity. Lamp power is derived from the distance to the
    target (P = E*pi*d^2), so scene scale never changes the look.
  * HDRIs are normalised by their measured horizontal irradiance (after clipping hot spots at
    `env_clip` x mean radiance), so swapping HDRIs keeps the same exposure.
  * The clipped hot spot (the sun) is replaced by an analytic Sun lamp aimed along the measured
    hot-spot direction, with strength from the HDRI's own sun:sky ratio (clamped per preset).
  * Exposure is metered from a tiny deterministic pre-render (subject log-average luminance -> meter_key).
  * Provenance is default-deny: an HDRI is used only if its library record is CC0-1.0, cleared, and
    the file's sha256 equals the recorded one.
  * Everything this module creates is named `StudioLook_*` and tagged `studio_look`; apply first removes
    all of it, so re-applying the same inputs yields the same scene. Camera, geometry and materials are
    never modified; pre-existing lights are hidden from render (not deleted) and restored on removal.
"""
import hashlib
import json
import math
import tempfile
from pathlib import Path

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

from scene_roles import TAG as ROLE_TAG, counts, first_blocking_hit

PRESETS_PATH = Path(__file__).resolve().parent / "look_data" / "lighting_presets.json"
PREFIX = "StudioLook_"
TAG = "studio_look"
HIDDEN_TAG = "studio_look_hidden"
PREV_WORLD = "studio_look_prev_world"
COLLECTION = PREFIX + "Lights"
WORLD = PREFIX + "World"
HDRI_SUFFIXES = (".hdr", ".exr")
EV_STEP = 0.05
DEFAULT_EV_CLAMP = (-4.0, 6.0)
METER_LONG_EDGE_PX = 160  # metering only needs a log-average; keeps it CPU-cheap
METER_SAMPLES = 8
METER_SEED = 0
REC709 = np.array([0.2126, 0.7152, 0.0722])


def _qa_fail(msg):
    return ValueError(f"LOOK_QA_FAILED: {msg}")


def _ours(idblock):
    return idblock is not None and (idblock.name.startswith(PREFIX) or bool(idblock.get(TAG)))


# ---------------------------------------------------------------- provenance
def resolve_hdri(asset_id, library_root):
    """Return {asset_id, version, path, sha256} for a cleared CC0 HDRI whose bytes match its record."""
    asset_dir = Path(library_root) / "assets" / asset_id
    versions = sorted(p for p in asset_dir.glob("v*") if (p / "asset.json").is_file())
    if not versions:
        raise _qa_fail(f"hdri provenance missing for {asset_id!r} (no asset.json under {asset_id}/v*)")
    vdir = versions[-1]
    try:
        record = json.loads((vdir / "asset.json").read_text())
    except (OSError, ValueError) as exc:
        raise _qa_fail(f"hdri provenance unreadable for {asset_id!r}: {exc}") from None
    source = record.get("source") or {}
    if source.get("license_id") != "CC0-1.0" or source.get("use_status") != "cleared":
        raise _qa_fail(f"hdri provenance for {asset_id!r} is not cleared CC0-1.0 "
                       f"(license_id={source.get('license_id')!r}, use_status={source.get('use_status')!r})")
    for entry in record.get("files") or []:
        rel = entry.get("relative_path") or Path(entry.get("path", "")).name
        if not rel.lower().endswith(HDRI_SUFFIXES):
            continue
        # Resolve inside this version dir only (recorded absolute paths may point at another library copy).
        path = next((p for p in (vdir / "original" / rel, vdir / rel) if p.is_file()), None)
        if path is None:
            raise _qa_fail(f"hdri provenance for {asset_id!r}: file {rel!r} missing from {vdir.name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not entry.get("sha256") or digest != entry["sha256"]:
            raise _qa_fail(f"hdri provenance sha256 mismatch for {asset_id!r}; refusing changed bytes")
        return {"asset_id": asset_id, "version": vdir.name, "path": str(path), "sha256": digest}
    raise _qa_fail(f"hdri provenance for {asset_id!r} lists no .hdr/.exr file")


def _pick_hdri(candidates, library_root):
    """First candidate present in the library; a present-but-invalid candidate fails (no silent fallthrough)."""
    for asset_id in candidates:
        if (Path(library_root) / "assets" / asset_id).is_dir():
            return resolve_hdri(asset_id, library_root)
    raise _qa_fail(f"hdri provenance: none of {list(candidates)} exists in the library")


# ---------------------------------------------------------------- HDRI analysis
def analyse_hdri(image, clip_mult):
    """Measure the equirect map in Blender's world frame (strength 1, no rotation)."""
    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    lum = px.reshape(h, w, 4)[..., :3].astype(np.float64) @ REC709  # row 0 = bottom
    lat = ((np.arange(h) + 0.5) / h - 0.5) * math.pi
    lon = (0.5 - (np.arange(w) + 0.5) / w) * 2 * math.pi  # inverse of Blender's equirect mapping
    d_omega = (np.cos(lat) * (2 * math.pi / w) * (math.pi / h))[:, None]
    up = np.clip(np.sin(lat), 0, None)[:, None]
    mean = float((lum * d_omega).sum() / (4 * math.pi))
    clip = clip_mult * mean
    clipped = np.minimum(lum, clip)
    hot = lum > clip
    e_sky = float((clipped * up * d_omega).sum())  # horizontal irradiance of the clipped map
    e_hot = float(((lum - clip) * hot * d_omega).sum())  # normal irradiance of the removed hot spot
    sun_dir = None
    if hot.any():
        weights = (lum - clip) * hot * d_omega
        LAT, LON = np.meshgrid(lat, lon, indexing="ij")
        v = np.stack([np.cos(LAT) * np.cos(LON), np.cos(LAT) * np.sin(LON), np.sin(LAT)], -1)
        sun_dir = Vector((v * weights[..., None]).sum((0, 1))).normalized()
    return {"clip": clip, "e_sky_horizontal": e_sky, "e_hot_normal": e_hot, "sun_dir_world": sun_dir}


# ---------------------------------------------------------------- scene helpers
def _target_bounds(scene, camera, target):
    """Target = given object(s), else bbox of renderable meshes whose origin is in the camera frustum."""
    # scene_roles 'bounds': an earth shell or fog volume around the set is not what is being lit
    meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render and not _ours(o) and counts(o, "bounds")]
    if target is not None:
        objs = target if isinstance(target, (list, tuple)) else [target]
        objs = [bpy.data.objects[o] if isinstance(o, str) else o for o in objs]
    else:
        objs = []
        for o in meshes:
            p = world_to_camera_view(scene, camera, o.matrix_world.translation)
            if 0 <= p.x <= 1 and 0 <= p.y <= 1 and p.z > 0:
                objs.append(o)
        objs = objs or meshes
    if not objs:
        raise _qa_fail("lighting target: no renderable mesh to light")
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if target is None:  # scattered instances in view count too (their host's bound_box is empty)
        from scene_geometry import instance_boxes
        for _host, lo_i, hi_i in instance_boxes(bpy.context.evaluated_depsgraph_get(), keep=lambda h: counts(h, "bounds")):
            centre = Vector((lo_i + hi_i) / 2)
            p = world_to_camera_view(scene, camera, centre)
            if 0 <= p.x <= 1 and 0 <= p.y <= 1 and p.z > 0:
                pts += [Vector(lo_i), Vector(hi_i)]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return (lo + hi) / 2, lo, hi


CEILING_GAP_M = 0.3      # practicals hang this far under the ceiling found above the target centre
FIXTURE_DROP_M = 3.0     # emissive_to_area: irradiance is defined this far under the fixture


def _ceiling_above(scene, center, top):
    """z of the first blocking surface straight above the target centre, if it is below the bounds top."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hit, location, *_ = first_blocking_hit(scene, depsgraph, center.copy(), Vector((0, 0, 1)), max(top - center.z, 0.0) + 1e-3)
    return location.z if hit else None


def _fixture_lights(scene, col):
    """Area lights under every 'light_fixture' (scene_roles) mesh, sized to its footprint; power from the
    fixture material's emission strength (W/m2 ~ strength), camera-invisible."""
    out = []
    for o in sorted((o for o in scene.objects if o.type == "MESH" and not o.hide_render and o.get(ROLE_TAG) == "light_fixture"), key=lambda o: o.name):
        pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
        lo = Vector([min(p[i] for p in pts) for i in range(3)]); hi = Vector([max(p[i] for p in pts) for i in range(3)])
        strength = 0.0
        for slot in o.material_slots:
            nodes = slot.material.node_tree.nodes if slot.material and slot.material.node_tree else []
            bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf:
                strength = max(strength, float(bsdf.inputs["Emission Strength"].default_value))
        if strength <= 0:
            continue
        area = max((hi.x - lo.x) * (hi.y - lo.y), 1e-4)
        at = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z - 0.01))
        light = _add_light(col, "fixture_" + o.name.replace("/", "_"), "AREA", at, at - Vector((0, 0, 1)), strength * area, 4000.0,
                           size=max(hi.x - lo.x, 0.05), shape="RECTANGLE")
        light.data.size_y = max(hi.y - lo.y, 0.05)
        light.visible_camera = False
        out.append((strength * area / (math.pi * FIXTURE_DROP_M ** 2), 4000.0, light))
    return out


def _camera_relative_dir(camera, center, azimuth, elevation):
    to_cam = camera.matrix_world.translation - center
    a = math.atan2(to_cam.y, to_cam.x) + math.radians(azimuth)
    e = math.radians(elevation)
    return Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))


def _add_light(col, name, kind, location, aim_at, power, temperature, size=None, shape=None, spread=None, angle=None):
    data = bpy.data.lights.new(PREFIX + name, kind)
    data.use_temperature = True
    data.temperature = temperature
    data.color = (1, 1, 1)
    data.energy = power
    if kind == "AREA":
        data.shape = shape or "SQUARE"
        data.size = size
        if spread is not None:
            data.spread = math.radians(spread)
    if kind == "SUN":
        data.angle = math.radians(angle)
    data[TAG] = True
    obj = bpy.data.objects.new(PREFIX + name, data)
    col.objects.link(obj)
    obj.location = location
    direction = Vector(location) - Vector(aim_at)
    obj.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()  # lamp emits along -Z
    obj[TAG] = True
    return obj


ATMOSPHERE_PAD_M = 1.0   # the fog box extends this far past the lit bounds


def _atmosphere(col, spec, lo, hi):
    """Opt-in fog + light beams (shot.render.atmosphere): a Principled Volume box over the lit bounds and spot
    lights shaped into shafts. Tagged studio_scene_role 'atmosphere', so control passes, clay, metering, ray
    casts and reveals ignore it (scene_roles). Returns (objects, lights, report)."""
    import bmesh
    objects, lights = [], []
    if spec.get("box"):  # confine the fog (an atrium, a shaft): fog over a whole site reads as haze, not beams
        lo_p, hi_p = Vector(spec["box"][0]), Vector(spec["box"][1])
    else:
        pad = Vector((ATMOSPHERE_PAD_M,) * 3)
        lo_p, hi_p = lo - pad, hi + pad
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=hi_p - lo_p, verts=bm.verts)
    mesh = bpy.data.meshes.new(PREFIX + "Fog"); bm.to_mesh(mesh); bm.free(); mesh[TAG] = True
    material = bpy.data.materials.new(PREFIX + "Fog"); material[TAG] = True
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.remove(nodes.get("Principled BSDF"))
    volume = nodes.new("ShaderNodeVolumePrincipled")
    volume.inputs["Density"].default_value = float(spec.get("density", 0.01))
    colour = spec.get("color_srgb", [1.0, 1.0, 1.0])
    volume.inputs["Color"].default_value = (*colour, 1.0)
    volume.inputs["Anisotropy"].default_value = float(spec.get("anisotropy", 0.3))  # forward scatter makes beams read
    material.node_tree.links.new(volume.outputs["Volume"], nodes.get("Material Output").inputs["Volume"])
    mesh.materials.append(material)
    fog = bpy.data.objects.new(PREFIX + "Fog", mesh)
    col.objects.link(fog)
    fog.location = (lo_p + hi_p) / 2
    fog[TAG] = True
    fog["studio_scene_role"] = "atmosphere"
    objects.append(fog)
    for i, beam in enumerate(spec.get("beams", [])):
        light = _add_light(col, f"beam_{i}", "SPOT", Vector(beam["location"]), Vector(beam["aim"]), float(beam.get("power_w", 2000.0)),
                           float(beam.get("temperature_k", 5500.0)))
        light.data.spot_size = math.radians(float(beam.get("spread_deg", 20.0)))
        light.data.spot_blend = float(beam.get("blend", 0.3))
        light["studio_scene_role"] = "atmosphere"
        lights.append(light)
    return objects, lights, {"fog_density": volume.inputs["Density"].default_value, "fog_box": [list(lo_p), list(hi_p)],
                             "beams": len(lights)}


def _named_node(nt, kind, name):
    node = nt.nodes.new(kind)
    node.name = node.label = PREFIX + name
    return node


SKY_NODE = "CameraSky"
EXPOSURE_KEYS = PREFIX + "exposure_keys"   # [{frame, delta_ev}] read by look_camera.compositor_setup


def _camera_sky(nt, sky):
    """Background the camera sees: a vertical gradient over the view direction's height (horizon -> zenith),
    colours in sRGB. Its strength is set after metering (2^-EV) so it reads the same at any exposure."""
    coord = _named_node(nt, "ShaderNodeTexCoord", "SkyCoord")
    split = _named_node(nt, "ShaderNodeSeparateXYZ", "SkySplit")
    nt.links.new(coord.outputs["Generated"], split.inputs[0])
    ramp = _named_node(nt, "ShaderNodeValToRGB", "SkyRamp")
    stops = sky["stops"]   # [[height (view z), [r, g, b] sRGB], ...] from below the horizon to the zenith
    elements = ramp.color_ramp.elements
    lo, hi = stops[0][0], stops[-1][0]
    for i, (h, rgb) in enumerate(stops):
        pos = (h - lo) / (hi - lo)
        e = elements[i] if i < len(elements) else elements.new(pos)
        e.position = pos
        e.color = (*[_srgb_to_linear(c) for c in rgb], 1.0)
    remap = _named_node(nt, "ShaderNodeMapRange", "SkyRange")
    remap.inputs["From Min"].default_value, remap.inputs["From Max"].default_value = lo, hi
    nt.links.new(split.outputs["Z"], remap.inputs["Value"])
    nt.links.new(remap.outputs["Result"], ramp.inputs["Fac"])
    bg = _named_node(nt, "ShaderNodeBackground", SKY_NODE)
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    return bg


def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _build_world(image, strength, clip, rotation_z, hide_from_camera, backdrop, camera_sky=None):
    world = bpy.data.worlds.new(WORLD)
    world[TAG] = True
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = _named_node(nt, "ShaderNodeOutputWorld", "Output")
    bg = _named_node(nt, "ShaderNodeBackground", "HDRI")
    bg.inputs["Strength"].default_value = strength
    coord = _named_node(nt, "ShaderNodeTexCoord", "Coord")
    mapping = _named_node(nt, "ShaderNodeMapping", "Mapping")
    mapping.inputs["Rotation"].default_value[2] = rotation_z
    env = _named_node(nt, "ShaderNodeTexEnvironment", "Environment")
    env.image = image
    cmin = _named_node(nt, "ShaderNodeVectorMath", "Clip")  # remove the hot spot the Sun lamp replaces
    cmin.operation = "MINIMUM"
    cmin.inputs[1].default_value = (clip, clip, clip)
    nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], cmin.inputs[0])
    nt.links.new(cmin.outputs["Vector"], bg.inputs["Color"])
    final = bg.outputs["Background"]
    if hide_from_camera or camera_sky:  # camera sees the backdrop (or the sky gradient); reflections/GI still see the HDRI
        path = _named_node(nt, "ShaderNodeLightPath", "LightPath")
        if camera_sky:
            flat = _camera_sky(nt, camera_sky)
        else:
            flat = _named_node(nt, "ShaderNodeBackground", "Backdrop")
            flat.inputs["Color"].default_value = (*backdrop[:3], 1.0)
            flat.inputs["Strength"].default_value = backdrop[3]
        mix = _named_node(nt, "ShaderNodeMixShader", "CameraMix")
        nt.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
        nt.links.new(final, mix.inputs[1])
        nt.links.new(flat.outputs["Background"], mix.inputs[2])
        final = mix.outputs["Shader"]
    nt.links.new(final, out.inputs["Surface"])
    return world


def _backdrop_of(world):
    try:
        bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
        return (*bg.inputs["Color"].default_value[:3], bg.inputs["Strength"].default_value)
    except Exception:
        return (0.05, 0.05, 0.05, 1.0)


# ---------------------------------------------------------------- metering
_METER_ATTRS = (
    (lambda s: s.render, ("engine", "resolution_x", "resolution_y", "resolution_percentage", "filepath",
                          "film_transparent", "use_compositing", "use_sequencer")),
    (lambda s: s.render.image_settings, ("file_format", "color_depth", "color_mode")),
    (lambda s: s.cycles, ("device", "samples", "seed", "use_animated_seed", "use_adaptive_sampling", "use_denoising")),
    (lambda s: s.view_settings, ("exposure",)),
)


def meter_exposure(scene, key, clamp_ev, highlight=None):
    """Subject log-average luminance (alpha>0.5 in a transparent-film pre-render) -> AgX EV.
    Deterministic: Cycles on CPU, seed 0, fixed sample count and size; EV rounded to EV_STEP, clamped.
    Every render setting touched is restored, also on error."""
    saved = [(get, {a: getattr(get(scene), a) for a in attrs}) for get, attrs in _METER_ATTRS]
    r = scene.render
    # Volumes and helpers (scene_roles 'bounds' False) stay in the light transport but out of the meter's view.
    unseen = [o for o in scene.objects if o.type == "MESH" and o.visible_camera and not counts(o, "bounds")]
    for o in unseen:
        o.visible_camera = False
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir) / "meter.exr"
            r.engine = "CYCLES"
            aspect = (saved[0][1]["resolution_x"], saved[0][1]["resolution_y"])
            k = METER_LONG_EDGE_PX / max(aspect)
            r.resolution_x, r.resolution_y = (max(4, round(v * k)) for v in aspect)
            r.resolution_percentage = 100
            r.film_transparent = True
            r.use_compositing = False
            r.use_sequencer = False
            r.filepath = str(tmp)
            c = scene.cycles
            c.device, c.samples, c.seed = "CPU", METER_SAMPLES, METER_SEED
            c.use_animated_seed = c.use_adaptive_sampling = c.use_denoising = False
            ims = r.image_settings
            ims.file_format, ims.color_depth, ims.color_mode = "OPEN_EXR", "32", "RGBA"
            scene.view_settings.exposure = 0.0
            bpy.ops.render.render(write_still=True, scene=scene.name)
            img = bpy.data.images.load(str(tmp))
            try:
                px = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
                img.pixels.foreach_get(px)
            finally:
                bpy.data.images.remove(img)
    finally:
        for get, values in saved:
            for attr, value in values.items():
                setattr(get(scene), attr, value)
        for o in unseen:
            o.visible_camera = True
    px = px.reshape(-1, 4).astype(np.float64)
    subject = px[:, 3] > 0.5
    px = px[subject] if subject.mean() > 0.02 else px  # empty frame: fall back to everything
    lum = (px[:, :3] / np.maximum(px[:, 3:4], 1e-6)) @ REC709
    logavg = float(np.exp(np.mean(np.log(np.maximum(lum, 1e-4)))))
    ev = math.log2(key / max(logavg, 1e-6))
    if highlight:  # highlight priority: the brightest share of the subject may not pass `max_linear` after exposure
        top = float(np.percentile(lum, highlight["percentile"]))
        ev = min(ev, math.log2(highlight["max_linear"] / max(top, 1e-6)))
    ev = min(max(ev, clamp_ev[0]), clamp_ev[1])
    return round(round(ev / EV_STEP) * EV_STEP, 2)


# ---------------------------------------------------------------- removal
def _remove_ours(scene):
    """Delete every StudioLook_ object/light/collection/world/image; keep hidden-light flags and prev world."""
    for obj in [o for o in bpy.data.objects if _ours(o)]:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Light):
            bpy.data.lights.remove(data)
    for light in [l for l in bpy.data.lights if _ours(l) and l.users == 0]:
        bpy.data.lights.remove(light)
    for col in [c for c in bpy.data.collections if _ours(c)]:
        bpy.data.collections.remove(col)
    stale = [w for w in bpy.data.worlds if _ours(w)]
    if scene.world in stale:
        prev = scene.get(PREV_WORLD)
        scene.world = bpy.data.worlds.get(prev) if prev else None
    for world in stale:
        bpy.data.worlds.remove(world)
    for img in [i for i in bpy.data.images if _ours(i) and i.users == 0]:
        bpy.data.images.remove(img)
    for mesh in [m for m in bpy.data.meshes if _ours(m) and m.users == 0]:  # atmosphere fog box
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if _ours(m) and m.users == 0]:
        bpy.data.materials.remove(material)


def remove_lighting(scene):
    """Undo apply_lighting: remove StudioLook_ items, unhide original lights, restore a plain world,
    reset exposure/look/white balance (view transform is left as is)."""
    _remove_ours(scene)
    for o in scene.objects:
        if o.get(HIDDEN_TAG):
            o.hide_render = False
            del o[HIDDEN_TAG]
    if scene.world is None:  # no previous world recorded: plain neutral background
        world = bpy.data.worlds.new("World")
        world.use_nodes = True
        bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
        bg.inputs["Color"].default_value = (0.05, 0.05, 0.05, 1.0)
        bg.inputs["Strength"].default_value = 1.0
        scene.world = world
    if PREV_WORLD in scene:
        if scene.world.name == scene[PREV_WORLD]:
            scene.world.use_fake_user = False  # set by apply_lighting; the scene is a real user again
        del scene[PREV_WORLD]
    vs = scene.view_settings
    vs.exposure = 0.0
    vs.look = "None"
    vs.use_white_balance = False
    if EXPOSURE_KEYS in scene:
        del scene[EXPOSURE_KEYS]


# ---------------------------------------------------------------- main entry
def apply_lighting(scene, preset_name, *, library_root, style_light_rig=None, style_world=None, camera=None, target=None, atmosphere=None,
                   exposure_keys=None):
    """exposure_keys: [{frame, transition_frames}] - frames metered on their own (a shot that goes from a night street
    into a lit interior); the deltas are keyed on a compositor Exposure node (look_camera), never on view_settings, so
    control and graphics passes stay at their own exposure."""
    rig_style = dict(style_light_rig or {})
    world_style = dict(style_world or {})
    warnings = []
    if rig_style.get("preset") and preset_name and rig_style["preset"] != preset_name:
        warnings.append(f"style_light_rig.preset {rig_style['preset']!r} overrides {preset_name!r}")
    preset = rig_style.get("preset") or preset_name
    spec = json.loads(PRESETS_PATH.read_text())["presets"].get(preset)
    if spec is None:
        raise ValueError(f"LOOK_QA_FAILED: unknown lighting preset {preset!r}")
    camera = camera or scene.camera
    if camera is None:
        raise _qa_fail("lighting needs a camera (camera-relative rig and metering)")

    # Provenance first: a refused HDRI must leave the scene untouched.
    candidates = [world_style["hdri_asset_id"]] if world_style.get("hdri_asset_id") else spec["hdri"]
    hdri = _pick_hdri(candidates, library_root)

    _remove_ours(scene)  # idempotency: rebuild from scratch
    if scene.world is not None and PREV_WORLD not in scene:
        scene[PREV_WORLD] = scene.world.name
        scene.world.use_fake_user = True
    backdrop = _backdrop_of(scene.world) if scene.world else (0.05, 0.05, 0.05, 1.0)
    kept = []
    for o in scene.objects:
        if o.type == "LIGHT" and not o.hide_render:
            if o.get("studio_keep_light"):  # author's practical, kept on purpose (metering sees it)
                kept.append(o.name)
                continue
            o[HIDDEN_TAG] = True
            o.hide_render = True
    center, lo, hi = _target_bounds(scene, camera, target)
    radius = max((hi - lo).length / 2, 0.05)
    col = bpy.data.collections.new(COLLECTION)
    col[TAG] = True
    scene.collection.children.link(col)

    # world
    image = bpy.data.images.load(hdri["path"], check_existing=False)
    image.name = PREFIX + "HDRI_" + hdri["asset_id"]
    image[TAG] = True
    analysis = analyse_hdri(image, spec["env_clip"])
    sun_spec = spec.get("sun") or {}
    if rig_style.get("hdri_rotation_deg") is not None:
        rot = math.radians(float(rig_style["hdri_rotation_deg"]))
    elif sun_spec.get("aim_camera_azimuth_deg") is not None and analysis["sun_dir_world"] is not None:
        # rotate the HDRI so its sun sits at a camera-relative azimuth (key side), whatever the map
        sd = analysis["sun_dir_world"]
        want = _camera_relative_dir(camera, center, sun_spec["aim_camera_azimuth_deg"], 0)
        rot = math.atan2(sd.y, sd.x) - math.atan2(want.y, want.x)
    else:
        rot = 0.0
    strength = spec["env_irradiance"] / max(analysis["e_sky_horizontal"], 1e-9)
    hide = spec["hide_hdri_from_camera"] if world_style.get("hide_from_camera") is None else bool(world_style["hide_from_camera"])
    scene.world = _build_world(image, strength, analysis["clip"], rot, hide, backdrop, camera_sky=spec.get("camera_sky"))

    sources = []  # (irradiance at target, temperature) for white balance
    lights = []
    if spec.get("sun"):
        sun = spec["sun"]
        if analysis["sun_dir_world"] is not None and sun.get("from_hdri"):
            d = Matrix.Rotation(-rot, 3, "Z") @ analysis["sun_dir_world"]  # mapping looks up Rz(rot)*world
            ratio = analysis["e_hot_normal"] / max(analysis["e_sky_horizontal"], 1e-9)
        else:
            d, ratio = None, sun["fallback_ratio"]
        if d is None or d.z < 0.05:  # sun missing or at/below horizon: authored fallback direction
            if d is not None:
                warnings.append("hdri sun at/below horizon; fallback sun direction used")
            a, e = math.radians(sun["fallback_azimuth_deg"]), math.radians(sun["fallback_elevation_deg"])
            d = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        lo_r, hi_r = sun["sun_to_sky_ratio_range"]
        sun_e = spec["env_irradiance"] * min(max(ratio, lo_r), hi_r)
        lights.append(_add_light(col, "sun", "SUN", center + d * 10, center, sun_e, sun["temperature_k"], angle=sun["angle_deg"]))
        sources.append((sun_e, sun["temperature_k"]))

    key_e = None
    for item in spec.get("rig", []):  # camera-relative rig; irradiance at the target is authored
        e = item.get("irradiance")
        if e is None:
            e = key_e * item["irradiance_ratio_of_key"]
        if item["name"] == "key":
            key_e = e
        dist = max(radius * 3.0, 0.5)
        pos = center + _camera_relative_dir(camera, center, item["azimuth_deg"], item["elevation_deg"]) * dist
        lights.append(_add_light(col, item["name"], "AREA", pos, center, e * math.pi * dist ** 2, item["temperature_k"],
                                 size=max(radius * item["size_factor"], 0.05)))
        sources.append((e, item["temperature_k"]))

    prac = spec.get("practicals")
    ceiling = None
    if prac:  # fixture grid hanging at height_fraction of the target bbox, pointing down
        nx, ny = prac["grid"]
        z = lo.z + (hi.z - lo.z) * prac["height_fraction"]
        ceiling = _ceiling_above(scene, center, hi.z)
        if ceiling is not None and ceiling < z:  # an interior: hang the fixtures under its ceiling, not above it
            z = max(ceiling - CEILING_GAP_M, lo.z + 0.5)
        drop = max(z - lo.z, 0.5)
        for i in range(nx):
            for j in range(ny):
                x = lo.x + (hi.x - lo.x) * (i + 0.5) / nx
                y = lo.y + (hi.y - lo.y) * (j + 0.5) / ny
                lights.append(_add_light(col, f"practical_{i}_{j}", "AREA", Vector((x, y, z)), Vector((x, y, z - 1)),
                                         prac["irradiance"] * math.pi * drop ** 2, prac["temperature_k"],
                                         size=max(radius * prac["size_factor"], 0.1), shape=prac["shape"],
                                         spread=prac.get("spread_deg")))
        sources.append((prac["irradiance"], prac["temperature_k"]))

    atmosphere_report = None
    if atmosphere:  # opt-in fog and beams; built after the bounds so it never inflates them
        _objs, beams, atmosphere_report = _atmosphere(col, atmosphere, lo, hi)
        lights += beams

    if rig_style.get("emissive_to_area"):  # opt-in: self-lit fixtures also light the scene as area lights
        for e, t, light in _fixture_lights(scene, col):
            lights.append(light)
            sources.append((e, t))

    # Camera white balance: style > preset pin > irradiance-weighted mired mean of the authored sources.
    lamps = [(e, t) for e, t in sources if e > 0]
    if rig_style.get("white_balance_k") is not None:
        wb = float(rig_style["white_balance_k"])
    elif spec.get("white_balance_k"):
        wb = float(spec["white_balance_k"])
    elif lamps:
        wb = 1e6 / (sum(e * 1e6 / t for e, t in lamps) / sum(e for e, _ in lamps))
    else:
        wb = 6500.0
    wb = round(wb, 1)
    vs = scene.view_settings
    vs.view_transform = "AgX"
    vs.look = spec["look"]
    vs.use_white_balance = True
    vs.white_balance_temperature = wb

    clamp = spec.get("exposure_clamp_ev", DEFAULT_EV_CLAMP)
    if rig_style.get("exposure_ev") is not None:
        ev, ev_source = float(rig_style["exposure_ev"]), "style"
    else:
        ev = meter_exposure(scene, spec["meter_key"], clamp, spec.get("meter_highlight"))
        ev_source = "metered"
    segments = []
    if exposure_keys and ev_source == "metered":   # meter every key frame before anything is keyed
        current = scene.frame_current
        for key in exposure_keys:
            scene.frame_set(key["frame"] + 1)
            seg_ev = meter_exposure(scene, spec["meter_key"], clamp, spec.get("meter_highlight"))
            segments.append({"frame": key["frame"], "ev": seg_ev, "delta_ev": round(seg_ev - ev, 2), "transition_frames": key["transition_frames"]})
        scene.frame_set(current)
        scene[EXPOSURE_KEYS] = json.dumps(segments)
    vs.exposure = ev
    sky = scene.world.node_tree.nodes.get(PREFIX + SKY_NODE) if spec.get("camera_sky") else None
    if sky is not None:   # the sky reads at its designed level whatever the exposure
        sky.inputs["Strength"].default_value = spec["camera_sky"].get("level", 1.0) * 2.0 ** -ev

    return {"preset": preset, "hdri_asset_id": hdri["asset_id"], "hdri_sha256": hdri["sha256"],
            "exposure_ev": ev, "ev_source": ev_source, "exposure_segments": segments, "camera_sky": bool(sky), "white_balance_k": wb,
            "view_transform": vs.view_transform, "look": vs.look,
            "lights": sorted(o.name for o in lights), "kept_author_lights": sorted(kept),
            "practical_ceiling_z": None if ceiling is None else round(ceiling, 3), "atmosphere": atmosphere_report, "warnings": warnings}
