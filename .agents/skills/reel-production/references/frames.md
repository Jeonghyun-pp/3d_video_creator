# Frame pictures — the first and last picture of a shot, and the camera that reproduces them

`studio/frame_targets.py`, `concept add --frame`, `camera.move` type `frame_to_frame`. Why (2026-10-11): the rocket
reel's 25 storyboards held the booster upright in the middle of the frame at eye level; six shots in a row were one
picture. The shot list said "just above the waves, the booster drops into frame from the top" and nothing could read
words. Its s12 "below the falling booster looking up" was built at -2° because the steep solve sank under the sea and
the angle was dropped to pass. The user: "샷별로 첫 장면과 마지막 장면을 그림으로 그리고 그사이를 움직임으로 메꾸고,
카메라 움직임까지 더해서 역동적으로".

## Order (after the shot list is approved, before the storyboard)
1. **Two pictures per shot** with your built-in image tool: the first moment (`--frame start`) and the last
   (`--frame end`); a third only for a moment that must land (`--frame key --at 0.6`: the touchdown). Prompt each from
   the shot's `direction` (camera, action, depth front/middle/back) and attach the world reference picture. A beat
   picture that already shows the moment is reused, not redrawn: `concept add --shot s01 --frame end --from-beat b1/p01`.
2. **Read them**: `frames measure --shot s01` (one Codex call for the new pictures, cached by hash) →
   `concepts/s01/frames_check.png`: the visible box (yellow), the whole subject (orange, may run off the frame), the
   horizon (cyan), and the reading (up/down and how steep, lens). **Look at it.** A misread picture is corrected, not
   accepted: `frames mark --shot s01 --concept c02 --box … --whole … --horizon 0.78 --view-deg -25`.
3. **Write the move**: `frames apply --shot s01 [--path straight|arc] [--anchor world|subject]`.
   - `anchor world` (default): the camera keeps its own path and the subject moves through the frame - a booster
     dropping in from the top, a ship passing.
   - `anchor subject`: the camera travels with the subject (a ride alongside it in flight).
   - `path arc`: the camera swings around the subject between the two framings (orbit-like); `straight`: a line.
   The move's timing (`burst_settle`, `ease_in_out`) carries the camera between two poses; style, guards and parts
   carry over from the old move.
4. **Build** (`shot build`): each pose is solved on the scene as it stands at that moment (the subject where it is
   then; ray-cast visibility) - `camera_move_report.json` `frames[]`: achieved size and place, lens tried, steps.
5. **Storyboard** (`storyboard propose`): the sheet adds every picture above the built frame of the same moment, with
   both boxes and what differs in words. Approval binds the pictures' hashes (`DECISION_STALE` when one changes).

## What a reading becomes
| read on the picture | becomes | rule |
|---|---|---|
| whole_box | `height_share`, `centre` (may be outside 0..1) | the whole subject places the camera: a booster cut by the top edge is solved with its centre above the frame |
| subject_box + cut_edges | the check | what the built moment must show: centre within 0.08, height ×0.75-1.33, the same edges cut |
| horizon_v | `view.elevation_deg` | a level horizon at v means the camera is pitched by atan((v - 0.5)·2·tan(half vertical view)); low horizon = looking up |
| view_height + view_deg (no horizon) | `view.elevation_deg` | up = negative (camera below), down = positive |
| lens wide / normal / tele | 24 / 35 / 70 mm | full-frame equivalent |
| side | (said, not used) | a cylinder looks alike from every side: the azimuth stays the shot's unless marked |

## Rules
- **The picture gives composition, never shape.** The booster's legs, fins and proportions come from the Blender model;
  a picture with five legs is still a good picture of where the booster sits. Why: an image model draws plausible
  rockets, not this one (subject_fidelity).
- **Keep the angle the picture asks for.** A steep view that puts the camera under the sea or the deck (declare them in
  `move.guards.clearance_ids` + `min_clearance_m`) is solved wider and closer (the lens steps 24 → 18 → 14 mm); only
  when that fails does the build stop with `FRAME_UNSOLVED: … below …`. Then, in order: raise the subject in the scene
  (it is falling - meet it higher), mark a less steep view with the user, last a different picture. Forbidden:
  flattening the angle to make the build pass (s12, 2026-10-11).
- **Movement is the dynamism, not off-centre gimmicks.** The study reels keep the subject near the middle across
  (center_x p25-p75 0.50-0.53, all roles); what changes is size through the shot (subject_growth), travel across the
  frame (subject_travel), the camera's speed and looking up (5-14 %). A first and a last picture that differ in size,
  height in frame, angle or what is cut give the move; two pictures of the same framing give a held shot - say so.
- **Section shots keep the flight.** A cut through the booster is drawn on the booster in flight - leaning body, flame
  below, sky behind - and the camera pushes into the cut. Why: "로켓 외부만 보여주면 그냥 영상 하나에 나레이션만 있는
  것처럼"; a section on a grey void reads as a diagram pasted in.
- **Neighbours differ.** The last picture of a shot and the first of the next differ in place, size or angle unless the
  hand-over says match cut (`FRAMING_REPEATED` on `storyboard board`).

Bad: s01 "rushing toward the ship, the booster drops in from the top" built as `macro_push` with `focus {height_share
0.78, centre [0.51, 0.43]}` at elevation -2° - the booster stands centred and only grows; s12 "looking up" at -2°.
Good: s01 start picture: sea at the bottom third, the ship small at centre, a flame entering at the top edge (whole box
above the frame); end picture: the booster half the frame, upper middle, horizon low (camera low looking up) →
`frames apply --anchor world`: the camera rushes low over the water while the booster drops into the frame.

## Checks
| code | when | kind |
|---|---|---|
| `FRAME_TARGET_MISS` | a pictured moment built as another composition (place, size, cut edges, up vs down) | taste |
| `FRAMING_REPEATED` | a cut joins two frames of one composition (centre ≤ 0.05, size ≤ ×1.15, angle ≤ 15°) | taste (board) |
| `FRAME_UNSOLVED` | a pose cannot be solved even at 14 mm (blocked, or under a guarded surface) | build error |

A miss is read, not silenced: in order, re-read the picture (`frames mark`), then the scene (where the subject is at
that moment), then the move's path or anchor; last, change the picture with the user.
