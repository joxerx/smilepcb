"""Host control tests; boundary search and physical proof remain Native gates."""
import ast
from collections import Counter, deque
from contextlib import contextmanager
from copy import deepcopy
import math
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

PATH = Path(__file__).resolve().parents[1] / 'smilepcb/keyboard_routing/controller_escape.py'


def extracted(name, scope):
    scope.update(__name__='smilepcb.keyboard_routing.controller_escape', __package__='smilepcb.keyboard_routing')
    tree = ast.parse(PATH.read_text())
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(PATH), 'exec'), scope)
    return scope[name]


class Shape:
    def __init__(self, a, b, width=0): self.a, self.b, self.width = a, b, width
    def GetSeg(self): return NS(A=self.a, B=self.b)


def collide(a, b, gap):
    def box(q):
        half=q.width/2
        return (min(q.a[0],q.b[0])-half, min(q.a[1],q.b[1])-half,
                max(q.a[0],q.b[0])+half, max(q.a[1],q.b[1])+half)
    x,y=box(a),box(b)
    return x[0]<=y[2]+gap and y[0]<=x[2]+gap and x[1]<=y[3]+gap and y[1]<=x[3]+gap


class Track:
    def __init__(self, uid, alternate=False):
        self.m_Uuid = NS(AsString=lambda: uid); self.y = 1000000 if alternate else 0
    def GetStart(self): return (0,self.y)
    def GetEnd(self): return (1000000,self.y)
    def GetLayer(self): return 2
    def GetWidth(self): return 200000


class Via(Track): pass


class Board:
    def __init__(self): self.tracks = {'retained': Track('retained')}
    def GetTracks(self): return list(self.tracks.values())
    def Delete(self, track): del self.tracks[track.m_Uuid.AsString()]
    def BuildConnectivity(self): pass
    def Zones(self): return []
    def GetDesignSettings(self): return NS(m_MinClearance=150000)


def attempt(outcomes, *, expire_first=False, reject_proof=False, first_search_s=1,
            phase_points=(), search_reasons=(), expanded=(), via_allowed=True, leg_allowed=True, constructor_s=0, probe_s=0):
    clock = NS(now=0.)
    clock.monotonic = lambda: clock.now
    board = Board()
    snapshot = lambda b: {'tracks': {uid: {'retained': uid == 'retained'} for uid in b.tracks}}
    before = snapshot(board)
    groups = {owner: dict(net=net, bounds=(0, 0, 10, 10),
                          pads=[NS(GetPosition=lambda: (1, 1), IsOnLayer=lambda face: face == 2)],
                          pad_ids=[owner + '-pad']) for owner, net in [('a', 'FIRST'), ('b', 'SECOND')]}
    necks = [dict(owner='b', net='SECOND', direction='inward', shape='hypothetical')]
    searches, proofs, reservations = [], [], []
    fixed = {'terminal_id': 'fixed-usb'}

    @contextmanager
    def reserve(ports):
        reservations.append(ports)
        try: yield
        finally: reservations.pop()

    class Router:
        def __init__(self, candidate, **kw):
            self.options = kw
            clock.now += constructor_s
            self.deadline = kw['deadline']
            self.search_budget_s = kw['search_budget_s']
            self.search_expansion_limit = 40000
            self.grid_step = 125000
            self.via_width = 500000
            self.via_cost_nm = 1000000
            self.probes = []
            self.track_width = 200000
            self.minimum_clearance = 173000
            self.net_clearances = {}
            self.generation = NS(name='dsn-process-v2')
            self.hits = Counter()
            self.added = []

        def via_clear(self, point, net):
            self.probes.append(('via', point))
            clock.now += probe_s
            return via_allowed(point) if callable(via_allowed) else via_allowed

        def leg(self, source, point, face, net):
            self.probes.append(('leg', point))
            return [source, point] if leg_allowed else None

    def search(world, starts, bounds, net, **kw):
        index = len(searches)
        searches.append(dict(options=dict(world.options, deadline=world.deadline,
            search_budget_s=world.search_budget_s, search_expansion_limit=world.search_expansion_limit),
            origin=getattr(world, 'grid_origin_nm', None), net=net, ports=deepcopy(reservations[-1]), probes=world.probes))
        okay, hits = outcomes(world, board, net, index) if callable(outcomes) else outcomes[index]
        world.hits = Counter(hits)
        world.last_search = dict(reason=search_reasons[index] if index < len(search_reasons) else
                                 'found' if okay else 'frontier_exhausted', states=index + 1,
                                 expanded=expanded[index] if index < len(expanded) else index + 1)
        clock.now += first_search_s if index == 0 else 1
        if expire_first and index == 0: clock.now = 95
        if okay:
            uid = 'new-' + net
            board.tracks[uid] = Track(uid, alternate=getattr(world, 'alternate', False))
            world.added = [uid]
            world.last_plan = {'legs': [(2, [(1, 1), (2, 1)])], 'vias': []}
        return okay

    def receipt(candidate, group, world):
        return dict(net=group['net'], pad_ids=group['pad_ids'], added_ids=world.added,
                    terminal_id=world.added[-1])

    def prove(candidate, original, ports, plans, protected, deadline, clearance):
        proofs.append(dict(pads={q for port in ports.values() for q in port['pad_ids']},
                           plans=set(plans), protected=protected, deadline=deadline))
        if reject_proof: raise ValueError('controlled physical proof rejection')

    physics = NS(SHAPE=NS(Collide=collide))
    history_penalty = extracted('_history_penalty', dict(p=physics, math=math, xy=lambda point: point))
    route = extracted('route_controller_escapes', dict(
        time=clock, math=math, Counter=Counter, deque=deque, snapshot=snapshot,
        _inventory=lambda *a: groups, current_launches=lambda: [fixed],
        _EscapeRouter=Router, _launches=lambda *a: (necks, {'a': 0, 'b': 1}),
        reserve_launches=reserve, xy=lambda p: p, p=NS(F_Cu=0, B_Cu=2, FromMM=lambda v: round(v * 1e6),
            PCB_VIA=Via, SHAPE_SEGMENT=Shape, SHAPE_CIRCLE=lambda point,radius: Shape(point,point,2*radius)),
        _history_penalty=history_penalty,
        search_boundary=search, _receipt=receipt, _prove=prove,
        _via_phase_candidates=lambda *a, admissible: (point for point in phase_points if admissible(point))))
    result = route(board, controller_refs={'MCU'}, mcu_refs={'MCU'},
                   include_nets={'FIRST', 'SECOND'}, deadline=100, search_budget_s=7)
    return result, searches, proofs, snapshot(board), before


