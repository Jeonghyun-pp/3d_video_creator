# Blender freedom — what you may do, and the lines that hold without you

The principle: **free to try, strict to judge.** You may do anything in Blender that makes the shot better; the build
judges the result the same way whatever made it. The lines are structural (processes, allow-lists, hashes, the
picture itself), not requests - so you never need to guess what is allowed: try it, read the error, follow its hint.

## Layers

| Layer | What you may do | The line (enforced) |
|---|---|---|
| L0 data (`shot.scene`, specs, edits) | everything the schema and the declared-reads tables name | a value nothing reads is refused (`INPUT_INVALID` lists what is read) |
| L1 workbench | explore, measure, preview, save variants, compare | commit replays the session; `exec` needs the user's words and blocks commit |
| L2 author script | any bpy - geometry, materials, modifiers, nodes, drivers with built-in expressions, linking/appending .blend assets | own process; lint before Blender (`AUTHOR_SCRIPT_REFUSED`), audit hook while it runs (`AUTHOR_SANDBOX_VIOLATION`), audit after (`author_audit.json`) |
| L3 contrib | new mesh builders, 2D profiles, joint laws, spec generators | pure function + manifest; contract tests; promoted only after a passing build used it; versions pinned |
| L4 expressive | grade, compositor ops, engine settings, bundled add-ons, .blend links | declared in `shot.render` / `shot.scene.links`; renderer-owned settings refused with a pointer |
| quality | - | the frame probe judges every build's frames through the real camera |

## L2 — author scripts

Allowed imports: `math random json bisect copy hashlib itertools functools collections statistics dataclasses typing
enum re string colorsys heapq operator fractions decimal textwrap struct time pathlib sys` (sys: `path argv
version_info platform`), `bpy bmesh mathutils bpy_extras numpy`, engine modules in `studio/blender_ops` (not the entry
scripts), and modules beside the script or at the project top level (they are linted too).

