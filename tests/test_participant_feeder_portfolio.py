"""A candidate scheduler must reuse rollback witnesses without stale ownership."""
from types import SimpleNamespace as S
import sys
from smilepcb.keyboard_routing import feeder_repair as repair


def scenario(monkeypatch,pending,handler,deadline=300):
 state=S(now=0.,pending=dict(pending),calls=[]);ownership=object()
 monkeypatch.setattr(repair,'time',S(monotonic=lambda:state.now))
 monkeypatch.setattr(repair,'pending_matrix',lambda *a:dict(state.pending))
 monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_launches',S(current_launches=lambda:[],interface_identity=lambda p:tuple(p['pad_ids'])))
 monkeypatch.setitem(sys.modules,'smilepcb.native_worker',S(snapshot=lambda b:{'tracks':{'incumbent':{'net':'closed'}}}))
 def inner(board,nets,protected,**kw):
  assert kw['participant_ownership'] is ownership
  targets=set(nets)&state.pending.keys();target=next(iter(targets)) if len(targets)==1 else 'group'
  state.calls.append((target,set(protected),kw['deadline']));state.now+=10
  return handler(state,target,protected)
 monkeypatch.setattr(repair,'_repair_feeders',inner)
 return state,lambda:repair.repair_feeders(None,set(pending)|{'closed'},set(),deadline=deadline,participant_ownership=ownership)


def test_participant_rejection_reuses_the_incumbent_blocker_witness(monkeypatch):
 def inner(s,target,protected):
  if 'incumbent' not in protected:
   return [dict(accepted=False,before={'first':1},after={'closed':1},retained_pending={'first':1},plan={'removed':['incumbent']})]
  s.pending.pop('first');return [dict(accepted=True,before={'first':1},after={})]
 state,run=scenario(monkeypatch,{'first':1},inner);rows=run()
 assert len(state.calls)==2 and state.calls[1][0]=='first' and state.calls[1][1]=={'incumbent'}
 assert state.pending=={} and rows[-1]['single_target']=='first'
 assert all(d<=300 for _,_,d in state.calls)


def test_an_accepted_participant_edit_ends_the_old_ownership_epoch(monkeypatch):
 def inner(s,target,protected):
  if target=='group':return [dict(accepted=False,before=dict(s.pending),after=dict(s.pending),retained_pending=dict(s.pending))]
  s.pending.pop(target);return [dict(accepted=True,before={target:1},after={})]
 state,run=scenario(monkeypatch,{'first':1,'second':1},inner);rows=run()
 assert [q[0] for q in state.calls]==['group','first']
 assert rows[-1]['after']=={'second':1} and rows[-1]['participant_epoch_ended'] is True


def test_no_retry_when_the_shared_proof_reserve_has_been_spent(monkeypatch):
 def inner(s,target,protected):
  s.now=294;return [dict(accepted=False,before=dict(s.pending),after=dict(s.pending),retained_pending=dict(s.pending))]
 state,run=scenario(monkeypatch,{'first':1,'second':1},inner);run()
 assert len(state.calls)==1


def test_single_target_without_a_changed_blocker_domain_is_not_repeated(monkeypatch):
 def inner(s,target,protected):
  return [dict(accepted=False,before={'first':1},after={'first':1},retained_pending={'first':1})]
 state,run=scenario(monkeypatch,{'first':1},inner);run()
 assert len(state.calls)==1
