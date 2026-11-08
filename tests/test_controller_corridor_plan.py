"""Orchestration contracts using a physical-proof adapter; no Native claims."""
from copy import deepcopy
from types import SimpleNamespace as NS
import time
import pytest
from smilepcb.keyboard_routing import controller_corridor_plan as cp


class World:
    def __init__(self): self.ghosts=[]
    def checkpoint(self): return list(self.ghosts)
    def restore(self,mark): self.ghosts[:]=mark


class Backend:
    def __init__(self): self.worlds=[];self.reject=None;self.emitted=[];self.calls=[]
    def context(self,board,cfg,ports):
        return deepcopy(dict(pads=board.pads,tracks=board.tracks,rules=cfg['rules'],
            targets={n:[dict(pad_id='target-'+n,point=(24000000,9000000+i*500000),layers=[0])] for i,n in enumerate(('a','b'))},
            bounds=[0,0,40000000,30000000],obstacles=[]))
    def world(self,board,context,deadline): w=World();self.worlds.append(w);return w
    def proposal_context(self,proposal,context,deadline):return context
    def bridge(self,w,lane,context,deadline):
        return dict(legs=[(0,lane['path'])],vias=[])
    def admit(self,w,row,route,context,deadline):
        self.calls.append((row['net'],list(w.ghosts)))
        if self.reject==row['net']:return False
        w.ghosts.append(row['net']);return True
    def emit(self,board,routes,context,deadline):
        for row in routes:
            board.tracks['join-'+row['net']]=row['route']
            if self.reject=='emit-'+row['net']:raise ValueError('emit rejected')
        return ['join-'+q['net'] for q in routes]
    def closed(self,*a):return {}
    def closure(self,*a,**kw):return True
    def snapshot(self,board):return deepcopy(board.tracks)
    def rollback(self,board,before):board.tracks=before


@pytest.fixture
def scene(monkeypatch):
    backend=Backend();monkeypatch.setattr(cp,'_backend',lambda:backend)
    board=NS(pads={'src-a':{'net':'a'},'src-b':{'net':'b'}},tracks={'head-a':{'net':'a'},'head-b':{'net':'b'}})
    cfg={'rules':{'track_width_nm':150000,'clearance_nm':198000,'via_width_nm':500000,'drill_nm':200000},'parts':[]}
    ports=[dict(net=n,pad_ids=['src-'+n],added_ids=['head-'+n],terminal_id='head-'+n,
        exit_nm=(2000000,3000000+i*500000),heading_origin_nm=(1000000,3000000+i*500000),exit_layer=0,bank=['chip',0,1]) for i,n in enumerate(('a','b'))]
    batch=NS(ports=ports,matrix_nets={'a','b'},seal='source-seal',verify=lambda *a,**k:None)
    return board,cfg,batch,backend


def plan(scene):return cp.plan_controller_corridors(*scene[:3],deadline=time.monotonic()+10)


def test_complete_cohort_keeps_earlier_ghosts_and_never_emits_while_planning(scene):
    before=deepcopy(scene[0].tracks);p=plan(scene)
    assert scene[0].tracks==before
    assert p.receipt['accepted'] and len(p.receipt['groups'][0]['routes'])==2
    assert any(net=='b' and ghosts==['a'] for net,ghosts in scene[3].calls)
    assert len(p.envelopes)==2 and all(len(q['nets'])==1 for q in p.envelopes)
    assert all(not w.ghosts for w in scene[3].worlds)


def test_second_lane_rejection_discards_entire_plan_and_all_ghosts(scene):
    scene[3].reject='b';before=deepcopy(scene[0].tracks)
    with pytest.raises(ValueError,match='complete'):plan(scene)
    assert scene[0].tracks==before and all(not w.ghosts for w in scene[3].worlds)


def test_model_and_pad_changes_reject_before_reproof_or_commit(scene):
    p=plan(scene);count=len(scene[3].calls);scene[0].pads['src-a']['net']='b'
    with pytest.raises(ValueError,match='binding'):p.commit(scene[0],scene[1],deadline=time.monotonic()+10)
    assert len(scene[3].calls)==count and 'join-a' not in scene[0].tracks


def test_full_group_is_reproved_against_new_copper_and_consumed_once(scene):
    p=plan(scene);scene[0].tracks['foreign-field']={'net':'foreign'}
    p.commit(scene[0],scene[1],deadline=time.monotonic()+10)
    assert {'join-a','join-b','foreign-field'}<=scene[0].tracks.keys()
    with pytest.raises(ValueError,match='consumed'):p.commit(scene[0],scene[1],deadline=time.monotonic()+10)
    assert all(not w.ghosts for w in scene[3].worlds)


def test_reproof_failure_rolls_back_board_and_ghosts_and_rejects_plan(scene):
    p=plan(scene);before=deepcopy(scene[0].tracks);scene[3].reject='b'
    with pytest.raises(ValueError,match='complete'):p.commit(scene[0],scene[1],deadline=time.monotonic()+10)
    assert scene[0].tracks==before and all(not w.ghosts for w in scene[3].worlds)
    assert p.phase=='rejected'


