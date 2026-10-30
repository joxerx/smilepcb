"""Native matrix trunks and 45-degree approaches with explicit edit ownership.

No file discovery or persistence: callers operate on a candidate and retain the
full native acceptance gate. Current callers use two-layer synthetic fixtures.
"""
from collections import defaultdict
import math
import time
from .native_worker import p,snapshot
from .copper_obstacles import copper_graphics,net_clearances,own_clearance


def xy(v):return (v.x,v.y)
def vec(v):return p.VECTOR2I(*v)


def edge_shapes(board):
    """Native milling boundaries, independent of the display stroke width.

    KiCad's edge-clearance checker clones Edge.Cuts with zero stroke width.
    Retain the original board and footprint graphics unchanged.
    """
    for graphic in [*board.GetDrawings(),*(g for fp in board.GetFootprints() for g in fp.GraphicalItems())]:
        if isinstance(graphic,p.PCB_SHAPE) and graphic.GetLayer()==p.Edge_Cuts:
            copy=graphic.Duplicate();copy.SetWidth(0)
            yield copy.GetEffectiveShape()


def on_segment(pos,a,b):
    return ((pos[0]-a[0])*(b[1]-a[1])==(pos[1]-a[1])*(b[0]-a[0])
            and min(a[0],b[0])<=pos[0]<=max(a[0],b[0])
            and min(a[1],b[1])<=pos[1]<=max(a[1],b[1]))
