import pytest
from smilepcb.keyboard_routing import field_hygiene as h

def track(a,b,**kw):
 return {'class':'PCB_TRACK','start':list(a),'end':list(b),'width':150000,'layer':0,'net':'signal','locked':False,**kw}

def test_owned_covered_fragment_is_removed_without_touching_source():
 tracks={'source':track((0,0),(10,0)),'inner':track((2,0),(6,0))}
 assert h.covered_ids(tracks,{'inner'})==['inner']
 assert set(tracks)=={'source','inner'}

def test_canonical_long_trunk_survives_its_own_short_duplicates():
 tracks={'long':track((0,0),(10,0)),'a':track((0,0),(4,0)),'b':track((4,0),(10,0))}
 assert set(h.covered_ids(tracks,set(tracks)))=={'a','b'}

@pytest.mark.parametrize('change',[{'net':'other'},{'layer':2},{'width':200000},{'locked':True}])
def test_different_copper_or_explicit_locks_are_preserved(change):
 tracks={'source':track((0,0),(10,0)),'inner':track((2,0),(6,0),**change)}
 assert h.covered_ids(tracks,{'inner'})==[]

def test_partly_exposed_track_is_never_deleted():
 tracks={'source':track((0,0),(10,0)),'exposed':track((8,0),(12,0))}
 assert h.covered_ids(tracks,{'exposed'})==[]
