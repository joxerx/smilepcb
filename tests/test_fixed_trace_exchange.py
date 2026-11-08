"""Round native fixed strokes keep full occupancy; via geometry is never reduced."""
import math
import pytest
from smilepcb.fixed_copper_exchange import fixed_trace_exchange,_read,_child

DSN='''(pcb board (unit um) (library (padstack v (shape (circle F.Cu 500)) (shape (circle B.Cu 500))))
(structure (via v)) (wiring
 (wire (path F.Cu 150 0 0 1000 0) (net a) (type fix))
 (via v 3000 2000 (net b) (type fix))
 (wire (path B.Cu 150 0 1000 1000 2000) (net c) (type route))))'''

def inside_convex(point,coords):
    points=list(zip(coords[::2],coords[1::2]));values=[]
    for a,b in zip(points,points[1:]+points[:1]):
        values.append((b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0]))
    return min(values)>=-1e-6 or max(values)<=1e-6

def test_fixed_trace_capsule_encloses_entire_round_copper_and_retains_other_objects():
    output,manifest=fixed_trace_exchange(DSN,indices={1});old=_read(DSN);new=_read(output)
    assert _child(new,'library')==_child(old,'library')
    assert _child(new,'structure')==_child(old,'structure')
    assert _child(new,'wiring')[-2:]==_child(old,'wiring')[-2:]
    capsule=_child(_child(new,'wiring')[1],'polygon');assert capsule is not None
    for center in ((0,0),(1000,0)):
        for i in range(720):
            angle=i*math.pi/360
            assert inside_convex((center[0]+75*math.cos(angle),center[1]+75*math.sin(angle)),capsule[3:])
    assert manifest['fixed_segments']==1 and manifest['fixed_vias']==0
    assert manifest['via_padstacks']=={} and manifest['fixed_via_instances']==[]

@pytest.mark.parametrize('indices',[{2},{3},{0},{4},{True}])
def test_trace_exchange_rejects_vias_editable_copper_and_invalid_selection(indices):
    with pytest.raises(ValueError):fixed_trace_exchange(DSN,indices=indices)
