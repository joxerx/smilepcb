"""Geometry and actual mutation/rollback contracts without loading KiCad."""
from copy import deepcopy
import importlib
import pytest


def api():
    try: return importlib.import_module('smilepcb.keyboard_routing.joint_tail_hygiene')
    except ModuleNotFoundError: pytest.fail('Generic tail hygiene implementation is missing')


def track(a,b,net='changed',width=150):
    return {'class':'PCB_TRACK','start':list(a),'end':list(b),'net':net,'width':width,'layer':2,'locked':False}


def test_centerline_attachment_can_extend_as_well_as_shorten_without_splitting_bus():
    m=api();bus=track((0,-1000),(0,1000),width=200)
    assert m.endpoint_candidates(track((2000,0),(75,0)),{'fixed-bus':bus}, {'end'})==[('end',(0,0),'fixed-bus')]
    assert m.endpoint_candidates(track((2000,0),(-75,0)),{'fixed-bus':bus}, {'end'})==[('end',(0,0),'fixed-bus')]
    assert bus==track((0,-1000),(0,1000),width=200)


def test_attachment_is_derived_after_rotation_renaming_and_obstacle_change():
    m=api();branch=track((90,2040),(90,115),net='another');bus=track((-910,40),(1090,40),net='another',width=200)
    assert m.endpoint_candidates(branch,{'new-id':bus},{'end'})==[('end',(90,40),'new-id')]
    bus['start']=[100,40]
    assert m.endpoint_candidates(branch,{'new-id':bus},{'end'})==[]


def test_attachment_rejects_foreign_layer_far_parallel_and_already_centered():
    m=api();branch=track((2000,0),(75,0));bus=track((0,-1000),(0,1000),width=200)
    assert m.endpoint_candidates(branch,{'x':{**bus,'net':'foreign'}},{'end'})==[]
    assert m.endpoint_candidates(branch,{'x':{**bus,'layer':0}},{'end'})==[]
    assert m.endpoint_candidates(track((2000,0),(400,0)),{'x':bus},{'end'})==[]
    assert m.endpoint_candidates(track((2000,0),(0,0)),{'x':bus},{'end'})==[]
    assert m.endpoint_candidates(branch,{'x':track((0,100),(2000,100))},{'end'})==[]


def test_full_topology_keeps_padless_branches_and_does_not_delete_an_entire_island():
    m=api();old=[{'pad-a','pad-b','tail','branch'},{'island','stub'}];pads=[{'pad-a','pad-b'}]
    m.require_tail_topology(old,pads,[{'pad-a','pad-b','branch','new'},{'island','stub'}],pads,{'tail'},{'new'})
    with pytest.raises(ValueError):m.require_tail_topology(old,pads,[{'pad-a','pad-b'},{'branch'},{'island','stub'}],pads,{'tail'},set())
    with pytest.raises(ValueError):m.require_tail_topology(old,pads,[{'pad-a','pad-b','branch'}],pads,{'tail','island','stub'},set())
    with pytest.raises(ValueError):m.require_tail_topology(old,pads,[{'pad-a','pad-b','branch'},{'island','stub'}],[{'pad-a'},{'pad-b'}],{'tail'},set())


class Item:
    def __init__(self,uid,shape):self.uid=uid;self.shape=deepcopy(shape)
    def Duplicate(self):return Item(self.uid,self.shape)
    def SetUuid(self,uid):self.uid=uid
    def SetStart(self,q):self.shape['start']=list(q)
    def SetEnd(self,q):self.shape['end']=list(q)


class Board:
    def __init__(self):self.items={'old':Item('old',{**track((2000,0),(75,0)),'custom-property':71})}
    def Remove(self,item):del self.items[item.uid]
    def Add(self,item):self.items[item.uid]=item


def test_real_replacement_changes_only_uuid_endpoint_and_rejection_restores_original():
    m=api();board=Board();old=board.items['old'];before=deepcopy(old.shape)
    new=m.replace_endpoint(board,old,'end',(0,0),'fresh',lambda q:q)
    assert set(board.items)=={'fresh'} and new.shape=={**before,'end':[0,0]}
    assert old.shape==before
    board.Remove(new);board.Add(old)
    def reject(item):raise ValueError('native creation rejection')
    with pytest.raises(ValueError):m.replace_endpoint(board,old,'end',(0,0),'fresh',lambda q:q,verify=reject)
    assert set(board.items)=={'old'} and board.items['old'] is old and old.shape==before


