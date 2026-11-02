# Setup — a fresh clone that builds the same scenes

## First check: no Blender, credentials or model calls

From the repository root, using Python 3.12 or newer:

```sh
git clone https://github.com/Jeonghyun-pp/3d_video_creator.git
cd 3d_video_creator
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest tests.test_shot_edit tests.test_timings tests.test_workbench tests.test_registry_sync tests.test_content_keys tests.test_read_limits -v
.venv/bin/python -m studio --help
.venv/bin/python -m studio2 --help
```

Expected: 38 tests, `OK`, then both CLI help pages. These tests use temporary fixtures and mocked Blender/model calls; they exercise edit contracts, timing records, replay and MCP boundaries, not rendering. CI runs this same subset on Ubuntu/Python 3.12. A separate CI step installs FFmpeg and runs `tests.test_studio2_foundation`, `tests.test_studio2_sheet` and `tests.test_studio2_video_composite` with local synthetic media; it does not call a paid API, Blender or RIFE. Dependency installation needs network access (or a populated package cache); the checks need no service credentials.

For the full production environment, install the tools below and run `scripts/bootstrap.sh`. It additionally downloads a pinned RIFE binary/model, builds the Blender API index and writes the local MCP config. RIFE is for generated-video frame interpolation and is not needed for the first check. The installer supports macOS and Linux; scratch speech and GPU behavior differ by platform.

## What you need

| Item | Version | Notes |
|---|---|---|
| Python | 3.12+ (measured on 3.14) | `requirements.txt`: jsonschema, Pillow. CAD factory (optional): `scripts/bootstrap.sh --cad` |
| Blender | **5.2.2** (official build, it bundles numpy) | Found via `STUDIO_BLENDER`, then `PATH`, then `/Applications/Blender.app` (macOS). Other versions build, but BUILD_REPORT numbers were measured on 5.2.2 |
| ffmpeg / ffprobe | 6+ | encode, QA, styles |
| Font | bundled | `library/fonts/pretendard` (SIL OFL 1.1): titles, captions and 3D text use the same glyphs on every OS |
| GPU | Apple Metal | `render_profile.py` asks for METAL only; on Linux/NVIDIA renders fall back to CPU (slow, same images). Not changed: the file is part of the render fingerprint |
| Reference speech (optional) | mlx-whisper (Apple Silicon) | `pattern pacing learn` transcribes the reference reels on this machine: `pip install mlx-whisper` in the studio environment and the model `mlx-community/whisper-small-mlx` on disk (`~/.cache/huggingface`); it runs offline and nothing leaves the machine |
| Paid generation (optional) | fal | `FAL_KEY` or `~/.config/fal/api_key`; every paid call waits for a reviewed, user-approved request |

Scratch narration uses macOS `say` (voice Yuna); elsewhere supply narration or use a paid voice.

## Blender integration check and production examples

With Blender 5.2.2 installed, run the existing isolated fixture check:

```sh
.venv/bin/python tests/studio/workbench_shot_smoke.py
```

It creates a temporary reducer scene, edits camera/action values, checks rollback and restoration, then compares the committed camera with the session. It renders small previews locally; no production project, user approval or paid service is used. Its synthetic decision fixtures are for tests only. Do not apply `tests.decisions_fixture.settle` to production projects.

To inspect the tracked production inputs without rendering:

```sh
.venv/bin/python -m studio project from-example --example samsung_cutaway --project projects/samsung_cutaway
.venv/bin/python -m studio project status --project projects/samsung_cutaway
```

The copy result includes `missing_inputs`; inspect it even when the command succeeds. The tracked example currently lacks the hybrid prompt `prompts/s01.txt` and does not include settled decision records. It is an input example, not a ready-to-build approved project. Follow the [production decision procedure](USING_THE_AGENT.md#production-decisions) before building. A premature build returns `DECISION_MISSING`; inspect the error's recovery instructions and prepare the missing decisions, rather than bypassing the gate.

Historical check (2026-10-06): after preparation in that environment, s01 matched the working v0026 (1,132 objects, camera samples, street digests and metered EV). This is not a claim that today's fresh copy builds without preparation.

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
.venv/bin/python -m unittest discover tests      # full suite; can invoke Blender when installed
.venv/bin/python tests/run_smokes.py             # Blender smokes (some render small test images)
.venv/bin/python tests/rig_regression.py         # camera rig byte-identical against the recorded jet chase
.venv/bin/python tests/freeze_check.py record    # once per machine, then `check` before/after engine changes
```

The sealed engine (studio/seal.py): everything that runs outside the agent's sandbox - the MCP server Astra uses, its
builds, brokered commands, dispatched workers - runs the copy of the engine the user approved, in
`~/.config/studio/engine/<checkout>/`, made by `studio freeze record --user-words "..."` (the user's own words; the same
call records the frozen-code baseline). Start Astra from it only:
`<venv python> -I -B -X pycache_prefix=<engine>/.pyc <engine>/studio/_launch.py reel_agent ...` (`studio seal status`
names the engine; `scripts/reel_agent.py` run from the checkout refuses and prints this). `.codex/config.toml` in the
checkout is for development sessions only; Astra's runs never read it.

Known gaps: `Dockerfile.blender` still installs the distro Blender (4.x); use the official 5.2.2 build instead.
