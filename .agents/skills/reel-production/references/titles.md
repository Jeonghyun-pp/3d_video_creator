# Titles — 2D words that stay in the frame

Code: `studio/titles.py` (raster, placement, safe-rect check), `studio/edit.py` (`make_overlays`), `studio/qa.py`
(`title_safe_area`); tests `tests/test_titles.py`. Read when a shot shows a title, a place name or any big text.

## Spec (`shot.titles[]`, up to 4)
`title_id`, `text` (`\n` for lines), `start_frame`, `end_frame`, `anim` (`hold` | `recede`), `anchor` [x, y] (where
the ink's alpha centroid sits, 0..1 from top-left), `width_frac` (ink width at scale 1, ≤ 0.78), `weight` (a face
of the pinned font: ExtraBold, Heavy, Bold...), `color_srgb`, `fade_in_frames`, `fade_out_frames`, `scale_curve`.
`recede` shrinks along a monotone curve (default measured from the reference: 1, .80, .69, .59, .48, .38, .24, .16,
.07 over the span) toward the anchor: the "title flies into the scene" move without a 3D object.

## Rules
- Titles are 2D, composited in the edit under captions and labels. Forbidden: 3D text for a title — it is placed for
  one camera and drifts off screen when the move is re-timed (s01 v0018: 78 % above the frame for frames 1–30) and
  the rig gate `graphic_in_frame` refuses it. Instead `shot.titles`.
- Every frame's ink box must lie in the title safe rect (`style.title_safe_rect_normalized`, default x .11–.89,
  y .14–.65: the feed UI covers the rest); else the edit stops with `TITLE_OUT_OF_SAFE`. Fix by, in order:
  1) move the anchor toward the centre; 2) lower `width_frac`; 3) split the text into title + smaller subtitle.
  Forbidden: widening the safe rect for one shot.
- A weight the font does not have is an error (`FONT_UNAVAILABLE`), never a silent fallback.
- Sub-pixel placement (premultiplied fractional-box resize) keeps a slowly shrinking title from shimmering; the
  measured centroid jitter is < 0.35 px.

Bad: `L.text('title', '삼성역\n기둥철근', (0, 60, 40), 13.0)` in the author script.
Good: `{"title_id": "title", "text": "삼성역\n기둥철근", "start_frame": 0, "end_frame": 42, "anim": "recede", "anchor": [0.5, 0.40], "width_frac": 0.70, "weight": "ExtraBold"}`.
