"""Executed by Blender only: import once, inspect, then apply semantic mapping."""
import json
from pathlib import Path
import sys
import traceback

import bpy
import bmesh
from mathutils import Vector


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def import_source(config):
    manifest = config["manifest"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    primitive = manifest.get("primitive")
    if primitive:
        kind = primitive.get("type", "cube")
        if kind == "cube":
            bpy.ops.mesh.primitive_cube_add(size=1)
        elif kind == "cylinder":
            bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=float(primitive.get("radius", 0.5)), depth=float(primitive.get("depth", 1)))
        elif kind == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=float(primitive.get("radius", 0.5)))
        else:
            raise ValueError("Supported primitives: cube, cylinder, sphere")
        obj = bpy.context.object
        obj.name = primitive.get("name", manifest["asset_id"])
        if primitive.get("dimensions"):
            obj.dimensions = primitive["dimensions"]
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.location = primitive.get("location", [0, 0, 0])
    else:
        files = manifest["files"]
        supported = [item for item in files if Path(item["path"]).suffix.lower() in {".glb", ".gltf", ".fbx", ".obj", ".blend"}]
        supported.sort(key=lambda item: item.get("role") != "primary")
        if not supported:
            raise ValueError("Preparation requires a model file or primitive, not a material/HDRI")
        path = supported[0]["path"]
        suffix = Path(path).suffix.lower()
        if suffix in {".glb", ".gltf"}:
            bpy.ops.import_scene.gltf(filepath=path)
        elif suffix == ".fbx":
            bpy.ops.import_scene.fbx(filepath=path)
        elif suffix == ".obj":
            bpy.ops.wm.obj_import(filepath=path)
        elif suffix == ".blend":
            with bpy.data.libraries.load(path, link=False) as (source, target):
                target.objects = source.objects
            for obj in target.objects:
                if obj and not obj.users_collection:
                    bpy.context.scene.collection.objects.link(obj)
        # Blender importers normalize their format's up axis. Explicit local
        # units are converted through one root without applying rig transforms.
        factor = {"meters": 1, "centimeters": 0.01, "millimeters": 0.001}.get(manifest.get("source_units", "meters"))
        if factor is None:
            raise ValueError("source_units must be meters, centimeters, or millimeters")
        if factor != 1:
            root = bpy.data.objects.new("AssetUnitRoot", None)
            bpy.context.scene.collection.objects.link(root)
            for obj in list(bpy.context.scene.objects):
                if obj != root and obj.parent is None:
                    obj.parent = root
            root.scale = (factor,) * 3
        # Generated meshes (image-to-3D) have no real units: scale_basis pins one real dimension.
        basis = manifest.get("scale_basis")
        if basis:
            bpy.context.view_layer.update()
            corners = [obj.matrix_world @ Vector(c) for obj in bpy.context.scene.objects if obj.type == "MESH" for c in obj.bound_box]
            if not corners:
                raise ValueError("scale_basis needs mesh geometry")
            size = [max(v[i] for v in corners) - min(v[i] for v in corners) for i in range(3)]
            measured = {"longest": max(size), "x": size[0], "y": size[1], "z": size[2]}[basis["dimension"]]
            if measured <= 0:
                raise ValueError("scale_basis dimension is zero")
            root = bpy.data.objects.new("AssetScaleBasisRoot", None)
            bpy.context.scene.collection.objects.link(root)
            for obj in list(bpy.context.scene.objects):
                if obj != root and obj.parent is None:
                    obj.parent = root
            root.scale = (basis["meters"] / measured,) * 3
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    for index, obj in enumerate(sorted(scene.objects, key=lambda item: item.name)):
        obj["studio_id"] = f"{manifest['asset_id']}:object_{index + 1:04d}"
        obj["studio_role"] = "unmapped"
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=config["inventory_scene"])


def _components(bm):
    parent = {v.index: v.index for v in bm.verts}
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    bm.verts.index_update()
    for edge in bm.edges:
        a, b = find(edge.verts[0].index), find(edge.verts[1].index)
        if a != b:
            parent[a] = b
    return len({find(v.index) for v in bm.verts if v.link_edges})


