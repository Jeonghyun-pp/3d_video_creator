"""Render an internal A/B aerial concept shot from one generated image.

This is a 2D digital push, not 3D camera travel or a surveyed DDP view.
Usage: python scripts/create_exterior_concept.py [--reference ddp_reference.mp4]
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from shot_qa import review


PROJECT = Path(__file__).resolve().parents[1] / "projects" / "ddp_reference_pilot"
ASSET_MANIFEST = PROJECT / "assets" / "asset_manifest.json"
ASSET = PROJECT / "assets" / "generated" / "ddp_aerial_concept_v1.png"
OUT = PROJECT / "exterior"
VIDEO = OUT / "exterior_aerial_concept_pan.mp4"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="Optional source for 0.25-second comparison QA")
    args = parser.parse_args()
    asset = next(item for item in json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))["assets"]
                 if item["id"] == "ddp_aerial_concept_v1")
    actual_hash = hashlib.sha256(ASSET.read_bytes()).hexdigest()
    if actual_hash != asset["sha256"]:
        raise ValueError(f"Asset hash changed: {ASSET}")
    OUT.mkdir(parents=True, exist_ok=True)
    filters = ("zoompan=z='1+0.10*on/149':x='(iw-iw/zoom)/2+10*on/149':"
               "y='(ih-ih/zoom)/2':d=1:s=1080x1920:fps=30,"
               "eq=contrast=1.025:saturation=1.025,unsharp=5:5:0.25")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "30",
                    "-i", str(ASSET), "-vf", filters, "-frames:v", "150", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(VIDEO)], check=True)
    qa = review(VIDEO, OUT / "qa_aerial", args.reference)
    manifest = {"video": str(VIDEO.relative_to(PROJECT)), "source": asset["source"],
                "source_sha256": actual_hash, "source_type": "AI-generated concept image",
                "use_status": "internal_preview_only", "architecture_rights": asset["architecture_rights"],
                "geometry_accuracy": asset["geometry_accuracy"],
                "method": "single-image 2D digital push, 1.00x to 1.10x; no camera parallax, 3D model, or moving panels",
                "duration_seconds": 5.0, "size": [1080, 1920], "fps": 30,
                "qa_report": str((OUT / "qa_aerial" / "report.json").relative_to(PROJECT)),
                "qa_sample_count": qa["sample_count"],
                "reference_gap": "Aerial composition is closer than the ground photo, but the building geometry is unverified and the reference's 2-5 second panel separation/reassembly is absent. Does not establish visual parity."}
    (OUT / "aerial_concept_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
