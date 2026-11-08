import math

from smilepcb.keyboard_routing.free_corridor_geometry import terminal_groups, corridor_candidates, blocker_closure
from smilepcb.keyboard_routing.bundle_geometry import neighbor_groups, paths_clear
from smilepcb.keyboard_routing.polish_geometry import valid_shape


def chain(net, path):
    return dict(net=net, path=path, layer=0, width=150000, ids=[net], start_layer=0, end_layer=0)


def test_terminal_banks_seed_a_group_with_no_existing_parallel_overlap():
    rows = [chain('a', [(4000000,0),(20000000,0)]),
            chain('b', [(4000000,500000),(20000000,16500000)]),
            chain('c', [(4000000,1000000),(4000000,20000000)])]
    assert neighbor_groups(rows) == []
    groups = terminal_groups(rows, (0,0))
    assert any({q['net'] for q in g} == {'a','b','c'} for g in groups)
    assert len(groups) == len({tuple(sorted(q['net'] for q in g)) for g in groups})


def test_corridor_candidates_preserve_terminals_and_offer_shared_uniform_lanes():
    rows = [chain('a', [(0,0),(5000000,5000000),(20000000,5000000)]),
            chain('b', [(0,500000),(8000000,8500000),(20000000,8500000)])]
    proposals = corridor_candidates(rows, clearance_nm=198000, limit=32)
    assert proposals
    for proposal in proposals:
        assert all(path[0] == row['path'][0] and path[-1] == row['path'][-1]
                   and valid_shape(path) for row,path in zip(rows,proposal['paths']))
    assert any(paths_clear(q['paths'],[150000,150000],198000) and q['parallel_mm'] >= 6
               for q in proposals)


def test_blocker_closure_moves_whole_chains_and_respects_protected_contacts():
    rows=[chain('a',[(0,0),(1000000,0)]),chain('b',[(0,1),(1000000,1)])]
    rows[0]['ids']=['a0','a1']; rows[1]['ids']=['b0','b1']
    assert blocker_closure({'a1'},rows,protected_ids=set()) == {'a0','a1'}
    assert blocker_closure({'a1'},rows,protected_ids={'a0'}) == set()
    assert blocker_closure({'pad'},rows,protected_ids=set()) == set()


def test_restoration_respects_column_shape_and_stops_before_expired_generation():
    from smilepcb.keyboard_routing.free_corridor_geometry import restoration_paths
    import time
    row=chain('column',[(0,0),(0,8000000),(2000000,10000000)])
    assert list(restoration_paths(row,deadline=time.monotonic()-1,preserve_v45_nets={'column'}))==[]
    paths=list(restoration_paths(row,deadline=time.monotonic()+1,preserve_v45_nets={'column'}))
    assert paths and any(path!=row['path'] for path in paths)
    assert all(all(a[1]!=b[1] for a,b in zip(path,path[1:])) for path in paths)


def test_restoring_a_blocker_on_its_wrong_face_has_a_cost():
    from smilepcb.keyboard_routing.free_corridor_geometry import restoration_cost_delta
    row=chain('column',[(0,0),(0,10000000)])
    row['old_legs']=[(0,row['path'])]
    before={'column':{'class':'PCB_TRACK'}}
    moved={'row':row,'path':row['path'],'route':{'legs':[(2,row['path'])],'vias':[]}}
    assert restoration_cost_delta(moved,before,preferred_layer=0)>30000000
