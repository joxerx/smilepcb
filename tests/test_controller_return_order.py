"""Run the actual caller body with deterministic phase fakes, without KiCad.

Native geometry is tested separately. These controls exercise ordering, scope,
failure propagation and the caller's real rollback body, not source text order.
"""
import ast
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import ModuleType,SimpleNamespace
import sys
import time
import pytest


class Item:
    def __init__(self,uid,net='SIGNAL',number=''):
        self.uid=uid;self.net=net;self.number=number;self.m_Uuid=SimpleNamespace(AsString=lambda:uid)
    def GetNetname(self):return self.net
    def SetNet(self,net):self.net=net
    def GetNumber(self):return self.number
    def GetPosition(self):return SimpleNamespace(x=0,y=0)


class Board:
    def __init__(self):
        self.tracks=[Item('original')];self.pad=Item('pad','SIGNAL','1')
        self.fp=SimpleNamespace(Pads=lambda:[self.pad],GetReference=lambda:'U_MCU')
    def GetTracks(self):return self.tracks
    def GetFootprints(self):return [self.fp]
    def Delete(self,item):self.tracks.remove(item)
    def FindNet(self,name):return name
    def BuildConnectivity(self):pass
    def GetDesignSettings(self):return SimpleNamespace(m_TrackMinWidth=150000,m_MinClearance=150000)


def snap(board):
    return {'tracks':{t.uid:{'net':t.net} for t in board.tracks},
        'footprints':{'mcu':{'ref':'U_MCU','pads':[{'uuid':'pad','net':board.pad.net}]}}}


def caller(monkeypatch,profile,*,early_failure=False,local_error=False,local_false=False,matrix_failure=False):
    events=[];seen=[]
    def add(board,name):board.tracks.append(Item(name))
    def returns(board,**kw):
        events.append('returns');seen.append(kw);add(board,'return')
        return {'accepted':not early_failure,'reason':'return failure','ports':[],'added_ids':['return'] if not early_failure else []}
    def local(board,cfg,**kw):
        events.append('local');add(board,'local')
        if local_error:raise ValueError('local failure')
        if local_false:board.Delete(board.tracks[-1])
        return {'accepted':not local_false,'local_closed':None}
    def fixed(board,**kw):
        events.append('fixed');add(board,'fixed')
        if matrix_failure:return {'accepted':True,'ports':[],'added_ids':['fixed']}
        board.pad.SetNet('changed');board.tracks[0].SetNet('changed')
        return {'accepted':False,'reason':'fixed failure'}
    def key_data(board,cfg,**kw):
        events.append('keydata');return {'accepted':True,'ports':[],'added_ids':[]}
    def key_modules(board,cfg,**kw):
        events.append('keymodules');add(board,'keymodule')
        return {'accepted':True,'added_ids':['keymodule']}
    def matrix(board,cfg,**kw):
        events.append('matrix');add(board,'matrix')
        return cfg,{'candidate_accepted':False,'reason':'matrix failure'}
    ownership=lambda *a,**kw:SimpleNamespace(generation_profile=profile,tracks={},pads={})
    modules={
        'generation':{'current_generation_profile':lambda:SimpleNamespace(name=profile)},
        'controller_local':{'prepare_controller_local':local},
        'modules':{'seed_key_data_access':key_data,'connect_key_modules':key_modules},
        'collector_preparation':{'verified_provisional_ownership':ownership,
            'ProvisionalOwnership':lambda generation_profile,tracks,pads:SimpleNamespace(generation_profile=generation_profile,tracks=tracks,pads=pads),
            'prepare_collector_matrix':matrix},
        'interface_launches':{'reserve_launches':lambda ports:nullcontext()}}
    for name,values in modules.items():
        full='smilepcb.keyboard_routing.'+name;m=ModuleType(full);m.__dict__.update(values);monkeypatch.setitem(sys.modules,full,m)
    source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/ordered_controller.py'
    parsed=ast.parse(source.read_text());function=next(q for q in parsed.body if isinstance(q,ast.FunctionDef) and q.name=='prepare_ordered_controller')
    namespace={'__name__':'smilepcb.keyboard_routing.ordered_controller','__package__':'smilepcb.keyboard_routing',
        'time':time,'snapshot':snap,'seed_controller_returns':returns,'route_controller_ports':fixed,
        'ordered_nets':lambda cfg:[('rows',['ROW']),('columns',['COL'])],
        'mcu_signal_nets':lambda cfg:{'SIGNAL','ROW','COL'},'defaultdict':__import__('collections').defaultdict,
        'probe_gpio_access':lambda *a,**k:{'excluded_gpios':[],'uncertain_gpios':[]},
        'apply_matrix_gpio':lambda board,cfg,**kw:(cfg,{})}
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
    return namespace['prepare_ordered_controller'],events,seen


def invoke(fn,*,complete=True):
    board=Board();cfg={'parts':[{'ref':'U_MCU','region':'controller'},{'ref':'C_AUX','region':'controller'}],'plane_nets':['GND']}
    before=snap(board);model=deepcopy(cfg)
    updated,result=fn(board,cfg,deadline=time.monotonic()+220,complete_matrix=complete)
    assert snap(board)==before and cfg==model and updated==model
    return result


@pytest.mark.parametrize('profile,complete,expected',[
    ('dsn-process-v2',True,['keydata','returns','local','fixed']),
    ('dsn-process-v2',False,['local','returns','fixed']),
    ('dsn-safe',True,['keydata','returns','fixed']),('dsn-safe',False,['returns','fixed']),
    ('native',True,['keydata','returns','fixed']),('native',False,['returns','fixed'])])
def test_only_full_v2_moves_returns_before_local_with_unchanged_scope(monkeypatch,profile,complete,expected):
    fn,events,seen=caller(monkeypatch,profile);result=invoke(fn,complete=complete)
    assert events==expected and not result['accepted']
    assert seen[0]['controller_refs']==({'U_MCU','C_AUX'} if complete else {'U_MCU'})
    assert seen[0]['plane_nets']=={'GND'} and seen[0]['via_to_smd_clearance_nm']==200000


def test_failed_early_return_skips_local_and_rolls_back(monkeypatch):
    fn,events,_=caller(monkeypatch,'dsn-process-v2',early_failure=True);result=invoke(fn)
    assert events==['keydata','returns'] and result['reason']=='Controller return reservation rejected'


def test_local_exception_after_early_success_rolls_back_both(monkeypatch):
    fn,events,_=caller(monkeypatch,'dsn-process-v2',local_error=True);result=invoke(fn)
    assert events==['keydata','returns','local'] and result['reason']=='local failure'


def test_optional_false_local_result_remains_nonfatal_until_fixed_rejection(monkeypatch):
    fn,events,_=caller(monkeypatch,'dsn-process-v2',local_false=True);result=invoke(fn)
    assert events==['keydata','returns','local','fixed'] and result['reason'].startswith('Fixed controller')


def test_later_matrix_failure_rolls_back_early_returns_local_and_fixed(monkeypatch):
    fn,events,_=caller(monkeypatch,'dsn-process-v2',matrix_failure=True);result=invoke(fn)
    assert events==['keydata','returns','local','fixed','keymodules','matrix'] and result['reason'].startswith('Full collector matrix rejected')
