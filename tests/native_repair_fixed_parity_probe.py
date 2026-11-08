"""A closed immutable fixed interface must agree in all negotiation oracles."""
import json,sys,time
from copy import deepcopy
from pathlib import Path
from native_provisional_collector_fixture import build_native
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,apply_native_rules
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.feeder_repair import repair_feeders,pending_matrix
from smilepcb.keyboard_routing import native_graph

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);board,fixture=build_native(p)
for a,z in zip(((24000000,8000000),(24000000,8250000),(25000000,8250000)),((24000000,8250000),(25000000,8250000),(25000000,16000000))):
    t=p.PCB_TRACK(board);t.SetStart(p.VECTOR2I(*a));t.SetEnd(p.VECTOR2I(*z));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetNet(board.FindNet('fixed'));board.Add(t)
path=out/'closed-fixed.kicad_pcb';path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')));assert p.SaveBoard(str(path),board);board=p.LoadBoard(str(path))
assert {t.GetOwnClearance(p.F_Cu) for t in board.GetTracks() if t.IsOnLayer(p.F_Cu)}=={150000}
apply_native_rules(board,'fr4-2l-1oz-0p5-0p2');before=snapshot(board);records={};checks=[];worlds=[]
shape=p.SHAPE_SEGMENT(p.VECTOR2I(23600000,8700000),p.VECTOR2I(24400000,8700000),150000)
old_negotiate,old_route=native_graph.negotiate_routes,native_graph.route_components

def negotiated(candidate,nets,protected,deadline,**kwargs):
    full=kwargs['router_factory'](candidate,deadline=deadline,compact=True)
    worlds.append(full);checks.append({'full_before_route_clear':full.clear(shape,p.F_Cu,'inner')})
    return old_negotiate(candidate,nets,protected,deadline,**kwargs)

def routed(candidate,router,*args,**kwargs):
    value=old_route(candidate,router,*args,**kwargs)
    if checks and 'relaxed_after_native_refresh_clear' not in checks[-1]:
        checks[-1].update(relaxed_after_native_refresh_clear=router.clear(shape,p.F_Cu,'inner'),lifecycle_equal_after_native_join=worlds[-1].port_policy.resolved==router.port_policy.resolved)
    return value

with generation_profile('dsn-safe-v1'),using_quality_profile('turns'):
    with reserve_launches([]):records['native_corridor_clear']=LocalRouter(board,compact=True,track_width_nm=150000).clear(shape,p.F_Cu,'inner')
    records['fixed_native_complete']=not pending_matrix(board,{'fixed'},time.monotonic()+5)
    native_graph.negotiate_routes,native_graph.route_components=negotiated,routed
    try:
        with reserve_launches(fixture['current_ports']):history=repair_feeders(board,{'outer_left','inner','outer_right'},set(fixture['immutable_ids']),deadline=time.monotonic()+40,ports=fixture['current_ports'],source_ports=fixture['source_ports'])
    finally:native_graph.negotiate_routes,native_graph.route_components=old_negotiate,old_route
records.update(checks=checks,history=history,fixed_copper_exact=all(snapshot(board)['tracks'].get(u)==t for u,t in before['tracks'].items() if t['net']=='fixed'))
(out/'report.json').write_text(json.dumps(records,indent=2)+'\n')
