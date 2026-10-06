# Storyboard — agree each shot as pictures before looks and paid generation

`studio/storyboard.py`, `studio/blender_ops/storyboard_render.py`. Workbench frames only (measured 1.5 s per sheet on a
data-built section shot): cheap enough for every round, never a look render, allowed in unattended runs.

1. `storyboard propose --shot S --frames 0,0.5,1 --focus '{"0.5": ["st.wall"]}' --captions '{"0": "street"}'` — builds the
   shot from its scene data if the shot changed, renders the frames and a top view with the camera path and frame
   numbers (`decisions/sheets/storyboard_S/rNN/sheet.png` + `sheet.md`). Show the image.
2. The user's words become typed edits (`storyboard revise --user-words "…" --ops ops.json`), never free code:

| They say | Op | Becomes |
|---|---|---|
| closer / wider | `{"op": "camera.closer", "factor": 0.7}` | the move's distance knob × factor |
| higher / lower | `{"op": "camera.height", "delta_m": 3}` | the move's height knob |
| from the side | `{"op": "camera.angle", "delta_deg": 90}` | the move's angle knob (moves without one say which knobs they have) |
| tighter lens | `{"op": "camera.lens", "mm": 50}` | `move.lens_mm` |
| horizon lower | `{"op": "camera.horizon", "v": 0.45}` | `move.framing.horizon_v` |
| look at X | `{"op": "camera.look_at", "id": "pump"}` | `move.look_target` |
| move / bigger / add / remove X | `object.move` (`delta_m`), `object.scale` (primitives), `object.add` (`entry`), `object.remove` | `shot.scene` |
| change the title | `{"op": "title.set", "title_id": "t", "text": "…"}` | `shot.titles` |

   The shot rebuilds from data and the new sheet shows before / after rows plus "what you asked → what changed".
   Fitted moves (`turntable`, `slide`, `macro_push`) answer "closer" through `distance_scale` (default 1.0), "higher"
   through `elevation_deg` (`delta_deg`) - knobs work without being set first.
3. `storyboard approve --shot S --sheet rNN --user-words "…"` — only the latest sheet, only if the shot is unchanged since.
   The measured frames (camera position, view, lens, where the focus objects sit) become the contract.

On the decision ladder, look/review/final renders and hybrid generation of a shot need its approved storyboard; a later
version is measured (no render) and refused with `STORYBOARD_DRIFT` when the camera looks more than 15° away, moves more
than 25 % of its distance to the focus (≥ 0.5 m), changes lens by more than 30 %, or a focus object moves 0.15 of the
frame or changes size outside 0.5–2× (provisional tolerances: they catch a different shot; the user judges looks).
Recovery: a new sheet of the new version and their approval — never loosen the tolerances.

## Takes — try freely, judge strictly

When a shot's staging is still open (the first sheet of a shot, or the user says "다른 방식으로" / "not like this"), show
2–4 **different** takes before tuning one: `storyboard variants --shot S --variants takes.json --frames 0,0.5,1`.
Each take is `{id, label, why, change}`; `change` may replace any of `camera`, `scene`, `actions`, `titles`, `graphics`,
so a take can be a different move, a different staging, a different object in focus. Every take is built and passes the
same gates; the sheet stacks them at the same frames (Workbench, ~2 s a take). Nothing is approvable until the user
picks: `storyboard pick --shot S --sheet vNN --variant B --user-words "…"` makes that take the shot (its version is
reused) and writes the normal sheet; tune it with `revise`, then `approve`.
Why: one first idea tuned by knobs converges on the safe, flat shot; different takes side by side let the user choose
the idea, and the strict part (their words, the contract) is unchanged.

Bad: takes A/B/C = turntable at 40°, 50°, 60° (one idea, three knob values — that is a `revise`).
Good: A turntable of the whole reducer, B macro push into one planet's mesh, C low slide past the ring — each `why`
names what the narration needs to show.
