"""Assign provisional source signals in collector-relative peel order.

This proposes a logical order, not a clearance proof. The native source-only
permutation and complete matrix admission must both accept it before compose.
"""
import math
from collections import Counter,defaultdict
from statistics import median
from .bank_geometry import terminal_origin


def collector_assignment(ports,targets,families):
    names=[q['net'] for q in ports]
    if len(set(names))!=len(names) or not set(names)<=set(targets) or not set(names)<=set(families):
        raise ValueError('Incomplete collector assignment inventory')
    if any(families[n] not in ('rows','columns') for n in names):raise ValueError('Unknown matrix role')
    groups=defaultdict(list)
    for q in ports:
        a=terminal_origin(q);z=q['exit_nm'];delta=(z[0]-a[0],z[1]-a[1]);g=math.gcd(*delta)
        if not g:raise ValueError('Collector assignment needs a physical source heading')
        h=tuple(v//g for v in delta)
        if h not in ((1,0),(-1,0),(0,1),(0,-1)):raise ValueError('Collector source rail must be cardinal')
        groups[tuple(q.get('bank') or ('isolated',q['net'])),q['exit_layer'],h,sum(h[i]*z[i] for i in (0,1))].append(q)
    mapping={n:n for n in names};plans=[]
    for (bank,face,source,_),members in sorted(groups.items()):
        origin=tuple(sum(q['exit_nm'][i] for q in members)/len(members) for i in (0,1))
        roles=Counter(families[q['net']] for q in members)
        role=max(roles,key=lambda r:(roles[r],r=='columns'))
        # A column collector advances across the field's columns; a row
        # collector advances across its rows. The physical lane order rotates
        # with the collector, rather than being sorted in world X/Y again.
        axis=0 if role=='columns' else 1
        source_normal=(-source[1],source[0])
        for family in sorted(roles):
            available=[q for q in members if families[q['net']]==family]
            directions=defaultdict(list)
            for q in available:
                sign=1 if targets[q['net']][axis]>=origin[axis] else -1
                directions[sign].append(q['net'])
            # Opposite-going targets get separate end chunks. Carrying a
            # backwards target inside a forward collector blocks its first
            # exposed peel and can drag every other signal past its branch.
            for sign,signals in sorted(directions.items(),key=lambda item:(len(item[1]),item[0])):
                goal=(sign,0) if axis==0 else (0,sign);normal=(-goal[1],goal[0])
                side=1 if median(sum((targets[n][i]-origin[i])*normal[i] for i in (0,1)) for n in signals)>0 else -1
                chunk=available
                projection=sum(goal[i]*source_normal[i] for i in (0,1))
                if len(directions)>1 and projection:
                    # Opposite turns depart from opposite rail ends. Peel side
                    # orders lanes inside that chunk; it must not choose its end.
                    chunk=sorted(available,key=lambda q:(-projection*sum((q['exit_nm'][i]-origin[i])*source_normal[i] for i in (0,1)),q['net']))[:len(signals)]
                lanes=sorted(chunk,key=lambda q:(-side*sum((q['exit_nm'][i]-origin[i])*source_normal[i] for i in (0,1)),q['net']))[:len(signals)]
                signals=sorted(signals,key=lambda n:(sum(targets[n][i]*goal[i] for i in (0,1)),n))
                mapping.update((q['net'],n) for q,n in zip(lanes,signals))
                occupied={q['net'] for q in lanes};available=[q for q in available if q['net'] not in occupied]
                plans.append({'bank':list(bank),'layer':face,'family':family,'source_heading':list(source),'collector_heading':list(goal),'peel_side':side,'physical_lane_order':[q['net'] for q in lanes],'signal_peel_order':signals})
    return mapping,plans
