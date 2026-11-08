"""Actual closed-native repair runway protection at the normal worker boundary."""
import inspect,json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb import keyboard_native as api
from smilepcb.keyboard_routing import native_graph
from smilepcb.keyboard_routing.local_router import LocalRouter,vec,xy
from smilepcb.keyboard_routing.port_policy import PortPolicy
from smilepcb.keyboard_routing.interface_contract import verify_interfaces
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launches,current_launch_inventory,interface_identity
from smilepcb.keyboard_routing.field_contract import verify_native
from smilepcb.keyboard_routing.completion import pad_partitions
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.routing_quality import using_quality_profile
from native_post_matrix_interface_scope_probe import make,PROFILE

OUT=Path(sys.argv[1]);OUT.mkdir(parents=True,exist_ok=True)
def track(board,net,a,z,face):
    t=p.PCB_TRACK(board);t.SetStart(vec(a));t.SetEnd(vec(z));t.SetWidth(150000);t.SetLayer(face);t.SetNet(board.FindNet(net));board.Add(t);return t

def via(board,net,at):
    t=p.PCB_VIA(board);t.SetPosition(vec(at));t.SetWidth(500000);t.SetDrill(200000);t.SetLayerPair(p.F_Cu,p.B_Cu);t.SetNet(board.FindNet(net));board.Add(t);return t

def own(board):return {i.m_Uuid.AsString()+'|'+str(face):[i.GetOwnClearance(face),i.GetLocalClearance() or 0] for i in [*board.GetTracks(),*(d for f in board.GetFootprints() for d in f.Pads())] for face in (p.F_Cu,p.B_Cu) if i.IsOnLayer(face)}
def physical(board,face):
    # Independent native physics only; no candidate is emitted in this scope.
    with reserve_launches([]):world=LocalRouter(board,deadline=time.monotonic()+3,compact=True,track_width_nm=150000)
    shape=p.SHAPE_SEGMENT(vec((7500000,22500000)),vec((7500000,23500000)),150000)
    return world.clear(shape,face,'foreign'),world.via_clear((7675000,23000000),'foreign')

def prepare(folder,face):
    path,cfg,receipt,field,replacements,headface=make(folder,face);other=p.B_Cu if face==p.F_Cu else p.F_Cu
    b=p.LoadBoard(str(path))
    for t in list(b.GetTracks()):
        if t.m_Uuid.AsString()==replacements['closed']:b.Delete(t)
    # The closed field connection leaves the source on the opposite face;
    # its forward500um physical strip remains genuinely empty and clear.
    via(b,'closed',(7000000,23000000));via(b,'closed',(15000000,23000000))
    for a,z in (((7000000,23000000),(7000000,24500000)),((7000000,24500000),(15000000,24500000)),((15000000,24500000),(15000000,23000000))):track(b,'closed',a,z,other)
    next(d for f in b.GetFootprints() if f.GetReference()=='ROW_FIELD' for d in f.Pads()).SetLocalClearance(250000)
    assert p.SaveBoard(str(path),b,True)

