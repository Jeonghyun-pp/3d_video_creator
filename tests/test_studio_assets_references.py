"""Offline acquisition contracts and real FFmpeg reference extraction."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from studio.assets import extract_zip, fetch_asset, prepare_asset, search_assets, select_files
from studio.common import StudioError, file_hash, stable_hash, write_json
from studio.references import prepare_reference


class AssetTests(unittest.TestCase):
    def test_live_search_slug_response_resolves_metadata(self):
        with tempfile.TemporaryDirectory() as root, patch("studio.assets._api", side_effect=[
            {"results": [{"slug": "modular_industrial_pipes_01", "score": 0.7}]},
            {"name": "Modular Industrial Pipes 01", "tags": ["pipes"]}
        ]) as api:
            found = search_assets({"query": "pipes"}, root)
            self.assertEqual(found["candidates"][0]["asset_id"], "modular_industrial_pipes_01")
            self.assertEqual(found["candidates"][0]["name"], "Modular Industrial Pipes 01")
            self.assertEqual(api.call_args.args[0], "/info/modular_industrial_pipes_01")

    def test_asset_ids_match_project_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            for asset_id in ("UPPER", "_leading", "-leading", "a/b"):
                with self.subTest(asset_id=asset_id), self.assertRaises(StudioError) as error:
                    fetch_asset({"asset_id": asset_id, "primitive": {"type": "cube"}}, temporary)
                self.assertEqual(error.exception.code, "INPUT_INVALID")
            with self.assertRaises(StudioError):
                fetch_asset({"asset_id": "valid", "version": None, "primitive": {"type": "cube"}}, temporary)

    def test_asset_version_rejects_source_changes_and_catalog_reuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.glb"
            source.write_bytes(b"first")
            candidate = {"asset_id": "fixed", "path": str(source), "name": "Fixed Model"}
            first = fetch_asset(candidate, root / "library")
            selected = search_assets({"query": "fixed"}, root / "library", online=False)["candidates"][0]
            self.assertEqual(fetch_asset(selected, root / "library")["manifest_path"], first["manifest_path"])
            source.write_bytes(b"second")
            with self.assertRaises(StudioError) as error:
                fetch_asset(candidate, root / "library")
            self.assertEqual(error.exception.code, "REVISION_CONFLICT")
            self.assertEqual(Path(first["files"][0]["path"]).read_bytes(), b"first")
            candidate["version"] = "v0002"
            self.assertFalse(fetch_asset(candidate, root / "library")["reused"])

    def test_prepared_mapping_is_immutable_and_repeat_needs_no_blender(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = root / "prepared"
            prepared.mkdir()
            scene = prepared / "prepared.blend"
            scene.write_bytes(b"fixed scene")
            mapping = {"parts": [{"part_id": "shell", "object_ids": ["fixed:object_0001"]}]}
            write_json(prepared / "inventory.json", {"objects": []})
            manifest = root / "asset.json"
            write_json(manifest, {"asset_id": "fixed", "status": "prepared", "files": [],
                                 "mapping_hash": stable_hash(mapping), "prepared_scene": str(scene),
                                 "prepared_scene_sha256": file_hash(scene), "inspection": {"preview_paths": [], "warnings": []}})
            with patch("studio.assets.subprocess.run") as process:
                self.assertTrue(prepare_asset(manifest, mapping)["reused"])
                process.assert_not_called()
            mapping["parts"][0]["part_id"] = "changed"
            with self.assertRaises(StudioError) as error:
                prepare_asset(manifest, mapping)
            self.assertEqual(error.exception.code, "REVISION_CONFLICT")
            self.assertEqual(scene.read_bytes(), b"fixed scene")

    def test_provider_gltf_downloads_external_bin_and_texture_then_reuses(self):
        gltf = json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": "model.bin", "byteLength": 4}],
                           "images": [{"uri": "textures/base.jpg"}]}).encode()
        payloads = {"https://example.test/model.gltf": gltf,
                    "https://example.test/model.bin": b"1234", "https://example.test/base.jpg": b"texture"}
        def spec(url):
            data = payloads[url]
            return {"url": url, "size": len(data), "md5": hashlib.md5(data).hexdigest()}
        model = {**spec("https://example.test/model.gltf"), "include": {
            "model.bin": spec("https://example.test/model.bin"), "textures/base.jpg": spec("https://example.test/base.jpg")}}
        metadata = {"gltf": {"1k": {"gltf": model}}}
        with tempfile.TemporaryDirectory() as temporary, patch("studio.assets._api", side_effect=[{"name": "Test"}, metadata]), \
                patch("studio.assets._request", side_effect=lambda url: io.BytesIO(payloads[url])) as network:
            candidate = {"asset_id": "test_model", "provider": "polyhaven"}
            result = fetch_asset(candidate, temporary)
            self.assertEqual(len(result["files"]), 3)
            self.assertEqual(result["source"]["use_status"], "cleared")
            self.assertEqual(result["downloaded_bytes"], sum(map(len, payloads.values())))
            self.assertTrue((Path(temporary) / "test_model/v0001/original/textures/base.jpg").is_file())
            cached = fetch_asset(candidate, temporary)
            self.assertTrue(cached["reused"])
            self.assertEqual(network.call_count, 3)

    def test_caller_cannot_self_clear_license(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "model.glb"; model.write_bytes(b"glb")
            root = Path(temporary) / "library"
            for source in ({"use_status": "cleared", "license_id": "CC0-1.0"}, {"provider": "factory"},
                           {"license_asserted_by": "studio"}):
                with self.subTest(source=source), self.assertRaises(StudioError) as error:
                    fetch_asset({"asset_id": "claimed", "path": str(model), "source": source}, root)
                self.assertEqual(error.exception.code, "INPUT_INVALID")
                self.assertFalse((root / "claimed/v0001/asset.json").exists())
            with self.assertRaises(StudioError):
                fetch_asset({"asset_id": "spoof", "provider": "fal", "path": str(model)}, root)
            licensed = fetch_asset({"asset_id": "licensed", "path": str(model), "source": {"license_id": "CC-BY-4.0", "page_url": "https://example.test"}}, root)
            self.assertEqual((licensed["source"]["use_status"], licensed["source"]["license_asserted_by"]), ("review_only", "caller"))
            primitive = fetch_asset({"asset_id": "cube", "provider": "original", "primitive": {"type": "cube", "dimensions": [1, 1, 1]},
                                     "source": {"use_status": "cleared"}}, root)
            self.assertEqual((primitive["source"]["use_status"], primitive["source"]["license_asserted_by"]), ("cleared", "studio"))

    def test_trusted_adapter_source_is_recorded(self):
        from studio.assets import fetch_trusted
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "bolt.glb"; model.write_bytes(b"glb")
            result = fetch_trusted({"asset_id": "bolt", "provider": "factory", "path": str(model)},
                                   {"license_id": "original-generated", "use_status": "cleared"}, Path(temporary) / "library")
            self.assertEqual((result["source"]["use_status"], result["source"]["license_asserted_by"]), ("cleared", "studio"))

    def test_missing_gltf_dependency_never_writes_success_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "model.gltf"
            write_json(source, {"buffers": [{"uri": "missing.bin"}]})
            with self.assertRaises(StudioError) as error:
                fetch_asset({"asset_id": "broken", "path": str(source)}, Path(temporary) / "library")
            self.assertEqual(error.exception.code, "MISSING_DEPENDENCY")
            self.assertFalse((Path(temporary) / "library/broken/v0001/asset.json").exists())

    def test_checksum_or_byte_limit_never_leaves_partial_success(self):
        for spec, limit in [({"md5": "0" * 32}, 100), ({}, 2)]:
            with self.subTest(spec=spec), tempfile.TemporaryDirectory() as temporary, \
                    patch("studio.assets._request", return_value=io.BytesIO(b"1234")):
                candidate = {"asset_id": "bad", "provider": "external", "files": [
                    {"url": "https://example.test/test.glb", "relative_path": "test.glb", **spec}]}
                with self.assertRaises(StudioError):
                    fetch_asset(candidate, temporary, download_limit=limit)
                self.assertEqual(list(Path(temporary).rglob("*.part")), [])
                self.assertEqual(list(Path(temporary).rglob("asset.json")), [])

    def test_unsafe_dependency_paths_and_archive_symlinks_are_rejected(self):
        for path in ("../evil.bin", "/tmp/evil.bin", "%2e%2e/evil.bin", "C:/evil.bin"):
            with self.subTest(path=path), self.assertRaises(StudioError):
                select_files({"gltf": {"1k": {"gltf": {"url": "https://example.test/model.gltf", "include": {path: {"url": "https://example.test/evil"}}}}}})
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "archive.zip"
            info = zipfile.ZipInfo("escape")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(info, "../escape")
            with self.assertRaises(StudioError):
                extract_zip(archive, Path(temporary) / "extracted")

    def test_local_search_has_real_name_and_role_filters(self):
        with tempfile.TemporaryDirectory() as temporary:
            write_json(Path(temporary) / "index.json", {"assets": [
                {"asset_id": "shell", "name": "Steel Panel", "roles": ["outer_shell"], "tags": ["steel"]},
                {"asset_id": "chair", "name": "Chair", "roles": ["furniture"]}]})
            result = search_assets({"query": "steel", "role": "outer_shell"}, temporary, online=False)
            self.assertEqual([candidate["asset_id"] for candidate in result["candidates"]], ["shell"])
            self.assertEqual(result["candidates"][0]["identity_status"], "unverified")


class ReferenceTests(unittest.TestCase):
    def test_image_copy_and_cache_include_source_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.png"
            Image.new("RGB", (400, 100), "blue").save(source)
            first = prepare_reference(root, source)
            second = prepare_reference(root, source)
            self.assertEqual(first["reference"]["analysis_status"], "frames_ready_for_visual_review")
            self.assertTrue(second["reused"])
            self.assertEqual(first["reference"]["claims"], [])
            Image.new("RGB", (400, 100), "red").save(source)
            self.assertFalse(prepare_reference(root, source)["reused"])
            with self.assertRaises(StudioError):
                prepare_reference(root, source, "0:1")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_video_range_uses_source_times_and_exact_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
            result = prepare_reference(root, source, "0.5:1.5")
            samples = result["reference"]["samples"]
            self.assertEqual(samples[0]["nearest_frame"], 15)
            self.assertEqual(samples[-1]["nearest_frame"], 44)
            self.assertTrue(all(Path(sample["path"]).is_file() for sample in samples))
            self.assertIn(30, [sample["nearest_frame"] for sample in samples])
            with self.assertRaises(StudioError):
                prepare_reference(root, source, "1:3")


if __name__ == "__main__":
    unittest.main()
