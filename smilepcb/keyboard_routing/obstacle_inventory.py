"""Physical MCU-pad-anchored feeder bodies and deterministic bank cohorts."""
from collections import Counter,defaultdict
import math,time
from smilepcb.native_worker import p
from .local_router import vec,LocalRouter
from .source_pad_star import owned_pad_stubs
from .cross_layer_polish import extract_spanning_chains
from .bundle_window import slice_legs,first_exit_distance
from .follow_geometry import simplify


def inventory_windows(board,nets,*,mcu_ref,bounds,protected_ids,deadline,deferred_zone_ids=()):
    mcu=next(f for f in board.GetFootprints() if f.GetReference()==mcu_ref);center=mcu.GetPosition();pads=[d for d in mcu.Pads() if d.GetNumber() and d.GetNetname() in nets]
    rows=extract_spanning_chains(board,eligible_nets=set(nets),protected_ids=protected_ids,deadline=deadline,deferred_zone_ids=deferred_zone_ids)
    vias=[t for t in board.GetTracks() if t.GetClass()=='PCB_VIA' and t.GetNetname() in nets];skipped=Counter();selected={}
    all_pads=[d for f in board.GetFootprints() if f.GetReference()!=mcu_ref for d in f.Pads() if d.GetNumber() and d.GetNetname() in nets]
    for row in rows:
        if time.monotonic()>=deadline:raise TimeoutError('MCU window inventory expired')
        witnesses=[]
        for reverse in (False,True):
            point=tuple(row['path'][-1] if reverse else row['path'][0]);face=row['end_layer'] if reverse else row['start_layer'];probe=p.SHAPE_CIRCLE(vec(point),1)
            for pad in pads:
                if pad.GetNetname()!=row['net']:continue
                contact=pad.IsOnLayer(face) and p.SHAPE.Collide(probe,pad.GetEffectiveShape(face),0)
                if not contact:
                    contact=any(v.GetNetname()==row['net'] and v.IsOnLayer(face) and p.SHAPE.Collide(probe,v.GetEffectiveShape(face),0) and any(pad.IsOnLayer(f) and v.IsOnLayer(f) and p.SHAPE.Collide(pad.GetEffectiveShape(f),v.GetEffectiveShape(f),0) for f in (p.F_Cu,p.B_Cu)) for v in vias)
                if contact:witnesses.append((reverse,pad))
        if not witnesses:skipped['no-direct-MCU-contact']+=1;continue
        reverse,pad=min(witnesses,key=lambda q:(q[1].GetNumber(),q[0]));legs=[(f,list(map(tuple,path))) for f,path in row['old_legs']]
        if reverse:legs=[(f,list(reversed(path))) for f,path in reversed(legs)]
        total=sum(math.dist(a,z) for _,path in legs for a,z in zip(path,path[1:]));gap=max(board.GetDesignSettings().m_MinClearance,150000);padding=gap+row['width']/2
        # An old escape may point inward or leave an artificial dogleg. It is
        # mutable copper, not a mechanical constraint. Start at the real land;
        # the Native oracle proves the complete replacement departure.
        lo=0
        exit_distance=first_exit_distance([path for _,path in legs],bounds)
        if exit_distance<total-1:hi=exit_distance
        else:
            last=legs[-1][1][-1];tail=250000
            for d in all_pads:
                if d.GetNetname()!=row['net']:continue
                if any(d.IsOnLayer(face) and p.SHAPE.Collide(p.SHAPE_CIRCLE(vec(last),1),d.GetEffectiveShape(face),0) for face in (p.F_Cu,p.B_Cu)):
                    bb=d.GetBoundingBox();expanded=[bb.GetLeft()-padding,bb.GetTop()-padding,bb.GetRight()+padding,bb.GetBottom()+padding]
                    tail=max(tail,first_exit_distance([list(reversed(path)) for _,path in reversed(legs)],expanded)+250000)
            hi=total-tail
        if hi-lo<2000000:skipped['direct-fragment-too-short']+=1;continue
        try:window=slice_legs(legs,lo,hi)
        except ValueError:skipped['invalid-layered-cut']+=1;continue
        position=pad.GetPosition();window['source']=(position.x,position.y)
        window['source_layer']=next(face for face in (window['source_layer'],p.F_Cu,p.B_Cu) if pad.IsOnLayer(face))
        delta=(position.x-center.x,position.y-center.y);candidates=[]
        for k in range(4):
            angle=-math.radians(mcu.GetOrientationDegrees()+90*k);axis=(math.cos(angle),math.sin(angle));candidates.append((sum(delta[i]*axis[i] for i in (0,1)),(round(axis[0]),round(axis[1]))))
        heading=max(candidates)[1]
        q=dict(row,old_legs=legs,path=simplify([point for _,path in legs for point in path]),window=window,heading=heading,pad_position=(position.x,position.y),mcu_pad=pad.GetNumber(),mcu_ref=mcu_ref)
        old=selected.get(row['net'])
        if old is None or hi-lo>old['window']['hi_nm']-old['window']['lo_nm']:selected[row['net']]=q
    # Take ownership of obsolete pad-local copper only with its full Native
    # contact star. Never leave invisible old rays constraining a new exit.
    if selected:
        oracle=LocalRouter(board,compact=True,deadline=deadline,deferred_zone_ids=deferred_zone_ids)
        for row in selected.values():
            pad=next(d for d in pads if d.GetNumber()==row['mcu_pad'])
            stubs=owned_pad_stubs(board,pad,row['ids'],protected_ids,oracle)
            row['terminal_stub_ids']=sorted(stubs);row['ids']=sorted(set(row['ids'])|stubs)
    return sorted(selected.values(),key=lambda q:(q['heading'],q['pad_position'],q['net'])),dict(chains=len(rows),eligible_nets=len(selected),owned_source_stubs=sum(len(q['terminal_stub_ids']) for q in selected.values()),skipped=dict(skipped))


def window_groups(rows):
    banks=defaultdict(list)
    for row in rows:banks[tuple(row['heading']),row['width']].append(row)
    out=[];seen=set()
    for (heading,width),bank in sorted(banks.items()):
        normal=(-heading[1],heading[0]);bank.sort(key=lambda q:(sum(q['pad_position'][i]*normal[i] for i in (0,1)),q['net']))
        proposals=[bank[i:i+6] for i in range(0,len(bank),6)]+[bank[i:i+2] for i in range(len(bank)-1)]
        for group in proposals:
            if len(group)<2:continue
            key=tuple(sorted(q['net'] for q in group))
            if key not in seen:seen.add(key);out.append(group)
    return sorted(out,key=lambda g:(-len(g),tuple(q['pad_position'] for q in g)))
