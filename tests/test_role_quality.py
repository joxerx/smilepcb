"""Local simplification must not buy a loss of complete-area bus quality."""
import importlib,importlib.util

def api():
 name='smilepcb.keyboard_routing.role_quality';assert importlib.util.find_spec(name) is not None,'Missing whole-area role quality gate';return importlib.import_module(name)
M=1000000
ROLES={'columns':{'a','b'},'rows':{'c','d'}}
def segment(net,a,z):return dict(net=net,layer=0,width=150000,**{'class':'PCB_TRACK'},start=a,end=z)
def traces():return {'a':segment('a',(0,0),(10*M,0)),'b':segment('b',(0,400000),(10*M,400000))}
def test_foreign_parallel_coverage_is_measured_independently_of_segment_splitting():
 m=api();a=traces();b=dict(a);b.pop('a');b.update(a0=segment('a',(0,0),(5*M,0)),a1=segment('a',(5*M,0),(10*M,0)))
 assert m.role_coverage(a,ROLES,(-M,-M,11*M,2*M))==m.role_coverage(b,ROLES,(-M,-M,11*M,2*M))
 assert abs(m.role_coverage(a,ROLES,(-M,-M,11*M,2*M))['columns']-17400000)<1

def test_simpler_or_shorter_local_copper_cannot_degroup_the_other_line():
 m=api();a=traces();b=dict(a,b=segment('b',(5*M,400000),(10*M,400000)))
 gate=m.quality_gate(ROLES,(-M,-M,11*M,2*M));assert not gate(a,b);assert gate(a,a)

def test_parallel_copper_on_the_same_net_or_in_a_different_role_is_not_a_bus_reward():
 m=api();a=traces();a['b']['net']='a';assert m.role_coverage(a,ROLES,(-M,-M,11*M,2*M))['columns']==0
 a['b']['net']='c';assert m.role_coverage(a,ROLES,(-M,-M,11*M,2*M))['columns']==0

def test_whole_area_gate_keeps_gain_that_was_not_parallel_before():
 m=api();a=traces();a['b']=segment('b',(5*M,400000),(10*M,400000));assert m.quality_gate(ROLES,(-M,-M,11*M,2*M))(a,traces())


def test_whole_board_cohesion_includes_rgb_power_and_matrix_neighbors():
 m=api();rows={'r':segment('ROW_A',(0,0),(10*M,0)),'rgb':segment('LED_LINK',(0,350000),(10*M,350000)),'v':segment('+V',(0,775000),(10*M,775000))}
 rows['v']['width']=300000
 roles={'matrix':{'ROW_A'},'rgb':{'LED_LINK'},'power':{'+V'}}
 score=m.whole_board_coverage(rows,roles,(-M,-M,11*M,2*M),preferred_edge_gap_nm=200000)
 assert all(abs(score[r]-10*M)<1 for r in roles)


def test_whole_board_cohesion_never_rewards_same_net_or_opposite_layer():
 m=api();rows=traces();rows['b']['net']='a'
 assert m.whole_board_coverage(rows,{'any':{'a'}},(-M,-M,11*M,2*M))=={'any':0}
 rows['b']['net']='b';rows['b']['layer']=2
 assert m.whole_board_coverage(rows,{'any':{'a','b'}},(-M,-M,11*M,2*M))=={'any':0}


def test_whole_board_cohesion_uses_real_foreign_copper_outside_reported_family():
 m=api();rows=traces()
 assert m.whole_board_coverage(rows,{'report_a':{'a'}},(-M,-M,11*M,2*M))['report_a']>0


def test_whole_board_cohesion_is_invariant_to_splits_and_exact_duplicates():
 m=api();rows=traces();roles={'all':{'a','b'}};box=(-M,-M,11*M,2*M)
 expected=m.whole_board_coverage(rows,roles,box)
 rows['duplicate']=dict(rows['a']);rows['a']['end']=(5*M,0);rows['tail']=segment('a',(5*M,0),(10*M,0))
 assert m.whole_board_coverage(rows,roles,box)==expected


def test_board_summary_reports_all_copper_without_double_counting_family_overlap():
 m=api();rows=traces();roles={'matrix':{'a'},'controls':{'a','b'}}
 result=m.cohesion_summary(rows,roles,(-M,-M,11*M,2*M),preferred_edge_gap_nm=250000)
 assert result['scope']=='whole-board foreign-net adjacency'
 assert result['canonical_length_nm']==20*M
 assert abs(result['weighted_coverage_nm']-20*M)<1
 assert result['weighted_fraction']==1
 assert result['per_role_nm']=={'matrix':10*M,'controls':20*M}


def test_empty_board_has_neutral_cohesion_summary():
 m=api();result=m.cohesion_summary({}, {'rgb':set()},(0,0,M,M))
 assert result['weighted_fraction']==0 and result['canonical_length_nm']==0
