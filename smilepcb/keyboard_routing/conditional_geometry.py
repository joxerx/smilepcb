"""Native geometry and immutable creation proofs for conditional matrix banks.

Proof queries operate on one sealed board/graph/launch state. Callers must run
assert_unchanged before admitting a result; no emission is allowed in that scope.
"""
import time
from copy import deepcopy
from contextlib import contextmanager
from smilepcb.native_worker import p
from .local_router import vec, LocalRouter
from .controller_ports import _port_collides


def emit_shapes(board,shapes):
    live={t.m_Uuid.AsString() for t in board.GetTracks()}
    live.update(f.m_Uuid.AsString() for f in board.GetFootprints())
    live.update(d.m_Uuid.AsString() for f in board.GetFootprints() for d in f.Pads())
    if live & set(shapes):raise ValueError('Plan UUID already exists on the target board')
    for uid,q in shapes.items():
        if q['class']=='PCB_TRACK':
            t=p.PCB_TRACK(board);t.SetStart(vec(q['start']));t.SetEnd(vec(q['end']));t.SetLayer(q['layer'])
        elif q['class']=='PCB_VIA':
            t=p.PCB_VIA(board);t.SetPosition(vec(q['start']));t.SetDrill(q['drill']);t.SetViaType(q['via_type']);t.SetLayerPair(q['top'],q['bottom'])
            from .safety import prepare_added_via
            prepare_added_via(t)
        else:raise ValueError('Unsupported complete-bank operation')
        net=board.FindNet(q['net'])
        if net is None:raise ValueError('Plan invented an undeclared net')
        t.SetWidth(q['width']);t.SetNet(net);t.SetLocked(q.get('locked',False));t.SetUuid(p.KIID(uid));board.Add(t)
    board.BuildConnectivity()


