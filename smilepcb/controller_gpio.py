"""Geometry-based matrix assignment within the existing STM32 GPIO allowlist.

This is a placement heuristic, not a proof of routability. Native routing and
complete ERC/DRC remain the acceptance authority.
"""
from copy import deepcopy
import math
from .keyboard_layout import ROW_PINS,COL_PINS


def minimum_assignment(costs):
    """Minimum-cost rectangular matching, rows <= columns, stable on ties."""
    n=len(costs);m=len(costs[0]) if n else 0
    if not n or m<n or any(len(row)!=m for row in costs) or any(
            type(value) not in (int,float) or not math.isfinite(value) for row in costs for value in row):
        raise ValueError('Assignment requires a finite rectangular cost matrix with at least one column per row')
    u=[0]*(n+1);v=[0]*(m+1);matched=[0]*(m+1);previous=[0]*(m+1)
    for row in range(1,n+1):
        matched[0]=row;column=0;slack=[math.inf]*(m+1);used=[False]*(m+1)
        while True:
            used[column]=True;active=matched[column];delta=math.inf;next_column=0
            for other in range(1,m+1):
                if used[other]:continue
                reduced=costs[active-1][other-1]-u[active]-v[other]
                if reduced<slack[other]:slack[other]=reduced;previous[other]=column
                if slack[other]<delta:delta=slack[other];next_column=other
            for other in range(m+1):
                if used[other]:u[matched[other]]+=delta;v[other]-=delta
                else:slack[other]-=delta
            column=next_column
            if not matched[column]:break
        while column:
            old=previous[column];matched[column]=matched[old];column=old
    result=[None]*n
    for column in range(1,m+1):
        if matched[column]:result[matched[column]-1]=column-1
    return result


