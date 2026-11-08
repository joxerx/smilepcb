"""Real geometry search and strict joint-replacement contracts, without KiCad."""
from copy import deepcopy
from pathlib import Path
import importlib
import math
import pytest


def api():
    from smilepcb.keyboard_routing import joint_repair_contract
    return joint_repair_contract


def test_narrow_channel_keeps_derived_medial_point_after_non_grid_transform():
    from smilepcb.keyboard_routing.joint_repair import visibility_points
    for dx,dy in [(0,0),(1237,-8491)]:
        circles=[{'center':(dx,dy),'radius':250000,'clearance':174000},
                 {'center':(dx,dy+1000000),'radius':250000,'clearance':174000}]
        points=visibility_points([(dx-900000,dy+500000),(dx+900000,dy+500000)],circles,
                                 bounds=(dx-2000000,dy-1000000,dx+2000000,dy+2000000),width=150000,via_width=500000)
        assert (dx,dy+500000) in points
        assert all(math.dist((dx,dy+500000),q['center'])>q['radius']+q['clearance']+75000 for q in circles)


def test_search_uses_actual_obstacles_and_keeps_alternative_same_removal_plans():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    points=[(0,0),(2,0),(4,0),(2,2),(2,-2)]
    def run(block_upper):
        def edge(a,b,layer):
            if layer!=0 or (a,b)==((0,0),(4,0)) or (a,b)==((4,0),(0,0)):return None
            if (2,0) in (a,b):return None
            if block_upper and (2,2) in (a,b):return None
            return {'path':[a,b],'blockers':{'movable'},'cost':math.dist(a,b)}
        return search_paths(points,[((0,0),0)],[((4,0),0)],edge,lambda q:None,
                            soft_ids={'movable'},deadline=float('inf'),max_results=4,via_cost=1)
    result=run(False)
    assert len({tuple(tuple(q) for leg in row['legs'] for q in leg[1]) for row in result})>=2
    assert all(row['removed']==frozenset({'movable'}) for row in result)
    changed=run(True)
    assert changed and all((2,2) not in path for row in changed for layer,path in row['legs'])


def test_search_never_treats_unknown_copper_as_movable():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    result=search_paths([(0,0),(1,0)],[((0,0),0)],[((1,0),0)],
        lambda a,b,l:{'path':[a,b],'blockers':{'user-copper'},'cost':1},lambda q:None,
        soft_ids={'owned-copper'},deadline=float('inf'))
    assert result==[]


def fixture():
    def track(net,a,b):return {'class':'PCB_TRACK','start':a,'end':b,'width':150000,'layer':0,'net':net,'locked':False}
    before={'tracks':{'spine':track('renamed',[0,0],[0,10]),'access':track('renamed',[0,0],[3,0]),
            'generated':track('return',[4,0],[5,0]),'user':track('signal',[8,0],[9,0])},
            'footprints':{'part':{'pads':[{'uuid':'terminal','net':'renamed'}]}}}
    from smilepcb.keyboard_routing.field_contract import capture
    contract=capture(before,{'complete':True,'roles':{'spine':'column_trunk','access':'column_access'},
        'owned_ids':['spine','access'],'terminals':{'terminal':['columns','renamed']}})
    after=deepcopy(before);del after['tracks']['access'];del after['tracks']['generated']
    after['tracks']['replacement']=track('renamed',[0,0],[3,0]);after['tracks']['new-return']=track('return',[4,0],[5,0])
    return before,after,contract


def replace(before,after,contract,**kwargs):
    return api().replace_field(before,after,contract,removed={'access','generated'},generated_ids={'generated'},
        protected_ids={'user'},new_roles={'replacement':'column_access'},**kwargs)


def test_owned_field_transition_keeps_trunk_and_requires_exact_new_role_coverage():
    before,after,contract=fixture();updated=replace(before,after,contract)
    from smilepcb.keyboard_routing.field_contract import validate
    assert set(validate(after,updated))=={'spine','replacement','new-return'}
    assert contract['roles']=={'spine':'column_trunk','access':'column_access'}
    after['tracks']['smuggled']=deepcopy(after['tracks']['replacement'])
    with pytest.raises(ValueError):replace(before,after,contract)


@pytest.mark.parametrize('change',['retained','source','terminal','unowned','locked','role'])
def test_transition_rejects_undeclared_mutation(change):
    before,after,contract=fixture()
    if change=='retained':after['tracks']['spine']['end']=[1,10]
    if change=='source':after['tracks']['user']['width']=200000
    if change=='terminal':after['footprints']['part']['pads'][0]['net']='wrong'
    if change=='unowned':after['tracks'].pop('user')
    if change=='locked':before['tracks']['generated']['locked']=True
    if change=='role':after['tracks']['replacement']['net']='wrong'
    with pytest.raises(ValueError):replace(before,after,contract)


def test_full_conductor_obligation_keeps_branches_and_padless_islands():
    obligations=api().component_obligations([{'pad','cut','branch','internal'},{'island-a','island-b'}],{'cut'})
    api().require_components(obligations,[{'pad','branch','internal','fresh'},{'island-a','island-b'}],added={'fresh'})
    for groups in [[{'pad','internal','fresh'},{'branch'},{'island-a','island-b'}],
                   [{'pad','branch','internal','fresh','island-a','island-b'}],
                   [{'pad','branch','internal'},{'island-a','island-b'},{'fresh'}]]:
        with pytest.raises(ValueError):api().require_components(obligations,groups,added={'fresh'})


