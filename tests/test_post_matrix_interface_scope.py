"""Host lifecycle controls; native geometry is exercised by the paired probe."""
import ast
from copy import deepcopy
import json
import math
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]


def run_wrapper(monkeypatch,tmp_path,*,full=True,mutate_source=False,legacy_signature=False):
    from smilepcb.keyboard_routing import field_contract,interface_contract
    from smilepcb.keyboard_routing.interface_launches import current_launches
    model=tmp_path/'.smilepcb-keyboard.json';model.write_text(json.dumps({'routing_preparation':{'mode':'ordered-banks-v2'}}))
    (tmp_path/interface_contract.MARKER).write_text('{}')
    observed=[];port={'net':'CONTROL','pad_ids':['source'],'added_ids':['head']}
    source_tracks={'head':{'width':150000}}
    def operation(checked,**kwargs):
        if mutate_source:source_tracks['head']['width']+=1
        return {'ports':list(current_launches()),'checked':checked}
    def verify(board,cfg,project,*,deadline,retire_completed=False):
        observed.append(retire_completed)
        if not retire_completed and full:raise ValueError('retired matrix copper')
        return {'ports':[port],'owned_ids':['head'],'source_tracks':deepcopy(source_tracks)}
    monkeypatch.setattr(interface_contract,'load_verified_interfaces',verify)
    monkeypatch.setattr(field_contract,'load',lambda project:{'scope':'matrix-with-controller' if full else 'key-field'})
    monkeypatch.setattr(field_contract,'guarded',lambda request,fn:fn(request))
    source=ROOT/'smilepcb/keyboard_native.py';node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='run')
    env={'Path':Path,'json':json,'p':SimpleNamespace(LoadBoard=lambda path:object()),'_run':operation,'w':SimpleNamespace(snapshot=lambda board:{'tracks':deepcopy(source_tracks)})}
    if legacy_signature:env['_run']=lambda checked:operation(checked)
    helper=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_admit_interface_delta')
    exec(compile(ast.Module(body=[node,helper],type_ignores=[]),str(source),'exec'),env)
    result=env['run']({'operation':'components','board':str(tmp_path/'keyboard.kicad_pcb')})
    return result,observed,port


def test_full_matrix_handoff_keeps_unfinished_nonmatrix_inventory(monkeypatch,tmp_path):
    result,observed,port=run_wrapper(monkeypatch,tmp_path)
    assert observed==[True], 'Full matrix does not retire an unfinished control net'
    assert result['ports']==[port]


def test_partial_matrix_retains_strict_original_receipt_verification(monkeypatch,tmp_path):
    result,observed,port=run_wrapper(monkeypatch,tmp_path,full=False)
    assert observed==[False]
    assert result['ports']==[port]


class Pad:
    def __init__(self,uid,number,net,parent):self.uid=uid;self.number=number;self.net=net;self.parent=parent;self.m_Uuid=SimpleNamespace(AsString=lambda:uid)
    def GetNumber(self):return self.number
    def GetNetname(self):return self.net
    def GetParentFootprint(self):return self.parent
    def GetPosition(self):return SimpleNamespace(x=0,y=0)
    def IsOnLayer(self,layer):return layer==0


class Footprint:
    def __init__(self,ref):self.ref=ref;self.pads=[]
    def GetReference(self):return self.ref
    def Pads(self):return self.pads


class Track:
    def __init__(self,uid,net,a,z):self.m_Uuid=SimpleNamespace(AsString=lambda:uid);self.net=net;self.a=a;self.z=z
    def GetClass(self):return 'PCB_TRACK'
    def GetStart(self):return SimpleNamespace(x=self.a[0],y=self.a[1])
    def GetEnd(self):return SimpleNamespace(x=self.z[0],y=self.z[1])
    def GetNetname(self):return self.net


