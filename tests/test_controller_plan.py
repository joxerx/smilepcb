"""Host tests for authentic batch ownership; no Native routing claims."""
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from smilepcb.keyboard_routing import controller_plan as cp


def scene(monkeypatch):
    cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'ROW','2':'CTRL'}}]}
    pads=[dict(uuid='p1',number='1',net='ROW'),dict(uuid='p2',number='2',net='CTRL')]
    before={'tracks':{},'footprints':{'mcu':{'ref':'U_MCU','pads':pads}}}
    b=NS(state=deepcopy(before))
    ports=[]
    for net,pad,uid in [('ROW','p1','s1'),('CTRL','p2','s2')]:
        b.state['tracks'][uid]=dict(net=net,locked=False,**{'class':'PCB_TRACK'},start=[0,0],end=[1,0])
        ports.append(dict(net=net,pad_ids=[pad],added_ids=[uid],terminal_id=uid,exit_nm=[1,0]))
    def graph(board,nets,deadline):
        rows=[dict(item=NS(m_Uuid=NS(AsString=lambda u=u:u)),net=d['net']) for u,d in b.state['tracks'].items() if d['net'] in nets]
        rows += [dict(item=NS(m_Uuid=NS(AsString=lambda u=d['uuid']:u)),net=d['net']) for f in b.state['footprints'].values() for d in f['pads'] if d['net'] in nets]
        return {'nodes':rows,'find':lambda i:rows[i]['net']}
    tools=NS(snapshot=lambda board:deepcopy(board.state),graph=graph,representable=lambda *a:None,physics=lambda *a:'physical',profile=lambda:'dsn-process-v2')
    monkeypatch.setattr(cp,'_proof_tools',lambda:tools)
    receipt=dict(accepted=True,ports=ports,added_ids=['s1','s2'],already_accessible=[])
    return b,cfg,before,receipt


def capture(args):return cp.certify_source_batch(*args,matrix_nets={'ROW'},requested_nets={'ROW','CTRL'},deadline=100000000000.)


def test_real_full_delta_partitions_once_without_a_fabricated_before(monkeypatch):
    b,cfg,before,receipt=scene(monkeypatch);batch=capture((b,cfg,before,receipt))
    handoff=batch.handoff();source=handoff.take(b,cfg,{'ROW'},deadline=1e11)
    assert source['ports']==[receipt['ports'][0]] and source['added_ids']==['s1']
    assert batch.fixed_ports==[receipt['ports'][1]]
    assert handoff.matrix_delta(b,cfg,source['ports'],deadline=1e11)=={'s1'}
    assert before['tracks']=={} and b.state['tracks'].keys()=={'s1','s2'}
    with pytest.raises(ValueError,match='consumed'):handoff.take(b,cfg,{'ROW'},deadline=1e11)


@pytest.mark.parametrize('change',['missing','duplicate','foreign','pad','fixed-binding','tamper'])
def test_bad_or_stale_batch_cannot_authorize_a_matrix_subset(monkeypatch,change):
    b,cfg,before,receipt=scene(monkeypatch)
    if change=='missing':receipt['ports'].pop()
    elif change=='duplicate':receipt['ports'][1]['added_ids']=['s1']
    elif change=='foreign':b.state['tracks']['foreign']=dict(b.state['tracks']['s2'])
    if change in ('missing','duplicate','foreign'):
        with pytest.raises(ValueError):capture((b,cfg,before,receipt))
        return
    batch=capture((b,cfg,before,receipt))
    if change=='pad':b.state['footprints']['mcu']['pads'][0]['number']='9'
    elif change=='fixed-binding':cfg['parts'][0]['nets']['2']='OTHER'
    else:batch.ports[0]['added_ids'].append('s2')
    with pytest.raises(ValueError):batch.handoff().take(b,cfg,{'ROW'},deadline=1e11)


def test_rebinding_accepts_only_matrix_labels_and_keeps_real_context(monkeypatch):
    b,cfg,before,receipt=scene(monkeypatch);batch=capture((b,cfg,before,receipt))
    b.state['tracks']['local']=dict(b.state['tracks']['s2'])
    h=batch.handoff();source=h.take(b,cfg,{'ROW'},deadline=1e11)
    assert h.matrix_delta(b,cfg,source['ports'],deadline=1e11)=={'s1'}
    b.state['tracks']['local']['end']=[4,0]
    with pytest.raises(ValueError):h.matrix_delta(b,cfg,source['ports'],deadline=1e11)


def test_deadline_rejection_does_not_consume_or_mutate(monkeypatch):
    args=scene(monkeypatch);batch=capture(args);h=batch.handoff();original=deepcopy(args[0].state)
    with pytest.raises(TimeoutError):h.take(args[0],args[1],{'ROW'},deadline=0)
    assert not h.used and args[0].state==original