def test_target_merge_must_include_all_original_pad_component_members():
    api().require_partitions([{'a','b'},{'c'},{'other'}],[{'a','b','c'},{'other'}],{'a','b','c'})
    with pytest.raises(ValueError):api().require_partitions([{'a','b'},{'c'}],[{'a','b','c'}],{'a','c'})


def test_late_saved_rejection_restores_board_and_receipt_exactly(tmp_path):
    board=tmp_path/'board';receipt=tmp_path/'receipt';board.write_bytes(b'old-board');receipt.write_bytes(b' old receipt\n')
    def reject():raise ValueError('saved conductor split')
    with pytest.raises(ValueError):api().publish_verified(board,receipt,b'new-board',{'new':True},reject)
    assert board.read_bytes()==b'old-board' and receipt.read_bytes()==b' old receipt\n'


def test_raw_splice_preserves_every_other_node_byte_for_byte():
    original='(kicad_pcb (version 1) (segment (uuid "old") (custom  weird)) (setup (x "\\\"kept")) (via (uuid "keep")))'
    native='(kicad_pcb (version 99) (segment (uuid "new") (start 1 2)))'
    result=api().splice_copper(original,{'old'},native,{'new'})
    assert '(setup (x "\\\"kept"))' in result and '(via (uuid "keep"))' in result and '(version 1)' in result
    assert '(version 99)' not in result and '(uuid "old")' not in result and '(uuid "new")' in result
    with pytest.raises(ValueError):api().splice_copper(original,{'keep'},native,{'keep'})


def test_joint_guard_rechecks_receipt_and_rolls_back_late_failure(monkeypatch,tmp_path):
    import json,sys
    from types import SimpleNamespace
    from smilepcb.keyboard_routing import field_contract as fc
    before,after,contract=fixture();path=tmp_path/'keyboard.kicad_pcb';receipt=tmp_path/fc.MARKER
    path.write_text(json.dumps(before));receipt.write_text(json.dumps(contract));original=(path.read_bytes(),receipt.read_bytes())
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',SimpleNamespace(p=SimpleNamespace(LoadBoard=lambda q:json.loads(Path(q).read_text())),snapshot=lambda board:board))
    monkeypatch.setattr(fc,'verify_native',lambda board,receipt,**kwargs:fc.validate(board,receipt))
    def operation(request):
        assert 'access' not in request.get('protected_ids',[])
        new=replace(before,after,contract);path.write_text(json.dumps(after));receipt.write_text(json.dumps(new))
        return {'accepted':True,'joint_field_transition':{'removed_ids':['access','generated'],'new_roles':{'replacement':'column_access'}}}
    request={'operation':'joint_repair','board':str(path),'generated_ids':['generated'],'protected_ids':['user']}
    result=fc.guarded(request,operation)
    assert result['matrix_field_preserved'] and json.loads(path.read_text())==after
    path.write_bytes(original[0]);receipt.write_bytes(original[1])
    def corrupted(request):
        result=operation(request);bad=json.loads(receipt.read_text());bad['terminals']={};receipt.write_text(json.dumps(bad));return result
    with pytest.raises(ValueError):fc.guarded(request,corrupted)
    assert (path.read_bytes(),receipt.read_bytes())==original


def test_routing_promotion_keeps_coupled_receipt_and_rolls_back_second_replace(monkeypatch,tmp_path):
    import ast,json,os,shutil,tempfile,time
    source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_build.py'
    node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='routing_transaction')
    env={'Path':Path,'shutil':shutil,'tempfile':tempfile,'os':os,'time':time,'NAME':'keyboard'}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    project=tmp_path/'input';project.mkdir();board=project/'keyboard.kicad_pcb';receipt=project/'.smilepcb-field.json'
    board.write_bytes(b'old board');receipt.write_bytes(b'old receipt')
    ledger=project/'.smilepcb-generated.json';ledger.write_bytes(b'old provenance')
    def attempt(folder):
        (folder/board.name).write_bytes(b'new board');(folder/receipt.name).write_bytes(b'new receipt');(folder/ledger.name).write_bytes(b'new provenance')
        return {'pad_group_gaps':0}
    transaction=env['routing_transaction']
    assert transaction(project,tmp_path/'success',attempt,{'pad_group_gaps':1})['accepted']
    assert board.read_bytes()==b'new board' and receipt.read_bytes()==b'new receipt' and ledger.read_bytes()==b'new provenance'
    board.write_bytes(b'old board');receipt.write_bytes(b'old receipt');ledger.write_bytes(b'old provenance');original_replace=os.replace
    def fail_board(src,dst):
        if Path(dst)==board:raise OSError('promotion failed')
        return original_replace(src,dst)
    monkeypatch.setattr(os,'replace',fail_board)
    with pytest.raises(OSError):transaction(project,tmp_path/'failure',attempt,{'pad_group_gaps':1})
    assert board.read_bytes()==b'old board' and receipt.read_bytes()==b'old receipt' and ledger.read_bytes()==b'old provenance'