def receipt_scene(monkeypatch,*,closed=('row','column'),missing=('row','column'),detached=()):
    """Only native graph construction/collision APIs are replaced on host."""
    from smilepcb.keyboard_routing import interface_contract
    chip=Footprint('U_MCU');field=Footprint('FIELD');nets=('row','column','control')
    for number,net in enumerate(nets,1):
        chip.pads.append(Pad('source-'+net,str(number),net,chip));field.pads.append(Pad('external-'+net,str(number),net,field))
    cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{str(i):net for i,net in enumerate(nets,1)}},{'ref':'FIELD','region':'matrix','nets':{str(i):net for i,net in enumerate(nets,1)}}], 'layout':{'gpio':{'rows':['A0'],'cols':['B0']},'rgb':False},'mcu_gpio_pads':{'A0':'1','B0':'2'},'plane_nets':[],'routing_preparation':{'mode':'ordered-banks-v2'}}
    ports=[{'net':net,'pad_ids':['source-'+net],'added_ids':['old-'+net],'terminal_id':'old-'+net,'kind':'same_layer','via_id':None,'via_nm':None,'exit_nm':[2,i],'heading_origin_nm':[1,i],'exit_layer':0} for i,net in enumerate(nets)]
    tracks={'old-'+net:{'class':'PCB_TRACK','net':net,'start':[1,i],'end':[2,i],'width':150000,'layer':0} for i,net in enumerate(nets)}
    fpstate={f.ref:{'ref':f.ref,'position':[0,0],'rotation':0,'layer':0,'locked':False,'pads':[{'uuid':d.uid,'number':d.number,'net':d.net,'position':[0,0]} for d in f.pads]} for f in (chip,field)}
    receipt={'schema':1,'ports':ports,'matrix_pins':{'rows':['A0'],'cols':['B0']},'tracks':deepcopy(tracks),'pads':{d['uuid']:deepcopy(d) for d in fpstate['U_MCU']['pads']},'controller_frame':[{k:v for k,v in fpstate['U_MCU'].items() if k!='pads'}]}
    state={'tracks':{u:t for u,t in tracks.items() if t['net'] not in missing},'footprints':fpstate}
    board=SimpleNamespace(GetFootprints=lambda:[chip,field]);nodes=[];roots=[]
    for i,net in enumerate(nets):
        for j,d in enumerate((chip.pads[i],field.pads[i])):nodes.append({'item':d,'net':net,'shapes':{0:object()},'zone':False});roots.append(i*3 if j==0 or net in closed else i*3+1)
        if net not in missing:nodes.append({'item':Track('old-'+net,net,(1,i),(2,i)),'net':net,'shapes':{0:object()},'zone':False});roots.append(i*3+2 if net in detached else i*3)
    graph={'nodes':nodes,'find':lambda i:roots[i],'nets':set(nets)}
    native=SimpleNamespace(PAD=Pad,PCB_VIA=type('Via',(),{}),SHAPE_CIRCLE=lambda v,r:object(),SHAPE=SimpleNamespace(Collide=lambda *args:True))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',SimpleNamespace(p=native,snapshot=lambda b:deepcopy(state)))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',SimpleNamespace(copper_graph=lambda *args,**kwargs:graph))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',SimpleNamespace(vec=lambda v:v))
    return interface_contract,board,cfg,receipt,state


def test_completed_native_nets_may_retire_stale_heads_but_control_stays_live(monkeypatch):
    api,board,cfg,receipt,state=receipt_scene(monkeypatch)
    proof=api.verify_interfaces(board,cfg,receipt,deadline=time.monotonic()+1,retire_completed=True)
    assert [q['net'] for q in proof['ports']]==['row','column','control']
    assert set(map(tuple,proof['resolved']))=={('source-row',),('source-column',)}
    assert proof['owned_ids']==['old-control']


def test_capture_default_does_not_accept_retired_geometry(monkeypatch):
    api,board,cfg,receipt,state=receipt_scene(monkeypatch)
    with pytest.raises(ValueError,match='copper changed'):api.verify_interfaces(board,cfg,receipt,deadline=time.monotonic()+1)


def test_reopened_net_cannot_reuse_a_missing_original_head(monkeypatch):
    api,board,cfg,receipt,state=receipt_scene(monkeypatch,closed=('column',))
    with pytest.raises(ValueError,match='copper changed'):api.verify_interfaces(board,cfg,receipt,deadline=time.monotonic()+1,retire_completed=True)


