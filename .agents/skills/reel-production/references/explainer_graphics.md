# Explainer graphics — arrows, dimensions, outlines on their own layer

Code: `studio/blender_ops/graphics.py` (build), `render_graphics.py` + `studio/graphics.py` (`graphics render`),
`studio/edit.py` (composite); smoke `graphics_smoke.py`. Read when a shot points at, measures or outlines something.

## Spec (`shot.graphics[]`)
| kind | needs | draws |
|---|---|---|
| `arrow` | `anchors` (≥ 2; tip = last), `head_m` | line + head facing the camera |
| `dimension` | `anchors` (two ends), `tick_m`, `text` | line + end ticks; `text: "auto"` draws the measured length ("12.9 m") beside the midpoint |
| `outline` | `target` (anchor id of an object) | Line Art silhouette of that object |
| `highlight` | `anchors` | thick translucent stroke (`opacity`) |
| `draw_line` | `anchors` (a path) | a line |

Common: `graphic_id`, `start_frame`/`end_frame` or `time_binding` (camera or voice cues), `draw_on_s` (the strokes
grow over that time), `color_srgb` (default accent red), `opacity`, `radius_m` (stroke radius in metres).
Anchors are label anchors (`object` or `object/part`) or `[x, y, z]`; positions are taken at the graphic's first frame.

## Rules
- Graphics are Grease Pencil objects with role `graphic`, hidden from the beauty render, previs, control passes and
  every generative input. They render alone (`graphics render --project P --shot S`, transparent RGBA, scene meshes
  as holdouts so the building hides what is behind it) and the edit lays them over the shot, under labels and
  captions. So they get no bloom, DOF or motion blur and a model is never asked to draw them.
  Forbidden: a mesh arrow or 3D text in the scene for explanation — it is lit, bloomed, blocks camera rays and shows up
  in the control clay. Instead use `shot.graphics` (or tag an unavoidable mesh graphic `studio_scene_role: graphic`).
- Generated clips: the edit refuses `GRAPHICS_UNSUPPORTED` unless the clip passed the hybrid structure check
  (`anchors_2d.json`), the same rule as labels. Render the layer before the edit: `GRAPHICS_NOT_RENDERED` otherwise.
- One idea per graphic, on screen ≥ 1 s (30 frames); draw-on 0.3–0.6 s. A dimension's value comes from `text: "auto"`
  (measured, never typed); names and other words are 2D labels. The value shows only while the line's midpoint is
  on screen and not behind geometry, so place a dimension in open space, not behind columns (measured on s01: a
  dimension inside the side level sat behind `st.col3.1.7` on every frame and its value never showed).

- `space: "screen"` (arrows): drawn in the frame — `points_2d` [[tail], [tip]] in normalized frame coordinates,
  `shaft_frac` (stroke width / frame height, 0.0075), `head_ratio` (3.5), `fade_frames` (8, linear). Built after the
  look (it reads the lens shift). Gate `GRAPHIC_ILLEGIBLE` per frame: length ≥ 0.08 H, head/shaft 3–4.5, the arrow's
  line ≥ 100 px (of 1080) from the focus of expansion, ≥ 25° to the optical flow there. An arrow on the flight line
  looks like a crack opening in the scene (s01 v0018: the world arrow on the dive axis read as a vertical seam).
  World arrows stay for things that move with the scene (a load on a beam).

Bad: a red box mesh labelled "LOAD" floating over the beam (lit, blurred, in the control pass).
Good: `{"graphic_id": "load", "kind": "arrow", "anchors": [[0, 2, 7.5], "beam"], "start_frame": 10, "draw_on_s": 0.4}`
plus a label "하중" on the same anchor.
