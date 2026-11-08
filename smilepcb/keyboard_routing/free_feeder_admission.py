"""Independent native admission for a matrix routed from actual physical pads.

No generated matrix head or source-port receipt is evidence of closure here.
The retained native frame owns all footprints and immutable copper; only new,
unlocked matrix tracks/vias may exist outside that frame.
"""
from copy import deepcopy
import time
from types import SimpleNamespace

from smilepcb.keyboard_phases import ordered_nets


def closed_partial_shapes(graph, required_pads, delta, candidates):
    """Keep whole net deltas whose pads and every added object share a root."""
    from smilepcb.native_worker import p

    wanted = {net: set() for net in candidates}
    expected = {}
    for uid, net in required_pads.items():
        if net in wanted:
            wanted[net].add(uid)
            expected[uid] = (net, True)
    for uid, shape in delta.items():
        net = shape['net']
        if net in wanted:
            wanted[net].add(uid)
            expected[uid] = (net, False)
    observed = {net: set() for net in candidates}
    roots = {net: set() for net in candidates}
    for index, node in enumerate(graph['nodes']):
        item = node['item']
        uid = item.m_Uuid.AsString()
        net = node['net']
        if expected.get(uid) == (net, isinstance(item, p.PAD)):
            observed[net].add(uid)
            roots[net].add(graph['find'](index))
    closed = {net for net in candidates if observed[net] == wanted[net] and len(roots[net]) == 1}
    return {uid: shape for uid, shape in delta.items() if shape['net'] in closed}


def prove_free_matrix(board,cfg,retained,*,fixed_ports,physical_seal,rule_seal,deadline):
    """Prove the entire matrix, including all mutable copper, without edits."""
    return _prove_free_copper(board,cfg,retained,fixed_ports=fixed_ports,
        physical_seal=physical_seal,rule_seal=rule_seal,deadline=deadline,seed_nets=None)


def prove_free_seed(board,cfg,retained,*,seed_nets,fixed_ports,physical_seal,rule_seal,deadline):
    """Prove a partial routing seed; this never issues matrix acceptance."""
    return _prove_free_copper(board,cfg,retained,fixed_ports=fixed_ports,
        physical_seal=physical_seal,rule_seal=rule_seal,deadline=deadline,seed_nets=frozenset(seed_nets))