def test_partial_emission_failure_rolls_back_the_whole_group(scene):
    p=plan(scene);before=deepcopy(scene[0].tracks);scene[3].reject='emit-b'
    with pytest.raises(ValueError):p.commit(scene[0],scene[1],deadline=time.monotonic()+10)
    assert scene[0].tracks==before and p.phase=='rejected'


def test_expiry_precedes_any_native_work(scene):
    p=plan(scene);count=len(scene[3].calls)
    with pytest.raises(TimeoutError):p.commit(scene[0],scene[1],deadline=0)
    assert len(scene[3].calls)==count and len(scene[3].worlds)==1


def test_via_envelopes_are_explicit_annulus_and_clearance_on_both_faces():
    route=dict(legs=[(0,[(0,0),(100,0)]),(2,[(100,0),(200,0)])],vias=[(100,0)])
    rows=cp._envelopes([dict(net='owner',source_key=['fixture-source'],route=route)],dict(track_width_nm=10,clearance_nm=10,via_width_nm=40,drill_nm=20),prefix='test')
    vias=[q for q in rows if q.get('kind')=='via']
    assert {q['layer'] for q in vias}=={0,2}
    assert all(q['center_nm']==(100,0) and q['radius_nm']==30 and q['nets']==['owner'] for q in vias)


def test_matrix_reseal_requires_old_exact_delta_and_retains_fixed_source(monkeypatch):
    import sys
    from test_controller_plan import scene as batch_scene
    from smilepcb.keyboard_routing import controller_plan as source
    b,cfg,before,receipt=batch_scene(monkeypatch)
    pad=dict(uuid='p3',number='3',net='COL')
    for state in (before,b.state):state['footprints']['mcu']['pads'].append(deepcopy(pad))
    cfg['parts'][0]['nets']['3']='COL';b.state['tracks']['s3']=dict(b.state['tracks']['s1'],net='COL')
    receipt['ports'].append(dict(receipt['ports'][0],net='COL',pad_ids=['p3'],added_ids=['s3'],terminal_id='s3'))
    receipt['added_ids'].append('s3')
    batch=source.certify_source_batch(b,cfg,before,receipt,matrix_nets={'ROW','COL'},requested_nets={'ROW','COL','CTRL'},deadline=1e11)
    mapping={'ROW':'COL','COL':'ROW'}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.gpio',NS(apply_exit_assignment=lambda *a,**k:None))
    def apply(board,model,ports,**kw):
        updated=deepcopy(model);new=deepcopy(ports)
        for q in new:
            for uid in q['added_ids']:board.state['tracks'][uid]['net']=mapping[q['net']]
            q['net']=mapping[q['net']]
        for d in board.state['footprints']['mcu']['pads']:d['net']=mapping.get(d['net'],d['net'])
        updated['parts'][0]['nets'].update({'1':'COL','3':'ROW'})
        return updated,dict(mapping=mapping,ports=new,accepted=True)
    sys.modules['smilepcb.keyboard_routing.gpio'].apply_exit_assignment=apply
    updated,new,assignment=source.rebind_source_batch(b,cfg,batch,net_mapping=mapping,deadline=1e11)
    assert new.lineage==dict(parent_seal=batch.seal,mapping=mapping)
    assert new.fixed_ports==batch.fixed_ports and new.tracks['s2']==batch.tracks['s2']
    assert new.before['tracks']==batch.before['tracks'] and cfg['parts'][0]['nets']['1']=='ROW'
    new.verify(b,updated,deadline=1e11)
    with pytest.raises(ValueError,match='consumed'):batch.handoff().take(b,cfg,{'ROW','COL'},deadline=1e11)
    assert new.handoff().take(b,updated,{'ROW','COL'},deadline=1e11)['accepted']


def test_identity_rebind_keeps_original_batch_and_fresh_handoff(monkeypatch):
    from test_controller_plan import scene as batch_scene,capture
    from smilepcb.keyboard_routing import controller_plan as source
    b,cfg,before,receipt=batch_scene(monkeypatch);batch=capture((b,cfg,before,receipt))
    updated,new,assignment=source.rebind_source_batch(b,cfg,batch,net_mapping={'ROW':'ROW'},deadline=1e11)
    assert new is batch and updated==cfg and not new.handoff().used
    assert assignment['mapping']=={'ROW':'ROW'}


