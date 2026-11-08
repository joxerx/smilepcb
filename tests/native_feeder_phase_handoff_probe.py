"""A final fine join must hand off to a separate bounded incumbent ladder."""
import json,sys,time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from native_provisional_collector_fixture import build_native
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import project_settings,apply_native_rules
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.routing_quality import using_quality_profile
from smilepcb.keyboard_routing import feeder_repair,native_graph,local_router
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launches,current_launch_inventory,resolved_interfaces
from smilepcb.keyboard_routing.completion import pad_partitions
from smilepcb.keyboard_routing.handoff import preserves_contacts

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={};nets={'outer_left','inner','outer_right'}
real_plan,real_route=native_graph.negotiate_routes,native_graph.route_components
clocks=(feeder_repair.time,native_graph.time,local_router.time)
with generation_profile('dsn-safe-v1'),using_quality_profile('turns'):
    for name,face,global_expiry in (('back-subwindow',p.B_Cu,False),('front-subwindow',p.F_Cu,False),('back-global',p.B_Cu,True),('front-global',p.F_Cu,True)):
        board,fixture=build_native(p,face=face,translation=(2000000,3000000) if face==p.F_Cu else (0,0))
        path=out/(name+'-before.kicad_pcb');path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')));assert p.SaveBoard(str(path),board);board=p.LoadBoard(str(path))
        own={item.GetOwnClearance(layer) for item in [*board.GetTracks(),*(d for fp in board.GetFootprints() for d in fp.Pads())] for layer in (p.F_Cu,p.B_Cu) if item.IsOnLayer(layer)}
        assert own=={150000},own
        apply_native_rules(board,'fr4-2l-1oz-0p5-0p2');before=snapshot(board);parts=pad_partitions(board,time.monotonic()+5)
        initial=feeder_repair.pending_matrix(board,nets,time.monotonic()+5);originals=deepcopy(fixture['source_ports']);current=deepcopy(fixture['current_ports'])
        deadline=time.monotonic()+40;stop=deadline-2;planning=[False];calls=[];expired=[];native_join=[]
        def plan(*args,**kwargs):
            planning[0]=True
            try:return real_plan(*args,**kwargs)
            finally:planning[0]=False
        def route(candidate,router,requested,**kwargs):
            if planning[0]:return real_route(candidate,router,requested,**kwargs)
            requested=set(requested);row={'nets':sorted(requested),'grid_nm':router.grid_step,'deadline':router.deadline,'router':id(router)};calls.append(row)
            if requested&set(initial) and router.grid_step!=125000:
                # Force the same coordinator transition as a failed coarse
                # opportunity; the final fine join still uses real native search.
                row['controlled_coarse_deferral']=True
                return real_route(candidate,router,requested-set(initial),**kwargs)
            count=real_route(candidate,router,requested,**kwargs);row['connections']=count
            if count and requested&set(initial) and router.grid_step==125000:
                pending=feeder_repair.pending_matrix(candidate,nets,stop)
                assert not set(initial)&set(pending) and pending,pending
                native_join.append(pending);expired.append(router);router.deadline=time.monotonic()-1
                if global_expiry:
                    clock=SimpleNamespace(monotonic=lambda:deadline+1)
                    feeder_repair.time=native_graph.time=local_router.time=clock
            return count
        native_graph.negotiate_routes,native_graph.route_components=plan,route
        try:
            with reserve_launches(current):
                scope=deepcopy(current_launches());scope_resolved=set(current_launch_inventory().resolved)
                history=feeder_repair.repair_feeders(board,nets,set(fixture['immutable_ids']),deadline=deadline,ports=current,source_ports=originals)
                scope_exact=current_launches()==scope and current_launch_inventory().resolved==scope_resolved
                # Restore only test clocks before independent physical reads.
                feeder_repair.time,native_graph.time,local_router.time=clocks
                scoped=list(current_launches());local_router.LocalRouter(board,compact=True,track_width_nm=150000)
                actual=native_graph.copper_graph(board,{q['net'] for q in scoped},time.monotonic()+5)
                scope_valid=current_launch_inventory().resolved==resolved_interfaces(scoped,set(),actual) if not global_expiry else scope_exact
        finally:
            native_graph.negotiate_routes,native_graph.route_components=real_plan,real_route
            feeder_repair.time,native_graph.time,local_router.time=clocks
        after=snapshot(board);pending=feeder_repair.pending_matrix(board,nets,time.monotonic()+5)
        path=out/(name+'-after.kicad_pcb');path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')));assert p.SaveBoard(str(path),board)
        saved=feeder_repair.pending_matrix(p.LoadBoard(str(path)),nets,time.monotonic()+5)
        protected=set(fixture['immutable_ids'])|{u for ids in fixture['live_advance_ids'].values() for u in ids}
        report[name]={'history':history,'calls':calls,'native_fine_join':native_join,'expired_routers':[id(r) for r in expired],'original_stop':stop,
            'all_requests_within_original_stop':all(q['deadline']<=stop for q in calls),'pending':pending,'saved_pending':saved,'exact':before==after,
            'source_field_live_exact':before['footprints']==after['footprints'] and all(after['tracks'].get(u)==before['tracks'][u] for u in protected),
            'metadata_exact':fixture['source_ports']==originals and fixture['current_ports']==current,'scope_valid':scope_valid,'scope_exact':scope_exact,
            'contacts_preserved':preserves_contacts([list(q) for q in parts],[list(q) for q in pad_partitions(board,time.monotonic()+5)])}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
