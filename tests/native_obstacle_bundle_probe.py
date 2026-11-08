import sys,time,json,math
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.visual_polish import _parts
from smilepcb.keyboard_routing.cross_layer_polish import extract_spanning_chains
from smilepcb.keyboard_routing.bundle_window import slice_legs
from smilepcb.keyboard_routing.obstacle_bundles import plan_obstacle_group,commit_obstacle_group

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
ns={'__name__':'fixture','__file__':str(Path(__file__))};code=Path(__file__).with_name('native_joint_bundle_probe.py').read_text().split('result={}')[0];exec(compile(code,'obstacle-bundle-fixture','exec'),ns)
results={}
for case in ('real-detour','partial-delete','quality-veto'):
    b,_=ns['board'](True)
    for z in list(b.Zones()):b.Delete(z)
    b.GetDesignSettings().m_MinClearance=200000
    keep=p.ZONE(b);keep.SetIsRuleArea(True);keep.SetDoNotAllowTracks(True);keep.SetDoNotAllowVias(True);ls=p.LSET();ls.AddLayer(p.F_Cu);ls.AddLayer(p.B_Cu);keep.SetLayerSet(ls);keep.Outline().NewOutline()
    for q in [(8000000,4000000),(12000000,4000000),(12000000,8000000),(8000000,8000000)]:keep.Outline().Append(*q)
    b.Add(keep);before=snapshot(b);parts=_parts(b,time.monotonic()+5)
    rows=extract_spanning_chains(b,eligible_nets={'first','second'},protected_ids=(),deadline=time.monotonic()+5)
    group=[]
    for q in sorted(rows,key=lambda q:q['net']):
        legs=q['old_legs'];total=sum(math.dist(a,z) for _,path in legs for a,z in zip(path,path[1:]));window=slice_legs(legs,800000,total-800000)
        group.append(dict(q,window=window,heading=(1,0)))
    plan=plan_obstacle_group(b,group,bounds=[0,0,20000000,15000000],preferred_layer=p.F_Cu,protected_ids=(),deferred_zone_ids=(),deadline=time.monotonic()+15)
    readonly=snapshot(b)==before;original_delete=p.BOARD.Delete;count=[0]
    if case=='partial-delete':
        def fail(self,item):
            if item.GetClass()=='PCB_TRACK' and item.m_Uuid.AsString() in before['tracks']:
                count[0]+=1
                if count[0]==2:raise RuntimeError('Injected commit partial deletion')
            return original_delete(self,item)
        p.BOARD.Delete=fail
    try:
        if plan.get('planned'):result=commit_obstacle_group(b,plan,protected_ids=(),deadline=time.monotonic()+8,quality_gate=(lambda a,z:False) if case=='quality-veto' else None)
        else:result=dict(accepted=False,reason='No complete plan')
    except Exception as error:result={'fault':type(error).__name__+': '+str(error)}
    finally:p.BOARD.Delete=original_delete
    after=snapshot(b);p.SaveBoard(str(out/(case+'.kicad_pcb')),b)
    results[case]=dict(plan=plan,result=result,readonly=readonly,unchanged=before==after,contacts_exact=_parts(b,time.monotonic()+5)==parts,footprints_exact=before['footprints']==after['footprints'])
(out/'result.json').write_text(json.dumps(results,indent=2)+'\n')
