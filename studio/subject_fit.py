"""Fit a subject's free parameters to its reference silhouettes, keeping every sourced dimension.

The spec says which numbers may move (builders[].free: JSON pointer + bounds); everything else is
fixed. One resident workbench session rebuilds only the changed parts per evaluation, the host scores
the silhouettes (datum-registered IoU, or true-scale aligned IoU without a datum) and subtracts a penalty
for any sourced dimension outside its tolerance. Bounded Nelder-Mead from several starts; the best
result is a candidate (subjects/<id>/candidates/fit_<n>.json) that --apply merges after lint.
"""
from __future__ import annotations

import copy
import time

from .common import StudioError
from .fidelity import aligned_iou, datum_iou, deviation_target, dimension_values, mask_iou, model_mask_scaled, reference_mask, silhouette_reference
from .subjects import load_spec, resolve_pointer, set_pointer

DIMENSION_WEIGHT = 2.0
UNOBSERVABLE_TOL = 1e-4  # a change worth less IoU than this is not supported by the drawing


class SubjectSession:
    """A workbench session holding one built subject (empty scene)."""

    def __init__(self, project, subject_id):
        self.project, self.subject_id, self.session_id = project, subject_id, None

    def __enter__(self):
        from . import workbench
        self.workbench = workbench
        self.session_id = workbench.start(self.project, subjects=[self.subject_id])['session_id']
        workbench.call(self.project, self.session_id, 'build_subject', {'subject_id': self.subject_id})
        return self

    def __exit__(self, *exc):
        self.workbench.stop(self.project, self.session_id)

    def geometry(self, spec, views=None):
        self.workbench.call(self.project, self.session_id, 'set_spec', {'subject_id': self.subject_id, 'spec': spec})
        return self.workbench.call(self.project, self.session_id, 'subject_report',
                                   {'subject_id': self.subject_id, 'views': views, 'geometry_only': True}, raw=True)['result']['geometry']


def nelder_mead(f, x0, max_evals, step=0.15, tol=1e-6):
    """Minimise f over the unit box [0, 1]^n (points are clipped). Returns (best_x, best_f, evals)."""
    n = len(x0)
    clip = lambda x: [min(1.0, max(0.0, v)) for v in x]  # noqa: E731
    evals = [0]
    def g(x):
        evals[0] += 1
        return f(x)
    simplex = [clip(x0)]
    for i in range(n):
        p = list(simplex[0]); p[i] = p[i] + step if p[i] + step <= 1 else p[i] - step
        simplex.append(clip(p))
    values = [g(p) for p in simplex]
    while evals[0] < max_evals:
        order = sorted(range(n + 1), key=values.__getitem__)
        simplex = [simplex[i] for i in order]; values = [values[i] for i in order]
        if abs(values[-1] - values[0]) < tol and max(abs(a - b) for p in simplex[1:] for a, b in zip(p, simplex[0])) < 1e-4:
            break
        centroid = [sum(p[i] for p in simplex[:-1]) / n for i in range(n)]
        worst = simplex[-1]
        reflected = clip([c + (c - w) for c, w in zip(centroid, worst)]); fr = g(reflected)
        if fr < values[0]:
            expanded = clip([c + 2 * (c - w) for c, w in zip(centroid, worst)]); fe = g(expanded)
            simplex[-1], values[-1] = (expanded, fe) if fe < fr else (reflected, fr)
        elif fr < values[-2]:
            simplex[-1], values[-1] = reflected, fr
        else:
            contracted = clip([c + 0.5 * (w - c) for c, w in zip(centroid, worst)]); fc = g(contracted)
            if fc < values[-1]:
                simplex[-1], values[-1] = contracted, fc
            else:
                for i in range(1, n + 1):
                    simplex[i] = clip([b + 0.5 * (p - b) for p, b in zip(simplex[i], simplex[0])])
                    values[i] = g(simplex[i])
    best = min(range(len(values)), key=values.__getitem__)
    return simplex[best], values[best], evals[0]


def free_parameters(spec):
    """[(absolute pointer, min, max)] from builders[].free."""
    out = []
    for i, builder in enumerate(spec['builders']):
        for free in builder.get('free', []):
            out.append((f'/builders/{i}{free["pointer"]}', float(free['min']), float(free['max'])))
    return out


def silhouette_scorer(spec, project, views):
    """view -> fn(triangles) -> IoU, using the datum when the silhouette has one."""
    scorers = {}
    for silhouette in spec.get('silhouettes', []):
        if silhouette['view'] not in views:
            continue
        if silhouette.get('datum'):
            reference = silhouette_reference(silhouette, project)
            scorers[silhouette['view']] = (lambda tris, s=silhouette, ref=reference:
                                           datum_iou(tris, ref, s['datum'], s['px_per_m'])[0])
        elif silhouette.get('px_per_m'):
            from pathlib import Path
            reference = reference_mask(Path(project) / silhouette['image'], silhouette.get('crop_px'), silhouette.get('invert', False),
                                       silhouette.get('outline', False), silhouette.get('erase_px', ()))
            scorers[silhouette['view']] = (lambda tris, s=silhouette, ref=reference: aligned_iou(model_mask_scaled(tris, s['px_per_m']), ref))
    return scorers


