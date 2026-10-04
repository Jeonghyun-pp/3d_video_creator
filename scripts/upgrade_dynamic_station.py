"""Upgrade the existing conceptual station with CC0 PBR maps and shot detail."""

import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects/dynamic_station_pilot"
ASSETS = PROJECT / "assets/cc0"
DEST = Path(sys.argv[sys.argv.index("--") + 1]).resolve()


def collection(name):
    result = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(result)
    return result


def material(name, color, metallic=0, roughness=.5, emission=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1)
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def pbr(name, asset, scale=1, metallic=None):
    mat = bpy.data.materials[name]
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    for node in list(nodes):
        if node != bsdf and node.type != "OUTPUT_MATERIAL":
            nodes.remove(node)
    coords = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeVectorMath")
    mapping.operation = "SCALE"
    mapping.inputs[3].default_value = scale
    links.new(coords.outputs["Object"], mapping.inputs[0])
    textures = {}
    for kind in ("Diffuse", "Rough", "nor_gl"):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(ASSETS / f"{asset}_{kind}.jpg"), check_existing=True)
        tex.projection = "BOX"
        tex.projection_blend = .18
        tex.extension = "REPEAT"
        if kind != "Diffuse":
            tex.image.colorspace_settings.name = "Non-Color"
        links.new(mapping.outputs[0], tex.inputs["Vector"])
        textures[kind] = tex
    if name != "Brushed steel":
        links.new(textures["Diffuse"].outputs["Color"], bsdf.inputs["Base Color"])
    links.new(textures["Rough"].outputs["Color"], bsdf.inputs["Roughness"])
    normal = nodes.new("ShaderNodeNormalMap")
    normal.inputs["Strength"].default_value = .12 if name == "Brushed steel" else .35
    links.new(textures["nor_gl"].outputs["Color"], normal.inputs["Color"])
    links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
    if metallic is not None:
        bsdf.inputs["Metallic"].default_value = metallic


def box(coll, name, pos, size, mat, bevel=.01, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    coll.objects.link(obj)
    obj.data.materials.append(mat)
    if bevel:
        mod = obj.modifiers.new("Light-catching edges", "BEVEL")
        mod.width = bevel
        mod.segments = 2
    if parent:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    return obj


def rod(coll, name, a, b, radius, mat, parent=None):
    start, end = Vector(a), Vector(b)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=radius, depth=(end-start).length,
                                        location=(start+end)/2)
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = (end-start).to_track_quat("Z", "Y").to_euler()
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    coll.objects.link(obj)
    obj.data.materials.append(mat)
    if parent:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    return obj


def sign(coll, name, text, pos, size, mat, parent=None):
    curve = bpy.data.curves.new(name, "FONT")
    curve.body = text
    curve.size = size
    curve.extrude = .002
    obj = bpy.data.objects.new(name, curve)
    coll.objects.link(obj)
    obj.location = pos
    obj.rotation_euler.x = math.pi / 2
    obj.data.materials.append(mat)
    if parent:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    return obj


def camera_key(obj, frame, pos):
    obj.location = pos
    obj.keyframe_insert(data_path="location", frame=frame)


