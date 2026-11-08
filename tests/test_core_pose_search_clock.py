from types import SimpleNamespace as S
from smilepcb.stm32 import core_pose_geometry as geometry


def test_slow_coarse_pass_retains_globally_spread_prefix_and_refinement_time(monkeypatch):
    clock=S(now=0.)
    monkeypatch.setattr(geometry,'time',S(monotonic=lambda:clock.now))
    def oracle(pose,*,deadline):
        clock.now+=1.
        return dict(legal=True,side_space_nm=dict(left=10,right=10,top=10,bottom=10),bank_headroom_nm=10,adverse_departures=0)
    result=geometry.balanced_pose_search((0,0,100,100),dict(origin_nm=[50,50],rotation_deg=0),
        coarse_step_nm=10,min_step_nm=2,oracle=oracle,deadline=40.,max_evaluations=500)
    coarse=[q['pose']['origin_nm'] for q in result['samples'] if q['phase']=='coarse']
    assert any(q['phase']=='refine' for q in result['samples'])
    assert min(q[1] for q in coarse)==0 and max(q[1] for q in coarse)==100
    assert not result['stats']['coverage_complete']
    assert result['stats']['coarse_stop_reason']=='time_reserve'
    assert result['stats']['evaluations']<=500 and clock.now<=40.


def test_progressive_coarse_order_still_measures_the_same_complete_lattice(monkeypatch):
    monkeypatch.setattr(geometry,'time',S(monotonic=lambda:0.))
    def oracle(pose,*,deadline):
        return dict(legal=True,side_space_nm=dict(left=10,right=10,top=10,bottom=10),bank_headroom_nm=10,adverse_departures=0)
    result=geometry.balanced_pose_search((0,0,20,20),dict(origin_nm=[0,0],rotation_deg=0),
        coarse_step_nm=10,min_step_nm=10,oracle=oracle,deadline=40.,max_evaluations=500)
    points={(*q['pose']['origin_nm'],q['pose']['rotation_deg']) for q in result['samples']}
    assert points=={(x,y,a) for x in (0,10,20) for y in (0,10,20) for a in (0,90,180,270)}
    assert result['stats']['coverage_complete']


def test_interrupted_large_lattice_prefix_covers_all_xy_quadrants(monkeypatch):
    clock=S(now=0.);seen=[]
    monkeypatch.setattr(geometry,'time',S(monotonic=lambda:clock.now))
    def oracle(pose,*,deadline):
        clock.now+=1.;seen.append(tuple(pose['origin_nm']))
        return dict(legal=True,side_space_nm=dict(left=10,right=10,top=10,bottom=10),bank_headroom_nm=10,adverse_departures=0)
    geometry.balanced_pose_search((0,0,63,63),dict(origin_nm=[0,0],rotation_deg=0),
        coarse_step_nm=1,min_step_nm=1,oracle=oracle,deadline=260.,max_evaluations=8192)
    sites=list(dict.fromkeys(seen))[:64]
    for right,bottom in ((False,False),(False,True),(True,False),(True,True)):
        assert sum((x>=32)==right and (y>=32)==bottom for x,y in sites)>=4


def test_no_refinement_does_not_waste_half_the_available_clock(monkeypatch):
    clock=S(now=0.)
    monkeypatch.setattr(geometry,'time',S(monotonic=lambda:clock.now))
    def oracle(pose,*,deadline):
        clock.now+=1.
        return dict(legal=True,side_space_nm=dict(left=10,right=10,top=10,bottom=10),bank_headroom_nm=10,adverse_departures=0)
    result=geometry.balanced_pose_search((0,0,20,20),dict(origin_nm=[0,0],rotation_deg=0),
        coarse_step_nm=10,min_step_nm=10,oracle=oracle,deadline=40.,max_evaluations=500)
    assert result['stats']['coverage_complete'] and clock.now==36.


def test_phase_timeout_can_be_remeasured_with_refinement_budget(monkeypatch):
    clock=S(now=0.);calls=[]
    monkeypatch.setattr(geometry,'time',S(monotonic=lambda:clock.now))
    def oracle(pose,*,deadline):
        key=(*pose['origin_nm'],pose['rotation_deg']);calls.append(key)
        if key==(0,0,90) and deadline<60.:
            clock.now=deadline;raise TimeoutError('coarse phase ended')
        clock.now+=1.
        return dict(legal=True,side_space_nm=dict(left=10,right=10,top=10,bottom=10),bank_headroom_nm=10,adverse_departures=0)
    result=geometry.balanced_pose_search((0,0,10,10),dict(origin_nm=[5,5],rotation_deg=0),
        coarse_step_nm=10,min_step_nm=5,oracle=oracle,deadline=60.,max_evaluations=500)
    assert calls.count((0,0,90))==2
    assert any(q['phase']=='refine' and q['pose']['origin_nm']==[0,0] and q['pose']['rotation_deg']==90 and q['legal'] for q in result['samples'])
