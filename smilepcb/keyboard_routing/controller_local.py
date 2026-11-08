"""Early controller pad joins and explicit, native-proved local obligations."""
from copy import deepcopy
import json,time
from smilepcb.keyboard_phases import ordered_nets
from .generation import current_generation_profile


def _members(board,cfg):
    from smilepcb.native_worker import snapshot
    state=snapshot(board);parts={q['ref']:q for q in cfg['parts']}
    phases=dict(ordered_nets(cfg));excluded=set(phases['rows']+phases['columns'])|set(cfg['plane_nets'])|{'USB_DP','USB_DM'}
    wanted={net for q in parts.values() if q['region']=='controller' for number,net in q['nets'].items()
            if number not in q.get('no_connect',()) and net and not net.startswith(('unconnected-','NC_'))}-excluded
    members={net:{} for net in wanted};bindings=set()
    for f in state['footprints'].values():
        for d in f['pads']:
            if not d['number'] or d['net'] not in wanted:continue
            q=parts.get(f['ref'])
            if q is None or q['nets'].get(d['number'])!=d['net'] or (type(q.get('region')) is not str or not q['region']):
                raise ValueError('Local controller pad has no exact model/region binding')
            members[d['net']][d['uuid']]={'ref':f['ref'],'pad':d};bindings.add((f['ref'],d['number']))
    for q in parts.values():
        for number,net in q['nets'].items():
            if net in wanted and not q['ref'].startswith('#') and (q['ref'],number) not in bindings:
                raise ValueError('Local controller model omitted an actual numbered pad')
    return state,parts,members


def local_targets(board,cfg):
    """Select controller subgraphs, including local portions of external nets."""
    _,parts,members=_members(board,cfg);result={}
    for net,rows in members.items():
        local={u:d for u,d in rows.items() if parts[d['ref']]['region']=='controller'}
        if len({(d['ref'],d['pad']['number']) for d in local.values()})>=2:result[net]=set(local)
    return result


def capture_local_closures(board,cfg,*,deadline):
    from .interface_contract import mcu_signal_nets
    from .native_graph import copper_graph
    if current_generation_profile().name!='dsn-process-v2':raise ValueError('Local closure creation requires explicit v2')
    _,parts,members=_members(board,cfg)
    eligible={net:rows for net,rows in members.items() if net in mcu_signal_nets(cfg) and rows and
        all(parts[d['ref']]['region']=='controller' for d in rows.values()) and
        len({(d['ref'],d['pad']['number']) for d in rows.values()})>=2}
    graph=copper_graph(board,set(eligible),deadline,include_zones=False)
    ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
    nets={net:{'pads':deepcopy(rows)} for net,rows in eligible.items() if set(rows)<=ids.keys() and
          len({graph['find'](ids[u]) for u in rows})==1}
    if time.monotonic()>=deadline:raise TimeoutError('Local closure proof deadline exhausted')
    return {'schema':1,'generation_profile':'dsn-process-v2','nets':nets}


