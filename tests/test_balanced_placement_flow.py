"""The normal placement stage must consume the proven balanced search."""
from copy import deepcopy
from types import SimpleNamespace as NS
import sys,time
import pytest
from smilepcb.stm32 import core_pose_native as native

class Oracle:
    bounds=[0,0,1_000_000,1_000_000]
    extraction={'core_features':1,'fixed_features':1}
    core=[{'box':[-10000,-10000,10000,10000]}]
    depth=10000
    def __call__(self,pose,*,deadline=None):
        x,y=pose['origin_nm'];space={'left':x+1,'right':1_000_001-x,'top':y+1,'bottom':1_000_001-y}
        return dict(legal=True,side_space_nm=space,bank_headroom_nm=min(space.values())-100_000,adverse_departures=0)


def scene(monkeypatch):
    baseline={'origin_nm':[0,0],'rotation_deg':0};board=NS(pose=deepcopy(baseline))
    cfg={'layout':{'keys':[{'center_mm':[0,0]},{'center_mm':[4,0]}]}}
    board.GetDesignSettings=lambda:NS(m_TrackMinWidth=150_000,m_MinClearance=150_000,m_ViasMinSize=500_000)
    monkeypatch.setattr(native,'_NativePoseOracle',lambda *a,**k:Oracle())
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(snapshot=lambda b:deepcopy(b.pose)))
    moves=[]
    def move(objects,old,new):
        assert board.pose==old
        board.pose=deepcopy(new);moves.append((deepcopy(old),deepcopy(new)))
    monkeypatch.setattr(native,'_move_packed_core',move,raising=False)
    return board,cfg,baseline,moves


def test_balanced_stage_replaces_first_legal_pose_using_all_four_side_space(monkeypatch):
    board,cfg,baseline,moves=scene(monkeypatch);original=deepcopy(cfg)
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,deadline=time.monotonic()+5,legal=lambda items:True)
    assert pose==board.pose and pose['origin_nm']!=[0,0]
    assert min(report['selected_metrics']['side_space_nm'].values())>min(report['baseline_metrics']['side_space_nm'].values())
    assert report['native_legal'] is True and report['stats']['evaluations']<=8192
    assert cfg==original and baseline=={'origin_nm':[0,0],'rotation_deg':0}


def test_native_rejection_restores_original_before_trying_another_pose(monkeypatch):
    board,cfg,baseline,moves=scene(monkeypatch);calls=[]
    def legal(items):calls.append(deepcopy(board.pose));return len(calls)>1
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,deadline=time.monotonic()+5,legal=legal)
    assert report['native_rejected']==1 and pose==board.pose
    assert moves[1][1]==baseline


def test_excluded_geometries_are_not_retried_and_current_context_is_retained(monkeypatch):
    board,cfg,baseline,moves=scene(monkeypatch)
    first,_=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,deadline=time.monotonic()+5,legal=lambda items:True)
    board.pose=deepcopy(baseline);moves.clear()
    excluded=[(*first['origin_nm'],first['rotation_deg'])]
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,deadline=time.monotonic()+5,legal=lambda items:True,excluded=excluded)
    assert (*pose['origin_nm'],pose['rotation_deg']) not in excluded


def test_search_leaves_time_for_native_admission_within_same_deadline(monkeypatch):
    from smilepcb.stm32 import core_pose_geometry
    board,cfg,baseline,moves=scene(monkeypatch);clock=[0.0];seen=[]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    def search(bounds,base,**kw):
        seen.append(kw['deadline']);clock[0]=kw['deadline']+.001
        point={'origin_nm':[500_000,500_000],'rotation_deg':0}
        return {'poses':[{**base,'diagnostics':Oracle()(base)},{**point,'diagnostics':Oracle()(point)}],'samples':[],'stats':{'evaluations':1}}
    monkeypatch.setattr(core_pose_geometry,'balanced_pose_search',search)
    checks=[]
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,deadline=10,legal=lambda items:checks.append(clock[0]) or True)
    assert seen[0]<10 and checks and checks[0]<10 and report['native_legal']


def test_late_native_admission_cannot_accept_and_restores_entry(monkeypatch):
    board,cfg,baseline,moves=scene(monkeypatch);clock=[0.0]
    monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    def late(items):clock[0]=11;return True
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,deadline=10,legal=late)
    assert pose is None and not report['native_legal'] and board.pose==baseline


def test_explicit_cardinal_rotation_is_retained(monkeypatch):
    board,cfg,baseline,moves=scene(monkeypatch)
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,deadline=time.monotonic()+5,legal=lambda items:True,allowed_rotations=(90,))
    assert pose['rotation_deg']==90


