"""Rebuild jet_canyon_rig's chase in a throwaway copy and compare its camera rig to the recorded version.

The jet rig is the engine's byte-identity reference: rig_hash and every per-frame sample (camera position,
pitch, lens, clearance, target visibility...) of a fresh build must equal the version on disk. The project
itself is never touched (the copy lives in a temporary directory inside projects/ so paths resolve).
Run: .venv/bin/python tests/rig_regression.py
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402

HV = ROOT / 'projects' / 'harness_validation'
SOURCE = ROOT / 'tests' / 'fixtures' / 'jet_canyon_rig'   # tracked: contracts, author, recorded report (no .blend)


def main():
    shot = read_json(SOURCE / 'shots/chase/shot.json')
    version = shot['scene_version']
    recorded = read_json(SOURCE / 'shots/chase/versions' / version / 'camera_rig_report.json')
    author = SOURCE / Path(read_json(SOURCE / 'shots/chase/versions' / version / 'changes.json')['author_original']).name
    HV.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.rig-regression-', dir=HV))
    try:
        copy = tmp / 'jet_canyon_rig'
        shutil.copytree(SOURCE, copy, ignore=shutil.ignore_patterns('versions', 'renders', 'final', 'edit', 'control', 'runs', 'candidates', 'workbench', 'comparisons'))
        fresh = dict(shot, scene_version=None)
        write_json(copy / 'shots/chase/shot.json', fresh)
        built = build_shot(copy, 'chase', copy / author.name)
        report = read_json(copy / 'shots/chase/versions' / built['scene_version'] / 'camera_rig_report.json')
    finally:
        shutil.rmtree(tmp)
    same_hash = report['rig_hash'] == recorded['rig_hash']
    diffs = [i for i, (a, b) in enumerate(zip(report['samples'], recorded['samples'])) if a != b]
    keys = sorted({k for i in diffs for k in set(report['samples'][i]) | set(recorded['samples'][i])
                   if report['samples'][i].get(k) != recorded['samples'][i].get(k)})
    example = {k: (recorded['samples'][diffs[0]].get(k), report['samples'][diffs[0]].get(k)) for k in keys} if diffs else {}
    result = {'version': version, 'rig_hash_same': same_hash, 'samples': len(report['samples']),
              'sample_frames_differing': diffs[:10], 'n_differing': len(diffs) + abs(len(report['samples']) - len(recorded['samples'])),
              'differing_keys': keys, 'example': example, 'summary_new_keys': sorted(set(report['summary']) - set(recorded['summary']))}
    print(json.dumps(result))
    return 0 if same_hash and not result['n_differing'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