def dimension_penalty(spec, geometry):
    """Sum over sourced dimensions of how far outside tolerance they are (fraction of the value)."""
    values = dimension_values(spec, geometry)
    penalty, broken = 0.0, []
    for dim in spec['dimensions']:
        if dim['source_id'] == 'assumed':
            continue
        value = values.get(dim['id'])
        if value is None:
            penalty += 1.0; broken.append(dim['id']); continue
        target = dim['value_m'] * (deviation_target(spec, 'dimension', dim['id']) or {}).get('factor', 1.0)  # a declared deviation is the goal
        excess = abs(value - target) - target * dim['tol_pct'] / 100
        if excess > 0:
            penalty += excess / target; broken.append(dim['id'])
    return penalty, broken


def fit(project, subject_id, views=None, max_evals=None, starts=None, apply=False, trace_candidate=None):
    spec = load_spec(project, subject_id)
    settings = spec.get('fit', {})
    views = views or settings.get('views') or [s['view'] for s in spec.get('silhouettes', []) if s.get('datum') or s.get('px_per_m')]
    max_evals = int(max_evals or settings.get('max_evals', 150))
    n_starts = int(starts or settings.get('starts', 3))
    weight = float(settings.get('dimension_weight', DIMENSION_WEIGHT))
    free = free_parameters(spec)
    if not free:
        raise StudioError('FIT_NO_TARGET', f'{subject_id}: no builders[].free parameters to fit')
    scorers = silhouette_scorer(spec, project, views)
    if not scorers:
        raise StudioError('FIT_NO_TARGET', f'{subject_id}: no scaled silhouette (px_per_m) for views {views}')
    def to_spec(x):
        candidate = copy.deepcopy(spec)
        for (pointer, lo, hi), t in zip(free, x):
            set_pointer(candidate, pointer, round(lo + t * (hi - lo), 5))
        return candidate
    def unit(values):
        return [min(1.0, max(0.0, (v - lo) / (hi - lo))) for v, (_, lo, hi) in zip(values, free)]
    history = []
    started = time.monotonic()
    with SubjectSession(project, subject_id) as session:
        def evaluate(x):
            candidate = to_spec(x)
            geometry = session.geometry(candidate, views=list(scorers))
            ious = {view: score(geometry['silhouettes'].get(view, [])) for view, score in scorers.items()}
            penalty, broken = dimension_penalty(candidate, geometry)
            value = sum(ious.values()) / len(ious) - weight * penalty
            history.append({'score': round(value, 5), 'ious': {k: round(v, 4) for k, v in ious.items()}, 'broken': broken})
            return -value
        current = unit([resolve_pointer(spec, p) for p, _, _ in free])
        before = -evaluate(current); before_row = history[-1]
        seeds = [current]
        if trace_candidate:
            traced = [trace_candidate.get(p, resolve_pointer(spec, p)) for p, _, _ in free]
            seeds.append(unit(traced))
        seeds.append([0.5] * len(free))
        seeds = seeds[:max(1, n_starts)]
        best = (current, -before)
        per_start = max(len(free) + 2, (max_evals - 1) // len(seeds))
        runs = []
        for seed in seeds:
            x, fx, used = nelder_mead(evaluate, seed, per_start)
            runs.append({'evals': used, 'score': round(-fx, 5)})
            if fx < best[1]:
                best = (x, fx)
        # Unobservable parameters (hidden in every fitted view) get no signal; keep their declared values.
        x = list(best[0]); kept = -best[1]; unobservable = []
        for i in range(len(free)):
            if abs(x[i] - current[i]) < 1e-9:
                continue
            trial = list(x); trial[i] = current[i]
            value = -evaluate(trial)
            if value >= kept - UNOBSERVABLE_TOL:
                x, kept = trial, max(kept, value); unobservable.append(free[i][0])
        after_spec = to_spec(x)
        after = -evaluate(x); after_row = history[-1]
    changes = [{'pointer': p, 'before': resolve_pointer(spec, p), 'after': resolve_pointer(after_spec, p), 'bounds': [lo, hi]}
               for p, lo, hi in free if resolve_pointer(spec, p) != resolve_pointer(after_spec, p)]
    improved = after > before + 1e-6
    result = {'subject_id': subject_id, 'views': list(scorers), 'free_parameters': len(free), 'evaluations': len(history),
              'seconds': round(time.monotonic() - started, 1), 'starts': runs,
              'before': before_row, 'after': after_row, 'improved': improved, 'kept_unobservable': unobservable,
              'changes': changes if improved else [], 'patch': {c['pointer']: c['after'] for c in changes} if improved else {}}
    from .subject_trace import _next_candidate, apply_patch
    from .common import write_json
    path = _next_candidate(project, subject_id, 'fit')
    write_json(path, {**result, 'history': history})
    if apply and result['patch']:
        apply_patch(project, subject_id, result['patch'])
    return {**result, 'applied': bool(apply and result['patch']), 'candidate_path': str(path), 'artifacts': [str(path)]}
