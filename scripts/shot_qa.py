"""Technical video QA and quarter-second visual review sheets.

Usage: python scripts/shot_qa.py candidate.mp4 [--reference reference.mp4] [--out-dir qa]
"""

import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

from PIL import Image, ImageDraw


SAMPLE_SECONDS = 0.25
SAMPLES_PER_SHEET = 40
THUMB_SIZE = (180, 320)


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args[:2])} failed: {result.stderr[-1000:]}")
    return result.stdout + result.stderr


def probe(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    data = json.loads(command(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                               "-of", "json", str(path)]))
    videos = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"]
    if len(videos) != 1:
        raise ValueError(f"Expected exactly one video stream: {path}")
    video = videos[0]
    fps = float(Fraction(video["avg_frame_rate"]))
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"Invalid frame rate: {path}")
    duration = float(video.get("duration") or data["format"].get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Invalid video duration: {path}")
    return {"path": str(path.resolve()), "width": video["width"], "height": video["height"],
            "fps": fps, "duration": duration, "codec": video.get("codec_name"),
            "pixel_format": video.get("pix_fmt"), "container_duration": float(data["format"]["duration"]),
            "audio_streams": sum(stream.get("codec_type") == "audio" for stream in data["streams"]),
            "nominal_fps": float(Fraction(video["r_frame_rate"])),
            "color_space": video.get("color_space"), "color_primaries": video.get("color_primaries"),
            "color_transfer": video.get("color_transfer"), "color_range": video.get("color_range")}


def detect_intervals(path: Path) -> dict:
    # A static shot can legitimately trigger freezedetect; findings are review prompts.
    log = command(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                   "blackdetect=d=0.2:pix_th=0.10,freezedetect=n=-60dB:d=0.5",
                   "-f", "null", "-"])
    black = [{"start": float(a), "end": float(b), "duration": float(c)}
             for a, b, c in re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", log)]
    starts = [float(value) for value in re.findall(r"freeze_start: ([\d.]+)", log)]
    ends = [float(value) for value in re.findall(r"freeze_end: ([\d.]+)", log)]
    freeze = [{"start": start, "end": ends[i] if i < len(ends) else None}
              for i, start in enumerate(starts)]
    return {"black_intervals": black, "freeze_intervals": freeze}


def sample_times(duration: float, fps: float) -> list[dict]:
    count = math.floor((duration - 1 / fps) / SAMPLE_SECONDS) + 1
    return [{"requested_seconds": round(i * SAMPLE_SECONDS, 3),
             "nearest_frame": math.floor(i * SAMPLE_SECONDS * fps + 0.5),
             "frame_seconds": round(math.floor(i * SAMPLE_SECONDS * fps + 0.5) / fps, 6)}
            for i in range(max(0, count))]


def extract_frames(path: Path, samples: list[dict], destination: Path) -> list[Path]:
    indices = [row["nearest_frame"] for row in samples]
    if len(indices) != len(set(indices)):
        raise ValueError("Source FPS is too low for unique 0.25-second frame samples")
    # FFmpeg's expression parser can fail on hundreds of OR terms, so decode in
    # bounded batches. Explicit indices avoid rounding one frame into two bins.
    for offset in range(0, len(indices), SAMPLES_PER_SHEET):
        batch = indices[offset:offset + SAMPLES_PER_SHEET]
        expression = "+".join(f"eq(n\\,{index})" for index in batch)
        # A concatenated MP4 may carry a new color range at the cut. Preserve
        # the filter graph so its n counter remains global across that change.
        command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-reinit_filter", "0",
                 "-i", str(path), "-an", "-vf", f"select='{expression}'",
                 "-fps_mode", "passthrough", "-frames:v", str(len(batch)),
                 "-start_number", str(offset), str(destination / "%04d.jpg")])
    frames = sorted(destination.glob("*.jpg"))
    if len(frames) != len(samples):
        raise RuntimeError(f"Expected {len(samples)} extracted frames from {path}, got {len(frames)}")
    return frames


