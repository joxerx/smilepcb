"""The prebuilt-module guard cannot repair prerequisites behind source routing."""
import ast
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
import sys
import traceback
from types import ModuleType, SimpleNamespace

import pytest


class Track:
    def __init__(self,uid,net='GND'):
        self.uid=uid;self.net=net;self.m_Uuid=SimpleNamespace(AsString=lambda:uid)
    def GetNetname(self):return self.net
    def SetNet(self,net):self.net=net


class Board:
    def __init__(self):self.tracks=[Track('retained'),Track('early-module')]
    def GetTracks(self):return self.tracks
    def GetFootprints(self):return [SimpleNamespace(GetReference=lambda:'U_MCU',GetLayer=lambda:0,Pads=lambda:[])]
    def GetDesignSettings(self):return SimpleNamespace(m_TrackMinWidth=150000,m_MinClearance=150000)
    def Delete(self,track):self.tracks.remove(track)
    def FindNet(self,net):return net
    def BuildConnectivity(self):pass


def snapshot(board):return {'footprints':{},'tracks':{t.uid:{'net':t.net} for t in board.tracks}}


@pytest.mark.parametrize('guard,mutation,accepted,reaches_returns',[
    (True,None,True,True),(True,'add',True,False),(True,'rebind',True,False),
    (True,None,False,False),(False,'add',True,True)])
def test_post_source_module_guard_requires_native_acceptance_without_mutation(monkeypatch,guard,mutation,accepted,reaches_returns):
    events=[];board=Board();before=snapshot(board);clock=SimpleNamespace(monotonic=lambda:1000.0)
    def source(board,**kw):
        assert 'early-module' in snapshot(board)['tracks']
        board.tracks.append(Track('source','ROW'))
        return {'accepted':True,'ports':[],'added_ids':['source']}
    def local(board,cfg,**kw):
        events.append(('local',kw['deadline']))
        if mutation=='add':board.tracks.append(Track('late-repair'))
        elif mutation=='rebind':board.tracks[1].SetNet('CHANGED')
        return {'accepted':accepted,'added_ids':['late-repair'] if mutation=='add' else []}
    def returns(*args,**kw):events.append(('returns',kw['deadline']));return {'accepted':True}
    def matrix(*args,**kw):
        events.append(('matrix',kw['deadline']))
        return {'complete':False,'controller_connected':False,'reason':'controlled downstream rejection'}
    def install(name,**exports):
        name='smilepcb.keyboard_routing.'+name;module=ModuleType(name);module.__dict__.update(exports)
        monkeypatch.setitem(sys.modules,name,module)
    install('generation',current_generation_profile=lambda:SimpleNamespace(name='dsn-process-v2'))
    install('modules',connect_key_modules=local)
    install('power',connect_controller_return_tree=returns)
    install('conditional_banks',certify_generated_heads=lambda *args,**kw:object())
    install('interface_contract',capture_interfaces=lambda *args,**kw:{'schema':1},verify_interfaces=lambda *args,**kw:{})
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/collector_preparation.py'
    fn=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='prepare_collector_matrix')
    ns={'__name__':'smilepcb.keyboard_routing.collector_preparation','__package__':'smilepcb.keyboard_routing',
        'time':clock,'snapshot':snapshot,'transient_access_ids':lambda *a:set(),'reserve_launches':lambda *a:nullcontext(),
        'route_controller_ports':source,'ordered_nets':lambda cfg:[('rows',['ROW']),('columns',[])],
        'collector_assignment':lambda *a:({},[]),'apply_exit_assignment':lambda board,cfg,*a,**kw:(deepcopy(cfg),{'ports':[]}),
        'matrix_stage':SimpleNamespace(prepare_matrix=matrix),'p':SimpleNamespace(F_Cu=0,B_Cu=31),
        'Path':Path,'traceback':traceback,'deepcopy':deepcopy}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
    options={'require_existing_modules':True} if guard else {}
    _,result=ns['prepare_collector_matrix'](board,{'plane_nets':['GND'],'parts':[{'ref':'U_MCU','region':'controller'}]},controller_refs={'U_MCU'},
        nets={'ROW'},targets={},fixed_ports=[],deadline=1200,**options)
    assert not result['candidate_accepted'] and snapshot(board)==before
    assert any(name=='returns' for name,_ in events)==reaches_returns,events
    assert all(stop<=1188 for name,stop in events if name=='local')
    if guard and mutation:
        assert all('changed prebuilt local modules' in trial['reason'] for trial in result['trials']),result
    elif not accepted:
        assert all('Incomplete local key prerequisites' in trial['reason'] for trial in result['trials']),result
    else:assert any(name=='matrix' for name,_ in events),events
