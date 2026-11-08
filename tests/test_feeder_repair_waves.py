"""Mock-clock ordered repair scheduling; native admission remains external."""
from copy import deepcopy
from types import SimpleNamespace as NS
import sys
import pytest
from smilepcb.keyboard_routing import feeder_repair as repair


def setup(monkeypatch,handler,*,pending=None,deadline=63.):
    state=NS(now=0.,pending=dict(pending or {'first':1,'second':1}),calls=[],reads=[])
    monkeypatch.setattr(repair,'time',NS(monotonic=lambda:state.now))
    monkeypatch.setattr(repair,'pending_matrix',lambda *a:state.reads.append(state.now) or dict(state.pending))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_launches',NS(current_launches=lambda:[],interface_identity=lambda p:tuple(p['pad_ids'])))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(snapshot=lambda b:{'tracks':{'incumbent':{'net':'closed'}}}))
    def trial(board,nets,protected,**kw):
        current=set(nets)&state.pending.keys()
        target=next(iter(current)) if len(current)==1 else 'group'
        state.calls.append(dict(target=target,nets=set(nets),protected=set(protected),deadline=kw['deadline'],started=state.now))
        return handler(state,target,protected)
    monkeypatch.setattr(repair,'_repair_feeders',trial)
    return state,lambda **kw:repair.repair_feeders(None,{'first','second','closed'},set(),deadline=deadline,**kw)


def test_first_wave_preserves_incumbent_retry_budget_then_serves_next_target(monkeypatch):
    def handler(s,target,protected):
        s.now+=5
        if target=='first' and 'incumbent' not in protected:
            return [dict(accepted=False,before={'first':1},after={'closed':1},retained_pending={'first':1},plan={'removed':['incumbent']})]
        s.pending.pop(target)
        return [dict(accepted=True,before={target:1},after={})]
    s,run=setup(monkeypatch,handler);history=run(target_order=['first','second'])
    assert [c['target'] for c in s.calls]==['first','first','second']
    # Original one-target first wave owns the whole63s end: unchanged24/29 cutoffs.
    assert [c['deadline'] for c in s.calls]==[24,29,34]
    assert s.calls[1]['protected']=={'incumbent'}
    assert [r['single_target'] for r in history]==['first','first','second']
    assert s.pending=={} and all(c['deadline']<=63 for c in s.calls)


@pytest.mark.parametrize('spent',[51,55])
def test_no_next_wave_when_only_proof_reserve_remains(monkeypatch,spent):
    def handler(s,target,protected):
        s.now=spent;s.pending.pop(target)
        return [dict(accepted=True,before={target:1},after={})]
    s,run=setup(monkeypatch,handler);run(target_order=['first','second'])
    assert [c['target'] for c in s.calls]==['first'] and s.reads==[0.]


def test_failed_targets_are_not_repeated_and_only_explicit_targets_are_eligible(monkeypatch):
    def handler(s,target,protected):
        s.now+=1
        return [dict(accepted=False,before={target:1},after={target:1})]
    s,run=setup(monkeypatch,handler,pending={'first':1,'second':1,'foreign':1})
    run(target_order=['first','first','second'])
    assert [c['target'] for c in s.calls]==['first','second']


def test_actual_closed_target_is_skipped_within_an_existing_wave(monkeypatch):
    def handler(s,target,protected):
        s.now+=5;s.pending.pop(target)
        if target=='first':s.pending.pop('second')
        return [dict(accepted=True,before={target:1},after={})]
    s,run=setup(monkeypatch,handler,deadline=96.)
    run(target_order=['first','second'])
    assert [c['target'] for c in s.calls]==['first']


def test_unordered_legacy_path_never_enters_dynamic_waves(monkeypatch):
    def handler(s,target,protected):
        return [dict(accepted=False,before={'first':1,'second':1},after={'first':1,'second':1},retained_pending={'first':1,'second':1})]
    s,run=setup(monkeypatch,handler);run()
    assert not s.reads and len(s.calls)==3


def test_next_target_does_not_spend_proof_reserve_on_a_graph_refresh(monkeypatch):
    def handler(s,target,protected):
        s.now=84;s.pending.pop(target)
        return [dict(accepted=True,before={target:1},after={})]
    s,run=setup(monkeypatch,handler,deadline=96.)
    run(target_order=['first','second'])
    assert len(s.calls)==1 and s.reads==[0.]
