"""Host policy/propagation contracts; Native physical qualification is separate."""
import ast
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest
from smilepcb.usb_policy import DEFAULT_USB_MAX_LENGTH_NM,resolve_usb_max_length_nm

ROOT=Path(__file__).resolve().parents[1]


def extracted(path,name,namespace=None):
    tree=ast.parse((ROOT/path).read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    scope={} if namespace is None else namespace
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/path),'exec'),scope)
    return scope[name]


def test_default_is_explicit_legacy_strict_twenty_mm():
    assert DEFAULT_USB_MAX_LENGTH_NM==20_000_000
    assert resolve_usb_max_length_nm()==20_000_000
    assert resolve_usb_max_length_nm(Ellipsis)==20_000_000
    assert resolve_usb_max_length_nm(None) is None
    assert resolve_usb_max_length_nm(45_000_000)==45_000_000


@pytest.mark.parametrize('limit',[True,False,0,-1,20_000_000.0,'20000000',float('inf')])
def test_malformed_limit_rejects_at_every_entry_before_native_work(limit):
    with pytest.raises(ValueError):resolve_usb_max_length_nm(limit)
    calls=[('smilepcb/keyboard_native.py','verify_usb_pair',lambda f:f(None,max_length_nm=limit)),
           ('smilepcb/usb_pair.py','balance_pair',lambda f:f(None,('a','b'),editable_ids=[],max_length_nm=limit)),
           ('smilepcb/usb_pair.py','try_joint_pair',lambda f:f(None,('a','b'),source_ref='s',target_ref='t',layer=2,deadline=10,max_length_nm=limit)),
           ('smilepcb/keyboard_routing/usb.py','route_controller_usb',lambda f:f(None,deadline=10,max_length_nm=limit)),
           ('smilepcb/stm32/usb_placement.py','select_usb_placement',lambda f:f(None,None,{},preferred_nm=[],legal=None,deadline=10,max_length_nm=limit)),
           ('smilepcb/stm32/usb_placement.py','verify_prepared_usb',lambda f:f(None,{},deadline=10,max_length_nm=limit))]
    for path,name,call in calls:
        with pytest.raises(ValueError):call(extracted(path,name))


class Track:
    def __init__(self,net,length):self.net=net;self.length=length;self.m_Uuid=NS(AsString=lambda:net)
    def GetNetname(self):return self.net
    def GetLength(self):return self.length
    def GetLayer(self):return 2
    def GetClass(self):return 'PCB_TRACK'


def test_balance_uses_explicit_limit_and_exact_upper_bound():
    tracks=[Track('a',22_000_000),Track('b',22_100_000)]
    board=NS(GetTracks=lambda:tracks)
    balance=extracted('smilepcb/usb_pair.py','balance_pair')
    with pytest.raises(ValueError):balance(board,('a','b'),editable_ids=[])
    assert balance(board,('a','b'),editable_ids=[],max_length_nm=30_000_000)['lengths_mm']=={'a':22.,'b':22.1}
    with pytest.raises(ValueError):balance(board,('a','b'),editable_ids=[],max_length_nm=22_100_000)


@pytest.mark.parametrize('limit,expected',[(Ellipsis,20_000_000),(30_000_000,30_000_000),(None,None)])
def test_route_passes_one_resolved_scalar_to_joint_balance_and_final_verify(monkeypatch,limit,expected):
    seen=[];state={'tracks':{},'footprints':{}}
    def joint(*a,**kw):seen.append(('joint',kw['max_length_nm']));return {net:{'added_ids':[]} for net in ('USB_DP','USB_DM')}
    def balance(*a,**kw):seen.append(('balance',kw['max_length_nm']));return {'changed':False}
    def verify(*a,**kw):seen.append(('verify',kw['max_length_nm']));return {'USB_DP':22.,'USB_DM':22.1}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_native',NS(verify_usb_pair=verify))
    board=NS(GetFootprints=lambda:[NS(GetReference=lambda:'U_MCU',GetLayer=lambda:2)],GetLayerName=lambda _: 'B.Cu')
    route=extracted('smilepcb/keyboard_routing/usb.py','route_controller_usb',dict(snapshot=lambda _:deepcopy(state),time=NS(monotonic=lambda:0),try_joint_pair=joint,balance_pair=balance,current_generation_profile=lambda:NS(name='native')))
    result=route(board,deadline=10,max_length_nm=limit)
    assert result['accepted'] and seen==[('joint',expected),('balance',expected),('verify',expected)]
    assert result['acceptance_context']['max_length_nm']==expected


@pytest.mark.parametrize('limit',[30_000_000,None])
def test_fixed_placement_passes_limit_and_stamps_same_acceptance_context(limit):
    seen=[];state={'tracks':{},'footprints':{}};pose={'origin_nm':[1,2],'rotation_deg':90.,'side':'B.Cu'}
    connector=NS(m_Uuid=NS(AsString=lambda:'connector'),SetOrientationDegrees=lambda _:None,SetPosition=lambda _:None)
    def route(*a,**kw):seen.append(kw['max_length_nm']);return dict(accepted=True,verified_lengths_mm={'USB_DP':22.,'USB_DM':22.1},added_ids=[])
    def context(*a,**kw):return dict(max_length_nm=kw['max_length_nm'])
    scope=dict(snapshot=lambda _:deepcopy(state),_pose=lambda *a:deepcopy(pose),_fingerprint=lambda _: 'hash',time=NS(monotonic=lambda:0),p=NS(VECTOR2I=lambda *a:a),route_controller_usb=route,_acceptance_context=context)
    select=extracted('smilepcb/stm32/usb_placement.py','select_usb_placement',scope)
    result=select(NS(),connector,{'parts':[{'ref':'J_UDB'}]},preferred_nm=[1,2],legal=lambda _:True,deadline=10,fixed_pose=pose,max_length_nm=limit)
    assert result['accepted'] and seen==[limit]
    assert result['usb']['acceptance_context']=={'max_length_nm':limit}


