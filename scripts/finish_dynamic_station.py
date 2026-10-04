"""Add narration and editorial overlays to the 3D station pilot frames."""

from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects/dynamic_station_pilot"
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
WIDTH, HEIGHT = 720, 1280
SHOTS = [
    ("지하철역 아래에는?", "지상에서는 입구만 보이지만,"),
    ("대합실과 개찰구", "아래에는 대합실이 있고,"),
    ("승강장과 열차", "더 내려가면 승강장과 열차가 이어집니다."),
]


def overlay(index, heading, caption):
    path = PROJECT / f"assets/overlay_{index}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 45, 540, 113), radius=12, fill=(8, 17, 24, 180))
    draw.rectangle((30, 45, 39, 113), fill=(235, 65, 61, 255))
    draw.text((57, 58), heading, font=ImageFont.truetype(FONT, 42), fill="white")
    draw.rounded_rectangle((22, 1145, WIDTH-22, 1213), radius=12, fill=(6, 12, 18, 195))
    draw.text((WIDTH/2, 1179), caption, font=ImageFont.truetype(FONT, 30),
              fill="white", anchor="mm")
    draw.text((WIDTH/2, 1251), "설명용 개념도 · 실제 역 구조와 다를 수 있음",
              font=ImageFont.truetype(FONT, 19), fill=(255, 255, 255, 225), anchor="mm")
    image.save(path)
    return path


def main():
    frames = PROJECT / "renders/frames"
    found = list(frames.glob("frame_*.png"))
    if len(found) != 192:
        raise RuntimeError(f"Expected 192 rendered frames, found {len(found)}")
    images = [overlay(i, *shot) for i, shot in enumerate(SHOTS, 1)]
    audio = [PROJECT / f"audio/line_0{i}.aiff" for i in range(1, 4)]
    destination = PROJECT / "final/dynamic_pilot.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    filtergraph = (
        "[0:v][1:v]overlay=0:0:enable='between(t,0,2)'[v1];"
        "[v1][2:v]overlay=0:0:enable='between(t,2,5)'[v2];"
        "[v2][3:v]overlay=0:0:enable='between(t,5,8)'[v];"
        "[4:a]adelay=300|300[a1];[5:a]adelay=2150|2150[a2];"
        "[6:a]adelay=5000|5000[a3];"
        "[a1][a2][a3]amix=inputs=3:duration=longest:normalize=0,"
        "apad,atrim=duration=8[a]"
    )
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-framerate", "24", "-i", str(frames / "frame_%06d.png")]
    for path in images:
        command += ["-loop", "1", "-i", str(path)]
    for path in audio:
        command += ["-i", str(path)]
    command += ["-filter_complex", filtergraph, "-map", "[v]", "-map", "[a]",
                "-t", "8", "-r", "24", "-c:v", "libx264", "-preset", "medium",
                "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(destination)]
    subprocess.run(command, check=True)
    print(destination)


if __name__ == "__main__":
    main()
