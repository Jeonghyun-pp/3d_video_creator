# 3D-first frame pictures (picture3d) and the review of generated clips

A shot's frame pictures are painted over its own 3D, so the picture and the build agree by construction
(2026-10-16 pilot: pictures drawn from text agreed with the 3D on 65 % of what was asked; painted over the clay with
its depth and lines they kept the build's parts and states). Nothing here picks or approves: a painted picture is
the agent's proposal; the storyboard is still approved in the user's words.

## Order per shot (`studio picture3d run --project P --shots s01 s02 ...`, resumable)
1. **candidates** (`frames candidates`): each pictured moment's intent poses; the user's newest pick of the moment
   (`frames pick`, which lands on the moment's current picture, found by its time) else the engine's first. A pick whose
   candidate the sheet no longer holds is refused (pick again), never posed from another candidate.
2. **passes** at that pose and frame (Workbench / EEVEE stills, `blender_ops/picture_passes.py`): `clay.png`,
   `depth.png`, `lines.png`, `region.png`, `id.json` (the id row: what shows where), `state.json` (each claimed
   thing's state from geometry: extended / retracted (a part against its owner), contact / clear (anything's lowest
   point against its support), on / off / unknown (in frame / hidden from rendering / out of frame) - `part_state_core`).
3. **claims**: what the shot's lines state about each thing they speak of (state, count) - one text-only read, cached;
   `picture3d mark-claim --thing --state --count --note` corrects it.
4. **check** (`facts.json`): the 3D against the claims, from the 3D's own data - `PICTURE3D_CLAIM_UNSEEN` (shows at no
   moment), `PICTURE3D_STATE_DIFFERS` (the claimed state's own kind was measured and is the opposite at every moment it
   was: a claimed contact is judged by contact alone, a kind nothing measures - open/closed - never; a state the shot
   changes is met by one moment; only the moments the shot pictures now count),
   `PICTURE3D_COUNT_DIFFERS` (another number than the spec builds). Broken: fix the 3D (author values, drives) before
   the pictures stand for it.
5. **paint** (the image tool, one at a time): clay + depth + lines + a style picture (the shot's picked or beat
   concept, else a neighbour's painted picture, else the moment's old picture - look only).
6. **register** (`blender_ops/picture_register.py`): one scale + shift lays the painting's edges on the clay's lines
   near the named things' outlines (not their painted insides); a painting that would leave more than `REGISTER_LIMIT`
   (10 %) of the frame unpainted on a side, or fits only at the search's edge, is painted again once, then the clay
   stands in (`PICTURE_PAINT_SHIFTED`; the clay's record carries no prompt or fit). A painting drawn smaller (zoomed
   out) lays back by cropping; one answered in another aspect (2:3 of a 9:16 clay) is cut to the clay's aspect first
   (`crop` and `painted_size` in register.json). The painting is the one the call's `--json` events name (else the
   newest made since it began, under the lock; `paint.json found_by`).
7. **read** (advisory): the painting read for the claimed things the 3D shows - `PICTURE_PAINT_DROPPED / STATE /
   COUNT`; one repaint with what it got wrong, within `PAINT_MAX` (2).
8. **record**: the registered painting becomes the moment's frame picture (`concept add --derived-from` the clay,
   `source: paint_over_3d`, `proposal: by agent`); its reading comes from the passes' id row and the pose
   (`frame_targets.derive_from_passes`: no labeller); the pose is pinned (`chosen`: the user's newest pick along the
   pictures it supersedes, with the user's words, else `by: agent`);
   the replaced picture's cheats carry over, its departures do not. Then `frames apply`.
9. **sheet** (`concepts/picture3d_sheet.html`): clay | painting | the picture it replaced, with the facts.

Steps one by one: `picture3d passes | claims | check | paint | register | read | record --shot S [--at T --attempt N]`;
`picture3d show --shot S` prints the ledger. A finished moment is not painted again while its pose and the authored
3D it was rendered in are the same (run.json `pose`). Unattended: `scripts/picture3d_overnight.py --project P [--shots ...]`
(the project's shots whose frames check disagrees with the 3D by default; the paid call code is never loaded). It runs
`frames apply` on each finished shot: the shots' move.frames are pinned to the painted poses (the agent's, `by: agent`,
unless the user picked), so the next build frames them there.

## Generate-only shots
`picture3d generate-only --shot S --note "why"`: a shot drawn from text only (no 3D) when its lines claim nothing about
any thing (`PICTURE_GENERATED_CLAIMS`, always an error) and the reel stays within `policy.generate_only_max`
(default 2, 0 allows none; `PICTURE_GENERATED_LIMIT`, budget; marked by `picture3d/text_only.json`). Its brief attaches the next shot's start picture so its look matches;
the sheet flags it (`PICTURE_GENERATED`).

## Review of generated clips (user rule 2026-10-16)
`video-review sheet --shot S --clip take.mp4 [--read]`: frames at the start, at each line's moment (its share of the
narration's characters) and at the end, the claims and the 3D facts beside them. `video-review pass --shot S --clip
take.mp4 --user-words "..."` binds the verdict to the clip's and the narration's hashes (an agent note only when the
user delegated `video_review`). A ladder adopted with `video_review` (new ladders) keeps an unreviewed take out of the
final cut (`edit build --profile candidate`: `VIDEO_REVIEW_MISSING` for a generative shot; a hybrid shot uses its Blender
pass, with a warning); a preview cut (`rough`) shows the take with a warning that it is unreviewed.
