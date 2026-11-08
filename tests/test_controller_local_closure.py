"""HOST proof-boundary controls; native geometry is tested separately."""
from copy import deepcopy
from types import SimpleNamespace
import sys,time
import pytest
from test_transient_generated_access import kernel,scene
from smilepcb.keyboard_routing.generation import generation_profile


def closed_scene(monkeypatch):
    board,cfg,before,proof=scene(monkeypatch,"fixed")
    for state in (before,board.state):
        for uid in ("lead","via"):state["tracks"].pop(uid,None)
        d=deepcopy(state["footprints"]["component"]["pads"][0]);d.update(uuid="load",number="1",position=[2000000,0])
        state["footprints"]["load"]={"ref":"R_LOCAL","pads":[d]}
    cfg["parts"].append({"ref":"R_LOCAL","region":"controller","nets":{"1":"control"}})
    cfg["routing_preparation"]={"mode":"ordered-banks-v2","generation_profile":"dsn-process-v2"}
    proof.update(ports=[],added_ids=[],local_closed={"schema":1,"generation_profile":"dsn-process-v2","nets":{"control":{"pads":{
        d["uuid"]:{"ref":f["ref"],"pad":deepcopy(d)} for f in board.state["footprints"].values() for d in f["pads"]}}}})
    def graph(*args,**kwargs):
        nodes=[{"item":SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u:u)),"net":"control"} for u in ("pad","load")]
        return {"nodes":nodes,"find":lambda i:0}
    monkeypatch.setitem(sys.modules,"smilepcb.native_worker",SimpleNamespace(snapshot=lambda b:deepcopy(b.state)))
    monkeypatch.setitem(sys.modules,"smilepcb.keyboard_routing.native_graph",SimpleNamespace(copper_graph=graph))
    return board,cfg,before,proof


def test_proven_whole_local_closure_covers_fixed_function_without_dummy_head(monkeypatch):
    args=closed_scene(monkeypatch)
    with generation_profile("dsn-process-v2"):
        owned=kernel().verified_provisional_ownership(*args,deadline=time.monotonic()+1)
    assert owned.tracks=={} and owned.pads=={}


@pytest.mark.parametrize('change',['alias','region','binding','shape','extra-field-pad','broken-contact','matrix','plane'])
def test_local_head_exemption_requires_every_actual_alias_binding_and_native_contact(monkeypatch,change):
    board,cfg,before,proof=closed_scene(monkeypatch)
    if change=='alias':
        d=deepcopy(board.state['footprints']['component']['pads'][0]);d['uuid']='second-physical-alias'
        board.state['footprints']['component']['pads'].append(d)
    elif change=='region':cfg['parts'][1]['region']='rgb'
    elif change=='binding':cfg['parts'][1]['nets']['1']='other'
    elif change=='shape':board.state['footprints']['load']['pads'][0]['size']=[700000,500000]
    elif change=='extra-field-pad':
        d=deepcopy(board.state['footprints']['load']['pads'][0]);d['uuid']='outside'
        board.state['footprints']['field']={'ref':'LED_REMOTE','pads':[d]}
        cfg['parts'].append({'ref':'LED_REMOTE','region':'rgb','nets':{'1':'control'}})
    elif change=='broken-contact':
        graph=sys.modules['smilepcb.keyboard_routing.native_graph'];original=graph.copper_graph
        def disconnected(*args,**kwargs):
            g=original(*args,**kwargs);g['find']=lambda i:i;return g
        graph.copper_graph=disconnected
    elif change=='matrix':
        cfg['layout']['gpio']['rows']=['A0'];cfg['mcu_gpio_pads']['A0']='7'
    else:cfg['plane_nets']=['control']
    from smilepcb.keyboard_routing.controller_local import verify_local_closures
    with generation_profile('dsn-process-v2'),pytest.raises(ValueError):
        verify_local_closures(board,cfg,proof['local_closed'],deadline=time.monotonic()+1)