def test_tail_delta_rejects_protected_unowned_retained_or_nonendpoint_changes():
    m=api();before={'tracks':{'old':track((2000,0),(75,0)),'bus':track((0,-1000),(0,1000))},'footprints':{'fixed':{}}}
    after=deepcopy(before);del after['tracks']['old'];after['tracks']['fresh']={**before['tracks']['old'],'end':[0,0]}
    replacements={'old':{'new_uuid':'fresh','end_nm':[0,0]}}
    m.require_tail_delta(before,after,{'old'},{'bus'},{'old'},replacements)
    for generated,protected in [(set(),{'bus'}),({'old'},{'old','bus'})]:
        with pytest.raises(ValueError):m.require_tail_delta(before,after,generated,protected,{'old'},replacements)
    bad=deepcopy(after);bad['tracks']['fresh']['width']=200
    with pytest.raises(ValueError):m.require_tail_delta(before,bad,{'old'},{'bus'},{'old'},replacements)
    bad=deepcopy(after);bad['tracks']['bus']['end']=[5,5]
    with pytest.raises(ValueError):m.require_tail_delta(before,bad,{'old'},{'bus'},{'old'},replacements)


def test_cleanup_orchestration_deletes_only_proven_tail_and_reverts_late_failure():
    m=api()
    class Context:
        def __init__(self,fail=False):
            self.board=Board();self.board.items['stub']=Item('stub',track((75,0),(-75,0)))
            self.board.items['bus']=Item('bus',track((0,-1000),(0,1000),width=200))
            self.before=self.state();self.fail=fail;self.round=0
        def state(self):return {'tracks':{u:deepcopy(t.shape) for u,t in self.board.items.items()},'footprints':{}}
        def items(self):return self.board.items.copy()
        def bare_ends(self,uid):return {'end'} if uid in {'old','stub'} else set()
        def fresh_uuid(self):return 'fresh'
        def vec(self,q):return q
        def verify(self,removed,replacements,final=False):
            m.require_tail_delta(self.before,self.state(),{'old','stub'},{'bus'},removed,replacements)
            if 'old' not in self.board.items and 'fresh' not in self.board.items:raise ValueError('retained padless branch would split')
            if final and self.fail:raise ValueError('late native creation proof failed')
    ctx=Context();out=m._normalize(ctx,{'old','stub'},{'bus'},float('inf'))
    assert out=={'removed_ids':['old','stub'],'added_ids':['fresh'],'endpoint_replacements':{'old':{'new_uuid':'fresh','end_nm':[0,0]}}}
    assert set(ctx.board.items)=={'fresh','bus'} and ctx.board.items['fresh'].shape['end']==[0,0]
    ctx=Context(True);before=deepcopy(ctx.before)
    with pytest.raises(ValueError):m._normalize(ctx,{'old','stub'},{'bus'},float('inf'))
    assert ctx.state()==before


def test_expired_request_never_enters_native_or_mutates_board():
    m=api();board=Board();before=deepcopy(board.items['old'].shape)
    with pytest.raises(TimeoutError):m.normalize_generated_tails(board,generated_ids={'old'},protected_ids=set(),deadline=0)
    assert board.items['old'].shape==before


def test_failed_native_add_restores_removed_original_track():
    m=api();board=Board();old=board.items['old'];add=board.Add
    def fail_new(item):
        if item.uid=='fresh':raise RuntimeError('native Add failed')
        add(item)
    board.Add=fail_new
    with pytest.raises(RuntimeError):m.replace_endpoint(board,old,'end',(0,0),'fresh',lambda q:q)
    assert board.items=={'old':old}


