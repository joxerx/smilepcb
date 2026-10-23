"""Native-feedback policy; no fixture net names, pin numbers or route coordinates."""
import copy


def fixed_copper_ids(board,protected_ids):
    """Explicit protection extends native locks; omission never unlocks copper."""
    tracks={t.m_Uuid.AsString():t for t in board.GetTracks()}
    protected=set(protected_ids)
    if protected-tracks.keys():raise ValueError('Protected source copper is missing')
    return protected|{uid for uid,item in tracks.items() if item.IsLocked()}


def prepare_routing(board, *, preserve_routing):
    """Choose fixed copper on an already isolated in-memory candidate.

    Explicit KiCad locks are honored in either mode. Persistence and source
    protection belong to the caller; this function never saves a board.
    """
    if type(preserve_routing) is not bool:
        raise ValueError('Routing preservation mode must be boolean')
    tracks=list(board.GetTracks())
    protected=[t.m_Uuid.AsString() for t in tracks if preserve_routing or t.IsLocked()]
    removed=[]
    for track in tracks:
        uid=track.m_Uuid.AsString()
        if uid not in protected:
            removed.append(uid);board.Delete(track)
    if removed:
        board.BuildConnectivity()
        for zone in board.Zones():
            if not zone.GetIsRuleArea():zone.SetNeedRefill(True)
    return {'preserve_routing':preserve_routing,
            'protected_ids':sorted(protected),'removed_ids':sorted(removed)}


def residual_nets(drc, snapshot):
    if drc.get('violations') or drc.get('schematic_parity'):
        raise ValueError('Recovery requires no ordinary DRC or schematic parity violations')
    nets = {uid: item['net'] for uid, item in snapshot['tracks'].items()}
    for fp in snapshot['footprints'].values():
        nets.update({pad['uuid']: pad['net'] for pad in fp['pads']})
    residual = set()
    for gap in drc['unconnected_items']:
        try:
            members = {nets[item['uuid']] for item in gap['items']}
        except KeyError as error:
            raise ValueError('DRC endpoint UUID is not in the native snapshot') from error
        if len(members) != 1 or not next(iter(members)):
            raise ValueError('Unconnected endpoints must identify one nonempty net')
        residual.update(members)
    return sorted(residual)


def prioritize_dsn(text, priority_nets):
    """Split native network classes; leave every byte of placement/wiring intact."""
    import sexpdata as sx
    start = text.index('\n  (network\n') + 1
    end = text.index('\n  (wiring', start)
    network = sx.loads(text[start:end])
    wanted = set(priority_nets)
    if not wanted:
        raise ValueError('Priority net set cannot be empty')
    seen, ignored, result = set(), [], [network[0]]
    occupied = {str(node[1]) for node in network[1:] if str(node[0]) == 'class'}
    for node in network[1:]:
        if str(node[0]) != 'class':
            result.append(node)
            continue
        cut = next((i for i in range(2, len(node)) if isinstance(node[i], list)), len(node))
        members, attrs = node[2:cut], node[cut:]
        names = {str(n) for n in members}
        if seen.intersection(names):
            raise ValueError('Duplicate net class membership')
        seen.update(names)
        active = [n for n in members if str(n) in wanted]
        deferred = [n for n in members if str(n) not in wanted]
        if active:
            result.append([node[0], node[1], *active, *copy.deepcopy(attrs)])
        if deferred:
            index = len(ignored) + 1
            label = f'SmilePCBDeferred{index}'
            while label in occupied:
                index += 1
                label = f'SmilePCBDeferred{index}'
            occupied.add(label)
            ignored.append(label)
            result.append([node[0], sx.Symbol(label), *deferred, *copy.deepcopy(attrs)])
    if not wanted <= seen:
        raise ValueError('Priority net missing from DSN classes')
    return text[:start] + '  ' + sx.dumps(result) + text[end:], ignored


def reset_candidates(seed, current):
    if seed['footprints'] != current['footprints']:
        raise ValueError('Protected component or pad changed')
    for uid, item in seed['tracks'].items():
        if current['tracks'].get(uid) != item:
            raise ValueError('Protected copper changed or disappeared')
    return sorted(set(current['tracks']) - set(seed['tracks']))


def allow_trace_junctions(text, nets=None):
    """Permit splitting temporary DSN wires, guarded by exact coverage on import.

    Freerouting 2.5.0 reads the keyword shove_fixed (not its writer's
    shoveFixed spelling). Vias stay system-fixed. Actual PCB lock flags stay.
    """
    import re
    import sexpdata as sx
    start = text.index('\n  (wiring')
    def convert(match):
        line = match.group(0)
        wire = sx.loads(line)
        net = next(str(n[1]) for n in wire[1:] if isinstance(n, list) and str(n[0]) == 'net')
        return line.replace('(type fix)', '(type shove_fixed)') if nets is None or net in nets else line
    return text[:start] + re.sub(r'(?m)^    \(wire[^\n]+', convert, text[start:])


def starved_thermal_pads(drc, snapshot):
    """Only reported pad UUIDs; no pin names or relaxation of thermal rules."""
    pads = {p['uuid'] for f in snapshot['footprints'].values() for p in f['pads']}
    selected = set()
    for violation in drc['violations']:
        if violation['type'] != 'starved_thermal':
            raise ValueError('Ordinary DRC violation cannot be repaired by thermal rotation')
        matches = {item['uuid'] for item in violation['items']} & pads
        if len(matches) != 1:
            raise ValueError('Starved thermal must identify one native pad UUID')
        selected.update(matches)
    return sorted(selected)


def pad_zone_thermal_pads(drc, snapshot, context):
    """One thermal orientation trial for a native pad-to-zone open only.

    Eligibility comes from native PTH/connection settings, never descriptions,
    reference names, pin numbers, net names or coordinates. Unknown endpoints
    remain for the strict residual-net gate to reject.
    """
    if drc.get('schematic_parity') or any(v['type'] != 'starved_thermal' for v in drc['violations']):
        raise ValueError('Thermal retry requires no ordinary DRC or parity violations')
    pads = {p['uuid']: p['net'] for f in snapshot['footprints'].values() for p in f['pads']}
    eligible, zones = set(context['eligible_pads']), context['zones']
    selected = set()
    for gap in drc['unconnected_items']:
        ids = [item['uuid'] for item in gap['items']]
        if len(ids) != 2:
            continue
        found_pads, found_zones = set(ids) & eligible, set(ids) & zones.keys()
        if len(found_pads) == len(found_zones) == 1:
            pad, zone = next(iter(found_pads)), next(iter(found_zones))
            if not pads[pad] or pads[pad] != zones[zone]:
                raise ValueError('Pad and zone must identify the same net')
            selected.add(pad)
    return sorted(selected)
