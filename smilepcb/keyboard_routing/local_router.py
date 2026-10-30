"""Bounded native-shape escape routing; no net/pin/ref/board coordinates.

This adds candidate copper only. Complete KiCad DRC and exact original-object
preservation remain mandatory. It is not a fabrication acceptance shortcut.
"""
import math
import time
from smilepcb.native_worker import p
from .safety import prepare_added_via
from .generation import current_generation_profile
from smilepcb.copper_obstacles import copper_graphics,own_clearance
from smilepcb.routing_quality import current_quality,ranked_paths
from smilepcb.keyboard_exchange import npth_exchange_clearance_nm


def xy(v): return (v.x,v.y)
def vec(v): return p.VECTOR2I(*v)
def distance(a,b): return math.hypot(a[0]-b[0],a[1]-b[1])


def paths(a,b,*,octile_only=False):
    """Straight, 45-degree, then symmetric local doglegs in increasing radius."""
    dx,dy=b[0]-a[0],b[1]-a[1]
    if not octile_only or not dx or not dy or abs(dx)==abs(dy):yield [a,b]
    yield [a,(a[0],b[1]),b]
    yield [a,(b[0],a[1]),b]
    dx,dy=b[0]-a[0],b[1]-a[1];m=min(abs(dx),abs(dy))
    yield [a,(a[0]+(m if dx>=0 else -m),a[1]+(m if dy>=0 else -m)),b]
    yield [a,(b[0]-(m if dx>=0 else -m),b[1]-(m if dy>=0 else -m)),b]
    for offset in range(500000,5000001,500000):
        for sign in (-1,1):
            y=a[1]+sign*offset;x=a[0]+sign*offset
            yield [a,(a[0],y),(b[0],y),b]
            yield [a,(x,a[1]),(x,b[1]),b]


