"""Dormant-source lifecycle and exact-capsule policy logic on the host."""
import ast
from copy import deepcopy
import math
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launches,current_launch_inventory,interface_identity
from smilepcb.keyboard_routing.bank_geometry import terminal_origin

ROOT=Path(__file__).resolve().parents[1]

class Shape:
    def __init__(self,token="capsule",hits=()):self.token=token;self.hits=set(hits)
    def BBox(self):return SimpleNamespace(GetLeft=lambda:0,GetRight=lambda:100,GetTop=lambda:0,GetBottom=lambda:100)

class Track:
    def __init__(self,uid="head",a=(0,0),z=(1000000,0)):
        self.uid=uid;self.a=a;self.z=z;self.m_Uuid=SimpleNamespace(AsString=lambda:uid)
    def GetClass(self):return "PCB_TRACK"
    def GetWidth(self):return 150000
    def GetNetname(self):return "control"
    def IsOnLayer(self,layer):return layer==0
    def GetEffectiveShape(self,layer):return Shape(self.uid)
    def state(self):return {"class":"PCB_TRACK","start":list(self.a),"end":list(self.z),"width":150000,"layer":0,"net":"control","locked":False}

class Pad:
    def __init__(self,uid):self.uid=uid;self.position=[0,0];self.m_Uuid=SimpleNamespace(AsString=lambda:uid)
    def GetNumber(self):return "1"
    def GetNetname(self):return "control"
    def IsOnLayer(self,layer):return layer==0
    def GetEffectiveShape(self,layer):return Shape(self.uid)
    def state(self):return {"uuid":self.uid,"number":"1","net":"control","position":list(self.position),"size":[300000,700000],"shape":1,"rotation":0,"drill":[0,0],"drill_shape":1,"layers":"F.Cu"}

class Board:
    def __init__(self):self.tracks={"head":Track()};self.pads=[Pad("source"),Pad("field")]
    def GetTracks(self):return list(self.tracks.values())
    def GetFootprints(self):return [SimpleNamespace(Pads=lambda:self.pads)]
    def GetDesignSettings(self):return SimpleNamespace(m_TrackMinWidth=150000,m_MinClearance=150000)
    def state(self):return {"tracks":{u:t.state() for u,t in self.tracks.items()},"footprints":{"all":{"pads":[d.state() for d in self.pads]}}}
    def graph(self,closed):
        nodes=[{"item":d,"net":"control"} for d in self.pads];roots=[0,0 if closed else 1]
        for t in self.tracks.values():nodes.append({"item":t,"net":"control"});roots.append(0)
        return {"nodes":nodes,"find":lambda i:roots[i],"nets":{"control"}}

@pytest.fixture
def scene(monkeypatch):
    board=Board();native=SimpleNamespace(F_Cu=0,B_Cu=2,PAD=Pad,PCB_VIA=type("Via",(),{}),
        SHAPE_SEGMENT=lambda *args:Shape(hits={"head"}),SHAPE=SimpleNamespace(Collide=lambda a,b,gap:b.token in a.hits))
    monkeypatch.setitem(sys.modules,"smilepcb.native_worker",SimpleNamespace(p=native,snapshot=lambda b:deepcopy(b.state())))
    monkeypatch.setitem(sys.modules,"smilepcb.routing_style",SimpleNamespace(own_clearance=lambda *args:150000))
    source=ROOT/"smilepcb/keyboard_routing/port_policy.py";node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=="PortPolicy")
    env={"__name__":"smilepcb.keyboard_routing._dormant_host","__package__":"smilepcb.keyboard_routing","math":math,"p":native,"vec":lambda p:p,"terminal_origin":terminal_origin}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),"exec"),env)
    port={"net":"control","pad_ids":["source"],"added_ids":["head"],"terminal_id":"head","kind":"same_layer","exit_nm":[1000000,0],"heading_origin_nm":[0,0],"exit_layer":0}
    state=board.state();port["source_shapes"]={"head":state["tracks"]["head"],"source":state["footprints"]["all"]["pads"][0]}
    return board,port,env["PortPolicy"]


def test_scope_retains_dormant_metadata_and_reopens_from_fresh_native_graph(scene):
    board,port,Policy=scene;key=interface_identity(port)
    with reserve_launches([port],resolved=[list(key)]):
        assert current_launch_inventory().resolved=={key}
        assert current_launches()[0]["source_shapes"]==port["source_shapes"]
        current_launch_inventory().refresh(board.graph(False))
        assert current_launch_inventory().resolved==set()
        assert current_launches()[0]["terminal_id"]=="head"


def test_restored_original_head_reactivates_items_and_foreign_runway(scene):
    board,port,Policy=scene;key=interface_identity(port);original=board.tracks.pop("head")
    policy=Policy(board,[port],resolved={key})
    board.tracks["head"]=Track()  # Same original geometry, new native object.
    policy.refresh(board.graph(False))
    assert policy.live_ports==[port] and policy.items["head"] is board.tracks["head"]
    assert policy._launch_conflict(Shape(hits={"capsule"}),0,"foreign")


@pytest.mark.parametrize("change",["moved","retired"])
def test_changed_live_head_fails_refresh_without_publishing_policy_state(scene,change):
    board,port,Policy=scene;policy=Policy(board,[port]);old=set(policy.resolved)
    if change=="moved":board.tracks["head"].z=(2000000,0)
    else:board.tracks.pop("head")
    with pytest.raises(ValueError):policy.refresh(board.graph(False))
    assert policy.resolved==old and policy.live_ports==[port]


@pytest.mark.parametrize("change",["moved","retired"])
def test_stale_closed_head_is_allowed_only_until_reopen(scene,change):
    board,port,Policy=scene;key=interface_identity(port)
    if change=="moved":board.tracks["head"].z=(2000000,0)
    else:board.tracks.pop("head")
    policy=Policy(board,[port],resolved={key})
    assert policy.live_ports==[]
    with pytest.raises(ValueError):policy.refresh(board.graph(False))
    assert policy.resolved=={key} and policy.live_ports==[]


def test_exact_native_capsule_cannot_be_replaced_by_a_guessed_width(scene):
    board,port,Policy=scene;policy=Policy(board,[port])
    assert policy.accepts_segment((1000000,0),(2000000,0),0,"control")
    exact=Shape(hits={"head","source"})
    assert not policy.accepts_segment((1000000,0),(2000000,0),0,"control",1,shape=exact)


def test_exact_shape_keeps_the_same_forward_heading_boundary(scene):
    board,port,Policy=scene;policy=Policy(board,[port])
    assert not policy.accepts_segment((1000000,0),(0,0),0,"control",shape=Shape())


def test_verified_advance_replaces_only_the_live_terminal(scene):
    board,port,Policy=scene;advanced=deepcopy(port)
    board.tracks["advanced"]=Track("advanced",(1000000,0),(2000000,0))
    advanced["added_ids"].append("advanced");advanced.update(terminal_id="advanced",exit_nm=[2000000,0],heading_origin_nm=[1000000,0])
    policy=Policy(board,[advanced])
    assert policy.accepts_segment((2000000,0),(3000000,0),0,"control",shape=Shape(hits={"advanced"}))
    assert not policy.accepts_segment((1000000,0),(2000000,0),0,"control",shape=Shape(hits={"advanced"}))
