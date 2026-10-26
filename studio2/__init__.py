"""v2: a generation-first pipeline for short vertical cutaway explainer reels (`python -m studio2`).

The story, the script, the decision chain, the readers and the assembly are v1's (`studio/`): this package imports them
and never copies them. What v2 adds is how a cut is made: instead of a Blender scene, each cut is planned as a cutaway
(what it opens, what flows through it, which parts are named, its scale step and its camera - the `cuts` decision layer,
studio2/cuts.py), drawn as pictures by the image tool (first, middle and last frame; studio2/images.py, frames.py),
checked by a judge and located (checks.py, locate.py), and moved and marked locally (motion.py: a push or a pull, the
first picture changing into the last, flows and labels drawn in). The preview reel is v1's edit with v1's scratch voice
and captions; v1's `reel see` and `reel judge` watch it.

Why (2026-10-26, the stage-1 A/B on three beats of the fab reel): cutaway grammar with generated pictures ranked first
on style 4 of 4 against the 3D clay, and the blind viewer read it best; the user: "생성이 훨씬 낫네", "v2로".
"""
