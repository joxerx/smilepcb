import copy
import pytest
from smilepcb.keyboard_routing.seed_junctions import ambiguous_junction_ids

def track(a,b,net='renamed signal',layer=0):return {'class':'PCB_TRACK','start':a,'end':b,'net':net,'layer':layer}
def test_half_grid_crossing_keeps_both_source_segments():
 d={'a':track([-200,-200],[200,200]),'b':track([-200,100],[200,-300])}
 assert ambiguous_junction_ids(d,quantum_nm=100)=={'a','b'}
def test_rational_non_octile_crossing_is_not_rounded_away():
 d={'a':track([0,0],[500,300]),'b':track([0,200],[500,0])}
 assert ambiguous_junction_ids(d,quantum_nm=100)=={'a','b'}
def test_exact_grid_crossing_and_collinear_segments_remain_editable():
 d={'a':track([-200,-200],[200,200]),'b':track([-200,200],[200,-200]),'c':track([-100,-100],[100,100])}
 assert ambiguous_junction_ids(d,quantum_nm=100)==set()
def test_other_net_or_copper_face_is_not_an_electrical_junction():
 a=track([-200,-200],[200,200]);b=track([-200,100],[200,-300],layer=2);c=track([-200,100],[200,-300],net='another')
 assert ambiguous_junction_ids({'a':a,'b':b,'c':c},quantum_nm=100)==set()
def test_actual_endpoint_contacts_are_preserved_without_freezing_more_copper():
 d={'a':track([0,0],[200,0]),'b':track([100,0],[100,200])};before=copy.deepcopy(d)
 assert ambiguous_junction_ids(d,quantum_nm=100)==set();assert d==before
@pytest.mark.parametrize('value',[0,-1,True,1.5])
def test_quantum_requires_positive_native_integer_units(value):
 with pytest.raises(ValueError):ambiguous_junction_ids({},quantum_nm=value)


def _closure():
 from smilepcb.keyboard_routing import seed_junctions
 function=getattr(seed_junctions,'endpoint_protection_closure',None)
 assert callable(function),'Exact endpoint protection closure is required'
 return function


def straight(a,b,*,net='a renamed net',layer=0,width=150000):
 return {'class':'PCB_TRACK','start':list(a),'end':list(b),'net':net,'layer':layer,'width':width}


def test_mixed_lock_chain_protects_the_editable_member_without_moving_copper():
 # KiCad may coalesce this fixed/editable/fixed chain into fixed DSN paths.
 tracks={'first':straight((100,100),(300,400)),
         'middle':straight((300,400),(800,400)),
         'last':straight((800,400),(1200,400))}
 protected={'first','last'};before=copy.deepcopy(tracks);saved=protected.copy()
 assert _closure()(tracks,protected)=={'first','middle','last'}
 assert tracks==before and protected==saved


def test_endpoint_closure_is_transitive_with_reversed_segments_and_branches():
 tracks={'a':straight((0,0),(100,0)),
         'b':straight((200,0),(100,0)),
         'c':straight((200,0),(300,100)),
         'branch':straight((200,0),(200,-100)),
         'far':straight((900,0),(1000,0))}
 assert _closure()(tracks,{'a'})=={'a','b','c','branch'}
 assert _closure()(tracks,{'far'})=={'far'}
 assert _closure()(tracks,set())==set()


@pytest.mark.parametrize('changes',[{'net':'other net'},{'layer':2},{'width':200000}])
def test_endpoint_closure_does_not_cross_net_layer_or_width_boundaries(changes):
 tracks={'a':straight((0,0),(100,0)),
         'other':straight((100,0),(200,0),**changes),
         'behind':straight((200,0),(300,0),**changes)}
 assert _closure()(tracks,{'a'})=={'a'}


def test_crossings_t_contacts_and_collinear_interior_overlap_do_not_propagate():
 tracks={'a':straight((0,0),(400,0)),
         'cross':straight((200,-100),(200,100)),
         'tee':straight((100,0),(100,100)),
         'overlap':straight((200,0),(300,0)),
         'one_nm_gap':straight((401,0),(500,0))}
 assert _closure()(tracks,{'a'})=={'a'}


def test_protected_vias_and_other_native_objects_are_preserved_without_freezing_tracks():
 tracks={'via':dict(straight((100,0),(100,0),width=500000),**{'class':'PCB_VIA'}),
         'arc':dict(straight((0,0),(100,0)),**{'class':'PCB_ARC'}),
         'a':straight((100,0),(200,0)),'b':straight((200,0),(300,0))}
 assert _closure()(tracks,{'via','arc'})=={'via','arc'}
 assert _closure()(tracks,{'via','a'})=={'via','a','b'}


def test_native_point_tracks_do_not_propagate_protection():
 tracks={'dot':straight((100,0),(100,0)),'a':straight((100,0),(200,0))}
 assert _closure()(tracks,{'dot'})=={'dot'}


@pytest.mark.parametrize('bad',[None,[],{'bad':None},{'bad':{}},
 {'a':dict(straight((0,0),(100,0)),start=[True,0])},
 {'a':dict(straight((0,0),(100,0)),end=[100.0,0])},
 {'a':dict(straight((0,0),(100,0)),width=0)},
 {'a':dict(straight((0,0),(100,0)),width=True)},
 {'a':dict(straight((0,0),(100,0)),layer=[])},
 {'a':dict(straight((0,0),(100,0)),net=[])},
 {str(i):{} for i in range(10001)}])
def test_endpoint_closure_rejects_invalid_or_unbounded_native_inventory(bad):
 with pytest.raises(ValueError):_closure()(bad,set())


@pytest.mark.parametrize('protected',['a',None,{'missing'},[False]])
def test_endpoint_closure_requires_bounded_known_protected_ids(protected):
 with pytest.raises(ValueError):_closure()({'a':straight((0,0),(100,0))},protected)


@pytest.mark.parametrize('deadline',[True,'later',float('nan'),float('inf')])
def test_endpoint_closure_rejects_invalid_deadlines(deadline):
 with pytest.raises(ValueError):_closure()({},set(),deadline=deadline)


@pytest.mark.parametrize('expire_after',[1,8])
def test_endpoint_closure_checks_deadline_without_mutating_inputs(monkeypatch,expire_after):
 from smilepcb.keyboard_routing import seed_junctions
 calls=[0]
 def clock():
  calls[0]+=1
  return 0.0 if calls[0]<expire_after else 2.0
 monkeypatch.setattr(seed_junctions.time,'monotonic',clock)
 tracks={'a':straight((0,0),(100,0)),'b':straight((100,0),(200,0)),
         'c':straight((200,0),(300,0))}
 protected={'a'};before=copy.deepcopy(tracks)
 with pytest.raises(TimeoutError):_closure()(tracks,protected,deadline=1.0)
 assert tracks==before and protected=={'a'}
