"""Real native graph expiry at candidate and emission boundaries."""
import json,sys,time
from pathlib import Path
from types import SimpleNamespace
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.interface_launches import reserve_launches,interface_identity
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing import native_graph


def fixture(face):
    b=p.BOARD();b.SetCopperLayerCount(2);rules=b.GetDesignSettings()
    rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000;rules.m_HoleClearance=250000
    rules.m_MinThroughDrill=200000;rules.m_ViasMinSize=500000;rules.m_ViasMinAnnularWidth=150000;rules.m_CopperEdgeClearance=250000
    b.Add(p.NETINFO_ITEM(b,'control'))
    vertices=[(0,0),(20000000,0),(20000000,15000000),(0,15000000)]
    for a,z in zip(vertices,vertices[1:]+vertices[:1]):
        e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
    pads=[]
    for ref,at in [('SOURCE',(5000000,5000000)),('FIELD',(15000000,5000000))]:
        f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetNet(b.FindNet('control'));d.SetPosition(vec(at));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec((300000,300000)));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);f.Add(d);pads.append(d)
    def track(a,z):
        t=p.PCB_TRACK(b);t.SetStart(vec(a));t.SetEnd(vec(z));t.SetWidth(150000);t.SetLayer(face);t.SetNet(b.FindNet('control'));b.Add(t);return t
    head=track((5000000,5000000),(8000000,5000000));carrier=track((8000000,5000000),(15000000,5000000))
    port={'net':'control','pad_ids':[pads[0].m_Uuid.AsString()],'added_ids':[head.m_Uuid.AsString()],'terminal_id':head.m_Uuid.AsString(),'kind':'same_layer','exit_nm':[8000000,5000000],'heading_origin_nm':[5000000,5000000],'exit_layer':face}
    return b,port,head,carrier,pads


def probe(face,method,kind):
    b,q,head,carrier,pads=fixture(face);clock=SimpleNamespace(now=99);calls=[];error=None;answer=None
    real_time=time.monotonic;real_graph=native_graph.copper_graph
    def graph(board,nets,deadline):
        calls.append(deadline)
        if kind=='during':clock.now=100
        result=real_graph(board,nets,deadline)
        if kind=='late':clock.now=100
        return result
    time.monotonic=lambda:clock.now
    try:
        with reserve_launches([q],resolved=[interface_identity(q)]):
            router=LocalRouter(b,deadline=100,compact=True,track_width_nm=150000)
            if kind=='invariant':head.SetEnd(vec((7800000,5000000)));b.Delete(carrier)
            before=snapshot(b);native_graph.copper_graph=graph
            if kind=='pre':clock.now=100
            try:
                if method=='leg':answer=router.leg((8000000,5000000),(9000000,5000000),face,'control')
                elif method=='via_clear':answer=router.via_clear((10000000,6500000),'control')
                elif method=='connect':answer=router.connect(*pads)
                elif method=='add_path':router.add_path([(8000000,5000000),(9000000,5000000)],face,'control')
                else:router.add_via((10000000,6500000),'control')
            except (TimeoutError,ValueError) as exc:error=type(exc).__name__
            after=snapshot(b)
            candidate=method in ('leg','via_clear','connect')
            expected_error='ValueError' if kind=='invariant' else None if candidate else 'TimeoutError'
            expected_answer=(answer is None if method=='leg' else answer is False) if kind!='healthy' else bool(answer)
            return {'error_contract':error==expected_error,'candidate_contract':not candidate or kind=='invariant' or expected_answer,
                    'source_exact':after==before,'no_emission':not router.added,'deadline_exact':router.deadline==100,
                    'preexpired_skips_graph':kind!='pre' or not calls,'graph_calls':len(calls),'error':error}
    finally:native_graph.copper_graph=real_graph;time.monotonic=real_time


def spoke_probe(face,expired):
    from smilepcb.keyboard_routing.via_spoke import rehome_spoke
    from smilepcb.keyboard_routing.completion import pad_partitions
    b,q,head,carrier,pads=fixture(face);other=p.B_Cu if face==p.F_Cu else p.F_Cu
    f=p.FOOTPRINT(b);f.SetReference('AUX');b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetNet(b.FindNet('control'));d.SetPosition(vec((5000000,10000000)));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec((300000,300000)));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);f.Add(d)
    def track(a,z,layer):
        t=p.PCB_TRACK(b);t.SetStart(vec(a));t.SetEnd(vec(z));t.SetWidth(150000);t.SetLayer(layer);t.SetNet(b.FindNet('control'));b.Add(t);return t
    def via(at):
        v=p.PCB_VIA(b);v.SetPosition(vec(at));v.SetWidth(500000);v.SetDrill(200000);v.SetViaType(p.VIATYPE_THROUGH);v.SetLayerPair(p.F_Cu,p.B_Cu);v.SetNet(b.FindNet('control'));b.Add(v);return v
    source_via=via((8000000,5000000));old=via((9000000,10000000));track((5000000,10000000),(9000000,10000000),face)
    track((9000000,10000000),(8000000,10000000),other);track((8000000,10000000),(8000000,5000000),other)
    before=snapshot(b);parts={frozenset(g) for g in pad_partitions(b,time.monotonic()+5)}
    clock=SimpleNamespace(now=99);real_time=time.monotonic;real_graph=native_graph.copper_graph;calls=[];error=None;move=None
    def graph(board,nets,deadline):
        calls.append(deadline)
        if expired:clock.now=100
        return real_graph(board,nets,deadline)
    time.monotonic=lambda:clock.now;native_graph.copper_graph=graph
    try:
        with reserve_launches([q],resolved=[interface_identity(q)]):
            try:move=rehome_spoke(b,old,face,protected={head.m_Uuid.AsString(),source_via.m_Uuid.AsString()},deadline=100)
            except (TimeoutError,ValueError) as exc:error=type(exc).__name__
    finally:time.monotonic=real_time;native_graph.copper_graph=real_graph
    after=snapshot(b)
    return {'expected_result':error is None and ((move is None) if expired else (move is not None)),
            'source_exact':after==before if expired else after['tracks'].get(head.m_Uuid.AsString())==before['tracks'][head.m_Uuid.AsString()],
            'native_partitions_exact':parts=={frozenset(g) for g in pad_partitions(b,time.monotonic()+5)},
            'deadline_not_renewed':all(deadline==100 for deadline in calls),'error':error}


if __name__=='__main__':
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={}
    with generation_profile('dsn-process-v2'):
        for label,face in [('front',p.F_Cu),('back',p.B_Cu)]:
            values={}
            for method in ('leg','via_clear','add_path','add_via'):
                for kind in ('pre','during','late','invariant'):
                    values[method+'-'+kind]=probe(face,method,kind)
            for kind in ('pre','during','invariant'):values['connect-'+kind]=probe(face,'connect',kind)
            for method in ('leg','via_clear'):values[method+'-healthy']=probe(face,method,'healthy')
            report[label]=values
        report['spokes']={label:{'expired':spoke_probe(face,True),'healthy':spoke_probe(face,False)} for label,face in [('front',p.F_Cu),('back',p.B_Cu)]}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