Refused, with what to do instead:
| Refused | Why | Instead |
|---|---|---|
| `os`, `subprocess`, `socket`, `urllib`, `shutil`, `importlib`, `ctypes` | files, processes and network belong to the studio tools | write data files only into `STUDIO_JOB['output_dir']`; ask the tools for anything else |
| `exec`, `eval`, `compile`, `__import__`, `getattr(x, computed)`, dunders | code that cannot be reviewed | write the code; use RNA paths (`obj.path_resolve`, `keyframe_insert(data_path=...)`) |
| `bpy.app.handlers / timers / driver_namespace` | they run later, inside steps you do not own | bake the animation as keys in the script body |
| `wm.save*`, `export_*`, `image.save*`, `libraries.write`, preferences / extensions ops | the build saves and packs; downloaded code never runs | let the build save; declare `shot.render.addons` (bundled only) or call `STUDIO_ENABLE_ADDON("name")` |
| 3D text that renders | lit, bloomed, leaks into control passes (rule #2) | `shot.titles`, labels, `shot.graphics` |

Recorded, not refused: render / colour / engine settings you change (`author_audit.json settings_changed`; settings
the renderer overrides at render time are marked `renderer_overrides` - they do nothing; declare `shot.render.*`
instead), cameras you change on a rig/move shot (the generated camera replaces yours), studio ids you remove, .blend
libraries you link (made local and packed; path and hash in `dependencies.json`).

Bad: `import os; os.makedirs(...)`, `bpy.app.handlers.frame_change_post.append(spin)`.
Good: keyframe the spin over the shot in the script body; write `mechanism_note.json` into `STUDIO_JOB['output_dir']`.

## L3 — contrib entries (new vocabulary)

When a shape or a law is reusable (a cycloidal disc, a cam profile, a Geneva drive law) and the data cannot say it, add an
entry. A one-off appearance form belongs in spec ops or the author script (any bpy):

```
projects/<p>/contrib/<kind>/<name>/impl.py        one pure function (no bpy, no files)
projects/<p>/contrib/<kind>/<name>/manifest.json  {kind, name, entry, params: {name: default}, lengths: [...], words: [...], description}
```
kinds: `mesh` (params -> verts, faces; used as a spec builder `"builder": "contrib:<name>@draft"`), `profile`
(params -> [[x, y]]; `"profile": {"contrib": "contrib:<name>@draft", "args": {...}}`), `coupling` (law(x, **params);
`{"kind": "contrib:<name>@draft", "driver", "driven", "args"}`), `spec` (params -> subject spec).

Contracts (`studio contrib check --project P --name N`): function parameters = manifest params, deterministic, finite;
mesh closed and consistently oriented; profile a simple loop; doubling the `lengths` params doubles the size; `words`
present (they describe the part to generation). A build that uses `@draft` and passes fidelity and the mechanism checks
promotes it to `library/contrib/<kind>/<name>/vNNN` (`CONTRIB_PROMOTED` warning) - then pin `@vNNN`. A deprecated
version (`contrib deprecate`) is refused for new use.

## L4 — expressive settings

```
shot.render.grade            {view_transform, look, exposure | exposure_offset_ev, gamma, white_balance_k, curve: [[x, y], ...]}
shot.render.compositor.ops   [{op: glare|lens_distortion|color_balance|hue_saturation|bright_contrast|sharpen|soften, ...}]
shot.render.engine_settings  {cycles: {max_bounces, blur_glossy, sample_clamp_indirect, ...}, eevee: {...}, render: {use_motion_blur, ...}}
shot.render.addons           [bundled add-on module names]
shot.scene.links             [{id, file, data_type: objects|collections, name, at}]
```
Applied after the look (`expressive_report.json`): `exposure_offset_ev` adds to the metered exposure (prefer it to an
absolute `exposure`); ops are spliced before the look's output; a position-changing op (`lens_distortion`) is refused
on shots with anchored labels. Samples, resolution, engine, denoising and device belong to the render profile - writing
them here is refused with the field that works. Every value is reachable by path (`set_shot_value`,
`storyboard revise --ops`, `revise --scope style` needs no script).

## The frame probe (every build)

8–12 frames (12 past 240 frames) plus key-part windows and storyboard focus frames, rendered as an id pass through
the scene camera at 256 px; `frame_report.json` + `frame_probe/*_id.png`.

| Code | Severity | Threshold (initial; recorded in `frame_probe_core.THRESHOLDS`) | What to change |
|---|---|---|---|
| `FRAME_EMPTY` | ❌ | < 1 % of the frame renders | aim / keys / target |
| `FRAME_NEAR_CLIP_CUT` | ❌ | near plane removes ≥ 0.5 % of subject/key pixels | camera back, or lower `clip_start` |
| `KEY_PART_INVISIBLE` | ❌ explain · ⚠️ mood | < 20 px (or `min_px`) in every frame of its window | angle, occluder, window |
| `FRAME_EDGE_CUT` | ⚠️/❌ by policy | key part touches the border in > 1/3 of its frames | framing |
| `FRAME_SUBJECT_SMALL` | ⚠️/❌ by policy | median subject share < 2 % | closer, longer lens |
| `KEY_PART_SMALL` | ⚠️/❌ by policy | key part never above 0.4 % of the frame | closer, longer lens |
| `CONCEALED_PART_VISIBLE` | ❌ never softened | a `shot.concealed_parts` part shows more than `max_px` (default 0) in its window | close the shell (gap, missing cover), or end the window where the reveal starts |

Concealed parts: `shot.concealed_parts: [{id, from_frame, to_frame, max_px}]` - what an intact view must not show (an
exterior turn hides the valve train, springs and chain; a reveal ends the window where the cut opens). Why: on 2026-10-07
an engine exterior showed its valve train through a cover gap while every other check passed. An object cannot be both
a key part and a concealed part.
Key parts: `shot.key_parts` (all codes); a rig's look target and what an approved storyboard kept in frame count for
presence only. Subject: what the camera and the shot name (rig subject, move target, `shot.subjects`, scene instances).

## Pitfalls seen in production
- A capped open `revolve` profile fills the centre (a ring comes out solid): use `closed_profile: true` (lint warns).
- An aim empty hides the target guard: moves now guard their real target (`rig.guard_target`).
- Geometry nearer than `clip_start` is not drawn: guards and the probe now ignore it as an occluder and report the cut.
- A setting written where the renderer overrides it is a silent no-op: check `author_audit.json` `renderer_overrides`.

## Running Astra (Blender access)
Blender cannot start inside the codex sandbox: the sandbox denies the GPU (IOKit `AGXDeviceUserClient`) and Blender dies at
Metal start-up (reproduced 2026-10-06, exit 139). A studio command that needs Blender refuses there with
`BLENDER_NEEDS_BROKER`; run it through the MCP tool `studio_run {args: ["shot", "build", ...]}` (same arguments as
`python -m studio`). The tool runs it outside the agent sandbox, inside the studio's own (`studio/broker.py`): GPU yes,
network no, no API keys, writes only in the repository; `freeze` and `contrib` are never brokered. The studio MCP server's
tools are approved by configuration (`default_tools_approval_mode = "approve"`), so no per-call prompt is needed.
Run records: `scripts/reel_agent.py ... --log-dir D` writes `events.jsonl`, `stderr.log` and `run.json` (start, end, exit
code, thread id). `scripts/run_status.py PROJECT --log-dir D [--watch]` reports passing / failed versions, renders and
decision notes per shot; `scripts/astra_run_metrics.py D` finds the run's main and subagent rollouts by thread id and sums
their tokens (2026-10-07 engine re-measure, s01 only: 78.7 M tokens over 4 agents, 97 % cached input).

## Reasoning effort (tokens are not free)
Your own effort is set per run (`scripts/reel_agent.py --effort`, default medium). Give each subagent its own when you
spawn it - pass `model` and `reasoning_effort` explicitly: the agent type files' settings are not applied (measured
2026-10-06: every subagent inherited the parent's; passed explicitly, `gpt-6.1-sol / high` was applied).

| Task | Effort |
|---|---|
| new shape or motion law (contrib), scene writing, diagnosing a failed gate, visual review | high |
| subject spec, workbench iteration, storyboard edits | medium |
| file / source inventories, reading and summarising docs | low |
Measure a run's tokens with `scripts/astra_run_metrics.py` (`tokens` per rollout and in total).