def exercise(folder,face):
    path=folder/'keyboard.kicad_pcb';cfg=json.loads((folder/'.smilepcb-keyboard.json').read_text());receipt=json.loads((folder/'.smilepcb-interfaces.json').read_text());field=json.loads((folder/'.smilepcb-field.json').read_text());other=p.B_Cu if face==p.F_Cu else p.F_Cu
    original=path.read_bytes();initial=p.LoadBoard(str(path));before=snapshot(initial);clearances=own(initial);parts=pad_partitions(initial,time.monotonic()+3)
    assert initial.GetDesignSettings().m_TrackMinWidth==150000,'Fresh fixture native project minimum differs from declared150um'
    assert sorted({v[0] for v in clearances.values()})==[150000,250000],'Fresh fixture150/250um item rules not loaded'
    q=next(q for q in receipt['ports'] if q['net']=='closed');key=interface_identity(q);ready=next(q for q in receipt['ports'] if q['net']=='ready')
    proof=verify_interfaces(initial,cfg,receipt,deadline=time.monotonic()+3,retire_completed=True)
    result={'entry':{'native_matrix_full':bool(verify_native(initial,field)), 'native_closed_resolved':key in set(map(tuple,proof['resolved'])), 'actual_closed_native_pads_joined':'closed' not in api.pad_gaps(initial), 'real150_and250_item_rules':sorted({v[0] for v in clearances.values()})==[150000,250000]}}
    saved_run=api._run
    cases=('normal_track','normal_via','raw_track','raw_via','same_net','field_branch','other_face','ordinary_components','ordinary_cleanup','nested_and_effective')
    for case in cases:
        path.write_bytes(original);observed={};error=None
        operation='components' if case=='ordinary_components' else 'cleanup' if case=='ordinary_cleanup' else 'complete'
        def controlled(request,**kwargs):
            observed['controlled_planner_entered']=True;board=p.LoadBoard(request['board'])
            observed['qualified_native_rules_before_planning']=board.GetDesignSettings().m_TrackMinWidth==150000 and sorted({v[0] for v in own(board).values()})==[150000,250000]
            assert observed['qualified_native_rules_before_planning'],'Native rule context changed before planner'
            world=LocalRouter(board,deadline=time.monotonic()+4,compact=True,track_width_nm=150000);shape=p.SHAPE_SEGMENT(vec((7500000,22500000)),vec((7500000,23500000)),150000)
            scope=current_launch_inventory();closed=next(q for q in current_launches() if q['net']=='closed');held=getattr(scope,'repair_reserved',set())
            observed.update(track_physically_clear=physical(board,face)[0],via_physically_clear=physical(board,face)[1],native_closure_not_forced_live=key in world.port_policy.resolved and all(interface_identity(q)!=key for q in world.port_policy.live_ports),physical_head_metadata_intact=next(t for t in board.GetTracks() if t.m_Uuid.AsString()==q['terminal_id']).GetNetname()=='closed')
            if case.startswith('normal_'):
                observed['held_intact_closed_only']=key in held and interface_identity(ready) not in held
                try:
                    if case=='normal_track':world.add_path([(7500000,22500000),(7500000,23500000)],face,'foreign')
                    else:world.add_via((7675000,23000000),'foreign')
                except ValueError:observed['admission_rejected']=True
                else:observed['admission_rejected']=False
                observed['no_emission']=not world.added;return {'accepted':False}
            if case=='nested_and_effective':
                graph=native_graph.copper_graph(board,{'closed'},time.monotonic()+3);indices=next(ids for ids in graph['groups'].values() if any(graph['nodes'][i]['item'].m_Uuid.AsString() in closed['pad_ids'] for i in ids))
                observed['closed_contact_inventory_kept']=((5000000,23000000),face) in world.port_policy.contacts(graph,indices,(15000000,23000000))
                observed['same_net_forward_allowed']=world.leg((7000000,23000000),(7500000,23000000),face,'closed') is not None
                world.port_policy=PortPolicy(board,[closed],resolved=[key]);observed['effective_replacement_keeps_repair_reservation']=not world.clear(shape,face,'foreign')
                world.port_policy=None;observed['captured_policy_reset_keeps_reservation']=not world.clear(shape,face,'foreign')
                with reserve_launches([closed],resolved=[key]):observed['nested_matching_identity_inherits']=not LocalRouter(board,deadline=time.monotonic()+3,compact=True,track_width_nm=150000).clear(shape,face,'foreign')
                options={'repair_reserved':()} if 'repair_reserved' in inspect.signature(reserve_launches).parameters else {}
                with reserve_launches([closed],resolved=[key],**options):observed['explicit_empty_child_exclusion_clear']=LocalRouter(board,deadline=time.monotonic()+3,compact=True,track_width_nm=150000).clear(shape,face,'foreign')
                calls=[];read=native_graph.copper_graph
                def counted(*args,**kw):calls.append(1);return read(*args,**kw)
                native_graph.copper_graph=counted
                try:
                    for _ in range(20):world.clear(shape,face,'foreign')
                finally:native_graph.copper_graph=read
                observed['no_per_edge_graph']=not calls;return {'accepted':False}
            if case.startswith('ordinary_'):
                observed['no_repair_reservation']=not held and world.clear(shape,face,'foreign')
                return saved_run(request,**kwargs)
            if case=='raw_via':via(board,'foreign',(7675000,23000000))
            elif case=='same_net':track(board,'closed',(7000000,23000000),(7500000,23000000),face)
            elif case=='field_branch':track(board,'closed',(10000000,24500000),(10000000,25000000),other)
            else:track(board,'foreign',(7500000,22500000),(7500000,23500000),other if case=='other_face' else face)
            assert p.SaveBoard(request['board'],board,True);return {'accepted':True}
        api._run=controlled
        try:response=api.run({'operation':operation,'strategy':'signal_negotiate','board':str(path),'protected_ids':[],'include_nets':[],'generation_profile':PROFILE,'routing_quality':'turns','track_width_nm':150000})
        except (ValueError,TimeoutError) as exc:error=type(exc).__name__+': '+str(exc)
        finally:api._run=saved_run
        after=p.LoadBoard(str(path));current=snapshot(after);held_after=current_launch_inventory()
        stable={'controlled_planner_entered':observed.get('controlled_planner_entered',False),'all_original_copper_exact':all(current['tracks'].get(u)==v for u,v in before['tracks'].items()),'footprint_pad_frame_exact':current['footprints']==before['footprints'],'old_contacts_exact':{frozenset(g) for g in pad_partitions(after,time.monotonic()+3)}=={frozenset(g) for g in parts},'retained_item_rules_exact':all(own(after).get(u)==v for u,v in clearances.items()),'closed_net_still_native_complete':'closed' not in api.pad_gaps(after),'context_exit_no_inventory_leak':held_after is None}
        if case.startswith('raw_'):result[case]={**observed,**stable,'independent_gate_rejected':error is not None and error.startswith('ValueError'),'saved_board_rollback_byte_exact':path.read_bytes()==original}
        else:result[case]={**observed,**stable,'normal_api_returned':error is None}
        if error and not case.startswith('raw_'):result[case]['unexpected_error']=error
        (folder/'partial-results.json').write_text(json.dumps(result,indent=2)+'\n')
    path.write_bytes(original);exit_board=p.LoadBoard(str(path))
    (folder/'exit-native-rules.json').write_text(json.dumps({'track_min_nm':exit_board.GetDesignSettings().m_TrackMinWidth,'project_min_mm':json.loads(path.with_suffix('.kicad_pro').read_text())['board']['design_settings']['rules']['min_track_width']})+'\n')
    world=LocalRouter(exit_board,deadline=time.monotonic()+3,compact=True,track_width_nm=150000);cross=p.SHAPE_SEGMENT(vec((7500000,22500000)),vec((7500000,23500000)),150000)
    result['exit']={'scratch_restored':path.read_bytes()==original,'no_foreign_reservation_leak':world.clear(cross,face,'foreign'),'no_scope_leak':current_launch_inventory() is None}
    return result

if __name__=='__main__':
    started=time.monotonic()
    with generation_profile(PROFILE),using_quality_profile('turns'):
        if '--prepare' in sys.argv:
            for name,face in (('front',p.F_Cu),('back',p.B_Cu)):prepare(OUT/name,face)
        else:
            faces={name:exercise(OUT/name,face) for name,face in (('front',p.F_Cu),('back',p.B_Cu))}
            (OUT/'report.json').write_text(json.dumps({'runtime':str(Path(api.__file__).resolve()),'elapsed_s':time.monotonic()-started,'faces':faces},indent=2)+'\n')