def test_external_load_preserves_head_while_its_controller_subgraph_remains_routable(monkeypatch):
    board,cfg,_,proof=closed_scene(monkeypatch)
    d=deepcopy(board.state['footprints']['load']['pads'][0]);d['uuid']='external-led'
    board.state['footprints']['external']={'ref':'LED_REMOTE','pads':[d]}
    cfg['parts'].append({'ref':'LED_REMOTE','region':'rgb','nets':{'1':'control'}})
    from smilepcb.keyboard_routing.controller_local import local_targets,capture_local_closures
    assert local_targets(board,cfg)=={'control':{'pad','load'}}
    with generation_profile('dsn-process-v2'):
        assert capture_local_closures(board,cfg,deadline=time.monotonic()+1)['nets']=={}


def test_raw_native_tuple_shapes_and_json_roundtrip_are_both_exact_proofs(monkeypatch):
    board,cfg,_,_=closed_scene(monkeypatch)
    for f in board.state['footprints'].values():
        for d in f['pads']:d['position']=tuple(d['position'])
    from smilepcb.keyboard_routing.controller_local import capture_local_closures,verify_local_closures
    import json
    with generation_profile('dsn-process-v2'):
        proof=capture_local_closures(board,cfg,deadline=time.monotonic()+1)
        assert verify_local_closures(board,cfg,proof,deadline=time.monotonic()+1)=={'control':{'pad'}}
        assert verify_local_closures(board,cfg,json.loads(json.dumps(proof)),deadline=time.monotonic()+1)=={'control':{'pad'}}


def test_missing_proof_keeps_old_fixed_alias_coverage_strict(monkeypatch):
    args=closed_scene(monkeypatch);args[3].pop('local_closed')
    with generation_profile('dsn-process-v2'),pytest.raises(ValueError,match='omitted'):
        kernel().verified_provisional_ownership(*args,deadline=time.monotonic()+1)


@pytest.mark.parametrize('profile',['native','dsn-safe-v1'])
def test_local_proof_cannot_be_created_by_legacy_generation(monkeypatch,profile):
    board,cfg,_,_=closed_scene(monkeypatch)
    from smilepcb.keyboard_routing.controller_local import capture_local_closures
    with generation_profile(profile),pytest.raises(ValueError,match='explicit v2'):
        capture_local_closures(board,cfg,deadline=time.monotonic()+1)


def test_expired_local_proof_is_not_accepted(monkeypatch):
    board,cfg,_,proof=closed_scene(monkeypatch)
    from smilepcb.keyboard_routing.controller_local import verify_local_closures
    with generation_profile('dsn-process-v2'),pytest.raises(TimeoutError):
        verify_local_closures(board,cfg,proof['local_closed'],deadline=time.monotonic()-1)


def physical_inventory(board,monkeypatch):
    class Pad:
        def __init__(self,d):self.d=d;self.m_Uuid=SimpleNamespace(AsString=lambda:d['uuid'])
        def GetNumber(self):return self.d['number']
        def GetNetname(self):return self.d['net']
    for f in board.state['footprints'].values():
        f.update(position=[0,0],rotation=0,layer=0,locked=False)
    board.GetFootprints=lambda:[SimpleNamespace(GetReference=lambda f=f:f['ref'],Pads=lambda f=f:[Pad(d) for d in f['pads']]) for f in board.state['footprints'].values()]
    sys.modules['smilepcb.native_worker'].p=SimpleNamespace(PAD=Pad)


def test_durable_capture_reproves_local_closure_and_old_receipt_stays_strict(monkeypatch):
    board,cfg,_,proof=closed_scene(monkeypatch);physical_inventory(board,monkeypatch)
    from smilepcb.keyboard_routing.interface_contract import capture_interfaces,verify_interfaces
    with generation_profile('dsn-process-v2'):
        receipt=capture_interfaces(board,cfg,[],deadline=time.monotonic()+1,local_closed=proof['local_closed'])
        assert receipt['ports']==[] and receipt['tracks']=={} and receipt['local_closed']==proof['local_closed']
        assert verify_interfaces(board,cfg,receipt,deadline=time.monotonic()+1)['accepted']
        legacy=deepcopy(receipt);legacy.pop('local_closed')
        with pytest.raises(ValueError,match='omitted'):verify_interfaces(board,cfg,legacy,deadline=time.monotonic()+1)
        board.state['footprints']['load']['pads'][0]['size']=[600000,500000]
        with pytest.raises(ValueError,match='Local closure'):verify_interfaces(board,cfg,receipt,deadline=time.monotonic()+1)