def test_virtual_neck_rejection_retries_once_under_same_cutoff_and_proves_all_sources():
    result, searches, proofs, _, _ = attempt([
        (False, {'neck:b:inward': 4}), (True, {}), (True, {})])
    assert result['accepted']
    assert [bool(q['options']['necks']) for q in searches] == [True, False, True]
    assert [q['options']['deadline'] for q in searches] == [7, 7, 9]
    assert [q['options']['search_budget_s'] for q in searches] == [7, 6, 7]
    assert all(q['ports'][0]['terminal_id'] == 'fixed-usb' for q in searches)
    assert [q['terminal_id'] for q in searches[2]['ports']] == ['fixed-usb', 'new-FIRST']
    assert proofs == [dict(pads={'a-pad', 'b-pad'}, plans={'a', 'b'},
                           protected=({'terminal_id': 'fixed-usb'},), deadline=100)]
    event = next(q for q in result['events'] if q['event'] == 'relax_future_necks')
    assert event['owner'] == 'a' and event['attempt'] == 1
    assert event['neck_hits'] == [('neck:b:inward', 4)]
    assert event['initial_search']['reason'] == 'frontier_exhausted'
    assert event['search_succeeded'] is True and event['retry_deadline'] == 7
    assert event['owner_search_cutoff'] == 7 and event['remaining_search_s'] == 6


@pytest.mark.parametrize('hits', [{'actual-copper': 4}, {'neck:b:inward': 0}, {}])
def test_no_virtual_collision_does_not_trigger_relaxation(hits):
    result, searches, proofs, after, before = attempt([(False, hits)])
    assert not result['accepted'] and result['rollback_exact']
    assert len(searches) == 1 and not proofs and after == before
    assert not any(q['event'] == 'relax_future_necks' for q in result['events'])


def test_expired_shared_cutoff_does_not_start_relaxed_retry():
    result, searches, proofs, after, before = attempt([(False, {'neck:b:inward': 4})], expire_first=True)
    assert not result['accepted'] and len(searches) == 1 and not proofs and after == before


def test_exhausted_owner_search_allowance_does_not_reset_while_phase_is_live():
    result, searches, proofs, after, before = attempt(
        [(False, {'neck:b:inward': 4})], first_search_s=7)
    assert not result['accepted'] and len(searches) == 1 and not proofs and after == before


def test_relaxed_physical_failure_does_not_retry_again_or_claim_success():
    result, searches, proofs, after, before = attempt([
        (False, {'neck:b:inward': 4}), (False, {'actual-copper': 8})])
    assert not result['accepted'] and result['rollback_exact']
    assert len(searches) == 2 and not proofs and after == before
    event = next(q for q in result['events'] if q['event'] == 'relax_future_necks')
    assert event['search_succeeded'] is False
    assert event['collision_ids'] == [('actual-copper', 8)]


