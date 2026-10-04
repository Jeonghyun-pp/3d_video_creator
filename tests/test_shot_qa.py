import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from shot_qa import review, sample_times


class ShotQATest(unittest.TestCase):
    def test_quarter_second_maps_to_nearest_frame(self):
        samples = sample_times(1.0, 30)
        self.assertEqual([row["nearest_frame"] for row in samples], [0, 8, 15, 23])
        self.assertAlmostEqual(samples[1]["frame_seconds"], 8 / 30, places=6)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_report_detects_black_and_freeze_and_builds_comparison(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = root / "candidate.mp4"
            reference = root / "reference.mp4"
            for destination, source, fps in ((candidate, "color=c=black:s=180x320:r=30:d=1.5", 30),
                                             (reference, "testsrc2=s=180x320:r=24:d=1.5", 24)):
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source,
                                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), str(destination)],
                               check=True)
            output = root / "qa"
            report = review(candidate, output, reference)
            saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["sample_count"], 6)
            self.assertEqual(saved["samples"][1]["nearest_frame"], 8)
            self.assertTrue(report["candidate_findings"]["black_intervals"])
            self.assertTrue(report["candidate_findings"]["freeze_intervals"])
            self.assertEqual(len(report["contact_sheets"]), 1)
            self.assertTrue(Path(report["contact_sheets"][0]).is_file())
            self.assertEqual(saved["reference"]["fps"], 24)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_samples_survive_concat_color_metadata_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for number, color_range in ((1, "tv"), (2, "pc")):
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                "-i", "testsrc2=s=180x320:r=12:d=6", "-c:v", "libx264",
                                "-pix_fmt", "yuv420p", "-color_range", color_range,
                                str(root / f"{number}.mp4")], check=True)
            (root / "list.txt").write_text("file '1.mp4'\nfile '2.mp4'\n")
            joined = root / "joined.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat",
                            "-safe", "0", "-i", str(root / "list.txt"), "-c", "copy", str(joined)], check=True)
            self.assertEqual(review(joined, root / "qa")["sample_count"], 48)


if __name__ == "__main__":
    unittest.main()