def test_second_handle_cannot_reuse_batch_and_fixed_label_cannot_permute(monkeypatch):
    b,cfg,before,receipt=scene(monkeypatch);batch=capture((b,cfg,before,receipt))
    first,second=batch.handoff(),batch.handoff();source=first.take(b,cfg,{'ROW'},deadline=1e11)
    with pytest.raises(ValueError,match='consumed'):second.take(b,cfg,{'ROW'},deadline=1e11)
    b.state['tracks']['s2']['net']='ROW'
    with pytest.raises(ValueError):first.matrix_delta(b,cfg,source['ports'],deadline=1e11)


def test_authentic_matrix_permutation_does_not_relabel_fixed_source(monkeypatch):
    b,cfg,before,receipt=scene(monkeypatch)
    pad=dict(uuid='p3',number='3',net='COL')
    for state in (before,b.state):state['footprints']['mcu']['pads'].append(deepcopy(pad))
    cfg['parts'][0]['nets']['3']='COL';b.state['tracks']['s3']=dict(b.state['tracks']['s1'],net='COL')
    receipt['ports'].append(dict(receipt['ports'][0],net='COL',pad_ids=['p3'],added_ids=['s3'],terminal_id='s3'))
    receipt['added_ids'].append('s3')
    batch=cp.certify_source_batch(b,cfg,before,receipt,matrix_nets={'ROW','COL'},requested_nets={'ROW','COL','CTRL'},deadline=1e11)
    h=batch.handoff();source=h.take(b,cfg,{'ROW','COL'},deadline=1e11)
    remap={'ROW':'COL','COL':'ROW'}
    for row in source['ports']:
        b.state['tracks'][row['added_ids'][0]]['net']=remap[row['net']];row['net']=remap[row['net']]
    for d in b.state['footprints']['mcu']['pads']:d['net']=remap.get(d['net'],d['net'])
    cfg['parts'][0]['nets'].update({'1':'COL','3':'ROW'})
    assert h.matrix_delta(b,cfg,source['ports'],deadline=1e11)=={'s1','s3'}
    assert b.state['tracks']['s2']['net']=='CTRL'


def local_scene(monkeypatch,external=False,broken=False):
    b,cfg,before,receipt=scene(monkeypatch)
    pad=dict(uuid='load',number='1',net='CTRL')
    b.state['footprints']['load']={'ref':'R_LOAD','pads':[pad]}
    cfg['parts'].append(dict(ref='R_LOAD',region='controller',nets={'1':'CTRL'}))
    if external:
        b.state['footprints']['field']={'ref':'J_FIELD','pads':[dict(pad,uuid='field')]}
        cfg['parts'].append(dict(ref='J_FIELD',region='field',nets={'1':'CTRL'}))
    events=[];connected=[False]
    def members(board,model):
        parts={q['ref']:q for q in model['parts']}
        rows={d['uuid']:{'ref':f['ref'],'pad':deepcopy(d)} for f in b.state['footprints'].values() for d in f['pads'] if d['net']=='CTRL'}
        return deepcopy(b.state),parts,{'CTRL':rows}
    def graph(*a,**kw):
        rows=[dict(item=NS(m_Uuid=NS(AsString=lambda u=u:u))) for u in ['p2','load','field']]
        return {'nodes':rows,'find':lambda i:0 if connected[0] and not broken or i==0 else i}
    def route(*a,**kw):events.append(('route',kw));connected[0]=True;return 1
    from contextlib import contextmanager
    @contextmanager
    def reserve(ports):events.append(('reserve',deepcopy(ports)));yield
    tools=NS(members=members,graph=graph,router=lambda *a,**k:object(),route=route,reserve=reserve,
        capture=lambda *a,**k:dict(nets={} if external else {'CTRL':{}}))
    monkeypatch.setattr(cp,'_local_tools',lambda:tools)
    b.GetDesignSettings=lambda:NS(m_TrackMinWidth=150000)
    return b,cfg,events


def test_controller_obligations_close_actual_local_pads_with_matrix_reserved(monkeypatch):
    b,cfg,events=local_scene(monkeypatch,external=True)
    proof=cp.close_controller_obligations(b,cfg,{'CTRL'},matrix_ports=[{'net':'ROW'}],deadline=1e11)
    assert events[0]==('reserve',[{'net':'ROW'}])
    assert events[1][1]['required_pad_ids']=={'p2','load'}
    assert events[1][1]['refresh_components'] is True and events[1][1]['prefer_small_goal'] is True
    assert proof['pad_subsets']['CTRL']['external_pad_ids']==['field']
    assert not proof['pad_subsets']['CTRL']['controller_only']
    cp.verify_controller_local_subsets(b,cfg,proof,deadline=1e11)
    b.state['footprints']['load']['pads'][0]['number']='alias-changed'
    with pytest.raises(ValueError):cp.verify_controller_local_subsets(b,cfg,proof,deadline=1e11)


def test_unclosed_controller_local_obligation_rejects_even_after_route_attempt(monkeypatch):
    b,cfg,_=local_scene(monkeypatch,broken=True)
    with pytest.raises(ValueError,match='not closed'):
        cp.close_controller_obligations(b,cfg,{'CTRL'},matrix_ports=[],deadline=1e11)


