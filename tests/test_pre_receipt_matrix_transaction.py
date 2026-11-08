"""Host transaction controls; native geometry is qualified separately."""
from copy import deepcopy
import sys
from types import SimpleNamespace
import pytest
from smilepcb.keyboard_routing import field_hygiene as h


class Track:
    def __init__(self,uid,shape):self.uid=uid;self.shape=deepcopy(shape)
    @property
    def m_Uuid(self):return SimpleNamespace(AsString=lambda:self.uid)
    def Duplicate(self):return Track(self.uid,self.shape)
    def SetUuid(self,uid):self.uid=uid


class Board:
    def __init__(self):
        def row(a,b,**kw):
            return {"class":"PCB_TRACK","start":[a,0],"end":[b,0],"width":150000,
                    "layer":0,"net":"matrix","locked":False,**kw}
        self.tracks={k:Track(k,v) for k,v in {
            "source":row(-1000000,0),"tail":row(0,2000000),"essential":row(0,3000000),
            "locked":row(5000000,6000000,locked=True),
            "via":row(3000000,3000000,**{"class":"PCB_VIA"}),
            "foreign":row(7000000,8000000,net="other")}.items()}
    def GetTracks(self):return list(self.tracks.values())
    def Delete(self,item):del self.tracks[item.uid]
    def Add(self,item):self.tracks[item.uid]=item
    def BuildConnectivity(self):pass
    def Zones(self):return []


def snap(board):
    return {"tracks":{u:deepcopy(t.shape) for u,t in board.tracks.items()},
            "footprints":{"physical":{"pads":[{"uuid":"a","number":"1"},{"uuid":"b","number":"1"}]}}}


@pytest.fixture
def native_boundary(monkeypatch):
    clock={"now":10.0};physics={"remove":"tail"}
    monkeypatch.setattr(h,"time",SimpleNamespace(monotonic=lambda:clock["now"]),raising=False)
    worker=SimpleNamespace(p=SimpleNamespace(KIID=lambda uid:uid),snapshot=snap)
    def partitions(board,deadline):
        return [{"a","b"}] if "essential" in board.tracks else [{"a"},{"b"}]
    def tails(board,*,protected,budget_s):
        victim=physics["remove"]
        if victim in board.tracks:board.Delete(board.tracks[victim])
        if physics.get("spend_work_budget"):clock["now"]+=budget_s
        if physics.get("expire"):clock["now"]=21
        return {"removed_ids":[victim],"trimmed_ids":[]}
    # Only slow/native boundary dependencies are replaced. The ownership,
    # duplicate selection, admission and rollback code under test stays real.
    monkeypatch.setitem(sys.modules,"smilepcb.native_worker",worker)
    monkeypatch.setitem(sys.modules,"smilepcb.keyboard_routing.completion",SimpleNamespace(pad_partitions=partitions))
    monkeypatch.setitem(sys.modules,"smilepcb.keyboard_routing.cleanup",SimpleNamespace(remove_tails=tails))
    return physics


def clean(board):
    return h.clean_matrix_tracks(board,owned_ids={"tail","essential","locked","via"},
            source_ids={"source"},nets={"matrix"},deadline=20)


def test_accepted_owned_tail_is_removed_before_sealing(native_boundary):
    board=Board();before=snap(board);result=clean(board)
    assert result["removed_ids"]==["tail"] and result["all_pad_partitions_exact"]
    assert snap(board)=={**before,"tracks":{u:v for u,v in before["tracks"].items() if u!="tail"}}


@pytest.mark.parametrize("victim",["essential","source","foreign","via","locked"])
def test_native_contact_or_immutable_loss_rolls_back_exactly(native_boundary,victim):
    board=Board();before=snap(board);native_boundary["remove"]=victim
    with pytest.raises(ValueError):clean(board)
    assert snap(board)==before


def test_deadline_after_real_mutation_rolls_back_exactly(native_boundary):
    board=Board();before=snap(board);native_boundary["expire"]=True
    with pytest.raises(TimeoutError):clean(board)
    assert snap(board)==before


def test_tail_work_cannot_spend_the_mandatory_native_proof_window(native_boundary):
    board=Board();native_boundary["spend_work_budget"]=True
    result=clean(board)
    assert result["all_pad_partitions_exact"] and "tail" not in board.tracks