class FrozenProofRouter(LocalRouter):
    """Read-only proof world: reuse the exact completed graph and native index.

    The caller may perform queries only, then must call assert_unchanged before
    accepting a proof. No route emission or model/launch updates are permitted.
    """
    def __init__(self,board,*,deadline,track_width_nm,graph,covered_nets,refresh_launches=True):
        from smilepcb.native_worker import snapshot
        super().__init__(board,deadline=deadline,compact=True,track_width_nm=track_width_nm)
        self._proof_nets=frozenset(covered_nets)
        graph_nets=set(graph.get('nets',{q['net'] for q in graph['nodes']}))
        if not self._proof_nets<=graph_nets:raise ValueError('Frozen proof graph does not cover requested nets')
        if refresh_launches:self.refresh_interfaces(graph)
        self._proof_board=snapshot(board);self._proof_timestamp=board.GetTimeStamp()
        self._proof_launch=self._launch_state();self._proof_rules=self._rule_state()
        self._proof_physical=self._physical_state()
        self._proof_active=False
        self._proof_slots={};self._proof_contributions={}
        physical_ids=set(self._proof_board['tracks'])|{q['uuid'] for f in self._proof_board['footprints'].values() for q in f['pads']}
        for table in [*self.buckets.values(),self.holes]:
            for cell,items in table.items():
                for uid in {q[0] for q in items}:
                    self._proof_slots.setdefault(uid,[]).append((table,cell))
        for table in self.buckets.values():
            for items in table.values():
                for uid,shape,net,gap in items:
                    if uid in physical_ids:
                        contributions=self._proof_contributions.setdefault(net,{})
                        contributions[uid]=max(gap,contributions.get(uid,0))

    @contextmanager
    def excluding_copper(self,ids,*,clearance_nm):
        """Temporarily match LocalRouter's whole-owner ignored_copper index.

        Only saved tracks/vias may be excluded. Shapes, hole guards and per-net
        clearance contributions are restored exactly, including on exceptions.
        The caller must still seal-check the whole proof before admission.
        """
        ids=frozenset(ids)
        if self._proof_active:raise ValueError('Nested frozen-proof exclusion')
        if not ids<=self._proof_board['tracks'].keys():raise ValueError('Excluded copper is outside the frozen proof frame')
        if type(clearance_nm) is not int or clearance_nm<0:raise ValueError('Invalid proof clearance floor')
        if self.expired():raise TimeoutError('Owner proof deadline')
        previous=[];seen=set();gaps=dict(self.net_clearances);holes=dict(self.hole_clearances)
        minimum=self.minimum_clearance
        self._proof_active=True
        try:
            for uid in ids:
                for table,cell in self._proof_slots.get(uid,()):
                    key=(id(table),cell)
                    if key in seen:continue
                    seen.add(key);values=table[cell];previous.append((table,cell,values))
                    table[cell]=[q for q in values if q[0] not in ids]
                self.hole_clearances.pop(uid,None)
            self.minimum_clearance=max(minimum,clearance_nm)
            for net,contributions in self._proof_contributions.items():
                remaining=[gap for uid,gap in contributions.items() if uid not in ids]
                if remaining:self.net_clearances[net]=max(max(remaining),self.generation.foreign_copper_clearance_nm,clearance_nm)
                else:self.net_clearances.pop(net,None)
            yield self
        finally:
            for table,cell,values in previous:table[cell]=values
            self.net_clearances.clear();self.net_clearances.update(gaps)
            self.hole_clearances.clear();self.hole_clearances.update(holes)
            self.minimum_clearance=minimum;self._proof_active=False

    def _launch_state(self):
        from smilepcb.keyboard_routing.interface_launches import current_launch_inventory
        inventory=current_launch_inventory()
        if inventory is not self.launch_inventory:raise ValueError('Frozen proof launch scope changed')
        return None if inventory is None else (deepcopy(inventory.ports),set(inventory.resolved),set(inventory.repair_reserved))

    def _rule_state(self):
        rules=self.board.GetDesignSettings()
        return tuple(getattr(rules,key) for key in ('m_MinClearance','m_HoleClearance','m_TrackMinWidth',
            'm_CopperEdgeClearance','m_MinThroughDrill','m_ViasMinSize','m_ViasMinAnnularWidth'))

    def _physical_state(self,excluded_ids=()):
        """Seal actual clearances, holes, custom pads, artwork, milling and zones."""
        from smilepcb.copper_obstacles import copper_graphics,own_clearance
        from smilepcb.routing_style import edge_shapes,filled_zones
        from hashlib import sha256
        excluded_ids=frozenset(excluded_ids)
        if excluded_ids and (any(type(uid) is not str for uid in excluded_ids) or not excluded_ids<={
                item.m_Uuid.AsString() for item in self.board.GetTracks() if item.GetClass() in ('PCB_TRACK','PCB_VIA')}):
            raise ValueError('Native physical exclusion contains missing or non-track objects')
        pieces=[];minimum=self.board.GetDesignSettings().m_MinClearance
        for item in [*self.board.GetTracks(),*(d for f in self.board.GetFootprints() for d in f.Pads())]:
            uid=item.m_Uuid.AsString()
            if uid in excluded_ids:continue
            for face in (p.F_Cu,p.B_Cu):
                if item.IsOnLayer(face):pieces.append((uid,face,item.GetNetname(),own_clearance(item,face,minimum),item.GetEffectiveShape(face).Format(False)))
            if isinstance(item,p.PCB_VIA):hole=item.GetEffectiveHoleShape()
            elif isinstance(item,p.PAD):
                drill=item.GetDrillSize();hole=item.GetEffectiveHoleShape() if drill.x and drill.y else None
            else:hole=None
            if hole is not None:pieces.append((uid,'hole',hole.Format(False)))
        for uid,shape,net,face in copper_graphics(self.board):pieces.append((uid,'art',face,net,shape.Format(False)))
        for edge in edge_shapes(self.board):pieces.append(('edge',edge.Format(False)))
        for zone in self.board.Zones():
            if zone.GetIsRuleArea():pieces.append((zone.m_Uuid.AsString(),'rule',zone.GetLayerSet().FmtHex(),
                zone.GetDoNotAllowVias(),zone.GetDoNotAllowTracks(),zone.Outline().Format(False)))
        for shape,net,face,gap in filled_zones(self.board):pieces.append(('fill',net,face,gap,shape.Format(False)))
        outline=p.SHAPE_POLY_SET()
        if not self.board.GetBoardPolygonOutlines(outline,False):raise ValueError('Frozen proof outline became invalid')
        pieces.append(('outline',outline.Format(False)))
        return sha256(repr(sorted(pieces,key=repr)).encode()).hexdigest()

    def _refresh_source_net(self,net):
        # The exact graph was already computed after the final board mutation.
        if self.expired():raise TimeoutError('Frozen proof deadline')
        if net not in self._proof_nets:raise ValueError('Frozen proof queried an uncovered net')
        if self.board.GetTimeStamp()!=self._proof_timestamp or self._launch_state()!=self._proof_launch:
            raise ValueError('Frozen proof board or launch state changed')

    def assert_unchanged(self):
        from smilepcb.native_worker import snapshot
        if self._proof_active:raise ValueError('Frozen proof index still has an excluded UUID')
        if (snapshot(self.board)!=self._proof_board or self.board.GetTimeStamp()!=self._proof_timestamp or
                self._launch_state()!=self._proof_launch or self._rule_state()!=self._proof_rules or
                self._physical_state()!=self._proof_physical):
            raise ValueError('Frozen proof frame changed')
        if self.expired():raise TimeoutError('Frozen proof deadline')


