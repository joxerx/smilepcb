"""Bounded provisional topology changes retain the final-only admission gate."""
import json,shutil,sys,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from smilepcb import keyboard_lookahead as policy


def test_temporary_regression_is_bounded_by_the_trial_envelope():
    assert policy.provisional_step({'a':1},{'b':2},seen=set(),depth=4,max_depth=8,gap_ceiling=3)['allowed']
    assert not policy.provisional_step({'a':1},{'b':4},seen=set(),depth=4,max_depth=8,gap_ceiling=3)['allowed']
    assert not policy.provisional_step({'a':1},{'b':2},seen={(('b',2),)},depth=4,max_depth=8,gap_ceiling=3)['allowed']
    assert not policy.provisional_step({'a':1},{},seen=set(),depth=8,max_depth=8,gap_ceiling=3)['allowed']
    assert not policy.provisional_step({'a':1},{'b':2},seen=set(),depth=0)['allowed']


@pytest.mark.parametrize('ceiling',[True,0,-1,9,1.5])
def test_invalid_trial_envelopes_fail_closed(ceiling):
    with pytest.raises(ValueError):policy.provisional_step({'a':1},{},seen=set(),depth=0,max_depth=8,gap_ceiling=ceiling)


def test_limits_are_explicit_and_legacy_is_unchanged():
    limits=getattr(policy,'lookahead_limits',None)
    assert callable(limits),'Explicit shared host/native limits are required'
    assert limits(False)==(3,3)
    assert limits(True)==(8,8)
    with pytest.raises(ValueError):limits('yes')


def run_sequence(tmp_path,monkeypatch,*,elastic=True,frame=True,physical_error=False):
    project=tmp_path/'project';project.mkdir();out=tmp_path/'out';out.mkdir()
    (project/'keyboard.kicad_pcb').write_text('original')
    (project/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':['return']}))
    states=[{'supply':2,'data':1},{'power_gate':1,'buffer':1,'row_a':1,'row_b':1},
            {'return':1,'column':1},{'column':1},{'supply':1},{'row_b':1},{'enable':1},{}]
    calls=[];promoted=[]
    initial={'pad_groups_missing':states[0],'pad_group_gaps':3}
    def invoke(operation,tag=None,**request):
        calls.append((operation,request))
        if operation=='provisional':
            depth=request['depth'];path=request['board']
            assert path!=project/'keyboard.kicad_pcb'
            assert (project/'keyboard.kicad_pcb').read_text()=='original'
            path.write_text('candidate-'+str(depth))
            return dict(provisional_only=True,accepted_final=False,saved_trial=True,
                        before=states[depth],after=states[depth+1],
                        strategy='plane_negotiate' if 'return' in states[depth] else 'signal_negotiate')
        if operation=='final_frame':return {'original_frame_preserved':frame,'original_pad_partitions_preserved':frame}
        if operation=='verify':return {'pad_group_gaps':0,'pad_groups_missing':{}}
        raise AssertionError(operation)
    def checks(*_args,**_kwargs):
        common={'exit_code':0,'severity':{},'schematic_parity':0,'unconnected_items':0,'types':{}}
        return {'erc':dict(common),'drc':{**common,**({'severity':{'error':1}} if physical_error else {})}}
    def transaction(src,folder,checked,previous,**_kwargs):
        assert (src/'keyboard.kicad_pcb').read_text()=='original'
        assert len([q for q in calls if q[0]=='provisional'])==7
        candidate=folder/'project';shutil.copytree(src,candidate)
        result=checked(candidate);assert result['pad_group_gaps']==0
        shutil.copyfile(candidate/'keyboard.kicad_pcb',src/'keyboard.kicad_pcb');promoted.append(True)
        return {'accepted':True,'independent':result}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_build',SimpleNamespace(NAME='keyboard',BRIDGE=Path('bridge.py'),routing_transaction=transaction))
    monkeypatch.setitem(sys.modules,'smilepcb.lab_tools',SimpleNamespace(worker=None,rule_checks=checks))
    result=policy.run_lookahead(project,out,initial,[],time.perf_counter()+1200,lambda *_:None,
                               native_call=invoke,checks_call=checks,routing_quality='turns',elastic=elastic)
    return result,calls,promoted,project,initial


def test_seven_hop_search_can_finish_but_only_after_independent_and_native_checks(tmp_path,monkeypatch):
    result,calls,promoted,project,_=run_sequence(tmp_path,monkeypatch)
    assert result['pad_group_gaps']==0 and promoted==[True]
    assert (project/'keyboard.kicad_pcb').read_text()=='candidate-6'
    hops=[request for op,request in calls if op=='provisional']
    assert all(r['elastic'] is True and r['gap_ceiling']==5 and r['routing_quality']=='turns' for r in hops)
    assert hops[2]['protect_nets'] and hops[3]['protect_nets']==[]
    assert [op for op,_ in calls][-2:]==['final_frame','verify']


def test_conservative_mode_does_not_admit_a_worsening_first_hop(tmp_path,monkeypatch):
    result,calls,promoted,project,initial=run_sequence(tmp_path,monkeypatch,elastic=False)
    assert result is initial and not promoted
    assert (project/'keyboard.kicad_pcb').read_text()=='original'
    assert len(calls)==1


def test_zero_score_without_original_frame_proof_cannot_be_promoted(tmp_path,monkeypatch):
    with pytest.raises(ValueError,match='original native frame'):
        run_sequence(tmp_path,monkeypatch,frame=False)
    assert (tmp_path/'project/keyboard.kicad_pcb').read_text()=='original'


def test_zero_score_with_a_physical_violation_cannot_be_promoted(tmp_path,monkeypatch):
    result,_,promoted,project,initial=run_sequence(tmp_path,monkeypatch,physical_error=True)
    assert result is initial and not promoted
    assert (project/'keyboard.kicad_pcb').read_text()=='original'


def test_recovery_reconsiders_topology_after_later_routing_progress(tmp_path,monkeypatch):
    from smilepcb.keyboard_recovery import recover_generated
    (tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':[]}))
    previous={'pad_group_gaps':3,'pad_groups_missing':{'supply':2,'data':1}};events=[]
    def transaction(project,folder,repair,current,**kw):
        repair(project);return {'accepted':False,'independent':current}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_build',SimpleNamespace(NAME='keyboard',routing_transaction=transaction))
    def probe(*args,**kwargs):
        elastic=kwargs.get('elastic',False);events.append(('lookahead',elastic,kwargs['routing_quality']))
        return {'pad_group_gaps':0,'pad_groups_missing':{}} if elastic else previous
    monkeypatch.setattr(policy,'run_lookahead',probe)
    def call(operation,**kwargs):
        events.append((operation,kwargs.get('strategy')))
        if operation=='verify':return previous
        if operation=='completion_candidates':return {'candidates':[]}
        return {'accepted':False,'failed_vias':[]}
    result=recover_generated(tmp_path,tmp_path,previous,[],call,lambda *_:None,time.perf_counter()+2000,routing_quality='turns')
    assert result['pad_group_gaps']==0
    assert [r for r in events if r[0]=='lookahead']==[('lookahead',False,'turns'),('lookahead',True,'turns')]
    assert events[-2]==('complete','route')
