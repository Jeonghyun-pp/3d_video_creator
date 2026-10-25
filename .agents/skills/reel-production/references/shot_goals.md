# Shot goals — reach them, find why not, act

Read when: directing a shot's motion, reading a takes sheet, or a pick is refused. The goals are rows of
`library/shot_goals.json` (each: what it means, how it is measured, the floor, the usual causes and what to try). You
choose how a shot reaches them; the engine measures and holds the floors. Why (the user, 2026-10-23): "너무 경로를
정해주기보다는 claude 가 목표를 달성하되, 그 과정에서 원인을 찾고 행동하도록 가이드를 주는게 나으려나".

## The loop (per shot)

1. **Direct** in the shot list: `direction.move` - a recipe with `settings` (size, angle, lens), or `moments` (the
   framings the camera passes through), plus `others` when a different framing is worth comparing.
2. **Make takes** (`storyboard takes`) and read its warnings before judging: `TAKE_UNBUILT` (which take, which code),
   `TAKE_FRAMING_DEFAULT` (a take framed by nobody), `take X: CAMERA_TOO_FAST` (a rush, with when and how much).
   A take whose camera lurches (`CAMERA_JERK`) is never picked: direct it again.
3. **Judge** (`storyboard judge`), then read each take's verdict against the goals.
4. For every goal a take misses: **find why** - read the measure, then the goal's `causes`, then decide whether it is
   the engine or your direction (table below).
5. **Act**: direct again (step 1) or pick (`storyboard pick --agent-note`). A floor that refuses the pick names what to do.
6. **Bound**: a shot directed again more than `DIRECT_RETRIES` (2) times since its last pick is held
   (`TAKES_RETRIES_SPENT`) - one more round opens for a direction not tried yet; after that, write in REPORT.md what
   keeps failing and why, and `reel answer --awaits` if only the user can settle it.
7. **Before a coloured render** the engine reads all of this again on the version itself (`studio/quality_gate.py`,
   `reel gate`): measured, picked, no hard floor, watched in the reel with its neighbours, each finding fixed or answered,
   every reason read with the picture (`reel excuses`). Skipping a step is not a way through - the render waits.

## Engine or direction?

| Sign | Read | Usually |
|---|---|---|
| `FRAME_UNSOLVED` on the own take | `camera_move_report.json` `failure.steps`: `have` swinging round `want`, swings widening | engine (the solve) - report it with the steps; do not route around it by another recipe |
| `FRAME_UNSOLVED`, `have` stuck far from `want` | the steps' `depth` and `limited` | direction: the size cannot be had from that view (a long thing seen end-on, a lens too wide) - change view or size |
| `FRAME_EMPTY` while a part moves | the frame report: the part's px falling to 0 and back | engine when the camera follows a still root (fixed 2026-10-23: it follows its focus parts); else the part leaves the framing - frame its path |
| `KEY_PART_INVISIBLE` / `DETAIL_NOT_SHOWN` (x px of 20) | the frame report `why_hidden` (on screen, occluders) | direction: closer, another view, an opening that faces the camera |
| `CAMERA_TOO_FAST` | the line's axis, value, from-when | direction: the move covers too much in its time (or it is fast and followable: say why) |
| `CAMERA_JERK` (`SHOT_FLOOR`) | camera_dynamics.json `follow.jerk`, `jerk_at_s` | direction: a burst from rest or a dead stop - ease in, land with a slow-down |
| no take reads | the verdict notes | direction (framing, comparison, timing) unless every take failed the same engine way |
| `RECIPE_UNMADE` / `MOVE_UNMADE` | - | direction: write the move as moments |

An engine fault is recorded (REPORT.md, with the evidence) and, in a run that may fix the engine, fixed as a principle
with a test - never hidden by picking another take.

## What to do, in order