@pytest.mark.parametrize('extra_s',[0,400])
def test_optin_unified_plan_precedes_local_copper_and_scopes_reservations(monkeypatch,extra_s):
    import sys
    from contextlib import contextmanager
    from test_controller_plan import scene as source_scene
    from smilepcb.keyboard_routing import controller_plan as source
    b,cfg,_,_=source_scene(monkeypatch);cfg['plane_nets']=['GND'];b.GetDesignSettings=lambda:NS(m_MinClearance=173000)
    calls=[];ports=[{'net':'ROW'},{'net':'CTRL'}]
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_ports',NS(route_controller_ports=lambda *a,**k:dict(accepted=True,ports=ports)))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_contract',NS(mcu_signal_nets=lambda cfg:{'ROW','CTRL'}))
    batch=NS(ports=ports,fixed_ports=[ports[1]],seal='batch',model=None,handoff=lambda:NS(batch=batch))
    def certify(board,model,*a,**k):batch.model=deepcopy(model);return batch
    monkeypatch.setattr(source,'certify_source_batch',certify)
    monkeypatch.setattr(source,'_corridor_assignment_targets',lambda *a:{'ROW':(10,20)},raising=False)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.collector_assignment',NS(collector_assignment=lambda *a:({'ROW':'ROW'},[])))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_phases',NS(ordered_nets=lambda cfg:[('rows',['ROW']),('columns',[])]))
    monkeypatch.setattr(source,'rebind_source_batch',lambda board,model,batch,**k:(model,batch,dict(mapping={'ROW':'ROW'},ports=[ports[0]])))
    @contextmanager
    def scope(*a,**k):calls.append('reserve');yield;calls.append('release')
    proof=NS(receipt={'accepted':True},reserve=scope,seal='plan')
    stage_deadline=time.monotonic()+100
    def planning(*a,**k):
        assert k['deadline']==stage_deadline-18 and k['valid_until']==stage_deadline+extra_s
        calls.append('plan');return proof
    monkeypatch.setattr(cp,'plan_controller_corridors',planning)
    def close(*a,**k):calls.append('local');return dict(local_closed={'nets':{}},external_obligations={})
    monkeypatch.setattr(source,'close_controller_obligations',close)
    monkeypatch.setattr(source,'_controller_local_ownership',lambda *a,**kw:None)
    from smilepcb.keyboard_routing.generation import generation_profile
    with generation_profile('dsn-process-v2'):
        handoff,fixed,result=source.prepare_unified_sources(b,cfg,controller_refs={'U_MCU'},matrix_nets={'ROW'},
            local_closed=None,deadline=stage_deadline,corridor_valid_until=stage_deadline+extra_s,component_corridors=True)
    assert calls==['plan','reserve','local','release']
    assert handoff.corridor_plan is proof and handoff.batch.model['routing_preparation']['component_corridors']
    assert 'routing_preparation' not in cfg and result['component_corridors']['accepted']


def test_optin_is_explicit_and_requires_unified_complete_matrix():
    import ast
    from pathlib import Path
    p=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/ordered_controller.py'
    tree=ast.parse(p.read_text());node=next(q for q in tree.body if isinstance(q,ast.FunctionDef) and q.name=='prepare_ordered_controller')
    ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),ns)
    fn=ns[node.name]
    for options in ({},{'complete_matrix':True},{'complete_matrix':True,'unified_controller_plan':False}):
        with pytest.raises(ValueError,match='requires'):fn(None,{},deadline=0,component_corridors=True,**options)
    with pytest.raises(ValueError,match='boolean'):fn(None,{},deadline=0,component_corridors=1)


def test_early_collector_assignment_is_identity_and_rejects_stale_plan(scene):
    p=plan(scene);h=NS(batch=scene[2],corridor_plan=p,corridor_assignment=dict(collector_plans=[{'signal_peel_order':['a','b'],'collector_heading':[1,0]}]))
    source={'ports':deepcopy(scene[2].ports)}
    mapping,plans=cp.collector_plan_assignment(scene[0],scene[1],h,source,deadline=time.monotonic()+10)
    assert mapping=={'a':'a','b':'b'} and plans==h.corridor_assignment['collector_plans']
    source['ports'][0]['net']='b'
    with pytest.raises(ValueError,match='source'):cp.collector_plan_assignment(scene[0],scene[1],h,source,deadline=time.monotonic()+10)


def test_actual_whole_net_local_closure_consumes_lane_without_duplicate_emit(scene):
    p=plan(scene);scene[3].closed=lambda board,routes,context,deadline:{'a':['src-a','target-a']}
    result=p.commit(scene[0],scene[1],deadline=time.monotonic()+10)
    assert 'join-a' not in scene[0].tracks and 'join-b' in scene[0].tracks
    assert result['consumed_by_verified_local']=={'a':['src-a','target-a']}


@pytest.mark.parametrize('pending',[True,False])
def test_after_field_stage_commits_before_fallback_and_rejects_incomplete_full_matrix(monkeypatch,pending):
    import ast
    from pathlib import Path
    p=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/matrix_stage.py'
    tree=ast.parse(p.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_prepare_matrix')
    events=[];tracks={}
    pad=NS(GetNumber=lambda:'1',GetNetname=lambda:'a',m_Uuid=NS(AsString=lambda:'source'))
    chip=NS(GetReference=lambda:'U_MCU',Pads=lambda:[pad]);board=NS(GetFootprints=lambda:[chip])
    cfg=dict(parts=[dict(ref='U_MCU',nets={'1':'a'})],mcu_gpio_pads={'A0':'1'},layout=dict(gpio=dict(rows=['A0'],cols=[])))
    def field(*a,**k):events.append('field');return dict(complete=True,owned_ids=[],generated_owned_ids=[],roles={})
    def commit(*a,**k):
        events.append('commit')
        if not pending:raise ValueError('native full group rejected')
        tracks['body']={'net':'a'};tracks['fixed-body']={'net':'control'}
        return dict(accepted=True,added_ids=['body','fixed-body'])
    ns=dict(__name__='smilepcb.keyboard_routing.matrix_stage',__package__='smilepcb.keyboard_routing',time=time,snapshot=lambda board:dict(tracks=deepcopy(tracks)),pad_partitions=lambda *a:[],
        ordered_nets=lambda cfg:[('rows',['a']),('columns',[])],build_field=field,_field_cutoff=lambda *a:a[1],
        matrix_pad_state=lambda *a:dict(missing={'a':1}))
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),ns)
    result=ns[node.name](board,cfg,row_layer=0,column_layer=2,deadline=time.monotonic()+60,corridor_plan=NS(commit=commit))
    assert events==['field','commit'] and not result['complete'] and not result['controller_connected']
    if pending:
        assert result['owned_ids']==['body'] and result['roles']['body']=='row_access'
        assert result['pending_matrix']=={'a':1}
        assert result['component_corridors']['fixed_prerequisite_ids']==['fixed-body']
    else:assert not result['component_corridors']['accepted']


