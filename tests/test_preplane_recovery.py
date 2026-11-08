"""Exercise the production routing caller with native workers isolated."""
import json
import sys
import time
from pathlib import Path
import pytest
from smilepcb.stm32 import route
from smilepcb.keyboard_routing import generated_provenance as gp
from test_generated_provenance import setup_board,write_board,U,G,N


def proof(gaps):
    return {'pad_group_gaps':gaps,'pad_groups_missing':{'SIG':1} if gaps else {}}


def test_preplane_stall_uses_latest_owned_copper_and_preserves_hard_ids(tmp_path):
    project=tmp_path/'project';project.mkdir();board=setup_board(project);(project/'.smilepcb-field.json').write_text('{}')
    events=[]
    def native(script,request,log,**kwargs):
        events.append(request['operation'])
        path=Path(request['board'])
        if request['operation']=='complete':return {'accepted':False,**proof(1)}
        if request['operation']=='joint_repair':
            assert request['generated_ids']==[G]
            assert request['protected_ids']==[U]
            assert not gp.has_zones(path)
            write_board(path,[U,N]);return {'accepted':True,**proof(0)}
        return proof(0)
    def call(operation,tag=None,**kwargs):
        path=kwargs.pop('board_override',board)
        return gp.tracked_worker(native,'bridge',dict(operation=operation,board=str(path),**kwargs),tmp_path/'log')
    result=route.recover_preplane_signals(project,tmp_path/'attempts',proof(1),call,
        plane_nets=['GND'],deadline=time.perf_counter()+200,protected_ids=[U])
    assert result['pad_group_gaps']==0 and gp.generated_ids(board)==[N]
    assert events==['complete','joint_repair','verify']


def test_no_joint_attempt_for_filled_or_plane_only_input(tmp_path):
    board=setup_board(tmp_path);write_board(board,[U,G],zones=True)
    result=route.recover_preplane_signals(tmp_path,tmp_path/'attempts',proof(1),lambda *a,**k:pytest.fail('filled board entered preplane recovery'),plane_nets=['GND'],deadline=time.perf_counter()+200)
    assert result==proof(1)
    write_board(board,[U,G])
    plane={'pad_group_gaps':1,'pad_groups_missing':{'GND':2}}
    assert route.recover_preplane_signals(tmp_path,tmp_path/'attempts',plane,lambda *a,**k:pytest.fail('plane-only attempted signal repair'),plane_nets=['GND'],deadline=time.perf_counter()+200)==plane


def test_cold_route_invokes_joint_before_first_plane_fill(tmp_path,monkeypatch):
    source=tmp_path/'placed'/'trial'/'project';source.mkdir(parents=True);setup_board(source)
    (source/'.smilepcb-field.json').write_text('{}')
    (source/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':['GND'],'layout':{'rgb_order':[1]}}))
    (source/'keyboard.kicad_pro').write_text(json.dumps({'board':{'design_settings':{'rules':{'min_track_width':0.15}}}}))
    from smilepcb import lab_tools
    events=[];closed=False
    def worker(script,request,log,**kwargs):
        nonlocal closed
        operation=request['operation'];events.append(operation)
        if operation=='joint_repair':
            assert 'planes' not in events
            assert request['generated_ids']==[G] and request['protected_ids']==[U]
            write_board(Path(request['board']),[U,N]);closed=True
        return {'accepted':operation!='complete','generation_profile':route.PROFILE,
            'field_generation_profile':route.PROFILE,'scope':'matrix-with-controller',
            'controller_connected':True,**proof(0 if closed else 1)}
    monkeypatch.setattr(route.subprocess,'run',lambda *a,**k:pytest.fail('unexpected global fallback'))
    monkeypatch.setattr(route,'workspace',lambda:tmp_path)
    monkeypatch.setattr(route,'load_tools',lambda p:{})
    monkeypatch.setattr(route,'configure_kicad',lambda *a:None)
    monkeypatch.setattr(route,'ordered_nets',lambda cfg:[('rgb',['SIG'])])
    monkeypatch.setattr(route,'write_manifest',lambda *a,**k:{})
    monkeypatch.setattr(lab_tools,'worker',worker)
    monkeypatch.setattr(lab_tools,'rule_checks',lambda *a,**k:{'erc':{'severity':{}},'drc':{'severity':{},'schematic_parity':[]}})
    monkeypatch.setattr(sys,'argv',['route.py','tools','routed','placed','trial'])
    route.main()
    result=json.loads((tmp_path/'routed'/'result.json').read_text())
    assert result['independent']['pad_group_gaps']==0
    assert events.index('complete')<events.index('joint_repair')<events.index('planes')
    assert gp.generated_ids(tmp_path/'routed'/'project'/'keyboard.kicad_pcb')==[N]

def test_lookahead_promotes_positive_ledger_with_final_board(tmp_path,monkeypatch):
    from smilepcb.keyboard_lookahead import run_lookahead
    from smilepcb import lab_tools
    project=tmp_path/'project';project.mkdir();board=setup_board(project)
    (project/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':[]}))
    (project/'.smilepcb-field.json').write_text('same field')
    out=tmp_path/'out';out.mkdir()
    def worker(script,request,log,**kwargs):
        result={'generation_profile':request['generation_profile']}
        if request['operation']=='provisional':
            assert U in request['protected_ids']
            write_board(Path(request['board']),[U,N])
            return {**result,'provisional_only':True,'accepted_final':False,'saved_trial':True,
                'before':{'SIG':1},'after':{}}
        if request['operation']=='final_frame':
            return {**result,'original_frame_preserved':True,'original_pad_partitions_preserved':True}
        return {**result,**proof(0)}
    clean={'exit_code':0,'severity':{},'schematic_parity':0,'unconnected_items':0,'types':{}}
    monkeypatch.setattr(lab_tools,'worker',worker)
    monkeypatch.setattr(lab_tools,'rule_checks',lambda *a,**k:{'erc':clean,'drc':clean})
    result=run_lookahead(project,out,proof(1),[],time.perf_counter()+600,lambda *a:None)
    assert result['pad_group_gaps']==0 and gp.generated_ids(board)==[N]
    assert (project/'.smilepcb-field.json').read_text()=='same field'