def test_relaxed_failure_still_uses_existing_owned_ripup_path():
    result, searches, proofs, after, _ = attempt([
        (True, {}), (False, {'neck:a:inward': 4}),
        (False, {'new-FIRST': 8}), (True, {}), (True, {})])
    assert result['accepted'] and result['ripups'] == 1
    assert [q['net'] for q in searches] == ['FIRST', 'SECOND', 'SECOND', 'SECOND', 'FIRST']
    assert len(proofs) == 1 and proofs[0]['pads'] == {'a-pad', 'b-pad'}
    assert set(after['tracks']) == {'retained', 'new-FIRST', 'new-SECOND'}
    assert [q for q in result['events'] if q['event'] == 'ripup'] == [
        dict(event='ripup', owner='b', displaced='a', removed=1, history_objects=1)]


def test_relaxed_search_success_cannot_bypass_final_proof_or_exact_rollback():
    result, searches, proofs, after, before = attempt([
        (False, {'neck:b:inward': 4}), (True, {}), (True, {})], reject_proof=True)
    assert not result['accepted'] and result['rollback_exact'] and after == before
    assert len(searches) == 3 and len(proofs) == 1
    assert proofs[0]['pads'] == {'a-pad', 'b-pad'}
    assert result['reason'] == 'controlled physical proof rejection'


def test_neckless_router_still_delegates_collisions_and_via_admission_to_physics():
    class Physical:
        def collisions(self, shape, layer, net, via=False):
            yield 'actual-copper'
            yield 'controller-first-contact'
        def via_clear(self, position, net): return False

    router_type = extracted('_EscapeRouter', dict(LocalRouter=Physical, Counter=Counter,
        p=NS(SHAPE_CIRCLE=lambda *a: object()), vec=lambda q: q))
    world = router_type.__new__(router_type)
    world.necks = (); world.routed = set(); world.hits = Counter(); world.port_policy = None
    world.bounds = (0, 0, 2_000_000, 2_000_000); world.via_width = 500000; world.drill = 300000
    assert list(world.collisions(object(), 2, 'FIRST')) == ['actual-copper', 'controller-first-contact']
    assert world.via_clear((1_000_000, 1_000_000), 'FIRST') is False


def test_frontier_exhaustion_reuses_world_at_useful_event_phase_under_shared_allowances():
    result, searches, proofs, _, _ = attempt(
        [(False, {'pad-obstacle': 1}), (True, {}), (True, {})],
        phase_points=[(2, 2)], expanded=[17, 3, 2])
    assert result['accepted'] and len(proofs) == 1
    assert [q['origin'] for q in searches] == [None, (2, 2), None]
    assert [q['options']['search_expansion_limit'] for q in searches] == [40000, 39983, 40000]
    assert [q['options']['deadline'] for q in searches] == [7, 7, 9]
    assert searches[0]['probes'] == [('via', (2, 2)), ('leg', (2, 2))]
    event = next(q for q in result['events'] if q['event'] == 'adaptive_grid_phase')
    assert event['grid_origin_nm'] == [2, 2] and event['remaining_expansions'] == 39983
    assert event['initial_search']['reason'] == 'frontier_exhausted' and event['search_succeeded']


def test_phase_retries_skip_original_and_duplicate_modulo_phases():
    result, searches, _, _, _ = attempt(
        [(False, {}), (False, {}), (True, {}), (True, {})],
        phase_points=[(1, 1), (2, 2), (125002, 125002), (3, 3)])
    assert result['accepted']
    assert [q['origin'] for q in searches] == [None, (2, 2), (3, 3), None]


@pytest.mark.parametrize('reason', ['search_budget', 'expansion_budget', 'reconstruction_rejected'])
def test_phase_retry_requires_frontier_exhaustion(reason):
    result, searches, proofs, after, before = attempt(
        [(False, {})], phase_points=[(2, 2)], search_reasons=[reason])
    assert not result['accepted'] and not proofs and after == before
    assert len(searches) == 1 and not searches[0]['probes']


@pytest.mark.parametrize('via,leg', [(False, True)])
def test_event_phase_requires_actual_via_and_source_leg_admission(via, leg):
    result, searches, proofs, after, before = attempt(
        [(False, {})], phase_points=[(2, 2)], via_allowed=via, leg_allowed=leg)
    assert not result['accepted'] and not proofs and after == before and len(searches) == 1
    assert searches[0]['probes'] and searches[0]['probes'][0] == ('via', (2, 2))
    assert (('leg', (2, 2)) in searches[0]['probes']) is via


