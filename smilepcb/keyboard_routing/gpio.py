"""Apply geometric matrix allocation before any matrix copper is routed."""
from collections import defaultdict
from smilepcb.native_worker import p
from smilepcb.controller_gpio import assign_matrix_gpio
from smilepcb.keyboard_layout import ROW_PINS,COL_PINS


def apply_matrix_gpio(board,cfg,*,excluded_gpios=(),banked=False,preferred_banks=(),uncertain_gpios=(),unwitnessed_gpios=()):
    chip=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU')
    part=next(part for part in cfg['parts'] if part['ref']=='U_MCU')
    names={part['nets'][cfg['mcu_gpio_pads'][g]] for group in ('rows','cols') for g in cfg['layout']['gpio'][group]}
    assignable={part['nets'][number] for gpio,number in cfg['mcu_gpio_pads'].items()
                if gpio in set(ROW_PINS)|set(COL_PINS) and
                (part['nets'][number] in names or part['nets'][number].startswith('NC_') or
                 number in part.get('no_connect',()) and part['nets'][number].startswith('unconnected-'))}
    if any(t.GetNetname() in assignable for t in board.GetTracks()):
        raise ValueError('Matrix GPIO allocation must precede editable matrix copper')
    pads={pad.GetNumber():pad for pad in chip.Pads() if pad.GetNumber()}
    locations={gpio:(pads[number].GetPosition().x,pads[number].GetPosition().y)
               for gpio,number in cfg['mcu_gpio_pads'].items() if number in pads}
    targets=defaultdict(list)
    for fp in board.GetFootprints():
        if fp.GetReference()=='U_MCU':continue
        for pad in fp.Pads():
            if pad.GetNumber() and pad.GetNetname() in names:
                targets[pad.GetNetname()].append((pad.GetPosition().x,pad.GetPosition().y))
    centroids={net:tuple(round(sum(v[i] for v in rows)/len(rows)) for i in (0,1)) for net,rows in targets.items()}
    banks=None
    if banked:
        banks={s:[] for s in ('N','E','S','W')};center=chip.GetPosition();safe=set(ROW_PINS)|set(COL_PINS)
        for gpio,point in locations.items():
            number=cfg['mcu_gpio_pads'][gpio]
            if gpio not in safe or part['nets'][number] not in assignable:continue
            dx,dy=point[0]-center.x,point[1]-center.y
            side=('E' if dx>0 else 'W') if abs(dx)>abs(dy) else ('S' if dy>0 else 'N')
            banks[side].append(gpio)
        for side,rows in banks.items():rows.sort(key=lambda g:(locations[g][0 if side in ('N','S') else 1],g))
    updated,receipt=assign_matrix_gpio(cfg,locations,centroids,excluded_gpios=excluded_gpios,banks=banks,preferred_banks=preferred_banks,uncertain_gpios=uncertain_gpios,unwitnessed_gpios=unwitnessed_gpios)
    new_part=next(part for part in updated['parts'] if part['ref']=='U_MCU')
    for pad in chip.Pads():
        number=pad.GetNumber()
        if not number:continue
        name=new_part['nets'][number];net=board.FindNet(name)
        if net is None:net=p.NETINFO_ITEM(board,name);board.Add(net)
        pad.SetNet(net)
    board.BuildConnectivity()
    return updated,receipt


