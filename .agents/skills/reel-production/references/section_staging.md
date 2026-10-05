# Section staging — show a structure as a cut, the way architectural cutaways do

Code: `studio/blender_ops/section.py` (`stage`, `poche`, `front_cutter`), move `section_push`
(`camera_moves_core.py`), reveal (`reveal.py`); smoke `tests/studio/section_smoke.py`. Read when a shot must make an
underground or enclosed structure readable (stations, basements, tunnels, a building's floors).

## Recipe
1. Build the structure with its front end open (`cutaway`), a box ref for it (an empty with a cube display).
2. `section.stage(name, box, ceilings=[...])`: soil blocks around the box (sides, below, up to the ground), poché
   on every face lying on the cut plane, per-level ceiling area lights (`studio_keep_light`, ~90 W per bay) and
   linear LED rows (`light_fixture`) near the face.
3. The ground in front of the face: a `reveal` action whose cutter comes from `section.front_cutter(...)` (origin on
   the face, keys scale y 0.02 → 1, so the cut grows from the section toward the camera), `also_cut_overlapping`
   (sidewalks, markings), cap = the poché material. Keep fixtures out of that ground (`street(..., bare=[...])`)
   and traffic out of it (`keep_clear`).
4. Camera: `section_push` with `framing.horizon_v` — level, the section centre at `centre_v` (0.6–0.7), the section
   `fill` 0.45–0.6 of the frame width, then push in.

## Rules
- The cut reads only against mass and light: soil around it, poché on the cut, interior albedo ~0.75 and lights per
  bay. Measured 2026-10-05 (verify S1): with these the interior/aerial luminance ratio was 0.93–1.0 and 4–5 levels
  read; without (one night look, dark concrete) the interior was 2–2.5× too dark and the station read as a corridor.
- Poché goes on the structure's cut faces only; the soil keeps its strata material (poché on the soil face made the
  whole ground a black wall in s01 v0020).
- Looking down a corridor is not a section. Frame the cut frontally (≤ 6° off its normal) before entering it.

Bad: dive through a hole in the road and look down the station's length (v0018: the levels were never seen).
Good: s01 v0023 — `section_push(section='st.box', fill=0.6, centre_v=0.68)` + ground cut + `section.stage`.