class LocalRouter:
    def __init__(self,board,deadline=None,compact=False,ignored_copper=(),track_width_nm=None):
        self.deadline=deadline;self.compact=compact
        self.generation=current_generation_profile()
        self.quality=current_quality()
        rules=board.GetDesignSettings()
        self.track_width=max(p.FromMM(.2),rules.m_TrackMinWidth) if track_width_nm is None else track_width_nm
        if type(self.track_width) is not int or self.track_width<max(1,rules.m_TrackMinWidth):
            raise ValueError('Routing width must meet the native track minimum')
        # Respect the native minimum. A fixed 0.5 mm router-only guard can
        # seal off otherwise legal terminals beside milled LED cutouts.
        # Legacy zero-minimum projects still get a 0.25 mm physical margin.
        self.edge_clearance=(rules.m_CopperEdgeClearance if rules.m_CopperEdgeClearance>0 else p.FromMM(.25))+1000
        self.edge_clearance=max(self.edge_clearance,self.generation.edge_clearance_nm)
        self.drill=max(p.FromMM(.2),rules.m_MinThroughDrill or p.FromMM(.3))
        self.via_width=max(rules.m_ViasMinSize,self.drill+2*rules.m_ViasMinAnnularWidth,
                           p.FromMM(.5) if compact else p.FromMM(.6))
        self.board=board;self.cell=p.FromMM(4);self.buckets={p.F_Cu:{},p.B_Cu:{}}
        self.minimum_clearance=rules.m_MinClearance
        self.native_hole_clearance=max(p.FromMM(.25),rules.m_HoleClearance)
        self.hole_clearances={}
        self.net_clearances={}
        self.holes={};self.solder_lands={};self.edges=[];self.edge_buckets={};self.added=[]
        self.outline=p.SHAPE_POLY_SET()
        if not board.GetBoardPolygonOutlines(self.outline,False):raise ValueError('Invalid board outline')
        for fp in board.GetFootprints():
            for pad in fp.Pads():self.index(pad)
        for t in board.GetTracks():
            if t.m_Uuid.AsString() not in ignored_copper:self.index(t)
        for uid,shape,net,layer in copper_graphics(board):
            for cell in self.cells(shape.BBox(),self.minimum_clearance+1000):
                self.buckets[layer].setdefault(cell,[]).append((uid,shape,net,self.minimum_clearance+1000))
        from smilepcb.routing_style import edge_shapes
        for edge in edge_shapes(board):
            self.edges.append(edge)
            # Index the same exact centreline shapes, padded by the
            # narrow-phase clearance, including footprint cutouts.
            for cell in self.cells(edge.BBox(),self.edge_clearance):
                self.edge_buckets.setdefault(cell,[]).append(edge)
        self.reservations=[z for z in board.Zones() if z.GetIsRuleArea()]
        from smilepcb.routing_style import filled_zones
        for i,(shape,net,layer,gap) in enumerate(filled_zones(board)):
            for cell in self.cells(shape.BBox(),p.FromMM(.5)):
                self.buckets[layer].setdefault(cell,[]).append(('filled-zone-'+str(i),shape,net,max(gap,p.FromMM(.201))))

        self.minimum_clearance=max(self.minimum_clearance,self.generation.foreign_copper_clearance_nm)
        self.net_clearances={net:max(gap,self.generation.foreign_copper_clearance_nm)
                             for net,gap in self.net_clearances.items()}

    def cells(self,bounds,padding=0):
        for x in range((bounds.GetLeft()-padding)//self.cell,(bounds.GetRight()+padding)//self.cell+1):
            for y in range((bounds.GetTop()-padding)//self.cell,(bounds.GetBottom()+padding)//self.cell+1):yield x,y

    def index(self,item):
        uid=item.m_Uuid.AsString()
        for layer in self.buckets:
            if item.IsOnLayer(layer):
                shape=item.GetEffectiveShape(layer)
                clearance=own_clearance(item,layer,self.minimum_clearance)
                self.net_clearances[item.GetNetname()]=max(self.net_clearances.get(item.GetNetname(),0),clearance)
                for cell in self.cells(shape.BBox(),max(clearance,p.FromMM(.5))):
                    self.buckets[layer].setdefault(cell,[]).append((uid,shape,item.GetNetname(),clearance))
                if isinstance(item,p.PAD) and item.GetAttribute()==p.PAD_ATTRIB_SMD:
                    for cell in self.cells(shape.BBox(),p.FromMM(.1)):
                        self.solder_lands.setdefault(cell,[]).append((uid,shape))
        if isinstance(item,p.PAD):
            drill=item.GetDrillSize();hole=item.GetEffectiveHoleShape() if drill.x and drill.y else None
        elif isinstance(item,p.PCB_VIA):hole=item.GetEffectiveHoleShape()
        else:hole=None
        if hole:
            gap=self.native_hole_clearance+1000
            if (self.generation.name=='dsn-safe-v1' and isinstance(item,p.PAD) and
                item.GetAttribute()==p.PAD_ATTRIB_NPTH and drill.x==drill.y):
                gap=max(gap,npth_exchange_clearance_nm(self.native_hole_clearance,drill.x))
            self.hole_clearances[uid]=gap
            for cell in self.cells(hole.BBox(),max(p.FromMM(.5),gap)):
                self.holes.setdefault(cell,[]).append((uid,hole,item.GetNetname()))

    def clear(self,shape,layer,net,via=False):
        return next(self.collisions(shape,layer,net,via),None) is None

    def collisions(self,shape,layer,net,via=False):
        box=shape.BBox()
        for area in self.reservations:
            blocked=area.GetDoNotAllowVias() if via else area.GetDoNotAllowTracks()
            if blocked and area.IsOnLayer(layer) and p.SHAPE.Collide(shape,area.Outline(),0):
                yield area.m_Uuid.AsString()
        # A segment capsule or via circle is connected and contains its
        # centre. One interior witness plus exact boundary collisions below
        # proves it stays in material. Bounding-box corners can lie inside a
        # milled window even when the actual diagonal copper clears its edge.
        # GetEffectiveShape returns a base SHAPE proxy in KiCad's Python
        # binding. Use the native kind, otherwise a saved track and the
        # identical planned capsule get different material tests.
        probes=(((box.GetLeft()+box.GetRight())//2,(box.GetTop()+box.GetBottom())//2),) if shape.Type() in (
            p.SH_SEGMENT,p.SH_CIRCLE) else ((box.GetLeft(),box.GetTop()),(box.GetRight(),box.GetBottom()))
        for x,y in probes:
            if not self.outline.Contains(p.VECTOR2I(x,y)):yield 'board-outline'
        seen_edges=set()
        for cell in self.cells(box):
            for edge in self.edge_buckets.get(cell,[]):
                key=id(edge)
                if key in seen_edges:continue
                seen_edges.add(key)
                if p.SHAPE.Collide(shape,edge,self.edge_clearance):yield 'board-edge'
        seen=set()
        for cell in self.cells(box,max(self.net_clearances.get(net,0),self.generation.foreign_copper_clearance_nm)):
            for uid,other,othernet,gap in self.buckets[layer].get(cell,[]):
                if uid in seen or othernet==net:continue
                seen.add(uid)
                if p.SHAPE.Collide(shape,other,max(gap,self.net_clearances.get(net,0),self.generation.foreign_copper_clearance_nm)):yield uid
            for uid,hole,othernet in self.holes.get(cell,[]):
                if not via and othernet==net:continue
                # Copper-to-hole is stricter than the drill-to-drill minimum.
                if p.SHAPE.Collide(shape,hole,self.hole_clearances[uid]):yield uid

    def expired(self):
        return self.deadline is not None and time.monotonic()>=self.deadline

    def leg(self,a,b,layer,net):
        for path in ranked_paths(paths(a,b,octile_only=self.generation.name=='dsn-safe-v1'),self.quality):
            if self.expired():return None
            path=[v for i,v in enumerate(path) if not i or v!=path[i-1]]
            if all(self.clear(p.SHAPE_SEGMENT(vec(x),vec(y),self.track_width),layer,net)
                   for x,y in zip(path,path[1:])):return path
        return None

    def via_clear(self,position,net):
        # Ordinary unfilled vias may join same-net copper, but their drill
        # must stay outside all SMD solder lands on both board faces.
        hole=p.SHAPE_CIRCLE(vec(position),self.drill//2);seen=set()
        for cell in self.cells(hole.BBox()):
            if self.expired():return False
            for uid,land in self.solder_lands.get(cell,()):
                if uid in seen:continue
                seen.add(uid)
                if p.SHAPE.Collide(hole,land,p.FromMM(.1)):return False
        shape=p.SHAPE_CIRCLE(vec(position),self.via_width//2)
        if not all(self.clear(shape,layer,net,via=True) for layer in self.buckets):return False
        if self.generation.name=='native':return True
        # The broader annulus guard applies even to same-net solder lands and
        # holes. Broad-phase padding must include both stronger clearances.
        seen_lands=set();seen_holes=set()
        padding=max(self.generation.via_to_smd_clearance_nm,self.generation.via_to_hole_clearance_nm)
        for cell in self.cells(shape.BBox(),padding):
            if self.expired():return False
            for uid,land in self.solder_lands.get(cell,()):
                if uid in seen_lands:continue
                seen_lands.add(uid)
                if p.SHAPE.Collide(shape,land,self.generation.via_to_smd_clearance_nm):return False
            for uid,hole,othernet in self.holes.get(cell,()):
                if uid in seen_holes:continue
                seen_holes.add(uid)
                if p.SHAPE.Collide(shape,hole,self.generation.via_to_hole_clearance_nm):return False
        return True

    def via_pair_clear(self,a,z):
        """Check planned annuli against the other planned physical hole."""
        ac=p.SHAPE_CIRCLE(vec(a),self.via_width//2);ah=p.SHAPE_CIRCLE(vec(a),self.drill//2)
        zc=p.SHAPE_CIRCLE(vec(z),self.via_width//2);zh=p.SHAPE_CIRCLE(vec(z),self.drill//2)
        gap=self.generation.via_to_hole_clearance_nm
        return not (p.SHAPE.Collide(ac,zh,gap) or p.SHAPE.Collide(zc,ah,gap))

    def add_path(self,path,layer,net):
        for a,b in zip(path,path[1:]):
            if a==b:continue
            t=p.PCB_TRACK(self.board);t.SetStart(vec(a));t.SetEnd(vec(b));t.SetWidth(self.track_width);t.SetLayer(layer)
            t.SetNet(self.board.FindNet(net));self.board.Add(t);self.index(t);self.added.append(t.m_Uuid.AsString())

    def add_via(self,position,net):
        t=p.PCB_VIA(self.board);t.SetPosition(vec(position));t.SetWidth(self.via_width);t.SetDrill(self.drill)
        t.SetViaType(p.VIATYPE_THROUGH);t.SetLayerPair(p.F_Cu,p.B_Cu);t.SetNet(self.board.FindNet(net))
        prepare_added_via(t);self.board.Add(t);self.index(t);self.added.append(t.m_Uuid.AsString())

    def endpoints(self,item,toward):
        layers=[layer for layer in self.buckets if item.IsOnLayer(layer)]
        if isinstance(item,p.PCB_TRACK) and not isinstance(item,p.PCB_VIA):
            a,b=xy(item.GetStart()),xy(item.GetEnd());dx,dy=b[0]-a[0],b[1]-a[1]
            length=dx*dx+dy*dy
            t=max(0,min(1,((toward[0]-a[0])*dx+(toward[1]-a[1])*dy)/length)) if length else 0
            pos=(round(a[0]+t*dx),round(a[1]+t*dy))
        else:pos=xy(item.GetPosition())
        return pos,layers

    def connect(self,source,target):
        net=source.GetNetname()
        if not net or net!=target.GetNetname():raise ValueError('Cannot join different nets')
        a,la=self.endpoints(source,xy(target.GetPosition()))
        b,lb=self.endpoints(target,a)
        for layer in set(la)&set(lb):
            path=self.leg(a,b,layer,net)
            if path is not None:self.add_path(path,layer,net);return True
        if getattr(self,'grid_only',False):
            from .grid_search import search
            return search(self,a,b,la,lb,net)
        # Via candidates come from endpoint geometry, not a board-specific
        # corridor. Try nearest symmetric escapes first; native shapes decide.
        candidates=set()
        for origin in (a,b):
            for radius in range(500000,3000001,500000):
                for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                    candidates.add((origin[0]+dx*radius,origin[1]+dy*radius))
        if self.compact:
            for origin in (a,b):
                for radius in range(500000,2500001,100000):
                    for degrees in range(0,360,15):
                        angle=math.radians(degrees)
                        candidates.add((origin[0]+round(radius*math.cos(angle)),origin[1]+round(radius*math.sin(angle))))
        candidates=sorted(candidates,key=lambda v:distance(a,v)+distance(v,b))[:512 if self.compact else 128]
        if not set(la)&set(lb):
            for v in candidates:
                if self.expired():return False
                if not self.via_clear(v,net):continue
                pa=self.leg(a,v,la[0],net)
                if pa is None:continue
                pb=self.leg(v,b,lb[0],net)
                if pb is not None:
                    self.add_path(pa,la[0],net);self.add_via(v,net);self.add_path(pb,lb[0],net);return True
        else:
            # Two escapes can bypass a layer-specific obstruction. Limit each
            # endpoint to its eight nearest legal escapes to bound the search.
            layer=next(iter(set(la)&set(lb)));other=p.B_Cu if layer==p.F_Cu else p.F_Cu
            left=[];right=[]
            for v in sorted(candidates,key=lambda v:distance(a,v)):
                if self.expired():return False
                if len(left)==8:break
                if self.via_clear(v,net):
                    path=self.leg(a,v,layer,net)
                    if path is not None:left.append((v,path))
            for v in sorted(candidates,key=lambda v:distance(b,v)):
                if self.expired():return False
                if len(right)==8:break
                if self.via_clear(v,net):
                    path=self.leg(v,b,layer,net)
                    if path is not None:right.append((v,path))
            pairs=sorted(((distance(a,v)+distance(v,z)+distance(z,b),v,z,pa,pb)
                          for v,pa in left for z,pb in right if distance(v,z)>=p.FromMM(.61)))
            for _,v,z,pa,pb in pairs:
                if self.expired():return False
                if self.generation.name!='native' and not self.via_pair_clear(v,z):continue
                mid=self.leg(v,z,other,net)
                if mid is not None:
                    self.add_path(pa,layer,net);self.add_via(v,net);self.add_path(mid,other,net)
                    self.add_via(z,net);self.add_path(pb,layer,net);return True
        if self.compact and not self.expired():
            from .grid_search import search
            return search(self,a,b,la,lb,net)
        return False
