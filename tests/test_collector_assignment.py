"""Collector-relative source binding, independent of signal spelling/pin IDs."""
import pytest
from smilepcb.keyboard_routing import bank_geometry


def inputs(mirror=1):
    ports=[]
    for name,x in [('near',0),('middle',500000),('far',1000000)]:
        ports.append({'net':name,'bank':['chip',1,1],'exit_layer':31,'exit_nm':[mirror*x,10000000],'heading_origin_nm':[mirror*x,9000000]})
    targets={'near':(mirror*4000000,0),'middle':(mirror*14000000,0),'far':(mirror*24000000,0)}
    return ports,targets,{n:'columns' for n in targets}


@pytest.mark.parametrize('mirror',[1,-1])
def test_nearest_column_is_bound_to_the_exposed_lane_after_a_turn(mirror):
    from smilepcb.keyboard_routing.collector_assignment import collector_assignment
    ports,targets,families=inputs(mirror)
    mapping,plans=collector_assignment(ports,targets,families)
    assert mapping=={'far':'near','middle':'middle','near':'far'}
    assert plans[0]['collector_heading']==[mirror,0]
    assert plans[0]['signal_peel_order']==['near','middle','far']


def test_relative_assignment_preserves_matrix_roles():
    from smilepcb.keyboard_routing.collector_assignment import collector_assignment
    ports,targets,families=inputs();families['middle']='rows'
    mapping,plans=collector_assignment(ports,targets,families)
    assert mapping['middle']=='middle'
    assert all(families[n]==families[v] for n,v in mapping.items())


def test_relative_assignment_does_not_depend_on_alphabetical_signal_names():
    from smilepcb.keyboard_routing.collector_assignment import collector_assignment
    ports,targets,families=inputs();rename={'near':'zeta','middle':'alpha','far':'beta'}
    for q in ports:q['net']=rename[q['net']]
    targets={rename[n]:xy for n,xy in targets.items()};families={rename[n]:r for n,r in families.items()}
    mapping,_=collector_assignment(ports,targets,families)
    assert mapping=={'beta':'zeta','alpha':'alpha','zeta':'beta'}


def test_targets_on_opposite_sides_form_separate_ordered_collectors():
    from smilepcb.keyboard_routing.collector_assignment import collector_assignment
    ports,targets,families=inputs();targets['near']=(-4000000,0)
    mapping,plans=collector_assignment(ports,targets,families)
    assert mapping=={'near':'near','far':'middle','middle':'far'}
    directions={tuple(p['collector_heading']):p['signal_peel_order'] for p in plans}
    assert directions=={(-1,0):['near'],(1,0):['middle','far']}