def saved_via_clear_cached(oracle,via):
    """Equivalent to own-UUID ignored construction, with exact index restoration."""
    uid=via.m_Uuid.AsString();net=via.GetNetname()
    if oracle._proof_active:raise ValueError('Nested frozen-proof exclusion')
    if oracle._proof_board['tracks'].get(uid,{}).get('class')!='PCB_VIA':raise ValueError('Via is outside the frozen proof frame')
    if oracle.expired():raise TimeoutError('Saved-via proof deadline')
    previous=[];width,drill=oracle.via_width,oracle.drill
    had_net=net in oracle.net_clearances;net_gap=oracle.net_clearances.get(net)
    had_hole=uid in oracle.hole_clearances;hole_gap=oracle.hole_clearances.get(uid)
    oracle._proof_active=True
    try:
        for table,cell in oracle._proof_slots.get(uid,()):
            values=table[cell];previous.append((table,cell,values))
            table[cell]=[q for q in values if q[0]!=uid]
        remaining=[gap for key,gap in oracle._proof_contributions.get(net,{}).items() if key!=uid]
        if remaining:oracle.net_clearances[net]=max(max(remaining),oracle.generation.foreign_copper_clearance_nm)
        else:oracle.net_clearances.pop(net,None)
        oracle.hole_clearances.pop(uid,None)
        oracle.via_width=via.GetWidth(p.F_Cu);oracle.drill=via.GetDrillValue()
        clear=oracle.via_clear((via.GetPosition().x,via.GetPosition().y),net)
        if oracle.expired():raise TimeoutError('Saved-via proof deadline')
        return clear
    finally:
        for table,cell,values in previous:table[cell]=values
        oracle.via_width,oracle.drill=width,drill
        if had_net:oracle.net_clearances[net]=net_gap
        else:oracle.net_clearances.pop(net,None)
        if had_hole:oracle.hole_clearances[uid]=hole_gap
        else:oracle.hole_clearances.pop(uid,None)
        oracle._proof_active=False


def shapes_for_candidate_rows(rows,width,via_width,drill):
    result={};serial=0
    def track(a,z,layer,net):
        nonlocal serial
        if tuple(a)==tuple(z):return
        result[str(serial)]={'class':'PCB_TRACK','net':net,'layer':layer,'start':list(a),'end':list(z),'width':width};serial+=1
    for row in rows:
        for a,z in zip(row['path'],row['path'][1:]):track(a,z,row['layer'],row['net'])
        if row.get('kind','via')=='via':
            result[str(serial)]={'class':'PCB_VIA','net':row['net'],'start':list(row['via_nm']),'end':list(row['via_nm']),
                'width':via_width,'drill':drill,'top':p.F_Cu,'bottom':p.B_Cu,'via_type':p.VIATYPE_THROUGH};serial+=1
            track(row['via_nm'],row['exit_nm'],row['exit_layer'],row['net'])
    return result


