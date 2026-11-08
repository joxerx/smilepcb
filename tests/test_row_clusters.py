from types import SimpleNamespace
import pytest

class Pad:
    def __init__(self,x,y):self.position=SimpleNamespace(x=x,y=y)
    def GetPosition(self):return self.position

@pytest.mark.parametrize('points,sizes',[
    ([(0,0),(3,0),(13,12),(16,12)],[2,2]),
    ([(0,0),(3,0),(6,4),(9,4)],[2,2]),
    ([(0,0),(3,0),(6,0)],[3]),
    ([(1,2),(1,2)],[2]),
    ([(1,2)],[1]),
    ([],[]),
])
def test_physical_clusters_use_real_gaps_and_stagger(points,sizes):
    from smilepcb.keyboard_routing.row_clusters import physical_clusters
    pads=[Pad(*q) for q in points]
    result=physical_clusters(list(reversed(pads)))
    assert [len(group) for group in result]==sizes
    assert [(q.position.x,q.position.y) for group in result for q in group]==sorted(points)
