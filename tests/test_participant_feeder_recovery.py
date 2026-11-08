"""Real recovery coordinator over endpoint connectivity; Native geometry separate."""
from copy import deepcopy
from types import SimpleNamespace as NS
import sys
import pytest
from test_feeder_witness_lifecycle import Fixture,Pad,frame,partitions,endpoint_graph
from smilepcb.keyboard_routing import feeder_repair as repair


class MixedFixture(Fixture):
    def __init__(self,monkeypatch,*,fail_return=False,old_orphan=False):
        super().__init__(monkeypatch)
        self.return_net='supply-return';self.recovery_calls=[];self.owner_token=object();self.mutable=set()
        y=100000000
        for uid,point in [('return-A0',(0,y)),('return-A1',(3000000,y)),('return-B0',(0,y+10000000)),('return-B1',(3000000,y+10000000))]:self.item(uid,self.return_net,point,point,Pad)
        self.item('return-unknown',self.return_net,(0,y+10000000),(3000000,y+10000000));self.protected.add('return-unknown')
        if old_orphan:
            self.item('return-cut',self.return_net,(0,y),(1000000,y));self.item('return-cut-2',self.return_net,(2000000,y),(3000000,y))
            self.item('return-middle',self.return_net,(1000000,y),(2000000,y));self.item('return-branch',self.return_net,(1000000,y),(1000000,y+250000))
            self.mutable.update(('return-cut','return-cut-2','return-middle','return-branch'))
        else:
            self.item('return-cut',self.return_net,(0,y),(3000000,y));self.mutable.add('return-cut')
        self.before=frame(self.board);self.before_parts=partitions(self.board,100)
        def ownership(state,token,nets):
            if token is not self.owner_token:raise ValueError('Not checked in-process ownership')
            if any(state['tracks'].get(u)!=self.before['tracks'][u] for u in self.mutable):raise ValueError('Stale actual participant geometry')
            assert self.return_net not in nets
            return set(self.mutable)
        monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.collector_preparation',NS(transient_access_ids=ownership))
        router=sys.modules['smilepcb.keyboard_routing.matrix'].KeyboardRouter
        monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',NS(LocalRouter=router))
        native_graph=sys.modules['smilepcb.keyboard_routing.native_graph'];old_negotiate=self.negotiate;old_route=self.route
        def negotiate(board,missing,protected,deadline,**kw):
            assert missing=={'target'} and self.return_net not in missing
            assert 'return-cut' not in protected and 'return-unknown' in protected
            plan=old_negotiate(board,missing,protected,deadline,**kw)
            removed={'return-cut','return-cut-2'} if old_orphan else {'return-cut'}
            for uid in removed:board.Delete(board.items[uid])
            plan['removed']+=sorted(removed);return plan
        def route(board,router,nets,**kw):
            if self.return_net not in nets:return old_route(board,router,nets,**kw)
            self.recovery_calls.append((set(nets),set(kw.get('required_pad_ids',()))))
            assert nets=={self.return_net} and set(kw['required_pad_ids'])=={'return-A0','return-A1'}
            if fail_return=='exception':raise RuntimeError('Injected strict participant failure')
            if fail_return=='timeout':raise TimeoutError('Injected shared recovery deadline')
            if fail_return:return 0
            self.item('return-restored-1',self.return_net,(0,y),(0,y-1000000));self.item('return-restored-2',self.return_net,(0,y-1000000),(3000000,y-1000000));self.item('return-restored-3',self.return_net,(3000000,y-1000000),(3000000,y))
            return 1
        monkeypatch.setattr(native_graph,'negotiate_routes',negotiate);monkeypatch.setattr(native_graph,'route_components',route)


def test_mixed_displacement_recovers_each_old_partition_without_targeting_other_return_islands(monkeypatch):
    f=MixedFixture(monkeypatch);history=f.run(participant_ownership=f.owner_token)
    assert history[0]['accepted'] and history[0]['after']=={}
    assert f.recovery_calls==[({'supply-return'},{'return-A0','return-A1'})]
    assert all(any(old<=new for new in partitions(f.board,100)) for old in f.before_parts)
    assert {'return-A0','return-A1'} in partitions(f.board,100) and {'return-B0','return-B1'} in partitions(f.board,100)
    assert frame(f.board)['tracks']['return-unknown']==f.before['tracks']['return-unknown']
    assert f.scope_after['ports']==f.scope_before['ports'] and f.scope_after['repair_reserved']==f.scope_before['repair_reserved']


@pytest.mark.parametrize('failure',[True,'timeout','exception'])
def test_unrestored_return_rejects_target_success_and_rolls_back_every_object(monkeypatch,failure):
    f=MixedFixture(monkeypatch,fail_return=failure)
    if failure=='exception':
        with pytest.raises(RuntimeError,match='strict participant'):f.run(participant_ownership=f.owner_token)
    else:
        history=f.run(participant_ownership=f.owner_token);assert history[0]['accepted'] is False
    assert frame(f.board)==f.before
    assert partitions(f.board,100)==f.before_parts and f.scope_after==f.scope_before


