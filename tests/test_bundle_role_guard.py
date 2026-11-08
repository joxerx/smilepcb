import importlib

def guard(row,path):
 m=importlib.import_module('smilepcb.keyboard_routing.bundle_geometry')
 assert hasattr(m,'preserves_clean_columns'),'Missing column geometry guard in joint candidate admission'
 return m.preserves_clean_columns(row,path,{'column'})

def test_joint_affinity_cannot_buy_a_horizontal_piece_in_a_clean_column():
 row={'net':'column','path':[(0,0),(0,8),(2,10)]}
 assert not guard(row,[(0,0),(0,6),(1,7),(2,7),(3,8),(3,9),(2,10)])
 assert guard(row,[(0,0),(2,2),(2,10)])

def test_horizontal_mcu_feeders_and_other_roles_are_not_mistaken_for_clean_columns():
 assert guard({'net':'column','path':[(0,0),(8,0),(10,2)]},[(0,0),(8,0),(10,2)])
 assert guard({'net':'row','path':[(0,0),(0,8),(2,10)]},[(0,0),(0,6),(1,7),(2,7),(3,8),(3,9),(2,10)])

def test_two_layer_bundle_candidates_may_cross_in_projection_but_never_self_intersect():
 from smilepcb.keyboard_routing.bundle_geometry import bundle_candidates,paths_clear
 m=1000000
 group=[dict(net='a',layer=0,width=150000,path=[(0,0),(4*m,4*m)]),dict(net='b',layer=0,width=150000,path=[(0,4*m),(4*m,0)])]
 try:proposals=bundle_candidates(group,clearance_nm=173000,allow_layer_bridges=True)
 except TypeError:proposals=[]
 assert proposals,'Projected crossing must reach physical two-layer admission'
 assert any(not paths_clear(q['paths'],[150000,150000],173000) for q in proposals)
 assert all(paths_clear([path],[150000],0) for q in proposals for path in q['paths'])

def test_bundle_body_can_move_without_discarding_pad_escape_stems():
 m=importlib.import_module('smilepcb.keyboard_routing.bundle_geometry')
 assert hasattr(m,'stemmed_chain'),'Missing physical pad escape preservation'
 path=[(0,0),(0,10),(10,20),(10,30)]
 left,body,right=m.stemmed_chain(path,3,4)
 assert left==[(0,0),(0,3)] and right==[(10,26),(10,30)]
 assert body==[(0,3),(0,10),(10,20),(10,26)]
 from smilepcb.keyboard_routing.follow_geometry import simplify
 assert simplify([*left,*body[1:],*right[1:]])==path