def test_prepared_receipt_cannot_change_limit_and_legacy_missing_key_is_default_only(monkeypatch):
    class Pad:
        def __init__(self,ref):self.ref=ref
        def GetParentFootprint(self):return NS(GetReference=lambda:self.ref)
    graph=dict(nodes=[dict(net=net,item=Pad(ref)) for net in ('USB_DP','USB_DM') for ref in ('U_MCU','J_UDB')],groups={0:[0,1],2:[2,3]})
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',NS(LocalRouter=lambda *a,**kw:NS(expired=lambda:False)))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',NS(copper_graph=lambda *a,**kw:graph))
    measured=[];lengths={'USB_DP':10.,'USB_DM':10.1}
    def verify(*a,**kw):measured.append(kw['max_length_nm']);return lengths
    scope=dict(time=NS(monotonic=lambda:0),snapshot=lambda _: {},_fingerprint=lambda _:'hash',_acceptance_context=lambda *a,**kw:dict(rules='same',max_length_nm=kw['max_length_nm']),verify_usb_pair=verify,p=NS(PAD=Pad))
    check=extracted('smilepcb/stm32/usb_placement.py','verify_prepared_usb',scope)
    prepared=dict(accepted=True,board_state_sha256='hash',acceptance_context=dict(rules='same',max_length_nm=30_000_000),verified_lengths_mm=lengths,added_ids=[])
    board=NS(GetTracks=lambda:[])
    with pytest.raises(ValueError,match='context'):check(board,prepared,deadline=10)
    for limit in (None,40_000_000):
        with pytest.raises(ValueError,match='context'):check(board,prepared,deadline=10,max_length_nm=limit)
    assert measured==[]
    assert check(board,prepared,deadline=10,max_length_nm=30_000_000) is prepared
    legacy=deepcopy(prepared);del legacy['acceptance_context']['max_length_nm']
    assert check(board,legacy,deadline=10) is legacy
    with pytest.raises(ValueError,match='context'):check(board,legacy,deadline=10,max_length_nm=30_000_000)
    unbounded=deepcopy(prepared);unbounded['acceptance_context']['max_length_nm']=None
    with pytest.raises(ValueError,match='context'):check(board,unbounded,deadline=10)
    assert check(board,unbounded,deadline=10,max_length_nm=None) is unbounded
    assert measured==[30_000_000,20_000_000,None]


def test_balance_explicit_none_accepts_finite_si_unqualified_long_pair():
    tracks=[Track('a',60_000_000),Track('b',60_100_000)];board=NS(GetTracks=lambda:tracks)
    balance=extracted('smilepcb/usb_pair.py','balance_pair')
    with pytest.raises(ValueError):balance(board,('a','b'),editable_ids=[])
    assert balance(board,('a','b'),editable_ids=[],max_length_nm=None)['lengths_mm']=={'a':60.,'b':60.1}
    for length in (0,float('inf'),float('nan')):
        tracks[0].length=length
        with pytest.raises(ValueError):balance(board,('a','b'),editable_ids=[],max_length_nm=None)


def test_joint_candidate_uses_explicit_unbounded_policy_and_strict_finite_limit(monkeypatch):
    import math,time
    class Pad:
        def __init__(self,uid,ref,net,point):self.m_Uuid=NS(AsString=lambda:uid);self.ref=ref;self.net=net;self.point=point
        def GetParentFootprint(self):return NS(GetReference=lambda:self.ref)
        def GetNetname(self):return self.net
        def GetPosition(self):return self.point
        def IsOnLayer(self,layer):return layer==2
    sources={net:Pad('s'+net,'source',net,(0,0)) for net in ('a','b')}
    targets={net:Pad('t'+net,'target',net,(length,0)) for net,length in [('a',60_000_000),('b',60_100_000)]}
    board=NS(GetTracks=lambda:[],GetFootprints=lambda:[NS(GetReference=lambda:'source',Pads=lambda:list(sources.values())),NS(GetReference=lambda:'target',Pads=lambda:list(targets.values()))])
    def graph(board,nets,deadline):
        net=next(iter(nets));return dict(nodes=[dict(item=sources[net]),dict(item=targets[net])],groups={0:[0],1:[1]},find=lambda i:i)
    class Router:
        def __init__(self,*a,**kw):self.added=[];self.net_clearances={'a':0,'b':0}
        def expired(self):return False
        def clear(self,*a):return True
        def add_path(self,path,layer,net):self.added.append('new'+net)
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.pair_paths',NS(paired_octile_paths=lambda starts,ends:[[list(pair) for pair in zip(starts,ends)]]))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',NS(LocalRouter=Router))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',NS(copper_graph=graph))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.completion',NS(component_frontiers=lambda *a:[(0,0)]))
    scope=dict(__name__='smilepcb.usb_pair',__package__='smilepcb',math=math,p=NS(PAD=Pad,SHAPE_SEGMENT=lambda *a:a,SHAPE=NS(Collide=lambda *a:False)),xy=lambda p:p,vec=lambda p:p)
    joint=extracted('smilepcb/usb_pair.py','try_joint_pair',scope);kwargs=dict(source_ref='source',target_ref='target',layer=2,deadline=time.monotonic()+10)
    assert joint(board,('a','b'),**kwargs) is None
    assert joint(board,('a','b'),max_length_nm=60_100_000,**kwargs) is None
    assert set(joint(board,('a','b'),max_length_nm=None,**kwargs))=={'a','b'}