def test_movable_inventory_requires_positive_ownership_and_keeps_trunks_sources():
    from smilepcb.keyboard_routing.joint_repair import movable_ids
    before,after,contract=fixture()
    assert movable_ids(before,contract,{'generated'},protected={'user'},target_net='signal')=={'access','generated'}
    assert movable_ids(before,contract,set(),protected={'access'},target_net='signal')==set()


def test_contact_expansion_is_one_hop_and_never_removes_a_whole_chain_blindly():
    from smilepcb.keyboard_routing.joint_repair import expansion_masks
    masks=expansion_masks({'hit'},{'hit':{'near','source'},'near':{'hit','far'}},{'hit','near','far'})
    assert masks==[frozenset({'hit'}),frozenset({'hit','near'})]


def test_tail_attachment_uses_finite_bus_projection_and_retains_native_properties():
    from smilepcb.keyboard_routing.joint_repair import tail_attachment
    tail={'class':'PCB_TRACK','net':'renamed','layer':2,'start':[4,0],'end':[4,10.04],'width':.15,'locked':False}
    bus={'class':'PCB_TRACK','net':'renamed','layer':2,'start':[0,10],'end':[8,10],'width':.2,'locked':False}
    assert tail_attachment(tail,[bus])==('end',(4,10))
    assert tail_attachment(tail,[{**bus,'net':'other'}]) is None
    assert tail_attachment(tail,[{**bus,'end':[3,10]}]) is None


def test_recovery_tries_owned_joint_replacement_after_signal_negotiation_stalls(monkeypatch,tmp_path):
    import ast,json,os,shutil,sys,tempfile,time
    from types import SimpleNamespace
    from smilepcb.keyboard_recovery import recover_generated
    source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_build.py'
    node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='routing_transaction')
    env={'Path':Path,'shutil':shutil,'tempfile':tempfile,'os':os,'time':time,'NAME':'keyboard'}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_build',SimpleNamespace(NAME='keyboard',routing_transaction=env['routing_transaction']))
    from smilepcb.keyboard_routing import field_contract
    monkeypatch.setattr(field_contract,'load',lambda project:{'schema':1})
    project=tmp_path/'project';project.mkdir();output=tmp_path/'output';output.mkdir()
    board=project/'keyboard.kicad_pcb';board.write_text('(kicad_pcb (version 1))')
    (project/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':[]}))
    missing={'pad_group_gaps':1,'pad_groups_missing':{'signal':1}};calls=[]
    def call(operation,**kwargs):
        calls.append((operation,kwargs))
        if operation=='joint_repair':
            assert kwargs['generated_ids']==['created'] and 'strategy' not in kwargs
            Path(kwargs['board_override']).write_text('after')
            return {'accepted':True,'pad_group_gaps':0,'pad_groups_missing':{}}
        if operation=='verify' and board.read_text()=='after':return {'pad_group_gaps':0,'pad_groups_missing':{}}
        return {'accepted':False,**missing}
    result=recover_generated(project,output,missing,[],call,lambda q:None,time.perf_counter()+1000,generated_ids=['created'])
    assert result['pad_group_gaps']==0 and board.read_text()=='after'
    kinds=[(op,row.get('strategy')) for op,row in calls]
    assert kinds.index(('complete','signal_negotiate'))<kinds.index(('joint_repair',None))


def test_generated_provenance_does_not_release_field_prerequisites():
    from smilepcb.keyboard_routing.joint_repair import movable_ids
    before,after,contract=fixture()
    contract['prerequisite_tracks']['generated']=deepcopy(before['tracks']['generated'])
    assert movable_ids(before,contract,{'generated'},protected=set(),target_net='signal')=={'access'}


def test_search_bounds_via_history_frontier_before_expensive_owned_gate():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    points=[(x,y) for x in range(9) for y in range(9)]
    def edge(a,b,layer):
        if abs(a[0]-b[0])+abs(a[1]-b[1])!=1:return None
        hits={'gate'} if min(a[0],b[0])==3 and max(a[0],b[0])==4 else set()
        return {'path':[a,b],'blockers':hits,'cost':1}
    diagnostic={}
    result=search_paths(points,[((0,0),0)],[((8,8),0)],edge,lambda point:set(),soft_ids={'gate'},
        deadline=float('inf'),max_results=1,max_states=2000,via_cost=1,displacement_cost=100000,diagnostic=diagnostic)
    assert result and result[0]['removed']=={'gate'},diagnostic
    assert diagnostic['expanded']<2000


def test_transition_contract_itself_rejects_generated_prerequisite_removal():
    before,after,contract=fixture()
    contract['prerequisite_tracks']['generated']=deepcopy(before['tracks']['generated'])
    with pytest.raises(ValueError):replace(before,after,contract)


def test_endpoint_splice_copies_original_unmodeled_properties_exactly():
    original='(kicad_pcb (version 1) (segment (start 0 0) (end 2 3) (width 0.15) (teardrops  untouched) (uuid "old")))'
    native='(kicad_pcb (segment (start 0 0) (end 2 4) (width 0.15) (uuid "new")))'
    result=api().splice_copper(original,{'old'},native,{'new'},endpoint_replacements={'old':{'new_uuid':'new','end_nm':[2000000,4000000]}})
    assert '(teardrops  untouched)' in result and '(end 2.000000 4.000000)' in result
    assert '(start 0 0)' in result and '(width 0.15)' in result and '(uuid "new")' in result
    with pytest.raises(ValueError):api().splice_copper(original,{'old'},native,{'new'},endpoint_replacements={'old':{'new_uuid':'new','layer':2}})


def test_zero_displacement_target_addition_keeps_original_receipt_roles():
    before,unused,contract=fixture();after=deepcopy(before)
    after['tracks']['bridge']={**deepcopy(before['tracks']['user']),'start':[9,0],'end':[10,0]}
    updated=api().replace_field(before,after,contract,removed=set(),generated_ids=set(),protected_ids=set(),new_roles={})
    assert updated['roles']==contract['roles'] and updated['prerequisite_tracks']=={'bridge':after['tracks']['bridge']}


def test_visibility_search_changes_real_geometry_when_a_detour_is_obstructed():
    from smilepcb.keyboard_routing.joint_repair import visibility_points,search_paths
    starts=[((-2000000,0),0)];goals=[((2000000,0),0)]
    def solve(extra):
        obstacles=[{'center':(0,0),'radius':600000,'clearance':100000}]+extra
        points=visibility_points([starts[0][0],goals[0][0]],obstacles,bounds=(-2500000,-2500000,2500000,2500000),width=150000,via_width=500000,max_points=192)
        def edge(a,b,layer):
            dx,dy=b[0]-a[0],b[1]-a[1];length=dx*dx+dy*dy
            for q in obstacles:
                x,y=q['center'];t=max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/length))
                if math.dist((x,y),(a[0]+t*dx,a[1]+t*dy))<=q['radius']+q['clearance']+75000:return None
            return {'path':[a,b],'blockers':set(),'cost':math.sqrt(length)}
        result=search_paths(points,starts,goals,edge,lambda q:None,soft_ids=set(),deadline=float('inf'),max_results=1)
        assert result
        return result[0]['legs']
    baseline=solve([])
    blocked=solve([{'center':(0,-900000),'radius':550000,'clearance':100000}])
    assert min(y for layer,path in baseline for x,y in path)<0
    assert min(y for layer,path in blocked for x,y in path)>=0
    assert baseline!=blocked


