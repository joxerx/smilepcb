"""Limit a placement-feasibility job to the controller's real terminals.

The exchange still contains every footprint/pad/keepout as a hard geometric
obstacle. Each boundary net retains all controller pads and one actual external
component. Native PCB connectivity, not this reduced exchange, decides success.
"""
import math
import sexpdata as sx


def controller_subset_dsn(text,parts,*,plane_nets=()):
    positions={part['ref']:part['pcb'] for part in parts if not part['ref'].startswith('#')}
    refs={part['ref'] for part in parts if part['region']=='controller' and not part['ref'].startswith('#')}
    if not refs or len(positions)!=sum(not p['ref'].startswith('#') for p in parts):
        raise ValueError('Controller exchange needs unique real component references')
    ordered=sorted(positions,key=lambda ref:(-len(ref),ref))
    def owner(pin):
        found=next((ref for ref in ordered if str(pin).startswith(ref+'-')),None)
        if found is None:raise ValueError('Unknown DSN pin owner: '+str(pin))
        return found
    start=text.index('\n  (network');end=text.index('\n  (wiring',start)
    network=sx.loads(text[start:end],nil=None,true=None,false=None);routed=[];selected={}
    for row in network[1:]:
        if str(row[0])!='net':continue
        pins=next(v for v in row[2:] if isinstance(v,list) and str(v[0])=='pins')
        owners={str(pin):owner(pin) for pin in pins[1:]}
        local=[pin for pin in pins[1:] if owners[str(pin)] in refs]
        outside={owners[str(pin)] for pin in pins[1:] if owners[str(pin)] not in refs}
        if not local or len(local)==1 and not outside or str(row[1]) in plane_nets:continue
        routed.append(str(row[1]))
        if outside:
            anchors=[positions[owners[str(pin)]] for pin in local]
            center=tuple(sum(p[i] for p in anchors)/len(anchors) for i in (0,1))
            chosen=min(outside,key=lambda ref:(math.dist(positions[ref],center),ref))
            pins[1:]=[pin for pin in pins[1:] if owners[str(pin)] in refs or owners[str(pin)]==chosen]
            selected[str(row[1])]=chosen
    if not routed:raise ValueError('No controller connections to route')
    replacement='\n  (network\n'+''.join('    '+sx.dumps(row)+'\n' for row in network[1:])+'  )'
    return text[:start]+replacement+text[end:],{'routed_nets':sorted(routed),'external_components':selected,
            'acceptance':'Independent native controller pad groups and full physical DRC required'}
