# Audio, edit and technical QA

The CLI supports `audio build --project PATH --shot ID --mode scratch|final`,
`edit build --project PATH --profile rough|candidate`, and
`qa collect --project PATH --candidate ID`. All commands return JSON.

Audio caches per-shot immutable 48 kHz PCM WAV, provider response/alignment,
request settings, hashes, measured duration and subtitle cues. macOS Yuna is
scratch narration. Import a recording with `audio build --input-wav FILE` to
select it as final narration. Optional `narration.alignment_path` accepts supplied
character timing for imported audio. Sentence timing remains explicitly coarse
when no valid alignment exists. A single whole-utterance display-text override can change a subtitle while retaining the recording. The original recording and unnormalized voice
remain preserved. Speech overruns raise `TIMING_CONFLICT`; speech is never cut.

ElevenLabs timestamps require environment `ELEVENLABS_API_KEY` and
`ELEVENLABS_VOICE_ID`, plus explicit `audio build --mode final --allow-paid`.
The voice ID comes from the project when its provider is ElevenLabs; the environment voice ID overrides it. Voice settings and optional previous/next text participate in cache keys. An unchanged cached final voice remains available without another paid call; changed settings without authorization fall back explicitly to scratch. Selected imported recordings remain independent of TTS settings. The API adapter has mock response/cache/ambiguous-request tests. No paid provider
call was made during the build. A response is persisted before audio decoding.
An ambiguous POST outcome blocks automatic duplicate requests.

Edits pin shot JSON, clip and audio hashes, style/font hashes and copied projected
anchor data. Candidate edits require source clips at the actual configured output
resolution; rough edits may use smaller renders. Both keep 30fps and the complete
frame timeline. Korean caption boxes are measured with Pillow and limited to two
lines. At most two labels occupy distinct slots. `style.label_slots[slot]` accepts normalized `x`, `y`, and `width` to retune placement without changing 3D; measured text must remain inside the configured safe rectangle and clear the caption box. Labels follow projected points;
offscreen/behind/occluded points hide according to policy. A label target or timing
change reprojects the existing scene without image rendering. Text changes reuse
3D clips. Narration is padded per shot and normalized with two-pass loudnorm.
Scratch output is named `scratch_candidate.mp4` and marked `needs_voice`. Explicit `audio.provider=none` emits a silent scene with `speech_status=not_applicable` and `narration_present=false`; it creates no dummy narration.

QA checks encoded dimensions, decoded frame count, fps, H.264/yuv420p, AAC/48kHz,
duration, audio audibility/clipping, safe boxes and faststart. It measures encoded
loudness and generates quarter-second contact sheets through the existing QA
helper. Black/freeze detections are review prompts. `technical_pass` and
`auto_pass` do not grant visual or human approval. Candidate hashes remain exact;
the separate delivery command owns promotion to master.

Run media checks with `.venv/bin/python -m unittest tests.test_studio_media -v`.
The checks execute local FFmpeg and macOS Yuna, verify imported audio, motion-aware
labels, cached text revisions, cue mappings and voice status without paid calls.

## Generated and routing artifacts (2026-10-04)

- `route_plan.json` / `route_plan.md` (project root): per shot {shot_id, mode, rule_id, reason, features, confidence, status, est_cost_usd, est_minutes, estimate_source, locked}, `total_est_cost_usd`, `over_budget`, `conflicts`.
- `shots/<id>/generated/<key>/clip.json`: {status, route, key, request{endpoint, prompt_sha256, seed, take, inputs[{kind, sha256}], duration_seconds, output}, scene_version (hybrid), profile (final if native size), frame_count, fps 30, clip_path, clip_sha256, raw_sha256, source{width,height,fps,frames,duration}, retime, endpoint, request_id, estimated_usd, ai_generated: true, use_status: review_only}. `clip.mp4` is BT.709 tagged, exact frame count.
- `shots/<id>/control/<fp>/control.json`: {fingerprint, scene_version, near, far, files{depth|clay|canny: {path, sha256, frames}}}.
- `versions/<v>/look_report.json`, `versions/<v>/camera_rig_report.json`: build-time look and camera-rig reports (gate_failures fail the build).
- All encodes: H.264 yuv420p, BT.709 matrix + primaries/transfer/colorspace tags, TV range (`studio.common.h264_args`).

## Subject fidelity, workbench and repair artifacts (2026-10-04)
- `versions/<v>/subjects/<id>.spec.json`: the spec the version was built from; `dependencies.json.subject_specs` = {id: stable sha256}.
- `versions/<v>/fidelity_geometry.json`: raw measurements per subject {whole, parts{objects, features, x/y/z, bounds}, silhouettes{view: triangles}, assembly[{index, type, distance_m, penetration_m, ...}], screen_px}.
- `versions/<v>/fidelity_report.json`: {passed, subjects[{subject_id, passed, failures, checks[{kind: dimension|proportion|feature|silhouette|assembly, id, passed, measured, expected, note, detail_required, needs_review}], summary{failures_n, mean_silhouette_iou, needs_review}, spec_sha256, code_sha256}]}; `silhouette_<view>.png` overlay (red = reference only, green = model only, yellow = both).
- `versions/<v>/replay_report.json` (workbench commits): {ok, issues, actual}.
- `subjects/<id>/candidates/{trace,fit,dxf}_*.json`: proposed spec patches (JSON pointer → value) with evidence (pixels, IoU before/after, evaluations); `refs/` holds generated CAD silhouettes.
- `shots/<id>/repair.json`: {best{version, score}, stale_attempts, attempts[{version, base, score, diagnosis, outcome, reverted_to, error}], resets}.
- `workbench/<session>/`: session.json, ops.jsonl (every call: tool, args, kind, ok, ms), specs/, preview/, checkpoints/, patch.py, commit_expect.json.
- `library/exemplars/index.json` + `<id>/vNNN/{spec.json, fidelity_report.json, silhouette_*.png, refs/}`; `library/api/blender-<version>.json` (API index).

