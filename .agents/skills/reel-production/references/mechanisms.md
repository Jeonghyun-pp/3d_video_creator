# Mechanisms — parts that move together (gears, joints, linkages)

`studio/mechanisms.py` (spec generators), `studio/blender_ops/gear_core.py` (involute gears, planetary layout),
`kinematics_core.py` (couplings, pure), `kinematics.py` (pivots, keys, interference).

## Spec
A subject spec declares `joints` (`revolute`: degrees about `axis`; `prismatic`: metres along it; `parent` is a part or
`root`, `child` the part that moves, `origin` in the subject frame) and `couplings` between joints. Coupling kinds are one
row each in `kinematics_core.COUPLINGS` (unknown kinds refused; a joint driven twice or a cycle refused):

| kind | fields | follows |
|---|---|---|
| `gear` | `driver`, `driven`, `teeth: [a, b]` | driven = −driver·a/b |
| `internal_gear` | same | driven = +driver·a/b |
| `belt` | `driver`, `driven`, `ratio` | driven = driver·ratio |
| `rack` | `driver`, `driven`, `radius_m` | metres = radians·radius |
| `planetary` | `sun`, `carrier`, `planets`, `teeth: {sun, planet, ring}`, `ring_part` | ring fixed: carrier = sun·zs/(zs+zr) |

Gear numbers come from the gear's definition, never typed: `subject planetary --project P --subject reducer --module
0.002 --sun 18 --planet 27 --ring 72 --planets 3` writes the whole spec (outlines, planet centres and phases, the ring's
phase, joints, coupling). It refuses sets that cannot mesh (ring ≠ sun + 2·planet, unequal spacing, planets < 17 teeth).
Profile shapes `{'gear': {module, teeth}}` and `{'internal_tooth': {module, ring_teeth}}` are available to any spec.

## Motion
A `drive` action (`targets: [{instance_id, part_id}]`, `params.drives: [{joint, subject (default: the target instance),
rpm | keys: [{t, value}], profile: 'linear' | 'ease'}]`; every action type's readable params are rows of
`studio/blender_ops/action_params.py`) turns its input joints; every coupled joint is keyed per frame (LINEAR), planets ride their carrier.

## Gate
After the drive, every two parts that can move relative to each other (different nearest joint pivots - not only the
coupled pairs: a rim or housing can be in the way) are tested at 13 frames with surfaces inset 0.05 % of the subject's
size: any overlap is `MECHANISM_INTERFERENCE` (pair and frame in `kinematics_report.json`). Measured: the 18/27/72×3
reducer passes 20 pairs; a planet half a tooth off and a ring rim capped into a plate are both refused.
Recovery: fix the spec (phase, centre distance, a closed profile), never loosen the inset.
