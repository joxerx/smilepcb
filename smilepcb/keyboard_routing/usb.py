"""The existing bounded USB recipe, usable before matrix bank planning."""
import math,time
from smilepcb.native_worker import p,snapshot
from smilepcb.routing_style import Clearance,octile_paths,add_path
from smilepcb.usb_pair import try_joint_pair,balance_pair
from .native_graph import copper_graph,Endpoint
from .completion import component_frontiers
from .local_router import LocalRouter
from .generation import current_generation_profile


def route_controller_usb(board,*,deadline,max_length_nm=...):
    from smilepcb.usb_policy import resolve_usb_max_length_nm
    max_length_nm=resolve_usb_max_length_nm(max_length_nm)
    before=snapshot(board);fps={f.GetReference():f for f in board.GetFootprints()};layer=fps['U_MCU'].GetLayer();result={'accepted':False,'acceptance_context':{'max_length_nm':max_length_nm}}
    try:
        if time.monotonic()>=deadline:raise TimeoutError('USB preparation deadline exhausted')
        pair=None
        if current_generation_profile().name=='dsn-process-v2':
            from .common_pair import try_common_pair
            # Reserve half of this caller's existing allowance for legacy
            # proposals and proof; an optional corridor never renews a budget.
            common_deadline=time.monotonic()+max(0,(deadline-time.monotonic())/2)
            pair=try_common_pair(board,('USB_DP','USB_DM'),source_ref='U_MCU',target_ref='J_UDB',layer=layer,deadline=common_deadline,max_length_nm=max_length_nm)
        if pair is None:
            pair=try_joint_pair(board,('USB_DP','USB_DM'),source_ref='U_MCU',target_ref='J_UDB',layer=layer,deadline=deadline,max_length_nm=max_length_nm)
        if pair is None:
            pair={};oracle=Clearance(board)
            for name in ('USB_DP','USB_DM'):
                if time.monotonic()>=deadline:raise TimeoutError('USB preparation deadline exhausted')
                graph=copper_graph(board,{name},deadline)
                component=next(rows for rows in graph['groups'].values() if any(isinstance(graph['nodes'][i]['item'],p.PAD) and graph['nodes'][i]['item'].GetParentFootprint().GetReference()=='U_MCU' for i in rows))
                starts=component_frontiers(graph,component,deadline);target=next(d for d in fps['J_UDB'].Pads() if d.GetNetname()==name);end=(target.GetPosition().x,target.GetPosition().y);choices=[]
                for start in starts:
                    for path in octile_paths(start,end):
                        if time.monotonic()>=deadline:raise TimeoutError('USB preparation deadline exhausted')
                        if all(oracle.clear(a,z,200000,layer,name) for a,z in zip(path,path[1:])):choices.append((sum(math.dist(a,z) for a,z in zip(path,path[1:])),path))
                if choices:
                    _,path=min(choices);ids=add_path(board,oracle,path,layer,name,200000);pair[name]={'points_nm':path,'added_ids':ids,'method':'checked octile'}
                else:
                    router=LocalRouter(board,deadline=deadline,compact=True);router.grid_step=125000;router.connection_budget_s=5;router.search_budget_s=5;router.heuristic_weight=3;router.via_clear=lambda *args:False
                    if not any(router.connect(Endpoint(start,layer,name),target) for start in sorted(starts,key=lambda q:math.dist(q,end))):raise ValueError('No checked single-layer USB join for '+name)
                    pair[name]={'added_ids':router.added,'method':'existing single-layer grid search'}
                oracle=Clearance(board)
        balance_options={'editable_ids':[u for q in pair.values() for u in q['added_ids']],'max_length_nm':max_length_nm}
        if current_generation_profile().name=='dsn-process-v2':
            # Match the active creation policy, including stronger native item
            # clearances and material/launch guards, after both joins exist.
            balance_router=LocalRouter(board,deadline=deadline,compact=True)
            def clear_segment(a,z,width,face,net):
                shape=p.SHAPE_SEGMENT(p.VECTOR2I(*a),p.VECTOR2I(*z),width)
                return balance_router.clear(shape,face,net)
            balance_options.update(clear_segment=clear_segment,deadline=deadline)
        balance=balance_pair(board,('USB_DP','USB_DM'),**balance_options)
        if balance['changed']:
            row=pair[balance['net']];row['added_ids']=[u for u in row['added_ids'] if u not in balance['removed_ids']]+balance['added_ids']
        from smilepcb.keyboard_native import verify_usb_pair
        lengths=verify_usb_pair(board,max_length_nm=max_length_nm);after=snapshot(board)
        if time.monotonic()>=deadline:raise TimeoutError('USB preparation proof deadline exhausted')
        if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=s for u,s in before['tracks'].items()):raise ValueError('USB preparation changed source copper or placement')
        result.update(accepted=True,usb=pair,balance=balance,verified_lengths_mm=lengths,added_ids=sorted(set(after['tracks'])-set(before['tracks'])),layer=board.GetLayerName(layer),original_copper_unchanged=True)
    except (ValueError,TimeoutError) as error:result['reason']=str(error)
    finally:
        if not result['accepted']:
            for t in list(board.GetTracks()):
                if t.m_Uuid.AsString() not in before['tracks']:board.Delete(t)
            board.BuildConnectivity()
            if snapshot(board)!=before:raise ValueError('USB preparation rollback changed source')
    return result
