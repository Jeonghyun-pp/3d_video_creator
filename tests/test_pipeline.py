import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from video_pipeline import ProductionError, build, load_scene, validate_video
import video_pipeline


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "assets").mkdir()
        Image.new("RGB", (360, 640), (45, 86, 118)).save(self.project / "assets" / "still.png")
        self.scene = {
            "title": "테스트", "slug": "test", "duration": 1, "aspect_ratio": "9:16", "fps": 30,
            "key_message": "하나의 메시지", "objects": [{"id": "sample", "type": "structure"}],
            "scenes": [{"id": "scene_01", "start": 0, "duration": 1, "narration": "테스트 영상입니다.",
                        "visual_goal": "Show sample", "camera": {"type": "orthographic", "movement": "static_iso"},
                        "visible_objects": ["sample"], "animation": {"type": "none"}, "labels": [],
                        "transition": "end", "source": "image", "media": "assets/still.png"}]
        }
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        (self.project / "scene.json").write_text(json.dumps(self.scene, ensure_ascii=False), encoding="utf-8")

    def test_validation_catches_timeline_and_object_errors(self):
        self.assertEqual(load_scene(self.project)["duration"], 1)
        self.scene["scenes"][0]["start"] = .5
        self.save()
        with self.assertRaisesRegex(ProductionError, "starts"):
            load_scene(self.project)
        self.scene["scenes"][0]["start"] = 0
        self.scene["scenes"][0]["visible_objects"] = ["missing"]
        self.save()
        with self.assertRaisesRegex(ProductionError, "unknown objects"):
            load_scene(self.project)

    def test_rejects_path_escape_and_unapproved_final(self):
        self.scene["scenes"][0]["media"] = "../outside.png"
        self.save()
        with self.assertRaisesRegex(ProductionError, "escapes project"):
            load_scene(self.project)
        self.scene["scenes"][0]["media"] = "assets/still.png"
        self.save()
        with self.assertRaisesRegex(ProductionError, "review.json"):
            build(self.project, preview=False, force=False)

    def test_asset_manifest_blocks_uncleared_or_changed_sources(self):
        asset = self.project / "assets" / "still.png"
        manifest = self.project / "assets" / "asset_manifest.json"
        manifest.write_text(json.dumps({"assets": [{"path": "still.png", "sha256": video_pipeline.sha256(asset),
                                                    "use_status": "internal_preview_only"}]}))
        (self.project / "review.json").write_text(json.dumps({"facts_approved": True,
            "script_approved": True, "assets_approved": True, "visual_approved": True}))
        (self.project / "sources.md").write_text("Fixture: https://example.org/data\n")
        with self.assertRaisesRegex(ProductionError, "not cleared"):
            video_pipeline.source_approval(self.project, self.scene)
        data = json.loads(manifest.read_text())
        data["assets"][0]["use_status"] = "publish_ok"
        manifest.write_text(json.dumps(data))
        video_pipeline.source_approval(self.project, self.scene)
        unlisted = self.project / "assets" / "unlisted.png"
        shutil.copy2(asset, unlisted)
        self.scene["scenes"][0]["media"] = "assets/unlisted.png"
        with self.assertRaisesRegex(ProductionError, "absent from asset_manifest"):
            video_pipeline.source_approval(self.project, self.scene)
        self.scene["scenes"][0]["media"] = "assets/still.png"
        asset.write_bytes(b"changed")
        with self.assertRaisesRegex(ProductionError, "changed"):
            video_pipeline.source_approval(self.project, self.scene)

    def test_new_project_is_generic_and_valid(self):
        with patch.object(video_pipeline, "ROOT", self.project):
            generated = video_pipeline.create_project("elevator")
        self.assertEqual(load_scene(generated)["slug"], "elevator")
        self.assertEqual(load_scene(generated)["objects"][0]["id"], "main")
        with self.assertRaisesRegex(ProductionError, "Slug"):
            video_pipeline.create_project("-invalid")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_complete_preview_encode_and_cached_rerun(self):
        path = build(self.project, preview=True, force=False)
        self.assertTrue(path.is_file())
        self.assertEqual(validate_video(path, 360, 640, 12, 1)["audio_codec"], "aac")
        self.assertIn("00:00:00,000 --> 00:00:01,000", (self.project / "subtitles" / "narration.srt").read_text())
        first = path.stat().st_mtime_ns
        path = build(self.project, preview=True, force=False)
        self.assertTrue(path.is_file())
        self.assertTrue((self.project / "renders" / "preview" / "scene_01" / "fingerprint.txt").is_file())
        self.assertGreaterEqual(path.stat().st_mtime_ns, first)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_internal_review_uses_reference_resolution_and_fps(self):
        path = build(self.project, preview=False, review=True, force=False)
        self.assertEqual(path.name, "review.mp4")
        self.assertEqual(validate_video(path, 720, 1280, 30, 1)["audio_codec"], "aac")
        self.assertEqual(json.loads((self.project / "final/review.manifest.json").read_text())["profile"], "review")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_scene_audio_is_padded_to_exact_duration(self):
        (self.project / "audio").mkdir()
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=0.5", "-c:a", "pcm_s16le",
                        str(self.project / "audio" / "scene_01.wav")], check=True)
        path = build(self.project, preview=True, force=False)
        self.assertTrue(path.is_file())
        track = self.project / "renders" / "preview" / "narration.wav"
        self.assertTrue(track.is_file())
        data = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(track)]))
        self.assertAlmostEqual(float(data["format"]["duration"]), 1.0, places=2)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_final_export_with_approved_fixture(self):
        # Synthetic approval only exercises the production output path.
        (self.project / "review.json").write_text('{"facts_approved": true, "script_approved": true}')
        (self.project / "sources.md").write_text("Test fixture: https://example.org/data\n")
        (self.project / "audio").mkdir()
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1", "-c:a", "pcm_s16le",
                        str(self.project / "audio" / "narration.wav")], check=True)
        path = build(self.project, preview=False, force=False)
        self.assertEqual(validate_video(path, 1080, 1920, 30, 1)["video_codec"], "h264")
        self.assertTrue((self.project / "final" / "master.manifest.json").is_file())
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=880:duration=1", "-c:a", "pcm_s16le",
                        str(self.project / "audio" / "narration.wav")], check=True)
        build(self.project, preview=False, force=False)
        self.assertEqual(len(list((self.project / "final" / "history").glob("*.mp4"))), 1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_video_clip_ingest_and_short_clip_rejection(self):
        clip = self.project / "assets" / "clip.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=360x640:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
        self.scene["scenes"][0].update(source="video", media="assets/clip.mp4")
        self.save()
        self.assertTrue(build(self.project, preview=True, force=False).is_file())
        self.scene["scenes"][0]["duration"] = 2
        self.scene["duration"] = 2
        self.save()
        with self.assertRaisesRegex(ProductionError, "shorter than scene"):
            build(self.project, preview=True, force=False)


if __name__ == "__main__":
    unittest.main()