def test_missing_physical_target_requires_actual_whole_net_closure(scene):
    old=scene[3].context
    def context(*a):
        result=old(*a);result['targets'].pop('a');return result
    scene[3].context=context
    with pytest.raises(ValueError,match='target.*closure'):plan(scene)
    scene[3].closed=lambda *a:{'a':['src-a']}
    p=plan(scene)
    assert p.receipt['local_target_closures']=={'a':['src-a']}
    assert {q['net'] for g in p.receipt['groups'] for q in g['routes']}=={'b'}


def test_via_envelope_uses_actual_drill_and_process_margins():
    route=dict(legs=[(0,[(0,0),(100,0)]),(2,[(100,0),(200,0)])],vias=[(100,0)])
    rules=dict(track_width_nm=10,clearance_nm=10,via_width_nm=40,drill_nm=30,
        generation={'via_to_smd_clearance_nm':50,'via_to_hole_clearance_nm':60},hole_clearance_nm=90)
    rows=cp._envelopes([dict(net='owner',source_key=['fixture-source'],route=route)],rules,prefix='test')
    assert all(q['radius_nm']>=105 for q in rows if q.get('kind')=='via')


def test_controller_region_auxiliary_pad_is_a_real_target_outside_source_aliases():
    def pad(uid,number,point):
        return NS(GetNumber=lambda:number,GetNetname=lambda:'enable',m_Uuid=NS(AsString=lambda:uid),
            GetPosition=lambda:NS(x=point[0],y=point[1]),IsOnLayer=lambda face:face==0)
    source=pad('mcu','1',(0,0));aux=pad('load','2',(1000000,0))
    board=NS(GetFootprints=lambda:[NS(GetReference=lambda:'U_MCU',Pads=lambda:[source]),
        NS(GetReference=lambda:'R_AUX',Pads=lambda:[aux])])
    cfg=dict(parts=[dict(ref='U_MCU',region='controller',nets={'1':'enable'}),
        dict(ref='R_AUX',region='controller',nets={'2':'enable'})])
    pads,targets=cp._actual_targets(board,cfg,[dict(net='enable',pad_ids=['mcu'])])
    assert pads.keys()=={'mcu','load'}
    assert targets=={'enable':[dict(pad_id='load',point=(1000000,0),layers=[0])]}


def test_native_bridge_uses_exact_geometry_path_and_bounds_failure_witnesses(monkeypatch):
    import sys
    calls=[]
    native=NS(SHAPE_SEGMENT=lambda *a:object(),SHAPE_CIRCLE=lambda *a:object(),F_Cu=0,B_Cu=2)
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(p=native))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',NS(vec=lambda q:q))
    def bridge(path,*a,**kw):
        calls.append(deepcopy(path))
        for i in range(6):kw['clear']((0,i),(100,i),0)
        for i in range(6):kw['via_clear']((100,i))
        return None
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.column_bridges',NS(bridge_carrier=bridge))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.obstacle_bundles',NS(_physical_connection=lambda *a,**k:pytest.fail('No actual bridge to admit')))
    backend=cp._NativeBackend();world=NS(track_width=10,via_width=40,drill=20,
        clear=lambda *a,**k:False,via_clear=lambda *a:False,collisions=lambda *a,**k:iter(['physical-blocker']))
    row=dict(net='a',source_key=['source-a'],source_layer=0,path=[(0,0),(100,0)],source=(0,0),target=(100,0),heading=(1,0),target_layers=[2])
    assert backend.bridge(world,row,{},time.monotonic()+10) is None
    assert calls==[row['path']]
    assert backend.last_failure['queries']==dict(track=6,via=6,pair=0)
    assert len(backend.last_failure['rejections'])==4
    assert [q['kind'] for q in backend.last_failure['rejections']]==['track','track','via','via']
    assert backend.last_failure['rejections'][0]['collisions']==['physical-blocker']
    assert backend.last_failure['rejections'][-1]['collisions']=={'0':['physical-blocker'],'2':['physical-blocker']}


def test_expired_plan_cannot_be_revived_with_a_later_caller_deadline(scene,monkeypatch):
    p=plan(scene)
    assert 'valid_until' in p.receipt
    count=len(scene[3].calls);worlds=len(scene[3].worlds)
    monkeypatch.setattr(cp.time,'monotonic',lambda:p.receipt['valid_until']+1)
    with pytest.raises(TimeoutError):p.commit(scene[0],scene[1],deadline=p.receipt['valid_until']+100)
    assert len(scene[3].calls)==count and len(scene[3].worlds)==worlds


