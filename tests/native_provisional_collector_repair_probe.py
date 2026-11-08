"""Production negotiation must reopen and restore provisional collector feeders."""
import inspect,json,sys,time
from copy import deepcopy
from pathlib import Path
from native_provisional_collector_fixture import build_native
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launches,current_launch_inventory,resolved_interfaces
from smilepcb.keyboard_routing.feeder_repair import repair_feeders,matrix_pad_state
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.completion import pad_partitions
from smilepcb.keyboard_routing.handoff import preserves_contacts
from smilepcb.keyboard_process import project_settings,process_rules,apply_native_rules
from smilepcb.routing_quality import using_quality_profile

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={}
nets={'outer_left','inner','outer_right'}
with generation_profile('dsn-safe-v1'),using_quality_profile('turns'):
    for name,face,shift in (('back',p.B_Cu,(0,0)),('front-shifted',p.F_Cu,(2000000,3000000))):
        board,fixture=build_native(p,face=face,translation=shift)
        before_path=out/(name+'-before.kicad_pcb');before_path.with_suffix('.kicad_pro').write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')))
        assert p.SaveBoard(str(before_path),board)
        board=p.LoadBoard(str(before_path));before=snapshot(board)
        own={item.GetOwnClearance(layer) for item in [*board.GetTracks(),*(d for fp in board.GetFootprints() for d in fp.Pads())] for layer in (p.F_Cu,p.B_Cu) if item.IsOnLayer(layer)}
        assert own=={150000},own
        apply_native_rules(board,'fr4-2l-1oz-0p5-0p2')
        initial=matrix_pad_state(board,nets,time.monotonic()+5);parts=pad_partitions(board,time.monotonic()+5)
        assert initial['missing']=={'inner':1},initial
        originals=deepcopy(fixture['source_ports']);current=deepcopy(fixture['current_ports'])
        immutable=set(fixture['immutable_ids']);advances={u for ids in fixture['live_advance_ids'].values() for u in ids}
        carrier_ids={u for ids in fixture['carrier_ids'].values() for u in ids}
        arguments={'deadline':time.monotonic()+40,'ports':current}
        # Baseline runs the actual old matrix protection contract; the new API
        # switches ownership, never the production planner or collision oracle.
        if 'source_ports' in inspect.signature(repair_feeders).parameters:
            arguments['source_ports']=originals;protected=immutable
        else:protected=immutable|advances|carrier_ids
        with reserve_launches(current):
            history=repair_feeders(board,nets,protected,**arguments)
            scoped=list(current_launches());present={t.m_Uuid.AsString() for t in board.GetTracks()};continuation_error=None
            try:LocalRouter(board,compact=True,track_width_nm=150000)
            except ValueError as exc:continuation_error=str(exc)
            expected=resolved_interfaces(scoped,set(),copper_graph(board,{q['net'] for q in scoped},time.monotonic()+5))
            scope_valid=all(set(q['added_ids'])<=present for q in scoped) and current_launch_inventory().resolved==expected and continuation_error is None
        after=snapshot(board);state=matrix_pad_state(board,nets,time.monotonic()+5)
        (out/(name+'-after.kicad_pro')).write_text(json.dumps(project_settings('fr4-2l-1oz-0p5-0p2')))
        assert p.SaveBoard(str(out/(name+'-after.kicad_pcb')),board)
        reloaded=p.LoadBoard(str(out/(name+'-after.kicad_pcb')))
        saved=matrix_pad_state(reloaded,nets,time.monotonic()+5)
        record={'process_rules':process_rules('fr4-2l-1oz-0p5-0p2'),'own_clearance_nm':sorted(own),'creation_gap_nm':173000,'initial_missing':initial['missing'],'missing':state['missing'],'saved_missing':saved['missing'],
                'history':history,'original_metadata_exact':fixture['source_ports']==originals,'current_metadata_exact':fixture['current_ports']==current,
                'scope_valid':scope_valid,'continuation_error':continuation_error,'incumbent_contacts_preserved':preserves_contacts([list(q) for q in parts],[list(q) for q in pad_partitions(board,time.monotonic()+5)]),
                'source_and_live_advances_exact':before['footprints']==after['footprints'] and all(after['tracks'].get(u)==before['tracks'][u] for u in immutable|advances),
                'all_physical_pads_retained':initial['pad_ids']==state['pad_ids'],
                'closed_feeder_moved':any(u not in after['tracks'] for u in carrier_ids)}
        report[name]=record
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
