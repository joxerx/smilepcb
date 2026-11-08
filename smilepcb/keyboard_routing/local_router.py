"""Bounded native-shape escape routing; no net/pin/ref/board coordinates.

This adds candidate copper only. Complete KiCad DRC and exact original-object
preservation remain mandatory. It is not a fabrication acceptance shortcut.
"""
import math
import time
from smilepcb.native_worker import p
from .safety import prepare_added_via
from .generation import current_generation_profile,bind_generation_profile,uses_exchange_geometry
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
    def __init__(self,board,deadline=None,compact=False,ignored_copper=(),track_width_nm=None,path_policy=None,spatial_cell_nm=1000000,deferred_zone_ids=()):
        self.deadline=deadline;self.compact=compact;self.path_policy=path_policy;self._quality_tracks={};self._bundle_ranker=None
        self.quality=current_quality()
        rules=board.GetDesignSettings()
        self.native_hole_clearance=max(p.FromMM(.25),rules.m_HoleClearance)
        self.generation=bind_generation_profile(current_generation_profile(),
            native_copper_clearance_nm=rules.m_MinClearance,native_hole_clearance_nm=self.native_hole_clearance)
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
        if type(spatial_cell_nm) is not int or not 250000<=spatial_cell_nm<=8000000:
            raise ValueError('Spatial index cell must be 0.25..8 mm')
        self.board=board;self.cell=spatial_cell_nm;self.buckets={p.F_Cu:{},p.B_Cu:{}}
        self.minimum_clearance=rules.m_MinClearance
        self.hole_clearances={}
        self.net_clearances={}
        self.holes={};self.solder_lands={};self.edges=[];self.edge_buckets={};self.added=[]
        self.outline=p.SHAPE_POLY_SET()
        if not board.GetBoardPolygonOutlines(self.outline,False):raise ValueError('Invalid board outline')
        if self.generation.name=='dsn-process-v2':
            from .exchange_pad_envelope import import_grid_nm
            bounds=board.GetBoardEdgesBoundingBox()
            self._pad_import_grid_nm=import_grid_nm((bounds.GetLeft(),bounds.GetTop(),bounds.GetRight(),bounds.GetBottom()))
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
        fills=filled_zones(board,deferred_ids=deferred_zone_ids) if deferred_zone_ids else filled_zones(board)
        for i,(shape,net,layer,gap) in enumerate(fills):
            for cell in self.cells(shape.BBox(),p.FromMM(.5)):
                self.buckets[layer].setdefault(cell,[]).append(('filled-zone-'+str(i),shape,net,max(gap,p.FromMM(.201))))

        self.minimum_clearance=max(self.minimum_clearance,self.generation.foreign_copper_clearance_nm)
        self.net_clearances={net:max(gap,self.generation.foreign_copper_clearance_nm)
                             for net,gap in self.net_clearances.items()}
        from .interface_launches import current_launches,current_launch_inventory
        from .port_policy import PortPolicy
        ports=current_launches()
        self.launch_inventory=current_launch_inventory()
        self.launch_policy=PortPolicy(board,ports,track_width_nm=self.track_width,resolved=self.launch_inventory.resolved,repair_reserved=self.launch_inventory.repair_reserved) if ports else None
        self._captured_launch_policy=self.launch_policy;self._port_policy=None

    @property
    def port_policy(self):
        return self._port_policy or self.launch_policy

    @port_policy.setter
    def port_policy(self,policy):
        if policy is None:
            self._port_policy=None;self.launch_policy=self._captured_launch_policy;return
        from .interface_launches import interface_identity
        from .port_policy import PortPolicy
        inherited=self.launch_policy
        held=set(getattr(policy,'repair_reserved',()))
        if inherited is not None:held.update(getattr(inherited,'repair_reserved',()))
        if self.launch_inventory is not None:held.update(self.launch_inventory.repair_reserved)
        if inherited is not None:
            merged={interface_identity(q):q for q in inherited.ports}
            incoming={interface_identity(q):q for q in policy.ports};merged.update(incoming)
            effective=policy if merged.keys()==incoming.keys() else PortPolicy(self.board,list(merged.values()),track_width_nm=self.track_width,resolved=inherited.resolved|policy.resolved,repair_reserved=held)
            effective.set_resolved(inherited.resolved|policy.resolved)
        else:effective=policy
        if self.launch_inventory is not None:effective.set_resolved(effective.resolved|self.launch_inventory.resolved)
        if held:effective.reserve_repair(held)
        # Both foreign reservations and first-contact checks use this exact
        # terminal inventory; an old scoped envelope cannot survive an advance.
        self._port_policy=effective;self.launch_policy=effective

    @port_policy.deleter
    def port_policy(self):
        self.port_policy=None

    def refresh_interfaces(self,graph):
        if self.launch_inventory is not None:self.launch_inventory.refresh(graph)
        seen=set()
        for policy in (self.launch_policy,self.port_policy):
            if policy is not None and id(policy) not in seen:
                seen.add(id(policy));policy.refresh(graph)

    def _refresh_source_net(self,net):
        if self.expired():raise TimeoutError('Controller source refresh deadline exhausted')
        policy=self.port_policy
        if policy is None:return
        from .interface_launches import interface_identity
        if any(q['net']==net and interface_identity(q) in policy.resolved for q in policy.ports):
            from .native_graph import copper_graph
            self.refresh_interfaces(copper_graph(self.board,{net},self.deadline))
        if self.expired():raise TimeoutError('Controller source refresh deadline exhausted')

    def _candidate_source_refresh(self,net):
        try:self._refresh_source_net(net)
        except TimeoutError:return False
        return not self.expired()

    def cells(self,bounds,padding=0):
        for x in range((bounds.GetLeft()-padding)//self.cell,(bounds.GetRight()+padding)//self.cell+1):
            for y in range((bounds.GetTop()-padding)//self.cell,(bounds.GetBottom()+padding)//self.cell+1):yield x,y

    def index(self,item):
        uid=item.m_Uuid.AsString()
        if not isinstance(item,p.PAD) and isinstance(item,p.PCB_TRACK) and not isinstance(item,(p.PCB_VIA,p.PCB_ARC)):
            self._bundle_ranker=None
            self._quality_tracks[uid]=dict(**{'class':'PCB_TRACK'},net=item.GetNetname(),layer=item.GetLayer(),width=item.GetWidth(),start=xy(item.GetStart()),end=xy(item.GetEnd()))
        for layer in self.buckets:
            if item.IsOnLayer(layer):
                native_shape=item.GetEffectiveShape(layer);shape=native_shape
                if (self.generation.name=='dsn-process-v2' and isinstance(item,p.PAD)
                        and item.GetAttribute()!=p.PAD_ATTRIB_NPTH):
                    from .exchange_pad_envelope import foreign_pad_envelope
                    shape=foreign_pad_envelope(item,layer,self._pad_import_grid_nm)
                clearance=own_clearance(item,layer,self.minimum_clearance)
                self.net_clearances[item.GetNetname()]=max(self.net_clearances.get(item.GetNetname(),0),clearance)
                for cell in self.cells(shape.BBox(),max(clearance,p.FromMM(.5))):
                    self.buckets[layer].setdefault(cell,[]).append((uid,shape,item.GetNetname(),clearance))
                if isinstance(item,p.PAD) and item.GetAttribute()==p.PAD_ATTRIB_SMD:
                    for cell in self.cells(native_shape.BBox(),p.FromMM(.1)):
                        self.solder_lands.setdefault(cell,[]).append((uid,native_shape))
        if isinstance(item,p.PAD):
            drill=item.GetDrillSize();hole=item.GetEffectiveHoleShape() if drill.x and drill.y else None
        elif isinstance(item,p.PCB_VIA):hole=item.GetEffectiveHoleShape()
        else:hole=None
        if hole:
            gap=self.native_hole_clearance+1000
            if (uses_exchange_geometry(self.generation) and isinstance(item,p.PAD) and
                item.GetAttribute()==p.PAD_ATTRIB_NPTH and drill.x==drill.y):
                gap=max(gap,npth_exchange_clearance_nm(self.native_hole_clearance,drill.x))
            self.hole_clearances[uid]=gap
            for cell in self.cells(hole.BBox(),max(p.FromMM(.5),gap)):
                self.holes.setdefault(cell,[]).append((uid,hole,item.GetNetname()))

    def clear(self,shape,layer,net,via=False):
        return next(self.collisions(shape,layer,net,via),None) is None

    def collisions(self,shape,layer,net,via=False):
        from smilepcb.keyboard_routing.corridor_reservations import collisions as corridor_collisions
        yield from corridor_collisions(shape,layer,net)
        concrete=isinstance(shape,p.SHAPE_SEGMENT)
        policy=self.port_policy
        if policy is not None:
            if concrete:
                segment=shape.GetSeg()
                if not policy.accepts_segment(xy(segment.A),xy(segment.B),layer,net,shape=shape):
                    yield 'controller-first-contact';return
            elif policy._launch_conflict(shape,layer,net):
                yield 'reserved-controller-launch'
        # GetEffectiveShape may expose only a base SHAPE. Such a receipt is a
        # physical/foreign check, never a first-contact certificate; candidate
        # capsules and every actual path/via emission carry exact witnesses.
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
        if not self._candidate_source_refresh(net):return None
        ports=self.port_policy
        choices=paths(a,b,octile_only=uses_exchange_geometry(self.generation) or self.path_policy is not None)
        if self.generation.name=='dsn-process-v2' and self.quality.bend_penalty_nm:
            from .path_quality import BundlePathRanker
            gap=max(self.minimum_clearance,self.net_clearances.get(net,0))
            if self._bundle_ranker is None or self._bundle_ranker.gap!=gap:
                self._bundle_ranker=BundlePathRanker(self._quality_tracks,clearance_nm=gap,eligible_nets=self.net_clearances)
            choices=self._bundle_ranker.rank(choices,layer=layer,net=net,width=self.track_width,profile=self.quality,deadline=self.deadline)
        else:choices=ranked_paths(choices,self.quality)
        for path in choices:
            if self.expired():return None
            path=[v for i,v in enumerate(path) if not i or v!=path[i-1]]
            if self.path_policy is not None and not self.path_policy.accepts_path(path):continue
            if ports is not None and any(not ports.accepts_segment(x,y,layer,net,self.track_width) for x,y in zip(path,path[1:])):continue
            if all(self.clear(p.SHAPE_SEGMENT(vec(x),vec(y),self.track_width),layer,net)
                   for x,y in zip(path,path[1:])):return path
        return None

    def via_clear(self,position,net):
        if not self._candidate_source_refresh(net):return False
        ports=self.port_policy
        if ports is not None and not ports.accepts_via(position,net,self.via_width):return False
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

    def _check_corridor_path(self,path,layer,net):
        from smilepcb.keyboard_routing.corridor_reservations import current_corridors,collisions as corridor_collisions
        if not current_corridors():return
        for a,b in zip(path,path[1:]):
            if a==b:continue
            if next(corridor_collisions(p.SHAPE_SEGMENT(vec(a),vec(b),self.track_width),layer,net),None) is not None:
                raise ValueError('Candidate violates reserved corridor')

    def _check_corridor_via(self,position,net):
        from smilepcb.keyboard_routing.corridor_reservations import current_corridors,collisions as corridor_collisions
        if not current_corridors():return
        shape=p.SHAPE_CIRCLE(vec(position),self.via_width//2)
        for layer in (p.F_Cu,p.B_Cu):
            if next(corridor_collisions(shape,layer,net),None) is not None:
                raise ValueError('Candidate via violates reserved corridor')

    def add_path(self,path,layer,net):
        from smilepcb.keyboard_routing.corridor_reservations import current_corridors
        if current_corridors():self._check_corridor_path(path,layer,net)
        if self.path_policy is not None and not self.path_policy.accepts_path(path):
            raise ValueError('Candidate violates field path geometry')
        self._refresh_source_net(net)
        ports=self.port_policy
        if ports is not None and any(not ports.accepts_segment(a,b,layer,net,self.track_width) for a,b in zip(path,path[1:])):
            raise ValueError('Candidate violates controller first-contact boundary')
        for a,b in zip(path,path[1:]):
            if a==b:continue
            t=p.PCB_TRACK(self.board);t.SetStart(vec(a));t.SetEnd(vec(b));t.SetWidth(self.track_width);t.SetLayer(layer)
            t.SetNet(self.board.FindNet(net));self.board.Add(t);self.index(t);self.added.append(t.m_Uuid.AsString())

    def add_via(self,position,net):
        from smilepcb.keyboard_routing.corridor_reservations import current_corridors
        if current_corridors():self._check_corridor_via(position,net)
        self._refresh_source_net(net)
        ports=self.port_policy
        if ports is not None and not ports.accepts_via(position,net,self.via_width):
            raise ValueError('Candidate via violates controller first-contact boundary')
        t=p.PCB_VIA(self.board);t.SetPosition(vec(position));t.SetWidth(self.via_width);t.SetDrill(self.drill)
        t.SetViaType(p.VIATYPE_THROUGH);t.SetLayerPair(p.F_Cu,p.B_Cu);t.SetNet(self.board.FindNet(net))
        prepare_added_via(t);self.board.Add(t);self.index(t);self.added.append(t.m_Uuid.AsString())

    def endpoints(self,item,toward):
        self._refresh_source_net(item.GetNetname())
        ports=self.port_policy;uid=item.m_Uuid.AsString() if hasattr(item,'m_Uuid') else None
        owned=lambda q:uid in q['pad_ids'] or uid in q['added_ids']
        if ports is not None and any(owned(q) for q in ports.live_ports):
            from .native_graph import copper_graph
            graph=copper_graph(self.board,{item.GetNetname()},self.deadline)
            ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes']) if not n['zone']}
            if uid not in ids:raise ValueError('Controller source item is absent')
            self.refresh_interfaces(graph)
            heads=[]
            for q in ports.live_ports:
                if not owned(q):continue
                tip=q.get('terminal_id') or q.get('via_id');root=graph['find'](ids[uid])
                if (tip not in ids or graph['find'](ids[tip])!=root or
                        any(u not in ids or graph['find'](ids[u])!=root for u in q['pad_ids'])):
                    raise ValueError('Controller source lost its live terminal')
                node=graph['nodes'][ids[tip]];point=tuple(q['exit_nm']);probe=p.SHAPE_CIRCLE(vec(point),0)
                layers=sorted(layer for layer,shape in node['shapes'].items() if p.SHAPE.Collide(probe,shape,0))
                if not layers or node['net']!=q['net']:raise ValueError('Controller terminal has no physical contact')
                heads.append((distance(point,toward),point,layers))
            if heads:
                _,point,layers=min(heads)
                return point,layers
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
        if self.expired():return False
        try:
            a,la=self.endpoints(source,xy(target.GetPosition()))
            b,lb=self.endpoints(target,a)
        except TimeoutError:return False
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