**A lurch (`CAMERA_JERK`) or a rush (`CAMERA_TOO_FAST`, the watcher's harsh)**
1. A lurch is a change of pace, not a speed: ease the move in and out (timing `points` or `ease_in_out`, never a burst
   from rest) and land it with a slow-down (`settle_at`), not a dead stop.
2. Start nearer the end framing (a first moment closer, or `height_share` nearer the start's).
3. Split the move into moments with a rest between them (arrive, hold, go on).
4. Give the move more of the shot (move `key_at`, or a longer shot when its line allows).
- A fast move an eye can follow (a ride-along, a fly-in to a visible place) may stay fast: pick with
  `--because CAMERA_TOO_FAST="<what the eye holds on while it moves>"` - read with the picture before a render.
- `CAMERA_JERK` passes on no reason (`SHOT_FLOOR`; only the user's words, `reel allow`).
- Never: turn the shot into `watch` only to pass the floor - a held shot needs its own why.

**A change that does not read (`change_seen` false, CHANGE_* lines)**
1. Rest the camera over the change: two moments with the same framing around the change's `at` window.
2. Keep something still in frame to compare against (a surface, the ground, another object) for a move or a sinking.
3. A closer moment on the changing part while it changes.
- Never: make the change bigger without `exaggerated` and its reason.

**Nothing reads (`TAKE_NONE_READS`)**
1. Frame the whole thing the line is about before its detail (a first moment wide enough).
2. A comparison of a big and a small thing: two moments, one each, or the small one near the lens.
3. Another view (side, height) that shows what the line says.
- Never: pick the least-bad take (refused for an agent; no reason passes it).

**The own take was not built (`TAKE_OWN_UNBUILT`)**
1. Read its failure (`camera_move_report.json`, frame report) and decide engine or direction (table above).
2. Direction: direct the move again and make the takes again.
3. Engine: record it; pick another take only with `--because TAKE_OWN_UNBUILT="<why it says the same>"`.
- Never: let a compared take stand in for the meant one without saying so.

**A small change nobody can tell (`CHANGE_TOO_SMALL`, the watcher's `names_change`, `CHANGE_UNMARKED`)**
1. A moment close on the changing part while it changes: start where the line places it, go in, `hold` over the change.
2. Mark it in the shot list: `change.marks` - `label` (its name as the line says it), `flow` (along its way:
   `along: [ids or points]`), `outline` (its edge). The clay and the judges see them as the final does; a marked change
   needs half the area.
3. Draw it larger with `exaggerated` and its reason.
4. Give it a shot of its own (split the line over two cuts).
- Never: leave an inside part unmarked without `unmarked: why`.

Bad (r3 s05): the whole boat sinking while the bow tank's water rose at 0.078 of the frame - the watcher saw the sinking,
not the filling. Good: moments - the boat whole, then close on the opened bow tank with `label: 앞 메인 탱크` and a
`flow` from the vent down into it while it fills, held; the sinking the next cut's.

**A still shot where nothing shows (`SHOT_NOTHING_SHOWS`)**
1. Reveal while the line speaks: pull back, go round, bring the next thing into frame.
2. A still picture of nothing new does not render whatever the reason (`SHOT_FLOOR`, a hard floor since 2026-10-24: in r3
   every such take was dull). A rest the reel needs still shows something: the subject turns, the next thing enters.
- Never: a small push only to pass - the measure reads the picture, not the camera.

**A move or turn the camera masks (`CHANGE_MASKED`)**
1. Hold the camera over the change: a moment with `hold: true` after the one that frames it (the camera stays; the
   thing moves through the frame).
2. Keep something still in frame: the water's surface overhead, another object, or a thing placed near the lens.
3. Move with it only when something still shows the motion (a ride-along past a reference).
- Never: an exaggerated move to beat a moving camera.

**An opening the camera looks past (`OPENING_FACES_AWAY`)**
1. Sweep less round it (a smaller `span`, moments that stay on the cut's side).
2. Open it as a `section` (the whole half) when the camera must go round.
3. Split the shot into two moments, each seeing its own cut.

## Moments

`direction.move.moments: [{at, size, view: {elevation_deg, azimuth_deg}, lens_mm?, parts?, cut?, why}]`, at least two,
in time order; `path` (auto | straight | arc) and `follow` (0-1) on the move. The engine joins them (frame_to_frame:
each framing solved on the scene, the path round the subject when the views differ). The same framing twice is a
rest for a still subject; for a moving one write `{at, hold: true, why}` - the camera keeps the pose of the moment
before and the thing crosses the frame (a falling stone). The recipe stays the feel's name (a recipe without an engine
make is written so).

## Where things show, and from where

- **The framing is yours.** A move says its size and side (`settings.height_share`, `settings.azimuth_deg`) or is
  written as moments (`MOVE_FRAMING_UNSAID`); a compared take your framing does not carry to is refused - write it under
  `others` with its own. The sheet gives the subject's broad side and the reference's size as references, never as
  defaults. The clay page says when most of the reel sees one side (`VIEW_MONOTONE`).

- **Views in the subject's terms.** The shot list sheet says each azimuth as the side it sees ("az 30 = from the back
  quarter, on its right"); a spec may name its `front` (`+length`/`-length`). A recipe's default azimuth is the subject's
  broad side. Read the words before you pick numbers: r3 framed an 83 m hull from its stern quarter in every cut.
- **Placed by where it shows.** `place: [{id, near: {at, screen: [x, y], size}, why}]`: at that moment the subject's
  middle stands at that place of the frame, that share of its height tall - the engine finds the spot once the camera is
  solved (`PLACE_NEAR_MISSED` when it cannot). A small thing beside a big one: the small one near the lens.
- **Under the water.** A world filmed from inside its water says `shell: {ground: sea, camera: under}`: the surface is
  overhead, the water darkens below, no horizon; a shot on the surface says `scene.shell.camera: over`. A camera that
  leaves the water is refused (`CAMERA_OUT_OF_SPACE`).

Bad (r3 s05, picked): a compared `go_inside` take at the recipe's default size 0.815 - the whole boat to one tank in
half a second (scale 4.3 over 0.5 s), the filling hidden by the push.
Good: `moments` - at 0 the whole boat (size 0.45, below and off the bow); at 0.3 the bow tank close (size 0.6,
`parts: [submarine_hull/mbt_fore]`); at 0.65 the same framing (the camera rests while `fill_mbt` rises over 0.3-0.65);
at 1.0 wider again as the boat sinks past the still surface (size 0.4) - why: arrive, let the water rise, then show
what it does to the boat.

## Numbers

| What | Value | Where |
|---|---|---|
| comfort: scale / travel / turn over the fastest 0.5 s | 0.6 / 0.4 / 3 deg (a reason's, read with the picture) | `library/shot_goals.json` comfort.limits (first values, r3) |
| followable: jerk (largest change of 0.25 s screen motion between neighbouring 0.25 s windows, per s) | under 1.8 (fly-throughs max 1.63) | `library/shot_goals.json` followable.limits, `studio/camera_follow.py` |
| a reason that passes a floor | 8 characters or more, then read with the picture (taken or refused) | `storyboard.REASON_MIN`, `studio/excuses.py` |
| hard floors (no reason passes) | a camera that changes pace past the followable limit; a still shot whose picture never changes; a key change under the size floor | `library/shot_goals.json` `hard` (each row names its code and what it reads) |
| moments in a motion | 2 or more | `recipe_moves.MOMENTS_MIN` |
| directing again before stopping | 2 | `storyboard.DIRECT_RETRIES` |
| a camera that masks a change: its own motion against the change | half or more, with under 5 % still in frame | `library/shot_goals.json` reference |
| an opening must read in | half of its window's probed frames | `show_core.FACING_MIN` |
| a change's size on screen | 0.15 of the frame tall, 0.012 of its area (half when marked) | `library/shot_goals.json` change_seen.size_floor |
| a still shot must show | its subject's size change ≥ 0.15 or its middle moved ≥ 0.05 | `library/shot_goals.json` something_shows |
| one side of the subject | in at most half the reel's shots | `views.VIEW_SHARE_MAX` |