def ray(pos,end):
    dx,dy=end[0]-pos[0],end[1]-pos[1];g=math.gcd(dx,dy)
    return (dx//g,dy//g) if g else None
def dot(a,b):return a[0]*b[0]+a[1]*b[1]
def span(a,b):return max(abs(a[0]-b[0]),abs(a[1]-b[1]))
def shift(pos,direction,d):return tuple(pos[i]+direction[i]*d for i in (0,1))
def endpoint(t,pos):return pos in (xy(t.GetStart()),xy(t.GetEnd()))
def other_end(t,pos):return xy(t.GetEnd()) if xy(t.GetStart())==pos else xy(t.GetStart())
def move_end(t,pos,new):
    if xy(t.GetStart())==pos:t.SetStart(vec(new))
    elif xy(t.GetEnd())==pos:t.SetEnd(vec(new))
    else:raise ValueError('Track no longer ends at selected junction')


def deadline(seconds):
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=45:
        raise ValueError('Native recipe budget must be positive and at most 45 seconds')
    return time.monotonic()+seconds


def filled_zones(board):
    """Use actual filled copper, never a zone's uncut boundary as an obstacle.

    Callers refill candidate boards before and after edits and run native DRC.
    Stale, absent or empty fills cannot safely establish clearance/contact.
    """
    if board.GetCopperLayerCount()!=2:
        raise ValueError('Style recipes currently support two copper layers')
    result=[]
    for zone in board.Zones():
        if zone.GetIsRuleArea():continue
        if not zone.IsFilled() or zone.NeedRefill():
            raise ValueError('Refill copper zones before applying routing style')
        for layer in (p.F_Cu,p.B_Cu):
            if not zone.IsOnLayer(layer):continue
            shape=zone.GetFilledPolysList(layer)
            if not zone.HasFilledPolysForLayer(layer) or shape.OutlineCount()==0:
                raise ValueError('Refill copper zones; empty fills are not supported')
            # Filled copper can have disconnected islands. Retaining contact
            # with one island does not prove contact with every other island.
            for index in range(shape.OutlineCount()):
                result.append((shape.UnitSet(index),zone.GetNetname(),layer,zone.GetLocalClearance()))
    return result


class Clearance:
    """Conservative native shapes, project minima, holes and rule areas.

    The final KiCad DRC also applies custom rules and board policy. Existing
    foreign copper stays indexed conservatively even when subsequently trimmed.
    """
    def __init__(self,board):
        self.board=board;rules=board.GetDesignSettings()
        self.edge=max(p.FromMM(.2),rules.m_CopperEdgeClearance)+1000
        self.hole=max(p.FromMM(.25),rules.m_HoleClearance)+1000
        self.gap=max(p.FromMM(.2),rules.m_MinClearance)+1000
        self.net_clearances=net_clearances(board,rules.m_MinClearance)
        self.copper={p.F_Cu:[],p.B_Cu:[]};self.holes=[];self.edges=[]
        self.outline=p.SHAPE_POLY_SET()
        if not board.GetBoardPolygonOutlines(self.outline,False):raise ValueError('Invalid board outline')
        for fp in board.GetFootprints():
            for pad in fp.Pads():self.index(pad)
        for t in board.GetTracks():self.index(t)
        for _,shape,net,layer in copper_graphics(board):
            self.copper[layer].append((shape,net,self.gap))
        self.edges.extend(edge_shapes(board))
        self.areas=[z for z in board.Zones() if z.GetIsRuleArea()]
        for shape,net,layer,gap in filled_zones(board):
            self.copper[layer].append((shape,net,max(self.gap,gap or 0)))

    def index(self,item):
        for layer,entries in self.copper.items():
            if item.IsOnLayer(layer):
                gap=max(self.gap,own_clearance(item,layer,self.board.GetDesignSettings().m_MinClearance))
                entries.append((item.GetEffectiveShape(layer),item.GetNetname(),gap))
                self.net_clearances[item.GetNetname()]=max(self.net_clearances.get(item.GetNetname(),0),gap)
        if isinstance(item,p.PAD):
            drill=item.GetDrillSize();hole=item.GetEffectiveHoleShape() if drill.x and drill.y else None
        elif isinstance(item,p.PCB_VIA):hole=item.GetEffectiveHoleShape()
        else:hole=None
        if hole:self.holes.append((hole,item.GetNetname()))

    def clear(self,a,b,width,layer,net):
        shape=p.SHAPE_SEGMENT(vec(a),vec(b),width)
        # The connected capsule contains its midpoint. Exact edge checks
        # below enforce its full width without rejecting empty box corners.
        if not self.outline.Contains(p.VECTOR2I((a[0]+b[0])//2,(a[1]+b[1])//2)):return False
        if any(p.SHAPE.Collide(shape,e,self.edge) for e in self.edges):return False
        if any(othernet!=net and p.SHAPE.Collide(shape,s,max(gap,self.net_clearances.get(net,0))) for s,othernet,gap in self.copper[layer]):return False
        if any(othernet!=net and p.SHAPE.Collide(shape,h,self.hole) for h,othernet in self.holes):return False
        if any(z.IsOnLayer(layer) and z.GetDoNotAllowTracks() and p.SHAPE.Collide(shape,z.Outline(),0) for z in self.areas):return False
        return True


def inspect_angles(board):
    groups=defaultdict(list);points=defaultdict(set)
    for t in board.GetTracks():
        if t.GetClass()!='PCB_TRACK':continue
        key=(t.GetNetname(),t.GetLayer());groups[key].append(t)
        points[key].update((xy(t.GetStart()),xy(t.GetEnd())))
    pads=[d for f in board.GetFootprints() for d in f.Pads()]
    vias=[t for t in board.GetTracks() if t.GetClass()=='PCB_VIA']
    result=[]
    for (net,layer),tracks in sorted(groups.items()):
        for pos in sorted(points[net,layer]):
            incident=[t for t in tracks if on_segment(pos,xy(t.GetStart()),xy(t.GetEnd()))]
            rays={v for t in incident for end in (xy(t.GetStart()),xy(t.GetEnd())) if (v:=ray(pos,end))}
            if not any(dot(a,b)==0 for a in rays for b in rays if a!=b):continue
            probe=p.SHAPE_CIRCLE(vec(pos),1)
            covered=lambda items:any(t.GetNetname()==net and t.IsOnLayer(layer) and p.SHAPE.Collide(probe,t.GetEffectiveShape(layer),0) for t in items)
            kind='pad' if covered(pads) else 'via' if covered(vias) else 'elbow' if len(rays)==2 else 'junction'
            shortest=min(span(pos,end) for t in incident for end in (xy(t.GetStart()),xy(t.GetEnd())) if end!=pos)
            result.append({'net':net,'layer':layer,'position':list(pos),'kind':kind,
                           'visible':kind not in ('pad','via') and shortest>=min(t.GetWidth() for t in incident)/2,
                           'track_ids':sorted(t.m_Uuid.AsString() for t in incident)})
    return result


def add_path(board,oracle,path,layer,net,width):
    ids=[]
    for a,b in zip(path,path[1:]):
        if a==b:continue
        t=p.PCB_TRACK(board);t.SetStart(vec(a));t.SetEnd(vec(b));t.SetWidth(width)
        t.SetLayer(layer);t.SetNet(board.FindNet(net));board.Add(t);oracle.index(t)
        ids.append(t.m_Uuid.AsString())
    return ids


def seed_trunks(board,*,pad_ids,layer,width_nm=200000,budget_s=30):
    end=deadline(budget_s)
    pads={d.m_Uuid.AsString():d for f in board.GetFootprints() for d in f.Pads()}
    if (layer not in (p.F_Cu,p.B_Cu) or type(width_nm) is not int or width_nm<=0
            or len(set(pad_ids))!=len(pad_ids) or set(pad_ids)-pads.keys()):
        raise ValueError('Invalid trunk pad selection, width or layer')
    groups=defaultdict(list)
    for uid in pad_ids:
        pad=pads[uid]
        if not pad.GetNetname() or not pad.IsOnLayer(layer):raise ValueError('Trunk pads must have a net and the selected layer')
        groups[pad.GetNetname()].append(xy(pad.GetPosition()))
    before=snapshot(board);oracle=Clearance(board);report=[];added=[]
    retained_nets={t['net'] for t in before['tracks'].values()}
    for net,positions in sorted(groups.items()):
        if time.monotonic()>end:raise TimeoutError('Trunk search deadline exhausted')
        points=sorted(set(positions));ys=[q[1] for q in points]
        if net in retained_nets:
            report.append({'net':net,'seeded':False,'reason':'net already has copper'});continue
        if len(points)<2 or max(ys)-min(ys)>p.FromMM(.25):
            report.append({'net':net,'seeded':False,'reason':'pads are not a horizontal aligned group'});continue
        mean=round(sum(ys)/len(ys));chosen=None
        offsets=[0]+[v for i in range(1,37) for v in (i*250000,-i*250000)]
        for offset in offsets:
            if time.monotonic()>end:raise TimeoutError('Trunk search deadline exhausted')
            y=mean+offset;trunk=[(points[0][0],y),(points[-1][0],y)]
            branches=[[pt,(pt[0],y)] for pt in points if pt[1]!=y]
            if all(oracle.clear(a,b,width_nm,layer,net) for path in [trunk]+branches for a,b in zip(path,path[1:])):
                chosen=(trunk,branches);break
        if chosen:
            trunk,branches=chosen
            for path in [trunk]+branches:added.extend(add_path(board,oracle,path,layer,net,width_nm))
            report.append({'net':net,'seeded':True,'pad_count':len(points),'lane_y_nm':trunk[0][1]})
        else:report.append({'net':net,'seeded':False,'reason':'no clear lane within the search band'})
    board.BuildConnectivity()
    return {'groups':report,'added_ids':added}


def octile_paths(a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];d=min(abs(dx),abs(dy))
    sx=1 if dx>=0 else -1;sy=1 if dy>=0 else -1
    for path in ([a,(a[0]+sx*d,a[1]+sy*d),b],[a,(b[0]-sx*d,b[1]-sy*d),b]):
        yield [v for i,v in enumerate(path) if not i or v!=path[i-1]]


def available_run(board,track,pos,tip):
    """Stop trimming before another same-net attachment on this arm."""
    points=[];net=track.GetNetname();layer=track.GetLayer()
    for other in board.GetTracks():
        if other.GetNetname()==net and other.IsOnLayer(layer):
            points.extend((xy(other.GetStart()),xy(other.GetEnd())))
    for fp in board.GetFootprints():
        points.extend(xy(d.GetPosition()) for d in fp.Pads() if d.GetNetname()==net and d.IsOnLayer(layer))
    return min([span(pos,tip)]+[span(pos,q) for q in points if q!=pos and on_segment(q,pos,tip)])


def contacts_survive(board,pos,moves,paths,width,layer,net):
    """The replacement stays connected and retains every old copper contact.

    Physical intersections can occur inside a track or away from a pad/via
    centre. Point incidence alone cannot attest those connections.
    """
    moved={t.m_Uuid.AsString() for t,q in moves}
    old=[t.GetEffectiveShape(layer) for t,q in moves]
    new=[p.SHAPE_SEGMENT(vec(other_end(t,pos)),vec(q),t.GetWidth()) for t,q in moves]
    new.extend(p.SHAPE_SEGMENT(vec(a),vec(z),width) for path in paths for a,z in zip(path,path[1:]))
    others=[*board.GetTracks(),*(d for f in board.GetFootprints() for d in f.Pads())]
    for item in others:
        if item.m_Uuid.AsString() in moved or item.GetNetname()!=net or not item.IsOnLayer(layer):continue
        shape=item.GetEffectiveShape(layer)
        if any(p.SHAPE.Collide(s,shape,0) for s in old) and not any(p.SHAPE.Collide(s,shape,0) for s in new):return False
    for shape,zone_net,zone_layer,_ in filled_zones(board):
        if zone_net==net and zone_layer==layer:
            if any(p.SHAPE.Collide(s,shape,0) for s in old) and not any(p.SHAPE.Collide(s,shape,0) for s in new):return False
    return True


def apply_style(board,*,editable_ids,budget_s=45):
    end=deadline(budget_s);before=snapshot(board);editable=set(editable_ids)
    if editable-before['tracks'].keys():raise ValueError('Unknown editable copper object')
    protected={uid:t for uid,t in before['tracks'].items() if uid not in editable or t['locked'] or t['class']!='PCB_TRACK'}
    editable-=protected.keys();changes=0;added=[]
    initial=inspect_angles(board)
    for iteration in range(8):
        changed=0
        # Previous accepted trims can free space for another net. A fresh
        # obstacle snapshot is needed before declaring a geometric fixpoint.
        oracle=Clearance(board)
        by_id={t.m_Uuid.AsString():t for t in board.GetTracks()}
        for node in inspect_angles(board):
            if time.monotonic()>end:raise TimeoutError('Style deadline exhausted')
            if not node['visible']:continue
            pos=tuple(node['position']);net=node['net'];layer=node['layer']
            tracks=[by_id[uid] for uid in node['track_ids']]
            if not all(on_segment(pos,xy(t.GetStart()),xy(t.GetEnd())) for t in tracks):continue
            rays={ray(pos,e) for t in tracks for e in (xy(t.GetStart()),xy(t.GetEnd())) if e!=pos}
            if any(max(abs(v[0]),abs(v[1]))!=1 for v in rays):continue
            paths=[];moves=[]
            if node['kind']=='elbow' and len(tracks)==2:
                if any(t.m_Uuid.AsString() not in editable or not endpoint(t,pos) for t in tracks):continue
                if tracks[0].GetWidth()!=tracks[1].GetWidth():continue
                ends=[other_end(t,pos) for t in tracks]
                radius=min(750000,int(.4*min(available_run(board,t,pos,e) for t,e in zip(tracks,ends))));width=tracks[0].GetWidth()
                for d in sorted({max(1,round(radius*f)) for f in (1,.75,.5,.25,.1)},reverse=True):
                    if d<width:continue
                    q=[shift(pos,ray(pos,e),d) for e in ends]
                    if oracle.clear(*q,width,layer,net) and contacts_survive(board,pos,list(zip(tracks,q)),[q],width,layer,net):
                        moves=list(zip(tracks,q));paths=[q];break
            elif node['kind']=='junction':
                branches=[t for t in tracks if t.m_Uuid.AsString() in editable and endpoint(t,pos)
                          and any(dot(ray(pos,other_end(t,pos)),v)==0 for v in rays)]
                branches.sort(key=lambda t:(-span(pos,other_end(t,pos)),other_end(t,pos)))
                for branch in branches:
                    b=ray(pos,other_end(branch,pos));width=branch.GetWidth()
                    targets=[(t,ray(pos,e),e) for t in tracks if t!=branch for e in (xy(t.GetStart()),xy(t.GetEnd())) if e!=pos]
                    targets.sort(key=lambda x:(tuple(-v for v in x[1]) not in rays,tuple(-v for v in x[1]),-span(pos,x[2])))
                    for target,direction,tip in targets:
                        radius=min(750000,int(.4*available_run(board,branch,pos,other_end(branch,pos))),int(.4*available_run(board,target,pos,tip)))
                        for d in sorted({max(1,round(radius*f)) for f in (1,.75,.5,.25,.1)},reverse=True):
                            if d<width:continue
                            q=shift(pos,b,d);landing=shift(pos,direction,d)
                            if not on_segment(landing,xy(target.GetStart()),xy(target.GetEnd())):continue
                            for path in octile_paths(q,landing):
                                if len(path)<2:continue
                                directions=[ray(a,z) for a,z in zip(path,path[1:])]
                                if dot(b,directions[0])>=0:continue
                                if any(dot(tuple(-v for v in a),z)>=0 for a,z in zip(directions,directions[1:])):continue
                                if dot(directions[-1],direction)==0:continue
                                if directions[-1][0]*direction[1]==directions[-1][1]*direction[0]:continue
                                if any(on_segment(pos,a,z) for a,z in zip(path,path[1:])):continue
                                if not all(oracle.clear(a,z,width,layer,net) for a,z in zip(path,path[1:])):continue
                                if not contacts_survive(board,pos,[(branch,q)],[path],width,layer,net):continue
                                moves=[(branch,q)];paths=[path];break
                            if paths:break
                        if paths:break
                    if paths:break
            if not paths:continue
            for t,q in moves:move_end(t,pos,q)
            for path in paths:
                ids=add_path(board,oracle,path,layer,net,width);editable.update(ids);added.extend(ids)
            changed+=1
        changes+=changed
        if not changed:break
    else:raise ValueError('Style did not reach a stable result within eight passes')
    board.BuildConnectivity();after=snapshot(board)
    if after['footprints']!=before['footprints'] or any(after['tracks'].get(uid)!=t for uid,t in protected.items()):
        raise ValueError('Style changed protected copper or footprints')
    return {'changes':changes,'added_ids':added,'before':initial,'after':inspect_angles(board),
            'protected_objects':len(protected)}
