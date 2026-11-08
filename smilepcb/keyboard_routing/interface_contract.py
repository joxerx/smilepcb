"""Durable native proof for pre-compose ordered controller interfaces."""
import json,time
from copy import deepcopy
from pathlib import Path
from smilepcb.keyboard_phases import ordered_nets
MARKER='.smilepcb-interfaces.json'


def _pins(cfg):return {role:list(cfg['layout']['gpio'][role]) for role in ('rows','cols')}


def mcu_signal_nets(cfg):
    """Physical MCU functions that need access beyond USB and native planes."""
    part=next(q for q in cfg['parts'] if q['ref']=='U_MCU')
    return {net for number,net in part['nets'].items()
            if number not in part.get('no_connect',()) and net and not net.startswith(('unconnected-','NC_'))}-set(cfg['plane_nets'])-{'USB_DM','USB_DP'}


def capture_interfaces(board,cfg,ports,*,deadline,local_closed=None):
    from smilepcb.native_worker import snapshot
    live=snapshot(board);owned=[u for q in ports for u in q['added_ids']]
    if len(owned)!=len(set(owned)) or not set(owned)<=set(live['tracks']):raise ValueError('Ambiguous owned controller copper')
    pad_ids={u for q in ports for u in q['pad_ids']}
    pads={d['uuid']:d for f in live['footprints'].values() for d in f['pads'] if d['uuid'] in pad_ids}
    receipt={'schema':1,'ports':deepcopy(ports),'matrix_pins':_pins(cfg),
             'tracks':{u:live['tracks'][u] for u in owned},'pads':pads,
             'controller_frame':[{k:f[k] for k in ('ref','position','rotation','layer','locked')} for f in live['footprints'].values() if f['ref']=='U_MCU']}
    if local_closed is not None:receipt['local_closed']=deepcopy(local_closed)
    verify_interfaces(board,cfg,receipt,deadline=deadline)
    return receipt


