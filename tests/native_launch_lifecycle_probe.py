"""Native source interfaces retire and reopen with actual pad connectivity."""
import json,sys,time
from copy import deepcopy
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.interface_launches import reserve_launches
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.port_policy import PortPolicy
from smilepcb.keyboard_routing.native_graph import route_components


def fixture(alias=False):
    b=p.BOARD();rules=b.GetDesignSettings();rules.m_MinClearance=200000;rules.m_TrackMinWidth=150000
    for net in ('signal','fixed','foreign'):b.Add(p.NETINFO_ITEM(b,net))
    for a,z in (((0,0),(40000000,0)),((40000000,0),(40000000,30000000)),((40000000,30000000),(0,30000000)),((0,30000000),(0,0))):
        e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
    def pad(ref,net,point):
        f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(vec(point));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec((300000,300000)));ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet(net));f.Add(d);return d
    source=pad('U_MCU','signal',(5000000,5000000));external=pad('FIELD','signal',(15000000,9000000))
    fixed=pad('U_FIXED','fixed',(20000000,5000000));pad('FIXED_FIELD','fixed',(30000000,9000000))
    source_ids=[source.m_Uuid.AsString()]
    if alias:source_ids.append(pad('U_ALIAS','signal',(5000000,7000000)).m_Uuid.AsString())
    def track(net,a,z):
        t=p.PCB_TRACK(b);t.SetStart(vec(a));t.SetEnd(vec(z));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet(net));b.Add(t);return t
    lead=track('signal',(5000000,5000000),(10000000,5000000));other=track('fixed',(20000000,5000000),(25000000,5000000))
    def port(net,ids,terminal,origin,tip):return {'net':net,'pad_ids':ids,'added_ids':[terminal.m_Uuid.AsString()],'terminal_id':terminal.m_Uuid.AsString(),'kind':'same_layer','exit_nm':tip,'heading_origin_nm':origin,'exit_layer':p.F_Cu}
    ports=[port('signal',source_ids,lead,(5000000,5000000),(10000000,5000000)),port('fixed',[fixed.m_Uuid.AsString()],other,(20000000,5000000),(25000000,5000000))]
    return b,ports,track


def crossing(x,y=5000000):return p.SHAPE_SEGMENT(vec((x,y-400000)),vec((x,y+400000)),150000)


def settle(board,router,net):
    router.deadline=time.monotonic()+5
    return route_components(board,router,{net})


report={}
b,ports,track=fixture();continuation=[track('signal',(10000000,5000000),(10250000,5000000)),track('signal',(10250000,5000000),(10250000,9000000)),track('signal',(10250000,9000000),(15000000,9000000))];before=snapshot(b)
with reserve_launches(ports):
    router=LocalRouter(b,compact=True,track_width_nm=150000);router.port_policy=PortPolicy(b,[ports[0]])
    before_reserved=not router.clear(crossing(10700000),p.F_Cu,'foreign')
    settle(b,router,'signal')
    report['completed']={'reserved_before_proof':before_reserved,'foreign_corridor_available':router.clear(crossing(10700000),p.F_Cu,'foreign'),'fresh_planner_available':LocalRouter(b,compact=True,track_width_nm=150000).clear(crossing(10700000),p.F_Cu,'foreign'),'fixed_interface_retained':not router.clear(crossing(25700000),p.F_Cu,'foreign'),'source_exact':snapshot(b)==before}
    for item in continuation:b.Delete(item)
    # Read the exact reopened graph without allowing a repair to rejoin it.
    from smilepcb.keyboard_routing.native_graph import copper_graph
    graph=copper_graph(b,{'signal'},time.monotonic()+5)
    if hasattr(router,'refresh_interfaces'):router.refresh_interfaces(graph)
    report['reopened']={'guard_reactivated':not router.clear(crossing(10700000),p.F_Cu,'foreign'),'fresh_guard_reactivated':not LocalRouter(b,compact=True,track_width_nm=150000).clear(crossing(10700000),p.F_Cu,'foreign')}

