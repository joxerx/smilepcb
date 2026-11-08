"""Only proof-bound fresh access copper is provisional at matrix capture."""
import ast
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import sys,time
import pytest
from smilepcb.keyboard_phases import ordered_nets

ROOT=Path(__file__).resolve().parents[1]

def kernel():
    path=ROOT/"smilepcb/keyboard_routing/collector_preparation.py"
    nodes=[n for n in ast.parse(path.read_text()).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))]
    env={"__name__":__name__,"__package__":"smilepcb.keyboard_routing","dataclass":dataclass,"deepcopy":deepcopy,"Counter":Counter,"time":time,
        "ordered_nets":ordered_nets,"snapshot":lambda b:deepcopy(b.state),"p":SimpleNamespace(F_Cu=0,B_Cu=2,VIATYPE_THROUGH=4)}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),"exec"),env)
    return SimpleNamespace(**env)


def scene(monkeypatch,kind="key",*,reused=False,locked=False):
    def track(net):return {"class":"PCB_TRACK","net":net,"start":[0,0],"end":[1000000,0],"width":150000,"layer":0,"locked":False}
    pad={"uuid":"pad","number":"2" if kind=="key" else "7","net":"data" if kind=="key" else "control","position":[0,0],"size":[500000,500000],"layers":"F.Cu"}
    ref="LED1" if kind=="key" else "U_MCU";net=pad["net"]
    before={"tracks":{"usb":track("USB_DP"),"return":track("GND"),"donor":track("source")},"footprints":{"component":{"ref":ref,"pads":[pad]}}}
    via={**track(net),"class":"PCB_VIA","start":[1000000,0],"end":[1000000,0],"width":500000,"drill":200000,"via_type":4,"top":0,"bottom":2,"locked":locked}
    if reused:before["tracks"]["via"]=via
    state=deepcopy(before)
    if not reused:state["tracks"].update(via=via,lead=track(net))
    ids=[] if reused else ["lead","via"]
    row={"ref":ref,"pin":pad["number"],"net":net,"via_id":"via","ids":ids,"connected":True,"reused":reused} if kind=="key" else {
        "pad_ids":["pad"],"net":net,"added_ids":ids,"terminal_id":"lead","kind":"via","via_id":"via","via_nm":[1000000,0],"exit_nm":[1000000,0],"heading_origin_nm":[0,0],"exit_layer":0}
    proof={"accepted":True,"ports":[row],"added_ids":ids}
    if kind=="key":proof.update(required=1,connections=1)
    cfg={"layout":{"rgb":kind=="key","keys":[{"id":1}],"gpio":{"rows":[],"cols":[]}},"parts":[{"ref":ref,"region":"matrix" if kind=="key" else "controller","nets":{pad["number"]:net}}],"plane_nets":[],"mcu_gpio_pads":{}}
    board=SimpleNamespace(state=state)
    def graph(*args,**kwargs):
        nodes=[{"item":SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u:u)),"net":net} for u in ["pad",*state["tracks"]]]
        return {"nodes":nodes,"find":lambda i:0}
    monkeypatch.setitem(sys.modules,"smilepcb.keyboard_routing.native_graph",SimpleNamespace(copper_graph=graph))
    return board,cfg,before,proof


def verified(api,scene):
    return api.verified_provisional_ownership(*scene,deadline=time.monotonic()+1)


@pytest.mark.parametrize("kind",["key","fixed"])
def test_only_fresh_access_ids_leave_the_permanent_prerequisite_inventory(monkeypatch,kind):
    api=kernel();s=scene(monkeypatch,kind);owned=verified(api,s)
    assert set(owned.tracks)=={"via","lead"}
    assert api.transient_access_ids(s[0].state,owned,{"row","column"})=={"via","lead"}
    assert {"usb","return","donor"}.isdisjoint(owned.tracks)


def test_reused_source_via_and_new_locked_access_are_never_released(monkeypatch):
    api=kernel();s=scene(monkeypatch,reused=True);assert verified(api,s).tracks=={}
    s=scene(monkeypatch,locked=True);assert set(verified(api,s).tracks)=={"lead"}


@pytest.mark.parametrize("change",["old-id","wrong-pin","wrong-net","wrong-via","wrong-size","changed-original"])
def test_unproven_access_proof_cannot_authorize_transient_source(monkeypatch,change):
    api=kernel();s=scene(monkeypatch);board,cfg,before,proof=s
    if change=="old-id":proof["added_ids"].append("usb");proof["ports"][0]["ids"].append("usb")
    elif change=="wrong-pin":proof["ports"][0]["pin"]="8"
    elif change=="wrong-net":proof["ports"][0]["net"]="USB_DP"
    elif change=="wrong-via":proof["ports"][0]["via_id"]="donor"
    elif change=="wrong-size":board.state["tracks"]["via"]["drill"]=600000
    else:board.state["tracks"]["usb"]["width"]=100000
    with pytest.raises(ValueError):verified(api,s)


def test_untyped_or_stale_ownership_cannot_release_permanent_source(monkeypatch):
    api=kernel();s=scene(monkeypatch);owned=verified(api,s)
    with pytest.raises(ValueError):api.transient_access_ids(s[0].state,["usb"],{"row"})
    s[0].state["tracks"]["via"]["width"]=600000
    with pytest.raises(ValueError):api.transient_access_ids(s[0].state,owned,{"row"})


def test_bound_pad_mask_dimensions_cannot_drift_before_capture(monkeypatch):
    api=kernel();s=scene(monkeypatch);owned=verified(api,s)
    s[0].state["footprints"]["component"]["pads"][0]["size"]=[1000000,1000000]
    with pytest.raises(ValueError):api.transient_access_ids(s[0].state,owned,{"row"})


@pytest.mark.parametrize("omission",["function","physical-alias"])
def test_fixed_proof_must_cover_every_actual_nonmatrix_mcu_alias(monkeypatch,omission):
    api=kernel();s=scene(monkeypatch,"fixed");board,cfg,before,proof=s
    if omission=="function":
        proof["ports"]=[];proof["added_ids"]=[]
        for uid in ("lead","via"):board.state["tracks"].pop(uid)
    else:
        alias=deepcopy(before["footprints"]["component"]["pads"][0]);alias["uuid"]="physical-alias"
        before["footprints"]["component"]["pads"].append(alias)
        board.state["footprints"]["component"]["pads"].append(deepcopy(alias))
    with pytest.raises(ValueError):verified(api,s)


def test_zero_required_fixed_batch_keeps_empty_native_ownership_valid(monkeypatch):
    api=kernel();s=scene(monkeypatch,"fixed");board,cfg,before,proof=s
    proof["ports"]=[];proof["added_ids"]=[]
    for uid in ("lead","via"):board.state["tracks"].pop(uid)
    for state in (before,board.state):state["footprints"]["component"]["pads"][0]["net"]="NC_UNUSED"
    cfg["parts"][0]["nets"]["7"]="NC_UNUSED"
    assert verified(api,s).tracks=={}
