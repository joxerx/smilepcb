"""Root-only practical baseline/two-leader assembly and local proof experiment."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.stm32.core_pose_native import _Runtime,_rules

ap=argparse.ArgumentParser()
for key in ('raw-project','core','baseline-board','baseline-model','reference-project','placement','native-rules','output'):ap.add_argument('--'+key,type=Path,required=True)
ap.add_argument('--budget',type=float,default=90);ap.add_argument('--case',choices=('baseline','all'),default='all');ap.add_argument('--assembly-only',action='store_true')
a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
recorded=json.loads(a.native_rules.read_text());placement=json.loads(a.placement.read_text());cfg=json.loads(a.baseline_model.read_text())
manager=p.GetSettingsManager();pro=str(a.reference_project.resolve())
if not manager.LoadProject(pro):raise ValueError('Cannot load recorded reference project')
project=manager.GetProject(pro);board=p.LoadBoard(str(a.baseline_board.resolve()));board.SetProject(project);owners=[manager,project,board.GetDesignSettings()]
if apply_native_rules(board,recorded['profile'])!=recorded['profile_mm']:raise ValueError('Process numeric values changed')
context={key:recorded[key] for key in ('native_scalar_attributes','default_netclass_clearance_nm')}
if _rules(board)!=context:raise ValueError('Reference Native rules differ')
ledger=json.loads((a.raw_project/'.smilepcb-generated.json').read_text());imported=set(ledger['imported'])
core_refs={'U_MCU'}|{'C_CORE'+str(i) for i in range(1,8)}
core=[q for q in board.GetFootprints() if q.GetReference() in core_refs]+[q for q in board.GetTracks() if q.m_Uuid.AsString() in imported]
if len([q for q in core if isinstance(q,p.FOOTPRINT)])!=8 or len(imported)!=8:raise ValueError('Missing positive imported rigid-block ownership')
request=dict(project=str(a.output.resolve()),lookahead_raw_project=str(a.raw_project.resolve()),lookahead_reference_project=pro,core=str(a.core.resolve()),
    process_profile=recorded['profile'],generation_profile=placement['generation_profile'],routing_quality=placement['usb_placement']['usb']['acceptance_context']['quality']['name'],
    fanout_rows=placement['fanout_placement_reservation']['via_rows'],mirror_core=placement['core_face']=='F.Cu',lookahead_native_candidates=3)
fixed=placement['usb_placement']['selected_pose'];baseline=placement['core_pose'];started=time.monotonic();deadline=started+a.budget
report=dict(scope='geometric assembly + local Native admission only; no full routing',accepted=False,attempts=[],source_hashes={str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest() for path in (a.core,a.baseline_board,a.baseline_model,a.native_rules,a.raw_project/'model-before-placement.json')})
with generation_profile(request['generation_profile']),using_quality_profile(request['routing_quality']):
    runtime=_Runtime(board,cfg,request,core_objects=core,baseline_pose=baseline,fixed_pose=fixed,deadline=deadline)
    poses=[dict(baseline,pose_key='baseline')] if a.case=='baseline' else runtime.poses()[:3]
    (a.output/'shortlist.json').write_text(json.dumps(poses,indent=2));allowance=(deadline-time.monotonic())/len(poses)
    for index,pose in enumerate(poses):
        cutoff=min(deadline,time.monotonic()+allowance);attempt=dict(index=index,pose=pose,accepted=False);scene=None;began=time.monotonic()
        try:
            scene=runtime.assemble(pose,deadline=cutoff);r=scene['resource'];attempt['directory']=str(r['directory']);attempt['assembly_checks']=r['assembly'].get('assembly_checks',{})
            # Preserve actual geometry even when subsequent local or USB proof rejects.
            if not a.assembly_only:attempt['proof']=runtime.evaluate(scene,deadline=cutoff)
            else:attempt['scope']='actual geometry saved; local/USB/exit proof not attempted'
        except (ValueError,TimeoutError,RuntimeError) as error:attempt['reason']=str(error)
        finally:
            if scene is not None:
                r=scene['resource']
                p.SaveBoard(str(r['directory']/'admission.kicad_pcb'),r['board'])
                (r['directory']/'admission.model.json').write_text(json.dumps(r['cfg'],indent=2))
                runtime.dispose(scene)
            attempt['elapsed_seconds']=time.monotonic()-began;report['attempts'].append(attempt)
            (a.output/'comparison.json').write_text(json.dumps(report,indent=2))
    runtime.verify_baseline()
report['elapsed_seconds']=time.monotonic()-started;report['peak_rss_native_units']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
report['baseline_unchanged']=snapshot(board)==runtime.entry and cfg==runtime.model
(a.output/'comparison.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
