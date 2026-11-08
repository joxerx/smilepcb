"""Coherence is an atomic preference over an already feasible source set."""
from copy import deepcopy
import time
import pytest
from smilepcb.keyboard_routing.coherent_via_banks import refine_cohort_selection
from smilepcb.keyboard_routing.conditional_sources import SourceSearchBudget

COHORT=dict(id='physical',members=('a','b'),pad_ids=('pa','pb'))


def row(net,*,kind='via',profile=None,coherent=False):
    q=dict(net=net,pad_ids=['p'+net],kind=kind,path=[(0,0),(1000,0)],layer=2,via_nm=(1000,0) if kind=='via' else None,
        exit_nm=(2000,0) if kind=='via' else (1000,0),exit_layer=0 if kind=='via' else 2,
        heading_origin_nm=(1000,0) if kind=='via' else (0,0),cost=1000,bank='bank',profile=profile)
    if coherent:q.update(cohort='physical',cohort_pad_ids=('pa','pb'),cohort_profile=profile)
    return q


def baseline():return [row('a',kind='same_layer',profile='legacy'),row('b'),row('outside')]


def refine(selected,profiles,**kwargs):
    calls=[]
    def menu(cohort,limit,charge):
        calls.append(limit)
        for profile in profiles[:limit]:
            for candidate in profile:charge(candidate['net'])
        return profiles[:limit],{'retained_profiles':min(limit,len(profiles))}
    result,receipt=refine_cohort_selection(selected,[COHORT],menu,deadline=time.monotonic()+2,
        compatible=kwargs.pop('compatible',lambda a,b:True),max_candidates=kwargs.pop('max_candidates',2),**kwargs)
    return result,receipt,calls


def test_empty_menu_keeps_complete_feasible_legacy_selection_and_labels_mixed():
    selected=baseline();saved=deepcopy(selected)
    result,receipt,calls=refine(selected,[])
    assert result==saved==selected and len(result)==3
    assert receipt[0]['outcome']=='unchanged_no_compatible_profile'
    assert receipt[0]['after']['coherence']=='mixed_or_unprofiled'


def test_already_coherent_same_layer_is_kept_without_generating_vias():
    selected=[row(n,kind='same_layer',profile='one') for n in ('a','b')]
    result,receipt,calls=refine(selected,[[row(n,profile='new',coherent=True) for n in ('a','b')]])
    assert result==selected and not calls
    assert receipt[0]['outcome']=='kept_existing_coherent'
    assert receipt[0]['after']['via_count']==0


def test_whole_profile_collision_with_other_selected_head_keeps_every_old_lane():
    selected=baseline();saved=deepcopy(selected)
    profile=[row(n,profile='new',coherent=True) for n in ('a','b')]
    result,receipt,calls=refine(selected,[profile],compatible=lambda a,b:not (a['net']=='b' and b['net']=='outside'))
    assert result==saved==selected
    assert receipt[0]['outcome']=='unchanged_no_compatible_profile' and receipt[0]['profile_checks']==1


def test_one_valid_profile_replaces_every_member_consistently_and_reports_tradeoff():
    selected=baseline();profile=[row(n,profile='new',coherent=True) for n in ('a','b')]
    result,receipt,calls=refine(selected,[profile])
    assert [r['cohort_profile'] for r in result[:2]]==['new','new'] and result[2] is selected[2]
    assert all('cohort' not in r for r in selected)
    assert receipt[0]['outcome']=='applied_profile'
    assert receipt[0]['before']['via_count']==1 and receipt[0]['after']['via_count']==2
    assert receipt[0]['after']['coherence']=='coherent_via'
    assert receipt[0]['before']['actual_length_nm']==3000 and receipt[0]['after']['actual_length_nm']==4000


def test_shared_budget_never_replenishes_legacy_allowance_and_charges_consideration():
    selected=baseline();budget=SourceSearchBudget([('pa',),('pb',)],max_candidates=2)
    budget.charge_candidates(['pa'],2)
    result,receipt,calls=refine(selected,[],source_budget=budget)
    assert result==selected and not calls and receipt[0]['outcome']=='skipped_budget'
    budget=SourceSearchBudget([('pa',),('pb',)],max_candidates=2)
    budget.charge_candidates(['pa'],1);budget.charge_candidates(['pb'],1)
    profile=[row(n,profile='new',coherent=True) for n in ('a','b')]
    result,receipt,calls=refine(selected,[profile,profile],source_budget=budget,max_candidates=96)
    assert calls==[1] and all(q['spent']==2 for q in budget.receipt()['candidate_domains'])
    assert receipt[0]['additional_candidate_checks']=={'a':1,'b':1}


def test_expired_deadline_and_complete_constraint_preserve_feasible_choice(monkeypatch):
    from smilepcb.keyboard_routing import coherent_via_banks as api
    clock=[10.];monkeypatch.setattr(api.time,'monotonic',lambda:clock[0])
    selected=baseline();profile=[row(n,profile='new',coherent=True) for n in ('a','b')]
    result,receipt=refine_cohort_selection(selected,[COHORT],lambda *a:pytest.fail('Expired refinement'),
        deadline=9.,compatible=lambda *a:True,max_candidates=2)
    assert result==selected and receipt[0]['outcome']=='skipped_deadline'
    result,receipt,calls=refine(selected,[profile],accept_complete=lambda rows:False)
    assert result==selected and receipt[0]['outcome']=='unchanged_no_compatible_profile'


def test_physical_length_excludes_via_penalty_and_unemitted_continuation():
    selected=baseline();selected[1].update(cost=20_000_000,future_end_nm=(1000000,0))
    result,receipt,calls=refine(selected,[])
    assert receipt[0]['before']['actual_length_nm']==3000
    assert receipt[0]['before']['selector_cost']==20_001_000


def test_existing_certified_via_profile_is_kept_without_additional_menu():
    selected=[row(n,profile='old-coherent',coherent=True) for n in ('a','b')]
    result,receipt,calls=refine(selected,[])
    assert result==selected and not calls
    assert receipt[0]['outcome']=='kept_existing_coherent' and receipt[0]['after']['coherence']=='coherent_via'


def test_deadline_during_peer_checks_cannot_apply_half_a_profile(monkeypatch):
    from smilepcb.keyboard_routing import coherent_via_banks as api
    clock=[0.];monkeypatch.setattr(api.time,'monotonic',lambda:clock[0])
    selected=baseline();profile=[row(n,profile='new',coherent=True) for n in ('a','b')]
    def compatible(a,b):clock[0]=3.;return True
    result,receipt,calls=refine(selected,[profile],compatible=compatible)
    assert all(a is b for a,b in zip(result,selected))
    assert receipt[0]['outcome']=='skipped_deadline'
