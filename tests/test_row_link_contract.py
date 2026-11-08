"""Host ownership/direction checks; native geometry is proved separately."""
from copy import deepcopy
import json
import pytest
from smilepcb.keyboard_routing import field_contract as fc
from smilepcb.keyboard_routing.joint_repair import movable_ids


def fixture():
    def track(a,b,net='ROW_A'):
        return {'class':'PCB_TRACK','start':a,'end':b,'width':150000,
                'layer':2,'net':net,'locked':False}
    state={'tracks':{'trunk':track([0,0],[100,0]),
                     'link':track([100,0],[200,100]),
                     'access':track([200,100],[200,120]),
                     'source':track([-100,0],[-20,0],'control')},
           'footprints':{'diode':{'pads':[{'uuid':'pad','net':'ROW_A'}]}}}
    report={'complete':True,'roles':{'trunk':'row_trunk','link':'row_link','access':'row_access'},
            'owned_ids':['trunk','link','access'],'terminals':{'pad':['rows','ROW_A']}}
    return state,report


def test_explicit_link_survives_serialized_contract_round_trip():
    state,report=fixture()
    contract=json.loads(json.dumps(fc.capture(state,report,prerequisite_ids=['source'])))
    assert contract['schema']==1
    assert contract['roles']['link']=='row_link'
    assert set(fc.validate(deepcopy(state),contract))=={'trunk','link','access','source'}
    state['tracks']['unrelated']={**state['tracks']['source'],'net':'unrelated'}
    assert set(fc.validate(state,contract))=={'trunk','link','access','source'}


@pytest.mark.parametrize('delta',[(100,100),(100,-100),(-100,100),(0,100),(100,0)])
def test_link_allows_octile_segments_without_bending_the_trunk(delta):
    state,report=fixture();state['tracks']['link']['end']=[100+delta[0],delta[1]]
    contract=fc.capture(state,report)
    assert 'link' in fc.validate(state,contract)
    assert state['tracks']['trunk']['start'][1]==state['tracks']['trunk']['end'][1]


@pytest.mark.parametrize('end',[[200,70],[170,100]])
def test_nonoctile_link_is_rejected_even_with_matching_recorded_shape(end):
    state,report=fixture();contract=fc.capture(state,report)
    state['tracks']['link']['end']=end
    contract['tracks']['link']=deepcopy(state['tracks']['link'])
    with pytest.raises(ValueError):fc.validate(state,contract)


@pytest.mark.parametrize('end',[[100,100],[0,100]])
def test_trunk_direction_stays_horizontal(end):
    state,report=fixture()
    # Use a legacy row-only report so this guard independently tests trunk policy.
    report['roles'].pop('link');report['owned_ids'].remove('link')
    state['tracks']['trunk']['end']=end
    with pytest.raises(ValueError):fc.capture(state,report)


@pytest.mark.parametrize('change',['delete','endpoint','width','layer','net','role'])
def test_saved_link_is_immutable(change):
    state,report=fixture();contract=fc.capture(state,report)
    if change=='delete':del state['tracks']['link']
    elif change=='endpoint':state['tracks']['link']['end']=[210,110]
    elif change=='width':state['tracks']['link']['width']=200000
    elif change=='layer':state['tracks']['link']['layer']=0
    elif change=='net':state['tracks']['link']['net']='ROW_B'
    else:contract['roles']['link']='unknown'
    with pytest.raises(ValueError):fc.validate(state,contract)


def test_joint_repair_cannot_move_link_even_with_generated_provenance():
    state,report=fixture()
    state['tracks']['generated']={**deepcopy(state['tracks']['access']),'net':'free'}
    contract={'roles':report['roles'],'prerequisite_tracks':{'source':state['tracks']['source']}}
    movable=movable_ids(state,contract,set(state['tracks']),protected=set(),target_net='target')
    assert movable=={'access','generated'}
    assert 'link' not in movable
