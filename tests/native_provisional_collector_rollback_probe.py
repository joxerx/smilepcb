"""Fault injection after real production negotiation; incumbent must be exact."""
import json,sys,time
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
from native_provisional_collector_fixture import build_native
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,apply_native_rules
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.keyboard_routing import native_graph,feeder_repair
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launches,current_launch_inventory,resolved_interfaces
from smilepcb.keyboard_routing.feeder_repair import repair_feeders,pending_matrix

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);records={}
nets={'outer_left','inner','outer_right'}
old_negotiate,old_route=native_graph.negotiate_routes,native_graph.route_components
old_graph_clock,old_repair_clock=native_graph.time,feeder_repair.time


def make(name):
    board,fixture=build_native(p);path=out/(name+'.kicad_pcb')
    path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')))
    assert p.SaveBoard(str(path),board);board=p.LoadBoard(str(path))
    own={t.GetOwnClearance(layer) for t in [*board.GetTracks(),*(d for fp in board.GetFootprints() for d in fp.Pads())] for layer in (p.F_Cu,p.B_Cu) if t.IsOnLayer(layer)}
    assert own=={150000},own
    apply_native_rules(board,'fr4-2l-1oz-0p5-0p2')
    return board,fixture


def extra_source(board,fixture,net='extra',x=22000000):
    board.Add(p.NETINFO_ITEM(board,net));pads=[]
    for ref,xy in ((net+'_SOURCE',(x,4000000)),(net+'_FIELD',(x,11000000))):
        fp=p.FOOTPRINT(board);fp.SetReference(ref);board.Add(fp);d=p.PAD(fp);d.SetNumber('1');d.SetPosition(p.VECTOR2I(*xy));d.SetSize(p.VECTOR2I(150000,300000));d.SetShape(p.PAD_SHAPE_RECT);d.SetAttribute(p.PAD_ATTRIB_SMD);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(board.FindNet(net));fp.Add(d);pads.append(d.m_Uuid.AsString())
    t=p.PCB_TRACK(board);t.SetStart(p.VECTOR2I(x,4000000));t.SetEnd(p.VECTOR2I(x,6000000));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetNet(board.FindNet(net));board.Add(t);uid=t.m_Uuid.AsString()
    port={'net':net,'pad_ids':[pads[0]],'added_ids':[uid],'terminal_id':uid,'kind':'same_layer','exit_nm':[x,6000000],'heading_origin_nm':[x,4000000],'exit_layer':p.F_Cu}
    fixture['source_ports'].append(deepcopy(port));fixture['current_ports'].append(deepcopy(port));fixture['immutable_ids'].append(uid)


