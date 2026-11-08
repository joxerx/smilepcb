"""Host signal-pitch context contracts; physical admission remains Native."""
from copy import deepcopy
from types import SimpleNamespace as NS
import sys
import time

import pytest

from smilepcb.keyboard_routing import controller_corridor_plan as cp


@pytest.fixture
def context_scene(monkeypatch):
    oracle=NS(minimum_clearance=173000,
        net_clearances={'':251000,'signal-a':173000,'signal-b':173000,'unrelated':400000},
        via_width=500000,drill=200000,native_hole_clearance=250000,edge_clearance=251000,
        generation=NS(foreign_copper_clearance_nm=173000,
                      via_to_smd_clearance_nm=173000,via_to_hole_clearance_nm=251000))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(
        p=NS(FromMM=lambda value:round(value*1000000)),snapshot=lambda board:{'tracks':{}}))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.conditional_banks',NS(_source_physics=lambda *a:{'unchanged':True}))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',NS(LocalRouter=lambda *a,**kw:oracle))
    monkeypatch.setattr(cp,'_actual_targets',lambda *a:({},{}))
    monkeypatch.setattr(cp,'_departure_context',lambda *a:[])
    box=NS(GetLeft=lambda:0,GetTop=lambda:0,GetRight=lambda:10000000,GetBottom=lambda:10000000)
    board=NS(GetDesignSettings=lambda:NS(m_TrackMinWidth=150000),GetFootprints=lambda:[],
             GetBoardEdgesBoundingBox=lambda:box)
    ports=[dict(net=net,pad_ids=['source-'+net],added_ids=['head-'+net]) for net in ('signal-a','signal-b')]
    backend=cp._NativeBackend()
    return oracle,board,ports,backend


@pytest.mark.parametrize('participant_clearance,expected_gap,expected_radius',[
    (173000,173000,501000),
    (300000,300000,550000),
])
def test_only_participant_clearances_set_signal_pitch_with_physical_hole_guards_preserved(
        context_scene,participant_clearance,expected_gap,expected_radius):
    oracle,board,ports,backend=context_scene
    oracle.net_clearances['signal-a']=participant_clearance
    original=deepcopy(vars(oracle))
    rules=backend.context(board,{},ports)['rules']
    assert rules['clearance_nm']==expected_gap
    assert cp._via_radius(rules)==expected_radius
    assert rules['hole_clearance_nm']==250000 and rules['edge_clearance_nm']==251000
    assert rules['generation']['via_to_hole_clearance_nm']==251000
    assert vars(oracle)==original


def test_participant_clearance_changes_invalidate_the_sealed_context(context_scene,monkeypatch):
    oracle,board,ports,backend=context_scene
    cfg={};context=backend.context(board,cfg,ports)
    binding=cp._binding(context,cfg,ports);receipt={'valid_until':time.monotonic()+10};envelopes=[]
    seal=cp._digest(dict(binding=binding,receipt=receipt,envelopes=envelopes))
    plan=cp.ControllerCorridorPlan(binding,receipt,envelopes,seal,cp._ISSUER)
    monkeypatch.setattr(cp,'_backend',lambda:backend)
    plan.verify(board,cfg,deadline=time.monotonic()+5)
    oracle.net_clearances['unrelated']=500000
    oracle.net_clearances['']=600000
    plan.verify(board,cfg,deadline=time.monotonic()+5)
    oracle.net_clearances['signal-b']=300000
    with pytest.raises(ValueError,match='binding changed'):
        plan.verify(board,cfg,deadline=time.monotonic()+5)