def test_recovery_uses_current_positive_ledger_and_skips_filled_inputs(tmp_path):
    from smilepcb.keyboard_recovery import joint_generated_ids
    from smilepcb.keyboard_routing.generated_provenance import record_creation
    board=tmp_path/'keyboard.kicad_pcb';uid='d3085bb2-bb4e-4d09-8c16-39e3d11d9642'
    node='(segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net 1) (uuid "'+uid+'"))'
    board.write_text('(kicad_pcb (net 1 "signal") '+node+')')
    assert joint_generated_ids(board,['historical'])==['historical']
    record_creation(board,{},creator='controlled-operation')
    assert joint_generated_ids(board,['historical'])==[uid]
    board.write_text('(kicad_pcb (net 1 "signal") '+node+' (zone (net 1)))')
    assert joint_generated_ids(board,['historical']) is None


def test_discovery_reserves_result_slots_for_distinct_removal_masks():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    a=(0,0);b=(4,0);points=[a,b,(2,1),(2,2),(2,3),(2,-1)]
    def edge(x,y,layer):
        mid=next((q for q in (x,y) if q not in (a,b)),None)
        if mid is None or (x not in (a,b) and y not in (a,b)):return None
        cost=1+mid[1]/100 if mid[1]>0 else 10
        return {'path':[x,y],'blockers':{'lower' if mid[1]>0 else 'upper'},'cost':cost}
    result=search_paths(points,[(a,0)],[(b,0)],edge,lambda q:None,soft_ids={'lower','upper'},deadline=float('inf'),max_results=3,displacement_cost=1)
    assert {q['removed'] for q in result}=={frozenset({'lower'}),frozenset({'upper'})}


def test_attempt_schedule_tries_distinct_base_geometries_before_expanding_one():
    from smilepcb.keyboard_routing.joint_repair import trial_schedule
    plans=[{'removed':frozenset({'a'}),'name':'first'},{'removed':frozenset({'b'}),'name':'second'}]
    rows=list(trial_schedule(plans,lambda plan,removed:[removed,removed|{'adjacent'}],max_trials=8))
    assert [(plan['name'],removed,reverse) for plan,removed,reverse in rows[:2]]==[
        ('first',frozenset({'a'}),False),('second',frozenset({'b'}),False)]
    assert any(removed=={'a','adjacent'} for plan,removed,reverse in rows[2:])