def test_legacy_model_cannot_smuggle_new_omission_metadata(monkeypatch):
    board,cfg,_,proof=closed_scene(monkeypatch);physical_inventory(board,monkeypatch)
    cfg['routing_preparation']={'mode':'ordered-banks-v2','generation_profile':'dsn-safe-v1'}
    from smilepcb.keyboard_routing.interface_contract import capture_interfaces
    with generation_profile('dsn-safe-v1'),pytest.raises(ValueError,match='v2 interface model'):
        capture_interfaces(board,cfg,[],deadline=time.monotonic()+1,local_closed=proof['local_closed'])


@pytest.mark.parametrize('expire',['none','search','proof'])
def test_early_phase_keeps_existing_deadline_and_rolls_back_unproved_copper(monkeypatch,expire):
    board,cfg,_,_=closed_scene(monkeypatch);before=deepcopy(board.state);clock=[100.0];calls=[]
    from smilepcb.keyboard_routing import controller_local as api
    monkeypatch.setattr(api,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    board.GetDesignSettings=lambda:SimpleNamespace(m_TrackMinWidth=150000)
    board.GetTracks=lambda:[SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u:u)) for u in board.state['tracks']]
    board.Delete=lambda t:board.state['tracks'].pop(t.m_Uuid.AsString())
    board.BuildConnectivity=lambda:None
    class Router:
        def __init__(self,b,**kw):self.deadline=kw['deadline'];calls.append(kw);self.graph_diagnostic={'HOST_protocol_only':True}
    def join(b,router,nets,**kw):
        assert nets=={'control'} and kw['required_pad_ids']=={'pad','load'}
        b.state['tracks']['functional-local']={**deepcopy(b.state['tracks']['usb']),'net':'control'}
        if expire=='search':clock[0]=121
        return 1
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',SimpleNamespace(LocalRouter=Router))
    monkeypatch.setattr(sys.modules['smilepcb.keyboard_routing.native_graph'],'route_components',join,raising=False)
    if expire=='proof':
        original_capture=api.capture_local_closures
        def last_proof(*a,**kw):
            result=original_capture(*a,**kw);clock[0]=121;return result
        monkeypatch.setattr(api,'capture_local_closures',last_proof)
    with generation_profile('dsn-process-v2'):result=api.prepare_controller_local(board,cfg,deadline=130)
    assert calls[0]['deadline']==117 and calls[0]['deadline']<130
    if expire!='none':
        assert not result['accepted'] and result['added_ids']==[] and 'local_closed' not in result and board.state==before
    else:
        assert result['accepted'] and result['added_ids']==['functional-local'] and set(result['local_closed']['nets'])=={'control'}
        assert all(board.state['tracks'][u]==shape for u,shape in before['tracks'].items())


def test_matrix_and_plane_pad_groups_are_not_early_local_targets(monkeypatch):
    board,cfg,_,_=closed_scene(monkeypatch)
    from smilepcb.keyboard_routing.controller_local import local_targets
    cfg['plane_nets']=['control'];assert local_targets(board,cfg)=={}
    cfg['plane_nets']=[];cfg['layout']['gpio']['rows']=['A0'];cfg['mcu_gpio_pads']['A0']='7'
    assert local_targets(board,cfg)=={}