with generation_profile('dsn-safe-v1'),using_quality_profile('turns'):
    for case in ('timeout-after-negotiation','timeout-during-restoration','incumbent-restore-fails','plan-window-expired'):
        board,fixture=make(case);eligible=set(nets)
        if case=='incumbent-restore-fails':
            # The local witness displaces both outer incumbents. Preserve the
            # stronger control: fewer total gaps still cannot split either.
            extra_source(board,fixture);extra_source(board,fixture,'extra2',26000000);eligible.update(('extra','extra2'))
        before=snapshot(board);original=deepcopy(fixture['source_ports']);current=deepcopy(fixture['current_ports']);initial=pending_matrix(board,eligible,time.monotonic()+5)
        carrier_ids={u for ids in fixture['carrier_ids'].values() for u in ids};mutations=[];expired=[False];restored_after_expiry=[];plan_deadlines=[];boundary_events=[]
        def negotiated(*args,**kwargs):
            if case=='plan-window-expired':
                interface_nets={q['net'] for q in fixture['source_ports']}
                entry=native_graph.copper_graph(board,interface_nets,args[3])
                closed=resolved_interfaces(fixture['source_ports'],set(),entry)
            answer=old_negotiate(*args,**kwargs);plan_deadlines.append(args[3])
            mutations.append(bool(answer['connected'] and answer['removed'] and set(snapshot(board)['tracks'])!=set(before['tracks']) and any(isinstance(t,p.PCB_VIA) and t.m_Uuid.AsString() in answer['added'] for t in board.GetTracks())))
            if case=='plan-window-expired':
                actual=native_graph.copper_graph(board,interface_nets,args[3])
                reopened=closed-resolved_interfaces(fixture['source_ports'],closed,actual)
                boundary_events.append({'newly_reopened':sorted(reopened),'connected':answer['connected']})
                if answer['connected'] and not reopened and carrier_ids-set(snapshot(board)['tracks']):
                    # Expire after a real guard-consistent carve, irrespective
                    # of how many physical source identities needed reopening.
                    offset=max(0,args[3]-time.monotonic())+.001
                    clock=SimpleNamespace(monotonic=lambda:time.monotonic()+offset)
                    native_graph.time=clock;feeder_repair.time=clock;expired[0]=True
            if case=='timeout-after-negotiation':raise TimeoutError('Injected deadline after real native negotiation mutation')
            return answer
        def restoration(candidate,router,requested,**kwargs):
            if expired[0]:restored_after_expiry.append({'nets':sorted(requested),'now':feeder_repair.time.monotonic(),'deadline':router.deadline})
            present={t.m_Uuid.AsString() for t in candidate.GetTracks()}
            if carrier_ids-present:
                if case=='timeout-during-restoration':
                    count=old_route(candidate,router,requested,**kwargs)
                    assert count and set(snapshot(candidate)['tracks'])!=set(before['tracks'])
                    raise TimeoutError('Injected deadline after native displaced-source restoration mutation')
                if case=='incumbent-restore-fails':return old_route(candidate,router,set(requested)&set(initial),**kwargs)
            return old_route(candidate,router,requested,**kwargs)
        native_graph.negotiate_routes=negotiated
        if case in ('incumbent-restore-fails','timeout-during-restoration','plan-window-expired'):native_graph.route_components=restoration
        try:
            with reserve_launches(current):
                scope=deepcopy(current_launches());scope_resolved=set(current_launch_inventory().resolved);history=repair_feeders(board,eligible,set(fixture['immutable_ids']),deadline=time.monotonic()+40,ports=current,source_ports=original);scope_exact=current_launches()==scope and current_launch_inventory().resolved==scope_resolved
        finally:
            native_graph.negotiate_routes,native_graph.route_components=old_negotiate,old_route
            native_graph.time,feeder_repair.time=old_graph_clock,old_repair_clock
        records[case]={'history':history,'mutated_native_candidate':mutations,'exact':snapshot(board)==before,'pending':pending_matrix(board,eligible,time.monotonic()+5),'initial':initial,'metadata_exact':fixture['current_ports']==current and fixture['source_ports']==original,'scope_exact':scope_exact,'plan_deadlines':plan_deadlines,'plan_expired':expired[0],'boundary_events':boundary_events,'restored_after_expiry':restored_after_expiry}
    for case in ('missing-live-advance','omitted-physical-alias'):
        board,fixture=make(case);current=deepcopy(fixture['current_ports']);original=deepcopy(fixture['source_ports'])
        if case=='missing-live-advance':
            uid=fixture['live_advance_ids']['inner'][0]
            for t in list(board.GetTracks()):
                if t.m_Uuid.AsString()==uid:board.Delete(t)
        else:next(q for q in current if q['net']=='outer_right')['pad_ids'].pop()
        before=snapshot(board);error=None
        try:
            with reserve_launches(current):repair_feeders(board,nets,set(fixture['immutable_ids']),deadline=time.monotonic()+40,ports=current,source_ports=original)
        except ValueError as exc:error=str(exc)
        records[case]={'error':error,'exact':snapshot(board)==before}
(out/'report.json').write_text(json.dumps(records,indent=2)+'\n')
