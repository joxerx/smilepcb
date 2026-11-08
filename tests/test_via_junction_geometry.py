import time
try:from smilepcb.keyboard_routing.via_junction_geometry import junction_sites,arm_options
except ImportError:junction_sites=arm_options=None
M=1000000
arms=[[(2*M,6*M),(4*M,6*M),(8*M,10*M),(8*M,12*M),(10*M,14*M),(12*M,14*M),(14*M,12*M)],[(22*M,12*M),(20*M,12*M),(18*M,10*M),(16*M,10*M),(14*M,12*M)]]

def test_intersects_real_anchor_lines_to_offer_a_straight_front_arm():
 assert junction_sites is not None
 sites=junction_sites(arms,(14*M,12*M),limit=256)
 assert (14*M,6*M) in sites
 assert len(sites)==len(set(sites)) and (14*M,12*M) not in sites

def test_derived_sites_follow_rotated_translated_geometry():
 assert junction_sites is not None
 transform=lambda q:(-q[1]+37*M,q[0]-11*M)
 sites=junction_sites([[transform(p) for p in a] for a in arms],transform((14*M,12*M)),limit=256)
 assert transform((14*M,6*M)) in sites

def test_replacement_arms_start_at_fixed_anchor_and_finish_at_new_via():
 assert arm_options is not None
 paths=arm_options(arms[0],(14*M,6*M))
 assert [(2*M,6*M),(14*M,6*M)] in paths
 assert all(p[0]==(2*M,6*M) and p[-1]==(14*M,6*M) for p in paths)

def test_deadline_stops_site_enumeration_before_any_work():
 assert junction_sites is not None
 assert junction_sites(arms,(14*M,12*M),deadline=time.monotonic()-1)==[]
