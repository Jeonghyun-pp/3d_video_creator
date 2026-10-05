# Setup — a fresh clone that builds the same scenes

```sh
git clone https://github.com/Jeonghyun-pp/3d_video_creator.git
cd 3d_video_creator
scripts/bootstrap.sh            # .venv, Blender check, Blender API index, Codex MCP config, doctor
```

## What you need

| Item | Version | Notes |
|---|---|---|
| Python | 3.12+ (measured on 3.14) | `requirements.txt`: jsonschema, Pillow. CAD factory (optional): `scripts/bootstrap.sh --cad` |
| Blender | **5.2.2** (official build, it bundles numpy) | Found via `STUDIO_BLENDER`, then `PATH`, then `/Applications/Blender.app` (macOS). Other versions build, but BUILD_REPORT numbers were measured on 5.2.2 |
| ffmpeg / ffprobe | 6+ | encode, QA, styles |
| Font | bundled | `library/fonts/pretendard` (SIL OFL 1.1): titles, captions and 3D text use the same glyphs on every OS |
| GPU | Apple Metal | `render_profile.py` asks for METAL only; on Linux/NVIDIA renders fall back to CPU (slow, same images). Not changed: the file is part of the render fingerprint |
| Paid generation (optional) | fal | `FAL_KEY` or `~/.config/fal/api_key`; every paid call waits for a reviewed, user-approved request |

Scratch narration uses macOS `say` (voice Yuna); elsewhere supply narration or use a paid voice.

## First build (no render)

```sh
.venv/bin/python -m studio project from-example --example samsung_cutaway --project projects/samsung_cutaway
.venv/bin/python -m studio shot build --project projects/samsung_cutaway --shot s01 --script examples/samsung_cutaway/author_samsung.py
```

The build is deterministic: same inputs and Blender version give the same objects, camera, environment digest and
exposure. Checked 2026-10-06: a build from `examples/samsung_cutaway` matched the working project's s01 v0026
(1,132 objects, identical camera samples, street digests and metered EV).

## What lives where

- `examples/` — tracked inputs that reproduce productions: `samsung_cutaway` (author, library, contracts),
  `kits/` (the exemplar generators: `steel_joint` → `building_elements` → `environment_kits`).
- `library/` — verified exemplars, CC0 assets (manifests use paths relative to their folder), fonts, learned styles
  (numbers and source hashes only — reference videos are never stored).
- `projects/` — not tracked: working projects, renders, reference analysis. Start one with `project from-example`
  or `project init`.
- `tests/fixtures/` — minimal tracked projects the tests and `tests/rig_regression.py` use.

## Checks

```sh
.venv/bin/python -m unittest discover tests      # host tests
.venv/bin/python tests/run_smokes.py             # Blender smokes (some render small test images)
.venv/bin/python tests/rig_regression.py         # camera rig byte-identical against the recorded jet chase
.venv/bin/python tests/freeze_check.py record    # once per machine, then `check` before/after engine changes
```

Known gaps: `Dockerfile.blender` still installs the distro Blender (4.x); use the official 5.2.2 build instead.
