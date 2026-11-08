"""Actual two-face wrapper/collision/rollback controls for complex repair."""
import hashlib,json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb import keyboard_native as api
from smilepcb.keyboard_routing import native_graph
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.interface_contract import verify_interfaces,MARKER as INTERFACES
from smilepcb.keyboard_routing.interface_launches import current_launch_inventory,interface_identity
from smilepcb.keyboard_routing.generation import generation_profile
from native_post_matrix_interface_scope_probe import make,PROFILE

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)


def exercise(folder,face):
    path,cfg,receipt,field,replacements,headface=make(folder,face)
    # Existing parallel copper makes an ordinary subset retirement electrically
    # harmless; complex repair still must preserve its recorded original head.
    board=p.LoadBoard(str(path));cover=p.PCB_TRACK(board);cover.SetStart(vec((5000000,23000000)));cover.SetEnd(vec((15000000,23000000)));cover.SetWidth(150000);cover.SetLayer(face);cover.SetNet(board.FindNet('closed'));board.Add(cover);assert p.SaveBoard(str(path),board)
    original=path.read_bytes();result={};closed=next(q for q in receipt['ports'] if q['net']=='closed');head=closed['terminal_id'];ready=next(q for q in receipt['ports'] if q['net']=='ready')
    initial=p.LoadBoard(str(path));proof=verify_interfaces(initial,cfg,receipt,deadline=time.monotonic()+5,retire_completed=True)
    result['native_entry']={'closed_actual_full_pad_join':interface_identity(closed) in set(map(tuple,proof['resolved'])),
        'retired_ready_actual_full_pad_join':interface_identity(ready) in set(map(tuple,proof['resolved'])),
        'aliases_not_mistaken_for_closure':all(q['net']!='control' or len(q['pad_ids'])==2 and interface_identity(q) not in set(map(tuple,proof['resolved'])) for q in proof['ports'])}
    baseline=api._run
    for case in ('hard_and_carve','raw_closed_delete','raw_retired_delete','raw_retired_add','raw_live_change','alias_corruption','ordinary_subset'):
        path.write_bytes(original);(folder/INTERFACES).write_text(json.dumps(receipt));observed={};operation='components' if case=='ordinary_subset' else 'complete'
        if case=='alias_corruption':
            corrupt=json.loads(json.dumps(receipt));q=next(q for q in corrupt['ports'] if q['net']=='control');q['pad_ids'].pop();(folder/INTERFACES).write_text(json.dumps(corrupt))
        entry_bytes=path.read_bytes()
        def raw(request,**kwargs):
            b=p.LoadBoard(request['board']);protected=set(request.get('protected_ids',()))
            if case=='hard_and_carve':
                observed['closed_head_hard']=head in protected;observed['retired_net_hard']=replacements['ready'] in protected
                saved=native_graph.route_components
                def no_search(board,router,nets,**kw):
                    # Actual negotiator relaxed oracle must still index the
                    # completed source head as a real foreign copper obstacle.
                    router.graph_diagnostic={}
                    cross=p.SHAPE_SEGMENT(vec((6000000,22000000)),vec((6000000,24000000)),150000)
                    observed['relaxed_carve_cannot_ignore_head']=not router.clear(cross,face,'foreign')
                    return 0
                native_graph.route_components=no_search
                try:plan=native_graph.negotiate_routes(b,{'foreign'},protected,time.monotonic()+2)
                finally:native_graph.route_components=saved
                observed['no_head_removed']=head not in plan['removed']
                return {'accepted':False}
            for item in list(b.GetTracks()):
                uid=item.m_Uuid.AsString()
                if case=='raw_closed_delete' and uid==head:b.Delete(item)
                elif case=='raw_retired_delete' and uid==replacements['ready']:b.Delete(item)
                elif case=='raw_live_change' and uid==next(q for q in receipt['ports'] if q['net']=='control')['terminal_id']:item.SetWidth(150001)
                elif case=='ordinary_subset' and uid==head:item.SetEnd(vec((6800000,23000000)))
            if case=='raw_retired_add':
                t=p.PCB_TRACK(b);t.SetNet(b.FindNet('ready'));t.SetStart(vec((10000000,25000000)));t.SetEnd(vec((11000000,25000000)));t.SetLayer(face);t.SetWidth(150000);b.Add(t)
            if case=='ordinary_subset':observed['closed_head_not_complex_frozen']=head not in protected
            assert p.SaveBoard(request['board'],b)
            return {'accepted':True}
        api._run=raw;error=None
        try:api.run({'operation':operation,'strategy':'signal_negotiate','board':str(path),'generation_profile':PROFILE})
        except (ValueError,TimeoutError) as exc:error=type(exc).__name__
        finally:api._run=baseline
        if case=='hard_and_carve':result[case]={**observed,'source_exact':path.read_bytes()==entry_bytes,'no_error':error is None}
        elif case=='ordinary_subset':
            saved=p.LoadBoard(str(path));result[case]={**observed,'accepted':error is None,'native_closed_join_preserved':'closed' not in api.pad_gaps(saved)}
        else:result[case]={'rejected':error=='ValueError','saved_source_exact':path.read_bytes()==entry_bytes}
    path.write_bytes(original);(folder/INTERFACES).write_text(json.dumps(receipt))
    result['scratch_restored']=path.read_bytes()==original
    return result


if __name__=='__main__':
    started=time.monotonic()
    with generation_profile(PROFILE):report={name:exercise(out/name,face) for name,face in [('front',p.F_Cu),('back',p.B_Cu)]}
    (out/'report.json').write_text(json.dumps({'runtime':str(Path(api.__file__).resolve()),'elapsed_s':time.monotonic()-started,'faces':report},indent=2)+'\n')