def test_old_owned_whole_orphan_is_retired_without_removing_unknown_same_net_copper(monkeypatch):
    f=MixedFixture(monkeypatch,old_orphan=True);history=f.run(participant_ownership=f.owner_token)
    assert history[0]['accepted']
    assert {'return-middle','return-branch'}<=set(history[0]['retired_feeder_ids'])
    assert 'return-middle' not in f.board.items and 'return-branch' not in f.board.items
    assert 'return-unknown' in f.board.items and all(any(old<=new for new in partitions(f.board,100)) for old in f.before_parts)


def test_invalid_participant_handle_fails_before_negotiation_or_mutation(monkeypatch):
    f=MixedFixture(monkeypatch)
    with pytest.raises(ValueError):f.run(participant_ownership=object())
    assert not f.calls and frame(f.board)==f.before and f.scope_after==f.scope_before


@pytest.mark.parametrize('conflict',[False,True])
def test_reopened_guards_replan_only_witnesses_that_conflict_with_actual_new_context(monkeypatch,conflict):
    from test_feeder_witness_lifecycle import Track
    f=Fixture(monkeypatch,guarded=True);token=object()
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.collector_preparation',NS(transient_access_ids=lambda state,owner,nets:set()))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',NS(LocalRouter=sys.modules['smilepcb.keyboard_routing.matrix'].KeyboardRouter))
    native=sys.modules['smilepcb.native_worker'];native.p.F_Cu=0;native.p.B_Cu=2
    monkeypatch.setattr(Track,'GetEffectiveShape',lambda t,layer:NS(uid=t.uid))
    monkeypatch.setattr(Track,'GetNetname',lambda t:t.data['net'],raising=False)
    policy=sys.modules['smilepcb.keyboard_routing.port_policy'].PortPolicy
    monkeypatch.setattr(policy,'_launch_conflict',lambda p,shape,layer,net:conflict and shape.uid=='witness-1' and (f.owner+'-pad',) not in p.resolved,raising=False)
    rows=f.run(participant_ownership=token)
    assert rows[0]['accepted'] and len(f.calls)==(2 if conflict else 1)
    assert ('witness-1' in f.board.deleted)==conflict
    assert f.scope_after['repair_reserved']==f.scope_before['repair_reserved']


def test_new_owned_padless_component_is_retired_after_all_old_partitions_recover(monkeypatch):
    f=MixedFixture(monkeypatch);graph=sys.modules['smilepcb.keyboard_routing.native_graph'];route=graph.route_components
    def with_ring(board,router,nets,**kw):
        count=route(board,router,nets,**kw)
        if f.return_net in nets and count:
            f.item('new-ring-a',f.return_net,(10000000,150000000),(11000000,150000000));f.item('new-ring-b',f.return_net,(11000000,150000000),(10000000,150000000));router.added=['new-ring-a','new-ring-b']
        return count
    monkeypatch.setattr(graph,'route_components',with_ring)
    rows=f.run(participant_ownership=f.owner_token)
    assert rows[0]['accepted'] and {'new-ring-a','new-ring-b'}<=set(rows[0]['retired_feeder_ids'])
    assert not {'new-ring-a','new-ring-b'}&f.board.items.keys()


def test_new_orphan_touching_unknown_copper_is_rejected_instead_of_deleting_unknown(monkeypatch):
    f=MixedFixture(monkeypatch);f.item('unknown-floating',f.return_net,(10000000,150000000),(11000000,150000000));f.protected.add('unknown-floating');f.before=frame(f.board);f.before_parts=partitions(f.board,100)
    graph=sys.modules['smilepcb.keyboard_routing.native_graph'];route=graph.route_components
    def with_ring(board,router,nets,**kw):
        count=route(board,router,nets,**kw)
        if f.return_net in nets and count:
            f.item('new-floating',f.return_net,(11000000,150000000),(12000000,150000000));router.added=['new-floating']
        return count
    monkeypatch.setattr(graph,'route_components',with_ring)
    rows=f.run(participant_ownership=f.owner_token)
    assert rows[0]['accepted'] is False and frame(f.board)==f.before
    assert 'unknown-floating' in f.board.items


def test_intact_same_net_islands_do_not_trigger_repeated_unchanged_native_graph_reads(monkeypatch):
    f=MixedFixture(monkeypatch)
    for i in range(60):
        y=160000000+i*4000000
        f.item('island-%d-a'%i,f.return_net,(0,y),(0,y),Pad);f.item('island-%d-b'%i,f.return_net,(3000000,y),(3000000,y),Pad)
        uid='island-%d-copper'%i;f.item(uid,f.return_net,(0,y),(3000000,y));f.protected.add(uid)
    f.before=frame(f.board);f.before_parts=partitions(f.board,100)
    module=sys.modules['smilepcb.keyboard_routing.completion'];reads=[]
    def observe(board,deadline):reads.append(1);return partitions(board,deadline)
    monkeypatch.setattr(module,'pad_partitions',observe)
    rows=f.run(participant_ownership=f.owner_token)
    assert rows[0]['accepted'] and len(f.recovery_calls)==1
    assert len(reads)<=7, 'Unchanged Native graph was rebuilt %d times'%len(reads)
