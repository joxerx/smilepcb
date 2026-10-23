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


def assign_matrix_gpio(cfg,pin_xy,targets,*,excluded_gpios=()):
    """Return a new shared CAD/QMK model, keeping fixed-function pins reserved."""
    updated=deepcopy(cfg);mcu=next(part for part in updated['parts'] if part['ref']=='U_MCU')
    groups=[('rows','ROW'),('cols','COL')]
    names=[prefix+str(i) for group,prefix in groups for i in range(len(cfg['layout']['gpio'][group]))]
    if set(targets)!=set(names):raise ValueError('Every matrix net requires its physical destination centroid')
    pads=cfg['mcu_gpio_pads'];safe=set(ROW_PINS)|set(COL_PINS)
    available=sorted(gpio for gpio in pin_xy if gpio in safe and gpio in pads and
                     (mcu['nets'][pads[gpio]] in names or mcu['nets'][pads[gpio]].startswith('NC_') or
                      pads[gpio] in mcu.get('no_connect',()) and mcu['nets'][pads[gpio]].startswith('unconnected-')))
    if isinstance(excluded_gpios,(str,bytes)) or set(excluded_gpios)-set(available):
        raise ValueError('GPIO access feedback must select available safe matrix pins')
    excluded=set(excluded_gpios);available=[gpio for gpio in available if gpio not in excluded]
    points=[*pin_xy.values(),*targets.values()]
    if any(len(point)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in point) for point in points):
        raise ValueError('GPIO geometry requires finite two-dimensional coordinates')
    if len(available)<len(names):raise ValueError('Insufficient available safe GPIO locations')
    def cost(net,gpio):return sum((a-b)**2 for a,b in zip(targets[net],pin_xy[gpio]))
    assignment=minimum_assignment([[cost(net,gpio) for gpio in available] for net in names])
    selected={net:available[index] for net,index in zip(names,assignment)}
    previous={prefix+str(i):gpio for group,prefix in groups for i,gpio in enumerate(cfg['layout']['gpio'][group])}
    if any(gpio not in pin_xy for gpio in previous.values()):raise ValueError('Current GPIO geometry is missing')
    declared_unused=set(mcu.get('no_connect',()))
    for pin,net in list(mcu['nets'].items()):
        if net in names or pin in declared_unused and net.startswith('unconnected-'):
            mcu['nets'][pin]='NC_MCU_'+pin
    for net,gpio in selected.items():mcu['nets'][pads[gpio]]=net
    mcu['no_connect']=sorted(pin for pin,net in mcu['nets'].items() if net.startswith('NC_'))
    for group,prefix in groups:
        value=[selected[prefix+str(i)] for i in range(len(cfg['layout']['gpio'][group]))]
        updated['layout']['gpio'][group]=value;updated['qmk']['matrix_pins'][group]=value[:]
    return updated,{'method':'minimum squared distance to native matrix-pad centroids; routability unproven',
        'assignment':selected,'previous':previous,'safe_available_pins':len(available),'excluded_gpios':sorted(excluded),
        'squared_distance_before':sum(cost(net,gpio) for net,gpio in previous.items()),
        'squared_distance_after':sum(cost(net,gpio) for net,gpio in selected.items())}
