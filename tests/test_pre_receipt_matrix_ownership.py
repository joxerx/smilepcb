"""Explicit generated ownership is the only authority to prune before sealing."""
import pytest
from smilepcb.keyboard_routing import field_hygiene as h


def track(**kw):
    return {"class":"PCB_TRACK","start":[0,0],"end":[1000000,0],
            "width":150000,"layer":0,"net":"matrix","locked":False,**kw}


def test_only_explicit_generated_straight_tracks_can_be_cleaned():
    tracks={"source":track(),"tail":track(),"duplicate":track(),"locked":track(locked=True),
            "via":track(**{"class":"PCB_VIA"}),"arc":track(**{"class":"PCB_ARC"}),
            "unowned":track(),"foreign":track(net="fixed")}
    assert h.editable_matrix_tracks(tracks,owned_ids={"tail","duplicate","locked","via","arc"},
            source_ids={"source"},nets={"matrix"})=={"tail","duplicate"}


@pytest.mark.parametrize("owned,source,nets",[
    ({"missing"},{"source"},{"matrix"}),
    ({"tail"},{"missing"},{"matrix"}),
    ({"source","tail"},{"source"},{"matrix"}),
    ({"tail","foreign"},{"source"},{"matrix"}),
])
def test_unproven_ownership_cannot_release_source_or_other_nets(owned,source,nets):
    tracks={"source":track(),"tail":track(),"foreign":track(net="fixed")}
    with pytest.raises(ValueError):
        h.editable_matrix_tracks(tracks,owned_ids=owned,source_ids=source,nets=nets)


def test_unowned_covered_source_is_never_promoted_to_generated_ownership():
    tracks={"source":track(),"tail":track(start=[200000,0],end=[800000,0])}
    owned=h.editable_matrix_tracks(tracks,owned_ids={"tail"},source_ids={"source"},nets={"matrix"})
    assert h.covered_ids(tracks,owned)==["tail"]
    assert tracks["source"]["start"]==[0,0]
    assert tracks["source"]["end"]==[1000000,0]