def test_reseal_preserves_fixed_already_accessible_function_obligations(monkeypatch):
    import sys
    from test_controller_plan import scene as batch_scene
    from smilepcb.keyboard_routing import controller_plan as source
    b,cfg,before,receipt=batch_scene(monkeypatch)
    for state in (before,b.state):
        state['footprints']['field']={'ref':'J_FIELD','pads':[dict(uuid='external',number='1',net='CTRL')]}
        state['footprints']['mcu']['pads'].append(dict(uuid='p3',number='3',net='COL'))
    cfg['parts'].append(dict(ref='J_FIELD',region='matrix',nets={'1':'CTRL'}));cfg['parts'][0]['nets']['3']='COL'
    b.state['tracks'].pop('s2');receipt['ports'].pop();receipt['added_ids'].remove('s2')
    b.state['tracks']['s3']=dict(b.state['tracks']['s1'],net='COL')
    receipt['ports'].append(dict(receipt['ports'][0],net='COL',pad_ids=['p3'],added_ids=['s3'],terminal_id='s3'))
    receipt['added_ids'].append('s3');receipt['already_accessible']=[dict(net='CTRL',pad_ids=['p2'])]
    batch=source.certify_source_batch(b,cfg,before,receipt,matrix_nets={'ROW','COL'},requested_nets={'ROW','COL','CTRL'},deadline=1e11)
    mapping={'ROW':'COL','COL':'ROW'}
    def apply(board,model,ports,**kw):
        updated=deepcopy(model);new=deepcopy(ports)
        for q in new:
            for uid in q['added_ids']:board.state['tracks'][uid]['net']=mapping[q['net']]
            q['net']=mapping[q['net']]
        for f in board.state['footprints'].values():
            if f['ref']=='U_MCU':
                for d in f['pads']:d['net']=mapping.get(d['net'],d['net'])
        updated['parts'][0]['nets'].update({'1':'COL','3':'ROW'})
        return updated,dict(mapping=mapping,ports=new,accepted=True)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.gpio',NS(apply_exit_assignment=apply))
    updated,new,assignment=source.rebind_source_batch(b,cfg,batch,net_mapping=mapping,deadline=1e11)
    assert new.requested_nets==frozenset({'ROW','COL','CTRL'})
    assert list(new.already_accessible)==receipt['already_accessible']
    new.verify(b,updated,deadline=1e11)


def test_complete_launch_scope_preserves_unrelated_parent_closure_and_restores():
    from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory
    old=dict(net='old',pad_ids=['source']);new=dict(net='new',pad_ids=['source'])
    unrelated=dict(net='fixed',pad_ids=['fixed'])
    with reserve_launches([old,unrelated],resolved=[('source',),('fixed',)],repair_reserved=[('fixed',)]):
        parent=current_launch_inventory()
        with cp._launch_scope([new]):
            active=current_launch_inventory()
            assert active.ports=={('source',):new,('fixed',):unrelated}
            assert active.resolved=={('fixed',)} and active.repair_reserved=={('fixed',)}
        assert current_launch_inventory() is parent
    with cp._launch_scope([new]):assert current_launch_inventory().ports=={('source',):new}
    assert current_launch_inventory() is None


def test_departure_waypoint_is_measured_from_actual_peer_launch_envelope():
    import ast,math
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/port_policy.py'
    cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='PortPolicy')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='departure_waypoint_nm')
    ns=dict(math=math,terminal_origin=lambda port:port['heading_origin_nm'])
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),ns)
    source=dict(net='a',exit_nm=(2000000,3500000),heading_origin_nm=(1000000,3500000),exit_layer=0,bank=['chip',0,1])
    peer=dict(source,net='b',exit_nm=(2000000,3000000),heading_origin_nm=(1000000,3000000))
    bbox=NS(GetLeft=lambda:1925000,GetRight=lambda:2575000,GetTop=lambda:2925000,GetBottom=lambda:3075000)
    policy=NS(ports=[source,peer],track_width=150000,launch_gap=173000,net_clearances={},
        access_radius_nm=lambda *a:150001,launch_buckets={0:{0:[(1,'b',NS(BBox=lambda:bbox),173000,())]}})
    waypoint=ns['departure_waypoint_nm'](policy,source)
    assert waypoint[0]>2823000 and waypoint[1]==3500000


def test_departure_context_includes_nonbatch_peer_even_after_parent_resolution(monkeypatch):
    import sys
    from smilepcb.keyboard_routing.interface_launches import reserve_launches
    owned=dict(net='source',pad_ids=['source'],bank=['chip',0,1])
    peer=dict(net='peer',pad_ids=['peer'],bank=['chip',0,1])
    seen=[]
    class Policy:
        def __init__(self,board,ports,**kw):seen.append((deepcopy(ports),kw))
        def departure_waypoint_nm(self,port,**kw):return (9000000,1000000)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.port_policy',NS(PortPolicy=Policy))
    with reserve_launches([peer],resolved=[('peer',)]):
        assert cp._departure_context(object(),[owned],150000)==[dict(source_key=['source'],waypoint_nm=(9000000,1000000))]
    assert seen[0][0]==[peer,owned]
    assert 'resolved' not in seen[0][1]  # All actual peer envelopes, regardless lifecycle.