def test_discovery_does_not_fill_all_slots_with_one_physical_corridor():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    a=(0,0);b=(4,0);points=[a,b,(2,1),(2,2),(2,3),(2,4),(2,-1)]
    def edge(x,y,layer):
        mid=next((q for q in (x,y) if q not in (a,b)),None)
        if mid is None or (x not in (a,b) and y not in (a,b)):return None
        return {'path':[x,y],'blockers':{'neighbor-'+str(mid[1])},'cost':1+mid[1]/100 if mid[1]>0 else 10}
    result=search_paths(points,[(a,0)],[(b,0)],edge,lambda q:None,
        soft_ids={'neighbor-'+str(i) for i in (-1,1,2,3,4)},deadline=float('inf'),max_results=4,displacement_cost=1)
    assert any(min(y for layer,path in q['legs'] for x,y in path)<0 for q in result)
    assert any(max(y for layer,path in q['legs'] for x,y in path)>0 for q in result)


def test_native_result_keeps_private_owners_until_caller_finishes_without_serializing_them():
    import gc,json,weakref
    class Owner:pass
    owner=Owner();reference=weakref.ref(owner)
    result=api().NativeRepairResult({'accepted':False,'attempts':[]})
    result.retain_native(owner)
    del owner;gc.collect()
    assert reference() is not None
    assert json.loads(json.dumps(result))=={'accepted':False,'attempts':[]}
    with pytest.raises(ValueError):result.retain_native(result)
    del result;gc.collect()
    assert reference() is None


def test_joint_removal_cannot_erase_an_entire_original_padless_island():
    with pytest.raises(ValueError):api().component_obligations([{'pad','branch'},{'island-a','island-b'}],{'island-a','island-b'})


def test_restoration_region_includes_cut_contacts_and_source_launch_extents():
    from smilepcb.keyboard_routing.joint_repair import bounded_region
    for dx,dy in [(0,0),(1937,-8241)]:
        points=[(dx,dy),(dx+10000000,dy+1000000),(dx+5000000,dy+7500000),(dx+5000000,dy+9500000)]
        assert bounded_region(points,padding=2000000,max_span=40000000)==(dx-2000000,dy-2000000,dx+12000000,dy+11500000)
    with pytest.raises(ValueError):bounded_region([(0,0),(50000000,0)],padding=2000000,max_span=40000000)


def test_physical_route_dedup_ignores_leg_splitting_collinear_vertices_and_orientation():
    from smilepcb.keyboard_routing.joint_repair import physical_signature,search_paths
    assert physical_signature([(0,[(0,0),(1,0)]),(0,[(1,0),(3,0)])],[])==physical_signature([(0,[(3,0),(0,0)])],[])
    assert physical_signature([(0,[(0,0),(3,0)])],[])!=physical_signature([(2,[(0,0),(3,0)])],[])
    assert physical_signature([(0,[(0,0),(3,0)])],[])!=physical_signature([(0,[(0,0),(3,0)])],[(1,0)])
    a=(0,0);b=(3,0);bend=(1,2);points=[a,(1,0),(2,0),b,bend]
    def edge(x,y,layer):
        if bend in (x,y) and not ({x,y}&{a,b}):return None
        return {'path':[x,y],'blockers':set(),'cost':math.dist(x,y)}
    results=search_paths(points,[(a,0)],[(b,0)],edge,lambda q:None,soft_ids=set(),deadline=float('inf'),max_results=4)
    assert len({physical_signature(q['legs'],q['vias']) for q in results})==len(results)


