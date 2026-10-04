"""Cut a CC0 station photograph and the 3D cutaway into a reviewed pilot."""

from pathlib import Path
import json
import subprocess

from PIL import Image, ImageDraw, ImageFont


PROJECT = Path(__file__).resolve().parents[1] / "projects/dynamic_station_pilot"
ASSETS = PROJECT / "assets"
FRAMES = PROJECT / "renders/pbr_frames"
FINAL = PROJECT / "final"
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
SIZE = (720, 1280)


def run(*args):
    subprocess.run(args, check=True)


def overlay(index, title, caption, source=""):
    canvas = Image.new("RGBA", SIZE)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((24, 42, 664, 132), radius=15, fill=(7, 15, 24, 195))
    draw.rounded_rectangle((24, 42, 34, 132), radius=4, fill=(238, 67, 57, 255))
    draw.text((55, 58), title, font=ImageFont.truetype(FONT, 43), fill=(255, 255, 255))
    draw.rounded_rectangle((24, 1116, 696, 1204), radius=15, fill=(7, 15, 24, 198))
    draw.text((360, 1160), caption, anchor="mm", font=ImageFont.truetype(FONT, 30),
              fill=(255, 255, 255))
    if source:
        draw.text((360, 1243), source, anchor="mm", font=ImageFont.truetype(FONT, 20),
                  fill=(240, 242, 245, 225))
    path = ASSETS / f"pbr_overlay_{index}.png"
    canvas.save(path)
    return path


def main():
    present = sorted(FRAMES.glob("frame_*.png"))
    if len(present) != 144 or present[0].stem != "frame_000049" or present[-1].stem != "frame_000192":
        raise RuntimeError(f"Expected complete frames 49–192, found {len(present)}")
    FINAL.mkdir(parents=True, exist_ok=True)
    photo = ASSETS / "station_photo_vertical.jpg"
    run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i",
        str(ASSETS / "cc0/subway_entrance_tonemapped.jpg"), "-vf",
        "v360=input=equirect:output=rectilinear:yaw=0:pitch=0:h_fov=72:v_fov=95:w=720:h=1280",
        "-frames:v", "1", str(photo))
    overlays = [
        overlay(1, "지하철역 아래에는?", "지상에서는 입구만 보이지만,",
                "실제 역 사진: 암스테르담 · Poly Haven CC0"),
        overlay(2, "대합실과 개찰구", "아래에는 대합실이 있고,",
                "설명용 3D 재구성 · 실제 역 구조와 다름"),
        overlay(3, "승강장과 열차", "더 내려가면 승강장과 열차가 이어집니다.",
                "설명용 3D 재구성 · 실제 역 구조와 다름"),
    ]
    audio = [PROJECT / f"audio/line_0{i}.aiff" for i in range(1, 4)]
    destination = FINAL / "pbr_pilot_720.mp4"
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-loop", "1", "-framerate", "24", "-t", "2", "-i", str(photo),
               "-framerate", "24", "-start_number", "49", "-i", str(FRAMES / "frame_%06d.png")]
    for path in overlays:
        command += ["-loop", "1", "-i", str(path)]
    for path in audio:
        command += ["-i", str(path)]
    graph = (
        "[0:v]trim=duration=2,setpts=PTS-STARTPTS,"
        "zoompan=z='min(zoom+0.0007,1.045)':d=1:s=720x1280:fps=24[photo];"
        "[1:v]trim=duration=6,setpts=PTS-STARTPTS[model];"
        "[photo][model]concat=n=2:v=1:a=0[base];"
        "[base][2:v]overlay=0:0:enable='lt(t,2)'[v1];"
        "[v1][3:v]overlay=0:0:enable='between(t,2,4.999)'[v2];"
        "[v2][4:v]overlay=0:0:enable='gte(t,5)'[full];"
        "[full]scale=in_range=pc:out_range=tv,format=yuv420p[v];"
        "[5:a]adelay=300|300[a1];[6:a]adelay=2150|2150[a2];"
        "[7:a]adelay=5000|5000[a3];"
        "[a1][a2][a3]amix=inputs=3:duration=longest:normalize=0,"
        "apad,atrim=duration=8[a]"
    )
    command += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", "8",
                "-r", "24", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(destination)]
    run(*command)
    upload = FINAL / "pbr_pilot_1080.mp4"
    run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(destination),
        "-vf", "scale=1080:1920:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
        "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(upload))
    (FINAL / "pbr_pilot_sources.json").write_text(json.dumps({
        "scope": "Generic conceptual station, not a verified real station",
        "photo": "https://polyhaven.com/a/subway_entrance",
        "textures": sorted({item["source"] for item in json.loads((ASSETS / "cc0/manifest.json").read_text())}),
        "license": "CC0",
        "render": "Blender 5.2.2, Cycles Metal, 720x1280, 24 samples, denoised",
        "upload": "1080x1920 upscale from the 720x1280 render",
    }, ensure_ascii=False, indent=2) + "\n")
    print(destination, upload, sep="\n")


if __name__ == "__main__":
    main()
