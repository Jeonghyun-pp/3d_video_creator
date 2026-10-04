"""Blender author-script helper for pinned, independently editable instances.

Usage from an author script: ``from assets import import_prepared_asset``.
The build runner already places this directory on Blender's Python path.
"""
import hashlib
import json
import math
from pathlib import Path
import re

import bpy


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def import_prepared_asset(manifest_path, instance_id, transform=None):
    """Append a prepared version and return qualified object, part and anchor IDs.

    Source meshes/materials are appended, allowing instance-specific patches.
    Anchors remain in the owned object's local coordinates and follow motion.
    ``studio_explode_vector`` stays asset-local; ``studio_asset_root_id`` tells
    the action engine which instance transform converts its direction to world.
    """
    if not isinstance(instance_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", instance_id):
        raise ValueError("Invalid instance_id")
    if any(obj.get("studio_instance_id") == instance_id or obj.get("studio_id") == instance_id for obj in bpy.context.scene.objects):
        raise ValueError(f"Instance already exists: {instance_id}")
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "prepared" or not manifest.get("prepared_scene"):
        raise ValueError("Asset must be semantically mapped and prepared before import")
    source_path = Path(manifest["prepared_scene"])
    if not source_path.is_absolute():
        source_path = manifest_path.parent / source_path
    if not source_path.is_file() or not manifest.get("prepared_scene_sha256") or _sha256(source_path) != manifest["prepared_scene_sha256"]:
        raise ValueError("Prepared scene bytes do not match the pinned manifest")
    transform = transform or {}
    components = {key: transform.get(key, default) for key, default in (
        ("location", [0, 0, 0]), ("rotation_euler", [0, 0, 0]), ("scale", [1, 1, 1]))}
    for key, values in components.items():
        if len(values) != 3 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            raise ValueError(f"Instance {key} must contain three finite numbers")
    if any(abs(value) < 1e-9 for value in components["scale"]):
        raise ValueError("Instance scale must be invertible")
    loaded, instance_root, collection = [], None, None
    try:
        with bpy.data.libraries.load(str(source_path), link=False) as (available, appended):
            appended.objects = available.objects
        loaded = [obj for obj in appended.objects if obj]
        lookup = {obj.get("studio_id"): obj for obj in loaded}
        if None in lookup or len(lookup) != len(loaded):
            raise ValueError("Prepared objects need unique stable studio_id values")
        parts = manifest.get("parts", [])
        for part in parts:
            if part["root_object_id"] not in lookup or any(object_id not in lookup for object_id in part["object_ids"]):
                raise ValueError("Prepared scene does not contain its declared part objects")
        collection = bpy.data.collections.new(f"StudioInstance_{instance_id}")
        bpy.context.scene.collection.children.link(collection)
        instance_root = bpy.data.objects.new(instance_id, None)
        collection.objects.link(instance_root)
        instance_root["studio_id"] = instance_id
        instance_root["studio_instance_id"] = instance_id
        instance_root["studio_asset_id"] = manifest["asset_id"]
        instance_root["studio_asset_version"] = manifest["version"]
        instance_root["studio_asset_scene_sha256"] = manifest["prepared_scene_sha256"]
        # Appended objects have unevaluated matrices until linked and updated.
        # Evaluate the complete source hierarchy before reading local transforms.
        for obj in loaded:
            collection.objects.link(obj)
        bpy.context.view_layer.update()
        ids, part_ids, anchor_ids = {}, {}, {}
        for source_id, obj in lookup.items():
            qualified = f"{instance_id}/objects/{source_id}"
            ids[source_id] = qualified
            obj.name = qualified
            obj["studio_id"] = qualified
            obj["studio_instance_id"] = instance_id
            obj["studio_source_object_id"] = source_id
            obj["studio_asset_root_id"] = instance_id
            if obj.parent is None:
                local = obj.matrix_local.copy()
                obj.parent = instance_root
                obj.matrix_local = local
        for part in parts:
            root = lookup[part["root_object_id"]]
            qualified = f"{instance_id}/{part['part_id']}"
            root.name = qualified
            root["studio_id"] = qualified
            root["studio_part_id"] = part["part_id"]
            root["studio_part_kind"] = part["kind"]
            root["studio_explode_vector"] = part["explode_vector"]
            root["studio_rest_location"] = list(root.location)
            root["studio_rest_rotation"] = list(root.rotation_euler)
            ids[part["root_object_id"]] = qualified
            part_ids[part["part_id"]] = qualified
        for anchor in manifest.get("anchors", []):
            obj = lookup[anchor["object_id"]]
            identifier = f"{instance_id}/{anchor['anchor_id']}"
            local_anchors = json.loads(obj.get("studio_anchors", "{}"))
            local_anchors[identifier] = anchor["point_local_m"]
            # Also expose instance/part/anchor for common label contracts.
            owners = [part["part_id"] for part in parts if anchor["object_id"] in part["object_ids"] or anchor["object_id"] == part["root_object_id"]]
            for part_id in owners:
                local_anchors[f"{instance_id}/{part_id}/{anchor['anchor_id']}"] = anchor["point_local_m"]
            obj["studio_anchors"] = json.dumps(local_anchors)
            anchor_ids[anchor["anchor_id"]] = identifier
        instance_root.location = components["location"]
        instance_root.rotation_euler = components["rotation_euler"]
        instance_root.scale = components["scale"]
        bpy.context.view_layer.update()
        return {"instance_root_id": instance_id, "object_ids": ids, "part_ids": part_ids, "anchor_ids": anchor_ids}
    except Exception:
        for obj in loaded:
            bpy.data.objects.remove(obj, do_unlink=True)
        if instance_root:
            bpy.data.objects.remove(instance_root, do_unlink=True)
        if collection:
            bpy.data.collections.remove(collection)
        raise