def test_retained_component_restoration_uses_hard_visibility_with_three_vias_before_grid():
    import ast,itertools,time
    from types import SimpleNamespace
    from smilepcb.keyboard_routing.joint_repair import search_paths
    source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/joint_repair_native.py'
    node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_restore')
    board=SimpleNamespace(joined=False,vias=[])
    nodes=[{'item':SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda name=name:name))} for name in ('left','right')]
    def graph(*args,**kwargs):return {'nodes':nodes,'find':lambda i:0 if board.joined else i,'groups':{0:[0,1]} if board.joined else {0:[0],1:[1]}}
    class Router:
        def __init__(self):self.added=[]
        def refresh_interfaces(self,graph):pass
        def expired(self):return False
        def leg(self,*args):return None
    starts=[((0,0),0)];goals=[((4000000,0),2)]
    def visibility(router,starts,goals,net,region,deadline):
        assert net=='renamed'
        points=[(i*1000000,0) for i in range(5)]
        def edge(a,b,face):
            lo,hi=sorted((a[0]//1000000,b[0]//1000000))
            if hi-lo!=1 or face!=(0 if lo%2==0 else 2):return None
            return {'path':[a,b],'blockers':set(),'cost':1000000}
        plans=search_paths(points,starts,goals,edge,lambda point:set() if point[0] in (1000000,2000000,3000000) else None,
            soft_ids=set(),deadline=deadline,max_results=1,max_vias=4)
        if not plans:return False
        board.vias=plans[0]['vias'];board.joined=True
        router.added=['new-'+str(i) for i in range(len(plans[0]['legs'])+len(board.vias))]
        return True
    def grid(*args):raise AssertionError('Dense grid reached despite a feasible hard visibility connection')
    env={'math':math,'itertools':itertools,'time':time,'copper_graph':graph,'_check':lambda deadline:None,
         '_router':lambda *args:Router(),'_contacts':lambda router,graph,root,*args:starts if root==0 else goals,
         '_visibility_connection':visibility,'search_many':grid}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    before={'tracks':{'removed':{'class':'PCB_TRACK','net':'renamed','width':150000,'start':[0,0],'end':[4000000,0]}}}
    roles,steps=env['_restore'](board,[frozenset({'left','right'})],{'left':'renamed','right':'renamed'},
        {'removed'},before,{'roles':{'removed':'column_access'}},SimpleNamespace(bounds=(-1,-1,5000000,1)),{'renamed'},time.monotonic()+10)
    assert board.joined and board.vias==[(1000000,0),(2000000,0),(3000000,0)]
    assert set(roles.values())=={'column_access'} and len(roles)==7 and steps[0]['accepted']


def test_local_cut_region_does_not_include_remote_reachable_source_head():
    import ast
    from types import SimpleNamespace
    from smilepcb.keyboard_routing.joint_repair import bounded_region
    source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/joint_repair_native.py'
    selected=[n for n in ast.parse(source.read_text()).body
              if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ('_Region','_restoration_region')]
    def node(name,points):
        return {'item':SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda:name)),'points':points}
    nodes=[node('source',[(0,0),(1000000,0)]),node('trunk',[(1000000,0),(50000000,0)]),
           node('cut',[(50000000,0),(51000000,0)]),node('pad',[(51000000,0)]),node('mcu',[(0,0)])]
    graph={'nodes':nodes,'find':lambda i:0,'groups':{0:list(range(len(nodes)))}}
    env={'math':math,'bounded_region':bounded_region,'_check':lambda deadline:None,
         'nearest_to_node':lambda node,q:min(node['points'],key=lambda p:math.dist(p,q)),
         '__package__':'smilepcb.keyboard_routing'}
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(source),'exec'),env)
    plan={'legs':[(0,[(49500000,-500000),(51500000,-500000)])],'vias':[]}
    port={'pad_ids':['mcu'],'added_ids':['source'],'exit_nm':[1000000,0],
          'heading_origin_nm':[0,0],'terminal_id':'source'}
    region,diagnostic=env['_restoration_region'](graph,{'cut'},plan,[port],{'cut':{'trunk','pad'}},float('inf'))
    assert region.bounds==(47500000,-2500000,53500000,2000000)
    assert diagnostic['source_extents']==0
    # A directly exposed source fragment still includes its outward launch.
    nodes[0]['points']=[(49000000,0),(50000000,0)]
    port.update(exit_nm=[50000000,0],heading_origin_nm=[49000000,0])
    region,diagnostic=env['_restoration_region'](graph,{'cut'},plan,[port],{'cut':{'source','pad'}},float('inf'))
    assert diagnostic['source_extents']==1 and region.contains((49000000,0)) and region.contains((51000000,0))


def test_search_preserves_front_back_front_family_before_cheap_cut_variants():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    start=(0,0);end=(6,0);left=(1,0);right=(5,0)
    bends=[(3,y) for y in (-4,-3,-2,-1,1,2,3,4)]
    points=[start,end,left,right,*bends]
    def edge(a,b,face):
        if face==0:
            if {a,b} not in ({start,left},{right,end}):return None
            return {'path':[a,b],'blockers':set(),'cost':10}
        bend=next((q for q in (a,b) if q in bends),None)
        if bend is not None:
            if not ({a,b}&{start,end}):return None
            return {'path':[a,b],'blockers':{'piece-'+str(bend[1])},'cost':2+abs(bend[1])/10}
        if {a,b} not in ({start,end},{left,right},{start,right},{left,end}):return None
        return {'path':[a,b],'blockers':{'base'},'cost':math.dist(a,b)}
    owned={'base'}|{'piece-'+str(q[1]) for q in bends}
    result=search_paths(points,[(start,0),(start,2)],[(end,0),(end,2)],edge,
        lambda point:set() if point in (left,right) else None,soft_ids=owned,
        blocker_components={uid:'one-original-component' for uid in owned},
        deadline=float('inf'),max_results=6,via_cost=1,displacement_cost=.1,max_states=2000)
    assert any(row['legs'][0][0]==0 and len(row['vias'])==2 for row in result)
    families=[(row['layer_word'],row['cut_components'],row['side']) for row in result]
    first_repeat=next((i for i,value in enumerate(families) if value in families[:i]),len(families))
    assert first_repeat>=len(set(families))


def test_candidate_selection_defers_subdivided_same_component_cut_variants():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    start=(0,0);end=(10,0);bends=[(5,y) for y in (1,2,3,4,5)]
    def edge(a,b,face):
        bend=next((q for q in (a,b) if q in bends),None)
        if bend is None or not ({a,b}&{start,end}):return None
        hits={'split-'+str(bend[1])} if bend[1]<5 else {'other'}
        return {'path':[a,b],'blockers':hits,'cost':bend[1]}
    mapping={**{'split-'+str(i):'same-conductor' for i in range(1,5)},'other':'different-conductor'}
    result=search_paths([start,end,*bends],[(start,0)],[(end,0)],edge,lambda point:None,
        soft_ids=set(mapping),blocker_components=mapping,deadline=float('inf'),max_results=2,displacement_cost=0)
    assert {row['cut_components'] for row in result}=={('same-conductor',),('different-conductor',)}


