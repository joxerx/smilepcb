"""Pure policy and host emission contracts; real capsules have a Native probe."""
import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def policy():
    return importlib.import_module('smilepcb.keyboard_routing.corridor_reservations')


def record(**changes):
    row=dict(id='lane-a',layer=0,nets=['owner','co-owner'],path=[(10,0),(10,10),(20,10)],width_nm=2)
    row.update(changes)
    return row


def test_context_copies_records_combines_children_and_restores_on_exception():
    q=policy();row=record()
    assert q.current_corridors()==()
    with q.reserve_corridors([row]):
        outer=q.current_corridors()
        row['nets'].append('intruder');row['path'][0]=(100,100);row['width_nm']=200
        assert outer[0].nets==frozenset({'owner','co-owner'})
        assert outer[0].path==((10,0),(10,10),(20,10))
        assert outer[0].width_nm==2
        with pytest.raises(RuntimeError):
            with q.reserve_corridors([record(id='child',layer=2)]):
                assert [r.id for r in q.current_corridors()]==['lane-a','child']
                raise RuntimeError('abort')
        with q.reserve_corridors([]):assert q.current_corridors()==outer
        assert q.current_corridors()==outer
    assert q.current_corridors()==()


@pytest.mark.parametrize('change',[
    dict(id=''),dict(layer=1),dict(layer=True),dict(width_nm=0),dict(width_nm=-1),
    dict(width_nm=1.5),dict(width_nm=True),dict(nets=[]),dict(nets=['a','a']),
    dict(nets=['']),dict(nets='owner'),dict(path=[]),dict(path=[(0,0)]),
    dict(path=[(0,0),(2,1)]),dict(path=[(0,0),(1.5,0)]),
    dict(path=[(0,0),(True,0)]),dict(path=[(0,0),(0,0)]),
])
def test_invalid_records_fail_without_binding(change):
    q=policy()
    with pytest.raises((ValueError,TypeError)):
        with q.reserve_corridors([record(**change)]):pass
    assert q.current_corridors()==()


class Segment:
    def __init__(self,a,b,width):self.a=a;self.b=b;self.width=width
    def BBox(self):return SimpleNamespace(GetLeft=lambda:0,GetRight=lambda:40,GetTop=lambda:0,GetBottom=lambda:40)
    def Type(self):return 1


class Circle(Segment):
    def __init__(self,at,radius):super().__init__(at,at,radius*2)


def collide(a,b,gap):
    # Fixtures use perpendicular/parallel axis aligned capsules, so interval
    # overlap is sufficient here. Real native geometry is separately gated.
    return all(max(min(a.a[i],a.b[i])-a.width/2,min(b.a[i],b.b[i])-b.width/2)
               <=min(max(a.a[i],a.b[i])+a.width/2,max(b.a[i],b.b[i])+b.width/2) for i in (0,1))


@pytest.fixture
def host(monkeypatch):
    import sys
    native=SimpleNamespace(VECTOR2I=lambda x,y:(x,y),SHAPE_SEGMENT=Segment,SHAPE_CIRCLE=Circle,
        SHAPE=SimpleNamespace(Collide=collide),F_Cu=0,B_Cu=2,SH_SEGMENT=1,SH_CIRCLE=2)
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',SimpleNamespace(p=native))
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/local_router.py'
    tree=ast.parse(path.read_text())
    # Native imports are unavailable in standard Python. Execute the actual
    # router class while substituting only the external shape/board boundary.
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LocalRouter')
    ns=dict(p=native,vec=lambda v:v,xy=lambda v:v)
    # Preserve new pure-policy imports used by the real class.
    for n in tree.body:
        if isinstance(n,ast.ImportFrom) and n.module=='corridor_reservations':
            for name in n.names:ns[name.asname or name.name]=getattr(policy(),name.name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    router=ns['LocalRouter'].__new__(ns['LocalRouter'])
    router.launch_policy=None;router._port_policy=None;router.path_policy=None
    router._refresh_source_net=lambda net:None
    router.track_width=2;router.via_width=4;router.drill=2
    router.board=SimpleNamespace(Add=lambda item:pytest.fail('emitted copper before rejection'))
    router.added=[];router.reservations=[];router.net_clearances={};router.holes={}
    router.generation=SimpleNamespace(foreign_copper_clearance_nm=0)
    router.outline=SimpleNamespace(Contains=lambda v:True)
    router.cells=lambda *a:();router.edge_buckets={};router.buckets={0:{},2:{}}
    return router


def test_owner_foreign_faces_and_stable_ids(host):
    q=policy();cross=Segment((0,5),(20,5),2)
    assert list(q.collisions(cross,0,'foreign'))==[]
    assert list(host.collisions(cross,0,'foreign'))==[]
    with q.reserve_corridors([record()]):
        assert list(host.collisions(cross,0,'owner'))==[]
        assert list(host.collisions(cross,0,'co-owner'))==[]
        assert list(host.collisions(cross,2,'foreign'))==[]
        assert list(host.collisions(cross,0,'foreign'))==['lane-a']
        # One ID even where multiple edges of the same reservation collide.
        assert list(q.collisions(Segment((10,10),(10,10),2),0,'foreign'))==['lane-a']


def test_later_segment_collision_leaves_no_prefix(host):
    with policy().reserve_corridors([record()]):
        with pytest.raises(ValueError,match='corridor'):
            host.add_path([(0,-5),(0,5),(20,5)],0,'foreign')
    assert host.added==[]


@pytest.mark.parametrize('face',[0,2])
def test_actual_via_checks_both_faces_before_emission(host,face):
    with policy().reserve_corridors([record(layer=face)]):
        with pytest.raises(ValueError,match='corridor'):
            host.add_via((10,5),'foreign')
    assert host.added==[]


@pytest.fixture
def virtual(host):
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/virtual_copper.py'
    node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef))
    ns=dict(LocalRouter=type(host),p=SimpleNamespace(PCB_TRACK=lambda *a:pytest.fail('ghost leaked'),
        PCB_VIA=lambda *a:pytest.fail('ghost via leaked')),vec=lambda v:v)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    ghost=ns['VirtualCopper'].__new__(ns['VirtualCopper']);ghost.__dict__.update(host.__dict__)
    ghost.ghosts=[];ghost.operations=[]
    return ghost


