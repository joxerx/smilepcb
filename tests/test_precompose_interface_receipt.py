"""Host lifecycle controls using real interface capture and retirement proof."""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import traceback

import pytest
from test_controller_escape_integration import body, module_stub
from test_controller_return_order import Board, caller, snap
from test_post_matrix_interface_scope import receipt_scene
from smilepcb.keyboard_routing.generation import generation_profile


def collector_scene(monkeypatch, *, closed=('row','column'), local=False, retire=True):
    api,board,cfg,source_receipt,state=receipt_scene(monkeypatch,closed=closed,missing=())
    cfg.pop('routing_preparation')
    tracks=deepcopy(source_receipt['tracks']);events=[];captured=[]
    matrix_ports=deepcopy(source_receipt['ports'][:2]);fixed=deepcopy(source_receipt['ports'][2:])
    local_closed=None
    if local:
        cfg['parts'][1]['region']='controller';fixed=[];state['tracks'].pop('old-control')
        local_closed={'schema':1,'generation_profile':'dsn-process-v2','nets':{'control':{'pads':{
            d['uuid']:{'ref':f['ref'],'pad':deepcopy(d)} for f in state['footprints'].values()
            for d in f['pads'] if d['net']=='control'}}}}
    for uid in ('old-row','old-column'):state['tracks'].pop(uid)
    for f in board.GetFootprints():
        f.GetLayer=lambda:0
        for d in f.Pads():d.SetNet=lambda net,d=d:setattr(d,'net',net)
    def item(uid):
        return SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda:uid),
            GetNetname=lambda:state['tracks'][uid]['net'],SetNet=lambda net:state['tracks'][uid].update(net=net))
    board.GetTracks=lambda:[item(uid) for uid in state['tracks']]
    board.Delete=lambda t:state['tracks'].pop(t.m_Uuid.AsString())
    board.FindNet=lambda net:net;board.BuildConnectivity=lambda:None
    board.GetDesignSettings=lambda:SimpleNamespace(m_TrackMinWidth=150000,m_MinClearance=150000)
    def source(*args,**kwargs):
        events.append('source');state['tracks'].update({u:deepcopy(tracks[u]) for u in ('old-row','old-column')})
        return {'accepted':True,'ports':deepcopy(matrix_ports)}
    def assignment(board,cfg,ports,**kwargs):
        events.append('assignment');candidate=deepcopy(cfg);candidate['assignment_applied']=True
        return candidate,{'ports':deepcopy(ports)}
    def modules(*args,**kwargs):events.append('modules');return {'accepted':True}
    def returns(*args,**kwargs):events.append('returns');return {'accepted':True}
    original_capture=api.capture_interfaces
    def capture(board,candidate,ports,**kwargs):
        assert candidate['assignment_applied']
        assert events[-3:]==['assignment','modules','returns']
        receipt=original_capture(board,candidate,ports,**kwargs)
        events.append('capture');captured.append(receipt);return receipt
    def matrix(*args,**kwargs):
        events.append('matrix')
        if retire:
            for uid in ('old-row','old-column'):state['tracks'].pop(uid)
        # Replacement ports deliberately do not describe the original heads.
        return {'complete':True,'controller_connected':True,'matrix_ports':[]}
    module_stub(monkeypatch,'conditional_banks',certify_generated_heads=lambda *a,**kw:object())
    module_stub(monkeypatch,'modules',connect_key_modules=modules)
    # This lifecycle fixture models closure only, without native copper shapes.
    module_stub(monkeypatch,'matrix_layers',select_board_matrix_layers=lambda *a,**k:{'row_layer':0,'column_layer':2})
    module_stub(monkeypatch,'power',connect_controller_return_tree=returns)
    monkeypatch.setattr(api,'capture_interfaces',capture)
    native=sys.modules['smilepcb.native_worker'];native.p.F_Cu=0;native.p.B_Cu=2
    fn=body('collector_preparation','prepare_collector_matrix',dict(time=time,snapshot=native.snapshot,
        transient_access_ids=lambda *a:set(),reserve_launches=lambda *a:nullcontext(),route_controller_ports=source,
        ordered_nets=lambda c:[('rows',['row']),('columns',['column'])],
        collector_assignment=lambda *a:({},[{'collector_heading':[0,1],'signal_peel_order':['row','column']}]),
        apply_exit_assignment=assignment,matrix_stage=SimpleNamespace(prepare_matrix=matrix),
        capture_interfaces=capture,verify_interfaces=api.verify_interfaces,p=native.p,deepcopy=deepcopy,Path=Path,traceback=traceback))
    kwargs={'local_closed':local_closed} if local else {}
    initial=deepcopy(state)
    with generation_profile('dsn-process-v2'):
        updated,result=fn(board,cfg,controller_refs={'U_MCU'},nets={'row','column'},targets={},fixed_ports=fixed,
            deadline=time.monotonic()+200,**kwargs)
    return updated,result,captured,events,state,initial,local_closed


def test_collector_retains_strict_original_receipt_after_matrix_retires_heads(monkeypatch):
    updated,result,captured,events,state,_,_=collector_scene(monkeypatch)
    assert result['candidate_accepted'] and len(captured)==1
    assert result['interface'] is captured[0]
    assert list(result['interface']['tracks'])==['old-row','old-column','old-control']
    assert set(state['tracks'])=={'old-control'}
    assert result['assignment']['ports']==[]
    assert events==['source','assignment','modules','returns','capture','matrix']
    assert updated['routing_preparation']['generation_profile']=='dsn-process-v2'


