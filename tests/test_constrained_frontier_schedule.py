"""Host scheduling only; native geometry/source proof is a separate gate."""
import ast
import inspect
import itertools
import math
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest


class Item:
    def __init__(self,uid):self.m_Uuid=SimpleNamespace(AsString=lambda:uid)


class Pad(Item):
    def GetNumber(self):return '1'
    def GetParentFootprint(self):return SimpleNamespace(GetReference=self.m_Uuid.AsString)


class Clock:
    def __init__(self):self.now=0.
    def monotonic(self):return self.now


def scenario(monkeypatch,decision,*,source_count=1,field_count=3,live=True,escaped=False,penalty=False,deadline=100.,separate_field=False,field_origin=10000000,reverse_groups=False):
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/native_graph.py'
    node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='route_components')
    clock=Clock();calls=[];parents=[0,0,2]+([0 if escaped else 3] if escaped or separate_field else [])
    nodes=[{'net':'signal','item':Pad('source'),'points':[(0,0)],'shapes':{0:None},'zone':False},
           {'net':'signal','item':Item('tip'),'points':[(0,0)],'shapes':{0:None},'zone':False},
           {'net':'signal','item':Pad('field'),'points':[(10000000,0)],'shapes':{0:None},'zone':False},
           {'net':'signal','item':Pad('already-attached-field'),'points':[(1000000,0)],'shapes':{0:None},'zone':False}]
    if not escaped and not separate_field:nodes.pop()
    if separate_field:nodes[3]['points']=[(-2000000,0)]
    def find(i):
        while parents[i]!=i:i=parents[i]
        return i
    def join(i,j):parents[find(i)]=find(j)
    groups={0:[0,1]+([3] if escaped else []),2:[2]}
    if separate_field:groups[3]=[3]
    if reverse_groups:groups=dict(reversed(list(groups.items())))
    graph={'nodes':nodes,'groups':groups,'find':find,'join':join};graph_reads=[]
    def read_graph(*args,**kwargs):graph_reads.append(True);return graph
    source=[((i*1000,0),0) for i in range(source_count)]
    field=[((field_origin+i*1000,0),0) for i in range(field_count)]
    port={'net':'signal','pad_ids':['source'],'terminal_id':'tip','added_ids':['tip']}
    policy=SimpleNamespace(ports=[port],live_ports=[port] if live else [],contacts=lambda graph,indices,toward:set(source if 0 in indices else field if 2 in indices else [((-2000000,0),0)]))
    router=SimpleNamespace(port_policy=policy,launch_policy=None,deadline=deadline,grid_step=500000,
                           connection_budget_s=6,search_budget_s=6,routing_penalty=(object() if penalty else None),allow_penalty_fallback=True)
    router.expired=lambda:clock.now>=router.deadline
    router.refresh_interfaces=lambda graph:None
    def search(candidate,starts,targets,net):
        row={'from_source':starts[0][0][0]<5000000,'sources':len(starts),'targets':len(targets),
             'deadline':candidate.deadline,'grid_nm':candidate.grid_step,'penalized':candidate.routing_penalty is not None,'started':clock.now}
        calls.append(row);ok,reason,elapsed=decision(row,len(calls));clock.now+=elapsed
        candidate.last_search={'reason':reason,'expanded':len(calls)};return ok
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.grid_search',SimpleNamespace(search_many=search))
    ns={'__name__':'smilepcb.keyboard_routing._host_schedule','__package__':'smilepcb.keyboard_routing',
        'p':SimpleNamespace(PAD=Pad),'itertools':itertools,'time':clock,'distance':math.dist,
        'copper_graph':read_graph}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    route=ns['route_components']
    def run(finer=(),**options):
        # Baseline exercises its real old call contract rather than failing on
        # a missing keyword; the RED must show actual scheduling behavior.
        kw={'finer_grid_steps':finer} if 'finer_grid_steps' in inspect.signature(route).parameters else {}
        if 'prefer_small_goal' not in inspect.signature(route).parameters:options.pop('prefer_small_goal',None)
        return route(None,router,{'signal'},**kw,**options)
    return SimpleNamespace(run=run,router=router,calls=calls,clock=clock,graph_reads=graph_reads)


def failed(row,index):return False,'frontier_exhausted',.1


def test_unescaped_physical_interface_takes_direction_priority_even_with_more_contacts(monkeypatch):
    case=scenario(monkeypatch,failed,source_count=3,field_count=1)
    case.run();assert case.calls[0]['from_source'],case.calls


def test_smaller_component_starts_first_when_no_interface_is_live(monkeypatch):
    case=scenario(monkeypatch,failed,live=False)
    case.run();assert case.calls[0]['sources']==1,case.calls


