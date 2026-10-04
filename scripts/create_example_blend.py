"""Create an illustrative subway station model, not an engineering-verified design."""

from pathlib import Path
import sys

import bpy
from mathutils import Vector


def material(name, color, metallic=0.0):
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1)
    result.use_nodes = True
    principled = result.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = (*color, 1)
    principled.inputs["Metallic"].default_value = metallic
    principled.inputs["Roughness"].default_value = .55
    return result


def collection(name):
    result = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(result)
    return result


def move_to(obj, group):
    for previous in list(obj.users_collection):
        previous.objects.unlink(obj)
    group.objects.link(obj)
    bevel = obj.modifiers.new("Soft technical edges", "BEVEL")
    bevel.width = .055
    bevel.segments = 2


def box(group, name, at, scale, surface):
    bpy.ops.mesh.primitive_cube_add(size=1, location=at)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(surface)
    move_to(obj, group)
    return obj


def cylinder(group, name, at, radius, depth, surface, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=radius, depth=depth, location=at, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(surface)
    move_to(obj, group)
    return obj


def main():
    if "--" not in sys.argv:
        raise RuntimeError("Missing output path")
    destination = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    white = material("Concrete white", (.76, .80, .82))
    dark = material("Graphite", (.065, .085, .10))
    blue = material("Transit blue", (.08, .34, .70))
    orange = material("Infrastructure orange", (.95, .42, .09))
    glass = material("Glass blue", (.23, .55, .68), .25)
    steel = material("Brushed steel", (.43, .49, .51), .6)
    street = collection("street")
    concourse = collection("concourse")
    mechanical = collection("mechanical")
    platform = collection("platform")
    tunnel = collection("tunnel")
    train = collection("train")
    box(street, "Street slab", (0, 0, 4.4), (9.5, 6.4, .32), white)
    box(street, "Road", (0, .8, 4.59), (9.2, 2.7, .05), dark)
    for x in (-3.4, -2.1, 2.0, 3.5):
        box(street, "Building", (x, -1.5, 5.5), (1.05, 1.3, 1.9), glass)
    box(street, "Entry", (0, -2.2, 5.05), (1.3, 1.6, 1.1), blue)
    box(concourse, "Concourse floor", (0, 0, 2.4), (8.4, 5.7, .3), white)
    for x in (-3.7, 3.7):
        box(concourse, "Concourse wall", (x, 0, 3.1), (.2, 5.7, 1.3), white)
    for x in (-2.0, -.7, .7, 2.0):
        box(concourse, "Fare gate", (x, -1.5, 2.75), (.42, 1.1, .65), blue)
    box(mechanical, "Mechanical floor", (0, 0, .7), (8.0, 5.3, .24), white)
    for x in (-2.4, 0, 2.4):
        box(mechanical, "Plant cabinet", (x, 0, 1.15), (1.2, 1.4, .75), orange)
    for y in (-1.5, 1.5):
        cylinder(mechanical, "Air duct", (0, y, 1.65), .17, 7.1, steel, (0, 1.5708, 0))
    box(platform, "Platform slab", (0, 0, -1.05), (8.4, 5.5, .27), white)
    box(platform, "Platform edge", (0, -.7, -.87), (8.2, .22, .06), orange)
    for x in (-3.5, 3.5):
        box(platform, "Column", (x, 1.3, -.25), (.26, .3, 1.45), steel)
    box(tunnel, "Tunnel foundation", (0, -1.5, -2.5), (9.0, 3.0, .3), dark)
    for y in (-2.3, -.7):
        box(tunnel, "Rail", (0, y, -2.21), (8.8, .09, .12), steel)
    for x in (-3.5, -2.0, -.5, 1.0, 2.5):
        box(tunnel, "Sleeper", (x, -1.5, -2.32), (.16, 2.3, .1), white)
    box(train, "Train body", (0, -1.5, -1.60), (5.1, 1.27, .93), blue)
    box(train, "Train roof", (0, -1.5, -1.11), (4.8, 1.17, .12), steel)
    for x in (-1.65, -.55, .55, 1.65):
        box(train, "Train window", (x, -2.16, -1.47), (.7, .04, .39), glass)
    for x in (-1.8, 1.8):
        for y in (-1.95, -1.05):
            cylinder(train, "Wheel", (x, y, -2.12), .18, .12, dark, (1.5708, 0, 0))
    world = bpy.context.scene.world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.045, .065, .09, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .65
    bpy.ops.object.light_add(type="AREA", location=(1, -7, 10))
    bpy.context.object.name = "Key light"
    bpy.context.object.data.energy = 1700
    bpy.context.object.data.shape = "DISK"
    bpy.context.object.data.size = 8
    bpy.ops.object.light_add(type="AREA", location=(-5, 5, 7))
    bpy.context.object.name = "Fill light"
    bpy.context.object.data.energy = 700
    bpy.context.object.data.size = 7
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 12), rotation=(.35, -.45, -.35))
    bpy.context.object.name = "Layer light"
    bpy.context.object.data.energy = 1.25
    destination.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(destination))


main()
