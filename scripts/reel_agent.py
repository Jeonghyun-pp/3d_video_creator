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
    parser.add_argument('--effort',choices=['low','medium','high','xhigh'],default='medium',help="Astra's own reasoning effort (orchestration); subagents get theirs per task (references/blender_freedom.md)")
    parser.add_argument('--image',action='append',default=[],help='Image to attach to the request (reference photo, drawing); repeatable. Must be inside projects/')
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
            f"Render device: STUDIO_RENDER_DEVICE={device} (prefix render commands with it; confirm renderer_actual.json). It applies to "
            "render jobs only: workbench previews, id passes and the build's frame probe always use the GPU briefly - that is not a "
            "conflict with a CPU limit, use them. "
            "Before authoring, run `route plan` and decide camera.energy per shot. Never call paid generation "
            "(generate clip, asset image3d) unless the user approved that shot and cost in this conversation and `route approve` "
            "recorded their own words; never write approvals yourself. Generated frames must contain no text. "
            "Every named subject gets a subject spec before modelling (subject init/lint); you choose parts, relations and claims, "
            "numbers that make a claim come from sources, subject trace/fit or from-dxf, never typed guesses; appearance shape is "
            "free (spec ops, any bpy) and illustrative. Iterate with the workbench (workbench start/call/commit; exec explores, "
            "cannot be committed); each rebuild names one --diagnosis, regressions auto-revert, while fidelity fails stop after 3 "
            "non-improving builds and ask the user. Explore freely (variant_save, workbench compare) and commit one chosen "
            "variant with --why; declare deliberate shape changes in spec deviations instead of loosening checks. "
            "Every value of a shot is reachable by the user's words: use a word-op, else set/add/remove on its path "
            "(storyboard revise --ops, fill revise --ops, workbench set_shot_value); never say a value cannot be changed. "
            "Blender is yours within structural lines (references/blender_freedom.md): any bpy in an author script, new shapes "
            "or joint laws as contrib entries, looks as shot.render grade/compositor/engine_settings; read frame_probe images "
            "after every build. Blender cannot start in your sandbox: run every studio command that needs it (shot build/revise, "
            "storyboard, render, graphics, generate inputs/control, camera fit) through the MCP tool studio_run {args: [...]}, and "
            "iterate with the studio_workbench tools. When you spawn a subagent, pass model and reasoning_effort explicitly "
            "(agent type files are not applied): high for new shapes or motion laws (contrib), scene writing, failure diagnosis "
            "and visual review; medium for specs and workbench iteration; low for inventories and reading.\n"
            +context+'\nUser request:\n'+args.request)
    images=[]
    for image in args.image:
        path=(root/image).resolve() if not Path(image).is_absolute() else Path(image).resolve()
        if not path.is_relative_to(root/'projects') or not path.is_file():parser.error(f'--image {image}: put the image inside projects/ (references stay local)')
        images.append(str(path))
    if images:
        prompt+=('\n\nAttached reference images (local only: never commit them or send them to a generation model; numbers read off a photo '
                 'are estimates - mark them so, and prefer sourced specifications): '+', '.join(str(Path(i).relative_to(root)) for i in images))
    cmd=[codex,'exec','-C',str(root),'-m','gpt-6-astra','-c',f'model_reasoning_effort="{args.effort}"','--json']
    for image in images:cmd.extend(['-i',image])
    cmd.extend(['--approve-for-me'] if args.approve_for_me else ['--sandbox','workspace-write'])
    if not (root/'.git').exists():cmd.append('--skip-git-repo-check')
    cmd.append(prompt)
    env={**os.environ,'STUDIO_RENDER_DEVICE':device}
    if args.dry_run:print(json.dumps({'model':'gpt-6-astra','env':{'STUDIO_RENDER_DEVICE':device},'command':cmd},ensure_ascii=False,indent=2));return 0
    return subprocess.run(cmd,cwd=root,stdin=subprocess.DEVNULL,env=env).returncode

if __name__=='__main__':sys.exit(main())
