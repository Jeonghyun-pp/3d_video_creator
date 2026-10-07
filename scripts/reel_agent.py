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
    parser.add_argument('--log-dir',help='Write the run record here: events.jsonl (codex --json), stderr.log and run.json (start, end, exit code, thread id, arguments) - what scripts/astra_run_metrics.py and scripts/run_status.py read')
    parser.add_argument('--delegate',metavar='USER_WORDS',help="The user's own words handing this run's decisions to Astra: recorded on --project now, or on the project this run creates (project init); no decision-ladder sheets")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
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
            "Ask the user only for paid calls, approvals in their words, frozen-code edits, a large deviation on a real subject and "
            "anything outside the request; decide everything else yourself and record it (decide note --topic --choice --why). "
            "Build what shows concretely (SKILL #8): list the reference's detail in four tiers first, model every item or put it in "
            "the simplification table with its on-screen size; passing acceptance is the floor - while previews are free, fix the "
            "largest remaining difference. "
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
    if args.delegate:
        from studio.generative.review import check_user_words
        words=check_user_words(args.delegate,'delegation')
        if args.project:
            if not args.dry_run:
                from studio.decisions import delegate
                delegate(root/args.project,words)
        else:   # the project does not exist yet: project init records it (studio/decisions.claim_pending_delegation)
            pending=root/'.studio'/'delegation.json'
            if not args.dry_run:
                pending.parent.mkdir(exist_ok=True);pending.write_text(json.dumps({'user_words':words},ensure_ascii=False))
        prompt_note=('\n\nThis run is delegated by the user ("'+words+'"): open no decision-ladder sheets; '
                     + ('the delegation is recorded on the project.' if args.project else 'project init records it on the project you create.'))
        cmd[-1]=cmd[-1]+prompt_note
    if args.dry_run:print(json.dumps({'model':'gpt-6-astra','env':{'STUDIO_RENDER_DEVICE':device},'command':cmd},ensure_ascii=False,indent=2));return 0
    if not args.log_dir:
        return subprocess.run(cmd,cwd=root,stdin=subprocess.DEVNULL,env=env).returncode
    import time
    log=Path(args.log_dir).resolve();log.mkdir(parents=True,exist_ok=True)
    record={'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'model':'gpt-6-astra','effort':args.effort,'project':args.project,
            'images':images,'delegate':args.delegate,'request':args.request}
    (log/'run.json').write_text(json.dumps(record,ensure_ascii=False,indent=1))
    with open(log/'events.jsonl','w') as out,open(log/'stderr.log','w') as err:
        code=subprocess.run(cmd,cwd=root,stdin=subprocess.DEVNULL,env=env,stdout=out,stderr=err).returncode
    first=(log/'events.jsonl').read_text().split('\n',1)[0]
    try:thread=json.loads(first).get('thread_id')
    except ValueError:thread=None
    record.update({'finished_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'exit_code':code,'thread_id':thread})
    (log/'run.json').write_text(json.dumps(record,ensure_ascii=False,indent=1))
    return code

if __name__=='__main__':sys.exit(main())
