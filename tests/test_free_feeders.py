"""Host free-feeder orchestration: native routing/graphs use explicit seams."""
from copy import deepcopy
import importlib
from types import SimpleNamespace
import sys

import pytest
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory,current_launches


def track(net,*,y=0,**extra):
    return dict({'class':'PCB_TRACK','net':net,'start':[0,y],'end':[1000000,y],
                 'width':150000,'layer':0,'locked':False},**extra)


def scene(monkeypatch,*,missing=('alpha','beta','gamma'),route_closes=True,route_seconds=1,repair_closes=False):
    clock=SimpleNamespace(now=100.);clock.monotonic=lambda:clock.now
    pads=[{'uuid':'pad-'+net,'number':str(i),'net':net} for i,net in enumerate(('alpha','beta','gamma','control'),1)]
    retained={'footprints':{'mcu':{'ref':'U_MCU','pads':pads}},
              'tracks':{'field':track('alpha',y=9000000),'fixed':track('control',y=8000000)}}
    board=SimpleNamespace(state=deepcopy(retained),missing={n:1 for n in missing},
        GetDesignSettings=lambda:SimpleNamespace(m_TrackMinWidth=150000))
    board.state['tracks']['prefix']=track('beta',y=7000000)
    cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{str(i):n for i,n in enumerate(('alpha','beta','gamma','control'),1)}}],
         'layout':{'gpio':{'rows':['r1','r2'],'cols':['c1']},'rgb':False},
         'mcu_gpio_pads':{'r1':'1','r2':'2','c1':'3'},'plane_nets':[]}
    fixed=[{'net':'control','pad_ids':['pad-control'],'added_ids':['fixed']}]
    calls={'route':[],'repair':[],'graphs':[]};routers=[]
    module=importlib.import_module('smilepcb.keyboard_routing.free_feeders')
    monkeypatch.setattr(module,'time',clock)
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',SimpleNamespace(snapshot=lambda b:deepcopy(b.state)))
    class Router:
        def __init__(self,b,**kw):
            self.board=b;self.deadline=kw['deadline'];self.track_width=kw['track_width_nm'];self.minimum_clearance=173000
            self.graph_diagnostic={};routers.append(self)
        def expired(self):return clock.now>=self.deadline
    def native_state(b,nets,deadline):
        calls['graphs'].append(deadline)
        if clock.now>=deadline:raise TimeoutError('Native graph deadline')
        return {'missing':{n:v for n,v in b.missing.items() if n in nets},'groups':{n:2 if n in b.missing else 1 for n in nets},'pad_ids':set()}
    def route(b,router,nets,**kw):
        net=next(iter(nets));calls['route'].append((net,router,kw,tuple(q['net'] for q in current_launches())))
        clock.now=min(router.deadline,clock.now+route_seconds)
        b.state['tracks']['new-'+str(len(calls['route']))]=track(net)
        if route_closes:b.missing.pop(net,None)
        return 999  # This does not prove native closure.
    def repair(b,nets,protected,**kw):
        calls['repair'].append((nets,protected,kw,tuple(q['net'] for q in current_launches())))
        if repair_closes:b.missing={}
        return [{'reason':'controlled host repair'}]
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.matrix',SimpleNamespace(KeyboardRouter=Router))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',SimpleNamespace(route_components=route))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.feeder_repair',SimpleNamespace(matrix_pad_state=native_state,repair_feeders=repair))
    return module,board,cfg,retained,fixed,calls,clock,routers


def invoke(case,*,groups=None,deadline=200):
    api,board,cfg,retained,fixed,_,_,_=case
    return api.route_free_feeders(board,cfg,retained,groups=groups or [['alpha','beta'],['gamma']],fixed_ports=fixed,deadline=deadline)


