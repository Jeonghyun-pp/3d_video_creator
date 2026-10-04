"""Build an illustrative, fully 3D metro cutaway for the motion quality pilot."""

import math
import os
from pathlib import Path
import sys

import bpy
from mathutils import Vector


DESTINATION = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
FPS = int(os.environ.get("STUDIO_FPS", "24"))
SECONDS = 8


def material(name, color, metallic=0, roughness=.65, texture=.0, emission=0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1)
        bsdf.inputs["Emission Strength"].default_value = emission
    if texture:
        noise = nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = texture
        noise.inputs["Detail"].default_value = 3
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = .18
        bump.inputs["Distance"].default_value = .035
        mat.node_tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        mat.node_tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def group(name):
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    return coll


def linked(obj, coll, mat, bevel=0):
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    coll.objects.link(obj)
    if mat:
        obj.data.materials.append(mat)
    if bevel:
        mod = obj.modifiers.new("Edge highlights", "BEVEL")
        mod.width = bevel
        mod.segments = 2
        mod.affect = "EDGES"
    return obj


def box(coll, name, loc, size, mat, bevel=.015, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    linked(obj, coll, mat, bevel)
    if parent:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    return obj


def cylinder(coll, name, loc, radius, depth, mat, vertices=16, rotation=None, parent=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=loc,
                                        rotation=rotation or (0, 0, 0))
    obj = bpy.context.object
    obj.name = name
    linked(obj, coll, mat)
    if parent:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    return obj


def beam(coll, name, first, last, radius, mat, vertices=12):
    first, last = Vector(first), Vector(last)
    center = (first + last) / 2
    obj = cylinder(coll, name, center, radius, (last - first).length, mat, vertices)
    obj.rotation_euler = (last - first).to_track_quat("Z", "Y").to_euler()
    return obj


def curve(coll, name, points, radius, mat):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = radius
    data.bevel_resolution = 3
    spline = data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, co in zip(spline.points, points):
        point.co = (*co, 1)
    obj = bpy.data.objects.new(name, data)
    coll.objects.link(obj)
    data.materials.append(mat)
    return obj


def area(name, loc, energy, size, color, target):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.shape = "RECTANGLE"
    data.size = size[0]
    data.size_y = size[1]
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def animate(obj, positions):
    for frame, location in positions:
        obj.location = location
        obj.keyframe_insert(data_path="location", frame=frame)


def main():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    concrete = material("Warm cast concrete", (.47, .49, .46), roughness=.83, texture=6)
    cut_concrete = material("Fresh concrete cut", (.61, .61, .56), roughness=.86, texture=8)
    tile = material("Station porcelain tile", (.72, .72, .67), roughness=.43, texture=35)
    asphalt = material("Asphalt", (.095, .107, .105), roughness=.91, texture=23)
    soil = material("Brown earth", (.23, .15, .105), roughness=1, texture=7)
    clay = material("Clay stratum", (.34, .235, .16), roughness=1, texture=11)
    rock = material("Rock stratum", (.25, .27, .265), roughness=1, texture=14)
    steel = material("Brushed steel", (.42, .48, .49), metallic=.75, roughness=.3, texture=45)
    train_white = material("Train pearl silver", (.69, .74, .74), metallic=.35, roughness=.28)
    glass = material("Window glass", (.025, .06, .075), metallic=.18, roughness=.16)
    blue = material("Metro cobalt blue", (.025, .17, .47), metallic=.28, roughness=.31)
    yellow = material("Platform safety yellow", (.96, .62, .07), roughness=.7)
    charcoal = material("Graphite", (.055, .07, .075), roughness=.6)
    foliage = material("Tree foliage", (.09, .19, .10), roughness=.95, texture=10)
    warm_light = material("Warm fluorescent diffuser", (1.0, .79, .54), roughness=.5, emission=2.5)
    cool_light = material("Cool light diffuser", (.62, .84, 1.0), roughness=.5, emission=2)
    skin = material("People skin", (.52, .36, .27), roughness=.9)

    city, earth, shell = group("City"), group("Earth"), group("Removable shell")
    concourse, platform, tunnel = group("Concourse"), group("Platform"), group("Tunnel")
    train_group, detail = group("Train"), group("Detail")

    # A station box with its front wall already sectioned, so the interior has a continuous volume.
    box(earth, "Surrounding soil left", (-5.1, .3, 1.0), (2.1, 5.8, 7.7), soil, .01)
    box(earth, "Surrounding soil right", (5.1, .3, 1.0), (2.1, 5.8, 7.7), soil, .01)
    box(earth, "Earth behind station", (0, 3.4, 1), (8.1, 1.25, 7.7), clay, .01)
    for z, mat in ((3.35, soil), (1.25, clay), (-1.6, rock)):
        for x in (-5.1, 5.1):
            box(earth, "Exposed soil layer", (x, -2.65, z), (2.13, .13, 1.0), mat, .01)
    box(earth, "Foundation under tracks", (0, 0, -3.13), (8.1, 5.4, .42), rock, .025)
    box(shell, "Moving front soil face", (0, -2.73, 1.23), (8.05, .32, 6.18), soil, .04)
    box(shell, "Moving front concrete liner", (0, -2.49, 1.23), (7.9, .14, 6.0), cut_concrete, .02)
    box(city, "Road slab", (0, 0, 5.23), (12.4, 7.2, .35), concrete, .06)
    box(city, "Roadway", (0, .6, 5.43), (12.2, 3.45, .045), asphalt, .008)
    box(city, "Sidewalk", (0, -2.35, 5.45), (12.2, 1.3, .075), tile, .012)
    for x in (-4, -1.4, 1.4, 4):
        box(city, "Lane marking", (x, .6, 5.465), (1.0, .055, .01), cut_concrete, .002)
    for x in (-3.7, -2.3, 2.2, 3.6):
        box(city, "Zebra crossing", (x, -1.25, 5.47), (.42, 1.15, .01), cut_concrete, .002)
    # Modest facade detail: repeated windows and floor bands read as a city rather than toy blocks.
    for x, width, height, face in ((-4.7, 1.8, 3.9, concrete), (4.6, 2.1, 4.8, steel)):
        box(city, "Mid-rise building", (x, 2.75, 5.4 + height/2), (width, 1.25, height), face, .04)
        levels = int(height / .7)
        for level in range(levels):
            z = 5.78 + level * .7
            box(city, "Facade glass", (x, 2.08, z), (width*.78, .035, .47), glass, .008)
            box(city, "Window sill", (x, 2.03, z-.27), (width*.83, .07, .06), steel, .008)
            for offset in (-width*.22, width*.22):
                box(city, "Mullion", (x+offset, 2.015, z), (.035, .06, .49), steel, .005)
    box(city, "Entrance skylight roof", (0, -2.7, 7.07), (2.6, 1.45, .12), glass, .025)
    for x in (-1.2, 1.2):
        for y in (-3.33, -2.08):
            box(city, "Entrance steel post", (x, y, 6.25), (.09, .09, 1.75), steel, .012)
    box(city, "Entrance stair opening", (0, -2.7, 5.43), (1.6, 1.0, .08), charcoal, .008)
    for step in range(8):
        box(detail, "Entrance step", (0, -2.8 + step*.11, 5.23-step*.14), (1.35, .12, .07), tile, .008)
    for x in (-2.9, 2.9):
        cylinder(city, "Street tree trunk", (x, -3.15, 6.2), .085, 1.5, soil)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=.62, location=(x, -3.15, 7.15))
        linked(bpy.context.object, city, foliage)

    # Two occupied underground floors, real openings on the camera-facing side.
    for name, z, thickness, mat in (("Concourse floor slab", 2.48, .34, cut_concrete),
                                    ("Concourse porcelain finish", 2.67, .035, tile)):
        box(concourse, name, (0, -1.04, z), (8.0, 3.02, thickness), mat, .015)
        for x in (-2.47, 2.47):
            box(concourse, name, (x, 1.50, z), (3.06, 2.08, thickness), mat, .015)
    box(concourse, "Concourse rear retaining wall", (0, 2.44, 3.65), (8.0, .22, 1.9), concrete, .02)
    box(concourse, "Concourse ceiling", (0, 0, 4.72), (8.0, 5.1, .25), cut_concrete, .02)
    for x in (-3.6, -1.2, 1.2, 3.6):
        box(concourse, "Concourse column", (x, 1.65, 3.65), (.25, .28, 1.9), cut_concrete, .017)
    for x in (-3.1, -1.85, -.6, .65, 1.9, 3.15):
        box(concourse, "Fare gate cabinet", (x, -.72, 3.05), (.47, 1.05, .78), steel, .055)
        box(concourse, "Fare gate card reader", (x, -1.09, 3.47), (.34, .19, .055), blue, .01)
        box(concourse, "Fare gate glowing slot", (x, -.33, 3.42), (.33, .055, .06), cool_light, .006)
    for x in (-3.35, -1.1, 1.1, 3.35):
        box(concourse, "Concourse strip light", (x, 1.15, 4.58), (.12, 1.5, .04), warm_light, .007)
    box(concourse, "Wayfinding sign", (0, 2.28, 4.15), (2.8, .08, .43), charcoal, .015)
    box(concourse, "Wayfinding cyan line", (0, 2.22, 3.97), (2.55, .01, .04), cool_light, .003)

    # A visible escalator descending from the mezzanine to the platform.
    for side in (-.73, .73):
        beam(detail, "Escalator handrail", (side, .73, 2.53), (side, 2.05, -.42), .07, charcoal)
        beam(detail, "Escalator bright rail edge", (side, .75, 2.57), (side, 2.07, -.39), .022, steel)
    for step in range(22):
        fraction = step / 21
        box(detail, "Escalator tread", (0, .79+fraction*1.23, 2.33-fraction*2.63),
            (1.27, .10, .075), steel, .005)
    box(detail, "Escalator top landing", (0, .65, 2.7), (1.5, .7, .08), charcoal, .01)
    box(detail, "Escalator lower landing", (0, 2.1, -.37), (1.5, .7, .08), charcoal, .01)

    box(platform, "Platform concrete slab", (0, 1.0, -.53), (8.0, 3.1, .42), cut_concrete, .03)
    box(platform, "Platform tiled walking surface", (0, 1.07, -.30), (7.8, 2.8, .04), tile, .006)
    box(platform, "Platform edge yellow tactile strip", (0, -.39, -.245), (7.8, .16, .025), yellow, .004)
    box(platform, "Platform rear wall", (0, 2.44, .51), (8.0, .21, 1.65), concrete, .018)
    box(platform, "Platform ceiling", (0, -1.04, 1.55), (8.0, 3.02, .22), cut_concrete, .022)
    for x in (-2.47, 2.47):
        box(platform, "Platform ceiling around escalator", (x, 1.50, 1.55),
            (3.06, 2.08, .22), cut_concrete, .022)
    for x in (-3.45, -1.15, 1.15, 3.45):
        box(platform, "Platform column", (x, 1.3, .49), (.25, .28, 1.8), cut_concrete, .017)
        box(platform, "Column blue band", (x, 1.3, .88), (.27, .30, .12), blue, .009)
    for x in (-3.1, -1.0, 1.0, 3.1):
        box(platform, "Platform strip light", (x, .8, 1.39), (.16, 1.7, .045), warm_light, .005)
    for x in (-2.7, 2.7):
        box(platform, "Platform bench seat", (x, 1.1, -.02), (1.05, .42, .07), steel, .018)
        for dx in (-.43, .43):
            box(platform, "Bench legs", (x+dx, 1.1, -.15), (.07, .3, .22), charcoal, .01)
    box(platform, "Platform sign", (1.9, 2.3, .76), (2.2, .09, .42), blue, .014)
    box(platform, "Platform sign stripe", (1.9, 2.24, .59), (2.03, .01, .035), cool_light, .003)

    # Rail bed and tunnel ribs remain fixed while the train runs through the same space.
    box(tunnel, "Rail ballast", (0, -1.35, -1.12), (8.2, 2.75, .37), charcoal, .028)
    for y in (-2.04, -.56):
        box(tunnel, "Running rail", (0, y, -.88), (8.35, .085, .13), steel, .012)
        box(tunnel, "Rail foot", (0, y, -.97), (8.35, .15, .065), charcoal, .006)
    for i in range(27):
        box(tunnel, "Concrete sleeper", (-3.95+i*.305, -1.3, -1.01), (.13, 2.25, .09), concrete, .005)
    for y in (-2.6, 2.1):
        curve(tunnel, "Service conduit", [(x, y, 1.30) for x in (-4, -2, 0, 2, 4)], .045, steel)

    # Train parts parent to one root so windows, doors, wheels and lights stay coherent in motion.
    root = bpy.data.objects.new("Train motion root", None)
    train_group.objects.link(root)
    box(train_group, "Train car shell", (0, -1.32, -.14), (6.25, 1.62, 1.50), train_white, .2, root)
    box(train_group, "Train dark window band", (0, -2.147, .11), (5.87, .035, .70), glass, .018, root)
    box(train_group, "Train blue belt", (0, -2.18, -.42), (6.0, .04, .15), blue, .012, root)
    box(train_group, "Train roof equipment", (0, -1.3, .67), (4.75, 1.23, .17), steel, .045, root)
    for x in (-2.55, -1.63, -.72, .72, 1.63, 2.55):
        box(train_group, "Train side window", (x, -2.18, .10), (.63, .025, .53), glass, .022, root)
        box(train_group, "Train window trim", (x, -2.205, -.18), (.69, .018, .035), steel, .005, root)
    for x in (-1.15, 1.15):
        box(train_group, "Train sliding door seam", (x, -2.207, -.15), (.026, .02, 1.05), charcoal, .004, root)
        box(train_group, "Door window", (x-.23, -2.216, .17), (.35, .018, .45), glass, .015, root)
    for x in (-2.16, 2.16):
        for y in (-1.85, -.8):
            cylinder(train_group, "Rail wheel", (x, y, -1.01), .20, .10, charcoal, 24,
                     (math.pi/2, 0, 0), root)
    box(train_group, "Front windscreen", (3.13, -1.32, .14), (.035, 1.19, .56), glass, .04, root)
    for y in (-1.88, -.76):
        box(train_group, "Head lamp", (3.145, y, -.43), (.03, .15, .11), cool_light, .012, root)

    # Scale silhouettes with actual feet on floors; detail is concentrated near the camera.
    for x, y, z, shirt in ((-2.7, 1.25, 2.68, blue), (2.7, 1.22, 2.68, charcoal),
                           (-1.7, .45, -.27, charcoal), (1.0, 1.7, -.27, blue),
                           (2.8, .3, -.27, charcoal)):
        cylinder(detail, "Person legs", (x, y, z+.37), .095, .74, charcoal, 10)
        cylinder(detail, "Person torso", (x, y, z+1.05), .19, .68, shirt, 10)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=.15,
                                              location=(x, y, z+1.53))
        linked(bpy.context.object, detail, skin)

    world = bpy.context.scene.world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.035, .055, .075, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .55
    area("Broad daylight", (3, -10, 14), 1600, (10, 9), (1, .9, .8), (0, 0, 2))
    area("Cool rim", (-5, 6, 11), 950, (8, 7), (.65, .78, 1.0), (0, 0, 1))
    area("Concourse bounce", (0, -3.4, 4.4), 480, (7, 2.5), (1, .82, .65), (0, 0, 3.0))
    area("Platform bounce", (0, -3.4, 1.6), 510, (7, 2.5), (.78, .9, 1.0), (0, 0, -.4))
    sun = bpy.data.lights.new("Late afternoon sun", "SUN")
    sun.energy = 1.0
    sun.angle = math.radians(9)
    sun_obj = bpy.data.objects.new("Late afternoon sun", sun)
    bpy.context.scene.collection.objects.link(sun_obj)
    sun_obj.rotation_euler = (math.radians(30), math.radians(-35), math.radians(-25))

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 12
    scene.eevee.use_gtao = True
    scene.eevee.gtao_distance = 3
    scene.eevee.use_soft_shadows = True
    scene.render.resolution_x = int(os.environ.get("STUDIO_WIDTH", "720"))
    scene.render.resolution_y = int(os.environ.get("STUDIO_HEIGHT", "1280"))
    scene.render.resolution_percentage = 100
    scene.render.fps = FPS
    scene.frame_start = 1
    scene.frame_end = FPS * SECONDS
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    scene.render.film_transparent = False
    scene.render.filepath = str(DESTINATION.parent.parent / "renders/dynamic/frame_######")
    scene.camera = bpy.data.objects.new("Documentary camera", bpy.data.cameras.new("Documentary camera"))
    bpy.context.scene.collection.objects.link(scene.camera)
    scene.camera.data.lens = 34
    target = bpy.data.objects.new("Camera target", None)
    bpy.context.scene.collection.objects.link(target)
    track = scene.camera.constraints.new("TRACK_TO")
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"
    f = FPS
    animate(scene.camera, [(1, (12, -22, 12.7)), (2*f, (9, -16.5, 9.8)),
                           (2*f+1, (6.4, -11.5, 3.7)), (5*f, (5.3, -9.5, 3.1)),
                           (5*f+1, (5.8, -10.5, 1.0)), (8*f, (5.1, -9.0, .8))])
    animate(target, [(1, (0, 0, 1.25)), (2*f, (0, 0, 1.25)),
                     (2*f+1, (0, 0, 2.8)), (5*f, (0, .2, 2.7)),
                     (5*f+1, (0, 0, -.47)), (8*f, (0, 0, -.45))])
    animate(root, [(1, (-2.5, 0, 0)), (5*f, (-2.5, 0, 0)),
                   (8*f, (1.3, 0, 0))])
    for obj in shell.objects:
        animate(obj, [(1, obj.location.copy()), (int(.65*f), obj.location.copy()),
                      (int(1.8*f), obj.location + Vector((-10, 0, 0)))])
    for action in bpy.data.actions:
        for fcurve in action.fcurves:
            for key in fcurve.keyframe_points:
                key.interpolation = "BEZIER"
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(DESTINATION))
    print(f"Wrote {DESTINATION} with {len(bpy.data.objects)} objects and {scene.frame_end} frames")


main()
