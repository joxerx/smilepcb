import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.corridor_native import attempt_corridor
from smilepcb.keyboard_routing.cross_layer_polish import extract_spanning_chains
from smilepcb.keyboard_routing.visual_polish import _parts
from smilepcb.keyboard_routing.local_router import LocalRouter

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
ns={'__name__':'fixture','__file__':str(Path(__file__))}
code=Path(__file__).with_name('native_joint_bundle_probe.py').read_text().split('result={}')[0]
exec(compile(code,'native-corridor-transaction-fixture','exec'),ns)
results={};M=1000000
for case in ('closure','partial-delete','restore-fault'):
    b,_=ns['board'](True)
    for z in list(b.Zones()):b.Delete(z)
    for name,face in [('blockF',p.F_Cu),('blockB',p.B_Cu)]:
        b.Add(p.NETINFO_ITEM(b,name))
        for i,y in enumerate((4*M,11*M)):
            fp=p.FOOTPRINT(b);fp.SetReference(name+str(i));b.Add(fp)
            pad=p.PAD(fp);pad.SetNumber('1');pad.SetPosition(p.VECTOR2I(10*M,y));pad.SetSize(p.VECTOR2I(500000,500000));pad.SetShape(p.PAD_SHAPE_RECT);pad.SetAttribute(p.PAD_ATTRIB_SMD);layers=p.LSET();layers.AddLayer(face);pad.SetLayerSet(layers);pad.SetNet(b.FindNet(name));fp.Add(pad)
        t=p.PCB_TRACK(b);t.SetNet(b.FindNet(name));t.SetLayer(face);t.SetWidth(150000);t.SetStart(p.VECTOR2I(10*M,4*M));t.SetEnd(p.VECTOR2I(10*M,11*M));b.Add(t)
    p.SaveBoard(str(out/(case+'-input.kicad_pcb')),b);b=p.LoadBoard(str(out/(case+'-input.kicad_pcb')))
    b.GetDesignSettings().m_MinClearance=150000;b.GetDesignSettings().m_TrackMinWidth=150000
    source_oracle=LocalRouter(b,compact=True,track_width_nm=150000,deadline=time.monotonic()+5)
    for name,face in [('blockF',p.F_Cu),('blockB',p.B_Cu)]:
        assert source_oracle.clear(p.SHAPE_CIRCLE(p.VECTOR2I(10*M,4*M),75000),face,name), 'Fixture source must be clear of foreign copper'
    before=snapshot(b);parts=_parts(b,time.monotonic()+5)
    pool=extract_spanning_chains(b,eligible_nets={'first','second','blockF','blockB'},protected_ids=(),deadline=time.monotonic()+5)
    group=sorted([q for q in pool if q['net'] in {'first','second'}],key=lambda q:q['net'])
    paths=[[(2*M,5*M),(18*M,5*M)],[(2*M,6*M),(18*M,6*M)]]
    original_delete=p.BOARD.Delete;original_index=LocalRouter.index;count=[0]
    if case=='partial-delete':
        def fail_delete(self,item):
            if item.GetClass()=='PCB_TRACK' and item.m_Uuid.AsString() in before['tracks']:
                count[0]+=1
                if count[0]==2:raise RuntimeError('Injected second original Delete')
            return original_delete(self,item)
        p.BOARD.Delete=fail_delete
    if case=='restore-fault':
        def fail_index(self,item):
            if item.GetClass()=='PCB_TRACK' and item.GetNetname()=='blockB' and item.m_Uuid.AsString() not in before['tracks']:raise RuntimeError('Injected blocker restore Add')
            return original_index(self,item)
        LocalRouter.index=fail_index
    try:
        result=attempt_corridor(b,group,paths,preferred_layer=p.F_Cu,protected_ids=(),deadline=time.monotonic()+12,deferred_zone_ids=(),blocker_pool=pool,accept=lambda a,z,r,x:_parts(b,time.monotonic()+3)==parts)
    except Exception as error:result={'fault':type(error).__name__+': '+str(error)}
    finally:p.BOARD.Delete=original_delete;LocalRouter.index=original_index
    after=snapshot(b)
    results[case]=dict(result=result,unchanged=before==after,contacts_exact=_parts(b,time.monotonic()+5)==parts,footprints_exact=before['footprints']==after['footprints'])
(out/'result.json').write_text(json.dumps(results,indent=2)+'\n')
