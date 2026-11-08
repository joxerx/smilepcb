"""Real Native completed alternatives, timeout fallback and commit evidence."""
import sys,time,json,math
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.routing_quality import copper_metrics
from smilepcb.keyboard_routing.visual_polish import _parts
from smilepcb.keyboard_routing.cross_layer_polish import extract_spanning_chains
from smilepcb.keyboard_routing.bundle_window import slice_legs
from smilepcb.keyboard_routing.obstacle_bundles import plan_obstacle_group,commit_obstacle_group

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
ns={'__name__':'fixture','__file__':str(Path(__file__))}
code=Path(__file__).with_name('native_joint_bundle_probe.py').read_text().split('result={}')[0]
exec(compile(code,'completed-candidate-fixture','exec'),ns)
results={}
for case in ('best','timeout-after-complete','ineligible'):
    b,_=ns['board'](True)
    for z in list(b.Zones()):b.Delete(z)
    b.GetDesignSettings().m_MinClearance=200000
    keep=p.ZONE(b);keep.SetIsRuleArea(True);keep.SetDoNotAllowTracks(True);keep.SetDoNotAllowVias(True)
    ls=p.LSET();ls.AddLayer(p.F_Cu);ls.AddLayer(p.B_Cu);keep.SetLayerSet(ls);keep.Outline().NewOutline()
    for q in [(8000000,4000000),(12000000,4000000),(12000000,8000000),(8000000,8000000)]:keep.Outline().Append(*q)
    b.Add(keep);before=snapshot(b);parts=_parts(b,time.monotonic()+5)
    rows=extract_spanning_chains(b,eligible_nets={'first','second'},protected_ids=(),deadline=time.monotonic()+5)
    group=[]
    for q in sorted(rows,key=lambda q:q['net']):
        legs=q['old_legs'];total=sum(math.dist(a,z) for _,path in legs for a,z in zip(path,path[1:]))
        group.append(dict(q,window=slice_legs(legs,800000,total-800000),heading=(1,0)))
    calls=[0]
    def score(tracks):
        calls[0]+=1
        if case=='timeout-after-complete' and calls[0]>1:raise TimeoutError('Injected after a complete valid incumbent')
        m=copper_metrics(tracks)
        return -m['length_nm']-m['bends']*500000-m['vias']*1000000
    plan=plan_obstacle_group(b,group,bounds=[0,0,20000000,15000000],preferred_layer=p.F_Cu,
        protected_ids=(),deferred_zone_ids=(),deadline=time.monotonic()+(8 if case=='best' else 4),
        candidate_gate=(lambda a,z:False) if case=='ineligible' else None,candidate_score=score)
    readonly=snapshot(b)==before
    result=commit_obstacle_group(b,plan,protected_ids=(),deadline=time.monotonic()+8) if plan['planned'] else dict(accepted=False)
    after=snapshot(b)
    results[case]=dict(plan=plan,result=result,score_calls=calls[0],readonly=readonly,
        unchanged=before==after,contacts_exact=_parts(b,time.monotonic()+5)==parts,
        footprints_exact=before['footprints']==after['footprints'],final_metrics=copper_metrics(after['tracks']))
    p.SaveBoard(str(out/(case+'.kicad_pcb')),b)
(out/'result.json').write_text(json.dumps(results,indent=2)+'\n')