def verify_local_closures(board,cfg,proof,*,deadline,graph=None):
    """Revalidate all physical aliases/shapes/regions and actual whole-net closure."""
    if (not isinstance(proof,dict) or set(proof)!={'schema','generation_profile','nets'} or proof['schema']!=1 or
        proof['generation_profile']!='dsn-process-v2' or not isinstance(proof['nets'],dict) or
        (current_generation_profile().name!='dsn-process-v2' and cfg.get('routing_preparation',{}).get('generation_profile')!='dsn-process-v2')):
        raise ValueError('Invalid explicit controller local-closure proof')
    from .interface_contract import mcu_signal_nets
    from .native_graph import copper_graph
    _,parts,members=_members(board,cfg);normalize=lambda v:json.loads(json.dumps(v))
    names=mcu_signal_nets(cfg);covered={}
    for net,row in proof['nets'].items():
        actual=members.get(net,{})
        if (net not in names or not actual or normalize(row)!={'pads':normalize(actual)} or
            any(parts[d['ref']]['region']!='controller' for d in actual.values()) or
            len({(d['ref'],d['pad']['number']) for d in actual.values()})<2):
            raise ValueError('Local closure changed physical members, aliases, shapes or region')
        covered[net]={u for u,d in actual.items() if d['ref']=='U_MCU'}
    if graph is None:graph=copper_graph(board,set(covered),deadline,include_zones=False)
    if any(n.get('zone',False) for n in graph['nodes'] if n['net'] in covered):
        raise ValueError('Refillable planes cannot establish controller-local closure')
    ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
    for net in covered:
        all_ids=set(members[net])
        if not all_ids<=ids.keys() or len({graph['find'](ids[u]) for u in all_ids})!=1:
            raise ValueError('Recorded controller-local net is no longer natively closed')
    if time.monotonic()>=deadline:raise TimeoutError('Local closure verification deadline exhausted')
    return covered


def prepare_controller_local(board,cfg,*,deadline):
    """Optional bounded joins; unproved attempts retain the old head obligations."""
    from smilepcb.native_worker import snapshot
    from .local_router import LocalRouter
    from .native_graph import route_components
    start=time.monotonic();stop=min(deadline-3,start+20);before=snapshot(board);result={'accepted':False,'connections':0,'added_ids':[]}
    try:
        if stop-start<=3:raise TimeoutError('No local-controller planning/proof allowance')
        targets=local_targets(board,cfg)
        router=LocalRouter(board,deadline=stop-3,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
        required=set().union(*targets.values()) if targets else set()
        result['connections']=route_components(board,router,set(targets),required_pad_ids=required) if required else 0
        result['graph']=getattr(router,'graph_diagnostic',{})
        closure=capture_local_closures(board,cfg,deadline=stop)
        after=snapshot(board)
        if before['footprints']!=after['footprints'] or any(after['tracks'].get(u)!=v for u,v in before['tracks'].items()):
            raise ValueError('Local controller wiring changed original source geometry')
        if time.monotonic()>=stop:raise TimeoutError('Local controller acceptance deadline exhausted')
        result.update(accepted=True,added_ids=sorted(set(after['tracks'])-set(before['tracks'])),local_closed=closure)
        return result
    except TimeoutError as error:
        result['reason']=str(error);return result
    finally:
        if not result['accepted']:
            for t in list(board.GetTracks()):
                if t.m_Uuid.AsString() not in before['tracks']:board.Delete(t)
            board.BuildConnectivity()
            if snapshot(board)!=before:raise ValueError('Local controller rollback changed source geometry')
            result['connections']=0
        result['elapsed_s']=time.monotonic()-start


def rebind_local_closures(before,after,proof,*,net_remap):
    """Carry recorded closure through label-only compose; never invent new pads."""
    result=deepcopy(proof);normalize=lambda v:json.loads(json.dumps(v));result['nets']={}
    def pads(state,net=None):
        return {d['uuid']:{'ref':f['ref'],'pad':d} for f in state['footprints'].values() for d in f['pads']
                if d['number'] and (net is None or d['net']==net)}
    current=pads(after)
    for old,row in proof['nets'].items():
        if normalize(row)!={'pads':normalize(pads(before,old))}:
            raise ValueError('Compose local closure omitted original physical pads')
        name=net_remap.get(old,old)
        if name in result['nets'] or any(n!=old and value==name for n,value in net_remap.items()):
            raise ValueError('Compose merged a recorded local closure with another net')
        updated=deepcopy(row)
        for uid,entry in updated['pads'].items():
            entry['pad']['net']=name
            if normalize(current.get(uid))!=normalize(entry):
                raise ValueError('Compose changed local closure source pad geometry or binding')
        if normalize(updated)!={'pads':normalize(pads(after,name))}:
            raise ValueError('Compose local closure gained or lost physical members')
        result['nets'][name]=updated
    return result