b,ports,track=fixture();a=track('signal',(10000000,5000000),(10250000,5000000));z=track('signal',(10250000,5000000),(10250000,8000000));advanced=deepcopy(ports[0]);advanced['added_ids'].extend([a.m_Uuid.AsString(),z.m_Uuid.AsString()]);advanced.update(terminal_id=z.m_Uuid.AsString(),exit_nm=(10250000,8000000),heading_origin_nm=(10250000,5000000));before=snapshot(b)
with reserve_launches(ports):
    router=LocalRouter(b,compact=True,track_width_nm=150000);router.port_policy=PortPolicy(b,[advanced])
    launch=p.SHAPE_SEGMENT(vec((9850000,8700000)),vec((10650000,8700000)),150000)
    report['advanced']={'old_virtual_corridor_available':router.clear(crossing(10700000),p.F_Cu,'foreign'),'new_departure_retained':not router.clear(launch,p.F_Cu,'foreign'),'fixed_interface_retained':not router.clear(crossing(25700000),p.F_Cu,'foreign'),'source_exact':snapshot(b)==before}
    b.Delete(a);b.Delete(z)
    restored=LocalRouter(b,compact=True,track_width_nm=150000)
    report['speculative-rollback']={'original_departure_retained':not restored.clear(crossing(10700000),p.F_Cu,'foreign'),'discarded_departure_available':restored.clear(launch,p.F_Cu,'foreign')}

b,ports,track=fixture(alias=True);track('signal',(10000000,5000000),(10250000,5000000));track('signal',(10250000,5000000),(10250000,9000000));track('signal',(10250000,9000000),(15000000,9000000))
with reserve_launches(ports):
    router=LocalRouter(b,compact=True,track_width_nm=150000);router.port_policy=PortPolicy(b,[ports[0]])
    graph=copper_graph(b,{'signal'},time.monotonic()+5)
    if hasattr(router,'refresh_interfaces'):router.refresh_interfaces(graph)
    report['alias']={'disconnected_alias_retains_guard':not router.clear(crossing(10700000),p.F_Cu,'foreign')}
b,ports,track=fixture();before=snapshot(b)
with reserve_launches(ports):
    router=LocalRouter(b,compact=True,track_width_nm=150000);router.port_policy=PortPolicy(b,[ports[0]])
    count=settle(b,router,'signal');after=snapshot(b)
    report['new-connection']={'native_connection_added':count==1,'virtual_departure_retired':not router.port_policy._launch_conflict(crossing(10700000),p.F_Cu,'foreign'),'source_branchable_after_join':router.port_policy.accepts_segment((4900000,5000000),(5100000,5000000),p.F_Cu,'signal',150000),'original_source_exact':all(after['tracks'].get(uid)==item for uid,item in before['tracks'].items())}
b,ports,track=fixture(alias=True)
for fp in list(b.GetFootprints()):
    if fp.GetReference()=='FIELD':b.Delete(fp)
track('signal',(5000000,5000000),(5000000,7000000))
with reserve_launches(ports):
    router=LocalRouter(b,compact=True,track_width_nm=150000);router.port_policy=PortPolicy(b,[ports[0]])
    settle(b,router,'signal')
    report['source-only']={'connected_source_aliases_retain_guard':not router.clear(crossing(10700000),p.F_Cu,'foreign')}

from native_collector_flow_fixture import fixture as matrix_fixture
from smilepcb.keyboard_routing.ordered_controller import prepare_ordered_controller
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing import matrix_polish
b,cfg=matrix_fixture(p.B_Cu);old_polish=matrix_polish.polish_matrix;observed=[]
def inspect_polish(board,*args,**kwargs):
    world=LocalRouter(board,compact=True,track_width_nm=150000)
    active=world.launch_policy.live_ports if world.launch_policy is not None else []
    observed.append(not any(q['net'] in {'r0','r1','c0','c1'} for q in active))
    return old_polish(board,*args,**kwargs)
matrix_polish.polish_matrix=inspect_polish
try:
    with generation_profile('dsn-safe-v1'):
        _,result=prepare_ordered_controller(b,cfg,deadline=time.monotonic()+35,complete_matrix=True)
finally:matrix_polish.polish_matrix=old_polish
report['refinement']={'full_matrix_accepted':result['accepted'],'matrix_launches_retired_before_refinement':bool(observed) and all(observed)}
Path(sys.argv[1]).write_text(json.dumps(report,indent=2)+'\n')