def verify_interfaces(board,cfg,receipt,*,deadline,retire_completed=False):
    from smilepcb.native_worker import snapshot
    from .native_graph import copper_graph
    if time.monotonic()>=deadline:raise TimeoutError('Controller interface proof deadline exhausted')
    if type(retire_completed) is not bool:raise ValueError('Controller interface retirement must be boolean')
    phases=dict(ordered_nets(cfg));matrix=set(phases['rows'])|set(phases['columns']);full=cfg.get('routing_preparation',{}).get('mode')=='ordered-banks-v2';names=mcu_signal_nets(cfg) if full else matrix
    if not isinstance(receipt,dict):raise ValueError('Invalid controller interface receipt')
    if receipt.get('schema')!=1 or receipt.get('matrix_pins')!=_pins(cfg):raise ValueError('Controller interface model changed')
    graph=None;covered={}
    if 'local_closed' in receipt:
        if not full or cfg.get('routing_preparation',{}).get('generation_profile')!='dsn-process-v2':raise ValueError('Local closure requires a v2 interface model')
        from .controller_local import verify_local_closures
        graph=copper_graph(board,names,deadline,include_zones=False)
        covered=verify_local_closures(board,cfg,receipt['local_closed'],deadline=deadline,graph=graph)
        names-=set(covered)
    ports=receipt['ports']
    if {q['net'] for q in ports}!=names or (not full and len(ports)!=len(names)):raise ValueError('Controller interface omitted a required MCU signal')
    live=snapshot(board);normalize=lambda value:json.loads(json.dumps(value))
    if not retire_completed:
        for uid,shape in receipt['tracks'].items():
            if normalize(live['tracks'].get(uid))!=shape:raise ValueError('Controller interface copper changed: '+uid)
    pad_ids={u for q in ports for u in q['pad_ids']};owned=[u for q in ports for u in q['added_ids']]
    if len(owned)!=len(set(owned)) or set(owned)!=set(receipt['tracks']) or pad_ids!=set(receipt['pads']):raise ValueError('Controller interface ownership mismatch')
    chips=[f for f in board.GetFootprints() if f.GetReference()=='U_MCU']
    if len(chips)!=1:raise ValueError('Controller interface requires exactly one physical MCU')
    part=next(q for q in cfg['parts'] if q['ref']=='U_MCU');expected={n:set() for n in names}
    numbers=([cfg['mcu_gpio_pads'][g] for role in ('rows','cols') for g in cfg['layout']['gpio'][role]]
             if not full else [n for n,net in part['nets'].items() if net in names])
    for number in numbers:
        net=part['nets'][number];aliases=[d for d in chips[0].Pads() if d.GetNumber()==number]
        if not aliases or any(d.GetNetname()!=net for d in aliases):raise ValueError('Controller interface physical pin binding differs from the model')
        expected[net].update(d.m_Uuid.AsString() for d in aliases)
    mcu_ids={d.m_Uuid.AsString() for d in chips[0].Pads()};seen=set()
    all_pads={d.m_Uuid.AsString():d for f in board.GetFootprints() for d in f.Pads() if d.GetNumber()}
    parts={q['ref']:q for q in cfg['parts'] if q['region']=='controller'}
    for q in ports:
        own=set(q['pad_ids']);actual=own&mcu_ids
        if not actual or not actual<=expected[q['net']] or seen&own:raise ValueError('Controller interface has ambiguous physical MCU pad aliases')
        seen.update(own)
        for uid in own-mcu_ids:
            d=all_pads.get(uid);model=parts.get(d.GetParentFootprint().GetReference()) if d is not None else None
            if not full or model is None or model['nets'].get(d.GetNumber())!=q['net'] or d.GetNetname()!=q['net']:raise ValueError('Controller interface includes an unbound auxiliary pad')
    if (pad_ids&mcu_ids)!=set().union(*expected.values()):raise ValueError('Controller interface omitted a physical MCU pad alias')
    pads={d['uuid']:d for f in live['footprints'].values() for d in f['pads'] if d['uuid'] in pad_ids}
    frame=[{k:f[k] for k in ('ref','position','rotation','layer','locked')} for f in live['footprints'].values() if f['ref']=='U_MCU']
    if normalize(pads)!=receipt['pads'] or normalize(frame)!=receipt['controller_frame']:raise ValueError('Physical controller interface moved')
    if retire_completed:_verify_recorded_exits(ports,receipt['tracks'])
    if graph is None:graph=copper_graph(board,names,deadline)
    all_ports=ports
    if retire_completed:
        from .interface_launches import resolved_interfaces,interface_identity
        resolved=resolved_interfaces(ports,set(),graph)
        ports=[q for q in ports if interface_identity(q) not in resolved]
        owned=[u for q in ports for u in q['added_ids']]
        for uid in owned:
            if normalize(live['tracks'].get(uid))!=receipt['tracks'][uid]:raise ValueError('Controller interface copper changed: '+uid)
    ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
    from .local_router import vec
    from smilepcb.native_worker import p
    for q in ports:
        tip=q.get('terminal_id') or q.get('via_id')
        if tip not in q['added_ids'] or tip not in ids:raise ValueError('Controller terminal is absent')
        node=graph['nodes'][ids[tip]];point=p.SHAPE_CIRCLE(vec(q['exit_nm']),0)
        if node['net']!=q['net'] or q['exit_layer'] not in node['shapes'] or not p.SHAPE.Collide(point,node['shapes'][q['exit_layer']],0):raise ValueError('Controller terminal no longer touches its recorded exit')
        item=node['item'];exit_nm=tuple(q['exit_nm']);origin=tuple(q.get('heading_origin_nm',()))
        if item.GetClass()=='PCB_TRACK':
            ends=[(item.GetStart().x,item.GetStart().y),(item.GetEnd().x,item.GetEnd().y)]
            if exit_nm not in ends or origin!=ends[1-ends.index(exit_nm)] or origin==exit_nm:raise ValueError('Controller interface exit/heading is not its exposed segment endpoint')
        elif isinstance(item,p.PCB_VIA):
            leads=[n['item'] for n in graph['nodes'] if n['item'].m_Uuid.AsString() in q['added_ids'] and n['item'].GetClass()=='PCB_TRACK' and n['item'].IsOnLayer(q['exit_layer'])]
            if not any({(t.GetStart().x,t.GetStart().y),(t.GetEnd().x,t.GetEnd().y)}=={exit_nm,origin} and exit_nm!=origin for t in leads):raise ValueError('Controller via has no physical departure heading')
        else:raise ValueError('Invalid controller interface terminal kind')
        if q.get('kind')=='same_layer':
            if q.get('via_id') is not None or q.get('via_nm') is not None or isinstance(item,p.PCB_VIA):raise ValueError('Same-face controller interface invents a via')
        elif q.get('kind')=='via':
            uid=q.get('via_id');via=graph['nodes'][ids[uid]]['item'] if uid in ids else None
            if uid not in q['added_ids'] or not isinstance(via,p.PCB_VIA) or tuple(q.get('via_nm') or ())!=(via.GetPosition().x,via.GetPosition().y) or via.GetNetname()!=q['net'] or not all(via.IsOnLayer(l) for l in (p.F_Cu,p.B_Cu)):raise ValueError('Controller interface via metadata differs from its actual plated via')
        else:raise ValueError('Unknown controller interface kind')
        root=graph['find'](ids[tip])
        if any(u not in ids or graph['find'](ids[u])!=root or graph['nodes'][ids[u]]['net']!=q['net'] for u in q['pad_ids']):raise ValueError('Controller interface lost a physical pad')
    if time.monotonic()>=deadline:raise TimeoutError('Controller interface proof deadline exhausted')
    result={'accepted':True,'native_contacts_exact':True,'ports':deepcopy(ports),'owned_ids':sorted(owned)}
    if 'local_closed' in receipt:result['local_closed_nets']=sorted(covered)
    if retire_completed:
        result['source_tracks']={uid:deepcopy(receipt['tracks'][uid]) for uid in owned}
        result['ports']=deepcopy(all_ports)
        result['resolved']=sorted(resolved)
        for q in result['ports']:
            q['source_shapes']={uid:deepcopy((receipt['tracks'] if uid in receipt['tracks'] else receipt['pads'])[uid]) for uid in q['added_ids']+q['pad_ids']}
    return result


