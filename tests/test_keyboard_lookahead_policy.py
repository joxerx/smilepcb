"""Disposable search may exchange gaps but cannot cycle, regress or finalize."""
import importlib.util
import pytest

def policy():
    assert importlib.util.find_spec('smilepcb.keyboard_lookahead'), 'Bounded lookahead policy is required'
    from smilepcb import keyboard_lookahead
    return keyboard_lookahead

def test_two_hop_exchange_can_finish_without_calling_a_provisional_state_final():
    p=policy();first=p.provisional_step({'left':1},{'right':1},seen={(('left',1),)},depth=0)
    assert first=={'allowed':True,'complete':False,'protect_healed':{'left'}}
    second=p.provisional_step({'right':1},{},seen={(('left',1),),(('right',1),)},depth=1)
    assert second=={'allowed':True,'complete':True,'protect_healed':{'right'}}

@pytest.mark.parametrize('before,after,seen,depth',[
    ({'left':1},{'left':1},set(),0),({'left':1},{'right':2},set(),0),
    ({'left':1},{'right':1},{(('right',1),)},0),({'left':1},{},set(),3),
    ({'left':1},{},set(),-1),
])
def test_cycles_regressions_unchanged_boards_and_fourth_hops_are_rejected(before,after,seen,depth):
    assert policy().provisional_step(before,after,seen=seen,depth=depth)=={'allowed':False,'complete':False,'protect_healed':set()}

@pytest.mark.parametrize('after',[{'n':True},{'n':-1},{'n':0},{'n':1.5}])
def test_malformed_native_gap_scores_fail_closed(after):
    with pytest.raises(ValueError):policy().provisional_step({'left':1},after,seen=set(),depth=0)
