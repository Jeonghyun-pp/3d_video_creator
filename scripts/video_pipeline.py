#!/usr/bin/env python3
"""Reproducible local renderer and editor for technical shorts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "templates" / "scene.schema.json"
EXAMPLE = ROOT / "projects" / "example_subway_station"
DIRECTORIES = ("assets", "blender", "renders", "ai_clips", "audio", "subtitles", "final")


class ProductionError(Exception):
    pass


def run(args: list[str], *, log: Path | None = None, timeout: int = 3600) -> None:
    print("$", " ".join(str(x) for x in args), flush=True)
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ProductionError(f"Command timed out after {timeout}s: {args[0]}") from error
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise ProductionError(f"Command failed ({result.returncode}): {args[0]}\n{result.stdout[-4000:]}")


def output(args: list[str]) -> str:
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise ProductionError(f"Command timed out after 30s: {args[0]}") from error
    if result.returncode:
        raise ProductionError(result.stderr.strip() or f"Command failed: {args[0]}")
    return result.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frames(seconds: int | float, fps: int) -> int:
    value = Decimal(str(seconds)) * fps
    if value != int(value):
        raise ProductionError(f"Time {seconds}s is not aligned to {fps} fps")
    return int(value)


def inside_project(project: Path, relative: str) -> Path:
    path = (project / relative).resolve()
    if not path.is_relative_to(project.resolve()):
        raise ProductionError(f"Media path escapes project: {relative}")
    return path


def load_scene(project: Path) -> dict:
    path = project / "scene.json"
    if not path.is_file():
        raise ProductionError(f"Missing {path}")
    scene = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(scene), key=lambda error: list(map(str, error.path)))
    if errors:
        raise ProductionError("Scene schema errors:\n" + "\n".join(f"  {list(error.path)}: {error.message}" for error in errors))
    object_ids = [obj["id"] for obj in scene["objects"]]
    if len(object_ids) != len(set(object_ids)):
        raise ProductionError("Duplicate object IDs")
    ids = [shot["id"] for shot in scene["scenes"]]
    if len(ids) != len(set(ids)):
        raise ProductionError("Duplicate scene IDs")
    cursor = Decimal(0)
    fps = scene["fps"]
    for shot in scene["scenes"]:
        start = Decimal(str(shot["start"]))
        duration = Decimal(str(shot["duration"]))
        if start != cursor:
            raise ProductionError(f"{shot['id']}: starts at {start}s, expected {cursor}s")
        frames(shot["start"], fps)
        frames(shot["duration"], fps)
        unknown = set(shot["visible_objects"]) - set(object_ids)
        if unknown:
            raise ProductionError(f"{shot['id']}: unknown objects {sorted(unknown)}")
        source = shot.get("source", "blender")
        if source != "blender":
            if not shot.get("media"):
                raise ProductionError(f"{shot['id']}: {source} source requires media path")
            media = inside_project(project, shot["media"])
            if not media.is_file():
                raise ProductionError(f"{shot['id']}: missing media {media}")
        if shot["animation"]["type"] == "exploded_view" and not all(
            key in shot["animation"] for key in ("axis", "distance")
        ):
            raise ProductionError(f"{shot['id']}: exploded_view needs axis and distance")
        cursor += duration
    if cursor != Decimal(str(scene["duration"])):
        raise ProductionError(f"Scene timeline ends at {cursor}s, declared {scene['duration']}s")
    if scene["scenes"][-1]["transition"] != "end":
        raise ProductionError("Last scene must have transition=end")
    return scene


def source_approval(project: Path, scene: dict) -> None:
    review_path = project / "review.json"
    if not review_path.is_file():
        raise ProductionError("Final build requires review.json with facts_approved and script_approved")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("facts_approved") is not True or review.get("script_approved") is not True:
        raise ProductionError("Facts and script must be approved for final build")
    sources = (project / "sources.md").read_text(encoding="utf-8") if (project / "sources.md").is_file() else ""
    if "https://" not in sources and "http://" not in sources:
        raise ProductionError("Final build requires source URLs in sources.md")
    if "입력하세요" in scene["key_message"] or any("입력하세요" in shot["narration"] for shot in scene["scenes"]):
        raise ProductionError("Replace generated placeholder message and narration before final build")
    asset_manifest = project / "assets" / "asset_manifest.json"
    if asset_manifest.is_file():
        asset_data = json.loads(asset_manifest.read_text(encoding="utf-8"))
        if review.get("assets_approved") is not True or review.get("visual_approved") is not True:
            raise ProductionError("Final build requires human asset-rights and visual approval")
        listed_media = set()
        for asset in asset_data.get("assets", []):
            asset_path = inside_project(project / "assets", asset["path"])
            listed_media.add(asset_path.resolve())
            if not asset_path.is_file() or sha256(asset_path) != asset.get("sha256"):
                raise ProductionError(f"Asset missing or changed: {asset['path']}")
            if asset.get("use_status") != "publish_ok":
                raise ProductionError(f"Asset is not cleared for publication: {asset['path']}")
        for shot in scene["scenes"]:
            if shot.get("source") in ("image", "video"):
                media_path = inside_project(project, shot["media"])
                if media_path.resolve() not in listed_media:
                    raise ProductionError(f"Scene media is absent from asset_manifest.json: {shot['media']}")


def font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    if shutil.which("fc-match"):   # fontconfig is optional (macOS has none by default)
        candidates += [Path(line) for line in output(["fc-match", "sans:lang=ko", "-f", "%{file}\\n"]).splitlines() if line]
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                pass
    raise ProductionError("No usable font found for captions")


def wrap_text(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=face)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def caption_image(shot: dict, width: int, height: int, path: Path, *, preview: bool) -> None:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text = shot["narration"].strip()
    size = max(18, round(width * 0.050))
    face = font(size)
    max_width = round(width * 0.86)
    lines = wrap_text(draw, text, face, max_width)
    while len(lines) > 3 and size > 18:
        size -= 2
        face = font(size)
        lines = wrap_text(draw, text, face, max_width)
    if lines:
        spacing = round(size * 0.35)
        box = draw.multiline_textbbox((0, 0), "\n".join(lines), font=face, spacing=spacing, align="center")
        text_height = box[3] - box[1]
        pad = round(width * 0.04)
        bottom = round(height * 0.89)
        top = bottom - text_height - 2 * pad
        draw.rounded_rectangle((round(width * 0.05), top, round(width * 0.95), bottom), radius=pad, fill=(6, 16, 26, 208))
        draw.multiline_text((width / 2, top + pad - box[1]), "\n".join(lines), font=face,
                            fill=(255, 255, 255, 255), anchor="ma", spacing=spacing, align="center")
    if preview:
        label_face = font(max(13, round(width * 0.032)))
        draw.rounded_rectangle((round(width * .05), round(height * .045), round(width * .95), round(height * .11)),
                               radius=12, fill=(140, 30, 30, 220))
        draw.text((width / 2, round(height * .077)), "미리보기 · 사실 검증 전", font=label_face,
                  anchor="mm", fill="white")
    if shot["labels"]:
        label_face = font(max(13, round(width * .037)))
        x, y = round(width * .06), round(height * (.135 if preview else .065))
        pad_x, pad_y = round(width * .025), round(width * .016)
        for label in shot["labels"]:
            box = draw.textbbox((0, 0), label, font=label_face)
            chip_width = box[2] - box[0] + pad_x * 2
            chip_height = box[3] - box[1] + pad_y * 2
            if x + chip_width > width * .94:
                x, y = round(width * .06), y + chip_height + 5
            draw.rounded_rectangle((x, y, x + chip_width, y + chip_height), radius=pad_y,
                                   fill=(11, 35, 58, 220), outline=(125, 197, 220, 255), width=1)
            draw.text((x + pad_x, y + pad_y - box[1]), label, font=label_face, fill="white")
            x += chip_width + 5
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def validate_video(path: Path, width: int, height: int, fps: int, duration: float) -> dict:
    data = json.loads(output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
    videos = [stream for stream in data["streams"] if stream["codec_type"] == "video"]
    audios = [stream for stream in data["streams"] if stream["codec_type"] == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise ProductionError(f"Expected one video and one audio stream: {path}")
    video = videos[0]
    if video["codec_name"] != "h264" or audios[0]["codec_name"] != "aac" or video.get("pix_fmt") != "yuv420p":
        raise ProductionError("Output must be H.264 yuv420p video with AAC audio")
    if (video["width"], video["height"]) != (width, height):
        raise ProductionError(f"Wrong dimensions: {video['width']}x{video['height']}")
    numerator, denominator = map(int, video["avg_frame_rate"].split("/"))
    if numerator / denominator != fps:
        raise ProductionError(f"Wrong frame rate: {video['avg_frame_rate']}")
    actual = float(data["format"]["duration"])
    if abs(actual - duration) > max(.15, 2 / fps):
        raise ProductionError(f"Wrong duration: {actual}s, expected {duration}s")
    return {"width": width, "height": height, "fps": fps, "duration": actual,
            "video_codec": video["codec_name"], "audio_codec": audios[0]["codec_name"]}


def narration_track(project: Path, scene: dict, work: Path, ffmpeg: str) -> Path | None:
    whole = project / "audio" / "narration.wav"
    if whole.is_file():
        length = float(json.loads(output(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(whole)]))["format"]["duration"])
        if length > float(scene["duration"]) + .1:
            raise ProductionError(f"Narration is {length}s, longer than video {scene['duration']}s")
        return whole
    clips = [project / "audio" / f"{shot['id']}.wav" for shot in scene["scenes"]]
    found = [clip.is_file() for clip in clips]
    if not any(found):
        return None
    if not all(found):
        raise ProductionError("Provide narration.wav or one WAV per scene; partial scene audio is not allowed")
    for shot, clip in zip(scene["scenes"], clips):
        length = float(json.loads(output(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(clip)]))["format"]["duration"])
        if length > float(shot["duration"]) + .1:
            raise ProductionError(f"{shot['id']}: narration {length}s exceeds scene {shot['duration']}s")
    filters = []
    for index, shot in enumerate(scene["scenes"]):
        seconds = shot["duration"]
        filters.append(f"[{index}:a]aresample=48000,apad=pad_dur={seconds},atrim=duration={seconds},asetpts=N/SR/TB[a{index}]")
    filters.append("".join(f"[a{index}]" for index in range(len(clips))) +
                   f"concat=n={len(clips)}:v=0:a=1[out]")
    destination = work / "narration.wav"
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *[arg for clip in clips for arg in ("-i", str(clip))],
         "-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "pcm_s16le", str(destination)],
        log=work / "narration.log")
    return destination


def write_srt(project: Path, scene: dict) -> Path:
    def clock_time(seconds: Decimal) -> str:
        milliseconds = round(seconds * 1000)
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        whole_seconds, fraction = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{whole_seconds:02},{fraction:03}"

    cues = []
    for index, shot in enumerate(scene["scenes"], 1):
        start = Decimal(str(shot["start"]))
        end = start + Decimal(str(shot["duration"]))
        cues.append(f"{index}\n{clock_time(start)} --> {clock_time(end)}\n{shot['narration']}\n")
    path = project / "subtitles" / "narration.srt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(cues), encoding="utf-8")
    return path


def contact_sheet(video: Path, scene: dict, ffmpeg: str) -> Path:
    shots = scene["scenes"]
    tile_width, tile_height = 270, 480
    columns = min(3, len(shots))
    rows = (len(shots) + columns - 1) // columns
    sheet = Image.new("RGB", (tile_width * columns, tile_height * rows), (15, 20, 25))
    with tempfile.TemporaryDirectory() as temp:
        for index, shot in enumerate(shots):
            frame = Path(temp) / f"{index}.png"
            midpoint = float(shot["start"]) + float(shot["duration"]) / 2
            run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(midpoint), "-i", str(video),
                 "-frames:v", "1", str(frame)])
            with Image.open(frame) as image:
                tile = image.convert("RGB").resize((tile_width, tile_height), Image.Resampling.LANCZOS)
                sheet.paste(tile, ((index % columns) * tile_width, (index // columns) * tile_height))
    destination = video.with_suffix(".contact_sheet.jpg")
    sheet.save(destination, quality=88)
    return destination


def build(project: Path, *, preview: bool, force: bool, review: bool = False) -> Path:
    if preview and review:
        raise ProductionError("Choose preview or review, not both")
    scene = load_scene(project)
    internal = preview or review
    if not internal:
        source_approval(project, scene)
    subtitle_file = write_srt(project, scene)
    width, height = (360, 640) if preview else (720, 1280) if review else (1080, 1920)
    fps = 12 if preview else scene["fps"]
    for shot in scene["scenes"]:
        frames(shot["start"], fps)
        frames(shot["duration"], fps)
    blender = shutil.which(os.environ.get("STUDIO_BLENDER", "blender"))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ProductionError("ffmpeg is not installed")
    source_blend = project / "blender" / "source.blend"
    has_blender = any(shot.get("source", "blender") == "blender" for shot in scene["scenes"])
    if has_blender:
        if not blender:
            raise ProductionError("Blender is not installed")
        if not source_blend.is_file():
            raise ProductionError(f"Missing {source_blend}. Create a .blend with collections matching object IDs")
    blender_version = output([blender, "--background", "--version"]).splitlines()[0] if has_blender else None
    blender_wrapper_hash = sha256(Path(blender)) if has_blender and Path(blender).suffix == ".sh" else None
    render_engine = os.environ.get("STUDIO_RENDER_ENGINE", "wrapper-default")
    ffmpeg_version = output([ffmpeg, "-version"]).splitlines()[0]
    profile = "preview" if preview else "review" if review else "master"
    work = project / "renders" / profile
    work.mkdir(parents=True, exist_ok=True)
    segments = []
    scene_hash = sha256(project / "scene.json")
    blend_hash = sha256(source_blend) if source_blend.exists() else None
    for shot in scene["scenes"]:
        shot_dir = work / shot["id"]
        shot_dir.mkdir(parents=True, exist_ok=True)
        segment = shot_dir / "segment.mp4"
        media = inside_project(project, shot["media"]) if shot.get("media") else None
        visual_input = {key: shot[key] for key in ("duration", "camera", "visible_objects", "animation")}
        render_fingerprint = hashlib.sha256(json.dumps({"visual": visual_input,
            "width": width, "height": height, "fps": fps,
            "blend": blend_hash if shot.get("source", "blender") == "blender" else None,
            "blender_version": blender_version,
            "blender_wrapper_hash": blender_wrapper_hash,
            "render_engine": render_engine,
            "renderer": sha256(Path(__file__).with_name("blender_render.py"))}, sort_keys=True).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps({"shot": shot, "profile": profile,
            "width": width, "height": height, "fps": fps,
            "render": render_fingerprint,
            "media": sha256(media) if media else None,
            "ffmpeg_version": ffmpeg_version,
            "pipeline": sha256(Path(__file__))}, sort_keys=True).encode()).hexdigest()
        stamp = shot_dir / "fingerprint.txt"
        if not force and segment.is_file() and stamp.is_file() and stamp.read_text() == fingerprint:
            print(f"Reuse {shot['id']}")
            segments.append(segment)
            continue
        frame_count = frames(shot["duration"], fps)
        source = shot.get("source", "blender")
        if source == "blender":
            frames_dir = shot_dir / "frames"
            frames_stamp = shot_dir / "frames.fingerprint.txt"
            if force or not frames_stamp.is_file() or frames_stamp.read_text() != render_fingerprint or \
                    len(list(frames_dir.glob("*.png"))) != frame_count:
                if frames_dir.exists():
                    shutil.rmtree(frames_dir)
                frames_dir.mkdir()
                run([blender, "--background", str(source_blend), "--python-exit-code", "1", "--python",
                     str(ROOT / "scripts" / "blender_render.py"), "--", str(project / "scene.json"), shot["id"],
                     str(frames_dir), str(width), str(height), str(fps)], log=shot_dir / "blender.log")
                actual_frames = len(list(frames_dir.glob("*.png")))
                if actual_frames != frame_count:
                    raise ProductionError(f"{shot['id']}: Blender rendered {actual_frames}/{frame_count} frames")
                frames_stamp.write_text(render_fingerprint)
            else:
                print(f"Reuse frames {shot['id']}")
            video_input = ["-framerate", str(fps), "-start_number", "1", "-i", str(frames_dir / "%06d.png")]
        elif source == "image":
            video_input = ["-loop", "1", "-i", str(media)]
        else:
            probe = json.loads(output(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(media)]))
            if float(probe["format"]["duration"]) + .05 < float(shot["duration"]):
                raise ProductionError(f"{shot['id']}: video source is shorter than scene")
            video_input = ["-i", str(media)]
        caption = shot_dir / "caption.png"
        caption_image(shot, width, height, caption, preview=internal)
        temporary = shot_dir / "segment.tmp.mp4"
        if temporary.exists():
            temporary.unlink()
        filters = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[base];"
                   "[base][1:v]overlay=0:0:shortest=1[out]")
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *video_input, "-loop", "1", "-i", str(caption),
             "-filter_complex", filters, "-map", "[out]", "-frames:v", str(frame_count), "-an", "-c:v", "libx264",
             "-preset", "fast", "-crf", "23" if preview else "18", "-pix_fmt", "yuv420p", "-r", str(fps),
             str(temporary)], log=shot_dir / "ffmpeg.log")
        temporary.replace(segment)
        stamp.write_text(fingerprint)
        segments.append(segment)
    concat_file = work / "concat.txt"
    concat_file.write_text("".join(f"file '{shot['id']}/segment.mp4'\n" for shot in scene["scenes"]))
    joined = work / "joined.mp4"
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-c", "copy", str(joined)], log=work / "concat.log")
    narration = narration_track(project, scene, work, ffmpeg)
    if not internal and narration is None:
        raise ProductionError("Final build requires audio/narration.wav or one WAV per scene")
    audio_input = ["-i", str(narration)] if narration else ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    final_dir = project / "final"
    final_dir.mkdir(exist_ok=True)
    destination = final_dir / f"{profile}.mp4"
    temporary = final_dir / f".{profile}.tmp.mp4"
    if temporary.exists():
        temporary.unlink()
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(joined), *audio_input,
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-b:a", "128k",
         "-af", "apad", "-t", str(scene["duration"]), "-movflags", "+faststart", str(temporary)], log=work / "audio.log")
    metadata = validate_video(temporary, width, height, fps, float(scene["duration"]))
    if sha256(project / "scene.json") != scene_hash or (has_blender and sha256(source_blend) != blend_hash):
        raise ProductionError("Scene or Blender source changed during build; rerun to avoid mixed versions")
    if not internal and destination.is_file() and sha256(destination) != sha256(temporary):
        history = final_dir / "history"
        history.mkdir(exist_ok=True)
        old_hash = sha256(destination)[:12]
        archived = history / f"master_{old_hash}.mp4"
        if not archived.exists():
            shutil.copy2(destination, archived)
            previous_manifest = final_dir / "master.manifest.json"
            if previous_manifest.is_file():
                shutil.copy2(previous_manifest, history / f"master_{old_hash}.manifest.json")
    temporary.replace(destination)
    sheet = contact_sheet(destination, scene, ffmpeg)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "profile": profile,
                "scene_sha256": scene_hash, "source_blend_sha256": blend_hash,
                "blender_version": blender_version, "ffmpeg_version": ffmpeg_version,
                "render_engine": render_engine,
                "narration_sha256": sha256(narration) if narration else None,
                "output_sha256": sha256(destination), "output": str(destination),
                "contact_sheet": str(sheet), "subtitles": str(subtitle_file), "media": metadata}
    (final_dir / f"{profile}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {destination}")
    return destination


def create_project(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        raise ProductionError("Slug must use lowercase letters, digits, _ or -")
    project = ROOT / "projects" / slug
    if project.exists():
        raise ProductionError(f"Project already exists: {project}")
    for directory in DIRECTORIES:
        (project / directory).mkdir(parents=True, exist_ok=True)
    scene = {
        "title": slug.replace("_", " "), "slug": slug, "duration": 4, "aspect_ratio": "9:16", "fps": 30,
        "key_message": "핵심 메시지를 입력하세요", "objects": [{"id": "main", "type": "structure"}],
        "scenes": [{"id": "scene_01", "start": 0, "duration": 4, "narration": "내레이션을 입력하세요.",
                    "visual_goal": "핵심 구조를 보여준다", "camera": {"type": "orthographic", "movement": "static_iso"},
                    "visible_objects": ["main"], "animation": {"type": "none"}, "labels": [], "transition": "end"}]
    }
    (project / "scene.json").write_text(json.dumps(scene, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (project / "topic.md").write_text(f"# Topic\n\n{scene['title']}\n", encoding="utf-8")
    (project / "sources.md").write_text("# Sources\n\n", encoding="utf-8")
    (project / "script.md").write_text("# Script\n\n", encoding="utf-8")
    (project / "review.json").write_text('{"facts_approved": false, "script_approved": false}\n')
    return project


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    new = sub.add_parser("new")
    new.add_argument("slug")
    for name in ("validate", "build"):
        command = sub.add_parser(name)
        command.add_argument("project", type=Path)
        if name == "build":
            profile_group = command.add_mutually_exclusive_group()
            profile_group.add_argument("--preview", action="store_true")
            profile_group.add_argument("--review", action="store_true")
            command.add_argument("--force", action="store_true")
    example = sub.add_parser("prepare-example")
    example.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            for tool in ("blender", "ffmpeg", "ffprobe", "fc-match"):
                executable = os.environ.get("STUDIO_BLENDER", "blender") if tool == "blender" else tool
                location = shutil.which(executable)
                if not location:
                    raise ProductionError(f"Missing required tool: {tool}")
                print(f"{tool}: {location}")
            print(output([shutil.which(os.environ.get("STUDIO_BLENDER", "blender")), "--background", "--version"]).splitlines()[0])
            print(output(["ffmpeg", "-version"]).splitlines()[0])
        elif args.command == "new":
            print(create_project(args.slug))
        elif args.command == "validate":
            scene = load_scene(args.project.resolve())
            print(f"Valid: {len(scene['scenes'])} scenes, {scene['duration']} seconds")
        elif args.command == "prepare-example":
            blender = shutil.which(os.environ.get("STUDIO_BLENDER", "blender"))
            if not blender:
                raise ProductionError("Blender is not installed")
            target = EXAMPLE / "blender" / "source.blend"
            if target.exists() and not args.force:
                raise ProductionError(f"Example model already exists: {target} (use --force to regenerate)")
            run([blender, "--background", "--python-exit-code", "1", "--python",
                 str(ROOT / "scripts" / "create_example_blend.py"), "--", str(target)])
            print(f"Illustrative, unverified model: {target}")
        else:
            build(args.project.resolve(), preview=args.preview, review=args.review, force=args.force)
    except (ProductionError, OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