def test_collector_captures_local_closure_under_explicit_v2_profile(monkeypatch):
    _,result,captured,_,_,_,local=collector_scene(monkeypatch,closed=('row','column','control'),local=True)
    assert result['candidate_accepted'] and captured[0]['local_closed']==local
    assert [q['net'] for q in result['interface']['ports']]==['row','column']


def test_matrix_success_flag_cannot_retire_a_head_for_an_open_native_net(monkeypatch):
    _,result,captured,_,state,initial,_=collector_scene(monkeypatch,closed=('column',))
    assert not result['candidate_accepted'] and 'copper changed' in result['reason']
    assert captured and 'interface' not in result and state==initial


def test_matrix_success_flag_cannot_hide_open_net_with_its_original_head(monkeypatch):
    _,result,_,_,state,initial,_=collector_scene(monkeypatch,closed=('column',),retire=False)
    assert not result['candidate_accepted'] and 'closure' in result['reason'].lower()
    assert 'interface' not in result and state==initial


def ordered_scene(monkeypatch, *, receipt=True, field_failure=False, scope='matrix-with-controller', terminal_nets=('ROW','COL')):
    fn,events,_=caller(monkeypatch,'dsn-process-v2',matrix_failure=True)
    original={'schema':1,'ports':[],'tracks':{'retired-head':{'net':'row'}}}
    local={'schema':1,'generation_profile':'dsn-process-v2','nets':{}}
    sys.modules['smilepcb.keyboard_routing.controller_local'].prepare_controller_local=lambda *a,**kw:{'accepted':True,'local_closed':local}
    def collector(board,cfg,**kwargs):
        assert kwargs.get('local_closed') is local
        updated=deepcopy(cfg);updated['routing_preparation']={'mode':'ordered-banks-v2','generation_profile':'dsn-process-v2'}
        prepared={'candidate_accepted':True,'assignment':{'ports':[]},'matrix':{'complete':True,'controller_connected':True},'prerequisite_ids':[]}
        if receipt:prepared['interface']=original
        return updated,prepared
    sys.modules['smilepcb.keyboard_routing.collector_preparation'].prepare_collector_matrix=collector
    def recapture(*args,**kwargs):raise AssertionError('Expired matrix heads must not be recaptured')
    fn.__globals__['capture_interfaces']=recapture
    def capture(*args,**kwargs):
        events.append('field-capture')
        return {'scope':scope,'terminals':{str(i):['rows' if n=='ROW' else 'columns',n] for i,n in enumerate(terminal_nets)}}
    def verify(*args,**kwargs):
        events.append('field-verify')
        if field_failure:raise ValueError('Matrix physical terminal connectivity was lost')
    module_stub(monkeypatch,'field_contract',capture=capture,verify_native=verify)
    board=Board();before=snap(board);cfg={'parts':[{'ref':'U_MCU','region':'controller'}],'plane_nets':['GND']}
    _,result=fn(board,cfg,deadline=time.monotonic()+200,complete_matrix=True)
    return result,original,events,board,before


def test_full_ordered_handoff_preserves_collector_receipt_after_field_native_gate(monkeypatch):
    result,original,events,_,_=ordered_scene(monkeypatch)
    assert result['accepted'] and result['interface'] is original
    assert events[-2:]==['field-capture','field-verify']


def test_full_ordered_handoff_rejects_absent_original_receipt(monkeypatch):
    result,_,_,board,before=ordered_scene(monkeypatch,receipt=False)
    assert not result['accepted'] and 'receipt' in result['reason'].lower()
    assert 'interface' not in result and snap(board)==before


@pytest.mark.parametrize('scope,failure',[('key-field',False),('matrix-with-controller',True)])
def test_matrix_report_success_cannot_bypass_full_native_field_gate(monkeypatch,scope,failure):
    result,_,_,board,before=ordered_scene(monkeypatch,scope=scope,field_failure=failure)
    assert not result['accepted'] and 'interface' not in result and 'matrix_contract' not in result
    assert snap(board)==before


@pytest.mark.parametrize('terminal_nets',[('ROW',),('COL',),('ROW','COL','EXTRA')])
def test_full_ordered_field_gate_requires_every_and_only_matrix_net(monkeypatch,terminal_nets):
    result,_,_,board,before=ordered_scene(monkeypatch,terminal_nets=terminal_nets)
    assert not result['accepted'] and 'interface' not in result and snap(board)==before


def test_partial_ordered_path_still_captures_strict_live_interfaces(monkeypatch):
    fn,_,_=caller(monkeypatch,'native',matrix_failure=True);captures=[]
    fn.__globals__['extend_controller_bundles']=lambda *a,**kw:{'accepted':True,'ports':[]}
    fn.__globals__['apply_exit_assignment']=lambda board,cfg,*a,**kw:(deepcopy(cfg),{'ports':[]})
    def capture(*args,**kwargs):captures.append(kwargs);return {'schema':1}
    fn.__globals__['capture_interfaces']=capture
    cfg={'parts':[{'ref':'U_MCU','region':'controller'}],'plane_nets':['GND']}
    _,result=fn(Board(),cfg,deadline=time.monotonic()+200)
    assert result['accepted'] and captures and 'retire_completed' not in captures[0]