def inventory():
    rows, warnings = [], []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in sorted(bpy.context.scene.objects, key=lambda item: item.name):
        row = {"object_id": obj.get("studio_id"), "name": obj.name, "type": obj.type,
               "parent_object_id": obj.parent.get("studio_id") if obj.parent else None,
               "scale": list(obj.scale), "material_names": [slot.material.name if slot.material else None for slot in obj.material_slots],
               "rest_transform": [value for matrix_row in obj.matrix_local for value in matrix_row]}
        if obj.type == "MESH":
            mesh = obj.data
            bm = bmesh.new()
            bm.from_mesh(mesh)
            row.update({"vertices": len(mesh.vertices), "polygons_original": len(mesh.polygons),
                        "non_manifold_edges": sum(not edge.is_manifold for edge in bm.edges),
                        "boundary_edges": sum(edge.is_boundary for edge in bm.edges),
                        "zero_area_faces": sum(face.calc_area() <= 1e-12 for face in bm.faces),
                        "triangles": sum(len(face.verts) - 2 for face in bm.faces)})
            # Loose parts after welding coincident vertices: generated meshes often split at seams.
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
            row["loose_parts_welded"] = _components(bm)
            row["watertight"] = not any(edge.is_boundary or not edge.is_manifold for edge in bm.edges)
            bm.free()
            if row["triangles"] > 300000:
                warnings.append(f"{obj.name}: {row['triangles']} triangles; decimate before close-up use")
            if row["loose_parts_welded"] > 50:
                warnings.append(f"{obj.name}: {row['loose_parts_welded']} loose parts; likely fragmented, not semantic parts")
            evaluated = obj.evaluated_get(depsgraph)
            eval_mesh = evaluated.to_mesh()
            row["polygons_evaluated"] = len(eval_mesh.polygons)
            evaluated.to_mesh_clear()
            row["bounds_world"] = [list(obj.matrix_world @ Vector(corner)) for corner in obj.bound_box]
            if not mesh.vertices or not mesh.polygons:
                warnings.append(f"Empty mesh: {obj.name}")
            if row["zero_area_faces"]:
                warnings.append(f"Degenerate faces: {obj.name}")
            if any(mod.type == "BOOLEAN" for mod in obj.modifiers) and row["non_manifold_edges"]:
                warnings.append(f"Boolean on non-manifold mesh: {obj.name}")
        if any(value < 0 for value in obj.scale):
            warnings.append(f"Negative scale: {obj.name}")
        if max(obj.scale) - min(obj.scale) > 1e-6:
            warnings.append(f"Nonuniform scale: {obj.name}")
        rows.append(row)
    missing = []
    for image in bpy.data.images:
        if image.source == "FILE" and not image.packed_file and image.filepath:
            path = bpy.path.abspath(image.filepath)
            if not Path(path).is_file():
                missing.append(path)
    return {"objects": rows, "object_count": len(rows), "mesh_count": sum(row["type"] == "MESH" for row in rows),
            "material_count": len(bpy.data.materials), "units": "meters", "up_axis": "Z",
            "polycount_original": sum(row.get("polygons_original", 0) for row in rows),
            "polycount_evaluated": sum(row.get("polygons_evaluated", 0) for row in rows),
            "missing_files": sorted(set(missing)), "warnings": warnings,
            "normal_check": "degenerate faces checked; orientation requires visual review",
            "scale_basis": "source_units; physical dimensions not independently verified"}


