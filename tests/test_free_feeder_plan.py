"""Fallback planning cannot invent time or combine unrelated native prefixes."""
from copy import deepcopy
import math
import pytest
from smilepcb.keyboard_routing import free_feeder_plan as plan
from smilepcb.keyboard_routing import conditional_search


@pytest.mark.parametrize('remaining,reserve,proof', [(40,20,5),(60,30,5),(84,42,6),(420,210,30),(1000,210,30)])
def test_cutoffs_reserve_routing_and_proof_from_the_existing_clock(remaining,reserve,proof):
    assert plan.fallback_cutoffs(100,100+remaining) == {
        'ordered_deadline': 100+remaining-reserve, 'routing_deadline': 100+remaining-proof,
        'proof_deadline': 100+remaining, 'reserved_s': reserve, 'enabled': True}


@pytest.mark.parametrize('started,deadline,enabled', [(100,139,True),(100,100,True),(101,100,True),(100,1000,False)])
def test_short_expired_or_disabled_budget_preserves_original_ordered_cutoff(started,deadline,enabled):
    assert plan.fallback_cutoffs(started,deadline,enabled=enabled) == {
        'ordered_deadline': deadline, 'routing_deadline': deadline, 'proof_deadline': deadline,
        'reserved_s': 0, 'enabled': False}


@pytest.mark.parametrize('shift', [-1000,0,15000])
def test_cutoff_allocation_is_invariant_under_clock_translation(shift):
    result=plan.fallback_cutoffs(100+shift,310+shift)
    assert result == {'ordered_deadline':205+shift,'routing_deadline':295+shift,
                      'proof_deadline':310+shift,'reserved_s':105,'enabled':True}


@pytest.mark.parametrize('started,deadline,enabled', [
    (True,100,True),(0,False,True),('0',100,True),(None,100,True),
    (math.nan,100,True),(0,math.inf,True),(-math.inf,0,True),
    (0,100,1),(0,100,None),(-1e308,1e308,True),pytest.param(0,10**1000,True,id='integer-overflow')])
def test_invalid_clock_values_and_nonboolean_enablement_fail_closed(started,deadline,enabled):
    with pytest.raises(ValueError):plan.fallback_cutoffs(started,deadline,enabled=enabled)


def accepted(index,bank,prefix,groups,**extra):
    return {'index':index,'depth':len(prefix),'bank':bank,'prefix_attempts':list(prefix),'accepted':True,
            'plan':{'bank':bank,'id':'choice-'+str(index),'ports':[{'pad_ids':list(ids),'net':'signal-'+str(n)}
                    for n,ids in enumerate(groups)],'shapes':{'item':{'start':[0,0],'end':[1,1]}},**extra}}


def rejected(index,bank,prefix):
    return {'index':index,'depth':len(prefix),'bank':bank,'prefix_attempts':list(prefix),'accepted':False}


def branches():
    return {'attempt_records':[
        accepted(1,'first',[],[['a1','a2'],['b']]),
        accepted(2,'second',[1],[['c']]),
        rejected(3,'third',[1,2]),
        accepted(4,'first',[],[['a1','a2'],['b']]),
        rejected(5,'second',[4])]}


def test_backtracking_keeps_the_best_complete_physical_prefix():
    value=branches()
    assert [q['id'] for q in plan.best_accepted_prefix(value)] == ['choice-1','choice-2']
    assert value == branches()


def test_equal_scores_keep_the_first_verified_branch_and_count_aliases_once():
    value={'attempt_records':[accepted(1,'first',[],[['a','alias']]),
                              accepted(2,'first',[],[['b'],['c']]),
                              accepted(3,'first',[],[['d'],['e']])]}
    assert [q['id'] for q in plan.best_accepted_prefix(value)] == ['choice-2']


def test_more_physical_interfaces_outrank_depth_or_copper_count():
    value={'attempt_records':[accepted(1,'first',[],[['a']]),accepted(2,'second',[1],[['b']]),
                              accepted(3,'first',[],[['c'],['d'],['e']])]}
    value['attempt_records'][0]['plan']['shapes']={str(i):{} for i in range(100)}
    assert [q['id'] for q in plan.best_accepted_prefix(value)] == ['choice-3']


