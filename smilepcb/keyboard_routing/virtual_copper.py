"""Native-accurate ghost copper with reversible indexes, never BOARD.Add/Delete."""
from smilepcb.native_worker import p
from .local_router import LocalRouter,vec
from .safety import prepare_added_via


class VirtualCopper(LocalRouter):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.ghosts=[];self.operations=[]
        self.ignored_ids=set(kwargs.get('ignored_copper',()))

    def add_path(self,path,layer,net):
        from smilepcb.keyboard_routing.corridor_reservations import current_corridors
        if current_corridors():self._check_corridor_path(path,layer,net)
        if self.path_policy is not None and not self.path_policy.accepts_path(path):raise ValueError('Virtual path violates geometry policy')
        policy=self.port_policy
        if policy is not None and any(not policy.accepts_segment(a,z,layer,net,self.track_width) for a,z in zip(path,path[1:])):raise ValueError('Virtual path violates source boundary')
        for a,z in zip(path,path[1:]):
            if tuple(a)==tuple(z):continue
            item=p.PCB_TRACK(self.board);item.SetStart(vec(a));item.SetEnd(vec(z));item.SetWidth(self.track_width);item.SetLayer(layer);item.SetNet(self.board.FindNet(net))
            self.ghosts.append(item);self.index(item);self.added.append(item.m_Uuid.AsString())
            self.operations.append(dict(kind='track',net=net,layer=layer,start=tuple(a),end=tuple(z),width=self.track_width))

    def add_via(self,position,net):
        from smilepcb.keyboard_routing.corridor_reservations import current_corridors
        if current_corridors():self._check_corridor_via(position,net)
        policy=self.port_policy
        if policy is not None and not policy.accepts_via(position,net,self.via_width):raise ValueError('Virtual via violates source boundary')
        item=p.PCB_VIA(self.board);item.SetPosition(vec(position));item.SetWidth(self.via_width);item.SetDrill(self.drill);item.SetViaType(p.VIATYPE_THROUGH);item.SetLayerPair(p.F_Cu,p.B_Cu);item.SetNet(self.board.FindNet(net));prepare_added_via(item)
        self.ghosts.append(item);self.index(item);self.added.append(item.m_Uuid.AsString())
        self.operations.append(dict(kind='via',net=net,position=tuple(position),width=self.via_width,drill=self.drill))

    def checkpoint(self):
        return dict(buckets={face:{cell:list(rows) for cell,rows in cells.items()} for face,cells in self.buckets.items()},
                    holes={cell:list(rows) for cell,rows in self.holes.items()},hole_clearances=dict(self.hole_clearances),net_clearances=dict(self.net_clearances),
                    added=len(self.added),ghosts=len(self.ghosts),operations=len(self.operations))

    def restore(self,checkpoint):
        self.buckets={face:{cell:list(rows) for cell,rows in cells.items()} for face,cells in checkpoint['buckets'].items()}
        self.holes={cell:list(rows) for cell,rows in checkpoint['holes'].items()};self.hole_clearances=dict(checkpoint['hole_clearances']);self.net_clearances=dict(checkpoint['net_clearances'])
        del self.added[checkpoint['added']:];del self.operations[checkpoint['operations']:];del self.ghosts[checkpoint['ghosts']:]

    def track_items(self):
        return {t.m_Uuid.AsString():t for t in [*(t for t in self.board.GetTracks() if t.m_Uuid.AsString() not in self.ignored_ids),*self.ghosts]}
