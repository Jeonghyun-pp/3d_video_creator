import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from studio.assets import extract_zip, fetch_asset, search_assets
from studio.common import StudioError, file_hash


def archive(members):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        for name, payload in members.items():
            bundle.writestr(name, payload)
    return data.getvalue()


ZIP = archive({"Metal009_1K-JPG_Color.jpg": b"c", "Metal009_1K-JPG_NormalGL.jpg": b"n", "Metal009_1K-JPG_NormalDX.jpg": b"x",
               "Metal009_1K-JPG_Roughness.jpg": b"r", "Metal009_1K-JPG.usdc": b"u", "Metal009_1K-JPG.blend": b"b"})
INFO = {"foundAssets": [{"assetId": "Metal009", "displayName": "Metal 009", "tags": ["metal"], "downloadFolders": {"default": {
    "downloadFiletypeCategories": {"zip": {"downloads": [
        {"downloadLink": "https://ambientcg.com/get?file=Metal009_1K-JPG.zip", "fileName": "Metal009_1K-JPG.zip", "size": len(ZIP), "filetype": "zip", "attribute": "1K-JPG"},
        {"downloadLink": "https://ambientcg.com/get?file=Metal009_2K-JPG.zip", "fileName": "Metal009_2K-JPG.zip", "size": 9, "filetype": "zip", "attribute": "2K-JPG"}]}}}}}]}


def fake(url, *args, **kwargs):
    if "full_json" in url:
        return io.BytesIO(json.dumps(INFO if "id=" in url else {"foundAssets": INFO["foundAssets"]}).encode())
    if url.endswith("1K-JPG.zip"):
        return io.BytesIO(ZIP)
    raise AssertionError("unexpected URL " + url)


class ProviderTests(unittest.TestCase):
    def test_default_search_only_queries_polyhaven(self):
        with tempfile.TemporaryDirectory() as temporary, patch("studio.assets._api", return_value={}) as api, \
                patch("studio.assets._request", side_effect=AssertionError("ambientCG must not be called")):
            search_assets({"query": "metal", "type": "textures"}, temporary)
            self.assertEqual(api.call_count, 1)

    def test_ambientcg_search_and_fetch_keep_three_maps_cleared(self):
        with tempfile.TemporaryDirectory() as temporary, patch("studio.assets._request", side_effect=fake):
            found = search_assets({"query": "metal", "type": "textures", "providers": ["ambientcg"]}, temporary)
            candidate = found["candidates"][0]
            self.assertEqual((candidate["asset_id"], candidate["provider_asset_id"]), ("ambientcg_metal009", "Metal009"))
            result = fetch_asset(candidate, Path(temporary) / "assets")
            self.assertEqual(sorted(f["role"] for f in result["files"]), ["base_color", "normal", "roughness"])
            self.assertEqual((result["source"]["use_status"], result["source"]["license_id"]), ("cleared", "CC0-1.0"))
            self.assertFalse((Path(temporary) / "assets/ambientcg_metal009/v0001/download").exists())
            again = fetch_asset(candidate, Path(temporary) / "assets")
            self.assertTrue(again["reused"])

    def test_zip_bomb_and_entry_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bomb.zip"
            path.write_bytes(archive({"a.jpg": b"0" * 5000, "b.jpg": b"1"}))
            for kwargs in ({"max_bytes": 1000}, {"max_entries": 1}):
                with self.subTest(**kwargs), self.assertRaises(StudioError) as error:
                    extract_zip(path, Path(temporary) / "out", **kwargs)
                self.assertEqual(error.exception.code, "BUDGET_EXHAUSTED")
                self.assertFalse((Path(temporary) / "out").exists())

    def test_ambientcg_id_must_come_from_adapter_shape(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(StudioError):
            fetch_asset({"asset_id": "ambientcg_x", "provider": "ambientcg", "provider_asset_id": "../x"}, temporary)


if __name__ == "__main__":
    unittest.main()