def _verify_recorded_exits(ports,tracks):
    """Retirement does not authorize inventing a different original receipt."""
    from smilepcb.native_worker import p
    for q in ports:
        owned=q['added_ids'];tip=q.get('terminal_id') or q.get('via_id')
        if tip not in owned or any(tracks[u]['net']!=q['net'] for u in owned):
            raise ValueError('Controller interface recorded ownership differs')
        shape=tracks[tip];exit_nm=tuple(q['exit_nm']);origin=tuple(q.get('heading_origin_nm',()))
        if shape['class']=='PCB_TRACK':
            ends=[tuple(shape['start']),tuple(shape['end'])]
            if (shape['layer']!=q['exit_layer'] or exit_nm not in ends or
                    origin!=ends[1-ends.index(exit_nm)] or origin==exit_nm):
                raise ValueError('Controller interface recorded exit/heading differs')
        elif shape['class']=='PCB_VIA':
            if exit_nm!=tuple(shape['start']) or not any(
                    tracks[u]['class']=='PCB_TRACK' and tracks[u]['layer']==q['exit_layer'] and
                    {tuple(tracks[u]['start']),tuple(tracks[u]['end'])}=={exit_nm,origin} and exit_nm!=origin
                    for u in owned):raise ValueError('Controller interface recorded via heading differs')
        else:raise ValueError('Invalid recorded controller terminal kind')
        if q.get('kind')=='same_layer':
            if q.get('via_id') is not None or q.get('via_nm') is not None or shape['class']=='PCB_VIA':
                raise ValueError('Same-face controller interface invents a recorded via')
        elif q.get('kind')=='via':
            uid=q.get('via_id');via=tracks.get(uid,{})
            if (uid not in owned or via.get('class')!='PCB_VIA' or
                    tuple(q.get('via_nm') or ())!=tuple(via['start']) or
                    {via['top'],via['bottom']}!={p.F_Cu,p.B_Cu}):
                raise ValueError('Controller interface recorded via metadata differs')
        else:raise ValueError('Unknown recorded controller interface kind')


