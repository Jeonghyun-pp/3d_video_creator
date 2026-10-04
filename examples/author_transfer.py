"""Reuse one pinned plate asset for an illustrative layer assembly.

Geometry comes entirely from import_prepared_asset. This is a schematic
production-tool transfer test, not a model of an actual mechanical product.
"""
import json
from pathlib import Path

import bpy
from mathutils import Vector

from assets import import_prepared_asset
from scene_tools import object_by_id

job = STUDIO_JOB
scene = bpy.context.scene
contract = json.loads((Path(job["project_dir"]) / "reuse_contract.json").read_text())
manifest_path = Path(contract["source_manifest"])
manifest = json.loads(manifest_path.read_text())
scene["subject_mode"] = "schematic"
scene["description"] = "Schematic laminated module: reused plate meshes, not an engineered product"
scene["studio_authored_animation"] = False
scene.render.engine = "CYCLES"
scene.cycles.samples = 16
scene.cycles.use_denoising = True
scene.render.image_settings.file_format = "PNG"
scene.view_settings.view_transform = "AgX"
scene.world = bpy.data.worlds.new("TransferWorld")
scene.world.use_nodes = True
background = scene.world.node_tree.nodes["Background"]
background.inputs["Color"].default_value = (0.035, 0.05, 0.075, 1)
background.inputs["Strength"].default_value = 0.5


def material(name, color, metallic, roughness):
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1)
    result.use_nodes = True
    principled = result.node_tree.nodes["Principled BSDF"]
    principled.inputs["Base Color"].default_value = (*color, 1)
    principled.inputs["Metallic"].default_value = metallic
    principled.inputs["Roughness"].default_value = roughness
    return result


materials = {
    "layer_01": material("Base | graphite", (0.075, 0.12, 0.16), 0.65, 0.36),
    "layer_02": material("Middle | vermilion", (0.55, 0.03, 0.015), 0.15, 0.4),
    "layer_03": material("Cover | satin silver", (0.52, 0.6, 0.66), 0.55, 0.35),
    "stage": material("Stage | matte", (0.055, 0.07, 0.095), 0.0, 0.75),
}
imports = []
for instance in job["shot"]["asset_instances"]:
    if (instance["asset_id"], instance["asset_version"]) != (manifest["asset_id"], manifest["version"]):
        raise ValueError("Transfer shot must pin the existing prepared asset version")
    ids = import_prepared_asset(manifest_path, instance["instance_id"], instance["transform"])
    for identifier in ids["object_ids"].values():
        obj = object_by_id(identifier)
        if obj and obj.type == "MESH":
            obj.data.materials.clear()
            obj.data.materials.append(materials[instance["instance_id"]])
    imports.append({"instance_id": instance["instance_id"], "ids": ids})

for name, location, energy, size in (
    ("TransferKey", (3, -4, 6), 1700, 5),
    ("TransferFill", (-4, -1, 3), 850, 4),
    ("TransferRim", (1, 4, 5), 1300, 3),
):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.size = energy, size
    lamp = bpy.data.objects.new(name, data)
    scene.collection.objects.link(lamp)
    lamp.location = location
    lamp.rotation_euler = (Vector((0, 0, 1)) - lamp.location).to_track_quat("-Z", "Y").to_euler()

# Persist how this new subject reused the prepared model; no mesh operators ran.
report = {"source_manifest": str(manifest_path), "asset_id": manifest["asset_id"],
          "asset_version": manifest["version"], "prepared_scene_sha256": manifest["prepared_scene_sha256"],
          "instances": imports, "geometry_created": False, "subject_mode": "schematic"}
(Path(job["output_dir"]) / "asset_reuse.json").write_text(json.dumps(report, indent=2))
