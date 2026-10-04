"""Render the illustrative subway cutaway quality pilot."""

from pathlib import Path
import math
import subprocess

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects/quality_pilot_subway"
WIDTH, HEIGHT, FPS, SECONDS = 1080, 1920, 30, 8
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
RED = (243, 71, 70)


def ease(value):
    value = max(0, min(1, value))
    return value * value * (3 - 2 * value)


def text(draw, xy, value, size, opacity=255, anchor=None):
    font = ImageFont.truetype(FONT, size)
    x, y = xy
    draw.text((x + 2, y + 3), value, font=font, fill=(0, 0, 0, int(opacity * .8)), anchor=anchor)
    draw.text((x, y), value, font=font, fill=(255, 255, 255, opacity), anchor=anchor)


def frame(source, time):
    # Slow camera push, with a small move toward the station platform.
    zoom = 1 + .065 * ease(time / SECONDS)
    crop_width, crop_height = WIDTH / zoom, HEIGHT / zoom
    center_x = WIDTH / 2
    center_y = HEIGHT / 2 + 65 * ease(time / SECONDS)
    left, top = center_x - crop_width / 2, center_y - crop_height / 2
    image = source.crop((round(left), round(top), round(left + crop_width), round(top + crop_height)))
    image = image.resize((WIDTH, HEIGHT), Image.Resampling.BICUBIC).convert("RGBA")
    overlay = Image.new("RGBA", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle((40, 52, 355, 111), radius=12, fill=(12, 20, 26, 195))
    draw.rectangle((40, 52, 49, 111), fill=RED)
    text(draw, (68, 70), "UNDER THE CITY", 25)

    title_opacity = round(255 * (1 - ease((time - 1.8) / .5)))
    if title_opacity:
        text(draw, (WIDTH / 2, 300), "지하철역 아래에는?", 73, title_opacity, "mm")

    details = [
        ("01  대합실", (565, 590), (365, 610), 1.7, 4.25),
        ("02  승강장", (765, 1350), (500, 1430), 4.1, 7.6),
    ]
    for label, point, label_at, begin, end in details:
        opacity = ease((time - begin) / .35) * (1 - ease((time - end) / .35))
        if opacity <= 0:
            continue
        alpha = round(255 * opacity)
        px, py = (point[0] - left) * zoom, (point[1] - top) * zoom
        lx, ly = (label_at[0] - left) * zoom, (label_at[1] - top) * zoom
        draw.line(((lx, ly + 48), (px, py)), fill=(*RED, alpha), width=4)
        radius = 15 + 5 * math.sin(time * 3)
        draw.ellipse((px-radius, py-radius, px+radius, py+radius), outline=(*RED, alpha), width=5)
        draw.ellipse((px-5, py-5, px+5, py+5), fill=(*RED, alpha))
        text(draw, (lx, ly), label, 49, alpha)

    subtitles = [(0, 2.15, "지상에서는 입구만 보이지만,"),
                 (2.15, 4.5, "아래에는 대합실이 있고,"),
                 (4.5, 8, "더 내려가면 승강장과 열차가 이어집니다.")]
    subtitle = next((line for start, end, line in subtitles if start <= time < end), "")
    draw.rounded_rectangle((39, 1730, 1041, 1817), radius=14, fill=(9, 16, 23, 185))
    text(draw, (WIDTH / 2, 1773), subtitle, 42, 255, "mm")
    text(draw, (WIDTH / 2, 1882), "설명용 개념도 · 실제 역사 구조와 다를 수 있음", 26, 235, "mm")
    return Image.alpha_composite(image, overlay).convert("RGB")


def main():
    source = Image.open(PROJECT / "assets/cutaway_keyframe.png").convert("RGB")
    source = source.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    destination = PROJECT / "final/quality_pilot.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
               *[arg for index in range(1, 4) for arg in ("-i", str(PROJECT / f"audio/line_0{index}.aiff"))],
               "-filter_complex", "[1:a]adelay=300|300[a1];[2:a]adelay=2150|2150[a2];"
               "[3:a]adelay=4500|4500[a3];[a1][a2][a3]amix=inputs=3:duration=longest:normalize=0,"
               "apad,atrim=duration=8[a]",
               "-map", "0:v", "-map", "[a]", "-t", str(SECONDS), "-c:v", "libx264",
               "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for index in range(FPS * SECONDS):
            process.stdin.write(frame(source, index / FPS).tobytes())
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("ffmpeg failed")
    except Exception:
        process.kill()
        process.wait()
        raise
    print(destination)


if __name__ == "__main__":
    main()