def test_actual_native_closure_rejects_an_unselected_open_same_net_target(monkeypatch):
    import sys
    graph=dict(nodes=[dict(item=NS(m_Uuid=NS(AsString=lambda u=u:u))) for u in ('source','selected','other')],find=lambda i:0 if i<2 else 1)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',NS(copper_graph=lambda *a,**k:graph))
    backend=cp._NativeBackend();routes=[dict(net='a',pad_ids=['source'],target_pad_id='selected')]
    context=dict(pads={u:dict(net='a') for u in ('source','selected','other')})
    with pytest.raises(ValueError,match='closure'):
        backend.closure(object(),routes,time.monotonic()+10,context=context)


def test_ordered_component_path_defers_led_and_optional_auxiliary_copper(monkeypatch):
    from contextlib import contextmanager
    from collections import defaultdict
    from test_controller_escape_integration import body,module_stub
    from test_module_preparation_order import Board,Item,snapshot
    board=Board();before=snapshot(board);events=[];active=[]
    cfg=dict(parts=[dict(ref='U_MCU',region='controller')],plane_nets=['GND'])
    accepted=lambda *a,**k:dict(accepted=True,ports=[],added_ids=[])
    @contextmanager
    def reserve(*a,**k):active.append(True);yield;active.pop()
    def seed(*a,**k):
        events.append('seed');assert 'plan' in events and active
        board.tracks.append(Item('led-seed','LED'));return dict(accepted=True,ports=[],added_ids=['led-seed'])
    def auxiliary(*a,**k):events.append('auxiliary');return dict(accepted=True,local_closed={'nets':{}})
    def capture(*a,**k):events.append('capture');return {'nets':{}}
    def planned(board,model,**kw):
        events.append('plan');assert 'seed' not in events and 'auxiliary' not in events
        current=deepcopy(model);current['routing_preparation']={'component_corridors':True}
        return NS(batch=NS(model=current),corridor_plan=NS(reserve=reserve)),dict(accepted=True,ports=[],local_closed={'nets':{}}),{'accepted':True}
    def ownership(board,model,frame,proof,**kw):
        events.append('ownership');assert 'led-seed' not in frame['tracks'] and 'led-seed' in snapshot(board)['tracks']
        return NS(tracks={'led-seed':{}},pads={},generation_profile='dsn-process-v2')
    def collector(*a,**k):events.append('collector');assert k['provisional_ownership'].tracks=={'led-seed':{}};return a[1],dict(candidate_accepted=False,reason='stop')
    module_stub(monkeypatch,'generation',current_generation_profile=lambda:NS(name='dsn-process-v2'))
    module_stub(monkeypatch,'modules',seed_key_data_access=seed,connect_key_modules=lambda *a,**k:pytest.fail('premature modules'))
    module_stub(monkeypatch,'controller_local',prepare_controller_local=auxiliary,capture_local_closures=capture)
    module_stub(monkeypatch,'collector_preparation',verified_provisional_ownership=ownership,ProvisionalOwnership=lambda *a:None,prepare_collector_matrix=collector)
    module_stub(monkeypatch,'controller_plan',prepare_unified_sources=planned)
    module_stub(monkeypatch,'interface_launches',reserve_launches=reserve)
    fn=body('ordered_controller','prepare_ordered_controller',dict(time=NS(monotonic=lambda:1000),snapshot=snapshot,
        seed_controller_returns=accepted,route_controller_ports=accepted,ordered_nets=lambda cfg:[('rows',['ROW']),('columns',['COL'])],
        mcu_signal_nets=lambda cfg:{'ROW','COL'},probe_gpio_access=lambda *a,**k:dict(excluded_gpios=[],uncertain_gpios=[]),
        apply_matrix_gpio=lambda board,cfg,**k:(deepcopy(cfg),{}),defaultdict=defaultdict,deepcopy=deepcopy))
    updated,result=fn(board,cfg,deadline=1200,complete_matrix=True,unified_controller_plan=True,component_corridors=True)
    assert not result['accepted'] and snapshot(board)==before
    assert events==['capture','plan','seed','ownership','collector']


