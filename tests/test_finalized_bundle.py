"""Transaction tests at the Native adapter boundary, using real contracts."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import importlib,sys,time
import pytest

class Track:
    def __init__(self,uid,net,component):self.uid=uid;self.net=net;self.component=component;self.m_Uuid=SimpleNamespace(AsString=lambda:uid)
    def GetNetname(self):return self.net
    def IsLocked(self):return getattr(self,'locked',False)
class Board:
    def __init__(self):
        self.tracks={q.uid:q for q in [Track('old','signal','s'),Track('retained','signal','s'),Track('branch','plane','p'),Track('anchor','plane','p')]}
        self.zones=[SimpleNamespace(GetIsRuleArea=lambda:False,GetNetname=lambda:'plane',m_Uuid=SimpleNamespace(AsString=lambda:'zone'))]
    def GetTracks(self):return list(self.tracks.values())
    def Zones(self):return list(self.zones)
    def GetFootprints(self):return []
    def Remove(self,q):del self.tracks[q.uid]
    def Delete(self,q):del self.tracks[q.uid]
    def Add(self,q):self.tracks[q.uid]=q

def state(b):return dict(tracks={u:dict(net=q.net,component=q.component) for u,q in b.tracks.items()},footprints={'fixed':True})
def graph(b,nets,deadline=None,**kwargs):
    nodes=[dict(item=q,zone=False,net=q.net) for q in b.GetTracks() if q.net in nets];groups={}
    for i,n in enumerate(nodes):groups.setdefault(n['item'].component,[]).append(i)
    return dict(nodes=nodes,groups=groups)
def row():return dict(net='signal',ids=['old'],window={'source':(0,0)})
def plan(b):return dict(planned=True,removed_ids=['old','branch'],operations=[{'kind':'track'}],source_snapshot=state(b),deferred_zone_ids=['zone'])

def selector(b,witness_ids,protected_ids=()):
    return dict(removed_ids=['branch'],ownership=[{'witness_ids':list(witness_ids)}],requires_filled_connectivity_proof=True)
def prefix(b,r,protected_ids=()):
    q=deepcopy(r);q['window']['source']=(1,1);q['source_prefix_witness']={'via':{'id':'via'}};return q

def commit_good(b,p,**kwargs):
    for u in p['removed_ids']:b.Delete(b.tracks[u])
    b.Add(Track('new','signal','s'))
    return dict(accepted=True,removed_ids=p['removed_ids'],new_ids=['new'])

@contextmanager
def adapters(planner=None,commit=commit_good,prefix_fn=prefix,selector_fn=selector):
    if planner is None:
        def planner(b,group,**kwargs):
            assert 'branch' not in b.tracks
            assert kwargs['deferred_zone_ids']==['zone'] and callable(kwargs['attachment_search'])
            return dict(planned=True,removed_ids=['old'],operations=[{'kind':'track'}],source_snapshot=state(b),source=group[0]['window']['source'])
    modules={
        'smilepcb.native_worker':SimpleNamespace(snapshot=state),
        'smilepcb.keyboard_routing.obstacle_bundles':SimpleNamespace(plan_obstacle_group=planner,commit_obstacle_group=commit),
        'smilepcb.keyboard_routing.source_prefixes':SimpleNamespace(source_prefix_variant=prefix_fn),
        'smilepcb.keyboard_routing.plane_branches':SimpleNamespace(candidate_plane_branches=selector_fn),
        'smilepcb.keyboard_routing.visual_polish':SimpleNamespace(_refill=lambda b:None,_copy=deepcopy),
        'smilepcb.keyboard_routing.native_graph':SimpleNamespace(copper_graph=graph),
    }
    with patch.dict(sys.modules,modules):
        yield importlib.import_module('smilepcb.keyboard_routing.finalized_bundle')

def run_plan(api,b,**kwargs):
    return api.plan_finalized_group(b,[row()],bounds=(0,0,10,10),preferred_layer=0,protected_ids=(),deadline=time.monotonic()+30,plane_witness_ids=['branch'],**kwargs)

def test_planning_restores_temporary_branch_and_exports_original_source_ownership():
    b=Board();before=state(b)
    with adapters() as api:out=run_plan(api,b,candidate_axes=[[{'kind':'track'}]])
    assert out['planned'] and out['source']==(1,1)
    assert out['removed_ids']==['branch','old'] and out['source_snapshot']==before
    assert out['source_prefixes'][0]['via']['id']=='via' and out['plane_ownership']['removed_ids']==['branch']
    assert state(b)==before

def test_no_prefix_falls_back_to_original_source_without_mutating_it():
    b=Board()
    with adapters(prefix_fn=lambda *a,**kw:None) as api:out=run_plan(api,b)
    assert out['planned'] and out['source']==(0,0) and out['source_prefixes']==[]

@pytest.mark.parametrize('failure',[TimeoutError('expired'),ValueError('blocked'),RuntimeError('unexpected')])
def test_planning_failure_restores_detached_native_owners(failure):
    b=Board();before=state(b)
    def broken(*args,**kw):raise failure
    with adapters(planner=broken) as api:
        if isinstance(failure,RuntimeError):
            with pytest.raises(RuntimeError):run_plan(api,b)
        else:assert not run_plan(api,b)['planned']
    assert state(b)==before

def test_missing_planes_or_rejected_ownership_cannot_produce_a_plan():
    b=Board();b.zones=[]
    with adapters() as api:assert not run_plan(api,b)['planned']
    b=Board()
    with adapters(selector_fn=lambda *a,**kw:None) as api:assert not run_plan(api,b)['planned']

def test_commit_preserves_complete_filled_components_and_keeps_new_copper_attached():
    b=Board();p=plan(b)
    with adapters() as api:out=api.commit_finalized_group(b,p,protected_ids=(),deadline=time.monotonic()+30)
    assert out['accepted'] and out['conductor_components_preserved']
    assert set(b.tracks)=={'retained','anchor','new'}

def test_detached_new_copper_rolls_back_even_after_underlying_commit_accepts():
    b=Board();before=state(b);p=plan(b)
    def detached(b,p,**kw):
        out=commit_good(b,p,**kw);b.tracks['new'].component='island';return out
    with adapters(commit=detached) as api:out=api.commit_finalized_group(b,p,protected_ids=(),deadline=time.monotonic()+30)
    assert not out['accepted'] and out['source_exact'] and state(b)==before

def test_removing_an_entire_padless_conductor_island_is_rejected_before_commit():
    b=Board();b.tracks['branch'].component='padless';before=state(b)
    with adapters() as api:out=api.commit_finalized_group(b,plan(b),protected_ids=(),deadline=time.monotonic()+30)
    assert not out['accepted'] and state(b)==before

def test_unexpected_post_mutation_failure_restores_source_and_propagates():
    b=Board();before=state(b)
    def broken(b,p,**kw):commit_good(b,p,**kw);raise RuntimeError('native failure')
    with adapters(commit=broken) as api:
        with pytest.raises(RuntimeError):api.commit_finalized_group(b,plan(b),protected_ids=(),deadline=time.monotonic()+30)
    assert state(b)==before

def test_remove_failure_does_not_add_an_owner_that_was_never_detached():
    b=Board();before=state(b)
    def failed_remove(item):raise RuntimeError('remove failed before mutation')
    def unique_add(item):
        assert item.uid not in b.tracks
        b.tracks[item.uid]=item
    b.Remove=failed_remove;b.Add=unique_add
    with adapters() as api:
        with pytest.raises(RuntimeError,match='remove failed'):
            run_plan(api,b)
    assert state(b)==before

@pytest.mark.parametrize('blocked',['locked','USB_DP','USB_DM'])
def test_commit_independently_protects_locked_and_usb_copper(blocked):
    b=Board();before=state(b)
    if blocked=='locked':b.tracks['old'].locked=True
    else:b.tracks['old'].net=blocked;before=state(b)
    def forbidden_commit(*a,**kw):pytest.fail('Protected workset reached copper commit')
    with adapters(commit=forbidden_commit) as api:
        out=api.commit_finalized_group(b,plan(b),protected_ids=(),deadline=time.monotonic()+30)
    assert not out['accepted'] and out['source_exact'] and state(b)==before

def test_direct_source_mode_skips_prefix_helper_and_preserves_source_exactly():
    b=Board();before=state(b)
    def forbidden_prefix(*args,**kwargs):pytest.fail('Direct trial must not construct owned prefixes')
    with adapters(prefix_fn=forbidden_prefix) as api:out=run_plan(api,b,source_mode='direct')
    assert out['planned'] and out['source']==(0,0) and out['source_prefixes']==[]
    assert out['source_mode']=='direct' and out['source_snapshot']==before and state(b)==before


def test_joint_strategy_is_forwarded_and_failed_joint_receipt_is_retained():
    def planner(b,group,**kwargs):
        assert kwargs['source_strategy']=='joint'
        return dict(planned=False,reason='blocked',joint_source_diagnostics=[{'reason':'fixed-copper'}],accepted_source_strategy=None)
    with adapters(planner=planner) as api:out=run_plan(api,Board(),source_strategy='joint')
    assert out['source_strategy']=='joint' and out['joint_source_diagnostics']==[{'reason':'fixed-copper'}]
    assert out['accepted_source_strategy'] is None


def test_unknown_source_strategy_is_rejected_before_planner():
    def forbidden(*args,**kwargs):pytest.fail('Unknown strategy reached planner')
    with adapters(planner=forbidden) as api:out=run_plan(api,Board(),source_strategy='unrecognized')
    assert not out['planned'] and 'strategy' in out['reason']


def test_joint_strategy_keeps_default_owned_prefix_source_mode():
    def planner(b,group,**kwargs):
        assert kwargs['source_strategy']=='joint'
        assert group[0]['window']['source']==(1,1) and 'source_prefix_witness' in group[0]
        return dict(planned=False,reason='diagnostic')
    with adapters(planner=planner) as api:out=run_plan(api,Board(),source_strategy='joint')
    assert out['source_mode']=='owned-prefix' and out['source_prefixes']


def test_staged_joint_requires_explicit_direct_sources_before_prefix_selection():
    def forbidden(*args,**kwargs):pytest.fail('Unsupported staged prefix mode reached planner')
    with adapters(planner=forbidden) as api:out=run_plan(api,Board(),source_strategy='staged-joint')
    assert not out['planned'] and 'direct' in out['reason']


def test_staged_joint_direct_is_forwarded_and_head_only_failure_is_not_success():
    def planner(b,group,**kwargs):
        assert kwargs['source_strategy']=='staged-joint' and group[0]['window']['source']==(0,0)
        return dict(planned=False,reason='No corridor',staged_source_heads={'accepted':True})
    with adapters(planner=planner) as api:out=run_plan(api,Board(),source_strategy='staged-joint',source_mode='direct')
    assert not out['planned'] and out['staged_source_heads']['accepted'] and out['source_mode']=='direct'