def test_retries_consider_already_measured_other_rotations_before_same_angle_neighbours(monkeypatch):
    from smilepcb.stm32 import core_pose_geometry
    board,cfg,baseline,moves=scene(monkeypatch)
    first={'origin_nm':[500_000,500_000],'rotation_deg':0}
    neighbour={'origin_nm':[510_000,500_000],'rotation_deg':0}
    alternative={'origin_nm':[550_000,500_000],'rotation_deg':90}
    def search(*a,**k):
        return {'poses':[{**baseline,'diagnostics':Oracle()(baseline)},{**first,'diagnostics':Oracle()(first)},{**neighbour,'diagnostics':Oracle()(neighbour)}],
            'samples':[{'pose':alternative,'legal':True,**Oracle()(alternative)}],'stats':{'evaluations':4}}
    monkeypatch.setattr(core_pose_geometry,'balanced_pose_search',search)
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,
        deadline=time.monotonic()+5,legal=lambda items:True,excluded=[(*first['origin_nm'],0)])
    assert pose==alternative and board.pose==alternative
    assert report['native_legal']


def basin_scene(monkeypatch):
    from smilepcb.stm32 import core_pose_geometry
    board,cfg,baseline,moves=scene(monkeypatch)
    oracle=Oracle();oracle.core=[{'box':[-10000,-10000,10000,10000]}];oracle.depth=10000
    monkeypatch.setattr(native,'_NativePoseOracle',lambda *a,**k:oracle)
    failed={'origin_nm':[500000,500000],'rotation_deg':0}
    near={'origin_nm':[510000,500000],'rotation_deg':90}
    far={'origin_nm':[300000,500000],'rotation_deg':90}
    samples=[{'pose':q,**oracle(q)} for q in (near,far)]
    def search(*a,**k):
        return {'poses':[{**near,'diagnostics':oracle(near)}], 'samples':deepcopy(samples),'stats':{'evaluations':2}}
    monkeypatch.setattr(core_pose_geometry,'balanced_pose_search',search)
    return board,cfg,baseline,failed,near,far,samples


def test_retry_adds_a_different_spatial_basin_from_all_measured_samples(monkeypatch):
    board,cfg,baseline,failed,near,far,samples=basin_scene(monkeypatch)
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,
        deadline=time.monotonic()+5,legal=lambda items:True,allowed_rotations=(90,),excluded=[(*failed['origin_nm'],0)])
    assert pose==far and report['failed_neighborhoods_nm']


def test_diverse_native_rejection_keeps_the_close_fallback_and_exact_rollback(monkeypatch):
    board,cfg,baseline,failed,near,far,samples=basin_scene(monkeypatch);seen=[]
    def legal(items):seen.append(deepcopy(board.pose));return board.pose==near
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,
        deadline=time.monotonic()+5,legal=legal,allowed_rotations=(90,),excluded=[(*failed['origin_nm'],0)])
    assert seen==[far,near] and pose==near and report['native_rejected']==1


def test_spatial_retry_order_is_independent_of_sample_iteration_order(monkeypatch):
    board,cfg,baseline,failed,near,far,samples=basin_scene(monkeypatch)
    def run():
        board.pose=deepcopy(baseline)
        return native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,
            deadline=time.monotonic()+5,legal=lambda items:True,allowed_rotations=(90,),excluded=[(*failed['origin_nm'],0)])[0]
    first=run();samples.reverse();assert first==run()==far


def test_when_every_escape_region_overlaps_the_old_order_is_still_usable(monkeypatch):
    board,cfg,baseline,failed,near,far,samples=basin_scene(monkeypatch)
    oracle=native._NativePoseOracle(None,None,None,None)
    oracle.core=[{'box':[-1000000,-1000000,1000000,1000000]}]
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,
        deadline=time.monotonic()+5,legal=lambda items:True,allowed_rotations=(90,),excluded=[(*failed['origin_nm'],0)])
    assert pose==near and all(report['candidate_overlaps_failed_neighborhood'])


def test_without_failed_regions_the_existing_first_choice_does_not_change(monkeypatch):
    board,cfg,baseline,failed,near,far,samples=basin_scene(monkeypatch)
    pose,report=native.balance_packed_core(board,cfg,[NS(m_Uuid=NS(AsString=lambda:'core'))],baseline,
        deadline=time.monotonic()+5,legal=lambda items:True,allowed_rotations=(90,))
    assert pose==near and report['failed_neighborhoods_nm']==[]
