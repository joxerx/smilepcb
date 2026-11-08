"""Foreign adjacency applies to all route families with real edge spacing."""
from smilepcb.routing_quality import quality_profile
from smilepcb.keyboard_routing.path_quality import ranked_bundle_paths
M=1000000
straight=[(0,2*M),(20*M,2*M)]
near=[(0,2*M),(1600000,400000),(18400000,400000),(20*M,2*M)]
far=[(0,2*M),(M,M),(19*M,M),(20*M,2*M)]

def rank(rows,net='query',paths=None):
 return list(ranked_bundle_paths(paths or [straight,far,near],rows,layer=0,net=net,width=200000,clearance_nm=200000,profile=quality_profile('turns')))

def line(net):return {'class':'PCB_TRACK','net':net,'layer':0,'width':200000,'start':(0,0),'end':(20*M,0)}

def test_all_foreign_families_can_be_guides_with_uniform_edge_gap():
 for net in ('column','row','RGB_DATA','+5V','GND','USB_DP','other'):
  assert rank({'guide':line(net)})[0]==near

def test_self_net_is_never_a_guide():
 assert rank({'guide':line('query')})[0]==straight

def test_duplicates_cannot_increase_the_reward_and_legacy_order_is_exact():
 one=rank({'a':line('other')})
 assert rank({'a':line('other'),'b':line('other')})==one
 assert list(ranked_bundle_paths([far,straight,near],{},layer=0,net='q',width=200000,clearance_nm=200000,profile=quality_profile('legacy')))==[far,straight,near]


def test_repeated_queries_reuse_the_same_physical_neighbor_index(monkeypatch):
 from smilepcb.keyboard_routing import path_quality as q
 original=q._runs;calls=[]
 def observe(*args):calls.append(1);return original(*args)
 monkeypatch.setattr(q,'_runs',observe)
 ranker=q.BundlePathRanker({'a':line('other')},clearance_nm=200000)
 for net in ('query','another query','query'):
  assert list(ranker.rank([straight,near],layer=0,net=net,width=200000,profile=quality_profile('turns')))[0]==near
 assert len(calls)==1
