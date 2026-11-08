"""Read-only P1 ghost-plan replay of an authentic captured source batch.

Arguments: FRAME_DIRECTORY OUTPUT_DIRECTORY CALLER_BUDGET_SECONDS.
No source selector, placement, field construction or commit runs here.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import time
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.controller_plan import certify_source_batch
from smilepcb.keyboard_routing.controller_corridor_plan import plan_controller_corridors
from smilepcb.keyboard_routing.conditional_banks import _source_physics
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_process import apply_native_rules

frame=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
budget=float(sys.argv[3])
if not math.isfinite(budget) or budget<=0:raise ValueError('Explicit positive finite caller budget required')
capture=json.loads((frame/'capture.json').read_text())
recorded_rules=json.loads((frame/'native-rules.json').read_text())
manager=p.GetSettingsManager();owners=[manager]
project_path=str(frame/'source.kicad_pro')
if not manager.LoadProject(project_path):raise ValueError('Cannot load recorded frozen source project')
project=manager.GetProject(project_path);board=p.LoadBoard(str(frame/'source.kicad_pcb'));board.SetProject(project)
owners.extend([project,board.GetDesignSettings()])
actual_profile_mm=apply_native_rules(board,recorded_rules['profile'])
if actual_profile_mm!=recorded_rules['profile_mm']:
    raise ValueError('Recorded frozen source process profile numeric rules differ')
def native_rules(board):
    settings=board.GetDesignSettings();attributes={}
    for key in dir(settings):
        if not key.startswith('m_'):continue
        try:value=getattr(settings,key)
        except Exception:continue
        if value is None or isinstance(value,(str,int,float,bool)):attributes[key]=value
    return dict(profile=recorded_rules['profile'],profile_mm=actual_profile_mm,
        native_scalar_attributes=attributes,default_netclass_clearance_nm=settings.m_NetSettings.GetDefaultNetclass().GetClearance())
loaded_rules=native_rules(board)
if loaded_rules!=recorded_rules:
    raise ValueError('Reloaded Native scalar settings or default netclass differ from original recorded source context')
entry=snapshot(board)
if entry!=capture['snapshot']:raise ValueError('Reloaded frozen source differs from its captured actual frame')
cfg=deepcopy(capture['cfg']);model=deepcopy(cfg);started=time.monotonic();deadline=started+budget
report=dict(accepted=False,scope='P1 early whole-corridor ghost planning only; no field or all-board closure qualification',
    source_frame=str(frame),source_count=len(capture['source']['ports']),generation=capture['generation'],caller_budget_seconds=budget,
    native_project_loaded=True,native_rules_exact=True,original_physical_sha256_available=False,
    native_rules=loaded_rules,stage='original_before_recertification')
batch=None
with generation_profile(capture['generation']):
    try:
        batch=certify_source_batch(board,cfg,deepcopy(capture['before']),deepcopy(capture['source']),
            matrix_nets=set(capture['matrix_nets']),requested_nets=set(capture['requested_nets']),
            lineage=deepcopy(capture.get('source_lineage') or {}),deadline=deadline)
        original=capture.get('original_source_seal')
        if original is not None and batch.seal!=original:
            raise ValueError('Reissued frozen batch differs from original source seal')
        report.update(recertified=True,original_source_seal=original,reissued_source_seal=batch.seal,
            original_seal_exact=original is not None,requested_nets=sorted(batch.requested_nets),
            already_accessible=list(batch.already_accessible),stage='component_corridors')
        plan=plan_controller_corridors(board,cfg,batch,deadline=deadline)
        expected={tuple(sorted(port['pad_ids'])) for port in capture['source']['ports']}
        actual={tuple(port['source_key']) for port in plan.binding['ports']}
        if actual!=expected or len(plan.binding['ports'])!=len(expected):
            raise ValueError('Frozen replay omitted or duplicated a physical source identity')
        report.update(accepted=True,stage='whole_ghost_plan_proved',plan_sha256=plan.seal,
            source_keys=[list(key) for key in sorted(actual)],plan=plan.receipt)
    except (ValueError,TimeoutError) as error:
        report['reason']=str(error)
    finally:
        report['board_exact']=snapshot(board)==entry
        report['model_exact']=cfg==model
        report['source_native_physics_unchanged_during_replay']=batch is not None and _source_physics(board,set(batch.tracks)|set(batch.pads))==batch.physical_sha256
        report['native_rules_unchanged_during_replay']=native_rules(board)==loaded_rules
        report['elapsed_seconds']=time.monotonic()-started
        (out/'report.json').write_text(json.dumps(report,indent=2))
        if not report['board_exact'] or not report['model_exact'] or not report['native_rules_unchanged_during_replay']:
            raise ValueError('Frozen source replay changed original board or model')
print(json.dumps(report))
raise SystemExit(0 if report['accepted'] else 2)
