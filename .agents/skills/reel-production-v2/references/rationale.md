# Why the v2 rules are as they are (measured)

- **Generation-first** (2026-10-26, stage-1 A/B on three beats of the fab reel): cutaway grammar with generated pictures
  ranked first on style 4 of 4 readings against the same content as plain generated pictures and against v1's 3D clay; the
  blind viewer read it best (3 of 6 "yes"). The user: "생성이 훨씬 낫네", "v2로".
- **#1 one view again and again** (2026-10-26, the dev preview): 5 of 8 cuts (c1, c3, c4, c5, c7, and c8 returning to c1)
  came out as nearly the same frontal view of the cleanroom bay. The picture check `opens_shown` passed them - they were
  sections - so the miss was the sameness, not the absence of a section: the same-view reader and SAME_PICTURE /
  PICTURE_REPEATS read it from the pictures.
- **#3 painted, not pasted** (2026-10-26): c1's wafer box (a sprite 0.12 of the frame tall) and c8's (0.26) sat on top of the
  picture; specks (0.011) read fine.
- **#4 rhythm** (2026-10-26): one cut per line gave a 5.3 s median; the cutaway reference's cuts: median 2.4 s (p25 1.5, p75
  4.6; weak: its reels' medians run 1.5-5.2 s); 78 % of its reels keep their median under 4.6 s; it speaks 7.4 characters
  a second.
- **Label and caption letters** (phase 0, a reader calibrated on drawn labels, 1.126x the ink): reference labels median
  0.033 of the frame height (p25 0.023, p75 0.045), subtitles 0.032 (0.030-0.034); v1's captions were about 60 % of that.
- **Readers at most 2** (2026-10-26): five codex readers started together failed and stalled.
- **The style verdict** (2026-10-27): read as yes/partly/no against four reference cuts, the reader called 8 of 8 held-out
  reference cuts and a plain photo alike "partly" - a verdict that cannot separate; it scores 0-10 against eight, and only a
  score that separates every calibration reading may be a floor (library/style_check.json).
- **Generated motion is checked by a person** (the user, 2026-10-16), against the narration, before a cut moves on.
