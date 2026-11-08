"""Read-only variants that replay an owned MCU escape through its first via.

These are source-window proposals, not clearance or connectivity certificates.
The caller must admit the complete replacement and verify the saved candidate.
"""
from copy import deepcopy
import math

from .bundle_window import slice_legs
from .follow_geometry import simplify


def _point(values):
    values=tuple(values)
    if len(values)!=2 or any(type(v) is not int for v in values):
        raise ValueError('Expected integer copper coordinates')
    return values


def _xy(value):
    return _point((value.x,value.y))


def _legs(values):
    result=[(face,[_point(q) for q in path]) for face,path in values]
    if any(len(path)<2 or any(a==z for a,z in zip(path,path[1:])) for _,path in result):
        raise ValueError('Expected nonempty copper legs')
    return result


def _cover(a,z,segments):
    """Return exact collinear coverage witnesses, independent of direction."""
    dx,dy=z[0]-a[0],z[1]-a[1];length2=dx*dx+dy*dy;intervals=[]
    if not length2:return None
    for uid,c,d in segments:
        if any((q[0]-a[0])*dy!=(q[1]-a[1])*dx for q in (c,d)):continue
        values=[(q[0]-a[0])*dx+(q[1]-a[1])*dy for q in (c,d)]
        lo,hi=max(0,min(values)),min(length2,max(values))
        if lo<hi:intervals.append((lo,hi,uid))
    cursor=0;used=set()
    for lo,hi,uid in sorted(intervals):
        if lo>cursor:return None
        cursor=max(cursor,hi);used.add(uid)
    return used if cursor==length2 else None


def source_prefix_variant(board,row,protected_ids=()):
    """Return a separately owned first-via window, or None without mutation.

    Only a single exact, owned pad-local track may bridge a chain endpoint to
    the pad center. Prefix centerlines must be covered by actual owned tracks;
    their Native via dimensions are recorded and replayed by the caller.
    """
    from smilepcb.native_worker import p
    try:
        return _variant(board,row,set(protected_ids),p)
    except (KeyError,TypeError,ValueError,StopIteration):
        return None


def _variant(board,row,protected,p):
    ids=set(row['ids']);net=row['net'];width=row['width'];old=row['window']
    if not ids or len(ids)!=len(row['ids']) or ids&protected:return None
    stub_ids=set(row.get('terminal_stub_ids',()))
    if not stub_ids<=ids:return None
    items={q.m_Uuid.AsString():q for q in board.GetTracks()}
    if not ids<=items.keys():return None
    owned={u:items[u] for u in ids}
    if any(q.IsLocked() or q.GetNetname()!=net or q.GetClass() not in ('PCB_TRACK','PCB_VIA') for q in owned.values()):return None
    if old['lo_nm']!=0 or old['prefix_legs']:return None
    legs=_legs(row['old_legs']);faces={p.F_Cu,p.B_Cu}
    if len(legs)<2 or any(face not in faces for face,_ in legs):return None
    if any(a[-1]!=z[0] for (_,a),(_,z) in zip(legs,legs[1:])):return None
    face=legs[0][0];transition=next(i for i,(f,_) in enumerate(legs) if f!=face)
    outlet=legs[transition][1][0];initial=legs[0][1][0];center=_point(row['pad_position'])
    pads=[d for f in board.GetFootprints() if f.GetReference()==row['mcu_ref'] for d in f.Pads()
          if d.GetNumber()==row['mcu_pad'] and d.GetNetname()==net]
    if len(pads)!=1:return None
    pad=pads[0]
    if _xy(pad.GetPosition())!=center or not pad.IsOnLayer(face):return None
    land=pad.GetEffectiveShape(face)
    if any(not p.SHAPE.Collide(p.SHAPE_CIRCLE(p.VECTOR2I(*q),0),land,0) for q in (initial,center)):return None
    extra=0.
    if initial!=center:
        stubs=[q for u,q in owned.items() if u in stub_ids and q.GetClass()=='PCB_TRACK'
               and q.GetLayer()==face and q.GetWidth()==width
               and {_xy(q.GetStart()),_xy(q.GetEnd())}=={initial,center}]
        if len(stubs)!=1:return None
        extra=math.dist(center,initial);legs[0][1].insert(0,center)
    vias=[(u,q) for u,q in owned.items() if q.GetClass()=='PCB_VIA'
          and _xy(q.GetStart())==outlet and _xy(q.GetEnd())==outlet]
    if len(vias)!=1:return None
    via_id,via=vias[0]
    if (via.GetViaType()!=p.VIATYPE_THROUGH or {via.TopLayer(),via.BottomLayer()}!=faces
            or not all(via.IsOnLayer(f) for f in faces)
            or via.GetWidth(p.F_Cu)!=via.GetWidth(p.B_Cu)
            or not 0<via.GetDrillValue()<via.GetWidth(p.F_Cu)):return None
    prefix=legs[:transition];segments={f:[] for f in faces};used=set()
    for u,q in owned.items():
        if q.GetClass()=='PCB_TRACK' and q.GetWidth()==width and q.GetLayer() in faces:
            segments[q.GetLayer()].append((u,_xy(q.GetStart()),_xy(q.GetEnd())))
    prefix_edges=[]
    for f,path in prefix:
        for a,z in zip(path,path[1:]):
            witnesses=_cover(a,z,segments[f])
            if witnesses is None:return None
            used.update(witnesses);prefix_edges.append((f,a,z))
    # A changed witness must not gain an unrepresented extension or branch.
    for u in used:
        q=owned[u];f=q.GetLayer()
        if _cover(_xy(q.GetStart()),_xy(q.GetEnd()),[(str(i),a,z) for i,(other,a,z) in enumerate(prefix_edges) if other==f]) is None:return None
    distance=sum(math.dist(a,z) for _,path in prefix for a,z in zip(path,path[1:]))
    window=slice_legs(legs,distance,old['hi_nm']+extra)
    if (window['source']!=outlet or window['source_layer']!=legs[transition][0]
            or window['prefix_legs']!=prefix
            or window['target']!=_point(old['target']) or window['target_layer']!=old['target_layer']
            or window['suffix_legs']!=_legs(old['suffix_legs'])
            or _legs(old['fixed_legs'])!=_legs(old['suffix_legs'])
            or window['fixed_vias']!=[outlet,*map(_point,old['fixed_vias'])]):return None
    result=deepcopy(row);result['old_legs']=legs;result['window']=window
    result['path']=simplify([q for _,path in legs for q in path])
    result['source_prefix_witness']=dict(track_ids=sorted(used),via=dict(id=via_id,net=net,position=outlet,
        width=via.GetWidth(p.F_Cu),drill=via.GetDrillValue(),via_type=via.GetViaType(),top=via.TopLayer(),bottom=via.BottomLayer()))
    return result
