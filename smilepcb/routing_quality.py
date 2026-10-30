"""Opt-in positive routing costs, independent of collision/admission checks."""
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class QualityProfile:
    name: str
    bend_penalty_nm: int = 0


_PROFILES={'legacy':QualityProfile('legacy'),'turns':QualityProfile('turns',1000000)}
_current=ContextVar('smilepcb_routing_quality',default=_PROFILES['legacy'])


def quality_profile(name):
    if type(name) is not str or name not in _PROFILES:
        raise ValueError('Unknown routing quality; expected legacy or turns')
    return _PROFILES[name]


def current_quality():return _current.get()


@contextmanager
def using_quality_profile(name='legacy'):
    token=_current.set(quality_profile(name))
    try:yield _current.get()
    finally:_current.reset(token)


def quality_from_args(arguments):
    """Shared validation for source recipe stages with positional arguments."""
    values=[];index=0
    while index<len(arguments):
        argument=arguments[index]
        if argument=='--routing-quality':
            index+=1
            if index==len(arguments):raise ValueError('Missing routing quality')
            values.append(arguments[index])
        elif argument.startswith('--routing-quality='):values.append(argument.split('=',1)[1])
        index+=1
    if len(values)>1:raise ValueError('Repeated routing quality')
    return quality_profile(values[0] if values else 'legacy').name


def heading(a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];scale=math.gcd(dx,dy)
    return (dx//scale,dy//scale) if scale else None


def path_cost(points,initial_heading=None,profile=None):
    profile=profile or current_quality();cost=0;previous=initial_heading
    for a,b in zip(points,points[1:]):
        direction=heading(a,b)
        if direction is None:continue
        cost+=math.hypot(b[0]-a[0],b[1]-a[1])
        if previous is not None and direction!=previous:cost+=profile.bend_penalty_nm
        previous=direction
    return cost


def ranked_paths(paths,profile=None):
    profile=profile or current_quality()
    return iter(paths) if not profile.bend_penalty_nm else iter(sorted(paths,key=lambda path:path_cost(path,profile=profile)))


def search_state(cell,incoming,profile=None):
    return (*cell,incoming) if (profile or current_quality()).bend_penalty_nm else cell


def copper_metrics(tracks):
    """Whole routed-copper diagnostics, including supply/ground and all vias.

    Collinear splits do not count as bends. Branch junctions are reported
    separately. Arcs are counted explicitly rather than treated as chords.
    These diagnostics never qualify electrical or physical correctness.
    """
    rays=defaultdict(set);length=0;vias=0;arcs=0
    for item in tracks.values():
        if item['class']=='PCB_VIA':vias+=1;continue
        if item['class']!='PCB_TRACK':arcs+=1;continue
        a,b=tuple(item['start']),tuple(item['end'])
        length+=math.dist(a,b)
        for point,other in ((a,b),(b,a)):
            direction=heading(point,other)
            if direction:rays[item['net'],item['layer'],point].add(direction)
    bends=0
    for directions in rays.values():
        if len(directions)==2:
            a,b=directions
            bends+=a!=tuple(-v for v in b)
    return {'length_nm':round(length),'bends':bends,'vias':vias,'branches':sum(len(ds)>2 for ds in rays.values()),'arcs':arcs}
