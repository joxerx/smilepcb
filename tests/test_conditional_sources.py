from itertools import product
import time
import pytest
from smilepcb.keyboard_routing.conditional_sources import select_constrained_head_set
from smilepcb.keyboard_routing.conditional_sources import geometry_signature


def row(net, pads, signature, label):
    return {'net':net,'pad_ids':list(pads),'signature':signature,'label':label}


def choose(domains, required, accept=lambda rows:True, compatible=lambda a,b:True):
    return select_constrained_head_set(domains,compatible,time.monotonic()+2,10000,
        required_heads=required,signature=lambda q:q['signature'],accept_complete=accept)


def test_prunes_unchanged_required_head_and_remaps_local_indices():
    a=[row('x',['physical-a'],'old','a0'),row('x',['physical-a'],'new','a1')]
    b=[row('y',['physical-b'],'other','b0')]
    selected,stats=choose([a,b],{('physical-a',):'old'})
    assert selected=={0:1,1:0}
    assert stats['head_constraints'][0]['before']==2
    assert stats['head_constraints'][0]['after']==1
    assert a[0]['signature']=='old'


def test_mapping_is_relative_to_preferred_callback_sublist():
    global_pool=[row('n',['p'],'old','global0'),row('n',['p'],'old','global1'),
                 row('n',['p'],'old','global2'),row('n',['p'],'new','global3')]
    preferred=[global_pool[1],global_pool[3]]
    selected,_=choose([preferred],{('p',):'old'})
    assert selected=={0:1}
    assert preferred[selected[0]] is global_pool[3]


@pytest.mark.parametrize('mask',range(8))
def test_exhaustive_leaf_set_equivalence_with_pair_constraints(mask):
    domains=[[row('a',['a'],('old' if i==0 else 'new'),i) for i in range(3)],
             [row('b',['b'],'same',i) for i in range(3)],
             [row('c',['c'],'same',i) for i in range(2)]]
    required={('a',):'old'}
    def compatible(a,b):return not ((a['label']+b['label']+mask)%5==0)
    def valid(rows):return rows[0]['signature']!='old' and (sum(q['label'] for q in rows)+mask)%2==0
    expected={indices for indices in product(*(range(len(d)) for d in domains))
        if valid([domains[i][v] for i,v in enumerate(indices)]) and all(
            compatible(domains[i][indices[i]],domains[j][indices[j]]) for i in range(3) for j in range(i+1,3))}
    found=set()
    while True:
        def accept(rows):return valid(rows) and tuple(q['label'] for q in rows) not in found
        selected,_=choose(domains,required,accept,compatible)
        if selected is None:break
        result=tuple(selected[i] for i in range(3));assert result not in found;found.add(result)
    assert found==expected


def test_split_alias_group_defers_to_aggregate_leaf_constraint():
    domains=[[row('same',['p1'],'partial','one')],[row('same',['p2'],'partial','two')]]
    seen=[]
    selected,stats=choose(domains,{('p1','p2'):'whole-old'},lambda rows:seen.append(rows) or True)
    assert selected=={0:0,1:0} and len(seen)==1
    assert stats['head_constraints'][0]['status']=='deferred_non_exact_group'
    assert stats['head_constraints'][0]['after'] is None


def test_duplicate_net_group_preserves_aggregate_geometry_semantics():
    domains=[[row('same',['p1'],'old','one')],[row('same',['p2'],'additional','two')]]
    selected,stats=choose(domains,{('p1',):'old'})
    assert selected=={0:0,1:0}
    assert stats['head_constraints'][0]['status']=='deferred_shared_net'


def test_uniform_complete_alias_group_can_be_filtered():
    ds=[[row('n',['p2','p1'],'old','old'),row('n',['p1','p2'],'new','new')]]
    selected,stats=choose(ds,{('p1','p2'):'old'})
    assert selected=={0:1} and stats['head_constraints'][0]['after']==1


def test_empty_filtered_group_is_not_a_budget_failure():
    selected,stats=choose([[row('n',['p'],'old','one')]],{('p',):'old'})
    assert selected is None and stats['states']==0
    assert stats['empty_filtered_groups']==[0]
    assert not stats.get('budget_exhausted',False)


def test_no_feedback_preserves_original_selector_choices():
    ds=[[row('n',['p'],'a','one'),row('n',['p'],'b','two')]]
    selected,stats=choose(ds,{})
    assert selected=={0:0} and stats['head_constraints']==[]


def test_known_unary_constraint_does_not_spend_state_cap_on_invalid_leaves():
    ds=[[row('required',['p'],'old',0),row('required',['p'],'new',1)],
        [row('b',['b'],'other',i) for i in range(8)],
        [row('c',['c'],'other',i) for i in range(8)]]
    selected,stats=select_constrained_head_set(ds,lambda a,b:True,time.monotonic()+2,20,
        required_heads={('p',):'old'},signature=lambda q:q['signature'],
        accept_complete=lambda rows:rows[0]['signature']!='old')
    assert selected is not None
    assert selected[0]==1
    assert stats['states']<=3


@pytest.mark.parametrize('required',[{('p1','p2'):'old'},{('p1',):'old'}])
def test_exhaustive_alias_aggregate_constraint_is_preserved(required):
    ds=[[row('same',['p1'],'old' if i==0 else 'new',i) for i in range(2)],
        [row('same',['p2'],'old' if i==0 else 'new',i) for i in range(2)]]
    found=set()
    while True:
        def accept(rows):
            key=tuple(q['label'] for q in rows)
            return key!=(0,0) and key not in found
        selected,stats=choose(ds,required,accept)
        if selected is None:break
        key=tuple(selected[i] for i in range(2));assert key not in found;found.add(key)
    assert found=={(0,1),(1,0),(1,1)}


def track(**updates):
    return dict({'class':'PCB_TRACK', 'net':'signal', 'width':150000, 'layer':0,
                 'start':[0,0], 'end':[2000000,0]}, **updates)


def via(**updates):
    return dict({'class':'PCB_VIA', 'net':'signal', 'width':500000, 'drill':200000,
                 'top':0, 'bottom':2, 'via_type':3, 'start':[0,0], 'end':[0,0]}, **updates)


def test_geometry_equivalence_ignores_ids_direction_duplicates_and_collinear_splits():
    original = {'track':track(), 'via':via()}
    split = {'one':track(end=[1000000,0]), 'two':track(start=[2000000,0], end=[1000000,0]),
             'duplicate':track(end=[1000000,0]), 'new-via-id':via(top=2,bottom=0)}
    assert geometry_signature(original) == geometry_signature(split)


@pytest.mark.parametrize('change', [{'net':'other'}, {'layer':2}, {'width':160000},
    {'start':[0,1], 'end':[2000000,1]}])
def test_track_binding_face_width_and_real_outlet_shift_remain_distinct(change):
    assert geometry_signature({'a':track()}) != geometry_signature({'b':track(**change)})


@pytest.mark.parametrize('change', [{'net':'other'}, {'drill':250000}, {'width':600000},
    {'start':[1,0], 'end':[1,0]}, {'via_type':1}, {'bottom':4}])
def test_every_via_physical_attribute_remains_significant(change):
    assert geometry_signature({'a':via()}) != geometry_signature({'b':via(**change)})


def test_collinear_gap_is_not_erased_by_geometry_equivalence():
    split = {'one':track(end=[999999,0]), 'two':track(start=[1000000,0])}
    assert geometry_signature({'whole':track()}) != geometry_signature(split)