def test_local_subset_receipt_cannot_drop_an_outstanding_function(monkeypatch):
    b,cfg,_=local_scene(monkeypatch)
    proof=cp.close_controller_obligations(b,cfg,{'CTRL'},matrix_ports=[],deadline=1e11)
    proof['pad_subsets']={}
    with pytest.raises(ValueError):cp.verify_controller_local_subsets(b,cfg,proof,deadline=1e11)


def test_shared_batch_rejects_a_model_region_change(monkeypatch):
    b,cfg,before,receipt=scene(monkeypatch);batch=capture((b,cfg,before,receipt))
    cfg['parts'][0]['region']='field'
    with pytest.raises(ValueError):batch.handoff().take(b,cfg,{'ROW'},deadline=1e11)


def test_unified_preparation_selects_all_outstanding_functions_in_one_call(monkeypatch):
    import sys
    b,cfg,_,_=scene(monkeypatch);cfg['plane_nets']=['GND'];b.GetDesignSettings=lambda:NS(m_MinClearance=173000)
    calls=[]
    source=dict(accepted=True,ports=[{'net':'ROW'},{'net':'CTRL'}],added_ids=[])
    def select(*a,**kw):calls.append(('source',kw));return source
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_ports',NS(route_controller_ports=select))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_contract',NS(mcu_signal_nets=lambda cfg:{'ROW','CTRL','LOCAL'}))
    internal_handoff=NS()
    batch=NS(ports=source['ports'],fixed_ports=[{'net':'CTRL'}],seal='authentic',handoff=lambda:internal_handoff)
    monkeypatch.setattr(cp,'_controller_local_ownership',lambda *a,**kw:None)
    def certify(board,model,before,receipt,**kw):
        assert before==b.state and receipt is source;calls.append(('certify',kw));return batch
    def close(board,model,names,**kw):
        calls.append(('close',dict(names=names,**kw)))
        return dict(local_closed={'nets':{'CTRL':{},'LOCAL':{}}},external_obligations={})
    monkeypatch.setattr(cp,'certify_source_batch',certify);monkeypatch.setattr(cp,'close_controller_obligations',close)
    stop=__import__('time').monotonic()+100
    handoff,fixed,result=cp.prepare_unified_sources(b,cfg,controller_refs={'U_MCU'},matrix_nets={'ROW'},
        local_closed={'nets':{'LOCAL':{}}},deadline=stop,coherent_via_banks=True)
    assert [name for name,_ in calls]==['source','certify','close']
    assert calls[0][1]['include_nets']=={'ROW','CTRL'} and calls[0][1]['coherent_via_banks']
    assert calls[1][1]['requested_nets']=={'ROW','CTRL'}
    assert calls[2][1]['names']=={'CTRL','LOCAL'} and calls[2][1]['matrix_ports']==[{'net':'ROW'}]
    assert calls[0][1]['deadline']<=stop-20 and calls[2][1]['deadline']==stop
    assert handoff is internal_handoff and handoff.controller_ownership is None and fixed['ports']==[] and result['accepted']


def test_source_fallback_uses_remaining_original_deadline_after_stock_miss(monkeypatch):
    import sys
    from smilepcb.keyboard_routing.generation import generation_profile
    b,cfg,_,_=scene(monkeypatch);cfg['plane_nets']=['GND'];b.GetDesignSettings=lambda:NS(m_MinClearance=173000)
    clock=[100.];monkeypatch.setattr(cp.time,'monotonic',lambda:clock[0]);calls=[]
    source=dict(accepted=True,ports=[{'net':'ROW'}],added_ids=[])
    def stock(*a,**kw):calls.append(('stock',kw['deadline']));clock[0]+=10;return dict(accepted=False,reason='template miss')
    def fallback(*a,**kw):calls.append(('fallback',kw['deadline']));return source
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_ports',NS(route_controller_ports=stock))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_escape',NS(route_controller_escapes=fallback))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_contract',NS(mcu_signal_nets=lambda cfg:{'ROW','CTRL'}))
    batch=NS(ports=source['ports'],fixed_ports=[],seal='authentic',handoff=lambda:NS())
    monkeypatch.setattr(cp,'certify_source_batch',lambda *a,**kw:batch)
    monkeypatch.setattr(cp,'_controller_local_ownership',lambda *a,**kw:None)
    monkeypatch.setattr(cp,'close_controller_obligations',lambda *a,**kw:dict(local_closed={'nets':{'CTRL':{}}},external_obligations={}))
    with generation_profile('dsn-process-v2'):
        _,_,result=cp.prepare_unified_sources(b,cfg,controller_refs={'U_MCU'},matrix_nets={'ROW'},local_closed={'nets':{'CTRL':{}}},deadline=220)
    assert result['accepted']
    assert calls==[('stock',135),('fallback',200)]