def test_matrix_plan_without_generated_heads_rolls_back_postcommit_failure(monkeypatch):
    import ast,sys
    from pathlib import Path
    from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory,publish_launches
    class Item:
        def __init__(self,uid):self.uid=uid;self.m_Uuid=NS(AsString=lambda:uid)
    board=NS(items=[Item('original')]);board.GetTracks=lambda:list(board.items)
    board.GetFootprints=lambda:[];board.Delete=lambda item:board.items.remove(item);board.Add=lambda item:board.items.append(item);board.BuildConnectivity=lambda:None
    snapshot=lambda board:dict(tracks={q.uid:{'net':'a'} for q in board.items},footprints={})
    before=snapshot(board);plan=NS(phase='planned')
    original=dict(net='a',pad_ids=['s']);changed=dict(original,terminal_id='new')
    def prepare(*a,**k):
        board.items.append(Item('new'));plan.phase='consumed';publish_launches([changed],resolved=[('s',)])
        return dict(complete=False,controller_connected=False)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.conditional_banks',NS(copy_native_tracks=lambda board:{q.uid:Item(q.uid) for q in board.items},_source_physics=lambda *a:'physical'))
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/matrix_stage.py'
    node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='prepare_matrix')
    ns=dict(__name__='smilepcb.keyboard_routing.matrix_stage',__package__='smilepcb.keyboard_routing',snapshot=snapshot,_prepare_matrix=prepare)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    with reserve_launches([original],repair_reserved=[('s',)]):
        inventory=current_launch_inventory()
        result=ns[node.name](board,{},row_layer=0,column_layer=2,deadline=time.monotonic()+10,corridor_plan=plan)
        assert not result['complete'] and snapshot(board)==before and plan.phase=='rejected'
        assert inventory.ports=={('s',):original} and inventory.resolved==set() and inventory.repair_reserved=={('s',)}


def test_native_bridge_avoids_greedy_via_on_future_peer_and_uses_earlier_wide_station(monkeypatch):
    import sys
    from smilepcb.keyboard_routing.bundle_geometry import _point_distance
    class Segment:
        def __init__(self,a,z,width):self.a=a;self.z=z;self.width=width
    class Circle:
        def __init__(self,point,radius):self.point=point;self.radius=radius
    def collide(circle,segment,gap):
        return _point_distance(circle.point,segment.a,segment.z)<=circle.radius+segment.width/2+gap
    native=NS(SHAPE_SEGMENT=Segment,SHAPE_CIRCLE=Circle,SHAPE=NS(Collide=collide),F_Cu=0,B_Cu=2)
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(p=native))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',NS(vec=lambda q:q))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.obstacle_bundles',NS(_physical_connection=lambda *a,**k:True))
    world=NS(track_width=200000,via_width=500000,drill=300000,minimum_clearance=173000,
        clear=lambda *a,**k:True,via_clear=lambda *a:True,via_pair_clear=lambda *a:True,
        collisions=lambda *a,**k:iter(()))
    row=dict(net='a',source_key=['source-a'],source_layer=0,path=[(0,0),(10000000,0)],source=(0,0),target=(10000000,0),heading=(1,0),target_layers=[2])
    context=dict(rules=dict(track_width_nm=200000,clearance_nm=173000,via_width_nm=500000,drill_nm=300000,
        generation={'via_to_smd_clearance_nm':173000,'via_to_hole_clearance_nm':251000},hole_clearance_nm=250000),bounds=[-1000000,-1000000,11000000,2000000])
    proposal=dict(lanes=[dict(net='a',source_key=['source-a'],path=row['path']),dict(net='b',source_key=['source-b'],path=[(0,700000),(7000000,700000),(7200000,500000),(10000000,500000)])])
    backend=cp._NativeBackend();deadline=time.monotonic()+10
    greedy=backend.bridge(world,row,context,deadline)
    assert greedy['vias'][0][0]>8000000
    joint=backend.proposal_context(proposal,context,deadline)
    route=backend.bridge(world,row,joint,deadline)
    assert route and route['vias'] and route['vias'][0][0]<=7000000
    for point in route['vias']:
        for segment in next(q['shapes'] for q in joint['cohort_carriers'] if q['source_key']==['source-b']):
            assert not collide(Circle(point,250000),segment,173000)
            assert not collide(Circle(point,joint['cohort_via_radius_nm']),segment,0)
    assert backend.proposal_context(dict(lanes=[proposal['lanes'][0]]),context,deadline) is context


def test_complete_proposal_peer_context_exists_before_the_first_bridge(scene,monkeypatch):
    observed=[];backend=scene[3]
    def proposal_context(proposal,context,deadline):
        observed.append(('context',[q['net'] for q in proposal['lanes']]))
        return dict(context,cohort_carriers={q['net']:q['path'] for q in proposal['lanes']})
    def bridge(world,row,context,deadline):
        observed.append(('bridge',list(context.get('cohort_carriers',{}))))
        return dict(legs=[(0,row['path'])],vias=[])
    monkeypatch.setattr(backend,'proposal_context',proposal_context,raising=False)
    monkeypatch.setattr(backend,'bridge',bridge)
    plan(scene)
    assert observed[0]==('context',['a','b'])
    assert observed[1:]==[('bridge',['a','b']),('bridge',['a','b'])]


def test_via_reservation_retains_conservative_annulus_to_future_hole_margin():
    rules=dict(via_width_nm=500000,drill_nm=300000,clearance_nm=173000,
        generation={'via_to_smd_clearance_nm':173000,'via_to_hole_clearance_nm':251000},
        hole_clearance_nm=250000,drill_land_clearance_nm=100000)
    assert cp._via_radius(rules)==501000


