"""Native alternative-contact regression on synthetic copper only."""
import json,sys,time,types
from copy import deepcopy
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.collector_capacity import DIRS
from smilepcb.keyboard_routing import collector_peel as module
from smilepcb.keyboard_routing.interface_launches import reserve_launches
from smilepcb.keyboard_routing.collectors import route_collectors


control=len(sys.argv)>2
if control:
    # Optional exact pinned source control supplied by the experiment runner;
    # never replace the installed runtime or the active worktree source.
    baseline=Path(sys.argv[2]);package='smilepcb.keyboard_routing'
    capacity=types.ModuleType(package+'._goals_capacity_control');capacity.__package__=package
    sys.modules[capacity.__name__]=capacity
    exec(compile((baseline/'collector_capacity.py').read_text(),str(baseline/'collector_capacity.py'),'exec'),capacity.__dict__)
    old=types.ModuleType(package+'._goals_peel_control');old.__package__=package
    sys.modules[old.__name__]=old
    code=(baseline/'collector_peel.py').read_text().replace('from .collector_capacity import Search,heading','from ._goals_capacity_control import Search,heading')
    exec(compile(code,str(baseline/'collector_peel.py'),'exec'),old.__dict__);module=old


def fixture(face,shift=(0,0),wall=True):
    b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings()
    r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_MinThroughDrill=300000;r.m_ViasMinSize=600000;r.m_ViasMinAnnularWidth=150000;r.m_CopperEdgeClearance=200000
    other=p.B_Cu if face==p.F_Cu else p.F_Cu
    def v(xy):return p.VECTOR2I(*(round((xy[i]+shift[i])*1e6) for i in (0,1)))
    def xy(a):q=v(a);return (q.x,q.y)
    def trace(net,points,layer,width=150000):
        ids=[]
        for a,z in zip(points,points[1:]):
            t=p.PCB_TRACK(b);t.SetStart(v(a));t.SetEnd(v(z));t.SetWidth(width);t.SetLayer(layer);t.SetNet(b.FindNet(net));b.Add(t);ids.append(t.m_Uuid.AsString())
        return ids
    for net in ('outer','inner','wall'):b.Add(p.NETINFO_ITEM(b,net))
    corners=[(0,0),(40,0),(40,40),(0,40)]
    for a,z in zip(corners,corners[1:]+corners[:1]):
        e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(v(a));e.SetEnd(v(z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
    chip=p.FOOTPRINT(b);chip.SetReference('U_MCU');chip.SetPosition(v((3,25)));b.Add(chip)
    def pad(fp,number,net,position,layer):
        d=p.PAD(fp);d.SetNumber(number);d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(300000,150000));d.SetPosition(v(position));d.SetNet(b.FindNet(net));ls=p.LSET();ls.AddLayer(layer);d.SetLayerSet(ls);fp.Add(d);return d
    ports=[]
    for i,(net,y) in enumerate((('outer',25),('inner',25.35))):
        d=pad(chip,str(i+1),net,(4,y),face);ids=trace(net,[(4,y),(5,y)],face)
        ports.append({'net':net,'kind':'same_layer','pad_ids':[d.m_Uuid.AsString()],'added_ids':ids,'terminal_id':ids[-1],'via_id':None,'via_nm':None,'exit_nm':xy((5,y)),'heading_origin_nm':xy((4,y)),'exit_layer':face,'collector_heading':[1,0]})
    f=p.FOOTPRINT(b);f.SetReference('FIELD');b.Add(f)
    a=pad(f,'1','outer',(17,21),other);z=pad(f,'2','outer',(23,21),other)
    trace('outer',[(17,21),(17,11),(23,5),(23,21)],other)
    pad(f,'3','inner',(30,30),face)
    if wall:
        trace('wall',[(15,15),(15,23),(19,23),(19,15)],other,200000)
    goals=[('outer',module.FieldTarget(d,d,xy(pt)),-1,(1,0)) for d,pt in ((a,(17,21)),(z,(23,21)))]
    return b,ports,goals


def search_for(b,ports,goals,deadline,override_goals=None):
    first=goals[0]
    kwargs={'deadline':deadline,'target':first[1],'leaf_net':first[0],'side':first[2],'goal_heading':first[3]}
    if hasattr(module,'BranchGoal'):
        kwargs['goals']=[module.BranchGoal(net,(target,),side,heading, max(1000000,sum((target.point[i]-ports[0]['exit_nm'][i])*heading[i] for i in (0,1))-1000000)) for net,target,side,heading in goals]
    if override_goals is not None:kwargs['goals']=override_goals
    search=module.CollectorSearch(b,ports,['outer','inner'],{'outer':first[1].point,'inner':first[1].point},**kwargs)
    native_edge=search.edge_clear
    # The test certifies only a straight physical spine; all accepted edges
    # still pass the real native finite-width collision admission.
    search.edge_clear=lambda state,new,turned:new[1]==0 and new[0]>=0 and new[2]==0 and native_edge(state,new,turned)
    return search


def connected(b):
    g=copper_graph(b,{'outer'},time.monotonic()+3)
    return len({g['find'](i) for i,n in enumerate(g['nodes']) if isinstance(n['item'],p.PAD) and n['net']=='outer'})==1


records={}
with generation_profile('dsn-safe-v1'):
    for face,shift in ((p.B_Cu,(0,0)),(p.F_Cu,(2,3))):
        b,ports,goals=fixture(face,shift);before=snapshot(b);original=deepcopy(ports)
        search=search_for(b,ports,goals,time.monotonic()+8);rollback=[];attempts=[];commit=search.commit
        def checked(edges):
            attempts.append(list(search.target_point));answer=commit(edges)
            if answer[0] is None:rollback.append(snapshot(b)==before and ports==original and search.branch is None and search.interface_graph is None)
            return answer
        search.commit=checked
        with reserve_launches(ports):result=search.solve()
        after=snapshot(b)
        records['wall-'+str(face)]={'result':result,'branch':search.branch,'attempts':attempts,'rollback_exact':rollback,'connected':connected(b),'source_exact':after['footprints']==before['footprints'] and all(after['tracks'].get(u)==t for u,t in before['tracks'].items()),'ports_exact':ports==original}
    if control:
        Path(sys.argv[1]).write_text(json.dumps(records,indent=2)+'\n');sys.exit(0)
    b,ports,goals=fixture(p.B_Cu);b.Add(p.NETINFO_ITEM(b,'fixed'))
    chip=next(f for f in b.GetFootprints() if f.GetReference()=='U_MCU')
    d=p.PAD(chip);d.SetNumber('3');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(300000,150000));d.SetPosition(p.VECTOR2I(13000000,25175000));d.SetNet(b.FindNet('fixed'));ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls);chip.Add(d)
    t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(13000000,25175000));t.SetEnd(p.VECTOR2I(12000000,25175000));t.SetLayer(p.B_Cu);t.SetWidth(150000);t.SetNet(b.FindNet('fixed'));b.Add(t)
    fixed={'net':'fixed','kind':'same_layer','pad_ids':[d.m_Uuid.AsString()],'added_ids':[t.m_Uuid.AsString()],'terminal_id':t.m_Uuid.AsString(),'via_id':None,'via_nm':None,'exit_nm':(12000000,25175000),'heading_origin_nm':(13000000,25175000),'exit_layer':p.B_Cu}
    before=snapshot(b)
    # Same real copper in both cases: only the unrelated live interface's
    # virtual departure differs. No native collision is bypassed.
    with reserve_launches(ports):
        clear=search_for(b,ports,goals,time.monotonic()+3)
        unguarded=clear.edge_clear((12,0,0,p.B_Cu,1),(13,0,0,p.B_Cu,1),False)
    with reserve_launches(ports+[fixed]):
        guarded=search_for(b,ports,goals,time.monotonic()+3)
        blocked=not guarded.edge_clear((12,0,0,p.B_Cu,1),(13,0,0,p.B_Cu,1),False)
    records['scope']={'cohort_axis_clear':unguarded,'fixed_axis_blocked':blocked,'blockers':guarded.blockers.most_common(),'exact':snapshot(b)==before}
    b,ports,goals=fixture(p.B_Cu);field=next(f for f in b.GetFootprints() if f.GetReference()=='FIELD')
    through=p.PAD(field);through.SetNumber('4');through.SetAttribute(p.PAD_ATTRIB_PTH);through.SetShape(p.PAD_SHAPE_CIRCLE);through.SetSize(p.VECTOR2I(900000,900000));through.SetDrillSize(p.VECTOR2I(300000,300000));through.SetPosition(p.VECTOR2I(17000000,21000000));through.SetLayerSet(p.LSET.AllCuMask());through.SetNet(b.FindNet('outer'));field.Add(through)
    goal=module.BranchGoal('outer',(goals[0][1],module.FieldTarget(through,through,(17000000,21000000))),-1,(1,0),11000000)
    before=snapshot(b);search=search_for(b,ports,goals,time.monotonic()+8,override_goals=[goal])
    with reserve_launches(ports):result=search.solve()
    records['faces']={'result':result,'branch':search.branch,'expected_contact_id':through.m_Uuid.AsString(),'connected':connected(b),'source_exact':all(snapshot(b)['tracks'].get(u)==t for u,t in before['tracks'].items())}
    b,ports,goals=fixture(p.B_Cu,wall=False);before=snapshot(b);original=deepcopy(ports);search=search_for(b,ports,goals,time.monotonic()+8)
    real_graph=module.copper_graph;injected=[];rollback=[];commit=search.commit
    def fail_once(*args,**kwargs):
        if not injected:
            injected.append(len(snapshot(b)['tracks'])>len(before['tracks']));raise ValueError('Injected rejection after native branch mutation')
        return real_graph(*args,**kwargs)
    def checked(edges):
        answer=commit(edges)
        if answer[0] is None:rollback.append(snapshot(b)==before and ports==original and search.branch is None and search.interface_graph is None)
        return answer
    module.copper_graph=fail_once;search.commit=checked
    try:
        with reserve_launches(ports):result=search.solve()
    finally:module.copper_graph=real_graph
    records['atomic']={'result':result,'injected_after_mutation':injected,'rollback_exact':rollback,'connected':connected(b),'ports_exact':ports==original}
    b,ports,goals=fixture(p.B_Cu,wall=False);before=snapshot(b);search=search_for(b,ports,goals,time.monotonic()+.5)
    proof_mutation=[];real_graph=module.copper_graph
    def expire_during_proof(*args,**kwargs):
        proof_mutation.append(len(snapshot(b)['tracks'])>len(before['tracks']))
        deadline=args[2];time.sleep(max(0,deadline-time.monotonic())+.002)
        return real_graph(*args,**kwargs)
    module.copper_graph=expire_during_proof
    try:
        with reserve_launches(ports):result=search.solve()
    finally:module.copper_graph=real_graph
    records['mid-expired']={'result':result,'proof_after_mutation':proof_mutation,'exact':snapshot(b)==before,'branch_clear':search.branch is None and search.interface_graph is None}
    b,ports,goals=fixture(p.B_Cu)
    for f in b.GetFootprints():
        if f.GetReference()=='FIELD':
            for d in f.Pads():
                if d.GetNetname()=='inner':d.SetPosition(p.VECTOR2I(20000000,27000000))
    before=snapshot(b)
    cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'outer','2':'inner'}}],
         'layout':{'gpio':{'rows':[],'cols':['A0','A1']}},'mcu_gpio_pads':{'A0':'1','A1':'2'}}
    source_ids={u for q in ports for u in q['added_ids']}
    field_ids=[t.m_Uuid.AsString() for t in b.GetTracks() if t.GetNetname()=='outer' and t.m_Uuid.AsString() not in source_ids]
    with reserve_launches(ports):result=route_collectors(b,cfg,ports,deadline=time.monotonic()+20,field_owned_ids=field_ids)
    after=snapshot(b)
    records['integration']={'result':result,'connected':connected(b),'source_exact':after['footprints']==before['footprints'] and all(after['tracks'].get(u)==t for u,t in before['tracks'].items())}
    b,ports,goals=fixture(p.B_Cu);before=snapshot(b);search=search_for(b,ports,goals,time.monotonic()-1)
    with reserve_launches(ports):result=search.solve()
    records['expired']={'result':result,'exact':snapshot(b)==before}
Path(sys.argv[1]).write_text(json.dumps(records,indent=2)+'\n')
