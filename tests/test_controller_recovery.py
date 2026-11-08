"""Host coordinator tests; real Native geometry is qualified separately."""
from copy import deepcopy
from types import SimpleNamespace as NS
import time
import pytest
from smilepcb.keyboard_routing import controller_recovery as cr


def setup(monkeypatch, *, restore_ok=True, unsafe=False):
    state={'tracks':{'power':{'net':'+V','locked':False},'hard':{'net':'USB','locked':False}},'footprints':{'exact':{'pads':[]}}}
    initial=deepcopy(state);closed=set();events=[]
    members={'alpha':{'p1':{'ref':'U'},'p2':{'ref':'A'}},'beta':{'p3':{'ref':'U'},'p4':{'ref':'B'},'p5':{'ref':'B'}},'row':{'r':{'ref':'U'},'s':{'ref':'S'}}}
    parts={'U':{'region':'controller'},'A':{'region':'controller'},'B':{'region':'controller'},'S':{'region':'matrix'}}
    def capture(*a,**k):return {'nets':{n:{} for n in closed}}
    def negotiate(board,nets,hard,deadline,**kw):
        assert hard==set(state['tracks'])-{'power','replacement'}
        net=next(iter(nets));events.append(net);closed.add(net)
        state['tracks'].pop('power',None)
        state['tracks']['signal-'+net]={'net':net,'locked':False}
        if unsafe:state['tracks']['hard']['net']='corrupted'
        return {'accepted':True}
    def restore(*a,**kw):
        if restore_ok:state['tracks']['replacement']={'net':'+V','locked':False}
        return [],restore_ok
    def rollback(*a):state.clear();state.update(deepcopy(initial));events.append('rollback')
    def graph(board,nets,*a,**kw):
        ids=set().union(*(set(members.get(n,{})) for n in nets))
        return {'nodes':[{'item':NS(m_Uuid=NS(AsString=lambda uid=uid:uid))} for uid in sorted(ids)],'find':lambda i:0}
    tools=NS(snapshot=lambda b:deepcopy(state),copy_tracks=lambda b:deepcopy(state),restore_tracks=rollback,
        prepare=lambda *a,**k:{'accepted':True},capture=capture,verify=lambda *a,**k:None,
        members=lambda *a:(deepcopy(state),parts,members),signal_nets=lambda c:set(members),
        ordered_nets=lambda c:[('rows',['row']),('columns',[])],partitions=lambda *a:['old physical pads'],
        preserved=lambda a,b:restore_ok,negotiate=negotiate,restore=restore,router=object,
        graph=graph,prove=lambda b,delta,*a:len(delta),via_clearance=lambda *a,**k:None,
        usb=lambda *a,**k:{},cleanup=lambda *a,**k:{'removed_ids':[],'trimmed_ids':[]})
    monkeypatch.setattr(cr,'_tools',lambda:tools)
    monkeypatch.setattr(cr,'_recovered_local_ownership',lambda *a,**kw:object())
    return state,initial,events


def test_closes_actual_controller_functions_in_smallest_group_order(monkeypatch):
    state,initial,events=setup(monkeypatch)
    r=cr.recover_controller_locals(object(),{'plane_nets':['GND']},module_power_ids={'power'},deadline=time.monotonic()+200)
    assert r['accepted'] and events==['alpha','beta']
    assert set(r['local_closed']['nets'])=={'alpha','beta'}
    assert r['power_descendant_ids']==['replacement']
    assert state['tracks']['hard']==initial['tracks']['hard']


@pytest.mark.parametrize('options',[{'restore_ok':False},{'unsafe':True}])
def test_failed_physical_or_hard_copper_guard_restores_exact_parent(monkeypatch,options):
    state,initial,events=setup(monkeypatch,**options)
    r=cr.recover_controller_locals(object(),{'plane_nets':['GND']},module_power_ids={'power'},deadline=time.monotonic()+200)
    assert not r['accepted'] and state==initial and events[-1]=='rollback'


@pytest.mark.parametrize('ids',[{'missing'},{'hard'}])
def test_unowned_or_non_supply_copper_is_never_a_repair_participant(monkeypatch,ids):
    state,initial,events=setup(monkeypatch)
    with pytest.raises(ValueError,match='compiled supply'):
        cr.recover_controller_locals(object(),{'plane_nets':['GND']},module_power_ids=ids,deadline=time.monotonic()+200)
    assert state==initial and not events


@pytest.mark.parametrize('unsafe',[False,True])
def test_owned_supply_tails_are_cleaned_before_final_closure_freeze(monkeypatch,unsafe):
    state,initial,events=setup(monkeypatch);tools=cr._tools();calls=[]
    def cleanup(b,**kw):
        calls.append(kw)
        assert kw['protected']==set(state['tracks'])-{'replacement'}
        uid='hard' if unsafe else 'replacement';del state['tracks'][uid]
        return {'removed_ids':[uid],'trimmed_ids':[]}
    tools.cleanup=cleanup
    result=cr.recover_controller_locals(object(),{'plane_nets':['GND']},module_power_ids={'power'},deadline=time.monotonic()+200)
    assert calls
    if unsafe:assert not result['accepted'] and state==initial
    else:
        assert result['accepted'] and result['cleanup']['removed_ids']==['replacement']
        assert result['power_descendant_ids']==[] and set(result['local_closed']['nets'])=={'alpha','beta'}