@pytest.mark.parametrize('case',['frame','pad','pin','binding','alias','ownership'])
def test_retirement_still_binds_entire_immutable_receipt(monkeypatch,case):
    api,board,cfg,receipt,state=receipt_scene(monkeypatch)
    if case=='frame':receipt['controller_frame'][0]['rotation']=90
    if case=='pad':receipt['pads']['source-row']['position']=[1,0]
    if case=='pin':receipt['matrix_pins']['rows']=['wrong']
    if case=='binding':cfg['parts'][0]['nets']['1']='wrong'
    if case=='alias':receipt['ports'][0]['pad_ids']=[];receipt['pads'].pop('source-row')
    if case=='ownership':receipt['ports'][0]['added_ids']=['old-control']
    with pytest.raises(ValueError):api.verify_interfaces(board,cfg,receipt,deadline=time.monotonic()+1,retire_completed=True)


def test_resolution_deadline_fails_closed_without_mutating_source(monkeypatch):
    api,board,cfg,receipt,state=receipt_scene(monkeypatch);before=deepcopy(state)
    with pytest.raises(TimeoutError):api.verify_interfaces(board,cfg,receipt,deadline=time.monotonic()-1,retire_completed=True)
    assert state==before


def operation_kernel(monkeypatch):
    source=ROOT/'smilepcb/keyboard_native.py';node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_run')
    pad=SimpleNamespace(GetNumber=lambda:'1',GetNetname=lambda:'control')
    board=SimpleNamespace(GetFootprints=lambda:[SimpleNamespace(Pads=lambda:[pad])],Zones=lambda:[])
    env={'Path':Path,'json':json,'p':SimpleNamespace(LoadBoard=lambda path:board,SaveBoard=lambda *args:True),'validate':lambda *args:None}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    return env['_run'],board


def test_complete_debits_preflight_from_existing_82_second_allocation(monkeypatch,tmp_path):
    run,board=operation_kernel(monkeypatch);observed=[]
    def repair(*args,**kwargs):observed.append(kwargs['deadline']);return {'accepted':False}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.completion',SimpleNamespace(repair_candidate=repair,detour_candidates=None))
    monkeypatch.setattr(time,'monotonic',lambda:108)
    (tmp_path/'.smilepcb-keyboard.json').write_text('{"plane_nets":[]}')
    run({'operation':'complete','board':str(tmp_path/'keyboard.kicad_pcb'),'protected_ids':[],'strategy':'test'},preflight_elapsed_s=8)
    assert observed==[182], 'An 8s proof cannot renew the operation budget at t=108'


def test_components_debits_proof_and_uses_the_live_head_contact_policy(monkeypatch,tmp_path):
    run,board=operation_kernel(monkeypatch);observed=[]
    def Router(*args,**kwargs):
        router,policy=scoped_router();router.deadline=kwargs['deadline'];router.added=[]
        return router
    def route(board,router,nets,**kwargs):
        observed.append((router.deadline,router.port_policy is router.launch_policy));return 0
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.matrix',SimpleNamespace(KeyboardRouter=Router))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',SimpleNamespace(route_components=route))
    monkeypatch.setattr(time,'monotonic',lambda:108)
    run({'operation':'components','board':str(tmp_path/'keyboard.kicad_pcb')},preflight_elapsed_s=8)
    assert observed==[(145,True)]


@pytest.mark.parametrize('case',['terminal','heading','kind','recorded_net'])
def test_closed_net_cannot_hide_an_internally_corrupt_source_receipt(monkeypatch,case):
    api,board,cfg,receipt,state=receipt_scene(monkeypatch)
    if case=='terminal':receipt['ports'][0]['terminal_id']='absent'
    if case=='heading':receipt['ports'][0]['heading_origin_nm']=[9,9]
    if case=='kind':receipt['ports'][0]['kind']='unknown'
    if case=='recorded_net':receipt['tracks']['old-row']['net']='wrong'
    with pytest.raises(ValueError):api.verify_interfaces(board,cfg,receipt,deadline=time.monotonic()+1,retire_completed=True)


