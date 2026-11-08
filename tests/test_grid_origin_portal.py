import pytest
from test_grid_policy_completion import engine,Router,Angles
from smilepcb.routing_quality import using_quality_profile

def corridor(monkeypatch,origin):
    grid=engine(monkeypatch)
    line=[(0,0),(1370000,1370000),(1370000,10000000)]
    with using_quality_profile('turns'):
        r=Router([(a,b,0) for a,b in zip(line,line[1:])],Angles())
        if origin is not None:r.grid_origin_nm=origin
        ok=grid.search(r,line[0],line[-1],[0],[0],'signal')
        return ok,r

def test_obstacle_aligned_grid_can_enter_off_phase_narrow_corridor(monkeypatch):
    normal,_=corridor(monkeypatch,None)
    aligned,r= corridor(monkeypatch,(1370000,1370000))
    assert not normal
    assert aligned,r.last_search
    assert r.last_search['terminal_origin_nm']==[1370000,1370000]

@pytest.mark.parametrize('origin',[(),(1,),(1,2,3),(1.5,2),'bad'])
def test_invalid_origin_is_rejected(monkeypatch,origin):
    with pytest.raises(ValueError,match='origin'):
        corridor(monkeypatch,origin)