def test_initial_soft_and_phase_retries_share_one_expansion_pool():
    result, searches, proofs, after, before = attempt(
        [(False, {'neck:b:inward': 1}), (False, {}), (False, {})],
        phase_points=[(2, 2), (3, 3)], expanded=[39998, 1, 1])
    assert not result['accepted'] and not proofs and after == before
    assert [q['options']['search_expansion_limit'] for q in searches] == [40000, 2, 1]
    assert len(searches) == 3


def test_exhausted_expansions_cannot_start_soft_or_phase_retry():
    result, searches, proofs, after, before = attempt(
        [(False, {'neck:b:inward': 1}), (False, {})], phase_points=[(2, 2)], expanded=[40000])
    assert not result['accepted'] and len(searches) == 1 and not proofs and after == before


def test_constructor_time_is_inside_owner_cutoff_for_both_search_worlds():
    result, searches, _, _, _ = attempt(
        [(False, {'neck:b:inward': 1}), (True, {}), (True, {})], constructor_s=1)
    assert result['accepted']
    assert [q['options']['deadline'] for q in searches] == [8, 8, 12]
    assert [q['options']['search_budget_s'] for q in searches] == [6, 4, 6]


def test_phase_success_still_requires_every_source_and_final_proof():
    result, searches, proofs, after, before = attempt(
        [(False, {}), (True, {}), (True, {})], phase_points=[(2, 2)], reject_proof=True)
    assert not result['accepted'] and result['rollback_exact'] and after == before
    assert len(searches) == 3 and proofs[0]['pads'] == {'a-pad', 'b-pad'}


def test_phase_adapter_uses_hit_envelopes_actual_lands_holes_and_source_pads(monkeypatch):
    import sys
    class Shape:
        def __init__(self, box): self.box = box
        def BBox(self):
            l, t, r, b = self.box
            return NS(GetLeft=lambda: l, GetTop=lambda: t, GetRight=lambda: r, GetBottom=lambda: b)
    source = Shape((100, 200, 300, 400))
    envelope = Shape((10, 20, 30, 40))
    land = Shape((11, 21, 29, 39))
    hole = Shape((12, 22, 28, 38))
    pad = NS(m_Uuid=NS(AsString=lambda: 'source'), GetPosition=lambda: (200, 300),
             IsOnLayer=lambda face: face == 2, GetEffectiveShape=lambda face: source,
             GetAttribute=lambda: 'SMD')
    rows = [('hit', envelope, 'FOREIGN', 9), ('unrelated', source, 'OTHER', 99)]
    world = NS(deadline=10, via_width=500000, drill=300000, grid_step=125, net_clearances={'SIGNAL': 20},
        generation=NS(name='dsn-process-v2', foreign_copper_clearance_nm=17,
                      via_to_smd_clearance_nm=23, via_to_hole_clearance_nm=29),
        buckets={2: {(0, 0): rows, (0, 1): rows}}, solder_lands={(0, 0): [('hit', land)]},
        holes={(0, 0): [('hit', hole, 'FOREIGN')]}, hole_clearances={'hit': 31})
    captured = {}
    def candidates(obstacles, starts, **kw):
        captured.update(obstacles=obstacles, starts=starts, **kw)
        return iter([(777, 888)])
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.controller_phase_geometry',
                        NS(iter_via_phase_candidates=candidates))
    adapter = extracted('_via_phase_candidates', dict(time=NS(monotonic=lambda: 0),
        p=NS(F_Cu=0, B_Cu=2, PAD_ATTRIB_SMD='SMD'), xy=lambda point: point))
    group = dict(net='SIGNAL', pad_ids=['source'], pads=[pad], bounds=(0, 0, 1000, 1000))
    admissible = lambda point: True
    assert list(adapter(world, group, Counter({'hit': 2}), admissible=admissible)) == [(777, 888)]
    assert captured['admissible'] is admissible
    assert {(tuple(q['box_nm']), q['clearance_nm']) for q in captured['obstacles']} == {
        ((10, 20, 30, 40), 20), ((11, 21, 29, 39), 23),
        ((12, 22, 28, 38), 31), ((100, 200, 300, 400), 23)}
    assert len(captured['obstacles']) == 4
    assert captured['starts'] == [(200, 300)]
    assert captured['bounds_nm'] == (-750000, -750000, 751000, 751000)
    assert captured['via_width_nm'] == 500000 and captured['grid_step_nm'] == 125


