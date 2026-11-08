"""Local displacement identifies only actual blockers and preserves their surroundings."""
import pytest
from smilepcb.keyboard_routing.feeder_repair import displaced_ids

def trace(net='a',locked=False):return {'net':net,'locked':locked,'class':'PCB_TRACK'}

def test_identifies_only_removed_blockers_without_retiring_clear_feeder_sections():
    before={k:trace(n) for k,n in [('source','a'),('field','a'),('cut','a'),('rest','a'),('other','b')]}
    live={k:v for k,v in before.items() if k!='cut'}
    live.update(fragment=trace(),target=trace('c'),fixed=trace('a',True))
    assert displaced_ids(before,live,removed=['cut'],protected={'source','field'},nets={'a','b','c'})==['cut']

@pytest.mark.parametrize('removed,protected,nets', [(['missing'],{'source'},{'a'}),(['source'],{'source'},{'a'}),(['cut'],{'source'},{'b'})])
def test_rejects_unproven_or_immutable_retirement(removed,protected,nets):
    before={'source':trace(),'cut':trace()};live={'source':trace()}
    with pytest.raises(ValueError):displaced_ids(before,live,removed=removed,protected=protected,nets=nets)

def test_no_displacement_does_not_retire_unrelated_open_feeders():
    assert displaced_ids({'a':trace()},{'a':trace()},removed=[],protected=set(),nets={'a'})==[]


@pytest.mark.parametrize('change',['lost','changed','still-present','locked'])
def test_rejects_displacement_receipt_that_loses_or_changes_unrelated_copper(change):
    before={'source':trace(),'cut':trace(),'rest':trace()};live={'source':trace(),'rest':trace()}
    if change=='lost':live.pop('rest')
    elif change=='changed':live['rest']=trace('other')
    elif change=='still-present':live['cut']=trace()
    else:before['cut']['locked']=True
    with pytest.raises(ValueError):displaced_ids(before,live,removed=['cut'],protected={'source'},nets={'a'})