def main():
    for name, asset, scale, metal in (
        ("Warm cast concrete", "concrete_wall_004", .85, 0),
        ("Fresh concrete cut", "concrete_wall_004", .9, 0),
        ("Station porcelain tile", "concrete_floor_01", 1.4, 0),
        ("Asphalt", "asphalt_02", 1.6, 0),
        ("Brown earth", "dirt_floor", .6, 0),
        ("Brushed steel", "metal_plate", 1.7, .75),
    ):
        pbr(name, asset, scale, metal)

    detail = collection("Art direction details")
    train = collection("Train finish details")
    station = collection("Station finish details")
    white = material("Lettering white", (.82, .9, .9), roughness=.6)
    red = material("Accent vermilion", (.68, .055, .035), roughness=.4, emission=.12)
    dark = material("Rubber and gaps", (.025, .034, .04), roughness=.82)
    glass_glint = material("Glass reflection", (.11, .19, .23), metallic=.25, roughness=.2)
    brushed = bpy.data.materials["Brushed steel"]
    concrete = bpy.data.materials["Fresh concrete cut"]
    light = material("Interior luminous strip", (.83, .9, 1), roughness=.4, emission=2.2)
    train_paint = bpy.data.materials["Train pearl silver"]
    root = bpy.data.objects["Train motion root"]

    # The old stick figures and spherical trees made the close views look like a toy set.
    for obj in list(bpy.data.objects):
        if obj.name.startswith(("Person legs", "Person torso", "Sphere", "Street tree trunk", "Icosphere",
                                "Mid-rise building", "Facade glass", "Window sill", "Mullion")):
            bpy.data.objects.remove(obj, do_unlink=True)

    # Facade panel boundaries, recessed doors, rubber gaskets and service markings.
    for x in (-2.75, -1.45, 0, 1.45, 2.75):
        box(train, "Car body joint", (x, -2.215, -.48), (.017, .025, 1.0), brushed, .002, root)
    for cx in (-1.15, 1.15):
        for offset in (-.26, .26):
            box(train, "Door leaf outline", (cx+offset, -2.225, -.08), (.018, .025, 1.08), dark, .002, root)
        box(train, "Door threshold", (cx, -2.236, -.64), (.56, .025, .035), brushed, .003, root)
        for dx in (-.36, .36):
            box(train, "Door handhold", (cx+dx, -2.245, -.16), (.018, .02, .22), brushed, .004, root)
    for x in (-2.55, -1.63, -.72, .72, 1.63, 2.55):
        for z in (-.17, .39):
            box(train, "Window gasket", (x, -2.23, z), (.7, .018, .02), dark, .003, root)
        for dx in (-.35, .35):
            box(train, "Window frame", (x+dx, -2.231, .10), (.018, .018, .56), brushed, .003, root)
        glint = box(train, "Window light reflection", (x-.18, -2.238, .10),
                    (.026, .008, .38), glass_glint, .004, root)
        glint.rotation_euler.y = math.radians(15)
    box(train, "Roof ventilation housing", (0, -1.28, .78), (2.7, 1.0, .17), brushed, .04, root)
    for x in [i*.13-1.15 for i in range(19)]:
        box(train, "Roof ventilation slat", (x, -1.29, .875), (.035, .78, .018), dark, .004, root)
    for x in (-2.15, 2.15):
        box(train, "Undercarriage gearbox", (x, -1.3, -.85), (.85, 1.0, .21), dark, .045, root)
        for y in (-1.85, -.8):
            rod(train, "Wheel axle", (x, y, -1.01), (x, y+.3, -1.01), .09, brushed, root)
    for x in (-2.8, 2.8):
        box(train, "Service hatch", (x, -2.223, -.45), (.38, .021, .26), train_paint, .012, root)
        for dx in (-.15, .15):
            box(train, "Hatch fastener", (x+dx, -2.242, -.45), (.018, .01, .018), dark, .002, root)
    sign(train, "Car line marking", "METRO 01", (-.43, -2.262, .48), .12, dark, root)
    sign(train, "Car number", "01-214", (2.27, -2.265, -.58), .095, dark, root)
    box(train, "Front signal band", (3.17, -1.32, -.48), (.02, 1.16, .045), red, .004, root)

    # Give the cut edges and station interior believable construction detail.
    for z in (2.39, 2.52, 4.61, 4.74, 1.47, 1.62):
        rod(station, "Exposed slab reinforcement", (-3.85, -2.58, z),
            (3.85, -2.58, z), .023, dark)
    for x in [i*.33-3.63 for i in range(23)]:
        rod(station, "Rebar tie", (x, -2.6, 2.38), (x, -2.6, 2.55), .012, dark)
    for z in (.34, .68, 3.35, 3.8):
        rod(station, "Rear wall utility conduit", (-3.85, 2.31, z),
            (3.85, 2.31, z), .025, brushed)
    for x in (-3.1, -1.0, 1.0, 3.1):
        box(station, "Linear light reflector", (x, .8, 1.42), (.23, 1.8, .045), brushed, .009)
        box(station, "Linear light diffuser", (x, .8, 1.385), (.12, 1.68, .025), light, .006)
    for x in (-3.45, -1.15, 1.15, 3.45):
        for z in (.32, 1.05, 3.05, 4.0):
            box(station, "Column expansion joint", (x, 1.1, z), (.265, .012, .018), dark, .003)
    sign(station, "Platform wayfinding", "1  METRO  |  EXIT", (.95, 2.235, .74), .13, white)
    for x in [i*.12-3.7 for i in range(62)]:
        box(station, "Tactile paving rib", (x, -.39, -.219), (.035, .12, .009),
            bpy.data.materials["Platform safety yellow"], .003)
    for x in (-3.8, 3.8):
        rod(detail, "Section outline", (x, -2.72, -3.0), (x, -2.72, 4.65), .025, red)
    for z in (-3.0, 4.65):
        rod(detail, "Section outline", (-3.8, -2.72, z), (3.8, -2.72, z), .025, red)

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 32
    scene.cycles.use_denoising = True
    scene.cycles.device = "GPU"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    scene.render.resolution_x = 1080
    scene.render.resolution_y = 1920
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "AgX"
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .35
    camera = scene.camera
    target = bpy.data.objects["Camera target"]
    for frame, lens in ((1, 35), (48, 38), (49, 39), (120, 42), (121, 68), (192, 72)):
        camera.data.lens = lens
        camera.data.keyframe_insert(data_path="lens", frame=frame)
    for frame, pos, aim in (
        (1, (13, -21, 10), (0, 0, 1.3)),
        (48, (10, -17, 8), (0, 0, 1.3)),
        (49, (9.0, -15.5, 5.8), (0, 0, 1.15)),
        (120, (8.0, -14.2, 5.35), (0, 0, 1.0)),
        (121, (6.9, -11.5, 1.6), (0, -1.0, 1.0)),
        (192, (6.2, -10.3, 1.45), (0, -1.0, 1.0)),
    ):
        camera_key(camera, frame, pos)
        camera_key(target, frame, aim)
    bpy.ops.file.pack_all()
    DEST.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(DEST))
    print(f"UPGRADED {DEST} objects={len(bpy.data.objects)}")


main()
