"""Exercise the executable report flow without loading a Native board."""
import hashlib,importlib,json,runpy,sys,time
from copy import deepcopy
from types import SimpleNamespace
import pytest

MODULE='smilepcb.keyboard_routing.finalized_research'

@pytest.fixture
def boundary(tmp_path,monkeypatch):
    path=tmp_path/'board.kicad_pcb';original=b'immutable source';path.write_bytes(original)
    before=dict(tracks={'plane-hit':dict(locked=False,net='earth')},footprints={})
    board=SimpleNamespace(GetProject=lambda:object(),GetDesignSettings=lambda:object(),
        GetFootprints=lambda:[SimpleNamespace(GetReference=lambda:'MCU',GetPosition=lambda:SimpleNamespace(x=0,y=0))],
        Zones=lambda:[SimpleNamespace(GetIsRuleArea=lambda:False,m_Uuid=SimpleNamespace(AsString=lambda:'zone'))])
    rows=[dict(net=name,prefix=False) for name in ('pulse-a','pulse-b')];loaded=[]
    def load(name):loaded.append(name);return board
    def inventory(b,nets,**kwargs):
        assert nets=={'pulse-a','pulse-b'} and kwargs['deadline']>time.monotonic()
        return deepcopy(rows),dict(eligible_nets=2)
    def prefix(b,row,protected):return dict(row,prefix=True)
    def witness(b,group,axis,**kwargs):
        assert all(row['prefix'] for row in group)
        return dict(witness_ids=['plane-hit'],carriers=[{'eligible':True}])
    def planner(b,group,**kwargs):
        assert kwargs['plane_witness_ids']==['plane-hit']
        assert kwargs['source_strategy']==request.get('source_strategy','sequential')
        assert kwargs['candidate_axes']==request['candidate_axes']
        assert time.monotonic()<kwargs['deadline']<=time.monotonic()+2
        return dict(planned=False,reason='Attachments rejected',source_snapshot=before)
    modules={
        'smilepcb.native_worker':SimpleNamespace(p=SimpleNamespace(F_Cu=0,LoadBoard=load),snapshot=lambda b:deepcopy(before)),
        'smilepcb.keyboard_routing.obstacle_inventory':SimpleNamespace(inventory_windows=inventory),
        'smilepcb.keyboard_routing.obstacle_bundles':SimpleNamespace(project_operations=lambda *a:pytest.fail('Rejected trial has no projected candidate')),
        'smilepcb.keyboard_routing.source_prefixes':SimpleNamespace(source_prefix_variant=prefix),
        'smilepcb.keyboard_routing.source_attachment_witnesses':SimpleNamespace(source_attachment_witnesses=witness),
        'smilepcb.keyboard_routing.finalized_bundle':SimpleNamespace(plan_finalized_group=planner),
    }
    for key,value in modules.items():monkeypatch.setitem(sys.modules,key,value)
    request=dict(board=str(path),source_sha256=hashlib.sha256(original).hexdigest(),mcu_ref='MCU',
        nets=['pulse-a','pulse-b'],budget_s=2,candidate_axes=[[dict(kind='track',layer=0,end_layer=0,start=[0,0],end=[-10,0])]],
        plane_witness_ids=['untrusted-request-value'])
    return request,path,original,loaded

def test_executable_cli_reports_rejection_and_forwards_only_derived_witnesses(boundary,tmp_path,monkeypatch):
    request,path,original,loaded=boundary;input_path=tmp_path/'request.json';report_path=tmp_path/'report.json'
    input_path.write_text(json.dumps(request));monkeypatch.setattr(sys,'argv',[MODULE,str(input_path),str(report_path)])
    runpy.run_module(MODULE,run_name='__main__')
    out=json.loads(report_path.read_text())
    assert out['plan']['planned'] is False and out['plan']['reason']=='Attachments rejected'
    assert out['collision_preflight']['witness_ids']==['plane-hit']
    assert out['source_exact'] and path.read_bytes()==original and len(loaded)==1
    assert out['quality'] is None and 'source_snapshot' not in out['plan']

@pytest.mark.parametrize('change',['stale_hash','zero_budget','excess_budget'])
def test_invalid_cli_request_cannot_load_a_board(boundary,change):
    request,path,original,loaded=boundary
    if change=='stale_hash':request['source_sha256']='0'*64
    if change=='zero_budget':request['budget_s']=0
    if change=='excess_budget':request['budget_s']=61
    with pytest.raises(ValueError):importlib.import_module(MODULE).run(request)
    assert not loaded and path.read_bytes()==original


def test_joint_request_is_forwarded_without_changing_input(boundary):
    request,path,original,loaded=boundary;request['source_strategy']='joint'
    out=importlib.import_module(MODULE).run(request)
    assert not out['plan']['planned'] and path.read_bytes()==original and len(loaded)==1


def test_unknown_source_strategy_cannot_load_board(boundary):
    request,path,original,loaded=boundary;request['source_strategy']='unknown'
    with pytest.raises(ValueError,match='strategy'):importlib.import_module(MODULE).run(request)
    assert not loaded and path.read_bytes()==original


def test_staged_request_requires_direct_before_loading_board(boundary):
    request,path,original,loaded=boundary;request['source_strategy']='staged-joint'
    with pytest.raises(ValueError,match='direct'):importlib.import_module(MODULE).run(request)
    assert not loaded


def test_staged_direct_request_is_forwarded(boundary,monkeypatch):
    request,path,original,loaded=boundary
    request.update(source_strategy='staged-joint',source_mode='direct')
    def witness(b,group,axis,**kwargs):
        assert not any(row['prefix'] for row in group)
        return dict(witness_ids=['plane-hit'],carriers=[])
    monkeypatch.setattr(sys.modules['smilepcb.keyboard_routing.source_attachment_witnesses'],'source_attachment_witnesses',witness)
    out=importlib.import_module(MODULE).run(request)
    assert not out['plan']['planned'] and out['source_exact']