def test_covered_short_chain_is_pruned_before_required_branch_endpoint_normalization():
    m=api()
    class Context:
        def __init__(self):
            self.board=Board()
            self.board.items['stub']=Item('stub',track((75,0),(0,0)))
            self.board.items['bus']=Item('bus',track((0,-1000),(0,1000),width=200))
            self.before=self.state()
        def state(self):return {'tracks':{u:deepcopy(t.shape) for u,t in self.board.items.items()},'footprints':{}}
        def items(self):return self.board.items.copy()
        def bare_ends(self,uid):
            return {'end'} if uid=='old' and 'stub' not in self.board.items else set()
        def fresh_uuid(self):return 'fresh'
        def vec(self,q):return q
        def verify(self,removed,replacements,final=False):
            m.require_tail_delta(self.before,self.state(),{'old','stub'},{'bus'},removed,replacements)
            # The remote pad needs the long branch; its short cap already
            # touches the bus physically after the redundant stub is removed.
            live=set(self.board.items)
            groups=[live|{'pad'}] if live&{'old','fresh'} else [live,{'pad'}]
            m.require_tail_topology([{'bus','stub','old','pad'}],[{'pad'}],groups,[{'pad'}],removed,set(replacements[q]['new_uuid'] for q in replacements))
    ctx=Context();out=m._normalize(ctx,{'old','stub'},{'bus'},float('inf'))
    assert out=={'removed_ids':['old','stub'],'added_ids':['fresh'],
                 'endpoint_replacements':{'old':{'new_uuid':'fresh','end_nm':[0,0]}}}
    assert set(ctx.board.items)=={'bus','fresh'}
    assert ctx.board.items['fresh'].shape['end']==[0,0]
    assert ctx.board.items['bus'].shape==track((0,-1000),(0,1000),width=200)


def test_short_bus_overlap_is_exact_local_and_requires_strict_finite_bus_interior():
    m=api();bus=track((0,-1000),(0,1000),width=200)
    eligible=[track((100,0),(0,0)),track((0,-100),(0,100)),track((350,0),(0,0))]
    for candidate in eligible:assert m.short_bus_overlap(candidate,{'bus':bus})
    rejected=[track((351,0),(0,0)),track((100,1000),(0,1000)),track((100,0),(1,0)),
              track((100,0),(0,0),net='foreign'),{**track((100,0),(0,0)),'layer':0},
              track((0,0),(0,0))]
    for candidate in rejected:assert not m.short_bus_overlap(candidate,{'bus':bus})
    assert not m.short_bus_overlap(track((100,0),(0,0)),{'same-size':track((0,-50),(0,50),width=200)})
    assert not m.short_bus_overlap(track((100,0),(0,0)),{'not-track':{**bus,'class':'PCB_VIA'}})
    rotated=track((40,1090),(40,990),net='renamed')
    assert m.short_bus_overlap(rotated,{'new-bus':track((-960,990),(1040,990),net='renamed',width=200)})


@pytest.mark.parametrize('generated,protected',[({'old'},{'bus'}),({'old','stub'},{'bus','stub'})])
def test_covered_overlap_never_admits_unowned_or_protected_copper(generated,protected):
    m=api()
    class Context:
        def __init__(self):
            self.board=Board();self.board.items['stub']=Item('stub',track((75,0),(0,0)))
            self.board.items['bus']=Item('bus',track((0,-1000),(0,1000),width=200));self.before=self.state()
        def state(self):return {'tracks':{u:deepcopy(t.shape) for u,t in self.board.items.items()},'footprints':{}}
        def items(self):return self.board.items.copy()
        def bare_ends(self,uid):return set()
        def verify(self,removed,replacements,final=False):
            m.require_tail_delta(self.before,self.state(),generated,protected,removed,replacements)
    ctx=Context();before=deepcopy(ctx.before)
    assert m._normalize(ctx,generated,protected,float('inf'))=={'removed_ids':[],'added_ids':[],'endpoint_replacements':{}}
    assert ctx.state()==before


def test_short_t_junction_admission_cannot_bypass_retained_component_proof():
    m=api()
    class Context:
        def __init__(self):
            self.board=Board();self.board.items['old']=Item('old',track((250,0),(0,0)))
            self.board.items['bus']=Item('bus',track((0,-1000),(0,1000),width=200))
            self.board.items['limb']=Item('limb',track((250,0),(2000,0)))
            self.before=self.state()
        def state(self):return {'tracks':{u:deepcopy(t.shape) for u,t in self.board.items.items()},'footprints':{}}
        def items(self):return self.board.items.copy()
        def bare_ends(self,uid):return set()
        def verify(self,removed,replacements,final=False):
            m.require_tail_delta(self.before,self.state(),{'old'},{'bus','limb'},removed,replacements)
            groups=[set(self.board.items)] if 'old' in self.board.items else [{'bus'},{'limb'}]
            m.require_tail_topology([{'old','bus','limb'}],[],groups,[],removed,set())
    ctx=Context();before=deepcopy(ctx.before)
    assert m.short_bus_overlap(before['tracks']['old'],{'bus':before['tracks']['bus']})
    assert m._normalize(ctx,{'old'},{'bus','limb'},float('inf'))=={'removed_ids':[],'added_ids':[],'endpoint_replacements':{}}
    assert ctx.state()==before
