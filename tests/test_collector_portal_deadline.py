"""Host deadline controls; native geometry is exercised in the paired probe."""
import ast
from collections import Counter
import heapq,math
from pathlib import Path
from types import SimpleNamespace
import pytest


def scene(expire_at=None):
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/collector_capacity.py'
    tree=ast.parse(path.read_text());clock=SimpleNamespace(now=0);events=[]
    def event(kind):
        events.append(kind)
        if kind==expire_at:clock.now=10
        return True
    native=SimpleNamespace(F_Cu=0,B_Cu=2,SHAPE_SEGMENT=lambda *a:a,SHAPE_CIRCLE=lambda *a:a,
                           SHAPE=SimpleNamespace(Collide=lambda *a:not event('pair')))
    ns={'heapq':heapq,'math':math,'time':SimpleNamespace(monotonic=lambda:clock.now),'p':native,'vec':lambda x:x,
        'band':lambda *a:a,'shifted':lambda points,offset:[(x,y+offset) for x,y in points],
        'DIRS':((1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1))}
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Search')
    exec(compile(ast.Module(body=[cls],type_ignores=[]),str(path),'exec'),ns)
    search=ns['Search'].__new__(ns['Search']);search.deadline=10;search.portal_cache={};search.allow_vias=True
    search.nets=['lower','upper'];search.offsets={'lower':-300000,'upper':300000};search.origin=(10000000,10300000)
    search.step=1000000;search.width=150000;search.gap=173000;search.blockers=Counter()
    search.stats={'portal_proposals':0,'portal_legal':0}
    search.world=SimpleNamespace(via_width=500000,drill=200000,generation=SimpleNamespace(via_to_hole_clearance_nm=401000),net_clearances={})
    search.detail=SimpleNamespace(deadline=10,via_clear=lambda *a:event('via'),via_pair_clear=lambda *a:event('via_pair'),clear=lambda *a:event('track'))
    search.shape_clear=lambda *a:event('coarse') and False
    return search,clock,events,(2,0,0,0,3)


def test_unused_envelope_failure_reaches_actual_shapes():
    search,clock,events,state=scene()
    assert search.portal(state) is not None
    assert events.count('via')==2 and events.count('track')==4


@pytest.mark.parametrize('cached',[False,True])
def test_expired_portal_does_no_work_or_returns_no_cached_success(cached):
    search,clock,events,state=scene();clock.now=10
    if cached:search.portal_cache[state[:4]]={'accepted':'earlier'}
    assert search.portal(state) is None
    assert events==[] and search.stats['portal_proposals']==0


@pytest.mark.parametrize('stage',['coarse','via','via_pair','track','pair'])
def test_detail_expiry_does_not_publish_a_legal_portal(stage):
    search,clock,events,state=scene(stage)
    assert search.portal(state) is None
    assert search.stats['portal_legal']==0
    assert events.count(stage)==1
    assert state[:4] not in search.portal_cache or search.portal_cache[state[:4]] is None


@pytest.mark.parametrize('window',[4.5,9.])
def test_solve_does_not_start_portal_after_edge_crosses_planning_window(window):
    search,clock,events,state=scene()
    search.h=(1,0);search.face=0;search.heading_index=0;search.reach=10000000;search.toward=(1,0)
    search.run_cap=3;search.radius=375000;search.reserved_width=1096000
    search.best_partial=None;search.best_partial_score=-math.inf
    search.stats.update(expanded=0,generated=0,native_goal_rejections=0)
    search.heuristic=lambda state:0;search.goal_ready=lambda state:False;search.select_goal=lambda index:None
    turns=[]
    def next_turn(**kwargs):
        if turns:return None
        turns.append(kwargs);return 0,window,18000
    agenda=SimpleNamespace(deadline=10,count=2,next_turn=next_turn)
    def edge(*args):clock.now=window;return False
    search.edge_clear=edge
    result=search.solve(agenda)
    assert not result['accepted'] and events==[]
    assert search.deadline==search.detail.deadline==10
    assert search.stats['portal_proposals']==0


def test_timeout_before_second_envelope_face_is_not_a_collision():
    search,clock,events,state=scene()
    def clear(*args):events.append('clear_face');clock.now=10;return True
    search.shape_clear=clear
    assert search.portal(state) is None
    assert events==['clear_face'] and search.stats.get('portal_envelope_blocked',0)==0


@pytest.mark.parametrize('outcome',['success','expired','exception'])
def test_local_portal_window_bounds_detail_and_restores_original_deadline(outcome):
    search,clock,events,state=scene();seen=[]
    def via(*args):
        seen.append(search.detail.deadline)
        if outcome=='exception':raise RuntimeError('native check failed')
        if outcome=='expired':clock.now=4;return False
        return True
    search.detail.via_clear=via
    if outcome=='exception':
        with pytest.raises(RuntimeError,match='native check failed'):search.portal(state,deadline=4)
    else:assert (search.portal(state,deadline=4) is not None)==(outcome=='success')
    assert seen and set(seen)=={4}
    assert search.deadline==search.detail.deadline==10
    if outcome=='expired':
        assert state[:4] not in search.portal_cache
        search.detail.via_clear=lambda *args:True
        assert search.portal(state,deadline=8) is not None


def test_expired_local_window_does_not_reuse_a_cached_portal():
    search,clock,events,state=scene();search.portal_cache[state[:4]]={'legal':'earlier'};clock.now=4
    assert search.portal(state,deadline=4) is None
    assert events==[] and search.stats['portal_proposals']==0


@pytest.mark.parametrize('expires_in',['edge','detail'])
def test_next_agenda_turn_retries_interrupted_state_whose_only_progress_is_portal(expires_in):
    search,clock,events,state=scene()
    search.h=(1,0);search.face=0;search.heading_index=0;search.reach=10000000;search.toward=(1,0)
    search.run_cap=3;search.radius=375000;search.reserved_width=1096000
    search.best_partial=None;search.best_partial_score=-math.inf
    search.stats.update(expanded=0,generated=0,native_goal_rejections=0)
    search.heuristic=lambda state:0;search.goal_ready=lambda state:state[3]==2
    selected=[];commits=[];via_deadlines=[]
    search.select_goal=selected.append
    turns=iter([(0,4.5,9000),(1,9.,18000)])
    agenda=SimpleNamespace(deadline=10,count=2,next_turn=lambda **kwargs:next(turns,None))
    def edge(*args):
        if expires_in=='edge':clock.now=4.5
        return False
    def via(*args):
        via_deadlines.append(search.detail.deadline)
        if expires_in=='detail' and len(via_deadlines)==1:clock.now=4.5;return False
        return True
    def commit(edges):
        commits.append((search.deadline,[edge[2] for edge in edges]))
        return {'lower':{},'upper':{}},[],None
    search.edge_clear=edge;search.detail.via_clear=via;search.commit=commit
    result=search.solve(agenda)
    assert result['accepted'] and selected==[0,1],result
    assert commits==[(10,['portal'])]
    assert via_deadlines==([9.,9.] if expires_in=='edge' else [4.5,9.,9.])
    assert search.stats['portal_legal']==1 and search.stats['generated']==1
    assert search.deadline==search.detail.deadline==10