def make_sheets(out_dir: Path, samples: list[dict], candidate: list[Path],
                reference: list[Path] | None) -> list[str]:
    paths = []
    columns = 4
    row_width = THUMB_SIZE[0] * (2 if reference else 1)
    row_height = THUMB_SIZE[1] + 38
    for offset in range(0, len(samples), SAMPLES_PER_SHEET):
        group = samples[offset:offset + SAMPLES_PER_SHEET]
        sheet = Image.new("RGB", (columns * row_width, math.ceil(len(group) / columns) * row_height), "#121820")
        draw = ImageDraw.Draw(sheet)
        for relative, sample in enumerate(group):
            x, y = (relative % columns) * row_width, (relative // columns) * row_height
            draw.text((x + 4, y + 3), f"t={sample['requested_seconds']:.2f}s  frame={sample['nearest_frame']}", fill="white")
            if reference:
                draw.text((x + 4, y + 20), "REFERENCE", fill="#8ed7f1")
                draw.text((x + THUMB_SIZE[0] + 4, y + 20), "CANDIDATE", fill="#ffd08a")
            else:
                draw.text((x + 4, y + 20), "CANDIDATE", fill="#ffd08a")
            for side, frame in enumerate(([reference[offset + relative]] if reference else []) +
                                         [candidate[offset + relative]]):
                with Image.open(frame) as image:
                    thumb = image.convert("RGB").resize(THUMB_SIZE, Image.Resampling.LANCZOS)
                    sheet.paste(thumb, (x + side * THUMB_SIZE[0], y + 38))
        output = out_dir / f"contact_{offset // SAMPLES_PER_SHEET:03d}.jpg"
        sheet.save(output, quality=90)
        paths.append(str(output.resolve()))
    return paths


def review(candidate_path: Path, out_dir: Path, reference_path: Path | None = None) -> dict:
    candidate = probe(candidate_path)
    reference = probe(reference_path) if reference_path else None
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = min(candidate["duration"], reference["duration"] if reference else candidate["duration"])
    slowest_fps = min(candidate["fps"], reference["fps"]) if reference else candidate["fps"]
    sample_clock = [row["requested_seconds"] for row in sample_times(duration, slowest_fps)]
    def indices(fps: float) -> list[dict]:
        return [{"requested_seconds": t, "nearest_frame": math.floor(t * fps + 0.5),
                 "frame_seconds": round(math.floor(t * fps + 0.5) / fps, 6)} for t in sample_clock]
    samples = indices(candidate["fps"])
    reference_samples = indices(reference["fps"]) if reference else None
    if reference_samples:
        for sample, reference_sample in zip(samples, reference_samples):
            sample["reference_nearest_frame"] = reference_sample["nearest_frame"]
            sample["reference_frame_seconds"] = reference_sample["frame_seconds"]
    warnings = []
    for item in [candidate, reference] if reference else [candidate]:
        if abs(item["fps"] - item["nominal_fps"]) > 0.01:
            warnings.append(f"{item['path']}: variable-frame-rate suspected; frame indices are approximate")
        if item["fps"] < 4:
            raise ValueError("At least 4 FPS is required for quarter-second samples")
    if reference:
        if abs(candidate["duration"] - reference["duration"]) > SAMPLE_SECONDS:
            warnings.append("Reference and candidate durations differ; contact sheets cover their common interval")
        if (candidate["width"], candidate["height"]) != (reference["width"], reference["height"]):
            warnings.append("Reference and candidate resolutions differ; thumbnails are resized for review")
    with tempfile.TemporaryDirectory() as temp:
        candidate_dir = Path(temp) / "candidate"
        candidate_dir.mkdir()
        candidate_frames = extract_frames(candidate_path, samples, candidate_dir)
        reference_frames = None
        if reference:
            reference_dir = Path(temp) / "reference"
            reference_dir.mkdir()
            reference_frames = extract_frames(reference_path, reference_samples, reference_dir)
        sheets = make_sheets(out_dir, samples, candidate_frames, reference_frames)
    report = {"candidate": candidate, "reference": reference, "sample_interval_seconds": SAMPLE_SECONDS,
              "sample_rule": "nearest source frame, round-half-up; constant-frame-rate assumption",
              "sample_count": len(samples), "samples": samples, "contact_sheets": sheets,
              "candidate_findings": detect_intervals(candidate_path),
              "reference_findings": detect_intervals(reference_path) if reference else None,
              "warnings": warnings,
              "interpretation": "Black and freeze intervals are review prompts, not an aesthetic pass/fail score."}
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    report = review(args.candidate, args.out_dir, args.reference)
    print(json.dumps({"report": str((args.out_dir / "report.json").resolve()),
                      "sample_count": report["sample_count"],
                      "contact_sheets": report["contact_sheets"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
