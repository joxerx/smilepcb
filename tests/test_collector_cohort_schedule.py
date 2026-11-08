"""Controlled-clock collector orchestration; physical admission stays native."""
import ast
from collections import deque
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from collector_partition_support import Board, capacity, rail_ports


def run_schedule(groups,*,deadline=30,compression_cost=0,accept_first=False,source=None,setup_cost=0,proof_cost=None,ports=None):
    clock=SimpleNamespace(now=0);clock.monotonic=lambda:clock.now
    calls=[];compressions=[];proof=[];proved=set()
    class Pad:
        def GetNumber(self):return '1'
    ports=ports or [q for group in groups for q in rail_ports(group,[i*500000 for i in range(len(group))])]
    all_nets={q['net'] for q in ports}
    def compress(board,live,nets,*,deadline,**kwargs):
        assert {q['net'] for q in live} == all_nets-proved
        compressions.append((list(nets),clock.now,deadline))
        clock.now=min(deadline,clock.now+compression_cost)
        return live,{'accepted':True}
    class BoundedSearch:
        def __init__(self,board,cfg_ports,nets,targets,*,deadline,goals):
            assert {q['net'] for q in cfg_ports} == all_nets-proved
            self.nets=list(nets);self.deadline=deadline;self.branch=None;self.ports=cfg_ports;self.interface_graph=None
            clock.now+=setup_cost
        def solve(self):
            calls.append((self.nets[:],clock.now,self.deadline))
            if proof_cost is not None:
                if clock.now+proof_cost<=self.deadline:
                    clock.now+=proof_cost;leaf=self.nets[0];proved.add(leaf);self.branch={'net':leaf}
                    return {'accepted':True,'nets':self.nets[:],'ports':{q['net']:q for q in self.ports}}
                clock.now=max(clock.now,self.deadline)
            else:clock.now=self.deadline
            if accept_first and not proved:
                leaf=self.nets[0];proved.add(leaf);self.branch={'net':leaf}
                return {'accepted':True,'nets':self.nets[:],'ports':{q['net']:q for q in self.ports}}
            return {'accepted':False,'nets':self.nets[:],'reason':'Controlled exhausted search'}
    def graph(board,nets,deadline):
        proof.append((clock.now,deadline))
        nodes=[{'item':Pad(),'net':net} for net in sorted(proved) for _ in range(2)]
        return {'nodes':nodes,'find':lambda i:i//2}
    ns=capacity(clock)
    ns.update({'time':clock,'deepcopy':deepcopy,'deque':deque,
        'snapshot':lambda b:{'tracks':{},'footprints':{}},
        'field_targets':lambda *a:{},'groups_for':lambda *a:deepcopy(groups),
        'compress':compress,'branch_goals':lambda *a:[SimpleNamespace(point=(0,0))],
        'CollectorSearch':BoundedSearch,'copper_graph':graph,'publish_launches':lambda *a:None,
        'p':SimpleNamespace(PAD=Pad,F_Cu=0,B_Cu=2,FromMM=lambda mm:round(mm*1000000))})
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/collectors.py'
    tree=ast.parse(path.read_text() if source is None else source);functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)]
    exec(compile(ast.Module(body=functions,type_ignores=[]),str(path),'exec'),ns)
    result=ns['route_collectors'](Board(),{},ports,deadline=deadline)
    return result,calls,compressions,proof


def test_all_original_cohorts_get_a_first_opportunity_before_failed_subdivisions():
    groups=[['a','b','c','d'],['isolated-a'],['isolated-b']]
    result,calls,_,proof=run_schedule(groups)
    assert [row[0] for row in calls[:3]]==groups,calls
    assert any(row[0]==['isolated-b'] for row in calls),calls
    assert proof and proof[0][0]<30 and proof[0][1]==30
    assert not result['accepted']


