"""Render a DDP photo pan for internal exterior-source evaluation.

This is a 2D crop of one photograph, not a 3D camera move or aerial recreation.
Usage: python scripts/create_exterior_pilot.py [--reference ddp_reference.mp4]
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from shot_qa import review


PROJECT = Path(__file__).resolve().parents[1] / "projects" / "ddp_reference_pilot"
ASSET = PROJECT / "assets" / "reference_licensed" / "ddp2369_cc0.jpg"
ASSET_MANIFEST = PROJECT / "assets" / "asset_manifest.json"
OUT = PROJECT / "exterior"
VIDEO = OUT / "exterior_photo_pan.mp4"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="Optional video for 0.25-second comparison QA")
    args = parser.parse_args()
    asset = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))["assets"][0]
    actual_hash = hashlib.sha256(ASSET.read_bytes()).hexdigest()
    if actual_hash != asset["sha256"]:
        raise ValueError(f"Asset hash changed: {ASSET}")
    OUT.mkdir(parents=True, exist_ok=True)
    # Full-height portrait crop of the 5311 x 3541 ground-level source.
    # Its horizontal shift is a 2D pan; it cannot introduce parallax.
    crop = "crop=1992:3540:x='600+1700*n/149':y=0"
    filters = f"{crop},scale=1080:1920:flags=lanczos,eq=contrast=1.04:saturation=1.03,unsharp=5:5:0.35"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "30",
                    "-i", str(ASSET), "-vf", filters, "-frames:v", "150", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(VIDEO)], check=True)
    qa = review(VIDEO, OUT / "qa", args.reference)
    manifest = {"video": str(VIDEO.relative_to(PROJECT)), "source": asset["source"],
                "source_sha256": actual_hash, "source_license": asset["photo_license"],
                "use_status": "internal_preview_only", "architecture_rights": asset["architecture_rights"],
                "method": "single-photo 2D horizontal crop pan; no camera parallax or 3D model",
                "duration_seconds": 5.0, "size": [1080, 1920], "fps": 30,
                "qa_report": str((OUT / "qa" / "report.json").relative_to(PROJECT)),
                "qa_sample_count": qa["sample_count"],
                "reference_gap": "Ground-level facade close-up; reference begins with aerial full-building view and animated exploded elements. Does not establish visual parity."}
    (OUT / "shot_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