def _prove_free_copper(board,cfg,retained,*,fixed_ports,physical_seal,rule_seal,deadline,seed_nets):
    from smilepcb.native_worker import p,snapshot
    from .conditional_geometry import FrozenProofRouter,prove_copper
    from .interface_contract import _verify_recorded_exits
    from .interface_launches import reserve_launches
    from .native_graph import copper_graph
    from .safety import require_via_land_clearance

    def budget():
        if time.monotonic()>=deadline:raise TimeoutError('Free matrix admission deadline exhausted')

    budget()
    frame=snapshot(board)
    if (frame['footprints']!=retained['footprints'] or
            any(frame['tracks'].get(uid)!=shape for uid,shape in retained['tracks'].items())):
        raise ValueError('Free matrix changed the retained native frame')
    try:
        phases=dict(ordered_nets(cfg));nets=set(phases['rows']+phases['columns'])
        parts={part['ref']:part for part in cfg['parts']}
        if any(parts.get(f['ref'],{}).get('nets',{}).get(pad['number'])!=pad['net']
               for f in frame['footprints'].values() for pad in f['pads'] if pad['number']):
            raise ValueError('Free matrix CAD/model binding changed')
        if any(cfg['layout']['gpio'][role]!=cfg['qmk']['matrix_pins'][role] for role in ('rows','cols')):
            raise ValueError('Free matrix firmware mapping differs')
    except (KeyError,TypeError) as error:
        raise ValueError('Invalid free matrix physical/firmware model') from error
    if not nets:raise ValueError('Free matrix has no declared matrix nets')
    closed_nets=nets if seed_nets is None else seed_nets
    if seed_nets is not None and (not seed_nets or not seed_nets<nets):
        raise ValueError('Free feeder seed must cover a proper subset of the matrix')
    mutable={uid:shape for uid,shape in frame['tracks'].items() if uid not in retained['tracks']}
    if any(shape['class'] not in ('PCB_TRACK','PCB_VIA') or shape['locked'] or shape['net'] not in closed_nets
           for shape in mutable.values()):
        raise ValueError('Free matrix contains foreign, locked or unsupported mutable copper')
    pads={pad['uuid']:pad for f in retained['footprints'].values() for pad in f['pads']}
    required={uid:pad['net'] for uid,pad in pads.items() if pad['number'] and pad['net'] in nets}
    all_matrix_pads={uid:pad['net'] for uid,pad in pads.items() if pad['net'] in nets}
    ports=deepcopy(list(fixed_ports));seen_pads=set();seen_copper=set()
    try:
        for port in ports:
            net=port['net'];source=port['pad_ids'];owned=port['added_ids']
            if (net in nets or not source or not owned or
                    len(set(source))!=len(source) or len(set(owned))!=len(owned) or
                    seen_pads.intersection(source) or seen_copper.intersection(owned) or
                    any(pads.get(uid,{}).get('net')!=net for uid in source) or
                    any(retained['tracks'].get(uid,{}).get('net')!=net for uid in owned)):
                raise ValueError('Free matrix fixed port has invalid retained ownership')
            seen_pads.update(source);seen_copper.update(owned)
        _verify_recorded_exits(ports,retained['tracks'])
    except (KeyError,TypeError) as error:
        raise ValueError('Invalid free matrix fixed-port metadata') from error

    native=SimpleNamespace(board=board)
    def physical(excluded_ids=()):return FrozenProofRouter._physical_state(native,excluded_ids=excluded_ids)
    def rules():return FrozenProofRouter._rule_state(native)
    budget()
    if physical(mutable)!=physical_seal:raise ValueError('Free matrix retained physical seal differs')
    if rules()!=rule_seal:raise ValueError('Free matrix native rules differ')
    whole_physical=physical();timestamp=board.GetTimeStamp()
    budget()
    # Include all physical pad/copper nets, even ones absent from fixed ports:
    # launch retirement and the creation oracle must see the same complete world.
    covered=nets|{pad['net'] for pad in pads.values()}|{shape['net'] for shape in frame['tracks'].values()}
    covered.update(zone.GetNetname() for zone in board.Zones())
    try:
        with reserve_launches(ports):
            graph=copper_graph(board,covered,deadline)
            budget()
            actual={};matrix_components={net:set() for net in nets};numbered_components={net:set() for net in nets}
            indices={};numbered={};matrix_pads={};observed_mutable={}
            for index,node in enumerate(graph['nodes']):
                budget()
                item=node['item'];uid=item.m_Uuid.AsString();net=node['net']
                if net!=item.GetNetname():raise ValueError('Free matrix graph net differs from native copper')
                # Filled zones may have several islands with one native UUID.
                if not node.get('zone',False):
                    if uid in indices:raise ValueError('Free matrix graph repeats native copper')
                    indices[uid]=index
                    actual[uid]=net
                if isinstance(item,p.PAD) and net in nets:
                    matrix_pads[uid]=net
                    matrix_components[net].add(graph['find'](index))
                    if item.GetNumber():
                        numbered[uid]=net;numbered_components[net].add(graph['find'](index))
                if uid in mutable:
                    observed_mutable[uid]=net
                    if net not in nets:raise ValueError('Free matrix graph changed a mutable net')
                    matrix_components[net].add(graph['find'](index))
            expected={uid:pad['net'] for uid,pad in pads.items()}
            expected.update((uid,shape['net']) for uid,shape in frame['tracks'].items())
            if actual!=expected:
                raise ValueError('Free matrix graph omitted or changed the native conductor inventory')
            if numbered!=required or matrix_pads!=all_matrix_pads:
                raise ValueError('Free matrix graph omitted or changed an actual matrix pad')
            if observed_mutable!={uid:shape['net'] for uid,shape in mutable.items()}:
                raise ValueError('Free matrix graph omitted or changed mutable copper')
            groups={net:len(numbered_components[net]) for net in sorted(closed_nets)}
            if any(groups[net]!=1 or len(matrix_components[net])!=1 for net in closed_nets):
                raise ValueError('Free matrix has disconnected pads or orphan mutable components')
            for port in ports:
                ids=port['pad_ids']+port['added_ids']
                if any(actual.get(uid)!=port['net'] for uid in ids):
                    raise ValueError('Free matrix graph lost a retained fixed port')
                if len({graph['find'](indices[uid]) for uid in ids})!=1:
                    raise ValueError('Free matrix fixed port is disconnected from its physical pads')
            budget()
            checked=prove_copper(board,mutable,graph,covered,deadline)
            budget()
            if checked!=len(mutable):raise ValueError('Free matrix creation proof omitted mutable copper')
            require_via_land_clearance(board,deadline=deadline)
            budget()
    finally:
        if (snapshot(board)!=frame or board.GetTimeStamp()!=timestamp or
                rules()!=rule_seal or physical()!=whole_physical or physical(mutable)!=physical_seal):
            raise ValueError('Free matrix proof changed its native frame')
        budget()
    proof={'matrix_groups':groups,'creation_checked_items':checked,
           'numbered_pad_count':sum(net in closed_nets for net in required.values()),
           'mutable_copper_count':len(mutable)}
    if seed_nets is None:proof['accepted']=True
    return proof
