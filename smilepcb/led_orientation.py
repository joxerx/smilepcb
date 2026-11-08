"""Geometric LED pose proposals from the actual directed open data chain.

This optimizes pad-center lead length, not obstacle or mechanical feasibility.
Native placement, routing and rule admission remain authoritative.
"""
from functools import lru_cache
import math
from pathlib import Path


@lru_cache(maxsize=1)
def data_offsets_nm():
    """Read DIN/DOUT offsets from the same source used to materialize MINI_E."""
    from . import schematic as s
    footprint=s.read(Path(__file__).with_name('data')/'keyboard'/'LED_MX_6028R.kicad_mod')
    pads={str(q[1]):q for q in s.children(footprint,'pad')}
    return tuple(tuple(round(value*1000000) for value in s.one(pads[number],'at')[1:3])
                 for number in ('3','2'))


def posed_offset(offset,side,rotation):
    """Match native LEFT_RIGHT backside flip followed by recipe rotation."""
    if side not in ('F.Cu','B.Cu') or rotation not in (0,180):
        raise ValueError('LED pose requires a copper face and zero/180 rotation')
    x,y=offset
    if side=='B.Cu':x=-x
    return (x,y) if rotation==0 else (-x,-y)


def chain_rotations(order,positions_nm,data_offsets,*,side='B.Cu',lead_nm=None):
    """Choose two-state poses for one open chain; never reconnect its cut."""
    order=list(order)
    if len(set(order))!=len(order) or set(order)!=set(positions_nm):
        raise ValueError('LED chain must visit each physical site exactly once')
    offsets={angle:tuple(posed_offset(q,side,angle) for q in data_offsets) for angle in (0,180)}
    if not order:return {}
    def terminal(key,angle,pin):
        return tuple(positions_nm[key][i]+offsets[angle][pin][i] for i in (0,1))
    states={angle:(math.dist(lead_nm,terminal(order[0],angle,0)) if lead_nm is not None else 0,(angle,))
            for angle in (0,180)}
    for previous,current in zip(order,order[1:]):
        states={angle:min((cost+math.dist(terminal(previous,before,1),terminal(current,angle,0)),path+(angle,))
                          for before,(cost,path) in states.items()) for angle in (0,180)}
    return dict(zip(order,min(states.values())[1]))