def local_router_type():
    source=ROOT/'smilepcb/keyboard_routing/local_router.py';node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='LocalRouter')
    native=SimpleNamespace(FromMM=lambda mm:round(mm*1e6),SHAPE_SEGMENT=lambda a,z,w:object(),SHAPE_CIRCLE=lambda *args:SimpleNamespace(BBox=lambda:object()),SHAPE=SimpleNamespace(Collide=lambda *args:True),PCB_TRACK=Track,PCB_VIA=type('Via',(),{}))
    env={'__name__':'smilepcb.keyboard_routing._scope_host','__package__':'smilepcb.keyboard_routing',
         'p':native,'time':time,'math':math,'distance':math.dist,'vec':lambda v:v,'xy':lambda v:(v.x,v.y),
         'paths':lambda a,b,**kw:[[a,b]],'ranked_paths':lambda paths,quality:paths,'uses_exchange_geometry':lambda profile:False};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    return env['LocalRouter']


def scoped_router():
    router=object.__new__(local_router_type());policy=SimpleNamespace(ports=[],resolved=set())
    router._port_policy=None;router.launch_policy=policy;router._captured_launch_policy=policy;router.launch_inventory=None
    return router,policy


def test_every_first_contact_consumer_sees_captured_launch_policy():
    router,policy=scoped_router()
    assert router.port_policy is policy


@pytest.mark.parametrize('clear',['set_none','delete'])
def test_clearing_override_restores_captured_interface_scope(clear):
    router,captured=scoped_router();router._port_policy=SimpleNamespace();router.launch_policy=router._port_policy
    if clear=='delete':del router.port_policy
    else:router.port_policy=None
    assert router.port_policy is captured


def test_explicit_empty_capacity_policy_does_not_restore_excluded_cohort(monkeypatch):
    router,captured=scoped_router();router.launch_policy=None
    empty=SimpleNamespace(ports=[],resolved=set());empty.set_resolved=lambda resolved:setattr(empty,'resolved',resolved)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.port_policy',SimpleNamespace(PortPolicy=object))
    router.port_policy=empty
    assert router.port_policy is empty and router.launch_policy is empty
    assert router._captured_launch_policy is captured


def direct_router():
    router,policy=scoped_router();router.path_policy=None;router.quality=None;router.deadline=None
    router.generation=SimpleNamespace(name='native');router.track_width=150000;router.drill=200000;router.via_width=500000
    router.cells=lambda *args:[];router.solder_lands={};router.buckets={0:{},2:{}};router.clear=lambda *args,**kwargs:True
    policy.accepts_segment=lambda *args:False;policy.accepts_via=lambda *args:False
    return router,policy


def test_direct_local_leg_cannot_bypass_own_first_contact_policy():
    router,policy=direct_router()
    assert router.leg((0,0),(1000000,0),0,'control') is None


def test_direct_local_via_cannot_bypass_own_first_contact_policy():
    router,policy=direct_router()
    assert not router.via_clear((0,0),'control')


def test_direct_endpoint_uses_only_a_source_connected_actual_head(monkeypatch):
    api,board,cfg,receipt,state=receipt_scene(monkeypatch);router,policy=direct_router()
    policy.live_ports=[receipt['ports'][2]];policy.ports=policy.live_ports;policy.refresh=lambda graph:None
    router.board=board
    source=board.GetFootprints()[0].Pads()[2]
    assert router.endpoints(source,(20,2))==((2,2),[0])


def test_direct_endpoint_rejects_a_disconnected_recorded_head(monkeypatch):
    api,board,cfg,receipt,state=receipt_scene(monkeypatch,detached=('control',));router,policy=direct_router()
    policy.live_ports=[receipt['ports'][2]];policy.ports=policy.live_ports;policy.refresh=lambda graph:None
    router.board=board
    with pytest.raises(ValueError,match='terminal'):
        router.endpoints(board.GetFootprints()[0].Pads()[2],(20,2))


def test_native_synthetic_endpoint_keeps_its_real_face_without_a_uuid():
    router,policy=direct_router();policy.live_ports=[]
    item=SimpleNamespace(GetNetname=lambda:'control',GetPosition=lambda:SimpleNamespace(x=8,y=9),IsOnLayer=lambda layer:layer==2)
    assert router.endpoints(item,(20,20))==((8,9),[2])