def test_virtual_path_checks_all_segments_before_any_ghost(virtual):
    with policy().reserve_corridors([record()]):
        with pytest.raises(ValueError,match='corridor'):
            virtual.add_path([(0,-5),(0,5),(20,5)],0,'foreign')
    assert virtual.ghosts==virtual.operations==virtual.added==[]


@pytest.mark.parametrize('face',[0,2])
def test_virtual_via_checks_both_faces_before_any_ghost(virtual,face):
    with policy().reserve_corridors([record(layer=face)]):
        with pytest.raises(ValueError,match='corridor'):
            virtual.add_via((10,5),'foreign')
    assert virtual.ghosts==virtual.operations==virtual.added==[]


def test_empty_and_owner_only_policy_never_import_native(monkeypatch):
    import sys
    q=policy()
    monkeypatch.delitem(sys.modules,'smilepcb.native_worker',raising=False)
    assert list(q.collisions(object(),0,'any'))==[]
    with q.reserve_corridors([record()]):
        assert list(q.collisions(object(),0,'owner'))==[]
        assert list(q.collisions(object(),2,'foreign'))==[]
    assert 'smilepcb.native_worker' not in sys.modules


def via_record(**changes):
    row=dict(kind='via',id='future-via',layer=2,nets=['bridge-owner'],center_nm=[10,5],radius_nm=3)
    row.update(changes)
    return row


def test_via_records_are_immutable_nested_and_face_specific(host):
    q=policy();row=via_record();cross=Segment((0,5),(20,5),2)
    with q.reserve_corridors([record()]):
        parent=q.current_corridors()
        with pytest.raises(RuntimeError):
            with q.reserve_corridors([row]):
                row['center_nm'][0]=200;row['nets'].append('intruder');row['radius_nm']=100
                bound=q.current_corridors()[1]
                assert bound.kind=='via' and bound.center_nm==(10,5) and bound.radius_nm==3
                assert bound.nets==frozenset({'bridge-owner'})
                assert list(q.collisions(cross,2,'foreign'))==['future-via']
                assert list(q.collisions(cross,2,'bridge-owner'))==[]
                assert list(q.collisions(cross,0,'bridge-owner'))==['lane-a']
                assert list(q.collisions(Segment((0,10),(20,10),2),2,'foreign'))==[]
                raise RuntimeError('abort nested via')
        assert q.current_corridors()==parent
    assert q.current_corridors()==()


@pytest.mark.parametrize('change',[
    dict(radius_nm=0),dict(radius_nm=-1),dict(radius_nm=1.5),dict(radius_nm=True),
    dict(center_nm=[]),dict(center_nm=[1]),dict(center_nm=[1,2,3]),
    dict(center_nm=[1.5,2]),dict(center_nm=[True,2]),dict(layer=1),dict(layer=True),
    dict(nets=[]),dict(nets=['owner','owner']),dict(id=''),dict(kind='disk'),
])
def test_invalid_via_records_fail_without_binding(change):
    q=policy()
    with pytest.raises((ValueError,TypeError)):
        with q.reserve_corridors([via_record(**change)]):pass
    assert q.current_corridors()==()


@pytest.mark.parametrize('face',[0,2])
def test_future_via_blocks_foreign_actual_path_before_any_emission(host,face):
    with policy().reserve_corridors([via_record(layer=face)]):
        with pytest.raises(ValueError,match='corridor'):
            host.add_path([(0,-5),(0,5),(20,5)],face,'foreign')
    assert host.added==[]
