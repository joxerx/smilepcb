try:from smilepcb.keyboard_routing.column_bridges import bridge_carrier
except ImportError:bridge_carrier=None
M=1000000

def test_crossing_uses_two_vias_and_returns_to_the_preferred_face():
 assert bridge_carrier is not None
 clear=lambda a,b,l:l==1 or max(a[1],b[1])<4*M or min(a[1],b[1])>6*M
 r=bridge_carrier([(0,0),(0,10*M)],0,1,clear=clear,via_clear=lambda p:True,via_pair_clear=lambda a,b:abs(a[1]-b[1])>=M)
 assert r is not None and len(r['vias'])==2
 assert r['legs'][0][0]==r['legs'][-1][0]==0
 assert all(a[0]==b[0] for _,q in r['legs'] for a,b in zip(q,q[1:]))
 assert any(face==1 for face,path in r['legs'])

def test_clear_carrier_does_not_add_vias():
 assert bridge_carrier is not None
 r=bridge_carrier([(0,0),(0,3*M),(3*M,6*M)],0,1,clear=lambda a,b,l:True,via_clear=lambda p:True,via_pair_clear=lambda a,b:True)
 assert r['vias']==[] and r['legs']==[(0,[(0,0),(0,3*M),(3*M,6*M)])]

def test_no_solution_preserves_absence_instead_of_ignoring_a_blocker():
 assert bridge_carrier is not None
 assert bridge_carrier([(0,0),(0,4*M)],0,1,clear=lambda a,b,l:False,via_clear=lambda p:False,via_pair_clear=lambda a,b:True) is None

def test_clear_long_carrier_does_not_scan_unneeded_via_sites():
 assert bridge_carrier is not None
 def unnecessary(p):raise AssertionError('No via search needed on a fully clear carrier')
 r=bridge_carrier([(0,0),(0,20*M)],0,1,clear=lambda a,b,l:True,via_clear=unnecessary,via_pair_clear=lambda a,b:True)
 assert r['vias']==[]
