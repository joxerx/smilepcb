"""Request-local, owner-aware corridor obstacles; never clearance exemptions."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Corridor:
    id: str
    layer: int
    nets: frozenset[str]
    path: tuple[tuple[int, int], ...] = ()
    width_nm: int = 0
    kind: str = 'track'
    center_nm: tuple[int, int] | None = None
    radius_nm: int | None = None


_current = ContextVar('smilepcb_corridor_reservations', default=())


def current_corridors():
    return _current.get()


def _validated(record):
    def field(name):
        return getattr(record,name) if isinstance(record,Corridor) else record[name]
    uid,layer=field('id'),field('layer')
    kind=record.kind if isinstance(record,Corridor) else record.get('kind','track')
    if kind not in ('track','via'):raise ValueError('Corridor kind must be track or via')
    if not isinstance(uid,str) or not uid.strip():raise ValueError('Corridor ID must be nonempty')
    if type(layer) is not int or layer not in (0,2):raise ValueError('Corridor face must be physical 0 or 2')
    raw_nets=field('nets')
    if isinstance(raw_nets,(str,bytes)):raise ValueError('Corridor owners must be distinct net names')
    nets=tuple(raw_nets)
    if not nets or any(not isinstance(n,str) or not n.strip() for n in nets) or len(set(nets))!=len(nets):
        raise ValueError('Corridor owners must be nonempty distinct net names')
    if kind=='via':
        center=tuple(field('center_nm'));radius=field('radius_nm')
        if len(center)!=2 or any(type(v) is not int for v in center):
            raise ValueError('Corridor via center must be an integer nm point pair')
        if type(radius) is not int or radius<=0:
            raise ValueError('Corridor via radius must be positive integer nm')
        return Corridor(uid,layer,frozenset(nets),kind='via',center_nm=center,radius_nm=radius)
    width=field('width_nm')
    if type(width) is not int or width<=0:raise ValueError('Corridor envelope width must be positive integer nm')
    path=tuple(tuple(point) for point in field('path'))
    if len(path)<2 or any(len(point)!=2 or any(type(v) is not int for v in point) for point in path):
        raise ValueError('Corridor path must contain integer nm point pairs')
    for a,b in zip(path,path[1:]):
        dx,dy=b[0]-a[0],b[1]-a[1]
        if (dx==dy==0) or (dx and dy and abs(dx)!=abs(dy)):
            raise ValueError('Corridor edges must be nonzero octile segments')
    return Corridor(uid,layer,frozenset(nets),path,width)


@contextmanager
def reserve_corridors(records):
    """Append immutable validated reservations; children cannot remove parents."""
    additions=tuple(_validated(record) for record in records)
    token=_current.set(current_corridors()+additions)
    try:yield current_corridors()
    finally:_current.reset(token)


def collisions(shape,layer,net):
    """Yield each intersecting foreign corridor ID once in binding order."""
    eligible=tuple(r for r in current_corridors() if r.layer==layer and net not in r.nets)
    if not eligible:return
    # Policy validation and empty/owner-only contexts require no Native runtime.
    from smilepcb.native_worker import p
    seen=set()
    for r in eligible:
        if r.id in seen:continue
        if r.kind=='via':
            reserved=p.SHAPE_CIRCLE(p.VECTOR2I(*r.center_nm),r.radius_nm)
            if p.SHAPE.Collide(shape,reserved,0):
                seen.add(r.id);yield r.id
        else:
            for a,b in zip(r.path,r.path[1:]):
                reserved=p.SHAPE_SEGMENT(p.VECTOR2I(*a),p.VECTOR2I(*b),r.width_nm)
                if p.SHAPE.Collide(shape,reserved,0):
                    seen.add(r.id);yield r.id;break