def test_bank_net_names_and_coordinates_do_not_select_a_different_prefix():
    value=branches()
    for record in value['attempt_records']:
        record['bank']='renamed-'+record['bank']
        if record['accepted']:
            item=record['plan'];item['bank']=record['bank']
            for port in item['ports']:
                port['net']='opaque';port['pad_ids']=['renamed-'+uid for uid in port['pad_ids']]
            item['shapes']['item'].update(start=[500,700],end=[501,701])
    assert [q['id'] for q in plan.best_accepted_prefix(value)] == ['choice-1','choice-2']


def test_returned_prefix_cannot_mutate_retained_search_evidence():
    value=branches();before=deepcopy(value)
    selected=plan.best_accepted_prefix(value)
    selected[0]['ports'][0]['pad_ids'].clear();selected[1]['shapes'].clear()
    assert value == before


@pytest.mark.parametrize('records', [[],[rejected(1,'first',[])]])
def test_no_accepted_plans_returns_an_empty_prefix(records):
    assert plan.best_accepted_prefix({'attempt_records':records}) == []


@pytest.mark.parametrize('case', ['missing_records','bad_index','duplicate_index','boolean_index','forward_parent',
    'rejected_parent','mixed_ancestry','depth','repeated_bank','plan_bank','missing_plan','rejected_plan',
    'accepted_flag','empty_ports','empty_aliases','duplicate_alias','overlapping_interfaces','overlapping_banks'])
def test_malformed_or_mixed_parent_evidence_cannot_seed_fallback(case):
    value=branches();records=value['attempt_records']
    if case=='missing_records':value={}
    elif case=='bad_index':records[1]['index']=7
    elif case=='duplicate_index':records[1]['index']=1
    elif case=='boolean_index':records[0]['index']=True
    elif case=='forward_parent':records[1]['prefix_attempts']=[4]
    elif case=='rejected_parent':records.append(accepted(6,'third',[4,5],[['z']]))
    elif case=='mixed_ancestry':records.append(accepted(6,'third',[4,2],[['z']]))
    elif case=='depth':records[1]['depth']=0
    elif case=='repeated_bank':records[1]['bank']=records[1]['plan']['bank']='first'
    elif case=='plan_bank':records[1]['plan']['bank']='wrong'
    elif case=='missing_plan':records[0].pop('plan')
    elif case=='rejected_plan':records[2]['plan']=deepcopy(records[0]['plan'])
    elif case=='accepted_flag':records[0]['accepted']=1
    elif case=='empty_ports':records[0]['plan']['ports']=[]
    elif case=='empty_aliases':records[0]['plan']['ports'][0]['pad_ids']=[]
    elif case=='duplicate_alias':records[0]['plan']['ports'][0]['pad_ids']=['a','a']
    elif case=='overlapping_interfaces':records[0]['plan']['ports'][1]['pad_ids']=['a1']
    elif case=='overlapping_banks':records[1]['plan']['ports'][0]['pad_ids']=['b']
    with pytest.raises(ValueError):plan.best_accepted_prefix(value)


def test_real_conditional_search_records_reconstruct_after_a_failed_descendant(monkeypatch):
    monkeypatch.setattr(conditional_search,'monotonic',lambda:0.)
    def attempt(bank,option,prefix,feedback,deadline):
        if bank=='third':return {'accepted':False}
        groups=[['a','alias']] if bank=='first' else [['b'],['c']]
        return {'accepted':True,'plan':accepted(1,bank,[],groups)['plan']}
    search=conditional_search.conditional_search(['first','second','third'],attempt,max_attempts=3,
        deadline=10.,leaf_validator=lambda _:pytest.fail('Partial prefix cannot validate as a leaf'))
    assert search['reason']=='attempt_limit'
    assert [q['bank'] for q in plan.best_accepted_prefix(search)] == ['first','second']