def test_native_missing_state_drives_routing_and_fixed_only_scope_is_restored(monkeypatch):
    case=scene(monkeypatch,missing=('alpha','gamma'));_,board,_,retained,fixed,calls,_,_=case
    old=deepcopy(retained);outer=fixed+[{'net':'alpha','pad_ids':['pad-alpha'],'added_ids':['historical-head']}]
    with reserve_launches(outer,resolved=[('pad-alpha',)],repair_reserved=[('pad-alpha',)]):
        inventory=current_launch_inventory();before=deepcopy(inventory.__dict__)
        report=invoke(case)
        assert current_launch_inventory() is inventory and inventory.__dict__==before
    assert [q[0] for q in calls['route']]==['alpha','gamma']
    assert all(q[3]==('control',) for q in calls['route'])
    assert not calls['repair'] and report['remaining']=={}
    assert 'accepted' not in report and 'complete' not in report and retained==old
    assert all(board.state['tracks'][u]==shape for u,shape in retained['tracks'].items())


@pytest.mark.parametrize('groups',[[['alpha'],['gamma']],[['alpha','beta'],['alpha','gamma']],[['alpha','beta','gamma','alien']],[[],['alpha','beta','gamma']]])
def test_groups_must_partition_the_actual_matrix_nets(monkeypatch,groups):
    case=scene(monkeypatch);before=deepcopy(case[1].state)
    with pytest.raises(ValueError):invoke(case,groups=groups)
    assert not case[5]['route'] and not case[5]['graphs'] and case[1].state==before


@pytest.mark.parametrize('damage',['retained','footprints','locked','foreign','unsupported','fixed_matrix','fixed_editable'])
def test_unproved_or_foreign_entry_ownership_rejected_before_native_routing(monkeypatch,damage):
    case=scene(monkeypatch);_,board,_,_,fixed,calls,_,_=case
    if damage=='retained':board.state['tracks']['field']['width']+=1
    elif damage=='footprints':board.state['footprints']['mcu']['pads'][0]['net']='changed'
    elif damage=='locked':board.state['tracks']['prefix']['locked']=True
    elif damage=='foreign':board.state['tracks']['prefix']['net']='control'
    elif damage=='unsupported':board.state['tracks']['prefix']['class']='PCB_ARC'
    elif damage=='fixed_matrix':fixed.append({'net':'alpha','pad_ids':['pad-alpha'],'added_ids':['field']})
    else:fixed[0]['added_ids']=['prefix']
    before=deepcopy(board.state)
    with pytest.raises(ValueError):invoke(case)
    assert not calls['route'] and not calls['graphs'] and board.state==before


def test_groups_share_routing_time_by_pending_net_count_and_preserve_repair_tail(monkeypatch):
    case=scene(monkeypatch,route_closes=False,route_seconds=10)
    report=invoke(case,deadline=130)
    assert report['routing_deadline']==115 and report['repair_reserved_s']==15
    routes=case[5]['route'];assert routes
    assert all(row[1].deadline<=115 for row in routes)
    assert routes[0][1].deadline<=110  # Two of three pending nets receive 10s.
    assert case[5]['repair'][0][2]['deadline']==130
    assert report['remaining']=={'alpha':1,'beta':1,'gamma':1}


def test_success_counts_cannot_claim_closure_and_repair_can_edit_only_proven_prefix(monkeypatch):
    case=scene(monkeypatch,route_closes=False,repair_closes=True)
    report=invoke(case);nets,protected,options,scope=case[5]['repair'][0]
    assert nets=={'alpha','beta','gamma'} and protected=={'field','fixed'}
    assert options['ports']==case[4] and options['source_ports']==case[4] and scope==('control',)
    assert report['repair_history']==[{'reason':'controlled host repair'}] and report['remaining']=={}
    assert len(report['routing_history'])==6


def test_expired_deadline_routes_nothing_and_does_not_invent_missing_state(monkeypatch):
    case=scene(monkeypatch);report=invoke(case,deadline=100)
    assert not case[5]['route'] and not case[5]['repair'] and not case[5]['graphs']
    assert report['remaining'] is None and report['deadline_exhausted']


