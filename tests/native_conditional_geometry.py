"""Native differential coverage for immutable conditional-bank geometry proofs.

Synthetic coordinates are independent guard witnesses. No routing recipe, private
board, external research path or saved board is required by these tests.
"""
import json
import sys
import time
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.local_router import vec
from smilepcb.keyboard_routing.conditional_geometry import (
    FrozenProofRouter, saved_via_clear_cached, certify_pair,
)

def saved_via_clear(board,via,*,deadline,track_width_nm,graph=None):
    """Recheck a saved via, excluding only its own indexed annulus and hole."""
    from smilepcb.keyboard_routing.local_router import LocalRouter
    if time.monotonic()>=deadline:raise TimeoutError('Saved-via proof deadline')
    oracle=LocalRouter(board,deadline=deadline,compact=True,
        ignored_copper=(via.m_Uuid.AsString(),),track_width_nm=track_width_nm)
    oracle.via_width=via.GetWidth(p.F_Cu);oracle.drill=via.GetDrillValue()
    if graph is not None:oracle.refresh_interfaces(graph)
    clear=oracle.via_clear((via.GetPosition().x,via.GetPosition().y),via.GetNetname())
    if time.monotonic()>=deadline:raise TimeoutError('Saved-via proof deadline')
    return clear


def selftest(oracle,*,deadline):
    from smilepcb.keyboard_process import apply_native_rules
    from smilepcb.keyboard_routing.local_router import LocalRouter
    from smilepcb.keyboard_routing.interface_launches import reserve_launches
    def plan(bank,shapes):return {'bank':bank,'id':'test','shapes':shapes,'proof':{'geometry_sha256':bank}}
    def track(net,layer,a,z):return {'class':'PCB_TRACK','net':net,'layer':layer,'start':a,'end':z,'width':150000}
    def via(net,at):return {'class':'PCB_VIA','net':net,'start':at,'end':at,'width':500000,'drill':200000,'top':p.F_Cu,'bottom':p.B_Cu,'via_type':p.VIATYPE_THROUGH}
    kw=dict(base_sha256='test',profile='test',minimum_clearance=oracle.minimum_clearance,
            net_clearances={},hole_clearance=oracle.generation.via_to_hole_clearance_nm,deadline=deadline)
    a=plan('a',{'track':track('a',p.F_Cu,[0,1000000],[2000000,1000000])})
    b=plan('b',{'track':track('b',p.B_Cu,[1000000,0],[1000000,2000000])})
    if not certify_pair(a,b,**kw)['clear']:raise ValueError('Opposite-face crossing selftest failed')
    b['shapes']['through']=via('b',[1000000,1000000])
    if certify_pair(a,b,**kw)['clear']:raise ValueError('Through-via two-face occupancy selftest failed')
    a=plan('a',{'first':via('same',[1000000,1000000]),'second':via('same',[5000000,5000000])})
    b=plan('b',{'near-second':via('same',[5550000,5000000])})
    proof=certify_pair(a,b,**kw)
    if proof['clear'] or not any(q['a_shape']=='second' and q['kind']=='hole' for q in proof['conflicts']):
        raise ValueError('All-via drill clearance selftest failed')
    # Exercise the same saved-object validator as the domain acceptance path.
    # Larger actual dimensions distinguish it from compact default dimensions.
    board=p.BOARD();board.SetCopperLayerCount(2)
    apply_native_rules(board,'fr4-2l-1oz-0p5-0p2')
    corners=[(0,0),(10000000,0),(10000000,10000000),(0,10000000),(0,0)]
    for a,z in zip(corners,corners[1:]):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(z))
        edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
    board.Add(p.NETINFO_ITEM(board,'saved-via-witness'))
    def add_saved(at):
        item=p.PCB_VIA(board);item.SetPosition(vec(at));item.SetWidth(700000);item.SetDrill(300000)
        item.SetViaType(p.VIATYPE_THROUGH);item.SetLayerPair(p.F_Cu,p.B_Cu)
        item.SetNet(board.FindNet('saved-via-witness'));board.Add(item);return item
    first=add_saved((5000000,5000000));stop=deadline
    with reserve_launches([]):
        raw=LocalRouter(board,deadline=stop,compact=True,track_width_nm=150000)
        raw.via_width=first.GetWidth(p.F_Cu);raw.drill=first.GetDrillValue()
        if raw.via_clear((5000000,5000000),'saved-via-witness'):
            raise ValueError('Saved-via self-hole positive control failed')
        if not saved_via_clear(board,first,deadline=stop,track_width_nm=150000):
            raise ValueError('Saved via must pass when only its own UUID is excluded')
        add_saved((5700000,5000000))
        compact=LocalRouter(board,deadline=stop,compact=True,ignored_copper=(first.m_Uuid.AsString(),),track_width_nm=150000)
        if not compact.via_clear((5000000,5000000),'saved-via-witness'):
            raise ValueError('Saved-via actual-dimensions distinguishing control failed')
        if saved_via_clear(board,first,deadline=stop,track_width_nm=150000):
            raise ValueError('Saved-via proof omitted another same-net via hole or actual dimensions')
    return {'opposite_face_crossing':True,'through_via_occupies_both_faces':True,'second_via_hole_checked':True,
        'saved_via_self_exclusion':True,'saved_via_actual_dimensions':True,'other_same_net_via_hole_retained':True}


