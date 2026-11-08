"""Pure via/grid phase events; no Native clearance or routing claims."""
from copy import deepcopy
import importlib
import importlib.util
import pytest


def candidates(*a,**kw):
    assert importlib.util.find_spec('smilepcb.keyboard_routing.controller_phase_geometry') is not None,'obstacle-derived phase helper is missing'
    return importlib.import_module('smilepcb.keyboard_routing.controller_phase_geometry').via_phase_candidates(*a,**kw)


def phase(point,origin,step):return tuple((point[i]-origin[i])%step for i in (0,1))


def test_two_expanded_smd_boxes_supply_interstice_missing_from_original_grid():
    obstacles=[dict(box_nm=[0,0,100,200],clearance_nm=10),dict(box_nm=[220,0,320,200],clearance_nm=10)]
    starts=[[0,100]];before=deepcopy((obstacles,starts));bounds=[0,-100,320,300]
    points=candidates(obstacles,starts,via_width_nm=80,grid_step_nm=125,bounds_nm=bounds,inset_nm=1)
    assert (151,100) in points or (169,100) in points
    pocket=[p for p in points if 151<=p[0]<=169 and p[1]==100]
    assert pocket and all(phase(p,starts[0],125)!=(0,0) for p in pocket)
    assert not any(151<=x<=169 for x in range(0,321,125))
    assert (obstacles,starts)==before


def test_phase_dedup_nearest_representative_order_and_source_axes_are_deterministic():
    obstacles=[dict(box_nm=[20,30,100,130],clearance_nm=3),dict(box_nm=[200,50,250,90],clearance_nm=7)]
    starts=[[11,17],[170,180]];kwargs=dict(via_width_nm=21,grid_step_nm=37,bounds_nm=[-100,-100,300,300],inset_nm=2)
    points=candidates(obstacles,starts,**kwargs)
    assert points==candidates(list(reversed(obstacles)),starts,**kwargs)
    phases=[phase(p,starts[0],37) for p in points]
    assert len(phases)==len(set(phases)) and (0,0) not in phases
    score=lambda p:(min((p[0]-s[0])**2+(p[1]-s[1])**2 for s in starts),p)
    assert points==sorted(points,key=score) and tuple(starts[1]) in points


def test_translation_preserves_every_candidate_and_relative_phase():
    obstacles=[dict(box_nm=[5,9,46,61],clearance_nm=4)];starts=[[2,3]];delta=(-83,127)
    kwargs=dict(via_width_nm=15,grid_step_nm=31,bounds_nm=[-40,-40,120,130],inset_nm=1)
    original=candidates(obstacles,starts,**kwargs)
    shift=lambda p:[p[0]+delta[0],p[1]+delta[1]]
    moved=[dict(box_nm=[*shift(q['box_nm'][:2]),*shift(q['box_nm'][2:])],clearance_nm=q['clearance_nm']) for q in obstacles]
    translated=candidates(moved,[shift(s) for s in starts],**dict(kwargs,bounds_nm=[*shift(kwargs['bounds_nm'][:2]),*shift(kwargs['bounds_nm'][2:])]))
    assert translated==[tuple(shift(p)) for p in original]


def test_cardinal_rotation_preserves_obstacle_derived_phase_inventory():
    obstacles=[dict(box_nm=[5,9,46,61],clearance_nm=4),dict(box_nm=[71,12,98,35],clearance_nm=2)];starts=[[2,3]]
    bounds=[-40,-30,120,130];step=31
    turn=lambda p:(-p[1],p[0])
    def box_turn(b):
        points=[turn(p) for p in ((b[0],b[1]),(b[0],b[3]),(b[2],b[1]),(b[2],b[3]))]
        return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
    kwargs=dict(via_width_nm=15,grid_step_nm=step,bounds_nm=bounds,inset_nm=1)
    original=candidates(obstacles,starts,**kwargs)
    rotated=candidates([dict(q,box_nm=box_turn(q['box_nm'])) for q in obstacles],[turn(s) for s in starts],**dict(kwargs,bounds_nm=box_turn(bounds)))
    # Lexicographic tie-breaking can choose another equally near representative;
    # the cardinally rotated non-original modulo-phase inventory is invariant.
    assert {phase(p,turn(starts[0]),step) for p in rotated}=={phase(turn(p),turn(starts[0]),step) for p in original}


@pytest.mark.parametrize('field,value',[('via_width_nm',0),('via_width_nm',True),('grid_step_nm',0),('inset_nm',-1)])
def test_invalid_dimensions_reject(field,value):
    kwargs=dict(via_width_nm=20,grid_step_nm=37,bounds_nm=[0,0,100,100],inset_nm=1);kwargs[field]=value
    with pytest.raises(ValueError):candidates([],[[0,0]],**kwargs)


def test_invalid_source_or_obstacle_inventory_rejects():
    kwargs=dict(via_width_nm=20,grid_step_nm=37,bounds_nm=[0,0,100,100])
    for obstacles,starts in [([],[]),([dict(box_nm=[5,0,1,2],clearance_nm=0)],[[0,0]]),([dict(box_nm=[0,0,1,2],clearance_nm=True)],[[0,0]]),([],[[1.5,0]])]:
        with pytest.raises(ValueError):candidates(obstacles,starts,**kwargs)


def test_admission_precedes_phase_dedup_so_farther_legal_representative_survives():
    obstacles=[dict(box_nm=[3,3,13,13],clearance_nm=0)];starts=[[0,0]]
    kwargs=dict(via_width_nm=2,grid_step_nm=10,bounds_nm=[0,0,20,20],inset_nm=0)
    initial=candidates(obstacles,starts,**kwargs)
    assert next(p for p in initial if phase(p,starts[0],10)==(2,0))==(2,0)
    seen=[]
    def admit(point):seen.append(point);return point!=(2,0)
    filtered=candidates(obstacles,starts,admissible=admit,**kwargs)
    assert next(p for p in filtered if phase(p,starts[0],10)==(2,0))==(12,0)
    assert (2,0) in seen and (12,0) in seen
    assert initial==candidates(obstacles,starts,admissible=None,**kwargs)


def test_admission_deadline_exception_propagates_without_discarding_failure():
    def expired(point):raise TimeoutError('caller phase deadline')
    with pytest.raises(TimeoutError,match='caller phase deadline'):
        candidates([dict(box_nm=[3,3,13,13],clearance_nm=0)],[[0,0]],via_width_nm=2,grid_step_nm=10,bounds_nm=[0,0,20,20],inset_nm=0,admissible=expired)


def test_lazy_iterator_admits_only_until_first_yield_then_resumes():
    module=importlib.import_module('smilepcb.keyboard_routing.controller_phase_geometry')
    assert hasattr(module,'iter_via_phase_candidates'),'lazy phase iterator is missing'
    calls=[]
    def admit(point):calls.append(point);return True
    kwargs=dict(via_width_nm=2,grid_step_nm=10,bounds_nm=[0,0,20,20],inset_nm=0)
    iterator=module.iter_via_phase_candidates([dict(box_nm=[3,3,13,13],clearance_nm=0)],[[0,0]],admissible=admit,**kwargs)
    assert calls==[]
    first=next(iterator)
    assert calls==[first]
    second=next(iterator)
    assert calls==[first,second] and phase(first,(0,0),10)!=phase(second,(0,0),10)
    assert [first,second,*iterator]==candidates([dict(box_nm=[3,3,13,13],clearance_nm=0)],[[0,0]],**kwargs)