def test_live_source_copper_is_protected_even_if_not_a_field_prerequisite(monkeypatch,tmp_path):
    result,observed,port=run_wrapper(monkeypatch,tmp_path)
    assert result['checked'].get('protected_ids')==['head']


def emission_router():
    router,policy=direct_router();emitted=[];router.added=[]
    class Copper:
        def __init__(self,board):self.m_Uuid=SimpleNamespace(AsString=lambda:'emitted')
        def __getattr__(self,key):return lambda *args:None
    native=router.add_path.__globals__['p'];native.PCB_TRACK=Copper;native.PCB_VIA=Copper;native.VIATYPE_THROUGH=0;native.F_Cu=0;native.B_Cu=2
    router.add_via.__globals__['prepare_added_via']=lambda via:None
    router.board=SimpleNamespace(FindNet=lambda net:net,Add=emitted.append);router.index=lambda item:None
    return router,policy,emitted


def test_direct_emission_cannot_bypass_first_contact_or_leave_a_partial_path():
    router,policy,emitted=emission_router();policy.accepts_segment=lambda a,z,*args:z[0]<=1
    with pytest.raises(ValueError):router.add_path([(0,0),(1,0),(2,0)],0,'control')
    assert emitted==[] and router.added==[]


def test_direct_via_emission_cannot_bypass_first_contact():
    router,policy,emitted=emission_router()
    with pytest.raises(ValueError):router.add_via((0,0),'control')
    assert emitted==[] and router.added==[]


def test_field_verify_debits_its_existing_15_second_allocation(monkeypatch,tmp_path):
    from smilepcb.keyboard_routing import field_contract
    run,board=operation_kernel(monkeypatch);observed=[]
    monkeypatch.setattr(field_contract,'load',lambda folder:{'scope':'matrix-with-controller'})
    monkeypatch.setattr(field_contract,'verify_native',lambda *args,**kwargs:observed.append(kwargs.get('deadline')) or [])
    monkeypatch.setattr(time,'monotonic',lambda:108)
    run({'operation':'field_verify','board':str(tmp_path/'keyboard.kicad_pcb')},preflight_elapsed_s=8)
    assert observed==[115]


def test_reported_tail_cleanup_debits_its_existing_30_second_allocation(monkeypatch,tmp_path):
    run,board=operation_kernel(monkeypatch);observed=[]
    def prune(*args,**kwargs):observed.append(kwargs.get('budget_s',30));return {}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.cleanup',SimpleNamespace(prune_reported_tails=prune))
    run({'operation':'prune_reported_tails','board':str(tmp_path/'keyboard.kicad_pcb'),'findings':[],'protected_ids':[]},preflight_elapsed_s=8)
    assert observed==[22]


def test_postoperation_source_check_does_not_trust_protected_flags(monkeypatch,tmp_path):
    with pytest.raises(ValueError,match='source copper'):
        run_wrapper(monkeypatch,tmp_path,mutate_source=True)


def test_fanout_reuses_a_verified_live_head_instead_of_another_pad_prefix(monkeypatch,tmp_path):
    from smilepcb.keyboard_routing.interface_launches import reserve_launches
    run,board=operation_kernel(monkeypatch);emitted=[]
    fp=SimpleNamespace(GetReference=lambda:'U_MCU',GetLayer=lambda:0,GetPosition=lambda:SimpleNamespace(x=0,y=0))
    pad=Pad('source','3','control',fp);fp.Pads=lambda:[pad];board.GetFootprints=lambda:[fp];board.GetTracks=lambda:[]
    native=run.__globals__['p'];native.F_Cu=0;native.B_Cu=2;native.FromMM=lambda mm:round(mm*1e6)
    native.SHAPE_SEGMENT=lambda *args:object()
    monkeypatch.setitem(sys.modules,'smilepcb.routing_style',SimpleNamespace(Clearance=lambda b:SimpleNamespace(clear=lambda *args:True),add_path=lambda *args:emitted.append(args) or ['created'],octile_paths=lambda *args:[]))
    router=lambda *args,**kwargs:SimpleNamespace(via_clear=lambda *args:False,clear=lambda *args:True,index=lambda *args:None,added=[])
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',SimpleNamespace(LocalRouter=router,vec=lambda v:v))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.power',SimpleNamespace(seed_plane_access=lambda *args,**kwargs:[]))
    (tmp_path/'.smilepcb-keyboard.json').write_text('{"plane_nets":[],"parts":[{"ref":"U_MCU","region":"controller"}]}')
    port={'net':'control','pad_ids':['source'],'added_ids':['head'],'exit_nm':[7000000,20000000],'exit_layer':2}
    with reserve_launches([port]):result=run({'operation':'fanout','board':str(tmp_path/'keyboard.kicad_pcb'),'include_nets':['control']})
    assert emitted==[], 'A live verified interface already owns this physical pad'
    assert result['ports'][0]['retained_interface'] is True


