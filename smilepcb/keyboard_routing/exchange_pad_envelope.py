"""Foreign occupancy of the pinned45-degree imported PAD tree, never copper.

The native outward polygon remains authoritative for contacts and solder.
Only an explicit v2 router indexes this additional conservative occupancy.
"""
import math
from smilepcb.keyboard_exchange import CIRCLE_SIDES,CIRCLE_ROUNDING_GUARD_UM

_NORMALS=((1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1))
_POLYGON_ERROR_NM=1000


def import_grid_nm(bounds_nm):
    """Pinned Structure scaling for unitum/resolution10, including formatting."""
    if (len(bounds_nm)!=4 or any(type(v)is not int for v in bounds_nm)
            or bounds_nm[0]>=bounds_nm[2] or bounds_nm[1]>=bounds_nm[3]):
        raise ValueError('Expected finite integer board-edge bounds')
    scale=10;extent=max(abs(v)for v in bounds_nm)+5
    while 5*extent*scale>=33554432*1000:
        scale//=10
        if not scale:raise ValueError('Board extent cannot retain a positive imported scale')
    return 1000//scale


def quantization_excess_nm(grid_nm):
    """Three coordinate vectors, two approximate rotations, eight supports."""
    if type(grid_nm)is not int or grid_nm<=0:raise ValueError('Expected positive import grid')
    return math.ceil(5*grid_nm/math.sqrt(2)+grid_nm)


def _ceil_sqrt(n):
    root=math.isqrt(n)
    return root+(root*root<n)


def export_excess_nm(kind,size_nm,corner_radius_nm=0):
    """Bounds of the reviewed KiCad exporter branches, including10nm format."""
    if (len(size_nm)!=2 or any(type(v)is not int or v<=0 for v in size_nm)
            or type(corner_radius_nm)is not int or not 0<=corner_radius_nm<=min(size_nm)//2):
        raise ValueError('Invalid native PAD dimensions')
    if kind=='circle':
        return math.ceil(size_nm[0]/2*(1/math.cos(math.pi/CIRCLE_SIDES)-1)
                         +CIRCLE_ROUNDING_GUARD_UM*1000+10)
    if kind in('roundrect','chamfered_rect'):
        delta=math.ceil(corner_radius_nm*(1-math.cos(math.pi/36)))
        return _ceil_sqrt(2*delta*delta)+30
    if kind in('rect','oval','trapezoid','custom'):return 30
    raise ValueError('Unsupported exported PAD shape')


def _hull(points):
    points=sorted(set(points))
    def cross(a,b,c):return(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def half(values):
        out=[]
        for q in values:
            while len(out)>1 and cross(out[-2],out[-1],q)<=0:out.pop()
            out.append(q)
        return out
    return half(points)[:-1]+half(reversed(points))[:-1]


def _vertices_twice(bounds):
    vertices=[]
    for i,(ax,ay)in enumerate(_NORMALS):
        for j,(bx,by)in enumerate(_NORMALS[:i]):
            det=ax*by-ay*bx
            if not det:continue
            x2=2*(bounds[i]*by-ay*bounds[j])//det
            y2=2*(ax*bounds[j]-bounds[i]*bx)//det
            if all(nx*x2+ny*y2<=2*h for(nx,ny),h in zip(_NORMALS,bounds)):
                vertices.append((x2,y2))
    return _hull(vertices)


def enclosing_octagon(points_nm,extra_nm):
    """Enclose all outlines plus imported displacement using exact integer math."""
    points=list(points_nm)
    if (type(extra_nm)is not int or extra_nm<0 or not points
            or any(len(q)!=2 or any(type(v)is not int for v in q)for q in points)
            or len(_hull(points))<3):
        raise ValueError('Expected nonempty finite2D PAD polygon and integer excess')
    support=[max(nx*x+ny*y for x,y in points)for nx,ny in _NORMALS]
    def expanded(gap):
        diagonal=_ceil_sqrt(2*gap*gap)
        return[h+(diagonal if nx and ny else gap)for(nx,ny),h in zip(_NORMALS,support)]
    required=_vertices_twice(expanded(extra_nm))
    # +1nm contains the entire halfplane intersection after nearest rounding
    # (at most sqrt(2)/2nm). Signed half ties follow Java floor(x+.5).
    result=_hull(((x2+1)//2,(y2+1)//2)for x2,y2 in _vertices_twice(expanded(extra_nm+1)))
    if len(result)<3 or any(abs(v)>2147483647 for q in result for v in q):
        raise ValueError('PAD envelope is outside the native integer domain')
    for x2,y2 in required:
        if any((b[0]-a[0])*(y2-2*a[1])-(b[1]-a[1])*(x2-2*a[0])<0
               for a,b in zip(result,result[1:]+result[:1])):
            raise ValueError('Integer PAD envelope lost required occupancy')
    return result


def foreign_pad_envelope(pad,face,grid_nm):
    """One collision-only native polygon for one real conductive PAD face."""
    from smilepcb.native_worker import p
    if not pad.IsOnLayer(face) or pad.GetAttribute()==p.PAD_ATTRIB_NPTH:
        raise ValueError('Imported PAD occupancy requires an actual conductive face')
    # The pinned exporter explicitly uses ALL_LAYERS; unproved custom padstacks
    # must not silently substitute one face for a different exported common shape.
    if pad.Padstack().Mode()!=p.PADSTACK.MODE_NORMAL:
        raise ValueError('Unsupported layer-specific PAD export stack')
    shapes=((p.PAD_SHAPE_CIRCLE,'circle'),(p.PAD_SHAPE_RECT,'rect'),(p.PAD_SHAPE_OVAL,'oval'),
            (p.PAD_SHAPE_TRAPEZOID,'trapezoid'),(p.PAD_SHAPE_ROUNDRECT,'roundrect'),
            (p.PAD_SHAPE_CHAMFERED_RECT,'chamfered_rect'),(p.PAD_SHAPE_CUSTOM,'custom'))
    kind=next((name for value,name in shapes if pad.GetShape(face)==value),None)
    size=pad.GetSize(face)
    corner=pad.GetRoundRectCornerRadius(face)if kind in('roundrect','chamfered_rect')else 0
    excess=export_excess_nm(kind,(size.x,size.y),corner)+quantization_excess_nm(grid_nm)
    outside=p.SHAPE_POLY_SET()
    pad.TransformShapeToPolygon(outside,face,0,_POLYGON_ERROR_NM,p.ERROR_OUTSIDE)
    if not outside.OutlineCount()or outside.ArcCount():
        raise ValueError('Expected a flat outward native PAD polygon')
    # Native circles repeat their closing vertex. Normalize only this owned
    # polygon; curved primitives cannot be bounded by endpoint samples.
    outside.Simplify()
    if not outside.OutlineCount()or outside.IsSelfIntersecting():
        raise ValueError('Invalid outward native PAD polygon')
    points=[]
    for i in range(outside.OutlineCount()):
        chain=outside.COutline(i)
        if chain.PointCount()<3:raise ValueError('Empty outward PAD outline')
        points.extend((chain.CPoint(j).x,chain.CPoint(j).y)for j in range(chain.PointCount()))
    result=p.SHAPE_POLY_SET();result.NewOutline()
    for x,y in enclosing_octagon(points,excess):result.Append(p.VECTOR2I(x,y))
    return result