def _native(shapes):
    result=[]
    for uid,q in shapes.items():
        if q['class']=='PCB_TRACK':
            caps={q['layer']:p.SHAPE_SEGMENT(vec(q['start']),vec(q['end']),q['width'])};hole=None
        elif q['class']=='PCB_VIA':
            circle=p.SHAPE_CIRCLE(vec(q['start']),q['width']//2)
            caps={q['top']:circle,q['bottom']:circle};hole=p.SHAPE_CIRCLE(vec(q['start']),q['drill']//2)
        else:raise ValueError('Unsupported bank collision operation')
        result.append((uid,q,caps,hole))
    return result


def certify_pair(a,b,*,base_sha256,profile,minimum_clearance,net_clearances,hole_clearance,deadline):
    conflicts=[];bounds={};checks=0
    native_a,native_b=_native(a['shapes']),_native(b['shapes'])
    for ua,qa,ca,ha in native_a:
        for ub,qb,cb,hb in native_b:
            if time.monotonic()>=deadline:raise TimeoutError('Complete-bank compatibility deadline')
            if qa['net']!=qb['net']:
                gap=max(minimum_clearance,net_clearances.get(qa['net'],0),net_clearances.get(qb['net'],0))
                for face in ca.keys() & cb.keys():
                    checks+=1
                    if _port_collides(ca[face],cb[face],gap,bounds):
                        conflicts.append({'a_shape':ua,'b_shape':ub,'kind':'copper','layer':face,'clearance_nm':gap})
            # Through-hole guards still apply to same-net planned vias. An
            # ordinary same-net track may join a via, matching LocalRouter.
            for hole,caps,other in ((ha,cb,qb),(hb,ca,qa)):
                if hole is None or qa['net']==qb['net'] and other['class']!='PCB_VIA':continue
                for cap in caps.values():
                    checks+=1
                    if _port_collides(hole,cap,hole_clearance,bounds):
                        conflicts.append({'a_shape':ua,'b_shape':ub,'kind':'hole','layer':None,'clearance_nm':hole_clearance})
            if conflicts:break
        if conflicts:break
    return {'a':[a['bank'],a['id']],'b':[b['bank'],b['id']],'base_sha256':base_sha256,'profile':profile,
            'a_geometry_sha256':a['proof']['geometry_sha256'],'b_geometry_sha256':b['proof']['geometry_sha256'],
            'clear':not conflicts,'conflicts':conflicts,'native_pair_checks':checks}


def copy_native_tracks(board,ids=None):
    copies={}
    for item in board.GetTracks():
        uid=item.m_Uuid.AsString()
        if ids is not None and uid not in ids:continue
        clone=item.Duplicate();clone.SetUuid(p.KIID(uid));copies[uid]=clone
    return copies



def prove_copper(board,added,graph,covered,deadline):
    proof=FrozenProofRouter(board,deadline=deadline,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000,
                            graph=graph,covered_nets=covered)
    count=0
    for item in board.GetTracks():
        if item.m_Uuid.AsString() not in added:continue
        if proof.expired():raise TimeoutError('Conditional matrix creation proof deadline')
        if isinstance(item,p.PCB_VIA):
            if not saved_via_clear_cached(proof,item):raise ValueError('Conditional matrix via violates creation oracle')
        elif any(item.IsOnLayer(face) and not proof.clear(item.GetEffectiveShape(face),face,item.GetNetname()) for face in (p.F_Cu,p.B_Cu)):
            raise ValueError('Conditional matrix track violates creation oracle')
        count+=1
    proof.assert_unchanged()
    return count
