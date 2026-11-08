"""Execute the actual planner orchestration with portable Native boundary fakes."""
import ast,collections,math,time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import pytest


class World:
    last=None
    def __init__(self,*args,**kwargs):
        World.last=self;self.operations=[];self.track_width=100;self.via_width=300;self.drill=200
        self.minimum_clearance=100;self.net_clearances={};self.added=[];self.restores=[]
    def checkpoint(self):return deepcopy(self.operations)
    def restore(self,mark):self.operations=deepcopy(mark);self.restores.append(deepcopy(mark))
    def add_path(self,path,layer,net):
        for a,b in zip(path,path[1:]):self.operations.append(dict(kind='track',net=net,layer=layer,start=tuple(a),end=tuple(b),width=self.track_width))
    def add_via(self,point,net):self.operations.append(dict(kind='via',net=net,position=tuple(point),width=self.via_width,drill=self.drill))
    def clear(self,*args):return True


def run_case(monkeypatch,*,menu=True,reject_join=False):
    source=Path(__file__).parents[1]/'smilepcb/keyboard_routing/obstacle_bundles.py'
    definitions=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in ('plan_obstacle_group','_route_from_operations')]
    before=dict(tracks={'old0':{},'old1':{}},footprints={'unchanged':True})
    # Any original-pad geometry query during staged body placement is a bug.
    board=NS(GetFootprints=lambda:pytest.fail('New outlet inherited original opposite-face MCU land lead'))
    group=[dict(net=str(i),ids=['old'+str(i)],width=100,heading=(1,0),pad_position=(0,i*1000),mcu_ref='package',mcu_pad=str(i),
        window=dict(source=(0,i*1000),source_layer=2,target=(30000,i*1000),target_layer=0,
            prefix_legs=[],suffix_legs=[(0,[(30000,i*1000),(31000,i*1000)])],
            fixed_legs=[(0,[(30000,i*1000),(31000,i*1000)])],fixed_vias=[])) for i in range(2)]
    joins=[];assemblies=[];observed={}
    def physical(world,route,a,z,net,bounds,**kwargs):
        for face,path in route['legs']:world.add_path(path,face,net)
        for point in route['vias']:world.add_via(point,net)
        return True
    def connect(world,a,z,la,lz,net,bounds,deadline,departure_heading=None,admit_continuation=None):
        joins.append((net,tuple(a),tuple(z),la,lz));assert deadline==observed['deadline']
        if reject_join and departure_heading is not None:return False
        route=dict(legs=[(la,[tuple(a),tuple(z)])],vias=[])
        if admit_continuation is not None:assert admit_continuation(route)
        world.add_path(route['legs'][0][1],la,net);return True
    def ports(a,targets,h,bounds,**kwargs):
        observed['source_mean']=a;observed['head_ops_before_body']=deepcopy(World.last.operations)
        return [((a[0]+5000,a[1]),(a[0]+10000,a[1]))] if menu else []
    def expanded(edges,names,offsets,width):
        return {net:[dict(kind='track',net=net,layer=0,path=[(edges[0]['start'][0],edges[0]['start'][1]+offsets[net]),
            (edges[-1]['end'][0],edges[-1]['end'][1]+offsets[net])],width=width)] for net in names}
    def add(world,op):world.add_path(op['path'],op['layer'],op['net']);return True
    def search(a,z,**kwargs):
        edges=[dict(kind='track',start=a,end=z,layer=0,end_layer=0)]
        accepted=kwargs['goal_accept'](edges)
        return dict(accepted=accepted,edges=edges,reason=None if accepted else 'rejected')
    def safe(route,*args):assemblies.append(deepcopy(route));return True
    namespace=dict(__package__='smilepcb.keyboard_routing',time=time,math=math,Counter=collections.Counter,
        snapshot=lambda board:deepcopy(before),VirtualCopper=World,CompleteChoice=None,
        _source_land_metadata=lambda w,row:dict(source_forward_extent_nm=900,source_backward_extent_nm=1500),
        _physical_connection=physical,_connect=connect,_expanded=expanded,_add_checked=add,
        _Portals=lambda *args:NS(propose=lambda *args:None),port_pairs=ports,search_corridor=search,
        safe_route=safe,_own_contacts_clear=lambda *a,**k:True,clean=lambda path:path,
        project_operations=lambda *args:{},vec=lambda point:point,
        p=NS(F_Cu=0,B_Cu=2,SHAPE_SEGMENT=lambda *args:object(),SHAPE_CIRCLE=lambda *args:object()))
    exec(compile(ast.Module(body=definitions,type_ignores=[]),str(source),'exec'),namespace)
    deadline=time.monotonic()+2;observed['deadline']=deadline
    result=namespace['plan_obstacle_group'](board,group,bounds=(-10000,-20000,50000,20000),preferred_layer=0,
        protected_ids=(),deadline=deadline,source_strategy='staged-joint')
    return result,joins,assemblies,observed


def test_complete_staged_plan_joins_actual_outlets_and_assembles_head_vias(monkeypatch):
    result,joins,assemblies,observed=run_case(monkeypatch)
    assert result['planned'] and result['accepted_source_strategy']=='staged-joint'
    heads=result['staged_source_heads'];assert heads['accepted']
    assert result['source_attachment_origin']=='admitted-head-outlets'
    assert observed['source_mean'][0]>0 # actual new head outlets, not original pad center
    for outlet in heads['outlets']:
        assert any(net==outlet['net'] and start==tuple(outlet['exit_nm']) and face==outlet['exit_layer'] for net,start,end,face,lastface in joins)
    assert any(route['vias'] for route in assemblies)
    # Every complete route that reaches a fixed field suffix includes its new via.
    for route in assemblies:
        if route['legs'] and route['legs'][-1][1][-1][0]==31000:assert len(route['vias'])==1
    assert any(op['kind']=='via' and not op.get('fixed') for op in result['operations'])


def test_heads_alone_are_not_a_plan_when_no_corridor_menu_exists(monkeypatch):
    result,joins,assemblies,observed=run_case(monkeypatch,menu=False)
    assert result['staged_source_heads']['accepted'] and not result['planned']
    assert 'operations' not in result and result['accepted_source_strategy'] is None
    assert not joins


def test_rejected_body_join_restores_admitted_heads_for_next_order(monkeypatch):
    result,joins,assemblies,observed=run_case(monkeypatch,reject_join=True)
    assert not result['planned'] and result['staged_source_heads']['accepted']
    heads=[op for op in observed['head_ops_before_body'] if not op.get('fixed')]
    assert heads
    assert sum(all(op in mark for op in heads) for mark in World.last.restores)>=2
    assert all(start!=(0,0) for net,start,end,face,lastface in joins)