def test_proposals_receive_actual_via_dimension_and_unchanged_owner_envelope(scene,monkeypatch):
    original=cp.corridor_proposals;seen=[]
    def proposals(*a,**kw):
        seen.append((kw['via_width_nm'],kw['via_envelope_radius_nm'],kw['max_candidates']))
        return original(*a,**kw)
    monkeypatch.setattr(cp,'corridor_proposals',proposals)
    proof=plan(scene)
    assert seen==[(500000,448000,48)]
    assert proof.receipt['candidate_attempts']<=48


def test_same_net_departures_keep_each_physical_source_heading(monkeypatch):
    import sys
    a=dict(net='shared',pad_ids=['source-east'],exit_nm=(100,0),heading_origin_nm=(0,0))
    b=dict(net='shared',pad_ids=['source-north'],exit_nm=(0,-100),heading_origin_nm=(0,0))
    class Policy:
        def __init__(self,*a,**k):pass
        def departure_waypoint_nm(self,port,**kw):
            return (200,0) if port['pad_ids']==['source-east'] else (0,-200)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.port_policy',NS(PortPolicy=Policy))
    rows=cp._departure_context(object(),[a,b],10)
    assert rows==[dict(source_key=['source-east'],waypoint_nm=(200,0)),dict(source_key=['source-north'],waypoint_nm=(0,-200))]


def test_two_same_net_roots_are_retained_in_bound_routes_and_closure(scene):
    board,cfg,batch,backend=scene
    for port in batch.ports:port['net']='shared'
    batch.ports[1].update(exit_nm=(4000000,7000000),heading_origin_nm=(4000000,8000000))
    old=backend.context
    def context(*a):
        row=old(*a);row['targets']={'shared':[dict(pad_id='external',point=(24000000,3000000),layers=[0])]}
        row['departure_waypoints']=[dict(source_key=['src-a'],waypoint_nm=(3000000,3000000)),dict(source_key=['src-b'],waypoint_nm=(4000000,6000000))]
        return row
    backend.context=context
    p=plan(scene)
    routes=[row for group in p.receipt['groups'] for row in group['routes']]
    assert {tuple(row['source_key']) for row in routes}=={('src-a',),('src-b',)}
    assert {tuple(q['pad_ids']):tuple(q['departure_waypoint_nm']) for q in p.binding['ports']}=={('src-a',):(3000000,3000000),('src-b',):(4000000,6000000)}
    assert all(row['net']=='shared' for row in routes)


def test_proposal_context_keeps_same_net_physical_carriers_and_does_not_block_merge(monkeypatch):
    import sys
    native=NS(SHAPE_SEGMENT=lambda *a:object(),SHAPE_CIRCLE=lambda *a:object(),SHAPE=NS(Collide=lambda *a:pytest.fail('Same owner must permit merge')),F_Cu=0,B_Cu=2)
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(p=native))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',NS(vec=lambda q:q))
    def bridge(path,*a,**kw):
        assert kw['via_clear']((100,0));return dict(legs=[(0,path)],vias=[])
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.column_bridges',NS(bridge_carrier=bridge))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.obstacle_bundles',NS(_physical_connection=lambda *a,**k:True))
    backend=cp._NativeBackend();context=dict(rules=dict(track_width_nm=10,clearance_nm=10,via_width_nm=40,drill_nm=20),bounds=[0,0,1000,1000])
    proposal=dict(lanes=[dict(source_key=['s1'],net='shared',path=[(0,0),(100,0)]),dict(source_key=['s2'],net='shared',path=[(0,10),(100,10)])])
    joint=backend.proposal_context(proposal,context,time.monotonic()+10)
    assert len(joint['cohort_carriers'])==2
    world=NS(track_width=10,via_width=40,drill=20,via_clear=lambda *a:True)
    row=dict(source_key=['s1'],net='shared',source_layer=0,target_layers=[0],path=[(0,0),(100,0)],source=(0,0),target=(100,0),heading=(1,0))
    assert backend.bridge(world,row,joint,time.monotonic()+10)


@pytest.mark.parametrize('changed,error',[(False,'unavailable'),(True,'unavailable'),(False,'invalid')])
def test_optional_template_miss_uses_verified_collector_not_a_rejected_plan(monkeypatch,changed,error):
    from smilepcb.keyboard_routing import controller_plan as source
    b=NS(state={'copper':'fixed'});cfg={'parts':[]};checks=[]
    monkeypatch.setattr(source,'_proof_tools',lambda:NS(snapshot=lambda b:deepcopy(b.state)))
    batch=NS(seal='actual-source',verify=lambda *a,**k:checks.append('verify'))
    def miss(*a,**k):
        if changed:b.state['copper']='changed'
        if error=='invalid':raise ValueError('invalid source binding')
        raise cp.CorridorUnavailable('No complete controller corridor cohort')
    monkeypatch.setattr(cp,'plan_controller_corridors',miss)
    if changed or error=='invalid':
        with pytest.raises(ValueError):source._prefer_component_corridors(b,cfg,batch,deadline=time.monotonic()+10,valid_until=time.monotonic()+30)
        assert not checks
    else:
        plan,receipt=source._prefer_component_corridors(b,cfg,batch,deadline=time.monotonic()+10,valid_until=time.monotonic()+30)
        assert plan is None and receipt['accepted'] is False and receipt['fallback']=='verified-collector'
        assert checks==['verify'] and b.state=={'copper':'fixed'}