def test_invalid_nearest_point_does_not_hide_farther_admissible_same_phase():
    result, searches, proofs, _, _ = attempt(
        [(False, {}), (True, {}), (True, {})],
        phase_points=[(2, 2), (125002, 125002)],
        via_allowed=lambda point: point == (125002, 125002))
    assert result['accepted'] and len(proofs) == 1
    assert [q['origin'] for q in searches] == [None, (125002, 125002), None]


def test_timeout_during_native_phase_filtering_cannot_start_search_or_claim_success():
    result, searches, proofs, after, before = attempt(
        [(False, {})], phase_points=[(2, 2)], probe_s=7)
    assert not result['accepted'] and result['rollback_exact'] and after == before
    assert len(searches) == 1 and not proofs
    assert 'phase deadline exhausted' in result['reason']


def test_constructor_cannot_spend_beyond_owner_cutoff_and_start_search():
    result, searches, proofs, after, before = attempt([], constructor_s=8)
    assert not result['accepted'] and result['rollback_exact'] and after == before
    assert not searches and not proofs
    assert 'owner search deadline exhausted' in result['reason']


def test_lazy_phase_admission_stops_immediately_after_first_search_success():
    def via(point):
        assert point == (2, 2), 'Later event admission must remain unevaluated after success'
        return True
    result, searches, proofs, _, _ = attempt(
        [(False, {}), (True, {}), (True, {})],
        phase_points=[(2, 2), (3, 3)], via_allowed=via)
    assert result['accepted'] and len(proofs) == 1
    assert searches[0]['probes'] == [('via', (2, 2)), ('leg', (2, 2))]
    summary = next(q for q in result['events'] if q['event'] == 'adaptive_grid_phases')
    assert summary['candidate_count'] == 1 and not summary['enumeration_complete']


def test_via_clear_phase_without_direct_leg_remains_a_deferred_search_candidate():
    result, searches, proofs, _, _ = attempt(
        [(False, {}), (True, {}), (True, {})], phase_points=[(2, 2)], leg_allowed=False)
    assert result['accepted'] and len(proofs) == 1
    assert [q['origin'] for q in searches] == [None, (2, 2), None]
    summary = next(q for q in result['events'] if q['event'] == 'adaptive_grid_phases')
    assert summary['deferred_without_direct_lead'] == 1


@pytest.mark.parametrize('reject_proof',[False,True])
def test_owned_conflict_history_breaks_two_route_oscillation_without_skipping_proof(reject_proof):
    def kernel(world, board, net, index):
        penalty=getattr(world, 'routing_penalty', None)
        old=Shape((0,0),(1000000,0),200000)
        alternate=Shape((0,1000000),(1000000,1000000),200000)
        if net=='FIRST':
            old_cost=1000000+(penalty(old,2,net,False) if penalty else 0)
            alt_cost=1500000+(penalty(alternate,2,net,False) if penalty else 0)
            world.alternate=alt_cost<old_cost
            if 'new-SECOND' in board.tracks and not world.alternate:
                return False, {'new-SECOND':4}
        elif 'new-FIRST' in board.tracks and not board.tracks['new-FIRST'].y:
            return False, {'new-FIRST':4}
        return True, {}
    result, searches, proofs, after, before=attempt(kernel,reject_proof=reject_proof)
    assert result['accepted'] is not reject_proof
    assert len([q for q in result['events'] if q['event']=='ripup'])==1
    if reject_proof:assert result['rollback_exact'] and after==before
    else:assert result['ripups']==1
    assert [q['net'] for q in searches]==['FIRST','SECOND','SECOND','FIRST']
    assert len(proofs)==1 and proofs[0]['pads']=={'a-pad','b-pad'}
    assert all(q['ports'][0]['terminal_id']=='fixed-usb' for q in searches)
    if not reject_proof:assert set(after['tracks'])=={'retained','new-FIRST','new-SECOND'}


def test_history_is_finite_length_scaled_same_face_cost_and_duplicate_independent():
    penalty=extracted('_history_penalty', dict(p=NS(SHAPE=NS(Collide=collide)),math=math,xy=lambda point:point))
    remembered=Shape((0,0),(1000000,0),200000)
    cost=penalty([(2,remembered),(2,remembered)],1000000)
    assert cost(Shape((0,0),(250000,0),200000),2,'ANY',False)==250000
    assert cost(Shape((0,0),(500000,0),200000),2,'ANY',False)==500000
    assert cost(remembered,0,'ANY',False)==0
    assert cost(Shape((0,1000000),(1000000,1000000),200000),2,'ANY',False)==0
    assert cost(remembered,2,'ANY',True)==1000000
    assert penalty([],1000000)(remembered,2,'ANY',False)==0