def apply_mapping(mapping, asset_id):
    lookup = {obj["studio_id"]: obj for obj in bpy.context.scene.objects if obj.get("studio_id")}
    definitions = mapping.get("parts", [])
    ids = [part.get("part_id") for part in definitions]
    if not definitions or len(ids) != len(set(ids)) or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("Mapping requires unique nonempty part IDs")
    claimed, roots, parts = set(), {}, []
    for definition in definitions:
        part_id = definition["part_id"]
        kind = definition.get("kind", "leaf")
        if kind not in {"leaf", "group"}:
            raise ValueError("Part kind must be leaf or group")
        objects = definition.get("object_ids", [])
        if kind == "leaf" and not objects:
            raise ValueError(f"Leaf has no owned objects: {part_id}")
        for object_id in objects:
            if object_id not in lookup or object_id in claimed:
                raise ValueError(f"Missing or multiply owned object: {object_id}")
            claimed.add(object_id)
        root_id = definition.get("root_object_id")
        if root_id:
            if root_id not in objects or root_id not in lookup:
                raise ValueError("Leaf root_object_id must refer to one of its owned objects")
            root = lookup[root_id]
        else:
            root = bpy.data.objects.new(f"Part_{part_id}", None)
            bpy.context.scene.collection.objects.link(root)
            root_id = f"{asset_id}:part:{part_id}"
            root["studio_id"] = root_id
            point = definition.get("pivot_local", [0, 0, 0])
            root.location = point
            lookup[root_id] = root
        root["studio_role"] = part_id
        root["studio_part_id"] = part_id
        roots[part_id] = root
        for object_id in objects:
            obj = lookup[object_id]
            obj["studio_role"] = part_id
            if obj != root and (obj.parent is None or obj.parent.get("studio_id") not in objects):
                world = obj.matrix_world.copy()
                obj.parent = root
                obj.matrix_world = world
        vector = Vector(definition.get("explode_vector", [0, 0, 1]))
        if vector.length <= 1e-9:
            raise ValueError("explode_vector must be nonzero")
        vector.normalize()
        root["studio_explode_vector"] = list(vector)
        parts.append({"part_id": part_id, "kind": kind, "root_object_id": root_id, "object_ids": objects,
                      "parent_part_id": definition.get("parent_part_id"), "child_part_ids": definition.get("child_part_ids", []),
                      "rest_transform": [], "pivot_local": list(root.location), "explode_vector": list(vector),
                      "anchor_ids": definition.get("anchor_ids", [])})
    parents = {}
    for part in parts:
        for child in part["child_part_ids"]:
            if child not in roots or child == part["part_id"]:
                raise ValueError(f"Unknown or cyclic child part: {child}")
            if child in parents and parents[child] != part["part_id"]:
                raise ValueError("A part may have only one parent")
            parents[child] = part["part_id"]
        if part["parent_part_id"]:
            if part["parent_part_id"] not in roots:
                raise ValueError("Unknown parent part")
            if part["part_id"] in parents and parents[part["part_id"]] != part["parent_part_id"]:
                raise ValueError("Conflicting part parent declarations")
            parents[part["part_id"]] = part["parent_part_id"]
    for part in parts:
        chain, parent = {part["part_id"]}, parents.get(part["part_id"])
        while parent:
            if parent in chain:
                raise ValueError("Part hierarchy contains a cycle")
            chain.add(parent)
            parent = parents.get(parent)
    for part in parts:
        parent_id = parents.get(part["part_id"])
        part["parent_part_id"] = parent_id
        part["child_part_ids"] = [child for child, parent in parents.items() if parent == part["part_id"]]
        root = roots[part["part_id"]]
        world = root.matrix_world.copy()
        root.parent = roots[parent_id] if parent_id else None
        root.matrix_world = world
        bpy.context.view_layer.update()
        part["rest_transform"] = [value for row in roots[part["part_id"]].matrix_local for value in row]
    anchors = mapping.get("anchors", [])
    seen = set()
    for anchor in anchors:
        if anchor.get("anchor_id") in seen or anchor.get("object_id") not in lookup or len(anchor.get("point_local_m", [])) != 3:
            raise ValueError("Anchor needs a unique ID, known object, and three local coordinates")
        seen.add(anchor.get("anchor_id"))
    return parts, anchors