def test_source_already_attached_to_field_does_not_override_smaller_component(monkeypatch):
    case=scenario(monkeypatch,failed,source_count=3,field_count=1,escaped=True)
    case.run();assert not case.calls[0]['from_source'],case.calls


def test_reverse_uses_the_primary_absolute_connection_end(monkeypatch):
    case=scenario(monkeypatch,lambda row,index:(False,'search_budget',4 if index==1 else 1))
    case.run();assert len(case.calls)==2,case.calls
    assert [q['deadline'] for q in case.calls]==[6,6],case.calls
    assert case.router.deadline==100


def test_preferences_and_reverse_do_not_refresh_the_connection_window(monkeypatch):
    case=scenario(monkeypatch,lambda row,index:(False,'frontier_exhausted',1),penalty=True)
    penalty=case.router.routing_penalty;case.run()
    assert [q['penalized'] for q in case.calls]==[True,False,True,False],case.calls
    assert max(q['deadline'] for q in case.calls)==6,case.calls
    assert case.router.deadline==100 and case.router.routing_penalty is penalty


@pytest.mark.parametrize('raise_on',[1,2])
def test_failed_search_restores_the_router_deadline_and_preference(monkeypatch,raise_on):
    def fault(row,index):
        if index==raise_on:raise RuntimeError('controlled search error')
        return False,'frontier_exhausted',1
    case=scenario(monkeypatch,fault,penalty=True);penalty=case.router.routing_penalty
    with pytest.raises(RuntimeError):case.run()
    assert case.router.deadline==100 and case.router.routing_penalty is penalty


def test_connection_expiry_cannot_launch_a_new_reverse_request(monkeypatch):
    case=scenario(monkeypatch,lambda row,index:(False,'search_budget',6))
    case.run();assert len(case.calls)==1 and case.router.deadline==100,case.calls


def test_coarse_frontier_exhaustion_defers_broad_reverse_when_finer_steps_remain(monkeypatch):
    case=scenario(monkeypatch,failed)
    case.run((250000,125000))
    assert len(case.calls)==1 and case.calls[0]['from_source'],case.calls


def test_final_resolution_retains_bounded_opposite_direction(monkeypatch):
    def search(row,index):return (not row['from_source']),'connected' if not row['from_source'] else 'frontier_exhausted',.1
    case=scenario(monkeypatch,search)
    assert case.run()==1
    assert [q['from_source'] for q in case.calls]==[True,False],case.calls
    assert case.calls[0]['deadline']==case.calls[1]['deadline']==6


def test_existing_finer_resolution_is_reached_before_broad_coarse_flood(monkeypatch):
    def search(row,index):
        if not row['from_source']:return False,'search_budget',6
        return row['grid_nm']==125000,'connected' if row['grid_nm']==125000 else 'frontier_exhausted',.1
    case=scenario(monkeypatch,search,deadline=6)
    steps=(500000,250000,125000);joined=0
    for i,step in enumerate(steps):
        if case.router.expired():break
        case.router.grid_step=step;joined+=case.run(steps[i+1:])
        if joined:break
    assert joined==1 and case.clock.now<6,case.calls
    assert [q['grid_nm'] for q in case.calls]==list(steps),case.calls


def test_direction_diagnostics_keep_actual_counts_reasons_and_deadlines(monkeypatch):
    case=scenario(monkeypatch,failed);case.run()
    attempts=case.router.graph_diagnostic['attempts'];searches=attempts[0].get('searches',[])
    assert [(q['sources'],q['targets']) for q in searches]==[(1,3),(3,1)],attempts
    assert all(q['search']['reason']=='frontier_exhausted' and q['connection_deadline']==6 and q['elapsed_s']>0 for q in searches),searches


def test_prior_join_reclassifies_the_current_source_component_before_another_field(monkeypatch):
    def search(row,index):return index==1,'connected' if index==1 else 'frontier_exhausted',.1
    case=scenario(monkeypatch,search,source_count=3,field_count=6,separate_field=True,field_origin=1000000)
    case.run()
    assert case.calls[0]['sources']==3 and case.calls[0]['targets']==6,case.calls
    assert case.calls[1]['sources']==1,case.calls


@pytest.mark.parametrize('steps',[(0,),(-1,),(True,),(1.5,),(500000,),(1000000,),(250000,250000),(125000,250000)])
def test_invalid_remaining_ladder_is_rejected_before_reading_native_graph(monkeypatch,steps):
    case=scenario(monkeypatch,failed)
    with pytest.raises(ValueError):case.run(steps)
    assert not case.graph_reads and not case.calls