def test_cohort_windows_keep_full_existing_caps_and_shared_absolute_end():
    groups=[['a','b','c','d'],['isolated-a'],['isolated-b']]
    _,calls,_,proof=run_schedule(groups)
    assert calls[0][1:]==(0,8),calls
    assert calls[1][0]==['isolated-a'] and calls[1][2]-calls[1][1]==4,calls
    assert all(0<=end-start<=(8 if len(nets)>1 else 4) for nets,start,end in calls),calls
    assert all(end<=25.5 for nets,start,end in calls),calls
    assert proof[0][0]<=25.5


def test_compression_and_native_search_share_one_cohort_window():
    groups=[['a','b','c','d'],['isolated-a'],['isolated-b']]
    _,calls,compressions,_=run_schedule(groups,compression_cost=1)
    assert compressions[0][1]==0
    assert calls[0][1:]==(1,8),calls
    assert calls[1][0]==['isolated-a'],calls


def test_expired_cohort_stage_does_not_attempt_or_compress_any_source():
    result,calls,compressions,proof=run_schedule([['a','b'],['c']],deadline=0)
    assert not result['accepted'] and not calls and not compressions and not proof


def test_successful_first_peel_yields_to_other_original_cohorts_before_continuing():
    groups=[['a','b','c','d'],['isolated-a'],['isolated-b']]
    result,calls,_,_=run_schedule(groups,accept_first=True)
    assert [row[0] for row in calls[:3]]==groups,calls
    assert result['closed_nets']==['a'],result
    assert any(row[0]==['b','c','d'] for row in calls[3:]),calls


def test_fixed_setup_and_native_proof_fit_whole_attempts_instead_of_fractional_slices():
    groups=[[str(i)] for i in range(12)]
    result,calls,_,proof=run_schedule(groups,deadline=12,setup_cost=.5,proof_cost=1)
    assert len(result['closed_nets'])>=4,(result,calls)
    assert all(end-start+.5>=4 for nets,start,end in calls),calls
    assert proof and proof[0][0]<=10.2 and proof[0][1]==12,proof


def test_insufficient_tail_is_retained_instead_of_spent_on_subquantum_attempts():
    result,calls,_,proof=run_schedule([['a'],['b'],['c']],deadline=10)
    assert len(calls)==2 and all(end-start==4 for nets,start,end in calls),calls
    assert any(q['nets']==['c'] for q in result['failures']),result
    assert proof[0][0]==8 and proof[0][1]==10,proof


def test_upfront_partition_replaces_parent_in_place_before_ordinary_scheduler():
    groups=[['z','y','x'],['b','a']]
    ports=rail_ports(groups[0],[0,625000,5125000])+rail_ports(groups[1],[0,500000])
    result,calls,compressions,proof=run_schedule(groups,ports=ports)
    assert result['initial_groups']==[['z','y'],['x'],['b','a']]
    assert [row[0] for row in calls[:3]]==[['z','y'],['x'],['b','a']]
    assert result['history'][0]['kind']=='partition'
    assert calls[0][1:]==(0,8) and calls[1][1:]==(8,12)
    assert compressions[0][0]==['z','y']
    assert all(end<=25.5 for _,_,end in calls) and proof[0][1]==30


def test_existing_short_caller_budget_gives_first_child_a_turn_without_extension():
    groups=[['a','b','c']]
    ports=rail_ports(groups[0],[0,625000,5125000])
    result,calls,_,proof=run_schedule(groups,ports=ports,deadline=6)
    assert calls==[(['a','b'],0,5)]
    assert any(row['nets']==['c'] and row['reason']=='Shared collector deadline' for row in result['failures'])
    assert proof==[(5,6)] and not result['accepted']


def test_observed_compact_first_bank_retains_exact_initial_and_pending_schedule():
    groups=[['a','b','c','d'],['e'],['f']]
    ports=rail_ports(groups[0],[0,500000,1000000,1750000])+rail_ports(['e','f'],[0,500000])
    result,calls,compressions,proof=run_schedule(groups,ports=ports,deadline=6)
    assert result['initial_groups']==groups
    assert calls==[(groups[0],0,5)] and compressions==[(groups[0],0,3)]
    assert [row['nets'] for row in result['failures']]==[['e'],['f'],['a','b'],['c','d']]
    assert [row['kind'] for row in result['history']]==['pitch','peel']
    assert proof==[(5,6)]
