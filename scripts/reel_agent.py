#!/usr/bin/env python3
"""Run the repository production skill through native Codex, explicitly on Astra."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request',help='Natural-language scene/reel request')
    parser.add_argument('--project',help='Existing project relative to repository root')
    parser.add_argument('--dry-run',action='store_true',help='Print command without a model call')
    parser.add_argument('--approve-for-me',action='store_true',help='Use native automatic review for sandbox-boundary requests; does not bypass approval')
    parser.add_argument('--device',choices=['GPU','CPU'],default='GPU',help='Render device for studio render jobs (Metal GPU is ~11x faster outside the Codex sandbox)')
    parser.add_argument('--low-load',action='store_true',help='Keep the computer usable: CPU renders, one worker, start with one small frame')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    device='CPU' if args.low_load else args.device
    codex=shutil.which('codex')
    if not codex:parser.error('codex not found; install and authenticate native Codex first')
    context=''
    if args.project:
        path=(root/args.project).resolve()
        if not path.is_relative_to(root/'projects'):parser.error('--project must be inside projects/')
        if not path.is_dir():parser.error('project does not exist')
        context=f'\nExisting project: {path}\n'
    prompt=("Use .agents/skills/reel-production/SKILL.md. You are the production orchestrator. "
            "Read project state, make the requested output, inspect representative renders, "
            "and finish with the requested artifacts and an evidence-based report. "
            "For a still-preview request, return stills; produce a playable artifact only when video is requested. "
            "Use available subagents for bounded independent tasks; keep one scene writer. "
            "Preserve other projects. Technical tests alone don't establish visual quality.\n"
            f"Render device: STUDIO_RENDER_DEVICE={device} (prefix render commands with it; confirm renderer_actual.json). "
            "Before authoring, run `route plan` and decide camera.energy per shot. Never call paid generation "
            "(generate clip, asset image3d) unless the user approved that shot and cost in this conversation and `route approve` "
            "recorded their own words; never write approvals yourself. Generated frames must contain no text. "
            "Every named subject gets a subject spec before modelling (subject init/lint); you choose parts, relations and claims, "
            "numbers come from sources, subject trace/fit or from-dxf, never typed guesses. Iterate with the workbench "
            "(workbench start/call/commit, no exec); each rebuild names one --diagnosis, regressions auto-revert, stop after 3 "
            "non-improving builds and ask the user. Explore freely (variant_save, workbench compare) and commit one chosen "
            "variant with --why; declare deliberate shape changes in spec deviations instead of loosening checks.\n"
            +context+'\nUser request:\n'+args.request)
    cmd=[codex,'exec','-C',str(root),'-m','gpt-6-astra','--json']
    cmd.extend(['--approve-for-me'] if args.approve_for_me else ['--sandbox','workspace-write'])
    if not (root/'.git').exists():cmd.append('--skip-git-repo-check')
    cmd.append(prompt)
    env={**os.environ,'STUDIO_RENDER_DEVICE':device}
    if args.dry_run:print(json.dumps({'model':'gpt-6-astra','env':{'STUDIO_RENDER_DEVICE':device},'command':cmd},ensure_ascii=False,indent=2));return 0
    return subprocess.run(cmd,cwd=root,stdin=subprocess.DEVNULL,env=env).returncode

if __name__=='__main__':sys.exit(main())