def rebind_interfaces(before,after,receipt,*,net_remap,paths):
    """Relabel authentic source evidence; never recapture retired copper.

    This proves the compose delta, not electrical closure. The caller must
    verify_interfaces on the resulting native board before publication, with
    retirement enabled only at a verified full-matrix boundary.
    """
    normalize=lambda value:json.loads(json.dumps(value))
    before,after,receipt=map(normalize,(before,after,receipt))
    if not isinstance(receipt,dict) or receipt.get('schema')!=1:
        raise ValueError('Invalid controller interface receipt for compose')
    expected=deepcopy(before)
    names={t['net'] for t in before['tracks'].values()}|{t['net'] for t in receipt['tracks'].values()}
    for footprint in expected['footprints'].values():
        ref=footprint['ref']
        if ref not in paths:raise ValueError('Composition omitted a physical footprint path')
        footprint['path']=paths[ref]
        for pad in footprint['pads']:
            names.add(pad['net']);pad['net']=net_remap.get(pad['net'],pad['net'])
    for track in expected['tracks'].values():track['net']=net_remap.get(track['net'],track['net'])
    if expected!=after:raise ValueError('Composition changed undeclared physical geometry or bindings')
    if len({net_remap.get(n,n) for n in names})!=len(names):
        raise ValueError('Composition merged distinct controller signals')
    source_pads={d['uuid']:d for f in before['footprints'].values() for d in f['pads']}
    frame=[{k:f[k] for k in ('ref','position','rotation','layer','locked')}
           for f in before['footprints'].values() if f['ref']=='U_MCU']
    if frame!=receipt['controller_frame'] or any(source_pads.get(uid)!=pad for uid,pad in receipt['pads'].items()):
        raise ValueError('Composition source differs from the recorded controller interface')
    result=deepcopy(receipt)
    for shape in list(result['tracks'].values())+list(result['pads'].values()):
        shape['net']=net_remap.get(shape['net'],shape['net'])
    for port in result['ports']:
        port['net']=net_remap.get(port['net'],port['net'])
        for shape in port.get('source_shapes',{}).values():shape['net']=net_remap.get(shape['net'],shape['net'])
    if 'local_closed' in result:
        from .controller_local import rebind_local_closures
        result['local_closed']=rebind_local_closures(before,after,receipt['local_closed'],net_remap=net_remap)
    return result


def load_interfaces(project):
    from .generated_seed import _read
    path=Path(project)/MARKER
    if not path.exists() and not path.is_symlink():return None
    value=json.loads(_read(path,8*1024*1024))
    if not isinstance(value,dict) or not value:raise ValueError('Invalid empty controller interface receipt')
    return value


def load_verified_interfaces(board,cfg,project,*,deadline,retire_completed=False):
    mode=cfg.get('routing_preparation',{}).get('mode','legacy-via')
    if mode not in ('legacy-via','ordered-banks-v1','ordered-banks-v2'):raise ValueError('Unknown controller interface preparation mode')
    receipt=load_interfaces(project)
    if receipt is None:
        if mode in ('ordered-banks-v1','ordered-banks-v2'):raise ValueError('Missing required ordered controller interface receipt')
        return None
    return verify_interfaces(board,cfg,receipt,deadline=deadline,retire_completed=retire_completed)