def test_output_cap_does_not_stop_before_available_lower_via_layer_word():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    start=(0,0);first=(1,0);second=(2,0);front_goal=(3,0);back_goal=(4,0)
    decoys=[(2,y) for y in range(1,9)]
    def edge(a,b,face):
        pair={a,b}
        if face==0:
            if pair not in ({start,first},{start,front_goal},{second,front_goal}):return None
            cost=math.dist(a,b)
        else:
            if pair=={first,second}:cost=1
            elif pair=={second,back_goal}:cost=100
            elif first in pair and any(q in pair for q in decoys):cost=2
            else:return None
        return {'path':[a,b],'blockers':set(),'cost':cost}
    diagnostic={}
    result=search_paths([start,first,second,front_goal,back_goal,*decoys],[(start,0)],
        [(front_goal,0),(back_goal,2)],edge,lambda q:set() if q in (first,second) else None,
        soft_ids=set(),blocker_components={},deadline=float('inf'),max_results=2,max_vias=2,
        via_cost=1,diagnostic=diagnostic)
    assert [row['layer_word'] for row in result]==[(0,),(0,2)]
    assert diagnostic['possible_layer_words']==[(0,),(0,2),(0,2,0)]
    assert diagnostic['found_layer_words']==diagnostic['possible_layer_words']


def test_family_coverage_only_requires_endpoint_parity_admissible_words():
    from smilepcb.keyboard_routing.joint_repair import search_paths
    points=[(0,0),(1,0),(2,0),(3,0)];diagnostic={}
    result=search_paths(points,[(points[0],0)],[(points[-1],0)],
        lambda a,b,face:{'path':[a,b],'blockers':set(),'cost':math.dist(a,b)},
        lambda q:set() if q in points[1:-1] else None,soft_ids=set(),blocker_components={},
        deadline=float('inf'),max_results=4,max_vias=3,via_cost=1,diagnostic=diagnostic)
    assert diagnostic['possible_layer_words']==[(0,),(0,2,0)]
    assert diagnostic['found_layer_words']==diagnostic['possible_layer_words']
    assert diagnostic['missing_layer_words']==[] and diagnostic['reason']=='family_coverage'
    assert {row['layer_word'] for row in result}=={(0,),(0,2,0)}


def test_learned_foreign_blockers_get_one_immediate_fresh_mask_trial():
    from smilepcb.keyboard_routing.joint_repair import trial_schedule
    plans=[{'name':'first','removed':frozenset({'a'})},{'name':'second','removed':frozenset({'c'})}]
    def feedback(plan,removed):return removed|({'foreign'} if removed==plan['removed'] else {'another'})
    trials=list(trial_schedule(plans,lambda plan,removed:[removed,removed|{'same-net-adjacent'}],
                              feedback=feedback,max_trials=8))
    assert [(plan['name'],removed) for plan,removed,reverse in trials[:3]]==[
        ('first',{'a'}),('first',{'a','foreign'}),('second',{'c'})]
    assert not any('another' in removed for plan,removed,reverse in trials)
    assert len(trials)<=8


def test_restoration_blocker_proposal_is_owned_original_foreign_and_never_emitted(monkeypatch):
    import ast,sys,time
    from types import SimpleNamespace
    from smilepcb.keyboard_routing.joint_repair import search_paths
    source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/joint_repair_native.py'
    node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_restoration_expansion')
    live=['foreign','same-net','new-target','unknown','protected']
    board=SimpleNamespace(GetTracks=lambda:[SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u:u)) for u in live])
    router=SimpleNamespace(board=board,track_width=150000)
    before={'tracks':{u:{'net':'broken' if u=='same-net' else 'other'} for u in ['foreign','same-net','unknown','protected','removed']}}
    def relaxed(board,region,width,deadline,ignored):
        assert set(ignored)=={'foreign'}
        return SimpleNamespace(refresh_interfaces=lambda graph:None)
    def plan_geometry(world,relaxed,starts,goals,net,region,soft,deadline,**kwargs):
        assert world is router and soft=={'foreign'}
        plans=search_paths([(0,0),(3,0)],starts,goals,
            lambda a,b,face:{'path':[a,b],'blockers':{'foreign'},'cost':3},lambda point:None,
            soft_ids=soft,deadline=deadline,max_results=1)
        return plans,{'reason':'result_cap'}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.grid_search',SimpleNamespace(valid_plan=lambda *args:True))
    env={'_router':relaxed,'_plan_geometry':plan_geometry,'__package__':'smilepcb.keyboard_routing',
         '_emit':lambda *args:pytest.fail('Relaxed proposal emitted copper')}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    proposal=env['_restoration_expansion'](router,{},[((0,0),0)],[((3,0),0)],'broken',object(),
        {'foreign','same-net','removed'},before,{'removed'},time.monotonic()+1)
    assert proposal['removed_ids']==['foreign'] and proposal['legs']==[(0,[(0,0),(3,0)])]
    assert live==['foreign','same-net','new-target','unknown','protected']
    original_oracle=env['_plan_geometry']
    for invalid in (set(),{'unknown'}):
        env['_plan_geometry']=lambda *args,invalid=invalid,**kwargs:([{'removed':invalid,'legs':[],'vias':[]}],{})
        rejected=env['_restoration_expansion'](router,{},[((0,0),0)],[((3,0),0)],'broken',object(),
            {'foreign','same-net','removed'},before,{'removed'},time.monotonic()+1)
        assert rejected['removed_ids']==[]
    env['_plan_geometry']=original_oracle
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.grid_search',SimpleNamespace(valid_plan=lambda *args:False))
    rejected=env['_restoration_expansion'](router,{},[((0,0),0)],[((3,0),0)],'broken',object(),
        {'foreign','same-net','removed'},before,{'removed'},time.monotonic()+1)
    assert rejected['removed_ids']==[] and rejected['reason']=='relaxed_proposal_validation'


