"""Actual native repair must keep the remote pieces of displaced feeders."""
import json,sys,time
from copy import deepcopy
from pathlib import Path
from native_provisional_collector_fixture import build_native,NM
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launches,current_launch_inventory,resolved_interfaces
from smilepcb.keyboard_routing.feeder_repair import repair_feeders,matrix_pad_state
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing import native_graph
from smilepcb.keyboard_routing.completion import pad_partitions
from smilepcb.keyboard_routing.handoff import preserves_contacts
from smilepcb.keyboard_process import project_settings,apply_native_rules
from smilepcb.routing_quality import using_quality_profile

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={}
nets={'outer_left','inner','outer_right'}
with generation_profile('dsn-safe-v1'),using_quality_profile('turns'):
    for name,face,shift,dogleg in (('back-long-straight',p.B_Cu,(0,0),False),('front-long-dogleg',p.F_Cu,(2*NM,3*NM),True)):
        board,fixture=build_native(p,face=face,translation=shift)
        # Replace only the far, electrically necessary tail of one incumbent.
        old=fixture['carrier_ids']['outer_left'][-1]
        item=next(t for t in board.GetTracks() if t.m_Uuid.AsString()==old)
        a=tuple(item.GetStart()[i] for i in (0,1));z=tuple(item.GetEnd()[i] for i in (0,1));board.Delete(item)
        vertices=[a,z] if not dogleg else [a,(a[0]-500000,a[1]-500000),(z[0]+500000,z[1]-500000),z]
        points=[vertices[0]]
        for start,end in zip(vertices,vertices[1:]):
            for step in range(1,5):points.append(tuple(start[i]+(end[i]-start[i])*step//4 for i in (0,1)))
        remote=[]
        for start,end in zip(points,points[1:]):
            track=p.PCB_TRACK(board);track.SetStart(p.VECTOR2I(*start));track.SetEnd(p.VECTOR2I(*end));track.SetLayer(face);track.SetWidth(150000);track.SetNet(board.FindNet('outer_left'));board.Add(track);remote.append(track.m_Uuid.AsString())
        fixture['carrier_ids']['outer_left'][-1:]=remote
        port=next(q for q in fixture['current_ports'] if q['net']=='outer_left')
        port['added_ids']=[u for u in port['added_ids'] if u!=old]+remote
        port.update(terminal_id=remote[-1],heading_origin_nm=list(points[-2]))
        rename={'outer_left':'lane_z','inner':'lane_a','outer_right':'lane_m','fixed':'fixed_x'} if dogleg else {n:n for n in ('outer_left','inner','outer_right','fixed')}
        for old,new in rename.items():board.FindNet(old).SetNetname(new)
        for q in fixture['source_ports']+fixture['current_ports']:q['net']=rename[q['net']]
        nets={rename[n] for n in ('outer_left','inner','outer_right')}
        before_path=out/(name+'-before.kicad_pcb');before_path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')))
        assert p.SaveBoard(str(before_path),board);board=p.LoadBoard(str(before_path))
        own={item.GetOwnClearance(layer) for item in [*board.GetTracks(),*(d for fp in board.GetFootprints() for d in fp.Pads())] for layer in (p.F_Cu,p.B_Cu) if item.IsOnLayer(layer)}
        assert own=={150000},own
        apply_native_rules(board,'fr4-2l-1oz-0p5-0p2');before=snapshot(board)
        initial=matrix_pad_state(board,nets,time.monotonic()+5);parts=pad_partitions(board,time.monotonic()+5)
        assert initial['missing']=={rename['inner']:1},initial
        originals=deepcopy(fixture['source_ports']);current=deepcopy(fixture['current_ports']);fragments={};fragment_origins={u:u for u in before['tracks']};checks=[];deadlines=[];calls=[];planning=[False]
        real_plan=native_graph.negotiate_routes;real_routes=native_graph.route_components
        def observe_plan(*args,**kwargs):
            entry=snapshot(board)['tracks'];deadlines.append(args[3]);planning[0]=True
            try:result=real_plan(*args,**kwargs)
            finally:planning[0]=False
            live=snapshot(board)['tracks']
            (out/(name+'-carved.json')).write_text(json.dumps({'tracks':live,'plan':result},indent=2)+'\n')
            ancestry=result.get('fragment_sources',{});checks.append(set(ancestry)==set(result['retained_fragments']) and all(parent in result['removed'] and parent in fragment_origins and all(live[u][key]==entry[parent][key] for key in ('net','layer','width','class')) for u,parent in ancestry.items()))
            parents={u:fragment_origins[parent] for u,parent in ancestry.items()}
            for u in result['removed']:fragments.pop(u,None);fragment_origins.pop(u,None)
            fragments.update({u:live[u] for u in result['retained_fragments']});fragment_origins.update(parents);return result
        def observe_routes(candidate,router,selected,**kwargs):
            if not planning[0]:calls.append({'nets':sorted(selected),'grid_nm':router.grid_step,'deadline':router.deadline,'router':id(router)})
            return real_routes(candidate,router,selected,**kwargs)
        native_graph.negotiate_routes=observe_plan;native_graph.route_components=observe_routes
        try:
            with reserve_launches(current):
                history=repair_feeders(board,nets,set(fixture['immutable_ids']),deadline=time.monotonic()+40,ports=current,source_ports=originals)
                scoped=list(current_launches());present={t.m_Uuid.AsString() for t in board.GetTracks()};error=None
                try:LocalRouter(board,compact=True,track_width_nm=150000)
                except ValueError as exc:error=str(exc)
                expected=resolved_interfaces(scoped,set(),native_graph.copper_graph(board,{q['net'] for q in scoped},time.monotonic()+5))
                valid=error is None and all(set(q['added_ids'])<=present for q in scoped) and current_launch_inventory().resolved==expected
        finally:native_graph.negotiate_routes=real_plan;native_graph.route_components=real_routes
        after=snapshot(board);state=matrix_pad_state(board,nets,time.monotonic()+5)
        removed={u for row in history for u in row.get('plan',{}).get('removed',[])}
        expected_old=set(before['tracks'])-removed
        path=out/(name+'-after.kicad_pcb');path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')));assert p.SaveBoard(str(path),board)
        saved=matrix_pad_state(p.LoadBoard(str(path)),nets,time.monotonic()+5)
        report[name]={'history':history,'missing':state['missing'],'saved_missing':saved['missing'],'own_clearance_nm':sorted(own),'creation_gap_nm':173000,
            'remote_piece_count':len(remote),'remote_exact':all(after['tracks'].get(u)==before['tracks'][u] for u in remote),
            'unaffected_old_count':len(expected_old),'unaffected_old_exact':all(after['tracks'].get(u)==before['tracks'][u] for u in expected_old),
            'retained_fragment_count':len(fragments),'retained_fragments_exact':bool(fragments) and all(after['tracks'].get(u)==data for u,data in fragments.items()),
            'original_missing_nets':sorted(initial['missing']),'displaced_nets':sorted({before['tracks'][u]['net'] for u in removed}),'planning_deadlines':deadlines,'fragment_ancestry_valid':bool(checks) and all(checks) and history[-1].get('plan',{}).get('fragment_sources')=={u:fragment_origins[u] for u in fragments},'restoration_calls':calls,'scope_valid':valid,'all_pads_retained':initial['pad_ids']==state['pad_ids'],
            'source_and_live_exact':before['footprints']==after['footprints'] and all(after['tracks'].get(u)==before['tracks'][u] for u in set(fixture['immutable_ids'])|{u for ids in fixture['live_advance_ids'].values() for u in ids}),
            'contacts_preserved':preserves_contacts([list(q) for q in parts],[list(q) for q in pad_partitions(board,time.monotonic()+5)]),
            'input_metadata_exact':fixture['source_ports']==originals and fixture['current_ports']==current}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
