from __future__ import annotations
import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

from .common import REPO, StudioError, blender_binary




def doctor(args=None):
    checks = {}
    for name in ('ffmpeg','ffprobe','say'):
        executable = shutil.which(name)
        checks[name] = {'available': bool(executable), 'path': executable}
    try:
        binary = blender_binary()
        version = subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=15).stdout.splitlines()[0]
        checks['blender'] = {'available':True,'path':binary,'version':version}
    except Exception as exc:
        checks['blender'] = {'available':False,'error':str(exc)}
    for name in ('PIL','jsonschema'):
        try:
            package = importlib.import_module(name); checks[name] = {'available':True,'version':importlib.metadata.version('Pillow' if name == 'PIL' else name)}
        except ImportError:
            checks[name] = {'available':False}
    try:
        from .common import font_file
        checks['font'] = {'available': True, 'path': str(font_file({}, REPO))}
    except Exception as exc:
        checks['font'] = {'available': False, 'error': str(exc)}
    try:
        from .asset_factory.factory import doctor_check
        checks['cad_venv'] = doctor_check()  # optional: only needed for asset generate
    except Exception as exc:
        checks['cad_venv'] = {'available': False, 'error': str(exc)}
    api = sorted((REPO / 'library' / 'api').glob('blender-*.json'))
    checks['api_index'] = {'available': bool(api), 'path': str(api[-1]) if api else None,
                           'matches_blender': bool(api) and checks['blender'].get('available', False) and api[-1].stem.split('-', 1)[1] in checks['blender'].get('version', '')}
    if checks['say']['available']:
        result = subprocess.run(['say','-v','?'],capture_output=True,text=True,timeout=10)
        checks['korean_voice'] = {'available':any('Yuna' in line and 'ko_KR' in line for line in result.stdout.splitlines())}
    usage = shutil.disk_usage(REPO)
    return {'status':'ready' if all(checks[k]['available'] for k in ('blender','ffmpeg','ffprobe','PIL','jsonschema')) else 'needs_setup',
            'python':sys.version.split()[0],'checks':checks,'disk_free_bytes':usage.free,'artifacts':[]}


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m studio',description='Local Astra + Blender reel production tools. All results are JSON; media and scenes remain editable files.')
    subparsers=parser.add_subparsers(dest='command',required=True)
    subparsers.add_parser('doctor').set_defaults(handler=doctor)
    worker=subparsers.add_parser('_worker',help=argparse.SUPPRESS); worker.add_argument('job_path'); worker.add_argument('token')
    from .render_worker import run_worker
    worker.set_defaults(handler=lambda a:run_worker(a.job_path,a.token))
    for name in ('project','blender','jobs','assets','references','audio','edit','qa','timing','routing','generative.clip','subjects','workbench','repair','api_index','motion_style','camera_fit','graphics','look_style','composition_style','critique','fill','facts','decisions','storyboard','freeze','contrib'):
        try:
            module=importlib.import_module('studio.'+name)
        except ModuleNotFoundError as exc:
            if exc.name=='studio.'+name:
                continue
            raise
        module.register_commands(subparsers)
    args=parser.parse_args(argv)
    operation='.'.join([args.command]+[getattr(args,key) for key in vars(args) if key.endswith('_command') and getattr(args,key)])
    try:
        result=args.handler(args)
        warnings=[]
        if getattr(args, 'project', None) and args.command not in ('project',):
            try:   # bookkeeping after the command: a failure here must not turn a completed command into a failed one
                from .project import status_project
                status_project(args.project)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f'STATUS_REFRESH_FAILED: {type(exc).__name__}: {exc}')
        result={'ok':True,'operation':operation,'project_id':None,'run_id':None,'job_id':None,'status':'complete','artifacts':[],'warnings':[],'error':None,**(result or {})}
        result['warnings']=list(result['warnings'])+warnings
        print(json.dumps(result,ensure_ascii=False,default=str))
        return 0
    except StudioError as exc:
        print(json.dumps({'ok':False,'operation':operation,'status':'failed','artifacts':[],'warnings':[],'error':exc.as_dict()},ensure_ascii=False))
        # 1 = retrying may succeed (the error says so); 2 = fix the input or get a decision first.
        return 1 if exc.retryable else 2
    except (OSError,ValueError) as exc:
        print(json.dumps({'ok':False,'operation':operation,'status':'failed','error':{'code':'INPUT_INVALID','message':str(exc),'retryable':False,'recovery':'Check command input and file contracts'}},ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 - a bug in the tools, not in the input
        import traceback
        tail=''.join(traceback.format_exception(exc)[-3:])[-800:]
        print(json.dumps({'ok':False,'operation':operation,'status':'failed','error':{'code':'INTERNAL_ERROR','message':f'{type(exc).__name__}: {exc}','retryable':False,
                          'recovery':'A tool bug: report it with this traceback; do not retry blindly','traceback':tail}},ensure_ascii=False))
        return 3

if __name__=='__main__':
    raise SystemExit(main())
