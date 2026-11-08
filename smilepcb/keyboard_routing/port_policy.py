"""First-contact boundaries for live ordered controller leads.

A lead without a via is still live. Its MCU side is not an alternative
attachment point. Once joined, ordinary field copper remains branchable.
"""
import math
from smilepcb.native_worker import p
from .native_graph import routing_points,nearest_to_node
from .local_router import vec
from .bank_geometry import terminal_origin


class PortPolicy:
    def __init__(self,board,ports,*,track_width_nm=None,resolved=(),repair_reserved=()):
        self.track_width=track_width_nm or board.GetDesignSettings().m_TrackMinWidth or 150000
        from .interface_launches import interface_identity
        self.board=board;self.ports=list(ports)
        self.repair_reserved={tuple(key) for key in repair_reserved}&{interface_identity(q) for q in self.ports}
        self.set_resolved(set(resolved))
        self.items={t.m_Uuid.AsString():t for t in board.GetTracks()}
        self.items.update({d.m_Uuid.AsString():d for f in board.GetFootprints() for d in f.Pads() if d.m_Uuid.AsString() in self.pads})
        import json
        from smilepcb.native_worker import snapshot
        state=snapshot(board);current={**state['tracks'],**{d['uuid']:d for f in state['footprints'].values() for d in f['pads']}}
        self.source_shapes=json.loads(json.dumps({uid:q.get('source_shapes',{}).get(uid,current.get(uid)) for q in self.ports for uid in q['added_ids']+q['pad_ids']}))
        self._require_sources(self.launch_indices)
        # Protect the same forward half-millimetre checked during admission.
        # A foreign feeder may otherwise stop just beyond an unjoined tip and
        # leave it clearance-correct but unable to make even its first move.
        from .generation import current_generation_profile
        from smilepcb.routing_style import own_clearance
        self.launch_gap=max(board.GetDesignSettings().m_MinClearance,current_generation_profile().foreign_copper_clearance_nm)
        self.net_clearances={}
        for item in [*board.GetTracks(),*(d for f in board.GetFootprints() for d in f.Pads())]:
            for layer in (p.F_Cu,p.B_Cu):
                if item.IsOnLayer(layer):
                    net=item.GetNetname();self.net_clearances[net]=max(self.net_clearances.get(net,self.launch_gap),own_clearance(item,layer,self.launch_gap))
        self.launch_cell=2000000;self.launch_buckets=self._launches(self.launch_indices)

    def _launches(self,active):
        buckets={p.F_Cu:{},p.B_Cu:{}}
        for index in sorted(active):
            q=self.ports[index]
            a=tuple(q['exit_nm']);origin=terminal_origin(q);d=tuple(a[i]-origin[i] for i in (0,1));g=math.gcd(*d)
            if not g:raise ValueError('Controller launch requires a physical departure heading')
            z=tuple(a[i]+d[i]//g*500000 for i in (0,1));item=self.items[q.get('terminal_id') or q.get('via_id')]
            width=item.GetWidth() if item.GetClass()=='PCB_TRACK' else self.track_width
            shape=p.SHAPE_SEGMENT(vec(a),vec(z),width);bb=shape.BBox();gap=max(self.launch_gap,self.net_clearances.get(q['net'],self.launch_gap));bounds=(bb.GetLeft()-gap,bb.GetTop()-gap,bb.GetRight()+gap,bb.GetBottom()+gap)
            row=(index,q['net'],shape,gap,bounds)
            for x in range(bounds[0]//self.launch_cell,bounds[2]//self.launch_cell+1):
                for y in range(bounds[1]//self.launch_cell,bounds[3]//self.launch_cell+1):buckets[q['exit_layer']].setdefault((x,y),[]).append(row)
        return buckets

    def set_resolved(self,resolved):
        from .interface_launches import interface_identity
        resolved={tuple(key) for key in resolved};active={i for i,q in enumerate(self.ports) if interface_identity(q) not in resolved}
        held=getattr(self,'repair_reserved',set())
        launch_indices=active|{i for i,q in enumerate(self.ports) if interface_identity(q) in held}
        if hasattr(self,'source_shapes'):
            # Native objects may have been removed/reintroduced since capture.
            # Validate the actual board before changing any closure state.
            self._require_sources(launch_indices)
        elif hasattr(self,'items'):
            if any(u not in self.items for i in launch_indices for u in self.ports[i]['added_ids']+self.ports[i]['pad_ids']):raise ValueError('Missing live controller interface')
        launches=self._launches(launch_indices) if hasattr(self,'launch_cell') else None
        self.resolved=resolved;self.active=active;self.launch_indices=launch_indices
        if launches is not None:self.launch_buckets=launches
        self.live_ports=[q for i,q in enumerate(self.ports) if i in self.active]
        self.copper={u for q in self.ports for u in q['added_ids']};self.pads={u for q in self.ports for u in q['pad_ids']}
        self.live_copper={u for q in self.live_ports for u in q['added_ids']};self.live_pads={u for q in self.live_ports for u in q['pad_ids']}
        self.by_net={}
        for q in self.live_ports:self.by_net.setdefault(q['net'],[]).append(q)

    def reserve_repair(self,identities):
        """Hold foreign departures without changing actual electrical closure."""
        from .interface_launches import interface_identity
        held={tuple(key) for key in identities}&{interface_identity(q) for q in self.ports}
        if held==self.repair_reserved:return
        indices=self.active|{i for i,q in enumerate(self.ports) if interface_identity(q) in held}
        self._require_sources(indices)
        launches=self._launches(indices)
        self.repair_reserved=held;self.launch_indices=indices;self.launch_buckets=launches

    def _require_sources(self,active):
        if not active:return
        import json
        from smilepcb.native_worker import snapshot
        state=snapshot(self.board);current={**state['tracks'],**{d['uuid']:d for f in state['footprints'].values() for d in f['pads']}}
        ids={uid for i in active for uid in self.ports[i]['added_ids']+self.ports[i]['pad_ids']}
        if any(uid not in current or self.source_shapes.get(uid)!=json.loads(json.dumps(current[uid])) for uid in ids):
            raise ValueError('Changed live controller interface source')
        pads={uid for q in self.ports for uid in q['pad_ids']}
        self.items={t.m_Uuid.AsString():t for t in self.board.GetTracks()}
        self.items.update({d.m_Uuid.AsString():d for f in self.board.GetFootprints() for d in f.Pads() if d.m_Uuid.AsString() in pads})

    def refresh(self,graph):
        from .interface_launches import resolved_interfaces
        self.set_resolved(resolved_interfaces(self.ports,self.resolved,graph))

    def _launch_conflict(self,shape,layer,net):
        bb=shape.BBox();left,top,right,bottom=bb.GetLeft(),bb.GetTop(),bb.GetRight(),bb.GetBottom();seen=set()
        incoming=self.net_clearances.get(net,self.launch_gap)
        for x in range((left-incoming)//self.launch_cell,(right+incoming)//self.launch_cell+1):
            for y in range((top-incoming)//self.launch_cell,(bottom+incoming)//self.launch_cell+1):
                for index,owner,reserved,gap,bounds in self.launch_buckets[layer].get((x,y),()):
                    if index not in self.launch_indices or index in seen or owner==net:continue
                    seen.add(index)
                    extra=max(0,incoming-gap)
                    if right<bounds[0]-extra or left>bounds[2]+extra or bottom<bounds[1]-extra or top>bounds[3]+extra:continue
                    if p.SHAPE.Collide(shape,reserved,max(gap,incoming)):return True
        return False

    def contacts(self,graph,indices,toward):
        rows=set();nodes=graph['nodes'];uids={nodes[i]['item'].m_Uuid.AsString() for i in indices if not nodes[i]['zone']}
        for q in self.live_ports:
            if not set(q['pad_ids'])&uids:continue
            tip=q.get('terminal_id') or q.get('via_id')
            if tip not in uids:raise ValueError('Controller component lost its live terminal')
            node=nodes[next(i for i in indices if nodes[i]['item'].m_Uuid.AsString()==tip)]
            probe=p.SHAPE_CIRCLE(vec(q['exit_nm']),0)
            rows.update((tuple(q['exit_nm']),layer) for layer,shape in node['shapes'].items() if p.SHAPE.Collide(probe,shape,0))
        for i in indices:
            node=nodes[i];uid=node['item'].m_Uuid.AsString()
            if uid in self.live_copper|self.live_pads:continue
            for point in [*routing_points(node),nearest_to_node(node,toward)]:
                rows.update((point,layer) for layer in node['shapes'])
        return rows

    def access_radius_nm(self,position,layer,net,width_nm):
        # A lattice micro-edge must start beyond the old terminal's rounded
        # cap. Check that whole lead atomically; never waive first contact.
        radii=[]
        for q in self.by_net.get(net,()):
            item=self.items[q.get('terminal_id') or q.get('via_id')]
            if tuple(position)!=tuple(q['exit_nm']) or not item.IsOnLayer(layer):continue
            width=item.GetWidth(layer) if isinstance(item,p.PCB_VIA) else item.GetWidth()
            radii.append(math.ceil((width+width_nm)/2)+1)
        return max(radii,default=0)

    def departure_waypoint_nm(self,port,*,width_nm=None):
        """Measure an outward waypoint beyond actual same-bank peer launches.

        This exposes geometry to the shared proposal generator, without
        granting any collision exemption or duplicating the launch length.
        """
        width=self.track_width if width_nm is None else width_nm
        source=tuple(port['exit_nm']);origin=terminal_origin(port)
        delta=tuple(source[i]-origin[i] for i in (0,1));divisor=math.gcd(*delta)
        if not divisor:raise ValueError('Physical source departure heading required')
        heading=tuple(v//divisor for v in delta);norm=sum(v*v for v in heading)
        if max(map(abs,heading))!=1:raise ValueError('Octile source departure heading required')
        run=max(1,math.ceil(self.access_radius_nm(source,port['exit_layer'],port['net'],width)/math.sqrt(norm)))
        seen=set();bank=port.get('bank')
        for rows in self.launch_buckets[port['exit_layer']].values():
            for index,owner,shape,gap,_ in rows:
                peer=self.ports[index]
                if index in seen or owner==port['net'] or bank is None or peer.get('bank')!=bank:continue
                seen.add(index);box=shape.BBox()
                extent=max(sum((point[i]-source[i])*heading[i] for i in (0,1))
                    for point in ((box.GetLeft(),box.GetTop()),(box.GetLeft(),box.GetBottom()),
                                  (box.GetRight(),box.GetTop()),(box.GetRight(),box.GetBottom())))
                margin=max(gap,self.launch_gap,self.net_clearances.get(port['net'],0))+(width+1)//2
                run=max(run,math.ceil((extent+margin*sum(map(abs,heading)))/norm)+1)
        return tuple(source[i]+run*heading[i] for i in (0,1))

    def accepts_segment(self,a,z,layer,net,width_nm=None,*,shape=None):
        if shape is None:shape=p.SHAPE_SEGMENT(vec(a),vec(z),width_nm or self.track_width)
        if self._launch_conflict(shape,layer,net):return False
        if a==z:return True
        for q in self.by_net.get(net,()):
            tip=tuple(q['exit_nm']);terminal=q.get('terminal_id') or q.get('via_id')
            portal=(a==tip or z==tip) and self.items[terminal].IsOnLayer(layer)
            other=z if a==tip else a;allowed=False
            if portal:
                if isinstance(self.items[terminal],p.PCB_VIA) and layer!=q['exit_layer']:allowed=True
                else:
                    origin=terminal_origin(q);h=tuple(tip[i]-origin[i] for i in (0,1));d=tuple(other[i]-tip[i] for i in (0,1));dot=sum(h[i]*d[i] for i in (0,1))
                    allowed=dot>0 and 2*dot*dot>=sum(v*v for v in h)*sum(v*v for v in d)
                if not allowed:return False
            for uid in q['added_ids']+q['pad_ids']:
                item=self.items[uid]
                if not item.IsOnLayer(layer):continue
                if p.SHAPE.Collide(shape,item.GetEffectiveShape(layer),0) and not (portal and allowed and uid==terminal):return False
        return True

    def accepts_via(self,position,net,diameter_nm):
        shape=p.SHAPE_CIRCLE(vec(position),diameter_nm//2)
        if any(self._launch_conflict(shape,layer,net) for layer in (p.F_Cu,p.B_Cu)):return False
        for q in self.by_net.get(net,()):
            terminal=q.get('terminal_id') or q.get('via_id');portal=tuple(position)==tuple(q['exit_nm'])
            for uid in q['added_ids']+q['pad_ids']:
                item=self.items[uid]
                for layer in (p.F_Cu,p.B_Cu):
                    if not item.IsOnLayer(layer):continue
                    if p.SHAPE.Collide(shape,item.GetEffectiveShape(layer),0) and not (portal and uid==terminal and not isinstance(item,p.PCB_VIA)):return False
        return True