def policy_type():
    source=ROOT/'smilepcb/keyboard_routing/port_policy.py';node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='PortPolicy')
    env={'__name__':'smilepcb.keyboard_routing._policy_scope_host','__package__':'smilepcb.keyboard_routing'}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    return env['PortPolicy']


def test_dormant_missing_head_cannot_be_reactivated_after_a_native_reopen():
    policy=object.__new__(policy_type());policy.ports=[{'net':'closed','pad_ids':['source'],'added_ids':['retired'],'terminal_id':'retired'}]
    policy.items={'source':object()};policy.set_resolved({('source',)})
    with pytest.raises(ValueError,match='live controller'):
        policy.set_resolved(set())
    assert policy.resolved=={('source',)}, 'A rejected reactivation must keep the prior policy state exact'


def delta_kernel(monkeypatch,*,admit=False):
    source=ROOT/'smilepcb/keyboard_native.py';node=next((n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_admit_interface_delta'),None)
    assert node is not None, 'Every backend must pass the entry-interface delta admission'
    observed=[]
    class Policy:
        def __init__(self,board,ports,**kwargs):self.ports=ports;self.items={};self.resolved=set(kwargs.get('resolved',()))
        def set_resolved(self,resolved):self.resolved=set(resolved)
        def accepts_segment(self,a,z,layer,net,width):observed.append((a,z,layer,net,width));return admit
        def accepts_via(self,*args):return admit
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.port_policy',SimpleNamespace(PortPolicy=Policy))
    env={'p':SimpleNamespace(PCB_VIA=type('Via',(),{})), 'time':time}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    return env['_admit_interface_delta'],observed


def delta_scene():
    old={'class':'PCB_TRACK','net':'control','start':[5,20],'end':[7,20],'width':150000,'layer':0}
    new={'class':'PCB_TRACK','net':'control','start':[7,20],'end':[15,20],'width':150000,'layer':0}
    class Item(Track):
        def GetNetname(self):return 'control'
        def GetLayer(self):return 0
        def GetWidth(self):return 150000
    item=Item('raw','control',(7,20),(15,20))
    before={'tracks':{'head':old},'footprints':{}};after={'tracks':{'head':old,'raw':new},'footprints':{}}
    entry=SimpleNamespace(GetTracks=lambda:[],GetFootprints=lambda:[]);candidate=SimpleNamespace(GetTracks=lambda:[item])
    ports=[{'net':'control','pad_ids':[],'added_ids':['head'],'source_shapes':{'head':old}}]
    return entry,candidate,before,after,ports


def test_raw_backend_delta_cannot_skip_entry_first_contact_admission(monkeypatch):
    validate,observed=delta_kernel(monkeypatch)
    with pytest.raises(ValueError,match='first-contact'):
        validate(*delta_scene(),(),deadline=time.monotonic()+1)
    assert observed==[((7,20),(15,20),0,'control',150000)]


def test_valid_head_delta_is_accepted_using_its_actual_endpoints_and_width(monkeypatch):
    validate,observed=delta_kernel(monkeypatch,admit=True)
    validate(*delta_scene(),(),deadline=time.monotonic()+1)
    assert observed==[((7,20),(15,20),0,'control',150000)]


def test_delta_admission_cannot_renew_an_expired_operation_deadline(monkeypatch):
    validate,observed=delta_kernel(monkeypatch,admit=True)
    with pytest.raises(TimeoutError):validate(*delta_scene(),(),deadline=time.monotonic()-1)
    assert observed==[]


def collision_router():
    router,policy=direct_router();policy._launch_conflict=lambda *args:False
    native=router.collisions.__globals__['p'];native.SH_SEGMENT=1;native.SH_CIRCLE=2;native.VECTOR2I=lambda x,y:(x,y)
    class Shape:
        def BBox(self):return SimpleNamespace(GetLeft=lambda:0,GetTop=lambda:0,GetRight=lambda:1,GetBottom=lambda:1)
        def Type(self):return 1
    class Segment(Shape):
        def GetSeg(self):return SimpleNamespace(A=SimpleNamespace(x=0,y=0),B=SimpleNamespace(x=1,y=0))
    native.SHAPE_SEGMENT=Segment
    router.reservations=[];router.outline=SimpleNamespace(Contains=lambda point:True);router.edge_buckets={}
    router.net_clearances={};router.generation.foreign_copper_clearance_nm=0
    return router,policy,Segment,Shape


def test_concrete_candidate_clear_uses_exact_capsule_before_any_emission():
    router,policy,Segment,Shape=collision_router();shape=Segment();observed=[]
    policy.accepts_segment=lambda *args,**kwargs:observed.append(kwargs.get('shape')) or False
    assert list(router.collisions(shape,0,'control'))
    assert observed==[shape]


def test_base_effective_shape_is_only_a_physical_foreign_check_without_a_downcast():
    router,policy,Segment,Shape=collision_router();observed=[]
    policy.accepts_segment=lambda *args,**kwargs:observed.append(args) or False
    assert list(router.collisions(Shape(),0,'control'))==[]
    assert observed==[]


def test_legacy_operation_hook_keeps_its_original_one_argument_dispatch(monkeypatch,tmp_path):
    result,observed,port=run_wrapper(monkeypatch,tmp_path,full=False,legacy_signature=True)
    assert result['ports']==[port] and observed==[False]


def test_closed_source_subset_trim_adds_no_copper_and_needs_no_new_contact(monkeypatch):
    validate,observed=delta_kernel(monkeypatch)
    entry,candidate,before,after,ports=delta_scene()
    after['tracks']={'head':{**before['tracks']['head'],'end':[6,20]}}
    item=Track('head','control',(5,20),(6,20));item.GetLayer=lambda:0;item.GetWidth=lambda:150000
    candidate.GetTracks=lambda:[item]
    validate(entry,candidate,before,after,ports,(),deadline=time.monotonic()+1)
    assert observed==[]


def test_modified_track_extension_checks_only_its_exact_added_piece(monkeypatch):
    validate,observed=delta_kernel(monkeypatch,admit=True)
    entry,candidate,before,after,ports=delta_scene()
    after['tracks']={'head':{**before['tracks']['head'],'end':[8,20]}}
    item=Track('head','control',(5,20),(8,20));item.GetLayer=lambda:0;item.GetWidth=lambda:150000
    candidate.GetTracks=lambda:[item]
    validate(entry,candidate,before,after,ports,(),deadline=time.monotonic()+1)
    assert observed==[((7,20),(8,20),0,'control',150000)]


@pytest.mark.parametrize('change',[{'end':[7,21]},{'width':200000},{'layer':2},{'net':'other'}])
def test_non_subset_changes_still_require_new_first_contact(monkeypatch,change):
    validate,observed=delta_kernel(monkeypatch)
    entry,candidate,before,after,ports=delta_scene()
    item=Track('head','control',(5,20),(7,20));item.GetLayer=lambda:0;item.GetWidth=lambda:150000
    candidate.GetTracks=lambda:[item];after['tracks']={'head':{**before['tracks']['head'],**change}}
    with pytest.raises(ValueError,match='first-contact'):
        validate(entry,candidate,before,after,ports,(),deadline=time.monotonic()+1)