def _index_state(oracle):
    """Record exact list/shape identities as well as scalar index contents."""
    def table_state(table):
        return (id(table), tuple((cell, id(items), tuple(
            (id(row), row[0], id(row[1]), *row[2:]) for row in items))
            for cell, items in sorted(table.items())))
    return (tuple((face, table_state(table)) for face, table in sorted(oracle.buckets.items())),
            table_state(oracle.holes), table_state(oracle.solder_lands),
            tuple(sorted(oracle.net_clearances.items())),
            tuple(sorted(oracle.hole_clearances.items())),
            oracle.via_width, oracle.drill, oracle._proof_active)


def run_proof_regressions(existing_helper, cached_helper, router_class, deadline):
    """Compare frozen reconstruction and reuse; raise on any mismatched guard.

    The supplied deadline bounds the whole fixture batch. No board is saved and
    no production or input board is modified. Every ordinary geometry case has
    an independent expected result, and is replayed in reverse on the same
    cache. The exception cases also require exact index restoration.
    """
    from smilepcb.native_worker import p, snapshot
    from smilepcb.keyboard_process import apply_native_rules
    from smilepcb.keyboard_routing.generation import generation_profile
    from smilepcb.keyboard_routing.interface_launches import reserve_launches
    from smilepcb.keyboard_routing.local_router import vec
    from smilepcb.keyboard_routing.native_graph import copper_graph

    width = 150000
    names = {'test', 'foreign'}
    results = {}

    def budget():
        if time.monotonic() >= deadline:
            raise TimeoutError('Proof regression batch deadline')

    def board():
        budget()
        result = p.BOARD()
        result.SetCopperLayerCount(2)
        apply_native_rules(result, 'fr4-2l-1oz-0p5-0p2')
        corners = [(0, 0), (20000000, 0), (20000000, 20000000),
                   (0, 20000000), (0, 0)]
        for start, end in zip(corners, corners[1:]):
            item = p.PCB_SHAPE(result)
            item.SetShape(p.SHAPE_T_SEGMENT)
            item.SetStart(vec(start))
            item.SetEnd(vec(end))
            item.SetLayer(p.Edge_Cuts)
            item.SetWidth(50000)
            result.Add(item)
        for name in sorted(names):
            result.Add(p.NETINFO_ITEM(result, name))
        return result

    def via(b, point=(10000000, 10000000), net='test', diameter=700000, drill=300000):
        item = p.PCB_VIA(b)
        item.SetPosition(vec(point))
        item.SetWidth(diameter)
        item.SetDrill(drill)
        item.SetViaType(p.VIATYPE_THROUGH)
        item.SetLayerPair(p.F_Cu, p.B_Cu)
        item.SetNet(b.FindNet(net))
        b.Add(item)
        return item

    def track(b, start, end, face, net='foreign'):
        item = p.PCB_TRACK(b)
        item.SetStart(vec(start))
        item.SetEnd(vec(end))
        item.SetWidth(width)
        item.SetLayer(face)
        item.SetNet(b.FindNet(net))
        b.Add(item)
        return item

    def pad(b, point, face, net='test', clearance=None):
        footprint = p.FOOTPRINT(b)
        footprint.SetReference('T' + str(len(list(b.GetFootprints())) + 1))
        b.Add(footprint)
        item = p.PAD(footprint)
        item.SetNumber('1')
        item.SetAttribute(p.PAD_ATTRIB_SMD)
        item.SetShape(p.PAD_SHAPE_RECT)
        item.SetSize(vec((300000, 300000)))
        layers = p.LSET()
        layers.AddLayer(face)
        item.SetLayerSet(layers)
        item.SetPosition(vec(point))
        item.SetNet(b.FindNet(net))
        if clearance is not None:
            item.SetLocalClearance(clearance)
        footprint.Add(item)
        return item

    def frozen(b, *, covered=names, cls=router_class):
        # BuildConnectivity may propagate nets across deliberately illegal
        # bare-copper crossings. This fixture must retain the input bindings;
        # copper_graph already derives contact from actual native shapes.
        before = snapshot(b)
        bindings = {item.m_Uuid.AsString(): (item.GetNetname(), item.GetNetCode())
                    for item in b.GetTracks()}
        graph = copper_graph(b, names, deadline)
        oracle = cls(b, deadline=deadline, track_width_nm=width,
                     graph=graph, covered_nets=covered)
        after_bindings = {item.m_Uuid.AsString(): (item.GetNetname(), item.GetNetCode())
                          for item in b.GetTracks()}
        if snapshot(b) != before or after_bindings != bindings:
            raise AssertionError('Proof fixture construction changed native geometry or net bindings')
        return oracle, graph

    def geometry_case(name, b, queries, *, launches=()):
        budget()
        before = snapshot(b)
        bindings_before = {item.m_Uuid.AsString(): [item.GetNetname(), item.GetNetCode()]
                           for item in b.GetTracks()}
        with reserve_launches(launches):
            oracle, graph = frozen(b)
            indexed = _index_state(oracle)
            old_time = new_time = 0.0
            for item, expected in queries:
                start = time.monotonic()
                old = existing_helper(b, item, deadline=deadline,
                                      track_width_nm=width, graph=graph)
                old_time += time.monotonic() - start
                if old is not expected:
                    # Preserve actual evidence before changing a failed fixture:
                    # native connectivity can propagate a bare track's net.
                    import json
                    from smilepcb.keyboard_routing.local_router import LocalRouter
                    circle = p.SHAPE_CIRCLE(item.GetPosition(), item.GetWidth(p.F_Cu) // 2)
                    probe = LocalRouter(b, deadline=deadline, compact=True,
                        ignored_copper=(item.m_Uuid.AsString(),), track_width_nm=width)
                    caps = []
                    for other in b.GetTracks():
                        for face in (p.F_Cu, p.B_Cu):
                            if other.IsOnLayer(face):
                                shape = other.GetEffectiveShape(face)
                                caps.append({'uuid': other.m_Uuid.AsString(), 'face': face,
                                    'shape': shape.Format(False),
                                    'circle_collides_0': p.SHAPE.Collide(circle, shape, 0)})
                    diagnostic = {'case': name, 'before': before, 'after': snapshot(b),
                        'bindings_before': bindings_before,
                        'bindings_after': {t.m_Uuid.AsString(): [t.GetNetname(), t.GetNetCode()]
                                           for t in b.GetTracks()},
                        'candidate_uuid': item.m_Uuid.AsString(), 'caps': caps,
                        'collisions': {str(face): list(probe.collisions(circle, face, item.GetNetname(), via=True))
                                       for face in (p.F_Cu, p.B_Cu)},
                        'bucket_ids': {str(face): sorted({row[0] for values in table.values() for row in values})
                                       for face, table in probe.buckets.items()}}
                    raise AssertionError(f'{name}: frozen baseline {old!r}, expected {expected!r}; '
                                         + json.dumps(diagnostic, sort_keys=True))
                start = time.monotonic()
                new = cached_helper(oracle, item)
                new_time += time.monotonic() - start
                if new is not old:
                    raise AssertionError(f'{name}: cached {new!r} differs from frozen {old!r}')
                if _index_state(oracle) != indexed:
                    raise AssertionError(name + ': cached query changed an index or dimension')
                oracle.assert_unchanged()
            for item, expected in reversed(queries):
                if cached_helper(oracle, item) is not expected:
                    raise AssertionError(name + ': reverse replay differs')
                if _index_state(oracle) != indexed:
                    raise AssertionError(name + ': reverse replay failed exact restoration')
            oracle.assert_unchanged()
        if snapshot(b) != before:
            raise AssertionError(name + ': proof mutated native copper or pads')
        results[name] = {'passed': True, 'queries': len(queries),
                         'frozen_s': round(old_time, 6), 'cached_s': round(new_time, 6)}

    def must_raise(kind, function, name):
        try:
            function()
        except kind:
            return
        raise AssertionError(name + ': expected ' + kind.__name__)

    with generation_profile('dsn-process-v2'):
        b = board()
        geometry_case('own_uuid_only', b, [(via(b), True)])

        # Alternating two colliding vias and an independent clear via catches
        # leaked exclusions across both faces and the physical-hole index.
        b = board()
        first = via(b)
        second = via(b, (10700000, 10000000))
        clear = via(b, (15000000, 15000000), diameter=500000, drill=200000)
        geometry_case('actual_dimensions_and_other_same_net_hole', b,
                      [(first, False), (clear, True), (second, False), (first, False)])

        for face in (p.F_Cu, p.B_Cu):
            b = board()
            candidate = via(b)
            track(b, (10000000, 9000000), (10000000, 11000000), face)
            geometry_case('foreign_track_face_' + str(face), b, [(candidate, False)])
            b = board()
            candidate = via(b)
            # Drill-to-land clears; the stronger same-net annulus-to-land
            # guard rejects (150 um actual copper gap < 173 um profile guard).
            pad(b, (10650000, 10000000), face)
            geometry_case('same_net_smd_annulus_face_' + str(face), b, [(candidate, False)])

        for x, expected in ((500000, False), (800000, True)):
            b = board()
            geometry_case('outline_edge_' + str(expected), b, [(via(b, (x, 10000000)), expected)])

        for clearance, expected in ((None, True), (800000, False)):
            b = board()
            candidate = via(b)
            pad(b, (2000000, 2000000), p.F_Cu, clearance=clearance)
            track(b, (11000000, 9000000), (11000000, 11000000), p.F_Cu)
            geometry_case('retained_net_clearance_' + str(clearance), b, [(candidate, expected)])

        # The completed launch actually retires from source+field pad closure;
        # the old validator refreshes it again per via, the cache reuses proof.
        b = board()
        source = pad(b, (6000000, 10000000), p.F_Cu)
        pad(b, (14000000, 10000000), p.F_Cu)
        candidate = via(b)
        head = track(b, (6000000, 10000000), (9000000, 10000000), p.F_Cu, 'test')
        track(b, (9000000, 10000000), (14000000, 10000000), p.F_Cu, 'test')
        port = {'net': 'test', 'pad_ids': [source.m_Uuid.AsString()],
                'added_ids': [head.m_Uuid.AsString()], 'terminal_id': head.m_Uuid.AsString(),
                'exit_nm': [9000000, 10000000], 'exit_layer': p.F_Cu,
                'heading_origin_nm': [6000000, 10000000]}
        geometry_case('native_closed_launch_graph', b, [(candidate, True)], launches=[port])

        # Uncovered-net failure occurs inside via_clear, after filtering. It
        # therefore tests the real finally path rather than a preflight return.
        b = board()
        candidate = via(b)
        with reserve_launches([]):
            oracle, graph = frozen(b, covered={'foreign'})
            indexed = _index_state(oracle)
            must_raise(ValueError, lambda: cached_helper(oracle, candidate), 'uncovered net')
            if _index_state(oracle) != indexed:
                raise AssertionError('Uncovered-net rejection leaked temporary exclusion')
            oracle.assert_unchanged()
            results['uncovered_net_exception_restoration'] = {'passed': True}

            oracle, graph = frozen(b)
            indexed = _index_state(oracle)
            with reserve_launches([]):
                must_raise(ValueError, lambda: cached_helper(oracle, candidate), 'changed launch scope')
            if _index_state(oracle) != indexed:
                raise AssertionError('Launch-scope rejection leaked temporary exclusion')
            oracle.assert_unchanged()
            results['launch_scope_exception_restoration'] = {'passed': True}

            expired = time.monotonic() - 1
            must_raise(TimeoutError, lambda: existing_helper(b, candidate, deadline=expired,
                       track_width_nm=width, graph=graph), 'frozen expired deadline')
            oracle.deadline = expired
            try:
                must_raise(TimeoutError, lambda: cached_helper(oracle, candidate), 'cached expired deadline')
            finally:
                oracle.deadline = deadline
            if _index_state(oracle) != indexed:
                raise AssertionError('Expired-deadline rejection changed index')
            oracle.assert_unchanged()
            results['expired_deadline_not_geometric_rejection'] = {'passed': True}

            # Controlled boundary injection advances only this oracle's clock
            # after filtering; its real via_clear then sees expiration. This
            # checks timeout classification and finally restoration mid-query.
            class ExpiringRouter(router_class):
                def via_clear(self, position, net):
                    self.deadline = time.monotonic() - 1
                    return super().via_clear(position, net)
            oracle, graph = frozen(b, cls=ExpiringRouter)
            indexed = _index_state(oracle)
            try:
                must_raise(TimeoutError, lambda: cached_helper(oracle, candidate), 'mid-query deadline')
            finally:
                oracle.deadline = deadline
            if _index_state(oracle) != indexed:
                raise AssertionError('Mid-query timeout leaked temporary exclusion')
            oracle.assert_unchanged()
            results['mid_query_deadline_exact_restoration'] = {'passed': True}

            oracle, graph = frozen(b)
            candidate.SetWidth(800000)
            must_raise(ValueError, oracle.assert_unchanged, 'changed native via')
            results['native_copper_mutation_guard'] = {'passed': True}

        # Native snapshot omits these changes. Preserve a valid closed outline
        # so this exercises the physical seal, not merely an invalid-board error.
        b = board()
        via(b)
        with reserve_launches([]):
            oracle, graph = frozen(b)
            for edge in b.GetDrawings():
                start, end = edge.GetStart(), edge.GetEnd()
                edge.SetStart(vec((start.x + 10000, start.y)))
                edge.SetEnd(vec((end.x + 10000, end.y)))
            must_raise(ValueError, oracle.assert_unchanged, 'changed valid outline')
            results['native_outline_mutation_guard'] = {'passed': True}

        b = board()
        via(b)
        land = pad(b, (2000000, 2000000), p.F_Cu)
        with reserve_launches([]):
            oracle, graph = frozen(b)
            land.SetLocalClearance(800000)
            must_raise(ValueError, oracle.assert_unchanged, 'changed local clearance')
            results['native_local_clearance_mutation_guard'] = {'passed': True}

    budget()
    return {'passed': True, 'cases': results, 'case_count': len(results),
            'scope': 'synthetic native differential; saved-domain differential still required'}



def generated_via_replay(output):
    """Snapshot replay must retain the generator's explicit mask tenting."""
    from smilepcb.native_worker import snapshot
    from smilepcb.keyboard_routing.conditional_geometry import emit_shapes
    from smilepcb.keyboard_routing.safety import prepare_added_via
    board=p.BOARD();board.SetCopperLayerCount(2)
    board.Add(p.NETINFO_ITEM(board,'replay'))
    via=p.PCB_VIA(board);via.SetPosition(vec((3000000,4000000)))
    via.SetWidth(500000);via.SetDrill(200000);via.SetViaType(p.VIATYPE_THROUGH)
    via.SetLayerPair(p.F_Cu,p.B_Cu);via.SetNet(board.FindNet('replay'))
    prepare_added_via(via);board.Add(via)
    def tented(item):
        return [item.GetFrontTentingMode()==p.TENTING_MODE_TENTED,
                item.GetBackTentingMode()==p.TENTING_MODE_TENTED]
    original=tented(via);before=snapshot(board)
    board.Delete(via);emit_shapes(board,before['tracks'])
    emitted=next(iter(board.GetTracks()));replayed=tented(emitted)
    path=output/'via-replay.kicad_pcb'
    assert p.SaveBoard(str(path),board)
    reloaded=p.LoadBoard(str(path))
    return {'original':original,'replayed':replayed,
            'reloaded':tented(next(iter(reloaded.GetTracks()))),
            'snapshot_exact':snapshot(board)==before and snapshot(reloaded)==before}


def main():
    from types import SimpleNamespace
    from smilepcb.keyboard_routing.generation import generation_profile, current_generation_profile
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 45
    with generation_profile('dsn-process-v2'):
        reference = SimpleNamespace(minimum_clearance=173000, generation=current_generation_profile())
        report = {'via_replay': generated_via_replay(output),
                  'pairs_and_saved_vias': selftest(reference, deadline=deadline),
                  'frozen_index': run_proof_regressions(saved_via_clear,
                      saved_via_clear_cached, FrozenProofRouter, deadline)}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
