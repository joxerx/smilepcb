"""Conservative, provenance-owned tail normalization on an in-memory board.

The caller protects field/prerequisite/source copper and owns raw-node export,
receipt publication and saved reload. Every mutation here preserves all original
retained conductor components, including padless branches, and all pad groups.
"""
from copy import deepcopy
import math
import time


def _check(deadline):
    if time.monotonic()>=deadline:raise TimeoutError('Generated tail hygiene deadline exhausted')


def endpoint_candidates(track,buses,ends):
    """Intersect the existing branch line with an intact finite bus centerline.

    Both a short extension and a short trim are permitted. No arbitrary bend,
    bus split or fractional-nanometer endpoint is introduced.
    """
    if track['class']!='PCB_TRACK' or track['locked']:return []
    a,b=track['start'],track['end'];d=(b[0]-a[0],b[1]-a[1]);length=math.hypot(*d)
    if not length:return []
    choices=[]
    for uid,bus in buses.items():
        if bus['class']!='PCB_TRACK' or (bus['net'],bus['layer'])!=(track['net'],track['layer']):continue
        c,z=bus['start'],bus['end'];e=(z[0]-c[0],z[1]-c[1]);den=d[0]*e[1]-d[1]*e[0]
        if not den:continue
        delta=(c[0]-a[0],c[1]-a[1]);num=delta[0]*e[1]-delta[1]*e[0];other=delta[0]*d[1]-delta[1]*d[0]
        if not 0<other/den<1:continue
        numerators=(a[0]*den+num*d[0],a[1]*den+num*d[1])
        if any(v%den for v in numerators):continue
        point=tuple(v//den for v in numerators)
        for end in sorted(ends):
            q=track[end];opposite=track['end' if end=='start' else 'start'];gap=math.dist(q,point)
            # An attachment must already be within the physical contact
            # envelope; near-parallel remote intersections are not repairs.
            perpendicular=abs((q[0]-c[0])*e[1]-(q[1]-c[1])*e[0])/math.hypot(*e)
            envelope=(track['width']+bus['width'])/2
            if not 0<gap<=2*envelope or perpendicular>envelope or point==tuple(opposite):continue
            choices.append((gap,end,point,uid))
    return [(end,point,uid) for _,end,point,uid in sorted(choices)]


def short_bus_overlap(track,buses):
    """Admit a short terminal overlap for proof, even with covered endpoints.

    An exact endpoint must lie strictly inside a longer same-face/net bus.
    This local geometry only proposes deletion; retained topology decides it.
    """
    if track['class']!='PCB_TRACK' or track['locked']:return False
    a,b=track['start'],track['end'];length2=sum((b[i]-a[i])**2 for i in (0,1))
    if not length2:return False
    for bus in buses.values():
        if bus['class']!='PCB_TRACK' or (bus['net'],bus['layer'])!=(track['net'],track['layer']):continue
        c,z=bus['start'],bus['end'];dx,dy=z[0]-c[0],z[1]-c[1];bus_length2=dx*dx+dy*dy
        if bus_length2<=length2 or length2>(track['width']+bus['width'])**2:continue
        for point in (a,b):
            x,y=point[0]-c[0],point[1]-c[1]
            if x*dy-y*dx==0 and 0<x*dx+y*dy<bus_length2:return True
    return False


def require_tail_topology(before_groups,before_pads,after_groups,after_pads,removed,added):
    from .joint_repair_contract import component_obligations,require_components
    if any(set(q)<=set(removed) for q in before_groups):raise ValueError('Tail cleanup would erase an original conductor island')
    require_components(component_obligations(before_groups,removed),after_groups,added=added)
    if {frozenset(q) for q in before_pads}!={frozenset(q) for q in after_pads}:
        raise ValueError('Tail cleanup changed a numbered-pad partition')


def require_tail_delta(before,after,generated,protected,removed,replacements):
    removed=set(removed);old=before['tracks'];new=after['tracks']
    if not removed<=set(generated)&old.keys() or removed&set(protected):raise ValueError('Tail change lacks positive ownership')
    if any(old[u]['locked'] or old[u]['class']!='PCB_TRACK' for u in removed):raise ValueError('Tail change includes locked or non-track copper')
    if set(old)-set(new)!=removed or before['footprints']!=after['footprints']:raise ValueError('Tail change altered original inventory')
    if any(new.get(u)!=q for u,q in old.items() if u not in removed):raise ValueError('Retained copper changed')
    if not replacements.keys()<=removed:raise ValueError('Undeclared endpoint replacement')
    ids={q['new_uuid'] for q in replacements.values()}
    if len(ids)!=len(replacements) or ids&old.keys() or set(new)-set(old)!=ids:raise ValueError('Replacement UUID inventory changed')
    for uid,row in replacements.items():
        ends=set(row)-{'new_uuid'}
        if len(ends)!=1 or not ends<={'start_nm','end_nm'}:raise ValueError('Expected exactly one endpoint replacement')
        key=next(iter(ends));expected=deepcopy(old[uid]);expected[key[:-3]]=list(row[key])
        if new[row['new_uuid']]!=expected:raise ValueError('Replacement changed non-endpoint properties')


def replace_endpoint(board,old,end,point,new_uuid,vec,*,verify=None):
    if end not in {'start','end'}:raise ValueError('Unknown endpoint')
    new=old.Duplicate();new.SetUuid(new_uuid)
    (new.SetStart if end=='start' else new.SetEnd)(vec(point))
    board.Remove(old);added=False
    try:
        board.Add(new);added=True
        if verify is not None:verify(new)
    except BaseException:
        if added:board.Remove(new)
        board.Add(old);raise
    return new


def _normalize(context,generated,protected,deadline):
    before=context.before;original=context.items();removed=set();replacements={};new_items={}
    eligible={u for u in set(generated)-set(protected) if u in before['tracks']
              and before['tracks'][u]['class']=='PCB_TRACK' and not before['tracks'][u]['locked']}
    # Geometry, not generated UUID order, decides which short exposed chain is
    # tested first. Every pruning step proves the original retained topology.
    ordered=sorted(eligible,key=lambda u:(math.dist(before['tracks'][u]['start'],before['tracks'][u]['end']),
                   tuple(before['tracks'][u]['start']),tuple(before['tracks'][u]['end']),u))
    try:
        for iteration in range(65):
            _check(deadline)
            if iteration==64:raise ValueError('Generated tail edit limit exhausted')
            changed=False;bare={};current=context.state()['tracks'];buses={}
            for uid,shape in current.items():
                if shape['class']=='PCB_TRACK':buses.setdefault((shape['net'],shape['layer']),{})[uid]=shape
            for uid in ordered:
                _check(deadline)
                if uid in removed:continue
                ends=context.bare_ends(uid)
                shape=current[uid]
                if not ends and not short_bus_overlap(shape,buses.get((shape['net'],shape['layer']),{})):continue
                bare[uid]=ends;item=original[uid];context.board.Remove(item)
                try:context.verify(removed|{uid},replacements)
                except ValueError:context.board.Add(item)
                except BaseException:context.board.Add(item);raise
                else:removed.add(uid);changed=True;break
            if changed:continue
            # Only after safe pruning stalls, correct a surviving branch end.
            # Replacements are never eligible for another speculative trim.
            for uid,ends in bare.items():
                _check(deadline)
                for end,point,bus in endpoint_candidates(current[uid],{u:q for u,q in current.items() if u!=uid},ends):
                    _check(deadline);identity=context.fresh_uuid();fresh=str(identity.AsString()) if hasattr(identity,'AsString') else str(identity)
                    proposed={**replacements,uid:{'new_uuid':fresh,end+'_nm':list(point)}}
                    def verify(item):context.verify(removed|{uid},proposed)
                    try:new=replace_endpoint(context.board,original[uid],end,point,identity,context.vec,verify=verify)
                    except ValueError:continue
                    removed.add(uid);replacements=proposed;new_items[fresh]=new;changed=True;break
                if changed:break
            if not changed:break
        context.verify(removed,replacements,final=True)
        from .joint_repair_contract import NativeRepairResult
        return NativeRepairResult({'removed_ids':sorted(removed),'added_ids':sorted(new_items),
            'endpoint_replacements':replacements}).retain_native(context,original,new_items)
    except BaseException:
        for item in new_items.values():context.board.Remove(item)
        for uid in removed:context.board.Add(original[uid])
        raise


class _NativeContext:
    def __init__(self,board,generated,protected,deadline):
        from smilepcb.native_worker import p,snapshot
        from smilepcb.copper_obstacles import copper_graphics
        from .native_graph import copper_graph
        self.p=p;self.snapshot=snapshot;self.board=board;self.generated=generated;self.protected=protected;self.deadline=deadline
        if any(not z.GetIsRuleArea() for z in board.Zones()):raise ValueError('Tail hygiene requires an unfilled saved candidate')
        if list(copper_graphics(board)):raise ValueError('Copper artwork needs an explicit tail attachment graph')
        self.before=self.state();self.original_items=self.items()
        if not set(protected)<=self.before['tracks'].keys():raise ValueError('Protected tail object is missing')
        self.nets={q['net'] for q in self.before['tracks'].values() if q['net']}|{q['net'] for f in self.before['footprints'].values() for q in f['pads'] if q['net']}
        self.graph_fn=copper_graph;graph=self.graph();self.groups,self.pads=self.partitions(graph)
        self.original_native={u:self.properties(q) for u,q in self.original_items.items()}
        self.endpoint_index=None
        self.original_shapes={u:self.shapes(q) for u,q in self.original_items.items()}
        self.original_fixed=self.fixed_world()

    def state(self):return self.snapshot(self.board)
    def items(self):return {q.m_Uuid.AsString():q for q in self.board.GetTracks()}
    def graph(self):return self.graph_fn(self.board,self.nets,self.deadline,include_zones=False)
    def vec(self,q):return self.p.VECTOR2I(*q)
    def fresh_uuid(self):return self.p.KIID()
    def partitions(self,graph):
        groups=[];pads=[]
        for indices in graph['groups'].values():
            groups.append({graph['nodes'][i]['item'].m_Uuid.AsString() for i in indices})
            row={graph['nodes'][i]['item'].m_Uuid.AsString() for i in indices if isinstance(graph['nodes'][i]['item'],self.p.PAD) and graph['nodes'][i]['item'].GetNumber()}
            if row:pads.append(row)
        return groups,pads

    def properties(self,item):
        from smilepcb.copper_obstacles import own_clearance
        names=('GetLocalClearance','HasSolderMask','GetLocalSolderMaskMargin','GetLocalRatsnestVisible',
               'GetTeardropsEnabled','GetTeardropBestLengthRatio','GetTeardropMaxLength','GetTeardropBestWidthRatio',
               'GetTeardropMaxWidth','GetTeardropCurved','GetTeardropPreferZoneConnections','GetTeardropAllowSpanTwoTracks','GetTeardropMaxTrackWidth')
        rules=self.board.GetDesignSettings()
        faces=tuple((face,own_clearance(item,face,rules.m_MinClearance)) for face in (self.p.F_Cu,self.p.B_Cu) if item.IsOnLayer(face))
        return (item.GetClass(),item.GetNetname(),faces,tuple(getattr(item,name)() for name in names) if item.GetClass()=='PCB_TRACK' else ())

    def shapes(self,item):
        faces=tuple((face,item.GetEffectiveShape(face).Format(False)) for face in (self.p.F_Cu,self.p.B_Cu) if item.IsOnLayer(face))
        hole=None
        if isinstance(item,self.p.PCB_VIA):hole=item.GetEffectiveHoleShape().Format(False)
        return faces,hole

    def fixed_world(self):
        from types import SimpleNamespace
        from .conditional_geometry import FrozenProofRouter
        class NoTracks:
            def __init__(self,board):self.board=board
            def GetTracks(self):return []
            def __getattr__(self,name):return getattr(self.board,name)
        return (FrozenProofRouter._physical_state(SimpleNamespace(board=NoTracks(self.board))),
                FrozenProofRouter._rule_state(SimpleNamespace(board=self.board)))

    def bare_ends(self,uid):
        _check(self.deadline);p=self.p;items=self.items();item=items[uid];face=item.GetLayer();net=item.GetNetname()
        key=frozenset(items)
        if self.endpoint_index is None or self.endpoint_index[0]!=key:
            buckets={};cell=5000000
            conductors=[*items.values(),*(d for f in self.board.GetFootprints() for d in f.Pads())]
            for other in conductors:
                _check(self.deadline);oid=other.m_Uuid.AsString()
                for layer in (p.F_Cu,p.B_Cu):
                    if not other.IsOnLayer(layer):continue
                    # A cap overlap can be electrically connected yet have a
                    # dangling centerline. Pads/vias keep their exact shape.
                    shape=p.SHAPE_SEGMENT(other.GetStart(),other.GetEnd(),0) if other.GetClass()=='PCB_TRACK' else other.GetEffectiveShape(layer)
                    box=shape.BBox()
                    for x in range(box.GetLeft()//cell,box.GetRight()//cell+1):
                        for y in range(box.GetTop()//cell,box.GetBottom()//cell+1):
                            buckets.setdefault((other.GetNetname(),layer,x,y),[]).append((oid,shape))
            self.endpoint_index=key,buckets
        buckets=self.endpoint_index[1];ends=set()
        for name,point in (('start',item.GetStart()),('end',item.GetEnd())):
            probe=p.SHAPE_CIRCLE(point,0)
            if not any(oid!=uid and p.SHAPE.Collide(probe,shape,0) for oid,shape in buckets.get((net,face,point.x//5000000,point.y//5000000),())):ends.add(name)
        return ends

    def verify(self,removed,replacements,final=False):
        _check(self.deadline);after=self.state();require_tail_delta(self.before,after,self.generated,self.protected,removed,replacements)
        current=self.items();added={q['new_uuid'] for q in replacements.values()}
        if any(self.properties(current[u])!=value for u,value in self.original_native.items() if u not in removed):raise ValueError('Retained native track properties changed')
        if any(self.properties(current[row['new_uuid']])!=self.original_native[u] for u,row in replacements.items()):raise ValueError('Replacement changed native track properties')
        graph=self.graph();groups,pads=self.partitions(graph)
        require_tail_topology(self.groups,self.pads,groups,pads,removed,added)
        if final and (self.fixed_world()!=self.original_fixed or any(self.shapes(current[u])!=q for u,q in self.original_shapes.items() if u not in removed)):
            raise ValueError('Tail hygiene changed retained native shapes, holes, pads or rules')
        if added:
            from .conditional_geometry import FrozenProofRouter
            proof=FrozenProofRouter(self.board,deadline=self.deadline,track_width_nm=min(current[u].GetWidth() for u in added),graph=graph,covered_nets=self.nets)
            for uid in sorted(added):
                _check(self.deadline);item=current[uid]
                if not proof.clear(self.p.SHAPE_SEGMENT(item.GetStart(),item.GetEnd(),item.GetWidth()),item.GetLayer(),item.GetNetname()):raise ValueError('Native endpoint replacement clearance failed')
            proof.assert_unchanged()


def normalize_generated_tails(board,*,generated_ids,protected_ids,deadline):
    """Normalize only positively owned, unlocked, unprotected original tracks.

    Returns removed_ids, added_ids and endpoint_replacements. All exceptions
    restore the original in-memory copper objects; no board or receipt is saved.
    """
    _check(deadline);generated=set(generated_ids);protected=set(protected_ids)
    if len(generated)>100000 or any(not isinstance(q,str) for q in generated|protected):raise ValueError('Invalid generated tail ownership inventory')
    context=_NativeContext(board,generated,protected,deadline)
    return _normalize(context,generated,protected,deadline)