def previews(out):
    scene = bpy.context.scene
    mesh_objects = [obj for obj in scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        raise ValueError("Asset has no mesh geometry")
    bounds = [obj.matrix_world @ Vector(corner) for obj in mesh_objects for corner in obj.bound_box]
    minimum = Vector(tuple(min(point[i] for point in bounds) for i in range(3)))
    maximum = Vector(tuple(max(point[i] for point in bounds) for i in range(3)))
    center, extent = (minimum + maximum) / 2, max(maximum - minimum)
    extent = max(extent, 0.01)
    # Cycles CPU also runs where headless EEVEE cannot create a graphics context.
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = 384
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.world = bpy.data.worlds.new("InspectionWorld")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.12, 0.14, 0.17, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.5
    camera_data = bpy.data.cameras.new("InspectionCamera")
    camera = bpy.data.objects.new("InspectionCamera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = extent * 1.4
    lamp_data = bpy.data.lights.new("InspectionKey", "AREA")
    lamp = bpy.data.objects.new("InspectionKey", lamp_data)
    scene.collection.objects.link(lamp)
    lamp_data.energy = 700 * extent * extent
    lamp_data.shape = "DISK"
    lamp_data.size = extent * 2
    gray = bpy.data.materials.new("InspectionGray")
    gray.diffuse_color = (0.42, 0.45, 0.49, 1)
    scene.view_layers[0].material_override = gray
    paths = []
    for name, direction in [("front", (0, -1, 0)), ("back", (0, 1, 0)), ("left", (-1, 0, 0)), ("right", (1, 0, 0)), ("top", (0, 0, 1)), ("bottom", (0, 0, -1))]:
        vector = Vector(direction)
        camera.location = center + vector * extent * 3
        camera.rotation_euler = (-vector).to_track_quat("-Z", "Y").to_euler()
        lamp.location = center + (vector + Vector((0.5, -0.7, 1.3))) * extent * 2
        lamp.rotation_euler = (center - lamp.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(out / f"gray_{name}.png")
        bpy.ops.render.render(write_still=True)
        paths.append(scene.render.filepath)
    scene.view_layers[0].material_override = None
    camera_data.type = "PERSP"
    camera_data.lens = 55
    camera.location = center + Vector((1.2, -1.6, 1.1)).normalized() * extent * 2.4
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(out / "material_hero.png")
    bpy.ops.render.render(write_still=True)
    paths.append(scene.render.filepath)
    # Inspection lights/camera belong only to previews, never the prepared asset.
    bpy.data.objects.remove(camera, do_unlink=True)
    bpy.data.objects.remove(lamp, do_unlink=True)
    return paths, {"minimum": list(minimum), "maximum": list(maximum)}


def main(config):
    out = Path(config["output_dir"])
    if config.get("mapping") is not None and Path(config["inventory_scene"]).is_file():
        bpy.ops.wm.open_mainfile(filepath=config["inventory_scene"])
    else:
        import_source(config)
    report = inventory()
    parts, anchors = apply_mapping(config["mapping"], config["manifest"]["asset_id"]) if config.get("mapping") is not None else ([], [])
    if parts:
        report = inventory()
    write(out / "inventory.json", report)
    paths, bounds = previews(out)
    prepared = None
    if parts and not report["missing_files"]:
        # Pack actual image bytes so later immutable snapshots are portable.
        bpy.ops.file.pack_all()
        prepared = str(out / "prepared.blend")
        bpy.ops.wm.save_as_mainfile(filepath=prepared)
    warnings = report["warnings"] + ["Model identity, internal geometry and close-up suitability require visual review"]
    if report["missing_files"]:
        warnings.append("External image files are missing; asset is not prepared")
    status = "prepared" if prepared else "needs_mapping" if not parts else "needs_dependencies"
    readiness = "ready" if prepared and any(part["kind"] == "leaf" for part in parts) else "needs_prep"
    return {"ok": True, "status": status, "prepared_scene": prepared, "parts": parts, "anchors": anchors, "inventory": report,
            "capabilities": {"explode": {"status": readiness, "reason": "Mapped roots and rest transforms" if prepared else "Semantic mapping or dependencies required"},
                             "peel": {"status": "needs_prep", "reason": "Panel thickness, backside and interior need visual validation"},
                             "closeup": {"status": "needs_prep", "reason": "Review material_hero.png at requested camera distance"}},
            "inspection": {"identity_status": "unverified", "geometry_status": "mesh_inventory_complete", "missing_files": report["missing_files"],
                           "warnings": warnings, "preview_paths": paths, "bounds_m": bounds,
                           "polycount_original": report["polycount_original"], "polycount_evaluated": report["polycount_evaluated"],
                           "normal_check": report["normal_check"]}}


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
    try:
        result = main(config)
    except Exception as exc:
        traceback.print_exc()
        result = {"ok": False, "error": str(exc), "error_code": "ASSET_NOT_SUITABLE"}
    write(Path(config["output_dir"]) / "result.json", result)
    if not result["ok"]:
        sys.exit(1)
