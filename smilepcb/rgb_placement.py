"""Bounded local candidates; the caller must check native board obstacles.

Dimensions describe a recipe's mechanical opening and component/cutout envelope.
These are not a substitute for a switch-specific mechanical qualification.
"""
import math


def courtyard_shapes(footprint):
    """Owned native courtyard polygons on both actual assembly sides.

    Preserve concave socket recesses. A copper-side flag and a rectangular
    bounding box cannot represent a through-hole footprint's assembly space.
    """
    from .native_worker import p
    footprint.BuildCourtyardCaches()
    result={}
    for side in (p.F_Cu,p.B_Cu):
        shape=footprint.GetCourtyard(side)
        if shape.OutlineCount():
            result[side]=p.SHAPE_POLY_SET(shape)
    return result


def contoured_courtyard(footprint, *, clearance_mm=.25):
    """Reserve the Fab body and actual pad contours on a 0.01 mm grid.

    KLC F5.3 permits contoured courtyards. Derive an owned polygon without
    modifying the footprint, its copper or its manufacturing cutouts.
    """
    from .native_worker import p
    if not math.isfinite(clearance_mm) or not 0 < clearance_mm <= 5:
        raise ValueError('Expected a finite courtyard clearance in (0, 5] mm')
    fab=p.B_Fab if footprint.GetLayer()==p.B_Cu else p.F_Fab
    body=[g.GetEffectiveShape().BBox() for g in footprint.GraphicalItems()
          if isinstance(g,p.PCB_SHAPE) and g.GetLayer()==fab]
    if not body:
        raise ValueError('A declared Fab body is required to derive a courtyard')
    grid=p.FromMM(.01)
    # Nearest-grid snapping can move a vertex inward by half a diagonal.
    # Reserve that error before snapping, keeping the requested true margin.
    margin=p.FromMM(clearance_mm)+math.ceil(grid/math.sqrt(2))+1
    x0=min(b.GetLeft() for b in body)-margin;y0=min(b.GetTop() for b in body)-margin
    x1=max(b.GetRight() for b in body)+margin;y1=max(b.GetBottom() for b in body)+margin
    shape=p.SHAPE_POLY_SET();shape.NewOutline()
    for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1)):
        shape.Append(p.VECTOR2I(x,y))
    for pad in footprint.Pads():
        contour=p.SHAPE_POLY_SET()
        pad.TransformShapeToPolygon(contour,footprint.GetLayer(),margin,
                                    p.FromMM(.001),p.ERROR_OUTSIDE)
        shape.BooleanAdd(contour)
    if shape.OutlineCount()!=1 or shape.HoleCount(0):
        raise ValueError('Body and pad clearance envelopes must form one courtyard')
    result=p.SHAPE_POLY_SET();result.NewOutline();points=[]
    chain=shape.COutline(0)
    for i in range(chain.PointCount()):
        at=chain.CPoint(i);point=(round(at.x/grid)*grid,round(at.y/grid)*grid)
        if not points or points[-1]!=point:points.append(point)
    if points[-1]==points[0]:points.pop()
    for x,y in points:result.Append(p.VECTOR2I(x,y))
    if len(points)<3 or result.IsSelfIntersecting():
        raise ValueError('Grid snapping produced an invalid courtyard contour')
    return result


def physical_bounds(footprint):
    """Native body/pad envelope, without assembly courtyard or annotations.

    Courtyard overlap is a separate placement/DRC constraint. This bound is
    used only for material containment at the board edge, not assembly space.
    """
    from .native_worker import p
    fab=p.B_Fab if footprint.GetLayer()==p.B_Cu else p.F_Fab
    boxes=[pad.GetEffectiveShape(footprint.GetLayer()).BBox() for pad in footprint.Pads()]
    boxes.extend(g.GetEffectiveShape().BBox() for g in footprint.GraphicalItems()
                 if isinstance(g,p.PCB_SHAPE) and g.GetLayer()==fab)
    if not boxes:
        raise ValueError('Component has no physical body or pad geometry')
    return (min(b.GetLeft() for b in boxes),min(b.GetTop() for b in boxes),
            max(b.GetRight() for b in boxes),max(b.GetBottom() for b in boxes))


def transform(origin, angle, local):
    """Millimetres, Cartesian rotation; convert KiCad's angle at the boundary."""
    a=math.radians(angle);c,s=math.cos(a),math.sin(a)
    return (origin[0]+local[0]*c-local[1]*s,
            origin[1]+local[0]*s+local[1]*c)


def window_candidates(*, window, body, offset, step=.1):
    values=(*window,*body,*offset,step)
    if (len(window)!=2 or len(body)!=2 or len(offset)!=2
            or not all(math.isfinite(v) for v in values)
            or min(*window,*body,step)<=0 or max(*window,*body)>50 or step<.01):
        raise ValueError('Expected a finite, bounded mechanical placement profile')
    dimensions=[body,body,body[::-1],body[::-1]]
    counts=[max(0,2*math.floor(((window[0]-w)/2+1e-9)/step)+1)
            *max(0,2*math.floor(((window[1]-h)/2+1e-9)/step)+1)
            for w,h in dimensions]
    if sum(counts)>50000:
        raise ValueError('Mechanical placement candidate budget exceeded')
    candidates=[]
    for angle in (0,180,90,270):
        w,h=body if angle%180==0 else body[::-1]
        nx=math.floor(((window[0]-w)/2+1e-9)/step)
        ny=math.floor(((window[1]-h)/2+1e-9)/step)
        for ix in range(-nx,nx+1):
            for iy in range(-ny,ny+1):
                dx,dy=round(ix*step,6),round(iy*step,6)
                candidates.append((round(dx*dx+dy*dy,9), (0,180,90,270).index(angle),
                                   round(offset[0]+dx,6),round(offset[1]+dy,6),angle))
    return [(x,y,a) for _,_,x,y,a in sorted(candidates)]