def test_buffered_new_cut_set_reaches_output_before_same_cut_layer_variants():
    from smilepcb.keyboard_routing.joint_repair import select_diverse_plans
    words=[(0,),(2,),(0,2),(2,0),(0,2,0),(2,0,2),(0,2,0,2),(2,0,2,0)]
    rows=[{'layer_word':word,'cut_components':('same-original-cut',),'side':1,'name':str(i)} for i,word in enumerate(words)]
    rows.append({'layer_word':(0,2,0),'cut_components':('different-cut',),'side':1,'name':'new-cut'})
    rows.append({**rows[0],'side':-1,'name':'opposite-side'})
    selected=select_diverse_plans(rows,8)
    assert [row['name'] for row in selected[:3]]==['0','new-cut','opposite-side']
    assert len(selected)==8 and len({row['layer_word'] for row in selected})==7
    complete=select_diverse_plans(rows,16)
    assert {row['name'] for row in complete}=={row['name'] for row in rows}
    assert complete[-1]['name']=='4'  # Geometry with an already represented word remains deferred.


def test_restore_feedback_restarts_original_and_restores_all_foreign_branch_obligations():
    import ast,itertools,time
    from types import SimpleNamespace
    from smilepcb.keyboard_routing.joint_repair import trial_schedule
    source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/joint_repair_native.py'
    node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_restore')
    original=[{'a-pad','a-branch','a-cut'},{'b-pad','b-padless-branch','b-cut'}]
    original_nets={u:('A' if u.startswith('a-') else 'B') for group in original for u in group}
    before={'tracks':{u:{'class':'PCB_TRACK','net':original_nets[u],'width':150000,'start':[0,0],'end':[5,0]}
                      for u in ('a-cut','b-cut')}}
    worlds=[];learned={};completed=[]
    def graph(board,*args,**kwargs):
        ids=sorted(set().union(*original)-board.removed)
        nodes=[{'item':SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u:u))} for u in ids]
        roots={i:(min(j for j,u in enumerate(ids) if original_nets[u]==original_nets[uid])
                  if original_nets[uid] in board.joined else i) for i,uid in enumerate(ids)}
        groups={root:[i for i in roots if roots[i]==root] for root in set(roots.values())}
        return {'nodes':nodes,'find':roots.__getitem__,'groups':groups}
    class Router:
        def __init__(self,board):self.board=board;self.added=[]
        def refresh_interfaces(self,graph):pass
        def expired(self):return False
        def leg(self,a,b,face,net):return [a,b] if 'b-cut' in self.board.removed else None
        def add_path(self,path,face,net):self.board.joined.add(net);self.added.append('new-'+net)
    env={'math':math,'itertools':itertools,'time':time,'copper_graph':graph,'_check':lambda deadline:None,
         '_router':lambda board,*args:Router(board),'_contacts':lambda router,graph,root,*args:[((root,0),0)],
         '_visibility_connection':lambda *args:False,
         '_restoration_expansion':lambda *args:{'removed_ids':['b-cut'],'legs':[],'vias':[],'search':{'reason':'result_cap'}},
         'search_many':lambda *args:pytest.fail('Learned blocker feedback fell through to grid')}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    plan={'removed':frozenset({'a-cut'})}
    for unused,removed,reverse in trial_schedule([plan],lambda plan,removed:[removed],
            feedback=lambda plan,removed:learned.get(removed),max_trials=2):
        board=SimpleNamespace(removed=set(removed),joined=set());worlds.append(board)
        obligations=api().component_obligations(original,removed);feedback={};steps=[]
        try:
            env['_restore'](board,obligations,original_nets,removed,before,
                {'roles':{'a-cut':'column_access','b-cut':'row_access'}},SimpleNamespace(bounds=(-1,-1,10,10)),
                {'A','B'},time.monotonic()+10,steps=steps,movable={'b-cut'} if removed==plan['removed'] else (),feedback=feedback)
        except ValueError:
            assert feedback['removed_ids']==['b-cut'] and not board.joined
            grown=removed|set(feedback['removed_ids']);api().component_obligations(original,grown);learned[removed]=grown
        else:
            current=graph(board);groups=[{current['nodes'][i]['item'].m_Uuid.AsString() for i in indices} for indices in current['groups'].values()]
            api().require_components(obligations,groups)
            assert {'b-pad','b-padless-branch'} in groups
            completed.append(board)
    assert len(worlds)==2 and worlds[0] is not worlds[1] and completed==[worlds[1]]
    assert worlds[0].removed=={'a-cut'} and worlds[1].joined=={'A','B'}