def apply_exit_assignment(board,cfg,ports,*,deadline,net_mapping=None,assignment_scope='within-role'):
    """Synchronize safe GPIO labels with final lane order before composition.

    Only owned provisional leads attached exclusively to their MCU pads may
    change net identity. No field-connected, locked or unrelated copper is
    relabeled. The caller publishes the returned shared model atomically.
    Explicit matrix scope permits cross-role binding only when the complete
    actual matrix GPIO/pad inventory is safe and represented by these ports.
    """
    import time
    from copy import deepcopy
    from smilepcb.native_worker import snapshot
    from smilepcb.keyboard_phases import ordered_nets
    from .bank_geometry import exit_permutation
    from .native_graph import copper_graph
    from .completion import pad_partitions
    if time.monotonic()>=deadline:raise TimeoutError('Final GPIO assignment deadline exhausted')
    phases=dict(ordered_nets(cfg));families={n:f for f in ('rows','columns') for n in phases[f]};names=set(families)
    chip=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU');all_pads=list(chip.Pads())
    targets=defaultdict(list)
    for fp in board.GetFootprints():
        if fp.GetReference()=='U_MCU':continue
        for pad in fp.Pads():
            if pad.GetNumber() and pad.GetNetname() in names:targets[pad.GetNetname()].append((pad.GetPosition().x,pad.GetPosition().y))
    targets={n:tuple(sum(q[i] for q in rows)/len(rows) for i in (0,1)) for n,rows in targets.items()}
    mapping=exit_permutation(ports,targets,families) if net_mapping is None else dict(net_mapping)
    from smilepcb.controller_gpio import validate_exit_mapping
    mapping=validate_exit_mapping(mapping,families,[q['net'] for q in ports],assignment_scope=assignment_scope)
    before=snapshot(board);tracks={t.m_Uuid.AsString():t for t in board.GetTracks()};owned={};graph=copper_graph(board,set(mapping),deadline)
    ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])};mcu_ids={d.m_Uuid.AsString():d for d in all_pads if d.GetNumber()}
    safe=set(ROW_PINS)|set(COL_PINS);gpios={num:g for g,num in cfg['mcu_gpio_pads'].items()}
    if assignment_scope=='matrix':
        selected=[g for role in ('rows','cols') for g in cfg['layout']['gpio'][role]]
        if len(selected)!=len(set(selected)) or not set(selected)<=safe:
            raise ValueError('Matrix assignment includes duplicate or reserved GPIO capabilities')
        numbers={cfg['mcu_gpio_pads'][g] for g in selected}
        if len(numbers)!=len(selected):raise ValueError('Matrix GPIO capabilities alias one physical pad number')
        expected={u for u,d in mcu_ids.items() if d.GetNumber() in numbers}
        actual={u for u,d in mcu_ids.items() if d.GetNetname() in names}
        claimed=[u for q in ports for u in q.get('pad_ids',[])]
        part=next(q for q in cfg['parts'] if q['ref']=='U_MCU')
        if (expected!=actual or set(claimed)!=expected or len(claimed)!=len(set(claimed))
                or {mcu_ids[u].GetNumber() for u in expected}!=numbers
                or any(mcu_ids[u].GetNetname()!=part['nets'][mcu_ids[u].GetNumber()] for u in expected)):
            raise ValueError('Matrix source inventory omits or changes an actual MCU pad alias')
    for port in ports:
        net=port['net'];source_ids=port.get('added_ids');pad_ids=port.get('pad_ids');terminal=port.get('terminal_id') or port.get('via_id')
        if not source_ids or not pad_ids or terminal not in source_ids or not set(source_ids)<=set(tracks) or set(source_ids)&set(owned):
            raise ValueError('Invalid owned GPIO port inventory')
        if not set(pad_ids)<=set(mcu_ids) or any(mcu_ids[u].GetNetname()!=net for u in pad_ids):
            raise ValueError('GPIO port does not bind its actual MCU pads')
        if any(tracks[u].GetNetname()!=net for u in source_ids):raise ValueError('Owned GPIO copper has a different signal')
        owned.update((u,net) for u in source_ids)
        if mapping[net]!=net or assignment_scope=='matrix':
            if any(tracks[u].IsLocked() for u in source_ids):raise ValueError('Cannot relabel locked GPIO copper')
            if any(gpios.get(mcu_ids[u].GetNumber()) not in safe for u in pad_ids):raise ValueError('Cannot relabel reserved MCU functions')
            allowed=set(source_ids)|set(pad_ids)
            if terminal not in ids or not set(pad_ids)<=set(ids):raise ValueError('Missing live GPIO terminal')
            root=graph['find'](ids[terminal]);members={n['item'].m_Uuid.AsString() for i,n in enumerate(graph['nodes']) if graph['find'](i)==root}
            if members-allowed or assignment_scope=='matrix' and members!=allowed:
                raise ValueError('Matrix source must be exactly its owned component; no unowned, field or disconnected copper')
            if any(graph['find'](ids[u])!=root for u in pad_ids):raise ValueError('GPIO pad lost its planned lane')
    original={frozenset(g) for g in pad_partitions(board,deadline)};updated=deepcopy(cfg);new_ports=deepcopy(ports)
    part=next(q for q in updated['parts'] if q['ref']=='U_MCU');old_pad_nets={d.m_Uuid.AsString():d.GetNetname() for d in all_pads if d.GetNumber()}
    try:
        for pad in all_pads:
            if not pad.GetNumber() or pad.GetNetname() not in mapping:continue
            net=mapping[pad.GetNetname()];part['nets'][pad.GetNumber()]=net;pad.SetNet(board.FindNet(net))
        for uid,net in owned.items():tracks[uid].SetNet(board.FindNet(mapping[net]))
        for port in new_ports:port['net']=mapping[port['net']]
        inverse={part['nets'][number]:gpio for gpio,number in updated['mcu_gpio_pads'].items() if number in part['nets']}
        for group,role in (('rows','rows'),('cols','columns')):
            values=[inverse[net] for net in phases[role]];updated['layout']['gpio'][group]=values;updated['qmk']['matrix_pins'][group]=values[:]
        board.BuildConnectivity();after=snapshot(board)
        for uid,old in before['tracks'].items():
            expected=dict(old)
            if uid in owned:expected['net']=mapping[owned[uid]]
            if after['tracks'].get(uid)!=expected:raise ValueError('GPIO assignment changed non-owned routing geometry')
        for uid,old in before['footprints'].items():
            expected=deepcopy(old)
            if old['ref']=='U_MCU':
                for pad in expected['pads']:pad['net']=mapping.get(pad['net'],pad['net'])
            if after['footprints'].get(uid)!=expected:raise ValueError('GPIO assignment changed physical source geometry')
        if {frozenset(g) for g in pad_partitions(board,deadline)}!=original:
            raise ValueError('GPIO assignment changed existing physical contacts')
    except BaseException:
        for pad in all_pads:
            if pad.GetNumber():pad.SetNet(board.FindNet(old_pad_nets[pad.m_Uuid.AsString()]))
        for uid,net in owned.items():tracks[uid].SetNet(board.FindNet(net))
        board.BuildConnectivity();raise
    return updated,{'accepted':True,'assignment_scope':assignment_scope,'mapping':mapping,'ports':new_ports,'owned_ids':sorted(owned),'placements_exact':True,'native_pad_partitions_exact':True,'shared_cad_qmk_model':True}
