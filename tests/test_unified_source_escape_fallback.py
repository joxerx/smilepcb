"""Actual unified issuer still certifies every fallback; routing is a host boundary."""
from copy import deepcopy
from types import SimpleNamespace as NS
import sys
import pytest
from smilepcb.keyboard_routing import controller_plan as cp
from test_controller_plan import scene


def attempt(monkeypatch,*,stock_accepted=False,elapsed=3,profile='dsn-process-v2',bad_delta=False):
    board,cfg,before,valid=scene(monkeypatch);board.state=deepcopy(before);cfg['plane_nets']=['GND']
    board.GetDesignSettings=lambda:NS(m_MinClearance=150000)
    clock=NS(now=1000.);clock.monotonic=lambda:clock.now;monkeypatch.setattr(cp,'time',clock)
    events=[];seen={};receipt={}
    def emit():
        for net,uid in [('ROW','s1'),('CTRL','s2')]:board.state['tracks'][uid]=dict(net=net,locked=False,**{'class':'PCB_TRACK'},start=[0,0],end=[1,0])
    def stock(candidate,**kw):
        events.append('stock');seen['stock']=kw;clock.now+=elapsed
        if stock_accepted:emit();return deepcopy(valid)
        return dict(accepted=False,reason='generic finite stock template rejection',ports=[],added_ids=[])
    def fallback(candidate,**kw):
        events.append('fallback');seen['fallback']=kw;emit();result=deepcopy(valid)
        if bad_delta:result['added_ids'].pop()
        return result
    def close(*a,**kw):events.append('local_proof');raise ValueError('controlled downstream proof rejection')
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_ports',NS(route_controller_ports=stock))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_escape',NS(route_controller_escapes=fallback))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_contract',NS(mcu_signal_nets=lambda _:{'ROW','CTRL'}))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.generation',NS(current_generation_profile=lambda:NS(name=profile)))
    monkeypatch.setattr(cp,'close_controller_obligations',close)
    with pytest.raises(ValueError):
        cp.prepare_unified_sources(board,cfg,controller_refs={'U_MCU','R_BOOT'},matrix_nets={'ROW'},local_closed=None,deadline=1200,
            coherent_via_banks=True,exit_bounds_nm=(1,2,3,4),receipt=receipt)
    return events,seen,receipt,board,before


def test_stock_rejection_uses_one_existing_escape_fallback_under_same_source_clock(monkeypatch):
    events,seen,receipt,_,_=attempt(monkeypatch)
    assert events==['stock','fallback','local_proof']
    assert seen['stock']['deadline']==1035 and seen['fallback']['deadline']==1180
    assert seen['fallback']['include_nets']=={'ROW','CTRL'} and seen['fallback']['controller_refs']=={'U_MCU','R_BOOT'}
    assert seen['fallback']['exit_bounds_nm']=={'U_MCU':(1,2,3,4)} and seen['fallback']['clearance_nm']>=173000
    assert receipt['stock_escapes']['accepted'] is False
    assert receipt['source']['coherent_via_banks']['complete_coherence'] is False
    assert receipt['source_batch_sha256'] and not receipt['accepted']


def test_accepted_stock_does_not_call_fallback_or_skip_required_local_proof(monkeypatch):
    events,_,receipt,_,_=attempt(monkeypatch,stock_accepted=True)
    assert events==['stock','local_proof'] and 'stock_escapes' not in receipt and not receipt['accepted']


@pytest.mark.parametrize('elapsed',[175,180])
def test_exhausted_or_tiny_source_tail_cannot_replenish_fallback_budget(monkeypatch,elapsed):
    events,seen,receipt,board,before=attempt(monkeypatch,elapsed=elapsed)
    assert events==['stock'] and 'fallback' not in seen and board.state==before and not receipt['accepted']


def test_legacy_profile_keeps_existing_stock_only_behavior(monkeypatch):
    events,_,receipt,board,before=attempt(monkeypatch,profile='native')
    assert events==['stock'] and not receipt['accepted'] and board.state==before


def test_fallback_cannot_bypass_actual_fresh_delta_batch_issuer(monkeypatch):
    events,_,receipt,_,_=attempt(monkeypatch,bad_delta=True)
    assert events==['stock','fallback'] and not receipt['accepted']
    assert 'ownership' in receipt['reason'] and 'source_batch_sha256' not in receipt


@pytest.mark.parametrize('elapsed',[30,35])
def test_stock_cap_does_not_discard_remaining_caller_allowance(monkeypatch,elapsed):
    events,seen,receipt,_,_=attempt(monkeypatch,elapsed=elapsed)
    assert events==['stock','fallback','local_proof']
    assert seen['stock']['deadline']==1035 and seen['fallback']['deadline']==1180
    assert not receipt['accepted']  # The required downstream proof still fails.