def assign_matrix_gpio(cfg,pin_xy,targets,*,excluded_gpios=(),banks=None,preferred_banks=(),uncertain_gpios=(),unwitnessed_gpios=()):
    """Return a new shared CAD/QMK model, keeping fixed-function pins reserved."""
    updated=deepcopy(cfg);mcu=next(part for part in updated['parts'] if part['ref']=='U_MCU')
    groups=('rows','cols');pads=cfg['mcu_gpio_pads']
    logical={group:[mcu['nets'][pads[gpio]] for gpio in cfg['layout']['gpio'][group]] for group in groups}
    names=[name for group in groups for name in logical[group]]
    if len(set(names))!=len(names):raise ValueError('Matrix roles require distinct signal identities')
    if set(targets)!=set(names):raise ValueError('Every matrix net requires its physical destination centroid')
    pads=cfg['mcu_gpio_pads'];safe=set(ROW_PINS)|set(COL_PINS)
    available=sorted(gpio for gpio in pin_xy if gpio in safe and gpio in pads and
                     (mcu['nets'][pads[gpio]] in names or mcu['nets'][pads[gpio]].startswith('NC_') or
                      pads[gpio] in mcu.get('no_connect',()) and mcu['nets'][pads[gpio]].startswith('unconnected-')))
    if isinstance(excluded_gpios,(str,bytes)) or set(excluded_gpios)-set(available):
        raise ValueError('GPIO access feedback must select available safe matrix pins')
    uncertain=set(uncertain_gpios)
    if uncertain-set(available):raise ValueError('Unknown uncertain matrix GPIO')
    unwitnessed=set(unwitnessed_gpios)
    if unwitnessed-set(available):raise ValueError('Unknown unwitnessed matrix GPIO')
    excluded=set(excluded_gpios);available=[gpio for gpio in available if gpio not in excluded]
    uncertain-=excluded;unwitnessed-=excluded
    points=[*pin_xy.values(),*targets.values()]
    if any(len(point)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in point) for point in points):
        raise ValueError('GPIO geometry requires finite two-dimensional coordinates')
    if len(available)<len(names):raise ValueError('Insufficient available safe GPIO locations')
    def cost(net,gpio):return sum((a-b)**2 for a,b in zip(targets[net],pin_xy[gpio]))
    method='minimum squared distance to native matrix-pad centroids; routability unproven'
    if banks is None:
        assignment=minimum_assignment([[cost(net,gpio) for gpio in available] for net in names])
        selected={net:available[index] for net,index in zip(names,assignment)}
    else:
        from .keyboard_routing.bank_geometry import bank_allocation
        filtered={side:[g for g in rows if g in available] for side,rows in banks.items()}
        if {g for rows in filtered.values() for g in rows}!=set(available):
            raise ValueError('Physical banks omit available GPIOs')
        allocation=bank_allocation(filtered,{group:len(logical[group]) for group in groups},preferred=preferred_banks,uncertain_pins=uncertain,unwitnessed_pins=unwitnessed)
        if allocation is None:raise ValueError('No exclusive physical-bank capacity for matrix roles')
        selected={}
        for group in groups:
            axis=1 if group=='rows' else 0;chosen=set(allocation[group])
            chunks=[(side,[g for g in pins if g in chosen]) for side,pins in filtered.items()]
            chunks=[(side,pins) for side,pins in chunks if pins]
            chunks.sort(key=lambda q:(sum(pin_xy[g][axis] for g in q[1])/len(q[1]),q[0]))
            signals=sorted(logical[group],key=lambda n:(targets[n][axis],n))
            selected.update(zip(signals,[g for _,pins in chunks for g in pins]))
        method='certified-first exclusive banks; contiguous target-ordered chunks; joint routability unproven'
    previous={name:gpio for group in groups for name,gpio in zip(logical[group],cfg['layout']['gpio'][group])}
    if any(gpio not in pin_xy for gpio in previous.values()):raise ValueError('Current GPIO geometry is missing')
    declared_unused=set(mcu.get('no_connect',()))
    for pin,net in list(mcu['nets'].items()):
        if net in names or pin in declared_unused and net.startswith('unconnected-'):
            mcu['nets'][pin]='NC_MCU_'+pin
    for net,gpio in selected.items():mcu['nets'][pads[gpio]]=net
    mcu['no_connect']=sorted(pin for pin,net in mcu['nets'].items() if net.startswith('NC_'))
    for group in groups:
        value=[selected[name] for name in logical[group]]
        updated['layout']['gpio'][group]=value;updated['qmk']['matrix_pins'][group]=value[:]
    return updated,{'method':method,
        'assignment':selected,'previous':previous,'safe_available_pins':len(available),'excluded_gpios':sorted(excluded),'uncertain_selected':sorted(set(selected.values())&uncertain),'unwitnessed_selected':sorted(set(selected.values())&unwitnessed),
        'squared_distance_before':sum(cost(net,gpio) for net,gpio in previous.items()),
        'squared_distance_after':sum(cost(net,gpio) for net,gpio in selected.items())}


def validate_exit_mapping(mapping,families,source_nets,*,assignment_scope='within-role'):
    """Validate logical scope; native source/capability proof remains mandatory."""
    if assignment_scope not in ('within-role','matrix'):
        raise ValueError('Unknown final GPIO assignment scope')
    if (not isinstance(mapping,dict) or any(not isinstance(a,str) or not isinstance(z,str)
            for a,z in mapping.items()) or isinstance(source_nets,(str,bytes))):
        raise ValueError('Expected explicit matrix signal identities')
    sources=list(source_nets)
    if len(sources)!=len(set(sources)) or set(mapping)!=set(sources):
        raise ValueError('GPIO permutation omitted or duplicated a source interface')
    if (set(mapping)!=set(mapping.values()) or not set(mapping)<=set(families)
            or any(families[n] not in ('rows','columns') for n in mapping)):
        raise ValueError('Final GPIO assignment must be a matrix signal bijection')
    if assignment_scope=='matrix':
        if set(mapping)!=set(families):
            raise ValueError('Matrix assignment must cover every matrix function')
    elif any(families[a]!=families[z] for a,z in mapping.items()):
        raise ValueError('Final GPIO assignment must be a within-role permutation')
    return dict(mapping)
