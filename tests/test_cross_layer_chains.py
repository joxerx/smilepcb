"""These regressions fail if a disposable via remains an optimization boundary."""
import importlib,importlib.util

def assemble(rows,vias):
    name='smilepcb.keyboard_routing.cross_layer_geometry'
    assert importlib.util.find_spec(name) is not None,'Missing cross-layer geometry unit'
    return importlib.import_module(name).assemble_chains(rows,vias)

def chain(uid,a,b,layer=0,width=200000):
    return dict(net='signal',width=width,layer=layer,path=[a,b],ids=[uid])

def via(uid,point):return dict(id=uid,net='signal',position=point,layers=[0,2])

def test_degree_two_via_does_not_freeze_a_detour():
    rows=[chain('a',(0,0),(0,2000000)),chain('b',(0,2000000),(1000000,4000000),2)]
    out=assemble(rows,[via('v',(0,2000000))]);assert len(out)==1
    assert set(out[0]['ids'])=={'a','b','v'}
    assert out[0]['path'][0]==(0,0) and out[0]['path'][-1]==(1000000,4000000)
    assert out[0]['start_layer']==0 and out[0]['end_layer']==2

def test_shuffle_is_not_a_geometric_preference():
    rows=[chain('b',(0,2000000),(1000000,4000000),2),chain('a',(0,0),(0,2000000))]
    assert assemble(rows,[via('v',(0,2000000))])==assemble(list(reversed(rows)),[via('v',(0,2000000))])

def test_branch_via_stays_a_terminal():
    rows=[chain('a',(0,0),(0,2000000)),chain('b',(0,2000000),(0,4000000),2),chain('c',(0,2000000),(2000000,4000000))]
    out=assemble(rows,[via('v',(0,2000000))]);assert len(out)==3
    assert all('v' not in q['ids'] for q in out)

def test_width_transition_is_not_silently_replaced():
    rows=[chain('a',(0,0),(0,2000000)),chain('b',(0,2000000),(0,4000000),2,300000)]
    assert len(assemble(rows,[via('v',(0,2000000))]))==2

def test_no_via_means_no_cross_layer_contact():
    rows=[chain('a',(0,0),(0,2000000)),chain('b',(0,2000000),(0,4000000),2)]
    assert len(assemble(rows,[]))==2

def test_closed_ring_is_not_a_replacement_chain():
    rows=[dict(chain('a',(0,0),(0,2000000)),path=[(0,0),(1000000,1000000),(0,2000000)]),dict(chain('b',(0,2000000),(0,0),2),path=[(0,2000000),(-1000000,1000000),(0,0)])]
    assert len(assemble(rows,[via('u',(0,0)),via('v',(0,2000000))]))==2
