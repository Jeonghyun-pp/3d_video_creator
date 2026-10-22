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
3. **Judge** (`storyboard judge`), then read each take's verdict against the goals.
4. For every goal a take misses: **find why** - read the measure, then the goal's `causes`, then decide whether it is
   the engine or your direction (table below).
5. **Act**: direct again (step 1) or pick (`storyboard pick --agent-note`). A floor that refuses the pick names what to do.
6. **Bound**: a shot directed again more than `DIRECT_RETRIES` (2) times since its last pick is held
   (`TAKES_RETRIES_SPENT`): stop, write in REPORT.md what keeps failing and why you think so.

## Engine or direction?

| Sign | Read | Usually |
|---|---|---|
| `FRAME_UNSOLVED` on the own take | `camera_move_report.json` `failure.steps`: `have` swinging round `want`, swings widening | engine (the solve) - report it with the steps; do not route around it by another recipe |
| `FRAME_UNSOLVED`, `have` stuck far from `want` | the steps' `depth` and `limited` | direction: the size cannot be had from that view (a long thing seen end-on, a lens too wide) - change view or size |
| `FRAME_EMPTY` while a part moves | the frame report: the part's px falling to 0 and back | engine when the camera follows a still root (fixed 2026-10-23: it follows its focus parts); else the part leaves the framing - frame its path |
| `KEY_PART_INVISIBLE` / `DETAIL_NOT_SHOWN` (x px of 20) | the frame report `why_hidden` (on screen, occluders) | direction: closer, another view, an opening that faces the camera |
| `CAMERA_TOO_FAST` | the line's axis, value, from-when | direction: the move covers too much in its time |
| no take reads | the verdict notes | direction (framing, comparison, timing) unless every take failed the same engine way |
| `RECIPE_UNMADE` / `MOVE_UNMADE` | - | direction: write the move as moments |

An engine fault is recorded (REPORT.md, with the evidence) and, in a run that may fix the engine, fixed as a principle
with a test - never hidden by picking another take.

## What to do, in order

**A rush (`CAMERA_TOO_FAST`, or the watcher's harsh)**
1. Start nearer the end framing (a first moment closer, or `height_share` nearer the start's).
2. Split the move into moments with a rest between them (arrive, hold, go on).
3. Give the move more of the shot (move `key_at`, or a longer shot when its line allows).
- Last: pick with `--because CAMERA_TOO_FAST="<why the jolt is the point>"` (an impact, a jump scare).
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

## Moments

`direction.move.moments: [{at, size, view: {elevation_deg, azimuth_deg}, lens_mm?, parts?, cut?, why}]`, at least two,
in time order; `path` (auto | straight | arc) and `follow` (0-1) on the move. The engine joins them (frame_to_frame:
each framing solved on the scene, the path round the subject when the views differ). The same framing twice is a
rest. The recipe stays the feel's name (a recipe without an engine make is written so).

Bad (r3 s05, picked): a compared `go_inside` take at the recipe's default size 0.815 - the whole boat to one tank in
half a second (scale 4.3 over 0.5 s), the filling hidden by the push.
Good: `moments` - at 0 the whole boat (size 0.45, below and off the bow); at 0.3 the bow tank close (size 0.6,
`parts: [submarine_hull/mbt_fore]`); at 0.65 the same framing (the camera rests while `fill_mbt` rises over 0.3-0.65);
at 1.0 wider again as the boat sinks past the still surface (size 0.4) - why: arrive, let the water rise, then show
what it does to the boat.

## Numbers

| What | Value | Where |
|---|---|---|
| comfort: scale / travel / turn over the fastest 0.5 s | 0.6 / 0.4 / 3 deg | `library/shot_goals.json` comfort.limits (first values, r3) |
| a reason that passes a floor | 8 characters or more | `storyboard.REASON_MIN` |
| moments in a motion | 2 or more | `recipe_moves.MOMENTS_MIN` |
| directing again before stopping | 2 | `storyboard.DIRECT_RETRIES` |
