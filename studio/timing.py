"""Resolve speech-bound action intervals and rebuild only the affected saved scene."""
from copy import deepcopy
from pathlib import Path
from .audio import build_audio
from .common import StudioError, stable_hash, write_json
from .project import load_shot, project_dir, validate_shot


def resolve_actions(shot, cues):
    by_id={cue['cue_id']:cue for cue in cues}
    if len(by_id)!=len(cues):raise StudioError('INPUT_INVALID','Duplicate resolved cue ID')
    updated=deepcopy(shot);changes=[]
    for action in updated['actions']:
        binding=action.get('time_binding')
        if not binding:continue
        try:
            first=by_id[binding['start_cue_id']]
            last=by_id[binding['end_cue_id']]
        except KeyError as exc:
            raise StudioError('TIMING_CONFLICT',f'Missing resolved speech cue {exc}; scratch voice supports whole-sentence cues only') from exc
        start=first['start_frame']+binding.get('start_offset_frames',0)
        end=last['end_frame']+binding.get('end_offset_frames',0)
        if not 0<=start<end<=shot['duration_frames']:
            raise StudioError('TIMING_CONFLICT',f"Speech timing for {action['action_id']} exceeds shot or has no duration")
        if (start,end)!=(action['start_frame'],action['end_frame']):
            changes.append({'action_id':action['action_id'],'before':[action['start_frame'],action['end_frame']],'after':[start,end]})
        action['start_frame']=start;action['end_frame']=end
    validate_shot(updated)
    return updated,changes


def resolve_timing(project,shot_id):
    path=project_dir(project);shot=load_shot(path,shot_id)
    if not any(a.get('time_binding') for a in shot['actions']):
        return {'status':'unchanged','shot_id':shot_id,'scene_version':shot['scene_version'],'render_invalidated':False,'changes':[]}
    audio=build_audio(path,shot_id,mode='scratch')
    # Audio may update metadata revision; read the current revision before patching.
    shot=load_shot(path,shot_id)
    updated,changes=resolve_actions(shot,audio['cues'])
    if not changes:return {'status':'unchanged','shot_id':shot_id,'scene_version':shot['scene_version'],'render_invalidated':False,'changes':[]}
    if not shot['scene_version']:
        raise StudioError('INPUT_INVALID','Build the initial scene before resolving action timing')
    change={'base_revision':shot['revision'],'scope':'motion','targets':[c['action_id'] for c in changes],
            'change':{'actions':updated['actions']},'preserve':shot['preserve']}
    directory=path/'revisions'/('timing_'+stable_hash(change)[:16]);file=directory/'change.json';write_json(file,change)
    from .blender import revise_shot
    result=revise_shot(path,shot_id,file)
    write_json(directory/'timing.json',{'audio_request_key':audio['request_key'],'cues':audio['cues'],'changes':changes,'scene_version':result['scene_version']})
    return {**result,'changes':changes,'artifacts':result.get('artifacts',[])+[str(directory/'timing.json')]}


def register_commands(subparsers):
    p=subparsers.add_parser('timing',help='Resolve speech cues into immutable motion versions').add_subparsers(dest='timing_command',required=True).add_parser('resolve')
    p.add_argument('--project',required=True);p.add_argument('--shot',required=True)
    p.set_defaults(handler=lambda a:resolve_timing(a.project,a.shot))