def test_actual_finalizer_publication_rebinds_and_reproves_local_metadata(monkeypatch,tmp_path):
    import ast,json
    from pathlib import Path
    board,cfg,_,proof=closed_scene(monkeypatch);physical_inventory(board,monkeypatch)
    from smilepcb.keyboard_routing.interface_contract import capture_interfaces,verify_interfaces,MARKER
    with generation_profile('dsn-process-v2'):
        receipt=capture_interfaces(board,cfg,[],deadline=time.monotonic()+1,local_closed=proof['local_closed'])
        for f in board.state['footprints'].values():f['path']='/'+f['ref']
        before=deepcopy(board.state);mapping={'control':'/controller/control'}
        for f in board.state['footprints'].values():
            for d in f['pads']:d['net']=mapping.get(d['net'],d['net'])
        for part in cfg['parts']:
            for number,net in part['nets'].items():part['nets'][number]=mapping.get(net,net)
        original=sys.modules['smilepcb.keyboard_routing.native_graph'].copper_graph
        def rebound_graph(*args,**kwargs):
            graph=original(*args,**kwargs)
            for node in graph['nodes']:node['net']='/controller/control'
            return graph
        monkeypatch.setattr(sys.modules['smilepcb.keyboard_routing.native_graph'],'copper_graph',rebound_graph)
        (tmp_path/MARKER).write_text(json.dumps(receipt))
        source=Path(__file__).resolve().parents[1]/'smilepcb/stm32/finalize_native.py'
        publication=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.With) and any(isinstance(q.context_expr,ast.Call) and isinstance(q.context_expr.func,ast.Name) and q.context_expr.func.id=='rollback_revision' for q in n.items))
        blocks=[n for n in publication.body if isinstance(n,ast.If) and ast.unparse(n.test)=='contract is not None']
        published=[];env={'contract':{},'b':board,'cfg':cfg,'path':tmp_path/'keyboard.kicad_pcb','before':before,'remap':mapping,'export_paths':{f['ref']:f['path'] for f in board.state['footprints'].values()},'time':time,'json':json,'snapshot':lambda b:deepcopy(b.state),'publish':lambda *a,**kw:published.append(True)}
        exec(compile(ast.Module(body=[blocks[-1]],type_ignores=[]),str(source),'exec'),env)
        result=json.loads((tmp_path/MARKER).read_text())
        assert published==[True] and set(result['local_closed']['nets'])=={'/controller/control'}
        assert verify_interfaces(board,cfg,result,deadline=time.monotonic()+1)['accepted']


def test_normal_controller_ports_does_not_recreate_verified_local_heads(monkeypatch,tmp_path):
    import ast,json
    from pathlib import Path
    board,cfg,_,proof=closed_scene(monkeypatch);physical_inventory(board,monkeypatch)
    from smilepcb.keyboard_routing.interface_contract import capture_interfaces,MARKER
    with generation_profile('dsn-process-v2'):
        receipt=capture_interfaces(board,cfg,[],deadline=time.monotonic()+1,local_closed=proof['local_closed'])
        (tmp_path/MARKER).write_text(json.dumps(receipt));(tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps(cfg))
        settings=SimpleNamespace(m_CopperEdgeClearance=200000,m_MinClearance=150000);board.GetDesignSettings=lambda:settings
        requested=[]
        def ports(*args,**kwargs):requested.append(kwargs['include_nets']);return {'accepted':True,'ports':[],'added_ids':[]}
        monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_ports',SimpleNamespace(route_controller_ports=ports))
        monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.power',SimpleNamespace(seed_controller_returns=lambda *a,**kw:{'accepted':True}))
        source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_native.py'
        run=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_run')
        block=next(n for n in run.body if isinstance(n,ast.If) and ast.unparse(n.test)=="request['operation'] == 'controller_ports'")
        wrapped=ast.parse('def call(request):\n pass\n').body[0];wrapped.body=[block]
        env={'p':SimpleNamespace(LoadBoard=lambda path:board,SaveBoard=lambda *a:True),'validate':lambda *a:None,'w':SimpleNamespace(snapshot=lambda b:deepcopy(b.state)),'Path':Path,'json':json,'remaining_budget':lambda seconds:seconds}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapped],type_ignores=[])),str(source),'exec'),env)
        assert env['call']({'operation':'controller_ports','board':str(tmp_path/'keyboard.kicad_pcb')})['accepted']
        assert requested==[set()]


@pytest.mark.parametrize('change',['geometry','alias','merge','undeclared-net'])
def test_compose_rebind_permits_only_exact_declared_labels(monkeypatch,change):
    board,cfg,_,proof=closed_scene(monkeypatch);before=deepcopy(board.state);after=deepcopy(before);remap={'control':'/control'}
    for f in after['footprints'].values():
        for d in f['pads']:d['net']=remap.get(d['net'],d['net'])
    local=proof['local_closed']
    if change=='geometry':after['footprints']['load']['pads'][0]['position'][0]+=1
    elif change=='alias':local['nets']['control']['pads'].pop('pad')
    elif change=='merge':remap['foreign']='/control'
    else:after['footprints']['load']['pads'][0]['net']='/undeclared'
    from smilepcb.keyboard_routing.controller_local import rebind_local_closures
    with pytest.raises(ValueError):rebind_local_closures(before,after,local,net_remap=remap)