def test_parallel_cost_refreshes_from_actual_foreign_matrix_copper_per_net(monkeypatch):
    case=scene(monkeypatch,missing=('alpha','gamma'))
    invoke(case)
    first,second=(q[1] for q in case[5]['route'])
    def shape(y):
        return SimpleNamespace(GetSeg=lambda:SimpleNamespace(A=SimpleNamespace(x=0,y=y),B=SimpleNamespace(x=1000000,y=y)))
    assert first.routing_penalty(shape(350000),0,'alpha',False)==900000
    assert second.routing_penalty(shape(350000),0,'gamma',False)==0
    assert second.routing_penalty(shape(350000),0,'alpha',False)==900000
    assert second.routing_penalty(shape(350000),0,'gamma',True)==0
    assert first.grid_step==second.grid_step==500000
    assert first.search_expansion_limit==200000 and first.heuristic_weight==3 and first.via_cost_nm==1000000


def test_native_failure_restores_outer_scope_and_propagates_without_accepting(monkeypatch):
    case=scene(monkeypatch);api,board,_,_,fixed,_,_,_=case
    def fail(*a,**kw):raise ValueError('native route rejected')
    sys.modules['smilepcb.keyboard_routing.native_graph'].route_components=fail
    with reserve_launches(fixed+[{'net':'alpha','pad_ids':['pad-alpha'],'added_ids':['old']}]) as _:
        inventory=current_launch_inventory();before=deepcopy(inventory.__dict__)
        with pytest.raises(ValueError,match='native route'):invoke(case)
        assert current_launch_inventory() is inventory and inventory.__dict__==before


def test_expired_group_keeps_later_groups_remaining_routing_allowance(monkeypatch):
    case=scene(monkeypatch);_,_,_,_,_,calls,clock,_=case
    feeder=sys.modules['smilepcb.keyboard_routing.feeder_repair'];original=feeder.matrix_pad_state;expired=[]
    def graph(board,nets,deadline):
        if deadline<155 and not expired:
            expired.append(deadline);clock.now=deadline;raise TimeoutError('First group assessment expired')
        return original(board,nets,deadline)
    feeder.matrix_pad_state=graph
    report=invoke(case)
    assert [row[0] for row in calls['route']]==['gamma']
    assert report['remaining']=={'alpha':1,'beta':1}
    assert calls['repair'][0][2]['deadline']==200


def test_post_route_retained_mutation_fails_with_outer_scope_restored(monkeypatch):
    case=scene(monkeypatch);_,board,_,_,fixed,_,_,_=case
    def mutate(*a,**kw):board.state['tracks']['field']['width']+=1;return 1
    sys.modules['smilepcb.keyboard_routing.native_graph'].route_components=mutate
    with reserve_launches(fixed):
        inventory=current_launch_inventory();before=deepcopy(inventory.__dict__)
        with pytest.raises(ValueError,match='retained'):invoke(case)
        assert current_launch_inventory() is inventory and inventory.__dict__==before


def test_coarse_connections_keep_fine_terminal_attachment_resolution(monkeypatch):
    case=scene(monkeypatch,route_closes=False)
    invoke(case)
    assert [kw['finer_grid_steps'] for _,router,kw,_ in case[5]['route'] if router.grid_step==500000]==[(125000,)]*3
    assert [kw['finer_grid_steps'] for _,router,kw,_ in case[5]['route'] if router.grid_step==125000]==[()]*3


@pytest.mark.parametrize('unified',[None,False,True])
def test_unified_free_matrix_stage_refreshes_contacts_without_reversing_default_direction(monkeypatch,unified):
    case=scene(monkeypatch,missing=('alpha',));cfg=case[2]
    if unified is not None:cfg['routing_preparation']={'unified_controller_plan':unified}
    invoke(case)
    calls=case[5]['route'];assert calls
    for _,router,kw,scope in calls:
        assert ('refresh_components' in kw) is (unified is True)
        assert 'prefer_small_goal' not in kw
        if unified:assert kw['refresh_components'] is True
        assert scope==('control',) and kw['finer_grid_steps']==(125000,)
        assert router.connection_budget_s==2 and router.grid_step==500000
