"""Internal 2.5D DDP exterior camera experiment from two depth layers.

The city and building are separate image planes with different projected motion.
Usage: python scripts/create_exterior_depth_pilot.py [--reference reference.mp4]
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image

from shot_qa import review


PROJECT = Path(__file__).resolve().parents[1] / "projects" / "ddp_reference_pilot"
OUT = PROJECT / "exterior" / "v2"
BACKGROUND = PROJECT / "assets" / "generated" / "exterior_v2_background.png"
BUILDING = PROJECT / "assets" / "generated" / "exterior_v2_building.png"
WIDTH, HEIGHT, FPS, FRAMES = 1080, 1920, 30, 150
DEPTH = {"background": 10.0, "building": 7.5}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transform(frame: int, layer: str, fast: bool = False) -> dict:
    if fast:
        seconds = frame / FPS
        phase = min(1.0, seconds / 2.2)
        zoom = phase * phase * (3 - 2 * phase)
        creep = max(0.0, (seconds - 2.2) / 2.8)
        camera_x = 0.18 * zoom + 0.04 * creep
        scale = 1 + (0.35 if layer == "background" else 0.50) * zoom + 0.03 * creep
        shift_x = -camera_x * 2160 / DEPTH[layer]
        return {"scale": scale, "shift_x_pixels": shift_x, "camera_x": camera_x,
                "camera_z": zoom, "profile": "fast_2.2s_push"}
    progress = (1 - math.cos(math.pi * frame / (FRAMES - 1))) / 2
    camera_x = -0.25 + 0.5 * progress
    camera_z = 0.7 * progress
    distance = DEPTH[layer]
    scale = 1.20 * distance / (distance - camera_z)
    # Projected x displacement is inversely proportional to plane depth.
    shift_x = -camera_x * 2160 / distance
    return {"scale": scale, "shift_x_pixels": shift_x, "camera_x": camera_x, "camera_z": camera_z}


def composite(frame: int, background: Image.Image, building: Image.Image, fast: bool = False) -> Image.Image:
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), (7, 9, 17, 255))
    for name, source in (("background", background), ("building", building)):
        pose = transform(frame, name, fast)
        size = (round(WIDTH * pose["scale"]), round(HEIGHT * pose["scale"]))
        layer = source.resize(size, Image.Resampling.LANCZOS)
        x = round((WIDTH - size[0]) / 2 + pose["shift_x_pixels"])
        y = round((HEIGHT - size[1]) / 2)
        canvas.alpha_composite(layer, (x, y))
    return canvas.convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="Optional source for 0.25-second comparison QA")
    parser.add_argument("--fast", action="store_true", help="Render a faster push-in experiment under exterior/v3")
    args = parser.parse_args()
    out = PROJECT / "exterior" / ("v3" if args.fast else "v2")
    out.mkdir(parents=True, exist_ok=True)
    video = out / ("exterior_fast_pan.mp4" if args.fast else "exterior_depth_pan.mp4")
    pipeline_video = PROJECT / "assets" / "generated" / video.name
    with Image.open(BACKGROUND) as image:
        background = image.convert("RGBA")
    with Image.open(BUILDING) as image:
        building = image.convert("RGBA")
    with tempfile.TemporaryDirectory() as temp:
        frame_dir = Path(temp)
        for frame in range(FRAMES):
            composite(frame, background, building, args.fast).save(frame_dir / f"{frame:04d}.png")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(FPS),
                        "-i", str(frame_dir / "%04d.png"), "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)], check=True)
    shutil.copy2(video, pipeline_video)
    qa = review(video, out / "qa", args.reference)
    keyframes = [{"frame": frame, "seconds": round(frame / FPS, 3),
                  "background": transform(frame, "background", args.fast),
                  "building": transform(frame, "building", args.fast)}
                 for frame in (0, 30, 60, 90, 120, 149)]
    report = {"video": str(video.relative_to(PROJECT)),
              "pipeline_video": str(pipeline_video.relative_to(PROJECT)),
              "pipeline_video_sha256": sha256(pipeline_video),
              "method": "two separately projected 2.5D image layers",
              "background_plate_sha256": sha256(BACKGROUND), "building_cutout_sha256": sha256(BUILDING),
              "source_type": "AI-generated concept and AI-edited background/cutout",
              "use_status": "internal_preview_only", "architecture_rights": "not verified for publication",
              "geometry_accuracy": "unverified; building is a flat cutout, not a surveyed 3D model",
              "parallax": "real differential layer motion, but not true volumetric geometry or occlusion within the building",
              "motion_keyframes": keyframes, "duration_seconds": FRAMES / FPS,
              "size": [WIDTH, HEIGHT], "fps": FPS, "qa_sample_count": qa["sample_count"],
              "qa_report": str((out / "qa" / "report.json").relative_to(PROJECT)),
              "reference_gap": "First two seconds only approximate aerial dolly. Building cutout can slide at edges; facade topology and 2-5 second panel assembly remain unsolved."}
    (out / "shot_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"video": str(video), "qa_sample_count": qa["sample_count"],
                      "contact_sheets": qa["contact_sheets"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
