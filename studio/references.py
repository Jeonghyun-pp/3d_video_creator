"""Prepare local reference media as inspectable, source-linked frames."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .common import StudioError, file_hash, read_json, stable_hash, write_json
from scripts.shot_qa import extract_frames, probe


def contact_sheets(frames, samples, destination, prefix="contact"):
    """Keep source aspect ratio and bound sheet sizes for long references."""
    paths = []
    for offset in range(0, len(frames), 40):
        group = frames[offset:offset + 40]
        sheet = Image.new("RGB", (4 * 240, math.ceil(len(group) / 4) * 270), "#141820")
        draw = ImageDraw.Draw(sheet)
        for i, frame in enumerate(group):
            x, y = (i % 4) * 240, (i // 4) * 270
            with Image.open(frame) as source:
                thumb = ImageOps.contain(source.convert("RGB"), (232, 240))
                sheet.paste(thumb, (x + (240 - thumb.width) // 2, y + 24 + (240 - thumb.height) // 2))
            draw.text((x + 5, y + 5), f"{samples[offset + i]['frame_seconds']:.3f}s", fill="white")
        path = destination / f"{prefix}_{offset // 40:03d}.jpg"
        sheet.save(path, quality=90)
        paths.append(str(path))
    return paths


def prepare_reference(project, input_path, time_range=None, interval=0.25):
    project, source = Path(project).resolve(), Path(input_path).resolve()
    if not project.is_dir() or not source.is_file():
        raise StudioError("INPUT_INVALID", "Project directory and input media must exist")
    if not math.isfinite(interval) or interval <= 0:
        raise StudioError("INPUT_INVALID", "Reference interval must be a positive number")
    digest = file_hash(source)
    key = stable_hash({"sha256": digest, "range": time_range, "interval": interval})[:16]
    out = project / "references" / key
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "reference.json"
    if manifest_path.exists():
        cached = read_json(manifest_path)
        if all(Path(item["path"]).is_file() and file_hash(item["path"]) == item["sha256"]
               for item in cached.get("files", [])):
            return {"status": "prepared", "artifacts": [str(manifest_path), *cached["contact_sheets"]],
                    "reference": cached, "reused": True}
    try:
        with Image.open(source) as image:
            if time_range is not None:
                raise StudioError("INPUT_INVALID", "A time range applies only to video references")
            ImageOps.exif_transpose(image).convert("RGB").save(out / "frame_0000.jpg", quality=95)
            media = {"kind": "image", "width": image.width, "height": image.height}
        frames = [out / "frame_0000.jpg"]
        samples = [{"frame_seconds": 0.0, "nearest_frame": 0}]
        start = end = 0.0
    except (Image.UnidentifiedImageError, OSError):
        try:
            media = {"kind": "video", **probe(source)}
        except FileNotFoundError as exc:
            raise StudioError("MISSING_DEPENDENCY", "ffprobe is required to inspect video references") from exc
        except (RuntimeError, ValueError) as exc:
            raise StudioError("INPUT_INVALID", f"Cannot inspect reference media: {exc}") from exc
        try:
            start, end = map(float, time_range.split(":")) if time_range else (0.0, media["duration"])
        except (ValueError, TypeError):
            raise StudioError("INPUT_INVALID", "Range must be START:END in seconds")
        if not all(map(math.isfinite, (start, end))) or start < 0 or end <= start or end > media["duration"] + 0.001:
            raise StudioError("INPUT_INVALID", "Reference range lies outside the video")
        fps = media["fps"]
        count = math.ceil((end - start) / interval)
        if count > 1200:
            raise StudioError("BUDGET_EXHAUSTED", "Reference exceeds 1200 frames; select a shorter range or larger interval")
        times = [start + i * interval for i in range(count)] + [start, (start + end) / 2, max(start, end - 1 / fps)]
        indices = sorted({min(math.ceil(end * fps) - 1, math.floor(t * fps + 0.5)) for t in times})
        samples = [{"nearest_frame": i, "frame_seconds": round(i / fps, 6)} for i in indices]
        frame_dir = out / "frames"
        frame_dir.mkdir(exist_ok=True)
        for old in frame_dir.glob("*.jpg"):
            old.unlink()
        try:
            frames = extract_frames(source, samples, frame_dir)
        except FileNotFoundError as exc:
            raise StudioError("MISSING_DEPENDENCY", "ffmpeg is required to extract video references") from exc
        except RuntimeError as exc:
            raise StudioError("INPUT_INVALID", f"Cannot decode reference frames: {exc}") from exc
    sheets = contact_sheets(frames, samples, out)
    all_files = [*frames, *map(Path, sheets)]
    warnings = ["Variable-frame-rate suspected; timestamps use average-FPS estimates"] if media["kind"] == "video" and abs(media["fps"] - media["nominal_fps"]) > 0.01 else []
    reference = {"schema_version": 1, "source_path": str(source), "source_sha256": digest,
                 "range_seconds": [start, end], "sample_interval_seconds": interval,
                 "media": media, "samples": [{**sample, "path": str(frame)} for sample, frame in zip(samples, frames)],
                 "contact_sheets": sheets, "observations": [], "inferences": [], "claims": [],
                 "analysis_status": "frames_ready_for_visual_review",
                 "warnings": warnings,
                 "files": [{"path": str(path), "sha256": file_hash(path)} for path in all_files]}
    write_json(manifest_path, reference)
    return {"status": "prepared", "artifacts": [str(manifest_path), *sheets], "reference": reference, "reused": False, "warnings": warnings}


def register_commands(subparsers):
    parser = subparsers.add_parser("reference", help="Prepare local reference images or video")
    commands = parser.add_subparsers(dest="reference_command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--project", required=True)
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--range", dest="time_range")
    prepare.add_argument("--interval", type=float, default=0.25)
    prepare.set_defaults(handler=lambda args: prepare_reference(args.project, args.input, args.time_range, args.interval))
